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

__all__ = [
    'WorkflowSubmissionPayload',
]
