from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from . import document_ops
from . import responses_runtime as llm_runtime


WORKFLOW_SUBMISSION_TOOL_NAME = "submit_workflow_result"
WORKFLOW_SUBMISSION_TOOL_DESCRIPTION = (
    "提交当前 agent 阶段的最终结果。生成阶段用它提交阶段完成摘要；"
    "审核阶段用它提交 passed、review_md、blocking_issues、rewrite_targets、chapters_to_revise。"
    "正文或 Markdown 文件内容应优先通过 submit_document_writes / submit_document_edits / "
    "submit_document_patches 落盘；只有阶段结束或审核结论才调用本工具。"
)


class WorkflowSubmissionPayload(BaseModel):
    content_md: str = Field("", description="阶段摘要或 Markdown 结果；通常用于兼容旧调用。")
    chapter_txt: str = Field("", description="章节正文兼容字段；新 agent 阶段应优先用文档工具写入正文文件。")
    character_status_cards_md: str = Field("", description="人物状态卡兼容字段；无变化则留空。")
    character_relationship_graph_md: str = Field("", description="人物关系链兼容字段；无变化则留空。")
    volume_plot_progress_md: str = Field("", description="卷级剧情进程兼容字段；无变化则留空。")
    foreshadowing_md: str = Field("", description="伏笔管理兼容字段；无变化则留空。")
    world_state_md: str = Field("", description="世界状态兼容字段；无变化则留空。")
    passed: bool | None = Field(None, description="审核步骤是否通过；生成步骤可留空。")
    review_md: str = Field("", description="审核 Markdown 正文。")
    blocking_issues: list[str] = Field(default_factory=list, description="阻塞问题列表。")
    rewrite_targets: list[str] = Field(default_factory=list, description="需要重写或更新的目标。")
    chapters_to_revise: list[str] = Field(default_factory=list, description="需要返工的章节编号列表。")
    generated_files: list[str] = Field(default_factory=list, description="本阶段已生成或更新的 file_key 列表。")
    summary: str = Field("", description="本阶段完成情况摘要。")
    error: str = Field("", description="阶段无法完成时的错误说明；正常完成时留空。")


def workflow_submission_tool_spec() -> llm_runtime.FunctionToolSpec[Any]:
    return llm_runtime.FunctionToolSpec(
        model=WorkflowSubmissionPayload,
        name=WORKFLOW_SUBMISSION_TOOL_NAME,
        description=WORKFLOW_SUBMISSION_TOOL_DESCRIPTION,
    )


def unified_workflow_tool_specs() -> list[llm_runtime.FunctionToolSpec[Any]]:
    return [
        *document_ops.document_tool_specs(),
        workflow_submission_tool_spec(),
    ]


__all__ = [
    "WORKFLOW_SUBMISSION_TOOL_NAME",
    "WORKFLOW_SUBMISSION_TOOL_DESCRIPTION",
    "WorkflowSubmissionPayload",
    "workflow_submission_tool_spec",
    "unified_workflow_tool_specs",
]
