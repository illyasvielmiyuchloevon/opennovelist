from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field

from novelist.core.files import (
    extract_json_payload,
    migrate_numbered_injection_dirs,
    migrate_renamed_files,
    normalize_path,
    now_iso,
    read_text_if_exists,
    write_markdown_data,
    write_text_if_changed,
)
from novelist.core.group_outline_plan import (
    GROUP_DIR_SUFFIX,
    GROUP_OUTLINE_PLAN_MANIFEST_NAME,
    GROUP_ROOT_DIRNAME,
    GROUP_STAGE_MANIFEST_NAME,
    group_batch_id,
    group_injection_dir as planned_group_injection_dir,
    group_injection_root as planned_group_injection_root,
    group_outline_docs_from_plan,
    group_outline_path as planned_group_outline_path,
    group_outline_plan_path,
    group_outline_plan_review_path,
    group_plan_groups,
    group_response_debug_path as planned_group_response_debug_path,
    group_review_path as planned_group_review_path,
    group_stage_manifest_path as planned_group_stage_manifest_path,
    load_group_outline_plan,
    validate_group_outline_files as validate_planned_group_outline_files,
    write_group_outline_plan_manifest,
)
import novelist.core.document_ops as document_ops
from novelist.core.novel_source import (
    build_chapter_source_bundle,
    discover_volume_dirs,
    get_chapter_material,
    load_volume_index,
    load_volume_material,
    load_volume_material_for_chapters,
)
from novelist.core.workflow_tools import (
    WORKFLOW_SUBMISSION_TOOL_DESCRIPTION,
    WORKFLOW_SUBMISSION_TOOL_NAME,
)
from novelist.core.ui import fail, pause_before_exit, print_progress, prompt_choice, prompt_text
import novelist.core.openai_config as openai_config
import novelist.core.responses_runtime as llm_runtime
from novelist.workflows.split_novel import (
    RebalanceReport,
    rebalance_source_volumes,
    rebalance_summary_lines,
)


PROJECT_MANIFEST_NAME = "00_project_manifest.md"
LEGACY_PROJECT_MANIFEST_NAME = "00_project_manifest.json"
REWRITE_MANIFEST_NAME = "00_chapter_rewrite_manifest.md"
GLOBAL_CONFIG_DIR = Path.home() / ".novel_adaptation"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.json"
LEGACY_GLOBAL_CONFIG_DIR = Path.home() / ".novel_adaptation_cli"
LEGACY_GLOBAL_CONFIG_PATH = LEGACY_GLOBAL_CONFIG_DIR / "config.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
CHAPTER_REVIEW_SKILL_PATH = REPO_ROOT / "skill" / "chapter_review" / "SKILL.md"
CHAPTER_WRITING_SKILL_PATH = REPO_ROOT / "skill" / "chapter_writing" / "SKILL.md"
GLOBAL_DIRNAME = "global_injection"
VOLUME_ROOT_DIRNAME = "volume_injection"
VOLUME_DIR_SUFFIX = "_volume_injection"
CHAPTER_DIR_SUFFIX = "_chapter_outline"
REWRITTEN_ROOT_DIRNAME = "rewritten_novel"
FIVE_CHAPTER_REVIEW_SIZE = 5
MAX_CHAPTER_REWRITE_ATTEMPTS = 3
MAX_CHAPTER_REVIEW_ATTEMPTS = 5
MAX_CHAPTER_REVIEW_FIX_ATTEMPTS = MAX_CHAPTER_REVIEW_ATTEMPTS - 1
MAX_GROUP_REVIEW_ATTEMPTS = 10
MAX_GROUP_REVIEW_FIX_ATTEMPTS = MAX_GROUP_REVIEW_ATTEMPTS - 1
MAX_VOLUME_REVIEW_ATTEMPTS = 10
MAX_VOLUME_REVIEW_FIX_ATTEMPTS = MAX_VOLUME_REVIEW_ATTEMPTS - 1
MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS = 2
MAX_REVIEW_FIX_ATTEMPTS = MAX_CHAPTER_REVIEW_FIX_ATTEMPTS
RUN_MODE_CHAPTER = "chapter"
RUN_MODE_GROUP = "group"
RUN_MODE_VOLUME = "volume"
PHASE1_OUTLINE = "phase1_outline"
PHASE2_CHAPTER_TEXT = "phase2_chapter_text"
PHASE2_SUPPORT_UPDATES = "phase2_support_updates"
PHASE3_REVIEW = "phase3_review"
CHAPTER_WORKFLOW_PHASE_ORDER = [
    PHASE1_OUTLINE,
    PHASE2_CHAPTER_TEXT,
    PHASE2_SUPPORT_UPDATES,
    PHASE3_REVIEW,
]
RUN_MODE_LABELS = {
    RUN_MODE_CHAPTER: "按章节运行",
    RUN_MODE_GROUP: "按组运行",
    RUN_MODE_VOLUME: "按卷运行",
}

ADAPTATION_GLOBAL_FILE_NAMES = {
    "world_model": "01_world_model.md",
    "style_guide": "02_style_guide.md",
    "book_outline": "03_book_outline.md",
    "foreshadowing": "04_foreshadowing.md",
}
REWRITE_GLOBAL_FILE_NAMES = {
    "character_status_cards": "05_character_status_cards.md",
    "character_relationship_graph": "06_character_relationship_graph.md",
    "world_state": "07_world_state.md",
}
LEGACY_GLOBAL_FILE_RENAMES = {
    "01_world_design.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "01_book_outline.md": ADAPTATION_GLOBAL_FILE_NAMES["book_outline"],
    "02_world_design.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "02_world_model.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "03_style_guide.md": ADAPTATION_GLOBAL_FILE_NAMES["style_guide"],
    "04_book_outline.md": ADAPTATION_GLOBAL_FILE_NAMES["book_outline"],
    "04_world_model.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "05_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "06_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "04_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "08_world_model.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "05_character_status_cards.md": REWRITE_GLOBAL_FILE_NAMES["character_status_cards"],
    "06_character_status_cards.md": REWRITE_GLOBAL_FILE_NAMES["character_status_cards"],
    "07_character_status_cards.md": REWRITE_GLOBAL_FILE_NAMES["character_status_cards"],
    "06_character_relationship_graph.md": REWRITE_GLOBAL_FILE_NAMES["character_relationship_graph"],
    "07_character_relationship_graph.md": REWRITE_GLOBAL_FILE_NAMES["character_relationship_graph"],
    "08_character_relationship_graph.md": REWRITE_GLOBAL_FILE_NAMES["character_relationship_graph"],
    "08_world_state.md": REWRITE_GLOBAL_FILE_NAMES["world_state"],
    "09_world_state.md": REWRITE_GLOBAL_FILE_NAMES["world_state"],
}

COMMON_CHAPTER_STAGE_OUTPUT_RULE = (
    "不要直接输出普通文本答案。"
    "本工作流固定提供 submit_workflow_result 与文档 write/edit/patch 工具。"
    "组生成阶段可以多次调用 write/edit/patch 写入当前组正文和状态文档，全部目标完成后必须调用 submit_workflow_result。"
    "组审和卷审阶段可以先调用 write/edit/patch 原地修复允许范围内的问题，最终必须调用 submit_workflow_result 提交审核结论。"
    "修订已有章节正文、状态文档、审核文档或修正 old_text / match_text 定位时，必须使用文档 write/edit/patch 工具。"
)
COMMON_CHAPTER_STAGE_TOOL_RULE = (
    "本工作流固定提供 submit_workflow_result 与文档 write/edit/patch 工具。"
    "章节仿写的新流程按已审核组纲计划运行：每个动态章节组用同一个 agent 会话处理当前组正文和状态文档。"
    "组纲由卷资料适配阶段生成并审核通过；章节正文阶段只读取组纲，不重新生成组纲，也不读取参考源章节正文。"
    "组生成阶段需要先把文件落盘，最后用 submit_workflow_result 结束；审核阶段可先返修，再用 submit_workflow_result 提交 passed/review_md。"
    "新运行不得创建独立章纲或章级审核文件；旧章纲只作为只读兼容输入。"
)
COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS = (
    "你是资深网络小说章节洗稿仿写作者、连续性编辑与审稿编辑。"
    "用户拥有参考源文本权利。"
    "每次只完成 1 个明确请求。"
    "请严格根据 Dynamic Request 中的 document_request 和当前阶段要求执行。"
    + COMMON_CHAPTER_STAGE_TOOL_RULE
    + document_ops.DOCUMENT_OPERATION_RULE
    + COMMON_CHAPTER_STAGE_OUTPUT_RULE
)
COMMON_SUPPORT_UPDATE_INSTRUCTIONS = COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS
COMMON_CHAPTER_TEXT_REVISION_INSTRUCTIONS = COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS
COMMON_VOLUME_REVIEW_INSTRUCTIONS = COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS
COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS = COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS

FIVE_CHAPTER_REVIEW_NAME = "组审查"


def agent_changed_keys(agent_result: Any) -> list[str]:
    changed_keys: list[str] = []
    for application in list(getattr(agent_result, "applications", []) or []):
        applied = getattr(application, "applied", None)
        if applied is None:
            continue
        for key in getattr(applied, "changed_keys", []) or []:
            key_text = str(key)
            if key_text not in changed_keys:
                changed_keys.append(key_text)
    return changed_keys


def print_agent_application_summary(
    agent_result: Any,
    *,
    agent_label: str,
    no_tool_message: str,
) -> None:
    applications = list(getattr(agent_result, "applications", []) or [])
    if not applications:
        print_progress(no_tool_message)
        return
    changed = ", ".join(agent_changed_keys(agent_result)) or "无内容变化"
    print_progress(f"{agent_label} 本轮执行文档工具 {len(applications)} 次，累计变更={changed}。")


def agent_submission_summary_text(submission: Any, *, limit: int = 120) -> str:
    summary = str(
        getattr(submission, "summary", "")
        or getattr(submission, "content_md", "")
        or getattr(submission, "error", "")
        or ""
    ).strip()
    summary = " ".join(summary.split())
    if not summary:
        return "无"
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rstrip() + "..."


def print_agent_generation_submission_summary(agent_result: Any, *, agent_label: str) -> None:
    submission = getattr(agent_result, "submission", None)
    generated_files = list(getattr(submission, "generated_files", []) or [])
    generated = ", ".join(str(item) for item in generated_files) or "未声明"
    print_progress(
        f"{agent_label} 提交阶段结果：generated_files={generated}；"
        f"摘要={agent_submission_summary_text(submission)}。"
    )


def print_agent_review_submission_summary(review: Any, *, agent_label: str) -> None:
    chapters_to_revise = [str(item).zfill(4) for item in getattr(review, "chapters_to_revise", []) or [] if item]
    rewrite_targets = [str(item) for item in getattr(review, "rewrite_targets", []) or [] if item]
    blocking_issues = [str(item) for item in getattr(review, "blocking_issues", []) or [] if item]
    print_progress(
        f"{agent_label} 提交审核结论："
        f"{'通过' if getattr(review, 'passed', None) else '未通过'}；"
        f"返修章节={'、'.join(chapters_to_revise) if chapters_to_revise else '无'}；"
        f"返修目标={', '.join(rewrite_targets) if rewrite_targets else '无'}；"
        f"阻塞问题={len(blocking_issues)} 项。"
    )

GLOBAL_DOC_LABELS = {
    "world_model": "世界模型",
    "style_guide": "文笔写作风格",
    "book_outline": "全书大纲",
    "foreshadowing": "伏笔管理",
    "character_status_cards": "人物状态卡",
    "character_relationship_graph": "人物关系链",
    "world_state": "世界状态",
}
VOLUME_DOC_LABELS = {
    "volume_outline": "卷级大纲",
    "volume_plot_progress": "卷级剧情进程",
    "volume_review": "卷级审核",
}
CHAPTER_DOC_LABELS = {
    "chapter_outline": "章纲",
    "chapter_review": "章级审核",
    "rewritten_chapter": "仿写章节",
}
GROUP_DOC_LABELS = {
    "group_outline": "组纲",
    "group_review": "组审查",
}

HEADING_MANAGED_DOC_SPECS = {
    "character_status_cards": {
        "template": [
            "# 人物状态卡",
            "## 核心人物当前状态",
            "## 当前目标与压力",
            "## 本章状态变化",
            "## 可扩展人物专题",
        ],
        "section_policy": [
            "一级标题固定为《人物状态卡》。",
            "正文使用二级标题组织信息，不要改写成字段表、数据库表或代码 schema。",
            "已有二级标题如果已经适合本书，就沿用原结构；只有出现新的真实人物维度时才新增二级标题。",
            "可扩展专题允许按本书实际需要拆成如“主角状态”“反派状态”“阵营人物状态”等二级标题。",
        ],
        "update_rules": [
            "只更新受当前章节影响的人物状态与本章状态变化。",
            "无变化人物不要改写；如果本章没有造成状态变化，可以完全不更新此文档。",
        ],
    },
    "character_relationship_graph": {
        "template": [
            "# 人物关系链",
            "## 当前稳定关系",
            "## 当前紧张关系与潜在冲突",
            "## 本章关系变化",
            "## 可扩展关系专题",
        ],
        "section_policy": [
            "一级标题固定为《人物关系链》。",
            "正文使用二级标题管理不同关系类型，不要强行写成角色节点表和关系边表。",
            "已有关系类二级标题尽量保留；如果本书出现血缘、师徒、宗门、阵营、利益绑定等特殊关系维度，可以按需新增二级标题。",
            "新增二级标题必须服务于这本书真实存在的关系类型，不要为了凑结构硬造分类。",
        ],
        "update_rules": [
            "只更新受当前章节影响的人物关系与本章关系变化。",
            "未变化关系必须原样保留，不得为了统一措辞重写整份人物关系链。",
            "如果当前章节没有新增或变化的人际关系，可以完全不更新此文档。",
        ],
    },
    "volume_plot_progress": {
        "template": [
            "# 卷级剧情进程",
            "## 卷主线",
            "### 起始",
            "### 已发生发展",
            "### 关键转折",
            "### 当前状态",
            "### 待推进",
            "## 节奏与冲突抬升",
            "## 本章卷内推进",
            "## 可扩展卷内专题",
        ],
        "section_policy": [
            "一级标题固定为《卷级剧情进程》。",
            "正文用二级标题管理当前卷内不同故事线、线索线、能力线或关系线，不要写成 subplot_id 或 rhythm_track 这类字段表。",
            "默认至少保留“卷主线”“节奏与冲突抬升”“本章卷内推进”“可扩展卷内专题”这些栏目。",
            "如果本卷存在试炼线、宗门线、副本线、感情线、能力线、线索线等真实推进维度，应为每条线单独建立二级标题，而不是把多条线混写在同一个总段落里。",
            "每条故事线二级标题下，都要尽量保持固定三级标题：起始、已发生发展、关键转折、当前状态、待推进。可以按实际需要补充别的三级标题，但不要删除这些核心进度栏。",
            "已有卷内二级标题和三级标题尽量沿用；新增二级标题只在本卷确实出现新的推进维度时才添加。",
        ],
        "update_rules": [
            "只更新受当前章节影响的卷内推进内容与本章卷内推进。",
            "更新时只改受影响故事线下的对应三级标题块，不要整段替换整条故事线；修改已有记录用 edit，追加新记录用 patch。",
            "如果只是给某条故事线补充一条新的推进记录，可以用 patch 的 insert_after 追加到该三级标题下最后一条相关记录后面。",
            "如果只是推进某条故事线的发展，就只更新该线的“已发生发展 / 当前状态 / 待推进”等相关三级标题，不要覆盖它的“起始”或其他未变化部分。",
            "未变化的卷内推进必须原样保留，不得重写整卷进程总结，也不得把每条故事线改成只剩最新发展。",
            "如果当前章节没有推动卷内层面的进程，可以完全不更新此文档。",
        ],
    },
    "foreshadowing": {
        "template": [
            "# 伏笔管理",
            "## 长线伏笔",
            "## 卷内伏笔",
            "## 本章推进 / 回收记录",
            "## 可扩展伏笔专题",
        ],
        "section_policy": [
            "一级标题固定为《伏笔管理》。",
            "正文使用二级标题组织伏笔层级与状态，不必写成刚性的字段表。",
            "如果本书存在特殊伏笔维度，例如“身份谜团”“功法谜团”“势力暗线”，可以按需新增二级标题。",
        ],
        "update_rules": [
            "只补充、推进或回收受当前章节影响的伏笔内容。",
            "无变化伏笔不要重写；如果当前章节没有伏笔变化，可以完全不更新此文档。",
        ],
    },
    "world_state": {
        "template": [
            "# 世界状态",
            "## 当前局势",
            "## 地点与势力动态",
            "## 事件与规则暴露",
            "## 本章状态变化",
            "## 可扩展动态专题",
        ],
        "section_policy": [
            "一级标题固定为《世界状态》。",
            "正文用二级标题管理世界正在发生的动态变化，不要写成 event_id 或 state_id 之类的字段表。",
            "如果本书存在特殊动态维度，如“秘境开启状态”“王朝局势”“宗门排名”“灾变扩散”，可以按需新增二级标题。",
            "已有世界状态二级标题要尽量沿用，只在真实出现新的动态类型时新增标题。",
        ],
        "update_rules": [
            "只更新受当前章节影响的世界动态与本章状态变化。",
            "未变化的状态内容必须原样保留，不得把世界状态改成只剩最近一章。",
            "如果当前章节没有造成世界状态变化，可以完全不更新此文档。",
        ],
    },
}

STABLE_INJECTION_KEYS = {
    "global": ["world_model", "style_guide", "book_outline"],
    "volume": ["volume_outline"],
    "chapter": ["chapter_outline"],
}

PHASE_DOC_SELECTIONS = {
    PHASE1_OUTLINE: {
        "global": [
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
    PHASE2_CHAPTER_TEXT: {
        "global": [
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
    PHASE2_SUPPORT_UPDATES: {
        "global": [
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
    PHASE3_REVIEW: {
        "global": [
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
}

CHAPTER_REWRITE_TARGET_LABELS = {
    "full_workflow": "整章工作流重跑",
    "chapter_outline": "章纲重写并连带后续阶段",
    "chapter_text": "只重写正文并重新审核",
    "support_updates": "只更新配套状态文档并重新审核",
}


REVIEW_KIND_LABELS = {
    "chapter": "章级审核",
    "group": FIVE_CHAPTER_REVIEW_NAME,
    "volume": "卷级审核",
}


# Export imported helpers and workflow constants for the split modules.
__all__ = [name for name in globals() if not name.startswith("_")]
