from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .models import (
    WorkflowSubmissionPayload,
    chapter_rewrite_stage_tool_specs,
    document_operation_result_from_stage_tool_result,
    workflow_submission_result_from_stage_tool_result,
)


def call_workflow_submission_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    WorkflowSubmissionPayload,
    str | None,
    llm_runtime.FunctionToolResult[WorkflowSubmissionPayload],
]:
    result = llm_runtime.call_function_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_specs=chapter_rewrite_stage_tool_specs(),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        tool_choice={"type": "function", "name": WORKFLOW_SUBMISSION_TOOL_NAME},
    )
    workflow_result = workflow_submission_result_from_stage_tool_result(result)
    return workflow_result.parsed, workflow_result.response_id, workflow_result

def call_markdown_tool_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[str, str | None, llm_runtime.FunctionToolResult[WorkflowSubmissionPayload]]:
    payload, response_id, result = call_workflow_submission_response(
        client,
        model,
        instructions,
        user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    content_md = payload.content_md.strip()
    if not content_md:
        raise llm_runtime.ModelOutputError("模型未通过统一函数工具返回 Markdown 正文。")
    return content_md, response_id, result

def call_chapter_text_tool_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[str, str | None, llm_runtime.FunctionToolResult[WorkflowSubmissionPayload]]:
    payload, response_id, result = call_workflow_submission_response(
        client,
        model,
        instructions,
        user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    chapter_txt = payload.chapter_txt.strip()
    if not chapter_txt:
        raise llm_runtime.ModelOutputError("模型未通过统一函数工具返回章节正文。")
    return chapter_txt, response_id, result

def call_support_updates_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    document_ops.DocumentOperationCallResult,
    str | None,
    document_ops.DocumentOperationCallResult,
]:
    result = llm_runtime.call_function_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_specs=chapter_rewrite_stage_tool_specs(),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        tool_choice="auto",
    )
    operation = document_operation_result_from_stage_tool_result(result)
    return operation, operation.response_id, operation

def call_chapter_text_revision_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    document_ops.DocumentOperationCallResult,
    str | None,
    document_ops.DocumentOperationCallResult,
]:
    result = llm_runtime.call_function_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_specs=chapter_rewrite_stage_tool_specs(),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        tool_choice="auto",
    )
    operation = document_operation_result_from_stage_tool_result(result)
    return operation, operation.response_id, operation

def call_chapter_review_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    WorkflowSubmissionPayload,
    str | None,
    llm_runtime.FunctionToolResult[WorkflowSubmissionPayload],
]:
    payload, response_id, result = call_workflow_submission_response(
        client,
        model,
        instructions,
        user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    finalized = finalize_review_payload(payload, review_kind="chapter")
    if finalized.passed is None or not finalized.review_md.strip():
        raise llm_runtime.ModelOutputError("模型未通过统一函数工具返回完整的章级审核结果。")
    return finalized, response_id, result

def call_volume_review_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    allowed_chapters: list[str] | None = None,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    WorkflowSubmissionPayload,
    str | None,
    llm_runtime.FunctionToolResult[WorkflowSubmissionPayload],
]:
    payload, response_id, result = call_workflow_submission_response(
        client,
        model,
        instructions,
        user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    finalized = finalize_review_payload(
        payload,
        review_kind="volume",
        allowed_chapters=allowed_chapters,
    )
    if finalized.passed is None or not finalized.review_md.strip():
        raise llm_runtime.ModelOutputError("模型未通过统一函数工具返回完整的卷级审核结果。")
    return finalized, response_id, result

def call_five_chapter_review_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    allowed_chapters: list[str] | None = None,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    WorkflowSubmissionPayload,
    str | None,
    llm_runtime.FunctionToolResult[WorkflowSubmissionPayload],
]:
    payload, response_id, result = call_workflow_submission_response(
        client,
        model,
        instructions,
        user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    finalized = finalize_review_payload(
        payload,
        review_kind="group",
        allowed_chapters=allowed_chapters,
    )
    if finalized.passed is None or not finalized.review_md.strip():
        raise llm_runtime.ModelOutputError("模型未通过统一函数工具返回完整的组审查结果。")
    return finalized, response_id, result

__all__ = [
    'call_workflow_submission_response',
    'call_markdown_tool_response',
    'call_chapter_text_tool_response',
    'call_support_updates_response',
    'call_chapter_text_revision_response',
    'call_chapter_review_response',
    'call_volume_review_response',
    'call_five_chapter_review_response',
]
