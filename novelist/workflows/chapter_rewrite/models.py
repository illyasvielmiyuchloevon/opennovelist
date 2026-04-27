from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from novelist.core.workflow_tools import WorkflowSubmissionPayload, workflow_submission_tool_spec

def chapter_rewrite_stage_tool_specs() -> list[llm_runtime.FunctionToolSpec[Any]]:
    return [
        *document_ops.document_tool_specs(),
        workflow_submission_tool_spec(),
    ]

def workflow_submission_result_from_stage_tool_result(
    result: llm_runtime.MultiFunctionToolResult,
) -> llm_runtime.FunctionToolResult[WorkflowSubmissionPayload]:
    if result.tool_name != WORKFLOW_SUBMISSION_TOOL_NAME:
        raise llm_runtime.ModelOutputError(
            f"当前 workflow 阶段必须调用 {WORKFLOW_SUBMISSION_TOOL_NAME}，不能调用 {result.tool_name}。",
            preview=result.preview,
            raw_body_text=result.raw_body_text,
        )
    payload = WorkflowSubmissionPayload.model_validate(result.parsed)
    return llm_runtime.FunctionToolResult(
        parsed=payload,
        response_id=result.response_id,
        status=result.status,
        output_types=result.output_types,
        token_usage=result.token_usage,
        preview=result.preview,
        raw_body_text=result.raw_body_text,
        raw_json=result.raw_json,
    )

def document_operation_result_from_stage_tool_result(
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
    if result.tool_name == WORKFLOW_SUBMISSION_TOOL_NAME:
        raise llm_runtime.ModelOutputError(
            f"当前文件编辑步骤必须调用文档 write/edit/patch 工具，不能调用 {WORKFLOW_SUBMISSION_TOOL_NAME}。",
            preview=result.preview,
            raw_body_text=result.raw_body_text,
        )
    raise llm_runtime.ModelOutputError(f"模型调用了未支持的章节工作流工具：{result.tool_name}")

__all__ = [
    'WorkflowSubmissionPayload',
    'chapter_rewrite_stage_tool_specs',
    'workflow_submission_result_from_stage_tool_result',
    'document_operation_result_from_stage_tool_result',
]
