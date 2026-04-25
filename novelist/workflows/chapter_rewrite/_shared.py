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
import novelist.core.document_ops as document_ops
from novelist.core.novel_source import (
    build_chapter_source_bundle,
    clip_for_context,
    discover_volume_dirs,
    get_chapter_material,
    load_volume_material,
)
from novelist.core.ui import fail, pause_before_exit, print_progress, prompt_choice, prompt_text
import novelist.core.openai_config as openai_config
import novelist.core.responses_runtime as llm_runtime


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
GROUP_ROOT_DIRNAME = "group_injection"
GROUP_DIR_SUFFIX = "_group_injection"
CHAPTER_DIR_SUFFIX = "_chapter_outline"
REWRITTEN_ROOT_DIRNAME = "rewritten_novel"
FIVE_CHAPTER_REVIEW_SIZE = 5
MAX_CHAPTER_REWRITE_ATTEMPTS = 3
MAX_VOLUME_REVIEW_ATTEMPTS = 3
MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS = 2
MAX_REVIEW_FIX_ATTEMPTS = 2
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
    "world_design": "01_world_design.md",
    "world_model": "02_world_model.md",
    "style_guide": "03_style_guide.md",
    "book_outline": "04_book_outline.md",
    "foreshadowing": "05_foreshadowing.md",
    "storyline_blueprint": "06_storyline_blueprint.md",
}
REWRITE_GLOBAL_FILE_NAMES = {
    "character_status_cards": "07_character_status_cards.md",
    "character_relationship_graph": "08_character_relationship_graph.md",
    "world_state": "09_world_state.md",
}
PROMPT_DOC_CONTENT_LIMITS = {
    "world_design": 16000,
    "world_model": 18000,
    "style_guide": 12000,
    "book_outline": 14000,
    "foreshadowing": 10000,
    "storyline_blueprint": 12000,
    "character_status_cards": 12000,
    "character_relationship_graph": 10000,
    "world_state": 10000,
    "volume_outline": 12000,
    "volume_plot_progress": 14000,
    "volume_review": 10000,
    "chapter_outline": 10000,
    "chapter_review": 8000,
    "rewritten_chapter": 30000,
}


def prompt_doc_content_limit(doc_key: str) -> int:
    return PROMPT_DOC_CONTENT_LIMITS.get(doc_key, 12000)

LEGACY_GLOBAL_FILE_RENAMES = {
    "01_book_outline.md": ADAPTATION_GLOBAL_FILE_NAMES["book_outline"],
    "02_world_design.md": ADAPTATION_GLOBAL_FILE_NAMES["world_design"],
    "04_world_model.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "05_global_plot_progress.md": ADAPTATION_GLOBAL_FILE_NAMES["storyline_blueprint"],
    "06_global_plot_progress.md": ADAPTATION_GLOBAL_FILE_NAMES["storyline_blueprint"],
    "06_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "04_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "05_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "08_world_model.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "07_global_plot_progress.md": ADAPTATION_GLOBAL_FILE_NAMES["storyline_blueprint"],
    "08_global_plot_progress.md": ADAPTATION_GLOBAL_FILE_NAMES["storyline_blueprint"],
    "05_character_status_cards.md": REWRITE_GLOBAL_FILE_NAMES["character_status_cards"],
    "06_character_status_cards.md": REWRITE_GLOBAL_FILE_NAMES["character_status_cards"],
    "06_character_relationship_graph.md": REWRITE_GLOBAL_FILE_NAMES["character_relationship_graph"],
    "07_character_relationship_graph.md": REWRITE_GLOBAL_FILE_NAMES["character_relationship_graph"],
}

COMMON_FUNCTION_OUTPUT_RULE = (
    "不要直接输出普通文本答案。"
    "你必须使用提供的函数工具提交最终结果，由程序负责写入文件。"
)
COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS = (
    "你是资深网络小说章节洗稿仿写作者、连续性编辑与审稿编辑。"
    "用户拥有参考源文本权利。"
    "每次只完成 1 个明确请求。"
    "请严格根据输入中的 document_request 和当前阶段要求执行。"
    + COMMON_FUNCTION_OUTPUT_RULE
)
COMMON_SUPPORT_UPDATE_INSTRUCTIONS = COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS + document_ops.DOCUMENT_OPERATION_RULE
COMMON_CHAPTER_TEXT_REVISION_INSTRUCTIONS = COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS + document_ops.DOCUMENT_OPERATION_RULE
COMMON_VOLUME_REVIEW_INSTRUCTIONS = (
    "你是资深网络小说卷级统稿编辑与审校编辑。"
    "用户拥有参考源文本权利。"
    "当前任务只执行当前卷的卷级审核。"
    + COMMON_FUNCTION_OUTPUT_RULE
)
COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS = (
    "你是资深网络小说阶段性连续性编辑与校准审查编辑。"
    "用户拥有参考源文本权利。"
    "当前任务只执行当前五章区间的校准审查。"
    + COMMON_FUNCTION_OUTPUT_RULE
)

WORKFLOW_SUBMISSION_TOOL_NAME = "submit_workflow_result"
WORKFLOW_SUBMISSION_TOOL_DESCRIPTION = (
    "提交当前工作流步骤的结果。"
    "所有步骤都使用同一个函数工具 schema。"
    "Markdown 正文放入 content_md；章节正文放入 chapter_txt；"
    "审核结果使用 passed、review_md、blocking_issues、rewrite_targets、chapters_to_revise；"
    "配套文档更新使用对应 *_md 字段。未使用的字段保留为空字符串、空数组或 null。"
)
FIVE_CHAPTER_REVIEW_NAME = "组审查"

GLOBAL_DOC_LABELS = {
    "world_design": "世界观设计",
    "world_model": "世界模型",
    "style_guide": "文笔写作风格",
    "book_outline": "全书大纲",
    "foreshadowing": "伏笔管理",
    "storyline_blueprint": "全书故事线蓝图",
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
            "未变化的卷内推进必须原样保留，不得重写整卷进程总结，也不得把每条故事线压缩成只剩最新发展。",
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
            "未变化的状态内容必须原样保留，不得把世界状态压缩成只剩最近一章。",
            "如果当前章节没有造成世界状态变化，可以完全不更新此文档。",
        ],
    },
}

STABLE_INJECTION_KEYS = {
    "global": ["world_design", "world_model", "style_guide", "book_outline", "storyline_blueprint"],
    "volume": ["volume_outline"],
    "chapter": ["chapter_outline"],
}

PHASE_DOC_SELECTIONS = {
    PHASE1_OUTLINE: {
        "global": [
            "world_design",
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "storyline_blueprint",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
    PHASE2_CHAPTER_TEXT: {
        "global": [
            "world_design",
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "storyline_blueprint",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
    PHASE2_SUPPORT_UPDATES: {
        "global": [
            "world_design",
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "storyline_blueprint",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
    PHASE3_REVIEW: {
        "global": [
            "world_design",
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "storyline_blueprint",
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
