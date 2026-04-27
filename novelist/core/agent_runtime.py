from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from . import document_ops
from . import responses_runtime as llm_runtime
from .workflow_tools import (
    WORKFLOW_SUBMISSION_TOOL_NAME,
    WorkflowSubmissionPayload,
    unified_workflow_tool_specs,
)


@dataclass
class AgentToolApplication:
    tool_name: str
    response_id: str | None
    success: bool
    output: str
    applied: document_ops.AppliedDocumentOperation | None = None


@dataclass
class AgentStageResult:
    submission: WorkflowSubmissionPayload
    response_id: str | None
    response_ids: list[str] = field(default_factory=list)
    applications: list[AgentToolApplication] = field(default_factory=list)

    @property
    def changed_keys(self) -> list[str]:
        keys: list[str] = []
        for application in self.applications:
            if application.applied is None:
                continue
            for key in application.applied.changed_keys:
                if key not in keys:
                    keys.append(key)
        return keys


def document_operation_from_tool_result(
    result: llm_runtime.MultiFunctionToolResult,
) -> document_ops.DocumentOperationCallResult:
    if result.tool_name == document_ops.DOCUMENT_WRITE_TOOL_NAME:
        return document_ops.DocumentOperationCallResult(
            mode="write",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            write_payload=document_ops.DocumentWritePayload.model_validate(result.parsed),
        )
    if result.tool_name == document_ops.DOCUMENT_EDIT_TOOL_NAME:
        return document_ops.DocumentOperationCallResult(
            mode="edit",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            edit_payload=document_ops.DocumentEditPayload.model_validate(result.parsed),
        )
    if result.tool_name == document_ops.DOCUMENT_PATCH_TOOL_NAME:
        return document_ops.DocumentOperationCallResult(
            mode="patch",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            patch_payload=document_ops.DocumentPatchPayload.model_validate(result.parsed),
        )
    raise llm_runtime.ModelOutputError(f"当前 agent 阶段不支持工具：{result.tool_name}", preview=result.preview)


def _tool_output_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _document_tool_output(
    result: llm_runtime.MultiFunctionToolResult,
    *,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> AgentToolApplication:
    try:
        operation = document_operation_from_tool_result(result)
        applied = document_ops.apply_document_operation(operation, allowed_files=allowed_files)
        output = _tool_output_json(
            {
                "ok": True,
                "tool": result.tool_name,
                "mode": applied.mode,
                "emitted_keys": applied.emitted_keys,
                "changed_keys": applied.changed_keys,
                "files": [
                    {
                        "file_key": item.file_key,
                        "file_path": str(item.path),
                        "mode": item.mode,
                        "emitted": item.emitted,
                        "changed": item.changed,
                        "edit_count": item.edit_count,
                    }
                    for item in applied.files
                ],
            }
        )
        return AgentToolApplication(
            tool_name=result.tool_name,
            response_id=result.response_id,
            success=True,
            output=output,
            applied=applied,
        )
    except ValueError as error:
        output = _tool_output_json(
            {
                "ok": False,
                "tool": result.tool_name,
                "error": str(error),
                "repair_instruction": (
                    "请根据当前错误修正上一轮工具参数。old_text 或 match_text 必须从已授权目标文件当前内容中逐字复制，"
                    "然后重新调用 write/edit/patch。"
                ),
            }
        )
        return AgentToolApplication(
            tool_name=result.tool_name,
            response_id=result.response_id,
            success=False,
            output=output,
        )


def _append_chat_tool_messages(
    messages: list[dict[str, Any]],
    result: llm_runtime.MultiFunctionToolResult,
    output: str,
) -> None:
    call_id = result.call_id or f"call_{len(messages)}"
    raw_arguments = result.raw_arguments
    if not raw_arguments:
        raw_arguments = result.parsed.model_dump_json()
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": result.tool_name,
                        "arguments": raw_arguments,
                    },
                }
            ],
        }
    )
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        }
    )


def _responses_tool_output_item(
    result: llm_runtime.MultiFunctionToolResult,
    output: str,
) -> dict[str, Any]:
    if not result.call_id:
        raise llm_runtime.ModelOutputError(
            "Responses 函数工具调用缺少 call_id，无法按官方协议回传 function_call_output。"
        )
    return {
        "type": "function_call_output",
        "call_id": result.call_id,
        "output": output,
    }


def _responses_function_call_item(
    result: llm_runtime.MultiFunctionToolResult,
) -> dict[str, Any]:
    if not result.call_id:
        raise llm_runtime.ModelOutputError(
            "Responses 函数工具调用缺少 call_id，无法按官方协议回传 function_call_output。"
        )
    raw_arguments = result.raw_arguments
    if not raw_arguments:
        raw_arguments = result.parsed.model_dump_json()
    return {
        "type": "function_call",
        "call_id": result.call_id,
        "name": result.tool_name,
        "arguments": raw_arguments,
    }


def _responses_user_message(text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": text,
            }
        ],
    }


def run_agent_stage(
    client: OpenAI,
    *,
    model: str,
    instructions: str,
    user_input: str,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
    retries: int = llm_runtime.DEFAULT_API_RETRIES,
    retry_delay_seconds: int = llm_runtime.DEFAULT_RETRY_DELAY_SECONDS,
    max_iterations: int = 40,
    on_tool_result: Callable[[AgentToolApplication], None] | None = None,
) -> AgentStageResult:
    protocol = llm_runtime.runtime_protocol(client)
    tool_specs = unified_workflow_tool_specs()
    response_ids: list[str] = []
    applications: list[AgentToolApplication] = []
    current_response_id = previous_response_id
    current_input: Any = user_input
    chat_messages: list[dict[str, Any]] | None = None
    responses_transcript: list[dict[str, Any]] | None = None
    if protocol == llm_runtime.PROTOCOL_OPENAI_COMPATIBLE:
        chat_messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ]
    else:
        # Match OpenCode's agent loop shape: keep the transcript locally and
        # resend it each tool turn instead of treating provider state as the
        # only source of conversation truth.
        responses_transcript = [_responses_user_message(user_input)]
        current_input = list(responses_transcript)

    for _ in range(max_iterations):
        request_previous_response_id = (
            current_response_id
            if protocol == llm_runtime.PROTOCOL_OPENAI_COMPATIBLE
            else None
        )
        result = llm_runtime.call_function_tools(
            client,
            model=model,
            instructions=instructions,
            user_input=current_input,
            tool_specs=tool_specs,
            previous_response_id=request_previous_response_id,
            prompt_cache_key=prompt_cache_key,
            retries=retries,
            retry_delay_seconds=retry_delay_seconds,
            tool_choice="auto",
            chat_messages=chat_messages,
            store=protocol == llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
        )
        current_response_id = result.response_id
        if result.response_id:
            response_ids.append(str(result.response_id))

        if result.tool_name == WORKFLOW_SUBMISSION_TOOL_NAME:
            return AgentStageResult(
                submission=WorkflowSubmissionPayload.model_validate(result.parsed),
                response_id=result.response_id,
                response_ids=response_ids,
                applications=applications,
            )

        application = _document_tool_output(result, allowed_files=allowed_files)
        applications.append(application)
        if on_tool_result is not None:
            on_tool_result(application)

        if protocol == llm_runtime.PROTOCOL_OPENAI_COMPATIBLE:
            assert chat_messages is not None
            _append_chat_tool_messages(chat_messages, result, application.output)
            current_input = ""
        else:
            assert responses_transcript is not None
            responses_transcript.extend(
                [
                    _responses_function_call_item(result),
                    _responses_tool_output_item(result, application.output),
                ]
            )
            current_input = list(responses_transcript)

    raise llm_runtime.ModelOutputError(
        f"agent 阶段超过最大工具循环次数 {max_iterations}，仍未调用 {WORKFLOW_SUBMISSION_TOOL_NAME} 完成阶段。"
    )


__all__ = [
    "AgentToolApplication",
    "AgentStageResult",
    "document_operation_from_tool_result",
    "run_agent_stage",
]
