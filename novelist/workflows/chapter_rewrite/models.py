from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class WorkflowSubmissionPayload(BaseModel):
    content_md: str = Field("", description="当前步骤需要写入的 Markdown 正文。")
    chapter_txt: str = Field("", description="当前步骤需要写入的章节纯文本正文。")
    character_status_cards_md: str = Field("", description="人物状态卡 Markdown；无变化则留空。")
    character_relationship_graph_md: str = Field("", description="人物关系链 Markdown；无变化则留空。")
    volume_plot_progress_md: str = Field("", description="卷级剧情进程 Markdown；无变化则留空。")
    foreshadowing_md: str = Field("", description="伏笔管理 Markdown；无变化则留空。")
    world_state_md: str = Field("", description="世界状态 Markdown；无变化则留空。")
    passed: bool | None = Field(None, description="当前审核步骤是否通过。")
    review_md: str = Field("", description="审核 Markdown 正文。")
    blocking_issues: list[str] = Field(default_factory=list, description="阻塞问题列表。")
    rewrite_targets: list[str] = Field(default_factory=list, description="需要重写或更新的目标。")
    chapters_to_revise: list[str] = Field(default_factory=list, description="需要返工的章节编号列表。")

def chapter_rewrite_stage_tool_specs() -> list[llm_runtime.FunctionToolSpec[Any]]:
    return [
        *document_ops.document_tool_specs(),
        llm_runtime.FunctionToolSpec(
            model=WorkflowSubmissionPayload,
            name=WORKFLOW_SUBMISSION_TOOL_NAME,
            description=WORKFLOW_SUBMISSION_TOOL_DESCRIPTION,
        ),
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
