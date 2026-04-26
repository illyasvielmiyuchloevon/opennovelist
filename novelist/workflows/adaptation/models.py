from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class AdaptationReviewTarget(BaseModel):
    file_key: str = Field(..., description="审核或修复目标的逻辑 key。")
    file_name: str = Field(..., description="文件名。")
    file_path: str = Field(..., description="文件绝对路径或工程内路径。")
    label: str = Field("", description="中文文档名。")
    scope: str = Field("", description="global 或 volume。")
    exists: bool = Field(False, description="文件当前是否存在。")
    current_char_count: int = Field(0, description="当前正文字符数。")
    current_content: str = Field("", description="当前文件正文，必要时会被截断。")
    preferred_mode: str = Field("edit_or_patch", description="建议工具模式。")

class AdaptationReviewPayload(BaseModel):
    passed: bool | None = Field(None, description="本卷资料审核是否通过。")
    review_md: str = Field("", description="Markdown 审核报告正文。")
    blocking_issues: list[str] = Field(default_factory=list, description="阻塞后续仿写的资料问题。")
    rewrite_targets: list[str] = Field(
        default_factory=list,
        description="需要原地修复的目标文件 key，必须来自 adaptation_documents 或 update_target_files。",
    )

class AdaptationReviewResult(BaseModel):
    payload: AdaptationReviewPayload
    response_ids: list[str] = Field(default_factory=list)
    review_path: str = ""
    fix_attempts: int = 0


def adaptation_stage_tool_specs() -> list[llm_runtime.FunctionToolSpec[Any]]:
    return [
        *document_ops.document_tool_specs(),
        llm_runtime.FunctionToolSpec(
            model=AdaptationReviewPayload,
            name=ADAPTATION_REVIEW_TOOL_NAME,
            description=ADAPTATION_REVIEW_TOOL_DESCRIPTION,
        ),
    ]

__all__ = [
    'AdaptationReviewTarget',
    'AdaptationReviewPayload',
    'AdaptationReviewResult',
    'adaptation_stage_tool_specs',
]
