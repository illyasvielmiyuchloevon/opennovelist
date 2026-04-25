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
    read_text,
    read_text_if_exists,
    sanitize_file_name,
    write_markdown_data,
    write_text_if_changed,
)
from novelist.core.ui import fail, pause_before_exit, print_progress, prompt_choice, prompt_text
import novelist.core.document_ops as document_ops
import novelist.core.openai_config as openai_config
import novelist.core.responses_runtime as llm_runtime


PROJECT_MANIFEST_NAME = "00_project_manifest.md"
LEGACY_PROJECT_MANIFEST_NAME = "00_project_manifest.json"
GLOBAL_CONFIG_DIR = Path.home() / ".novel_adaptation"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.json"
LEGACY_GLOBAL_CONFIG_DIR = Path.home() / ".novel_adaptation_cli"
LEGACY_GLOBAL_CONFIG_PATH = LEGACY_GLOBAL_CONFIG_DIR / "config.json"
GLOBAL_DIRNAME = "global_injection"
VOLUME_ROOT_DIRNAME = "volume_injection"
VOLUME_DIR_SUFFIX = "_volume_injection"
GLOBAL_FILE_NAMES = {
    "world_design": "01_world_design.md",
    "world_model": "02_world_model.md",
    "style_guide": "03_style_guide.md",
    "book_outline": "04_book_outline.md",
    "foreshadowing": "05_foreshadowing.md",
    "storyline_blueprint": "06_storyline_blueprint.md",
}
GLOBAL_INJECTION_DOC_ORDER = [
    "world_model",
    "style_guide",
    "book_outline",
    "foreshadowing",
    "storyline_blueprint",
]
ADAPTATION_DOC_CONTEXT_LIMITS = {
    "world_model": 18000,
    "style_guide": 12000,
    "book_outline": 14000,
    "foreshadowing": 10000,
    "storyline_blueprint": 12000,
    "volume_outline": 12000,
}


def adaptation_doc_context_limit(doc_key: str) -> int:
    return ADAPTATION_DOC_CONTEXT_LIMITS.get(doc_key, 12000)
LEGACY_GLOBAL_FILE_RENAMES = {
    "01_book_outline.md": GLOBAL_FILE_NAMES["book_outline"],
    "02_world_design.md": GLOBAL_FILE_NAMES["world_design"],
    "04_world_model.md": GLOBAL_FILE_NAMES["world_model"],
    "05_global_plot_progress.md": GLOBAL_FILE_NAMES["storyline_blueprint"],
    "06_global_plot_progress.md": GLOBAL_FILE_NAMES["storyline_blueprint"],
    "06_foreshadowing.md": GLOBAL_FILE_NAMES["foreshadowing"],
    "04_foreshadowing.md": GLOBAL_FILE_NAMES["foreshadowing"],
    "05_foreshadowing.md": GLOBAL_FILE_NAMES["foreshadowing"],
    "08_world_model.md": GLOBAL_FILE_NAMES["world_model"],
    "07_global_plot_progress.md": GLOBAL_FILE_NAMES["storyline_blueprint"],
    "08_global_plot_progress.md": GLOBAL_FILE_NAMES["storyline_blueprint"],
    "05_character_status_cards.md": "07_character_status_cards.md",
    "06_character_status_cards.md": "07_character_status_cards.md",
    "06_character_relationship_graph.md": "08_character_relationship_graph.md",
    "07_character_relationship_graph.md": "08_character_relationship_graph.md",
}
WORLD_MODEL_DEFAULT_SECTIONS = [
    "世界背景与时代",
    "历史与大事件",
    "地图、地域与地点体系",
    "势力与组织体系",
    "身份阶层与社会结构",
    "规则与底层常识",
    "能力与修炼体系",
    "功法 / 技能 / 神通 / 武学",
    "装备 / 道具 / 资源 / 材料",
    "职业 / 副职业 / 生产体系",
    "血脉 / 体质 / 天赋 / 特殊资格",
    "制度 / 禁忌 / 风俗 / 日常常识",
    "关键词与术语表",
    "已公开真相 / 未公开真相",
    "本卷新增或修正世界知识",
    "可扩展世界专题",
]
STORYLINE_BLUEPRINT_DEFAULT_SECTIONS = [
    "蓝图定位",
    "参考源功能映射",
    "分卷蓝图",
    "跨卷递进",
    "待后续补全",
]
STYLE_MODE_CUSTOM = "custom_style_file"
STYLE_MODE_SOURCE = "reference_source_style"
PROTAGONIST_MODE_CUSTOM = "custom_design"
PROTAGONIST_MODE_ADAPTIVE = "adaptive_from_source"
DEFAULT_API_RETRIES = 10
DEFAULT_RETRY_DELAY_SECONDS = 5
MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS = 2
MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS = 2
RUN_MODE_STAGE = "stage"
RUN_MODE_BOOK = "book"
RUN_MODE_LABELS = {
    RUN_MODE_STAGE: "按阶段运行",
    RUN_MODE_BOOK: "按全书运行",
}
ADAPTATION_REVIEW_TOOL_NAME = "submit_adaptation_review"
ADAPTATION_REVIEW_TOOL_DESCRIPTION = (
    "提交每卷改编资料审核结果。必须判断当前卷资料文档是否已经满足后续仿写需要，"
    "并在不通过时给出阻塞问题与可原地修复的目标文件 key。"
)


COMMON_DOCUMENT_OUTPUT_RULE = (
    "不要直接输出普通文本答案。"
    "你必须使用提供的文档工具提交结果，由程序负责写入或 patch 到文件。"
)
COMMON_STAGE_DOCUMENT_INSTRUCTIONS = (
    "你是资深网络小说改编规划编辑。"
    "用户拥有参考源文本权利。"
    "当前任务每次只处理 1 份目标文档。"
    "请严格根据输入中的 document_request 执行。"
    "严禁把参考源的人名、地名、姓氏、势力名、事件名、专用术语、等级体系、称谓口吻或话语体系直接写入新书资料；"
    "必须转换成新书自己的命名、设定与表达，只保留功能映射。"
    + document_ops.DOCUMENT_OPERATION_RULE
    + COMMON_DOCUMENT_OUTPUT_RULE
)
COMMON_ADAPTATION_REVIEW_INSTRUCTIONS = (
    "你是资深网络小说仿写资料总审核编辑。"
    "用户拥有参考源文本权利。"
    "当前任务是审核本卷已经生成或继承的改编资料是否能支撑后续章节仿写。"
    "不要直接输出普通文本答案，必须调用 submit_adaptation_review 提交结构化审核结果。"
)
COMMON_ADAPTATION_REVIEW_FIX_INSTRUCTIONS = (
    "你是资深网络小说仿写资料原地返修编辑。"
    "用户拥有参考源文本权利。"
    "当前任务不是重新审核，也不是重新生成整卷资料；"
    "你只能根据上一轮未通过的审核结果，直接修复允许范围内的目标资料文档。"
    + document_ops.DOCUMENT_OPERATION_RULE
    + COMMON_DOCUMENT_OUTPUT_RULE
)


# Export imported helpers and workflow constants for the split modules.
__all__ = [name for name in globals() if not name.startswith("_")]
