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
GLOBAL_CONFIG_DIR = Path.home() / ".novel_adaptation_cli"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
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
    "global_plot_progress": "06_global_plot_progress.md",
}
REWRITE_GLOBAL_FILE_NAMES = {
    "character_status_cards": "07_character_status_cards.md",
    "character_relationship_graph": "08_character_relationship_graph.md",
    "world_state": "09_world_state.md",
}
LEGACY_GLOBAL_FILE_RENAMES = {
    "01_book_outline.md": ADAPTATION_GLOBAL_FILE_NAMES["book_outline"],
    "02_world_design.md": ADAPTATION_GLOBAL_FILE_NAMES["world_design"],
    "04_world_model.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "05_global_plot_progress.md": ADAPTATION_GLOBAL_FILE_NAMES["global_plot_progress"],
    "06_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "04_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "05_foreshadowing.md": ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
    "08_world_model.md": ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
    "07_global_plot_progress.md": ADAPTATION_GLOBAL_FILE_NAMES["global_plot_progress"],
    "08_global_plot_progress.md": ADAPTATION_GLOBAL_FILE_NAMES["global_plot_progress"],
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
    "global_plot_progress": "全局剧情进程",
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
            "更新时优先只 patch 受影响故事线下的对应三级标题块，不要整段替换整条故事线。",
            "如果只是给某条故事线补充一条新的推进记录，优先用 insert_after 追加到该三级标题下最后一条相关记录后面。",
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
    "global": ["world_design", "world_model", "style_guide", "book_outline", "global_plot_progress"],
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
            "global_plot_progress",
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
            "global_plot_progress",
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
            "global_plot_progress",
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
            "global_plot_progress",
            "character_status_cards",
            "character_relationship_graph",
            "world_state",
        ],
        "volume": ["volume_outline", "volume_plot_progress", "volume_review"],
        "chapter": ["chapter_outline", "chapter_review"],
    },
}


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


def load_chapter_review_skill_reference() -> dict[str, Any]:
    content = read_text_if_exists(CHAPTER_REVIEW_SKILL_PATH).strip()
    if not content:
        return {
            "label": "AI 痕迹审查 Skill",
            "file_name": CHAPTER_REVIEW_SKILL_PATH.name,
            "file_path": str(CHAPTER_REVIEW_SKILL_PATH),
            "content": "",
        }
    return {
        "label": "AI 痕迹审查 Skill",
        "file_name": CHAPTER_REVIEW_SKILL_PATH.name,
        "file_path": str(CHAPTER_REVIEW_SKILL_PATH),
        "content": clip_for_context(content, limit=50000),
    }


def load_chapter_writing_skill_reference() -> dict[str, Any]:
    content = read_text_if_exists(CHAPTER_WRITING_SKILL_PATH).strip()
    if not content:
        return {
            "label": "写作规范 Skill",
            "file_name": CHAPTER_WRITING_SKILL_PATH.name,
            "file_path": str(CHAPTER_WRITING_SKILL_PATH),
            "content": "",
        }
    return {
        "label": "写作规范 Skill",
        "file_name": CHAPTER_WRITING_SKILL_PATH.name,
        "file_path": str(CHAPTER_WRITING_SKILL_PATH),
        "content": clip_for_context(content, limit=50000),
    }


def normalize_review_chapter_numbers(
    values: list[str],
    *,
    allowed_chapters: set[str] | None = None,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            continue
        chapter_number = digits.zfill(4)
        if allowed_chapters is not None and chapter_number not in allowed_chapters:
            continue
        if chapter_number in seen:
            continue
        seen.add(chapter_number)
        normalized.append(chapter_number)
    return normalized


def infer_review_passed(
    payload: WorkflowSubmissionPayload,
    *,
    fallback_text: str = "",
) -> bool | None:
    if payload.passed is not None:
        return payload.passed

    if payload.blocking_issues or payload.rewrite_targets or payload.chapters_to_revise:
        return False

    combined_text = "\n".join(
        part.strip()
        for part in (payload.review_md, payload.content_md, fallback_text)
        if isinstance(part, str) and part.strip()
    )
    if not combined_text:
        return None

    normalized_text = combined_text.replace("*", "").replace("`", "")

    if re.search(r"(未通过|不通过|需返工|需要返工|必须返工|存在重大问题)", normalized_text):
        return False
    if re.search(r"(审核通过|本章通过|通过。|通过$|总体结论[^。\n]*通过|结论[^。\n]*通过)", normalized_text):
        return True
    return None


def extract_chapter_numbers_from_text(
    text: str,
    *,
    allowed_chapters: set[str] | None = None,
) -> list[str]:
    matches = re.findall(r"\b\d{1,4}\b", text)
    return normalize_review_chapter_numbers(matches, allowed_chapters=allowed_chapters)


def build_canonical_review_markdown(
    *,
    review_kind: str,
    passed: bool,
    review_md: str,
    blocking_issues: list[str],
    rewrite_targets: list[str],
    chapters_to_revise: list[str],
) -> str:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    original = review_md.strip()
    canonical_sections = {
        "总体结论",
        "核心问题",
        "需要返工的章节",
        "需要返工的对象",
        "修改建议",
        "详细审查说明",
    }
    if original and any(f"## {heading}" in original for heading in canonical_sections):
        return original

    lines = [
        f"# {label}",
        "",
        "## 总体结论",
        f"- **{'通过' if passed else '不通过'}**",
        "",
        "## 核心问题",
    ]

    if blocking_issues:
        lines.extend(f"- {item}" for item in blocking_issues)
    else:
        lines.append("- 无。")

    if review_kind in {"group", "volume"}:
        lines.extend(["", "## 需要返工的章节"])
        if chapters_to_revise:
            lines.extend(f"- {item}" for item in chapters_to_revise)
        else:
            lines.append("- 无。")
    else:
        lines.extend(["", "## 需要返工的对象"])
        if rewrite_targets:
            lines.extend(f"- {item}" for item in rewrite_targets)
        else:
            lines.append("- 无。")

    lines.extend(["", "## 修改建议"])
    if original:
        lines.append(original)
    elif not passed:
        lines.append("- 请根据上述问题返工。")
    else:
        lines.append("- 当前产物可继续进入下一阶段。")

    return "\n".join(lines).strip()


def review_output_contract_lines(review_kind: str) -> list[str]:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    range_section_title = "需要返工的章节" if review_kind in {"group", "volume"} else "需要返工的对象"
    lines = [
        f"必须通过函数工具返回完整的{label}结果，至少包含 passed 和 review_md。",
        f"review_md 必须使用固定骨架：# {label} / ## 总体结论 / ## 核心问题 / ## {range_section_title} / ## 修改建议。",
        "如果 passed=true，review_md 的总体结论必须明确写“通过”。",
        "如果 passed=false，review_md 的总体结论必须明确写“不通过”，并在对应返工章节或返工对象小节中列出需要返工的内容。",
    ]
    if review_kind == "chapter":
        lines.append(
            "章级审核不通过时，rewrite_targets 必须只使用这些返工对象："
            " full_workflow / chapter_outline / chapter_text / support_updates，"
            "如果只需改正文就只写 chapter_text；如果只需改配套状态文档就只写 support_updates；"
            "如果章纲到正文都要重来就写 full_workflow。"
        )
    else:
        lines.append(
            f"{label}不通过时，rewrite_targets 必须使用“章节号:返工对象”格式，"
            "例如 0003:chapter_text、0004:support_updates、0005:full_workflow。"
        )
    return lines


def finalize_review_payload(
    payload: WorkflowSubmissionPayload,
    *,
    review_kind: str,
    allowed_chapters: list[str] | None = None,
) -> WorkflowSubmissionPayload:
    allowed_set = set(allowed_chapters or [])
    fallback_text = payload.content_md.strip()
    chapters_to_revise = normalize_review_chapter_numbers(
        payload.chapters_to_revise,
        allowed_chapters=allowed_set if allowed_set else None,
    )

    if not chapters_to_revise and review_kind in {"group", "volume"}:
        inferred = extract_chapter_numbers_from_text(
            "\n".join(
                [
                    payload.review_md.strip(),
                    payload.content_md.strip(),
                    "\n".join(payload.blocking_issues),
                    "\n".join(payload.rewrite_targets),
                ]
            ),
            allowed_chapters=allowed_set if allowed_set else None,
        )
        chapters_to_revise = inferred

    passed = infer_review_passed(
        payload.model_copy(update={"chapters_to_revise": chapters_to_revise}),
        fallback_text=fallback_text,
    )
    if passed is None:
        raise llm_runtime.ModelOutputError("模型未通过统一函数工具返回明确的审核结论。")

    review_md_source = payload.review_md.strip() or fallback_text
    if not review_md_source:
        review_md_source = "模型未提供审核正文，已根据结构化字段生成标准化审查摘要。"

    canonical_review_md = build_canonical_review_markdown(
        review_kind=review_kind,
        passed=passed,
        review_md=review_md_source,
        blocking_issues=payload.blocking_issues,
        rewrite_targets=payload.rewrite_targets,
        chapters_to_revise=chapters_to_revise,
    )

    return payload.model_copy(
        update={
            "passed": passed,
            "review_md": canonical_review_md,
            "chapters_to_revise": chapters_to_revise,
        }
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "基于 novel_adaptation_cli 产出的工程目录，逐章生成仿写章节、配套状态文档与审核文档，"
            "使用 OpenAI Responses API 与 core 运行时。"
        )
    )
    parser.add_argument(
        "input_root",
        nargs="?",
        help="已有小说工程目录路径，或 split_novel 的来源目录路径；不传则启动后提示输入。",
    )
    parser.add_argument("--base-url", help="OpenAI Responses API 的 base_url。")
    parser.add_argument("--api-key", help="OpenAI API Key。")
    parser.add_argument("--model", help="调用的模型名称。")
    parser.add_argument("--volume", help="指定处理某一卷，例如 001。")
    parser.add_argument("--chapter", help="指定处理某一章，例如 0001。")
    parser.add_argument(
        "--run-mode",
        choices=(RUN_MODE_CHAPTER, RUN_MODE_GROUP, RUN_MODE_VOLUME),
        help="运行模式：按章节运行、按组运行、按卷运行。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只识别工程、卷状态和待处理章节，不调用 API。",
    )
    parser.add_argument(
        "--workflow-controlled",
        action="store_true",
        help="由统一工作流入口调度时启用：当前只处理本次目标范围，完成后直接返回，不在子 CLI 内继续下一章/组/卷。",
    )
    return parser.parse_args()


def validate_source_root(source_root: Path) -> None:
    if not source_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{source_root}")
    if not discover_volume_dirs(source_root):
        fail("当前目录下未识别到 split_novel 产出的编号卷目录，例如 001、002。")


def load_project_manifest(project_root: Path) -> dict[str, Any] | None:
    manifest_path = project_root / PROJECT_MANIFEST_NAME
    if manifest_path.exists():
        return extract_json_payload(manifest_path.read_text(encoding="utf-8"))

    legacy_path = project_root / LEGACY_PROJECT_MANIFEST_NAME
    if legacy_path.exists():
        return json.loads(legacy_path.read_text(encoding="utf-8"))
    return None


def manifest_matches_source_root(manifest: dict[str, Any], source_root: Path) -> bool:
    manifest_source = manifest.get("source_root")
    if not manifest_source:
        return False
    try:
        return normalize_path(str(manifest_source)) == source_root.resolve()
    except Exception:
        return False


def find_existing_project_for_source(source_root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []

    for child in source_root.parent.iterdir():
        if not child.is_dir() or child.resolve() == source_root.resolve():
            continue
        manifest = load_project_manifest(child)
        if manifest and manifest_matches_source_root(manifest, source_root):
            candidates.append((str(manifest.get("updated_at", "")), child, manifest))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, project_root, manifest = candidates[0]
    return project_root, manifest


def resolve_project_input(
    raw_path: str | None,
    global_config: dict[str, Any],
) -> tuple[Path, Path, dict[str, Any]]:
    default_path = (
        global_config.get("last_chapter_rewrite_input_root")
        or global_config.get("last_project_root")
        or global_config.get("last_input_root")
        or global_config.get("last_source_root")
    )
    if raw_path is None:
        raw_path = prompt_text(
            "请输入 novel_adaptation_cli 的工程目录路径，或 split_novel 的来源目录路径",
            default=str(default_path) if default_path else None,
        )

    input_root = normalize_path(raw_path)
    if not input_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{input_root}")
    if not input_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{input_root}")

    manifest = load_project_manifest(input_root)
    if manifest is not None:
        source_root = normalize_path(str(manifest["source_root"]))
        validate_source_root(source_root)
        return input_root, source_root, manifest

    source_root = input_root
    validate_source_root(source_root)
    project_root, manifest = find_existing_project_for_source(source_root)
    if project_root is None or manifest is None:
        fail(
            "未在该来源目录旁边识别到 novel_adaptation_cli 的工程目录。"
            "请传入已有工程目录，或先运行 novel_adaptation_cli。"
        )
    return project_root, source_root, manifest


def resolve_run_mode(args: argparse.Namespace) -> str:
    if args.run_mode:
        return args.run_mode
    return prompt_choice(
        "请选择运行方式",
        [
            (RUN_MODE_CHAPTER, "按章节运行"),
            (RUN_MODE_GROUP, "按组运行"),
            (RUN_MODE_VOLUME, "按卷运行"),
        ],
    )


def load_rewrite_manifest(project_root: Path) -> dict[str, Any] | None:
    manifest_path = project_root / REWRITE_MANIFEST_NAME
    if not manifest_path.exists():
        return None
    return extract_json_payload(manifest_path.read_text(encoding="utf-8"))


def ensure_rewrite_dirs(project_root: Path) -> list[str]:
    global_dir = project_root / GLOBAL_DIRNAME
    global_dir.mkdir(parents=True, exist_ok=True)
    warnings = migrate_renamed_files(global_dir, LEGACY_GLOBAL_FILE_RENAMES)
    (project_root / REWRITTEN_ROOT_DIRNAME).mkdir(parents=True, exist_ok=True)
    migrate_numbered_injection_dirs(
        project_root,
        container_dirname=VOLUME_ROOT_DIRNAME,
        suffix=VOLUME_DIR_SUFFIX,
    )
    migrate_numbered_injection_dirs(
        project_root,
        container_dirname=GROUP_ROOT_DIRNAME,
        suffix=GROUP_DIR_SUFFIX,
    )
    return warnings


def save_rewrite_manifest(manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = now_iso()
    write_markdown_data(
        Path(manifest["project_root"]) / REWRITE_MANIFEST_NAME,
        title="Chapter Rewrite Manifest",
        payload=manifest,
        summary_lines=[
            f"new_book_title: {manifest['new_book_title']}",
            f"source_root: {manifest['source_root']}",
            f"rewrite_output_root: {manifest['rewrite_output_root']}",
            f"processed_volumes: {', '.join(manifest.get('processed_volumes', [])) or 'none'}",
            f"last_processed_volume: {manifest.get('last_processed_volume') or 'none'}",
            f"last_processed_chapter: {manifest.get('last_processed_chapter') or 'none'}",
        ],
    )


def init_or_load_rewrite_manifest(
    project_root: Path,
    source_root: Path,
    project_manifest: dict[str, Any],
    volume_dirs: list[Path],
) -> dict[str, Any]:
    existing = load_rewrite_manifest(project_root)
    if existing is not None:
        existing["total_volumes"] = len(volume_dirs)
        existing["rewrite_output_root"] = str(project_root / REWRITTEN_ROOT_DIRNAME)
        save_rewrite_manifest(existing)
        return existing

    manifest = {
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "project_root": str(project_root),
        "source_root": str(source_root),
        "new_book_title": project_manifest["new_book_title"],
        "target_worldview": project_manifest.get("target_worldview", ""),
        "rewrite_output_root": str(project_root / REWRITTEN_ROOT_DIRNAME),
        "total_volumes": len(volume_dirs),
        "processed_volumes": [],
        "last_processed_volume": None,
        "last_processed_chapter": None,
        "chapter_states": {},
        "volume_review_states": {},
        "five_chapter_review_states": {},
    }
    save_rewrite_manifest(manifest)
    return manifest


def rewrite_paths(project_root: Path, volume_number: str, chapter_number: str | None = None) -> dict[str, Path]:
    global_dir = project_root / GLOBAL_DIRNAME
    volume_root_dir = project_root / VOLUME_ROOT_DIRNAME
    volume_dir = volume_root_dir / f"{volume_number}{VOLUME_DIR_SUFFIX}"
    rewritten_root = project_root / REWRITTEN_ROOT_DIRNAME
    rewritten_volume_dir = rewritten_root / volume_number
    paths: dict[str, Path] = {
        "global_dir": global_dir,
        "volume_root_dir": volume_root_dir,
        "volume_dir": volume_dir,
        "rewritten_root": rewritten_root,
        "rewritten_volume_dir": rewritten_volume_dir,
        "book_outline": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["book_outline"],
        "world_design": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["world_design"],
        "style_guide": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["style_guide"],
        "global_plot_progress": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["global_plot_progress"],
        "world_model": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
        "foreshadowing": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
        "character_status_cards": global_dir / REWRITE_GLOBAL_FILE_NAMES["character_status_cards"],
        "character_relationship_graph": global_dir / REWRITE_GLOBAL_FILE_NAMES["character_relationship_graph"],
        "world_state": global_dir / REWRITE_GLOBAL_FILE_NAMES["world_state"],
        "volume_outline": volume_dir / f"{volume_number}_volume_outline.md",
        "volume_plot_progress": volume_dir / f"{volume_number}_volume_plot_progress.md",
        "volume_review": volume_dir / f"{volume_number}_volume_review.md",
    }
    if chapter_number is not None:
        chapter_dir = volume_dir / f"{chapter_number}{CHAPTER_DIR_SUFFIX}"
        paths.update(
            {
                "chapter_dir": chapter_dir,
                "chapter_outline": chapter_dir / f"{chapter_number}_chapter_outline.md",
                "chapter_review": chapter_dir / f"{chapter_number}_chapter_review.md",
                "chapter_stage_manifest": chapter_dir / "00_stage_manifest.md",
                "chapter_response_debug": chapter_dir / "00_last_response_debug.md",
                "rewritten_chapter": rewritten_volume_dir / f"{chapter_number}.txt",
            }
        )
    else:
        paths.update(
            {
                "volume_stage_manifest": volume_dir / "00_volume_rewrite_manifest.md",
                "volume_response_debug": volume_dir / "00_volume_review_debug.md",
            }
        )
    return paths


def assess_volume_readiness(project_root: Path, source_root: Path, volume_number: str) -> dict[str, Any]:
    paths = rewrite_paths(project_root, volume_number)
    missing: list[str] = []

    source_volume_dir = source_root / volume_number
    if not source_volume_dir.exists():
        missing.append(f"缺少来源卷目录：{source_volume_dir}")

    for key, file_name in ADAPTATION_GLOBAL_FILE_NAMES.items():
        if not paths[key].exists():
            missing.append(f"缺少全局注入文档：{file_name}")

    if not paths["volume_outline"].exists():
        missing.append(f"缺少卷级大纲：{paths['volume_outline'].name}")

    return {
        "volume_number": volume_number,
        "eligible": not missing,
        "missing": missing,
    }


def print_volume_readiness_summary(readiness_map: dict[str, dict[str, Any]]) -> None:
    print_progress("卷可进入章节工作流的检测结果：")
    for volume_number in sorted(readiness_map):
        info = readiness_map[volume_number]
        if info["eligible"]:
            print_progress(f"  第 {volume_number} 卷：可进入章节工作流。")
        else:
            print_progress(f"  第 {volume_number} 卷：暂不可进入章节工作流。")
            for reason in info["missing"]:
                print_progress(f"    - {reason}")


def get_chapter_state(manifest: dict[str, Any], volume_number: str, chapter_number: str) -> dict[str, Any]:
    chapter_states = manifest.setdefault("chapter_states", {})
    volume_states = chapter_states.setdefault(volume_number, {})
    state = volume_states.setdefault(
        chapter_number,
        {
            "status": "pending",
            "attempts": 0,
            "last_stage": None,
            "updated_at": None,
            "blocking_issues": [],
            "pending_phases": [],
            "rewrite_targets": [],
            "revision_origin": None,
        },
    )
    state.setdefault("pending_phases", [])
    state.setdefault("rewrite_targets", [])
    state.setdefault("revision_origin", None)
    return state


def update_chapter_state(
    manifest: dict[str, Any],
    volume_number: str,
    chapter_number: str,
    **updates: Any,
) -> dict[str, Any]:
    state = get_chapter_state(manifest, volume_number, chapter_number)
    state.update({key: value for key, value in updates.items() if value is not None})
    state["updated_at"] = now_iso()
    manifest["last_processed_volume"] = volume_number
    manifest["last_processed_chapter"] = chapter_number
    save_rewrite_manifest(manifest)
    return state


def full_chapter_workflow_plan() -> list[str]:
    return list(CHAPTER_WORKFLOW_PHASE_ORDER)


def normalize_phase_plan(phases: list[str]) -> list[str]:
    allowed = set(CHAPTER_WORKFLOW_PHASE_ORDER)
    normalized: list[str] = []
    for phase in CHAPTER_WORKFLOW_PHASE_ORDER:
        if phase in phases and phase in allowed and phase not in normalized:
            normalized.append(phase)
    return normalized


def revision_plan_label(phases: list[str]) -> str:
    if not phases:
        return "无待重跑阶段"
    if phases == full_chapter_workflow_plan():
        return CHAPTER_REWRITE_TARGET_LABELS["full_workflow"]
    if phases == [PHASE2_CHAPTER_TEXT, PHASE3_REVIEW]:
        return CHAPTER_REWRITE_TARGET_LABELS["chapter_text"]
    if phases == [PHASE2_SUPPORT_UPDATES, PHASE3_REVIEW]:
        return CHAPTER_REWRITE_TARGET_LABELS["support_updates"]
    if phases == [PHASE2_CHAPTER_TEXT, PHASE2_SUPPORT_UPDATES, PHASE3_REVIEW]:
        return "正文重写 + 配套状态文档更新 + 重新审核"
    return " -> ".join(phases)


def normalize_rewrite_target_token(token: str) -> str:
    normalized = token.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized


def phase_plan_from_single_rewrite_target(target: str) -> list[str]:
    token = normalize_rewrite_target_token(target)
    support_update_aliases = {
        PHASE2_SUPPORT_UPDATES,
        "support_updates",
        "state_docs",
        "state_documents",
        "document_updates",
        "support_docs",
        "character_status_cards",
        "character_relationship_graph",
        "volume_plot_progress",
        "foreshadowing",
        "world_state",
    }
    chapter_text_aliases = {
        PHASE2_CHAPTER_TEXT,
        "chapter_text",
        "text",
        "rewritten_chapter",
    }
    outline_aliases = {
        PHASE1_OUTLINE,
        "phase1",
        "chapter_outline",
        "outline",
    }
    full_aliases = {
        "full_workflow",
        "full",
        "all",
        "rerun_all",
        "entire_chapter",
    }

    if token in full_aliases:
        return full_chapter_workflow_plan()
    if token in outline_aliases:
        return full_chapter_workflow_plan()
    if token in chapter_text_aliases:
        return [PHASE2_CHAPTER_TEXT, PHASE3_REVIEW]
    if token in support_update_aliases:
        return [PHASE2_SUPPORT_UPDATES, PHASE3_REVIEW]
    return []


def merge_phase_plans(*phase_lists: list[str]) -> list[str]:
    merged: list[str] = []
    for phase_list in phase_lists:
        for phase in phase_list:
            if phase not in merged:
                merged.append(phase)
    normalized = normalize_phase_plan(merged)
    if PHASE1_OUTLINE in normalized:
        return full_chapter_workflow_plan()
    if PHASE2_CHAPTER_TEXT in normalized and PHASE2_SUPPORT_UPDATES in normalized:
        return [PHASE2_CHAPTER_TEXT, PHASE2_SUPPORT_UPDATES, PHASE3_REVIEW]
    return normalized


def build_chapter_revision_plan(
    *,
    rewrite_targets: list[str],
    fallback_full_workflow: bool = True,
) -> list[str]:
    plans = [phase_plan_from_single_rewrite_target(item) for item in rewrite_targets if str(item).strip()]
    merged = merge_phase_plans(*plans)
    if merged:
        return merged
    if fallback_full_workflow:
        return full_chapter_workflow_plan()
    return []


def build_multi_chapter_revision_plan(
    *,
    chapters_to_revise: list[str],
    rewrite_targets: list[str],
) -> dict[str, list[str]]:
    normalized_chapters = [item.zfill(4) for item in chapters_to_revise if item]
    revision_plan: dict[str, list[str]] = {
        chapter_number: full_chapter_workflow_plan() for chapter_number in normalized_chapters
    }
    for raw_target in rewrite_targets:
        text = str(raw_target).strip()
        if not text or ":" not in text:
            continue
        chapter_number_raw, target = text.split(":", 1)
        chapter_number = "".join(ch for ch in chapter_number_raw if ch.isdigit()).zfill(4)
        if chapter_number not in revision_plan:
            continue
        current_plan = revision_plan[chapter_number]
        target_plan = build_chapter_revision_plan(
            rewrite_targets=[target],
            fallback_full_workflow=False,
        )
        if target_plan:
            revision_plan[chapter_number] = merge_phase_plans(current_plan if current_plan != full_chapter_workflow_plan() else [], target_plan) or current_plan
    return revision_plan


def rewrite_targets_for_chapter(chapter_number: str, rewrite_targets: list[str]) -> list[str]:
    normalized = chapter_number.zfill(4)
    local_targets: list[str] = []
    for raw_target in rewrite_targets:
        text = str(raw_target).strip()
        if not text:
            continue
        if ":" in text:
            chapter_number_raw, target = text.split(":", 1)
            current = "".join(ch for ch in chapter_number_raw if ch.isdigit()).zfill(4)
            if current != normalized:
                continue
            local_targets.append(target.strip())
        else:
            local_targets.append(text)
    return local_targets


def chapter_pending_phase_plan(
    manifest: dict[str, Any],
    volume_number: str,
    chapter_number: str,
) -> list[str]:
    state = get_chapter_state(manifest, volume_number, chapter_number)
    pending = normalize_phase_plan(list(state.get("pending_phases", [])))
    if pending:
        return pending
    if state.get("status") == "needs_revision":
        return full_chapter_workflow_plan()
    return full_chapter_workflow_plan()


def get_volume_review_state(manifest: dict[str, Any], volume_number: str) -> dict[str, Any]:
    review_states = manifest.setdefault("volume_review_states", {})
    return review_states.setdefault(
        volume_number,
        {
            "status": "pending",
            "attempts": 0,
            "chapters_to_revise": [],
            "updated_at": None,
            "blocking_issues": [],
        },
    )


def update_volume_review_state(
    manifest: dict[str, Any],
    volume_number: str,
    **updates: Any,
) -> dict[str, Any]:
    state = get_volume_review_state(manifest, volume_number)
    state.update({key: value for key, value in updates.items() if value is not None})
    state["updated_at"] = now_iso()
    save_rewrite_manifest(manifest)
    return state


def get_five_chapter_review_state(
    manifest: dict[str, Any],
    volume_number: str,
    batch_id: str,
    chapter_numbers: list[str],
) -> dict[str, Any]:
    review_states = manifest.setdefault("five_chapter_review_states", {})
    volume_states = review_states.setdefault(volume_number, {})
    return volume_states.setdefault(
        batch_id,
        {
            "status": "pending",
            "attempts": 0,
            "chapter_numbers": list(chapter_numbers),
            "chapters_to_revise": [],
            "updated_at": None,
            "blocking_issues": [],
        },
    )


def update_five_chapter_review_state(
    manifest: dict[str, Any],
    volume_number: str,
    batch_id: str,
    chapter_numbers: list[str],
    **updates: Any,
) -> dict[str, Any]:
    state = get_five_chapter_review_state(manifest, volume_number, batch_id, chapter_numbers)
    state.update({key: value for key, value in updates.items() if value is not None})
    state["chapter_numbers"] = list(chapter_numbers)
    state["updated_at"] = now_iso()
    save_rewrite_manifest(manifest)
    return state


def build_five_chapter_groups(volume_material: dict[str, Any]) -> list[list[str]]:
    chapter_numbers = [chapter["chapter_number"] for chapter in volume_material["chapters"]]
    return [
        chapter_numbers[index : index + FIVE_CHAPTER_REVIEW_SIZE]
        for index in range(0, len(chapter_numbers), FIVE_CHAPTER_REVIEW_SIZE)
    ]


def five_chapter_batch_id(chapter_numbers: list[str]) -> str:
    if not chapter_numbers:
        fail("组审查区间不能为空。")
    return f"{chapter_numbers[0]}_{chapter_numbers[-1]}"


def group_injection_root(project_root: Path, volume_number: str) -> Path:
    return project_root / GROUP_ROOT_DIRNAME / f"{volume_number}{GROUP_DIR_SUFFIX}"


def group_injection_dir(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    batch_id = five_chapter_batch_id(chapter_numbers)
    return group_injection_root(project_root, volume_number) / f"{batch_id}_group_injection"


def five_chapter_review_path(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    group_dir = group_injection_dir(project_root, volume_number, chapter_numbers)
    return group_dir / f"{five_chapter_batch_id(chapter_numbers)}_group_review.md"


def find_group_for_chapter(volume_material: dict[str, Any], chapter_number: str) -> list[str]:
    normalized = chapter_number.zfill(4)
    for group in build_five_chapter_groups(volume_material):
        if normalized in group:
            return group
    fail(f"未找到章节 {normalized} 对应的五章区间。")


def build_chapter_session_key(manifest: dict[str, Any], volume_number: str, chapter_number: str) -> str:
    seed = f"{manifest['project_root']}|{manifest['source_root']}|{volume_number}|{chapter_number}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"chapter-rewrite-{digest}"


def build_volume_review_session_key(manifest: dict[str, Any], volume_number: str) -> str:
    seed = f"{manifest['project_root']}|{manifest['source_root']}|{volume_number}|volume-review"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"volume-review-{digest}"


def read_doc_catalog(project_root: Path, volume_number: str, chapter_number: str) -> dict[str, dict[str, Any]]:
    paths = rewrite_paths(project_root, volume_number, chapter_number)
    catalog: dict[str, dict[str, Any]] = {}

    for key, label in GLOBAL_DOC_LABELS.items():
        catalog[key] = {
            "key": key,
            "category": "global",
            "label": label,
            "path": paths[key],
            "content": read_text_if_exists(paths[key]).strip(),
        }

    for key, label in VOLUME_DOC_LABELS.items():
        catalog[key] = {
            "key": key,
            "category": "volume",
            "label": label,
            "path": paths[key],
            "content": read_text_if_exists(paths[key]).strip(),
        }

    for key, label in CHAPTER_DOC_LABELS.items():
        catalog[key] = {
            "key": key,
            "category": "chapter",
            "label": label,
            "path": paths[key],
            "content": read_text_if_exists(paths[key]).strip(),
        }

    return catalog


def serialize_doc_for_prompt(entry: dict[str, Any], *, limit: int = 120000) -> dict[str, Any]:
    content = str(entry["content"]).strip()
    return {
        "label": entry["label"],
        "file_name": Path(entry["path"]).name,
        "file_path": str(entry["path"]),
        "content": clip_for_context(content, limit=limit),
    }


def prepare_injected_docs(
    catalog: dict[str, dict[str, Any]],
    include_keys: list[str],
    *,
    category: str,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    payload_docs: dict[str, dict[str, Any]] = {}
    included: list[str] = []
    omitted: list[str] = []

    for key, entry in catalog.items():
        if entry["category"] != category:
            continue
        label = f"[{entry['category']}] {entry['label']}"
        if key not in include_keys:
            omitted.append(f"{label}：本阶段不注入。")
            continue
        if not entry["content"]:
            omitted.append(f"{label}：当前文件不存在或内容为空。")
            continue
        payload_docs[key] = serialize_doc_for_prompt(entry)
        included.append(f"{label} -> {entry['path']}（字符数约 {len(entry['content'])}）")

    return payload_docs, included, omitted


def prepare_cache_ordered_injected_docs(
    catalog: dict[str, dict[str, Any]],
    include_keys: list[str],
    *,
    category: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str], list[str]]:
    stable_docs: dict[str, dict[str, Any]] = {}
    rolling_docs: dict[str, dict[str, Any]] = {}
    included: list[str] = []
    omitted: list[str] = []
    stable_keys = set(STABLE_INJECTION_KEYS.get(category, []))

    for key, entry in catalog.items():
        if entry["category"] != category:
            continue
        label = f"[{entry['category']}] {entry['label']}"
        if key not in include_keys:
            omitted.append(f"{label}：本阶段不注入。")
            continue
        if not entry["content"]:
            omitted.append(f"{label}：当前文件不存在或内容为空。")
            continue
        serialized = serialize_doc_for_prompt(entry)
        if key in stable_keys:
            stable_docs[key] = serialized
        else:
            rolling_docs[key] = serialized
        included.append(f"{label} -> {entry['path']}（字符数约 {len(entry['content'])}）")

    return stable_docs, rolling_docs, included, omitted


def build_payload_with_trailing_docs(
    *,
    stable_fields: dict[str, Any],
    trailing_doc_fields: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    payload.update(stable_fields)
    payload.update(trailing_doc_fields)
    return payload


def build_payload_with_cache_layers(
    *,
    shared_prefix_fields: dict[str, Any],
    request_fields: dict[str, Any],
    trailing_doc_fields: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    payload.update(shared_prefix_fields)
    payload.update(request_fields)
    payload.update(trailing_doc_fields)
    return payload


def source_context_inventory(
    volume_material: dict[str, Any],
    chapter_number: str,
) -> list[dict[str, Any]]:
    chapter = get_chapter_material(volume_material, chapter_number)
    inventory: list[dict[str, Any]] = []
    for extra in volume_material["extras"]:
        inventory.append(
            {
                "type": "extra",
                "file_name": extra["file_name"],
                "file_path": extra["file_path"],
                "char_count": len(extra["text"]),
            }
        )
    inventory.append(
        {
            "type": "chapter",
            "file_name": chapter["file_name"],
            "file_path": chapter["file_path"],
            "chapter_number": chapter["chapter_number"],
            "source_title": chapter["source_title"],
            "char_count": len(chapter["text"]),
        }
    )
    return inventory


def build_chapter_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
    source_bundle: str,
    source_char_count: int,
) -> str:
    chapter = get_chapter_material(volume_material, chapter_number)
    payload = {
        "project": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest.get("target_worldview", ""),
            "current_volume": volume_material["volume_number"],
            "current_chapter": chapter_number,
            "source_title": chapter["source_title"],
            "rewrite_output_root": manifest["rewrite_output_root"],
        },
        "workflow_rules": [
            "当前章节的章纲生成、正文生成、配套文档更新、审核与返工属于同一个章节会话，请沿用同一会话上下文。",
            "每一次请求都会重新附带当前章节参考源与本阶段要求注入的全局/卷级/章级文档。",
            "全局注入是每卷每章都要看的资料；卷级注入只限当前卷；章级注入只限当前章。",
            "严禁把参考源的人名、地名、宗门名、术语名、招式名原样照搬到仿写结果里。",
            "参考源当前章不仅提供情节功能映射，也提供篇幅、叙事节奏、情节结构、对话密度、句长、段落分割与收尾方式的直接参照；除非审核意见明确要求，不得明显扩写。",
            "遇到旧审核意见时要显式吸收并修正，不要重复犯同样的问题。",
        ],
        "source_files": source_context_inventory(volume_material, chapter_number),
        "source_char_count": source_char_count,
        "current_chapter_source_bundle": source_bundle,
    }
    return (
        "## Chapter Shared Context\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )


def build_volume_review_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> str:
    payload = {
        "project": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest.get("target_worldview", ""),
            "current_volume": volume_material["volume_number"],
            "rewrite_output_root": manifest["rewrite_output_root"],
        },
        "workflow_rules": [
            "当前任务是卷级审核，只审核当前卷。",
            "需要检查卷内章节彼此之间的逻辑连续性、角色状态一致性、设定一致性和风格一致性。",
            "如果审核不通过，必须给出需要返工的章节编号。",
        ],
        "rewritten_chapter_inventory": [
            {
                "chapter_number": chapter_number,
                "file_name": data["file_name"],
                "file_path": data["file_path"],
                "char_count": len(data["text"]),
            }
            for chapter_number, data in rewritten_chapters.items()
        ],
    }
    return (
        "## Volume Review Shared Context\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )


def build_five_chapter_source_bundle(
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
) -> tuple[str, int]:
    selected = {item.zfill(4) for item in chapter_numbers}
    blocks: list[str] = []

    for extra in volume_material["extras"]:
        blocks.append(
            "\n".join(
                [
                    f"[补充文件 {extra['file_name']}]",
                    f"文件路径：{extra['file_path']}",
                    extra["text"],
                ]
            )
        )

    for chapter in volume_material["chapters"]:
        if chapter["chapter_number"] not in selected:
            continue
        blocks.append(
            "\n".join(
                [
                    f"[章节文件 {chapter['file_name']}]",
                    f"章节编号：{chapter['chapter_number']}",
                    f"文件路径：{chapter['file_path']}",
                    chapter["text"],
                ]
            )
        )

    source_bundle = "\n\n".join(blocks)
    return source_bundle, len(source_bundle)


def build_five_chapter_review_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_bundle: str,
    rewritten_chapters: dict[str, dict[str, Any]],
) -> str:
    payload = {
        "project": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest.get("target_worldview", ""),
            "current_volume": volume_material["volume_number"],
            "review_range": chapter_numbers,
            "rewrite_output_root": manifest["rewrite_output_root"],
        },
        "workflow_rules": [
            f"当前任务是{FIVE_CHAPTER_REVIEW_NAME}，只审查当前这一个五章区间。",
            "需要检查最近这组章节之间是否前后矛盾、逻辑是否通畅、剧情是否偏离参考源、卷纲与全书大纲。",
            "如果审核不通过，必须明确指出需要返工的章节编号。",
        ],
        "current_range_source_bundle": source_bundle,
        "rewritten_chapters": rewritten_chapters,
    }
    return (
        "## Five Chapter Alignment Review Context\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )


def load_relevant_five_chapter_review_docs(
    project_root: Path,
    volume_material: dict[str, Any],
    chapter_number: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    group = find_group_for_chapter(volume_material, chapter_number)
    path = five_chapter_review_path(project_root, volume_material["volume_number"], group)
    content = read_text_if_exists(path).strip()
    label = f"[group] {FIVE_CHAPTER_REVIEW_NAME}（{group[0]}-{group[-1]}）"
    if content:
        return (
            [
                {
                    "label": f"{FIVE_CHAPTER_REVIEW_NAME}（{group[0]}-{group[-1]}）",
                    "file_name": path.name,
                    "file_path": str(path),
                    "content": clip_for_context(content, limit=40000),
                }
            ],
            [f"{label} -> {path}（字符数约 {len(content)}）"],
            [],
        )
    return [], [], [f"{label}：当前无相关审查文档。"]


def chapter_shared_prefix_summary_lines(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
    source_char_count: int,
) -> list[str]:
    chapter = get_chapter_material(volume_material, chapter_number)
    return [
        "共享前缀构造：COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS + build_chapter_shared_prompt()。",
        f"固定函数工具：{WORKFLOW_SUBMISSION_TOOL_NAME}（统一 workflow schema）。",
        (
            f"固定项目上下文：新书《{manifest['new_book_title']}》 / 目标世界观："
            f"{manifest.get('target_worldview', '') or '未设置'} / 当前卷：{volume_material['volume_number']} / 当前章：{chapter_number}。"
        ),
        f"固定工作流规则：章节工作流规则 {5} 条。",
        f"固定参考源文件清单：补充文件 {len(volume_material['extras'])} 个 + 当前源章节 1 个（{chapter['file_name']}）。",
        f"固定参考源原文：当前章 source bundle，字符数约 {source_char_count}。",
    ]


def group_review_shared_prefix_summary_lines(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_char_count: int,
    rewritten_chapters: dict[str, dict[str, Any]],
) -> list[str]:
    return [
        "共享前缀构造：COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS + build_five_chapter_review_shared_prompt()。",
        f"固定函数工具：{WORKFLOW_SUBMISSION_TOOL_NAME}（统一 workflow schema）。",
        (
            f"固定项目上下文：新书《{manifest['new_book_title']}》 / 目标世界观："
            f"{manifest.get('target_worldview', '') or '未设置'} / 当前卷：{volume_material['volume_number']} / 当前组："
            f"{chapter_numbers[0]}-{chapter_numbers[-1]}。"
        ),
        f"固定工作流规则：{FIVE_CHAPTER_REVIEW_NAME}规则 3 条。",
        f"固定参考源原文：当前组 source bundle，包含 {len(chapter_numbers)} 章参考源与 {len(volume_material['extras'])} 个补充文件，字符数约 {source_char_count}。",
        f"固定已生成章节清单：当前组待审章节 {len(rewritten_chapters)} 章。",
    ]


def volume_review_shared_prefix_summary_lines(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> list[str]:
    total_chars = sum(len(data.get("text", "")) for data in rewritten_chapters.values())
    return [
        "共享前缀构造：COMMON_VOLUME_REVIEW_INSTRUCTIONS + build_volume_review_shared_prompt()。",
        f"固定函数工具：{WORKFLOW_SUBMISSION_TOOL_NAME}（统一 workflow schema）。",
        (
            f"固定项目上下文：新书《{manifest['new_book_title']}》 / 目标世界观："
            f"{manifest.get('target_worldview', '') or '未设置'} / 当前卷：{volume_material['volume_number']}。"
        ),
        "固定工作流规则：卷级审核规则 3 条。",
        f"固定已生成章节清单：当前卷 {len(rewritten_chapters)} 章，正文总字符数约 {total_chars}。",
    ]


def payload_prefix_doc_summary_lines(payload: dict[str, Any]) -> list[str]:
    doc_bucket_labels = {
        "stable_injected_global_docs": "稳定全局注入文档",
        "stable_injected_volume_docs": "稳定卷级注入文档",
        "stable_injected_chapter_docs": "稳定章级注入文档",
    }
    lines: list[str] = []
    for key, label in doc_bucket_labels.items():
        value = payload.get(key, {})
        count = len(value) if isinstance(value, dict) else 0
        lines.append(f"Dynamic Request 前段固定注入：{label} {count} 项。")
    return lines


def payload_dynamic_suffix_summary_lines(payload: dict[str, Any]) -> list[str]:
    document_request = payload.get("document_request", {})
    phase = str(document_request.get("phase", "unknown"))
    role = str(document_request.get("role", "")).strip()
    task = str(document_request.get("task", "")).strip()
    required_file = str(document_request.get("required_file", "")).strip()
    requirements = payload.get("requirements", [])
    lines = [
        f"动态请求构造：document_request.phase={phase}" + (f" / role={role}" if role else "") + "。",
    ]
    if task:
        lines.append(f"本次动态任务：{task}")
    if required_file:
        lines.append(f"目标输出文件：{required_file}")
    if isinstance(requirements, list):
        lines.append(f"本次阶段要求：{len(requirements)} 条。")

    doc_bucket_labels = {
        "rolling_injected_global_docs": "滚动全局注入文档",
        "rolling_injected_volume_docs": "滚动卷级注入文档",
        "rolling_injected_chapter_docs": "滚动章级注入文档",
        "rolling_injected_group_docs": "滚动组级注入文档",
        "writing_skill_reference": "写作规范 skill 参考",
        "review_skill_reference": "审核 skill 参考",
        "update_target_files": "待更新目标文件清单",
        "rewritten_chapters": "已生成章节正文清单",
    }
    for key, label in doc_bucket_labels.items():
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, dict):
            count = len(value)
        elif isinstance(value, list):
            count = len(value)
        else:
            count = 1 if value else 0
        lines.append(f"本次动态附带：{label} {count} 项。")

    if "current_generated_chapter" in payload:
        lines.append("本次动态附带：当前章节正文 1 项。")

    return lines


def print_request_context_summary(
    *,
    request_label: str,
    volume_number: str,
    chapter_number: str | None,
    location_label: str | None = None,
    source_summary_lines: list[str],
    included_docs: list[str],
    omitted_docs: list[str],
    previous_response_id: str | None,
    prompt_cache_key: str | None,
    shared_prefix_lines: list[str],
    dynamic_suffix_lines: list[str],
) -> None:
    print_progress(f"{request_label} 本次请求将携带以下内容：")
    if location_label:
        print_progress(f"  当前定位：{location_label}")
    elif chapter_number is not None:
        print_progress(f"  当前定位：第 {volume_number} 卷，第 {chapter_number} 章。")
    else:
        print_progress(f"  当前定位：第 {volume_number} 卷，卷级审核。")
    if prompt_cache_key:
        print_progress(f"  提示词缓存键：{prompt_cache_key}")
    if previous_response_id:
        print_progress(f"  会话：沿用 previous_response_id={previous_response_id}")
    else:
        print_progress("  会话：本轮首次请求，将创建新的会话。")

    print_progress("  提示词缓存共享前缀：")
    for line in shared_prefix_lines:
        print_progress(f"    - {line}")

    print_progress("  动态后缀（本次请求会变化）：")
    for line in dynamic_suffix_lines:
        print_progress(f"    - {line}")

    print_progress("  参考源输入：")
    for line in source_summary_lines:
        print_progress(f"    - {line}")

    print_progress("  已输入文档：")
    if included_docs:
        for line in included_docs:
            print_progress(f"    - {line}")
    else:
        print_progress("    - 无。")

    print_progress("  未输入文档：")
    if omitted_docs:
        for line in omitted_docs:
            print_progress(f"    - {line}")
    else:
        print_progress("    - 无。")


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
    result = llm_runtime.call_function_tool(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_model=WorkflowSubmissionPayload,
        tool_name=WORKFLOW_SUBMISSION_TOOL_NAME,
        tool_description=WORKFLOW_SUBMISSION_TOOL_DESCRIPTION,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    return result.parsed, result.response_id, result


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
    result = document_ops.call_document_operation_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    return result, result.response_id, result


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
    result = document_ops.call_document_operation_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
    )
    return result, result.response_id, result


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


def write_response_debug_snapshot(
    debug_path: Path,
    *,
    error_message: str,
    preview: str,
    raw_body_text: str = "",
) -> None:
    write_markdown_data(
        debug_path,
        title="Last Response Debug",
        payload={
            "generated_at": now_iso(),
            "error_message": error_message,
            "preview": preview,
            "raw_body_text": raw_body_text,
        },
        summary_lines=[
            f"error_message: {error_message}",
            f"preview_length: {len(preview)}",
            f"raw_body_length: {len(raw_body_text)}",
        ],
    )


def write_chapter_stage_snapshot(
    stage_manifest_path: Path,
    *,
    volume_number: str,
    chapter_number: str,
    status: str,
    note: str,
    attempt: int,
    last_phase: str | None = None,
    response_ids: list[str] | None = None,
) -> None:
    write_markdown_data(
        stage_manifest_path,
        title=f"Chapter Stage Manifest {volume_number}-{chapter_number}",
        payload={
            "generated_at": now_iso(),
            "volume_number": volume_number,
            "chapter_number": chapter_number,
            "status": status,
            "note": note,
            "attempt": attempt,
            "last_phase": last_phase,
            "response_ids": response_ids or [],
        },
        summary_lines=[
            f"volume_number: {volume_number}",
            f"chapter_number: {chapter_number}",
            f"status: {status}",
            f"attempt: {attempt}",
            f"last_phase: {last_phase or 'none'}",
            f"note: {note}",
        ],
    )


def write_volume_stage_snapshot(
    stage_manifest_path: Path,
    *,
    volume_number: str,
    status: str,
    note: str,
    attempt: int,
    response_id: str | None = None,
) -> None:
    write_markdown_data(
        stage_manifest_path,
        title=f"Volume Rewrite Manifest {volume_number}",
        payload={
            "generated_at": now_iso(),
            "volume_number": volume_number,
            "status": status,
            "note": note,
            "attempt": attempt,
            "response_id": response_id,
        },
        summary_lines=[
            f"volume_number: {volume_number}",
            f"status: {status}",
            f"attempt: {attempt}",
            f"response_id: {response_id or 'none'}",
            f"note: {note}",
        ],
    )


def doc_label_for_key(doc_key: str) -> str:
    if doc_key == "group_review":
        return f"{FIVE_CHAPTER_REVIEW_NAME}文档"
    return (
        GLOBAL_DOC_LABELS.get(doc_key)
        or VOLUME_DOC_LABELS.get(doc_key)
        or CHAPTER_DOC_LABELS.get(doc_key)
        or doc_key
    )


def write_artifact(path: Path, content: str) -> bool:
    return write_text_if_changed(path, content)


def support_update_target_paths(paths: dict[str, Path]) -> dict[str, Path]:
    return {
        "character_status_cards": paths["character_status_cards"],
        "character_relationship_graph": paths["character_relationship_graph"],
        "volume_plot_progress": paths["volume_plot_progress"],
        "foreshadowing": paths["foreshadowing"],
        "world_state": paths["world_state"],
    }


def chapter_text_target_inventory(paths: dict[str, Path], current_text: str) -> list[dict[str, Any]]:
    return [
        {
            "file_key": "rewritten_chapter",
            "file_name": paths["rewritten_chapter"].name,
            "file_path": str(paths["rewritten_chapter"]),
            "exists": paths["rewritten_chapter"].exists(),
            "preferred_mode": "patch" if current_text.strip() else "write",
            "write_policy": "patch_only_if_exists",
            "structure_mode": "existing_chapter_text_revision",
            "update_rules": [
                "当前文件已存在时，只能基于现有正文做局部改写、增删、替换与重组。",
                "不要把整章当成全新生成任务推倒重写。",
                "未变化段落应尽量保留，优先只修改受审核意见影响的局部。",
            ],
            "current_content": clip_for_context(current_text, limit=30000),
        }
    ]


def support_update_general_rules() -> list[str]:
    return [
        "这是长期知识文档更新步骤，只更新当前章节真实发生变化且确有必要更新的文档。",
        "无变化的文档不要返回，也不要为了统一措辞重写旧内容。",
        "已有非空文件默认禁止整篇写入，必须优先使用 patch 工具做局部增量更新。",
        "patch 可以更新多个文件，但每个文件都只能改动受当前章节影响的局部。",
        "如果只是给某一段、某条记录或某个小块后面补充新内容，优先使用 insert_after 直接追加，不要改写整段。",
        "长期知识文档采用“固定标题 + 可扩展二级标题”的管理方式，不要写成数据库字段表、代码 schema 或过度表格化文档。",
        "一级标题固定；二级标题用于管理不同类型的信息。已有二级标题结构如果已经适合本书，应优先沿用。",
        "每本书的信息类型都可能不同。出现新知识类型时，可以按实际小说内容新增新的二级标题，而不是硬套少数预设分类。",
    ]


def support_update_doc_rules() -> dict[str, dict[str, Any]]:
    return HEADING_MANAGED_DOC_SPECS


def support_update_target_inventory(paths: dict[str, Path]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    doc_rules = support_update_doc_rules()
    for file_key, path in support_update_target_paths(paths).items():
        current_content = read_text_if_exists(path).strip()
        rule = doc_rules.get(file_key, {})
        inventory.append(
            {
                "file_key": file_key,
                "file_name": path.name,
                "file_path": str(path),
                "exists": path.exists(),
                "preferred_mode": "patch" if current_content else "write",
                "write_policy": "patch_only_if_exists",
                "structure_mode": "fixed_title_expandable_sections_document",
                "template": rule.get("template", []),
                "section_policy": rule.get("section_policy", []),
                "update_rules": rule.get("update_rules", []),
                "current_content": clip_for_context(current_content, limit=18000),
            }
        )
    return inventory


def print_call_artifact_report(
    call_label: str,
    artifacts: list[tuple[str, Path]],
    changed_keys: list[str],
) -> None:
    print_progress(f"{call_label} 产出物：")
    if artifacts:
        for label, path in artifacts:
            content = read_text_if_exists(path).strip()
            print_progress(f"  - {label} -> {path}（字符数约 {len(content)}）")
    else:
        print_progress("  - 无。")

    print_progress(f"{call_label} 改动文档：")
    if changed_keys:
        for key in changed_keys:
            print_progress(f"  - {doc_label_for_key(key)}")
    else:
        print_progress("  - 无，本次生成结果与现有文件一致。")


def build_phase_request_payload(
    *,
    phase_key: str,
    project_root: Path,
    volume_material: dict[str, Any],
    volume_number: str,
    chapter_number: str,
    catalog: dict[str, dict[str, Any]],
    chapter_text: str = "",
    chapter_text_revision: bool = False,
) -> tuple[dict[str, Any], list[str], list[str]]:
    paths = rewrite_paths(project_root, volume_number, chapter_number)
    selection = PHASE_DOC_SELECTIONS[phase_key]
    five_chapter_review_docs, included_five_reviews, omitted_five_reviews = load_relevant_five_chapter_review_docs(
        project_root,
        volume_material=volume_material,
        chapter_number=chapter_number,
    )
    stable_global_docs, rolling_global_docs, included_globals, omitted_globals = prepare_cache_ordered_injected_docs(
        catalog,
        selection["global"],
        category="global",
    )
    stable_volume_docs, rolling_volume_docs, included_volumes, omitted_volumes = prepare_cache_ordered_injected_docs(
        catalog,
        selection["volume"],
        category="volume",
    )
    stable_chapter_docs, rolling_chapter_docs, included_chapters, omitted_chapters = prepare_cache_ordered_injected_docs(
        catalog,
        selection["chapter"],
        category="chapter",
    )

    included_docs = [*included_globals, *included_volumes, *included_chapters, *included_five_reviews]
    omitted_docs = [*omitted_globals, *omitted_volumes, *omitted_chapters, *omitted_five_reviews]
    reference_chapter = get_chapter_material(volume_material, chapter_number)
    reference_char_count = len(reference_chapter["text"])
    min_target_chars = max(1, int(reference_char_count * 0.8))
    max_target_chars = max(min_target_chars, int(reference_char_count * 1.2))

    if phase_key == "phase1_outline":
        payload = build_payload_with_cache_layers(
            shared_prefix_fields={
                "stable_injected_global_docs": stable_global_docs,
                "stable_injected_volume_docs": stable_volume_docs,
                "stable_injected_chapter_docs": stable_chapter_docs,
            },
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "章纲策划编辑",
                    "task": "只生成当前章的章纲 Markdown。",
                    "required_file": f"{chapter_number}_chapter_outline.md",
                },
                "reference_chapter_metrics": {
                    "source_title": reference_chapter["source_title"],
                    "source_char_count": reference_char_count,
                    "target_length_guideline": "章纲粒度应服务于后续正文保持与参考源当前章相近的篇幅和节奏。",
                },
                "requirements": [
                    "章纲必须体现与参考源当前章的功能映射关系，但不能照搬原名词。",
                    "章纲要能直接服务后续正文生成与审核返工。",
                    "章纲粒度要贴近参考源当前章，不要为了发挥把单章扩成更多场景、更多推进点或更多转折层次。",
                    "章纲应尽量对齐参考源当前章的场景数量、冲突层级、叙事节奏与收尾功能。",
                ],
            },
            trailing_doc_fields={
                "rolling_injected_global_docs": rolling_global_docs,
                "rolling_injected_volume_docs": rolling_volume_docs,
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "rolling_injected_group_docs": five_chapter_review_docs,
            },
        )
        return payload, included_docs, omitted_docs

    if phase_key == "phase2_chapter_text":
        writing_skill = load_chapter_writing_skill_reference()
        if chapter_text_revision:
            payload = build_payload_with_cache_layers(
                shared_prefix_fields={
                    "stable_injected_global_docs": stable_global_docs,
                    "stable_injected_volume_docs": stable_volume_docs,
                    "stable_injected_chapter_docs": stable_chapter_docs,
                },
                request_fields={
                    "document_request": {
                        "phase": phase_key,
                        "role": "章节仿写修订作者",
                        "task": "基于当前章现有正文、当前章上下文与审核意见，对已有章节正文做增量改写/修改。",
                        "required_file": str(rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]),
                    },
                    "reference_chapter_metrics": {
                        "source_title": reference_chapter["source_title"],
                        "source_char_count": reference_char_count,
                        "target_char_count_range": [min_target_chars, max_target_chars],
                    },
                    "writing_skill_reference": writing_skill,
                    "requirements": [
                        "必须把注入的写作规范 skill 作为当前章正文修订的主写作规则。",
                        "这是基于现有正文的修订任务，不是从零整篇重写任务。",
                        "如果当前文件已经存在，必须优先使用 patch 工具对现有正文做局部或分段修改；不要用整篇写入覆盖旧正文。",
                        "优先保留未变化段落，只修改受审核意见影响的局部；只有在局部无法修正时，才扩大修改范围。",
                        "正文修订后仍必须符合全局文笔写作风格文档，不要写解释说明或提纲。",
                        "修订时不能把参考源的人名、地名、宗门、术语原样照搬。",
                        "修订后的正文必须能承接章纲、卷纲、全局大纲与当前状态文档。",
                        f"修订后的正文目标篇幅仍应贴近参考源当前章，通常控制在约 {min_target_chars}-{max_target_chars} 字符；除非审核意见明确要求，不要明显扩写。",
                        "修订后的正文必须同时贴合文笔写作风格文档中的这些维度：爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式。",
                        "不得沿用参考源的章节标题、人物名、地点名、事件名、物品名、数值体系和具体数值；如果正文出现标题式文本或强识别设定，也必须转换为新书体系下的对应表达。",
                    ],
                },
                trailing_doc_fields={
                    "rolling_injected_global_docs": rolling_global_docs,
                    "rolling_injected_volume_docs": rolling_volume_docs,
                    "rolling_injected_chapter_docs": rolling_chapter_docs,
                    "rolling_injected_group_docs": five_chapter_review_docs,
                    "update_target_files": chapter_text_target_inventory(
                        rewrite_paths(project_root, volume_number, chapter_number),
                        chapter_text,
                    ),
                    "current_generated_chapter": {
                        "label": "当前章节正文",
                        "file_name": f"{chapter_number}.txt",
                        "file_path": str(rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]),
                        "content": chapter_text.strip(),
                    },
                },
            )
            return payload, included_docs, omitted_docs
        payload = build_payload_with_cache_layers(
            shared_prefix_fields={
                "stable_injected_global_docs": stable_global_docs,
                "stable_injected_volume_docs": stable_volume_docs,
                "stable_injected_chapter_docs": stable_chapter_docs,
            },
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "章节仿写作者",
                    "task": "只生成当前章的完整仿写章节正文。",
                    "required_file": str(rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]),
                },
                "reference_chapter_metrics": {
                    "source_title": reference_chapter["source_title"],
                    "source_char_count": reference_char_count,
                    "target_char_count_range": [min_target_chars, max_target_chars],
                },
                "writing_skill_reference": writing_skill,
                "requirements": [
                    "必须把注入的写作规范 skill 作为当前章正文仿写的主写作规则。",
                    "正文必须符合全局文笔写作风格文档，不要写解释说明或提纲。",
                    "不能把参考源的人名、地名、宗门、术语原样照搬。",
                    "正文必须能承接章纲、卷纲、全局大纲与当前状态文档。",
                    f"正文目标篇幅要贴近参考源当前章，通常控制在约 {min_target_chars}-{max_target_chars} 字符；除非审核意见明确要求，不要明显扩写。",
                    "正文必须同时贴合文笔写作风格文档中的这些维度：爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式。",
                    "不得沿用参考源的章节标题、人物名、地点名、事件名、物品名、数值体系和具体数值；如果正文出现标题式文本或强识别设定，也必须转换为新书体系下的对应表达。",
                    "如果参考源当前章是短促推进型，就保持短促；如果是对话驱动型，就保持相近的对话密度；不要额外补写解释性段落、总结性抒情、世界观说明或重复心理复述来硬性扩字。",
                ],
            },
            trailing_doc_fields={
                "rolling_injected_global_docs": rolling_global_docs,
                "rolling_injected_volume_docs": rolling_volume_docs,
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "rolling_injected_group_docs": five_chapter_review_docs,
            },
        )
        return payload, included_docs, omitted_docs

    if phase_key == "phase2_support_updates":
        payload = build_payload_with_cache_layers(
            shared_prefix_fields={
                "stable_injected_global_docs": stable_global_docs,
                "stable_injected_volume_docs": stable_volume_docs,
                "stable_injected_chapter_docs": stable_chapter_docs,
            },
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "连续性编辑与状态维护编辑",
                    "task": "根据刚写完的章节，按需更新人物状态卡、人物关系链、卷级剧情进程、伏笔、世界状态。",
                },
                "requirements": [
                    *support_update_general_rules(),
                    "人物关系链、卷级剧情进程、世界状态要保持固定标题，并通过贴合本书内容的二级标题来组织信息。",
                    "这些长期知识文档如果已有内容，必须优先沿用现有有效的二级标题结构，只对受当前章节影响的段落、小节或记录做 patch。",
                    "不要把这些小说参考文档改写成字段表、节点表、边表、数据库表或代码化 schema。",
                    "不要每次都更新全部文档；只返回当前章节确实发生变化、必须更新的文档。",
                    "如果某个文档在当前章节没有真实变化，就不要返回该文件，也不要做空更新。",
                    "卷级剧情进程只写当前卷内容。",
                    "卷级剧情进程必须尽量按“故事线二级标题 + 固定三级标题（起始、已发生发展、关键转折、当前状态、待推进）”维护。",
                    "更新卷级剧情进程时，优先 patch 当前受影响故事线下的对应三级标题，不要整段覆盖整条故事线，更不要让不同故事线互相覆盖。",
                ],
            },
            trailing_doc_fields={
                "rolling_injected_global_docs": rolling_global_docs,
                "rolling_injected_volume_docs": rolling_volume_docs,
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "rolling_injected_group_docs": five_chapter_review_docs,
                "update_target_files": support_update_target_inventory(paths),
                "current_generated_chapter": {
                    "label": "当前章节正文",
                    "file_name": f"{chapter_number}.txt",
                    "file_path": str(paths["rewritten_chapter"]),
                    "content": chapter_text.strip(),
                },
            },
        )
        return payload, included_docs, omitted_docs

    if phase_key == "phase3_review":
        review_skill = load_chapter_review_skill_reference()
        payload = build_payload_with_cache_layers(
            shared_prefix_fields={
                "stable_injected_global_docs": stable_global_docs,
                "stable_injected_volume_docs": stable_volume_docs,
                "stable_injected_chapter_docs": stable_chapter_docs,
            },
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "章级审核编辑",
                    "task": "审核当前章的全部产物，并判断是否需要返工。",
                    "required_file": f"{chapter_number}_chapter_review.md",
                },
                "reference_chapter_metrics": {
                    "source_title": reference_chapter["source_title"],
                    "source_char_count": reference_char_count,
                    "target_char_count_range": [min_target_chars, max_target_chars],
                },
                "requirements": [
                    "必须把注入的 chapter_review skill 作为主要审查方向。skill 中列出的 AI 痕迹、句法污染、节奏问题、术语一致性规则优先参与判断。",
                    "重点检查参考源原人名地名是否被照搬，若照搬则不合格。",
                    "重点检查 AI 感、机械感、逻辑断裂、幻觉错位、风格偏移。",
                    "重点检查是否出现过度修饰的排比、意象堆砌、诗化抒情过量、句式整齐得过头等问题；"
                    "如果语言明显非常符合当前主流大模型常见腔调，例如像 Claude 或 GPT-4 常见的华丽总结式文风，也视为不合格。",
                    "重点检查正文篇幅是否明显偏离参考源当前章；如果出现接近翻倍的扩写、明显灌水，或远超目标区间，也视为不合格。",
                    "重点检查正文是否真正符合文笔写作风格文档中对爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式的要求；若显著漂移则不合格。",
                    "如果不通过，rewrite_targets 必须写出需要返工的对象，例如 chapter_text、world_state 等。",
                    *review_output_contract_lines("chapter"),
                ],
            },
            trailing_doc_fields={
                "rolling_injected_global_docs": rolling_global_docs,
                "rolling_injected_volume_docs": rolling_volume_docs,
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "rolling_injected_group_docs": five_chapter_review_docs,
                "review_skill_reference": review_skill,
                "current_generated_chapter": {
                    "label": "当前章节正文",
                    "file_name": f"{chapter_number}.txt",
                    "file_path": str(rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]),
                    "content": chapter_text.strip(),
                },
            },
        )
        return payload, included_docs, omitted_docs

    fail(f"不支持的阶段：{phase_key}")


def build_volume_review_payload(
    *,
    project_root: Path,
    volume_material: dict[str, Any],
    volume_number: str,
    catalog: dict[str, dict[str, Any]],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    review_skill = load_chapter_review_skill_reference()
    stable_global_docs, rolling_global_docs, included_globals, omitted_globals = prepare_cache_ordered_injected_docs(
        catalog,
        [
            "book_outline",
            "world_design",
            "style_guide",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "global_plot_progress",
            "world_model",
            "world_state",
        ],
        category="global",
    )
    stable_volume_docs, rolling_volume_docs, included_volumes, omitted_volumes = prepare_cache_ordered_injected_docs(
        catalog,
        ["volume_outline", "volume_plot_progress", "volume_review"],
        category="volume",
    )

    payload = build_payload_with_cache_layers(
        shared_prefix_fields={
            "stable_injected_global_docs": stable_global_docs,
            "stable_injected_volume_docs": stable_volume_docs,
        },
        request_fields={
            "document_request": {
                "phase": "volume_review",
                "role": "卷级审核编辑",
                "task": "审核当前卷所有已生成章节与卷级文档是否一致、合理、符合风格。",
                "required_file": f"{volume_number}_volume_review.md",
            },
            "requirements": [
                "必须把注入的 chapter_review skill 作为主要审查方向。skill 中列出的 AI 痕迹、句法污染、节奏问题、术语一致性规则优先参与判断。",
                "需要检查与卷级大纲、世界观设计、文风规范、全局剧情进程是否一致。",
                "需要检查卷内章节的文风是否稳定符合文笔写作风格文档，尤其是爽点铺垫、剧情转折、叙事节奏、情节结构、段落分割、对话密度、句长与收尾方式是否持续一致。",
                "如果不通过，chapters_to_revise 必须列出需要返工的章节编号。",
                *review_output_contract_lines("volume"),
            ],
        },
        trailing_doc_fields={
            "rolling_injected_global_docs": rolling_global_docs,
            "rolling_injected_volume_docs": rolling_volume_docs,
            "review_skill_reference": review_skill,
            "rewritten_chapters": rewritten_chapters,
        },
    )
    included_docs = [*included_globals, *included_volumes]
    omitted_docs = [*omitted_globals, *omitted_volumes]
    return payload, included_docs, omitted_docs


def build_rewritten_chapters_payload(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for chapter_number in chapter_numbers:
        chapter_path = rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]
        chapter_text = read_text_if_exists(chapter_path).strip()
        if not chapter_text:
            fail(f"卷级审核时缺少章节正文：{chapter_path}")
        payload[chapter_number] = {
            "file_name": chapter_path.name,
            "file_path": str(chapter_path),
            "content": chapter_text,
            "text": chapter_text,
        }
    return payload


def five_chapter_review_source_summary_lines(
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_char_count: int,
    rewritten_chapters: dict[str, dict[str, Any]],
) -> list[str]:
    lines = [
        f"当前审查区间：{chapter_numbers[0]}-{chapter_numbers[-1]}。",
        f"当前区间参考源总字符数约 {source_char_count}。",
        f"当前区间已生成章节数：{len(rewritten_chapters)}。",
    ]
    for chapter_number in chapter_numbers:
        chapter = get_chapter_material(volume_material, chapter_number)
        lines.append(
            f"参考源章节：{chapter['file_name']}（标题：{chapter['source_title']}，字符数约 {len(chapter['text'])}）"
        )
    return lines


def build_five_chapter_review_payload(
    *,
    project_root: Path,
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    catalog: dict[str, dict[str, Any]],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    review_skill = load_chapter_review_skill_reference()
    current_batch_id = five_chapter_batch_id(chapter_numbers)
    current_batch_doc_name = f"{current_batch_id}_group_review.md"
    current_review_path = five_chapter_review_path(project_root, volume_material["volume_number"], chapter_numbers)
    current_review_content = read_text_if_exists(current_review_path).strip()
    if current_review_content:
        five_chapter_review_docs = [
            {
                "label": f"{FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}）",
                "file_name": current_review_path.name,
                "file_path": str(current_review_path),
                "content": clip_for_context(current_review_content, limit=40000),
            }
        ]
        included_five_reviews = [
            f"[group] {FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}） -> "
            f"{current_review_path}（字符数约 {len(current_review_content)}）"
        ]
        omitted_five_reviews: list[str] = []
    else:
        five_chapter_review_docs = []
        included_five_reviews = []
        omitted_five_reviews = [
            f"[group] {FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}）：当前无上一轮审查文档。"
        ]
    stable_global_docs, rolling_global_docs, included_globals, omitted_globals = prepare_cache_ordered_injected_docs(
        catalog,
        [
            "book_outline",
            "world_design",
            "style_guide",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "global_plot_progress",
            "world_model",
            "world_state",
        ],
        category="global",
    )
    stable_volume_docs, rolling_volume_docs, included_volumes, omitted_volumes = prepare_cache_ordered_injected_docs(
        catalog,
        ["volume_outline", "volume_plot_progress", "volume_review"],
        category="volume",
    )
    payload = build_payload_with_cache_layers(
        shared_prefix_fields={
            "stable_injected_global_docs": stable_global_docs,
            "stable_injected_volume_docs": stable_volume_docs,
        },
        request_fields={
            "document_request": {
                "phase": "five_chapter_alignment_review",
                "role": FIVE_CHAPTER_REVIEW_NAME,
                "task": f"审核当前这组章节 {chapter_numbers[0]}-{chapter_numbers[-1]} 是否沿着正确方向推进。",
                "required_file": current_batch_doc_name,
            },
            "requirements": [
                "必须把注入的 chapter_review skill 作为主要审查方向。skill 中列出的 AI 痕迹、句法污染、节奏问题、术语一致性规则优先参与判断。",
                "重点检查最近这组章节之间是否前后矛盾、逻辑是否通畅。",
                "重点检查剧情是否和参考源发生重大偏移，是否和卷纲、全书大纲、世界观设计发生重大偏移。",
                "如果不通过，chapters_to_revise 必须只列当前区间内需要返工的章节编号。",
                *review_output_contract_lines("group"),
            ],
        },
        trailing_doc_fields={
            "rolling_injected_global_docs": rolling_global_docs,
            "rolling_injected_volume_docs": rolling_volume_docs,
            "rolling_injected_group_docs": five_chapter_review_docs,
            "review_skill_reference": review_skill,
            "rewritten_chapters": rewritten_chapters,
        },
    )
    included_docs = [*included_globals, *included_volumes, *included_five_reviews]
    omitted_docs = [*omitted_globals, *omitted_volumes, *omitted_five_reviews]
    return payload, included_docs, omitted_docs


def chapter_source_summary_lines(volume_material: dict[str, Any], chapter_number: str, source_char_count: int) -> list[str]:
    chapter = get_chapter_material(volume_material, chapter_number)
    lines = [
        f"当前源章节：{chapter['file_name']}（标题：{chapter['source_title']}，字符数约 {len(chapter['text'])}）",
        f"当前卷补充文件：{len(volume_material['extras'])} 个，当前请求会一并注入。",
        f"当前章节参考源总字符数约 {source_char_count}。",
    ]
    for extra in volume_material["extras"]:
        lines.append(f"补充文件：{extra['file_name']}（字符数约 {len(extra['text'])}）")
    lines.append("未输入的来源章节：同卷其他章节当前不注入。")
    return lines


def volume_review_source_summary_lines(rewritten_chapters: dict[str, dict[str, Any]]) -> list[str]:
    lines = [f"当前卷已生成章节数：{len(rewritten_chapters)}。"]
    for chapter_number, data in rewritten_chapters.items():
        lines.append(f"已生成章节：{chapter_number}.txt（字符数约 {len(data['text'])}）")
    return lines


def mark_five_chapter_group_pending_for_chapter(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
) -> None:
    group = find_group_for_chapter(volume_material, chapter_number)
    batch_id = five_chapter_batch_id(group)
    state = get_five_chapter_review_state(manifest, volume_material["volume_number"], batch_id, group)
    if state.get("status") == "passed":
        update_five_chapter_review_state(
            manifest,
            volume_material["volume_number"],
            batch_id,
            group,
            status="pending",
            chapters_to_revise=[],
            blocking_issues=[],
        )


def all_group_chapters_passed(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
) -> bool:
    for chapter_number in chapter_numbers:
        state = get_chapter_state(manifest, volume_material["volume_number"], chapter_number)
        if state.get("status") != "passed":
            return False
    return True


def next_pending_group(volume_material: dict[str, Any], manifest: dict[str, Any]) -> list[str] | None:
    for group in build_five_chapter_groups(volume_material):
        batch_id = five_chapter_batch_id(group)
        state = get_five_chapter_review_state(manifest, volume_material["volume_number"], batch_id, group)
        if state.get("status") != "passed":
            return group
    return None


def current_due_group_review(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> list[str] | None:
    group = next_pending_group(volume_material, manifest)
    if group is None:
        return None
    if not all_group_chapters_passed(manifest, volume_material, group):
        return None
    return group


def group_review_passed(
    manifest: dict[str, Any],
    volume_number: str,
    chapter_numbers: list[str],
) -> bool:
    batch_id = five_chapter_batch_id(chapter_numbers)
    state = get_five_chapter_review_state(manifest, volume_number, batch_id, chapter_numbers)
    return state.get("status") == "passed"


def next_group_after(
    volume_material: dict[str, Any],
    manifest: dict[str, Any],
    current_group: list[str],
) -> list[str] | None:
    groups = build_five_chapter_groups(volume_material)
    found_current = False
    for group in groups:
        if not found_current:
            if group == current_group:
                found_current = True
            continue
        if not group_review_passed(manifest, volume_material["volume_number"], group):
            return group
    return None


def run_five_chapter_review(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
) -> bool:
    project_root = Path(rewrite_manifest["project_root"])
    volume_number = volume_material["volume_number"]
    batch_id = five_chapter_batch_id(chapter_numbers)
    review_path = five_chapter_review_path(project_root, volume_number, chapter_numbers)
    rewritten_chapters = build_rewritten_chapters_payload(project_root, volume_number, chapter_numbers)
    source_bundle, source_char_count = build_five_chapter_source_bundle(volume_material, chapter_numbers)
    prompt_cache_key = f"{build_volume_review_session_key(rewrite_manifest, volume_number)}-{batch_id}"
    shared_prompt = build_five_chapter_review_shared_prompt(
        manifest=rewrite_manifest,
        volume_material=volume_material,
        chapter_numbers=chapter_numbers,
        source_bundle=source_bundle,
        rewritten_chapters=rewritten_chapters,
    )

    for attempt in range(1, MAX_VOLUME_REVIEW_ATTEMPTS + 1):
        update_five_chapter_review_state(
            rewrite_manifest,
            volume_number,
            batch_id,
            chapter_numbers,
            status="in_progress",
            attempts=attempt,
            chapters_to_revise=[],
            blocking_issues=[],
        )
        catalog = read_doc_catalog(project_root, volume_number, chapter_numbers[0])
        payload, included_docs, omitted_docs = build_five_chapter_review_payload(
            project_root=project_root,
            volume_material=volume_material,
            chapter_numbers=chapter_numbers,
            catalog=catalog,
            rewritten_chapters=rewritten_chapters,
        )
        print_progress(
            f"{FIVE_CHAPTER_REVIEW_NAME} 调用：审核第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]}。"
        )
        print_request_context_summary(
            request_label=f"{FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}）",
            volume_number=volume_number,
            chapter_number=None,
            location_label=f"第 {volume_number} 卷，第 {chapter_numbers[0]}-{chapter_numbers[-1]} 组审查。",
            source_summary_lines=five_chapter_review_source_summary_lines(
                volume_material,
                chapter_numbers,
                source_char_count,
                rewritten_chapters,
            ),
            included_docs=included_docs,
            omitted_docs=omitted_docs,
            previous_response_id=None,
            prompt_cache_key=prompt_cache_key,
            shared_prefix_lines=[
                *group_review_shared_prefix_summary_lines(
                    rewrite_manifest,
                    volume_material,
                    chapter_numbers,
                    source_char_count,
                    rewritten_chapters,
                ),
                *payload_prefix_doc_summary_lines(payload),
            ],
            dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
        )
        review, response_id, _ = call_five_chapter_review_response(
            client,
            model,
            COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS,
            shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            allowed_chapters=chapter_numbers,
            previous_response_id=None,
            prompt_cache_key=prompt_cache_key,
        )
        group_review_changed = write_artifact(review_path, review.review_md)
        print_call_artifact_report(
            f"{FIVE_CHAPTER_REVIEW_NAME}调用",
            [(f"{FIVE_CHAPTER_REVIEW_NAME}文档", review_path)],
            ["group_review"] if group_review_changed else [],
        )

        if review.passed:
            update_five_chapter_review_state(
                rewrite_manifest,
                volume_number,
                batch_id,
                chapter_numbers,
                status="passed",
                attempts=attempt,
                chapters_to_revise=[],
                blocking_issues=[],
            )
            print_progress(
                f"{FIVE_CHAPTER_REVIEW_NAME} 已通过：第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]}。"
            )
            return True

        chapters_to_revise = [item.zfill(4) for item in review.chapters_to_revise if item]
        revision_plan = build_multi_chapter_revision_plan(
            chapters_to_revise=chapters_to_revise,
            rewrite_targets=review.rewrite_targets,
        )
        update_five_chapter_review_state(
            rewrite_manifest,
            volume_number,
            batch_id,
            chapter_numbers,
            status="needs_revision",
            attempts=attempt,
            chapters_to_revise=chapters_to_revise,
            blocking_issues=review.blocking_issues,
        )
        for chapter_number in chapters_to_revise:
            update_chapter_state(
                rewrite_manifest,
                volume_number,
                chapter_number,
                status="needs_revision",
                blocking_issues=review.blocking_issues,
                pending_phases=revision_plan.get(chapter_number, full_chapter_workflow_plan()),
                rewrite_targets=rewrite_targets_for_chapter(chapter_number, review.rewrite_targets),
                revision_origin="group_review",
            )
        print_progress(
            f"{FIVE_CHAPTER_REVIEW_NAME} 未通过，需要返工章节："
            f"{'、'.join(chapters_to_revise) or '未返回明确章节，请人工检查审查文档。'}"
        )
        return False

    fail(f"第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]} 连续审查失败。")


def run_due_five_chapter_reviews(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    target_group: list[str] | None = None,
) -> bool:
    while True:
        if target_group is not None:
            due_group = target_group if (
                all_group_chapters_passed(rewrite_manifest, volume_material, target_group)
                and not group_review_passed(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    target_group,
                )
            ) else None
        else:
            due_group = current_due_group_review(
                rewrite_manifest,
                volume_material,
            )
        if due_group is None:
            return True
        if not run_five_chapter_review(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_numbers=due_group,
        ):
            return False
        if target_group is not None:
            return True


def select_next_chapter(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    requested_chapter: str | None = None,
    allowed_chapters: list[str] | None = None,
) -> str | None:
    available = {chapter["chapter_number"] for chapter in volume_material["chapters"]}
    allowed = {item.zfill(4) for item in (allowed_chapters or [])} or None
    if requested_chapter:
        normalized = requested_chapter.zfill(4)
        if normalized not in available:
            fail(f"未在第 {volume_material['volume_number']} 卷找到指定章节：{normalized}")
        if allowed is not None and normalized not in allowed:
            fail(f"指定章节 {normalized} 不在当前运行范围内。")
        return normalized

    volume_state = manifest.get("chapter_states", {}).get(volume_material["volume_number"], {})
    review_state = get_volume_review_state(manifest, volume_material["volume_number"])
    revision_targets = [item.zfill(4) for item in review_state.get("chapters_to_revise", []) if item]
    for chapter_number in revision_targets:
        if chapter_number in available and (allowed is None or chapter_number in allowed) and volume_state.get(chapter_number, {}).get("status") != "passed":
            return chapter_number

    five_review_states = manifest.get("five_chapter_review_states", {}).get(volume_material["volume_number"], {})
    for group in build_five_chapter_groups(volume_material):
        batch_id = five_chapter_batch_id(group)
        state = five_review_states.get(batch_id, {})
        for chapter_number in [item.zfill(4) for item in state.get("chapters_to_revise", []) if item]:
            if chapter_number in available and (allowed is None or chapter_number in allowed) and volume_state.get(chapter_number, {}).get("status") != "passed":
                return chapter_number

    for chapter in volume_material["chapters"]:
        chapter_number = chapter["chapter_number"]
        if allowed is not None and chapter_number not in allowed:
            continue
        state = volume_state.get(chapter_number, {})
        if state.get("status") != "passed":
            return chapter_number
    return None


def all_chapters_passed(manifest: dict[str, Any], volume_material: dict[str, Any]) -> bool:
    for chapter in volume_material["chapters"]:
        state = get_chapter_state(manifest, volume_material["volume_number"], chapter["chapter_number"])
        if state.get("status") != "passed":
            return False
    return True


def select_volume_to_process(
    volume_dirs: list[Path],
    manifest: dict[str, Any],
    readiness_map: dict[str, dict[str, Any]],
    requested_volume: str | None,
) -> Path | None:
    volume_map = {volume_dir.name: volume_dir for volume_dir in volume_dirs}

    if requested_volume:
        normalized = requested_volume.zfill(3)
        if normalized not in volume_map:
            fail(f"未找到指定卷：{normalized}")
        readiness = readiness_map.get(normalized)
        if readiness and not readiness["eligible"]:
            fail(
                f"第 {normalized} 卷的 novel_adaptation_cli 产物尚不完善，暂不可进入章节工作流：\n"
                + "\n".join(readiness["missing"])
            )
        return volume_map[normalized]

    processed = set(manifest.get("processed_volumes", []))
    for volume_dir in volume_dirs:
        if volume_dir.name in processed:
            continue
        readiness = readiness_map.get(volume_dir.name, {})
        if not readiness.get("eligible", False):
            print_progress(f"第 {volume_dir.name} 卷暂不可进入章节工作流，已停止在这一卷。")
            for reason in readiness.get("missing", []):
                print_progress(f"  - {reason}")
            return None
        return volume_dir
    return None


def prompt_next_chapter(next_chapter: str | None) -> bool:
    if next_chapter is None:
        print_progress("当前卷没有新的待处理章节了；如需继续，请改用按组运行或按卷运行。")
        return False
    if not sys.stdin or not sys.stdin.isatty():
        print_progress("当前章节已完成；当前环境无法交互确认，程序将退出。")
        return False
    choice = prompt_choice(
        f"当前章节已完成。下一章是 {next_chapter}。请选择后续操作",
        [
            ("next", f"继续下一章（{next_chapter}）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"


def prompt_next_volume(next_volume: Path | None) -> bool:
    if next_volume is None:
        print_progress("本书完整结束。")
        return False
    if not sys.stdin or not sys.stdin.isatty():
        print_progress(
            f"当前卷已通过审核，下一卷是第 {next_volume.name} 卷；当前环境无法交互确认，程序将退出。"
        )
        return False
    choice = prompt_choice(
        f"当前卷已通过审核。下一卷是第 {next_volume.name} 卷。请选择后续操作",
        [
            ("next", f"开始下一卷（第 {next_volume.name} 卷）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"


def prompt_next_group(next_group: list[str] | None) -> bool:
    if next_group is None:
        return False
    if not sys.stdin or not sys.stdin.isatty():
        print_progress(
            f"当前组已通过审查，下一组是 {next_group[0]}-{next_group[-1]}；当前环境无法交互确认，程序将退出。"
        )
        return False
    choice = prompt_choice(
        f"当前组已通过审查。下一组是 {next_group[0]}-{next_group[-1]}。请选择后续操作",
        [
            ("next", f"继续下一组（{next_group[0]}-{next_group[-1]}）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"


def find_next_volume_after(
    volume_dirs: list[Path],
    current_volume_name: str,
    readiness_map: dict[str, dict[str, Any]],
) -> Path | None:
    found_current = False
    for volume_dir in volume_dirs:
        if not found_current:
            if volume_dir.name == current_volume_name:
                found_current = True
            continue
        readiness = readiness_map.get(volume_dir.name, {})
        if not readiness.get("eligible", False):
            print_progress(f"第 {volume_dir.name} 卷暂不可进入章节工作流，无法继续下一卷。")
            for reason in readiness.get("missing", []):
                print_progress(f"  - {reason}")
            return None
        return volume_dir
    return None


def prompt_continue_same_mode_next_volume(run_mode: str, next_volume: Path | None) -> bool:
    if next_volume is None:
        print_progress("本书完整结束。")
        return False
    mode_label = RUN_MODE_LABELS.get(run_mode, run_mode)
    if not sys.stdin or not sys.stdin.isatty():
        print_progress(
            f"当前卷已经没有可继续的内容，下一卷是第 {next_volume.name} 卷；"
            f"当前环境无法交互确认，程序将退出。"
        )
        return False
    choice = prompt_choice(
        f"当前卷已处理到末尾。请选择后续操作",
        [
            ("next", f"继续下一卷（第 {next_volume.name} 卷，保持{mode_label}）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"


def render_dry_run_summary(
    rewrite_manifest: dict[str, Any],
    readiness_map: dict[str, dict[str, Any]],
    target_volume: Path | None,
    target_chapter: str | None,
    run_mode: str,
) -> None:
    print(f"工程目录：{rewrite_manifest['project_root']}")
    print(f"重写输出目录：{rewrite_manifest['rewrite_output_root']}")
    print(f"已处理完成的卷：{', '.join(rewrite_manifest.get('processed_volumes', [])) or 'none'}")
    print("卷检测结果：")
    for volume_number in sorted(readiness_map):
        info = readiness_map[volume_number]
        status = "ready" if info["eligible"] else "blocked"
        print(f"  - {volume_number}: {status}")
        for reason in info["missing"]:
            print(f"      * {reason}")
    if target_volume is not None:
        print(f"本次准备处理卷：{target_volume.name}")
    if target_chapter is not None:
        print(f"本次准备处理章：{target_chapter}")
    print(f"本次运行模式：{run_mode}")
    print("本次 dry-run 不调用 API，也不会生成正文。")


def run_chapter_workflow(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
) -> None:
    project_root = Path(rewrite_manifest["project_root"])
    paths = rewrite_paths(project_root, volume_material["volume_number"], chapter_number)
    paths["chapter_dir"].mkdir(parents=True, exist_ok=True)
    paths["rewritten_volume_dir"].mkdir(parents=True, exist_ok=True)

    source_bundle, source_char_count = build_chapter_source_bundle(volume_material, chapter_number)
    chapter_session_key = build_chapter_session_key(
        rewrite_manifest,
        volume_material["volume_number"],
        chapter_number,
    )

    for attempt in range(1, MAX_CHAPTER_REWRITE_ATTEMPTS + 1):
        phase_plan = chapter_pending_phase_plan(
            rewrite_manifest,
            volume_material["volume_number"],
            chapter_number,
        )
        if not phase_plan:
            phase_plan = full_chapter_workflow_plan()
        total_steps = len(phase_plan)
        step_map = {phase: index + 1 for index, phase in enumerate(phase_plan)}
        response_ids: list[str] = []
        previous_response_id: str | None = None
        stage_shared_prompt = build_chapter_shared_prompt(
            manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_number=chapter_number,
            source_bundle=source_bundle,
            source_char_count=source_char_count,
        )
        update_chapter_state(
            rewrite_manifest,
            volume_material["volume_number"],
            chapter_number,
            status="in_progress",
            attempts=attempt,
            last_stage=phase_plan[0],
            pending_phases=phase_plan,
        )
        write_chapter_stage_snapshot(
            paths["chapter_stage_manifest"],
            volume_number=volume_material["volume_number"],
            chapter_number=chapter_number,
            status="in_progress",
            note=f"开始当前章节工作流。本轮重跑计划：{revision_plan_label(phase_plan)}。",
            attempt=attempt,
            last_phase=phase_plan[0],
            response_ids=response_ids,
        )

        try:
            current_chapter_text = read_text_if_exists(paths["rewritten_chapter"]).strip()

            if PHASE1_OUTLINE in phase_plan:
                catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                payload, included_docs, omitted_docs = build_phase_request_payload(
                    phase_key=PHASE1_OUTLINE,
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    catalog=catalog,
                )
                print_progress(f"第 {step_map[PHASE1_OUTLINE]}/{total_steps} 次调用：生成第 {chapter_number} 章章纲。")
                print_request_context_summary(
                    request_label="第一阶段：章纲生成",
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                    included_docs=included_docs,
                    omitted_docs=omitted_docs,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    shared_prefix_lines=[
                        *chapter_shared_prefix_summary_lines(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                            source_char_count,
                        ),
                        *payload_prefix_doc_summary_lines(payload),
                    ],
                    dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                )
                outline_md, previous_response_id, outline_result = call_markdown_tool_response(
                    client,
                    model,
                    COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                    stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                )
                response_ids.append(str(outline_result.response_id or ""))
                chapter_outline_changed = write_artifact(paths["chapter_outline"], outline_md)
                print_call_artifact_report(
                    f"第 {step_map[PHASE1_OUTLINE]}/{total_steps} 次调用",
                    [("章纲", paths["chapter_outline"])],
                    ["chapter_outline"] if chapter_outline_changed else [],
                )

                remaining = [phase for phase in phase_plan if phase != PHASE1_OUTLINE]
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    last_stage=remaining[0] if remaining else PHASE1_OUTLINE,
                    pending_phases=remaining,
                )
                if remaining:
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="in_progress",
                        note=f"章纲已完成，准备进入下一阶段：{remaining[0]}。",
                        attempt=attempt,
                        last_phase=remaining[0],
                        response_ids=response_ids,
                    )

            if PHASE2_CHAPTER_TEXT not in phase_plan and not read_text_if_exists(paths["chapter_outline"]).strip():
                fail(f"第 {chapter_number} 章缺少章纲，无法跳过章纲阶段直接继续后续流程。")

            if PHASE2_CHAPTER_TEXT in phase_plan:
                chapter_text_revision_mode = bool(current_chapter_text.strip())
                catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                payload, included_docs, omitted_docs = build_phase_request_payload(
                    phase_key=PHASE2_CHAPTER_TEXT,
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    catalog=catalog,
                    chapter_text=current_chapter_text,
                    chapter_text_revision=chapter_text_revision_mode,
                )
                print_progress(
                    f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用："
                    + (f"修订第 {chapter_number} 章现有正文。" if chapter_text_revision_mode else f"生成第 {chapter_number} 章完整正文。")
                )
                print_request_context_summary(
                    request_label="第二阶段-第一部分：正文修订" if chapter_text_revision_mode else "第二阶段-第一部分：正文生成",
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                    included_docs=included_docs,
                    omitted_docs=omitted_docs,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    shared_prefix_lines=[
                        *chapter_shared_prefix_summary_lines(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                            source_char_count,
                        ),
                        *payload_prefix_doc_summary_lines(payload),
                    ],
                    dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                )
                if chapter_text_revision_mode:
                    chapter_text_update, previous_response_id, chapter_text_result = call_chapter_text_revision_response(
                        client,
                        model,
                        COMMON_CHAPTER_TEXT_REVISION_INSTRUCTIONS,
                        stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                    )
                    response_ids.append(str(chapter_text_result.response_id or ""))
                    applied_chapter_text_update = document_ops.apply_document_operation(
                        chapter_text_update,
                        allowed_files={"rewritten_chapter": paths["rewritten_chapter"]},
                    )
                    current_chapter_text = read_text_if_exists(paths["rewritten_chapter"]).strip()
                    print_call_artifact_report(
                        f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用",
                        [("仿写章节正文", paths["rewritten_chapter"])],
                        applied_chapter_text_update.changed_keys,
                    )
                else:
                    chapter_txt, previous_response_id, chapter_text_result = call_chapter_text_tool_response(
                        client,
                        model,
                        COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                        stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                    )
                    response_ids.append(str(chapter_text_result.response_id or ""))
                    chapter_text_changed = write_artifact(paths["rewritten_chapter"], chapter_txt)
                    current_chapter_text = chapter_txt
                    print_call_artifact_report(
                        f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用",
                        [("仿写章节正文", paths["rewritten_chapter"])],
                        ["rewritten_chapter"] if chapter_text_changed else [],
                    )

                remaining = [phase for phase in phase_plan if phase not in {PHASE1_OUTLINE, PHASE2_CHAPTER_TEXT}]
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    last_stage=remaining[0] if remaining else PHASE2_CHAPTER_TEXT,
                    pending_phases=remaining,
                )
                if remaining:
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="in_progress",
                        note=f"正文已完成，准备进入下一阶段：{remaining[0]}。",
                        attempt=attempt,
                        last_phase=remaining[0],
                        response_ids=response_ids,
                    )

            if PHASE2_SUPPORT_UPDATES in phase_plan:
                current_chapter_text = current_chapter_text or read_text_if_exists(paths["rewritten_chapter"]).strip()
                if not current_chapter_text:
                    fail(f"第 {chapter_number} 章缺少正文，无法执行配套状态文档更新。")
                catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                payload, included_docs, omitted_docs = build_phase_request_payload(
                    phase_key=PHASE2_SUPPORT_UPDATES,
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    catalog=catalog,
                    chapter_text=current_chapter_text,
                )
                print_progress(f"第 {step_map[PHASE2_SUPPORT_UPDATES]}/{total_steps} 次调用：更新第 {chapter_number} 章配套状态文档。")
                print_request_context_summary(
                    request_label="第二阶段-第二部分：状态文档更新",
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                    included_docs=included_docs,
                    omitted_docs=omitted_docs,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    shared_prefix_lines=[
                        *chapter_shared_prefix_summary_lines(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                            source_char_count,
                        ),
                        *payload_prefix_doc_summary_lines(payload),
                    ],
                    dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                )
                support_updates, previous_response_id, support_result = call_support_updates_response(
                    client,
                    model,
                    COMMON_SUPPORT_UPDATE_INSTRUCTIONS,
                    stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                )
                response_ids.append(str(support_result.response_id or ""))
                applied_updates = document_ops.apply_document_operation(
                    support_updates,
                    allowed_files=support_update_target_paths(paths),
                )
                emitted_docs = applied_updates.emitted_keys
                changed_docs = applied_updates.changed_keys
                print_call_artifact_report(
                    f"第 {step_map[PHASE2_SUPPORT_UPDATES]}/{total_steps} 次调用",
                    [(doc_label_for_key(key), paths[key]) for key in emitted_docs],
                    changed_docs,
                )
                print_progress(
                    "本轮配套文档更新结果："
                    + (", ".join(doc_label_for_key(key) for key in changed_docs) if changed_docs else "模型判定当前无需要落盘的文档更新。")
                )

                remaining = [phase for phase in phase_plan if phase not in {PHASE1_OUTLINE, PHASE2_CHAPTER_TEXT, PHASE2_SUPPORT_UPDATES}]
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    last_stage=remaining[0] if remaining else PHASE2_SUPPORT_UPDATES,
                    pending_phases=remaining,
                )
                if remaining:
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="in_progress",
                        note=f"配套状态文档已完成，准备进入下一阶段：{remaining[0]}。",
                        attempt=attempt,
                        last_phase=remaining[0],
                        response_ids=response_ids,
                    )

            if PHASE3_REVIEW in phase_plan:
                current_chapter_text = current_chapter_text or read_text_if_exists(paths["rewritten_chapter"]).strip()
                if not current_chapter_text:
                    fail(f"第 {chapter_number} 章缺少正文，无法执行章级审核。")
                catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                payload, included_docs, omitted_docs = build_phase_request_payload(
                    phase_key=PHASE3_REVIEW,
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    catalog=catalog,
                    chapter_text=current_chapter_text,
                )
                print_progress(f"第 {step_map[PHASE3_REVIEW]}/{total_steps} 次调用：审核第 {chapter_number} 章全部产物。")
                print_request_context_summary(
                    request_label="第三阶段：章级审核",
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                    included_docs=included_docs,
                    omitted_docs=omitted_docs,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    shared_prefix_lines=[
                        *chapter_shared_prefix_summary_lines(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                            source_char_count,
                        ),
                        *payload_prefix_doc_summary_lines(payload),
                    ],
                    dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                )
                chapter_review, previous_response_id, review_result = call_chapter_review_response(
                    client,
                    model,
                    COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                    stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                )
                response_ids.append(str(review_result.response_id or ""))
                chapter_review_changed = write_artifact(paths["chapter_review"], chapter_review.review_md)
                print_call_artifact_report(
                    f"第 {step_map[PHASE3_REVIEW]}/{total_steps} 次调用",
                    [("章级审核文档", paths["chapter_review"])],
                    ["chapter_review"] if chapter_review_changed else [],
                )

                if chapter_review.passed:
                    update_chapter_state(
                        rewrite_manifest,
                        volume_material["volume_number"],
                        chapter_number,
                        status="passed",
                        attempts=attempt,
                        last_stage=PHASE3_REVIEW,
                        blocking_issues=[],
                        pending_phases=[],
                        rewrite_targets=[],
                        revision_origin=None,
                    )
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="passed",
                        note="当前章节已通过章级审核。",
                        attempt=attempt,
                        last_phase=PHASE3_REVIEW,
                        response_ids=response_ids,
                    )
                    mark_five_chapter_group_pending_for_chapter(
                        rewrite_manifest,
                        volume_material,
                        chapter_number,
                    )
                    print_progress(f"第 {chapter_number} 章已通过章级审核。")
                    return

                revision_plan = build_chapter_revision_plan(
                    rewrite_targets=chapter_review.rewrite_targets,
                )
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    status="needs_revision",
                    attempts=attempt,
                    last_stage=PHASE3_REVIEW,
                    blocking_issues=chapter_review.blocking_issues,
                    pending_phases=revision_plan,
                    rewrite_targets=chapter_review.rewrite_targets,
                    revision_origin="chapter_review",
                )
                write_chapter_stage_snapshot(
                    paths["chapter_stage_manifest"],
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    status="needs_revision",
                    note=f"章级审核未通过，待重跑阶段：{revision_plan_label(revision_plan)}。",
                    attempt=attempt,
                    last_phase=PHASE3_REVIEW,
                    response_ids=response_ids,
                )
                print_progress(
                    f"第 {chapter_number} 章章级审核未通过，将按审核意见重试。"
                    f" 本轮问题：{'; '.join(chapter_review.blocking_issues) or '见审核文档'}。"
                    f" 待重跑阶段：{revision_plan_label(revision_plan)}。"
                )
        except Exception as error:
            if isinstance(error, llm_runtime.ModelOutputError):
                write_response_debug_snapshot(
                    paths["chapter_response_debug"],
                    error_message=str(error),
                    preview=error.preview,
                    raw_body_text=getattr(error, "raw_body_text", ""),
                )
            update_chapter_state(
                rewrite_manifest,
                volume_material["volume_number"],
                chapter_number,
                status="failed",
                attempts=attempt,
            )
            write_chapter_stage_snapshot(
                paths["chapter_stage_manifest"],
                volume_number=volume_material["volume_number"],
                chapter_number=chapter_number,
                status="failed",
                note=str(error),
                attempt=attempt,
                last_phase=get_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                ).get("last_stage"),
            )
            raise

    fail(f"第 {volume_material['volume_number']} 卷第 {chapter_number} 章连续 {MAX_CHAPTER_REWRITE_ATTEMPTS} 次仍未通过章级审核。")


def run_volume_review(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> bool:
    project_root = Path(rewrite_manifest["project_root"])
    paths = rewrite_paths(project_root, volume_material["volume_number"])
    chapter_numbers = [chapter["chapter_number"] for chapter in volume_material["chapters"]]
    rewritten_chapters = build_rewritten_chapters_payload(project_root, volume_material["volume_number"], chapter_numbers)
    prompt_cache_key = build_volume_review_session_key(rewrite_manifest, volume_material["volume_number"])
    shared_prompt = build_volume_review_shared_prompt(
        manifest=rewrite_manifest,
        volume_material=volume_material,
        rewritten_chapters=rewritten_chapters,
    )

    for attempt in range(1, MAX_VOLUME_REVIEW_ATTEMPTS + 1):
        update_volume_review_state(
            rewrite_manifest,
            volume_material["volume_number"],
            status="in_progress",
            attempts=attempt,
            chapters_to_revise=[],
            blocking_issues=[],
        )
        write_volume_stage_snapshot(
            paths["volume_stage_manifest"],
            volume_number=volume_material["volume_number"],
            status="in_progress",
            note="开始卷级审核。",
            attempt=attempt,
        )
        try:
            catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_numbers[0])
            payload, included_docs, omitted_docs = build_volume_review_payload(
                project_root=project_root,
                volume_material=volume_material,
                volume_number=volume_material["volume_number"],
                catalog=catalog,
                rewritten_chapters=rewritten_chapters,
            )
            print_progress(f"卷级审核调用：审核第 {volume_material['volume_number']} 卷。")
            print_request_context_summary(
                request_label="卷级审核",
                volume_number=volume_material["volume_number"],
                chapter_number=None,
                location_label=f"第 {volume_material['volume_number']} 卷，卷级审核。",
                source_summary_lines=volume_review_source_summary_lines(rewritten_chapters),
                included_docs=included_docs,
                omitted_docs=omitted_docs,
                previous_response_id=None,
                prompt_cache_key=prompt_cache_key,
                shared_prefix_lines=[
                    *volume_review_shared_prefix_summary_lines(
                        rewrite_manifest,
                        volume_material,
                        rewritten_chapters,
                    ),
                    *payload_prefix_doc_summary_lines(payload),
                ],
                dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
            )
            volume_review, response_id, review_result = call_volume_review_response(
                client,
                model,
                COMMON_VOLUME_REVIEW_INSTRUCTIONS,
                shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                allowed_chapters=list(rewritten_chapters.keys()),
                previous_response_id=None,
                prompt_cache_key=prompt_cache_key,
            )
            volume_review_changed = write_artifact(paths["volume_review"], volume_review.review_md)
            print_call_artifact_report(
                "卷级审核调用",
                [("卷级审核文档", paths["volume_review"])],
                ["volume_review"] if volume_review_changed else [],
            )
            write_volume_stage_snapshot(
                paths["volume_stage_manifest"],
                volume_number=volume_material["volume_number"],
                status="completed",
                note="卷级审核已完成。",
                attempt=attempt,
                response_id=response_id,
            )

            if volume_review.passed:
                processed = set(rewrite_manifest.get("processed_volumes", []))
                processed.add(volume_material["volume_number"])
                rewrite_manifest["processed_volumes"] = sorted(processed)
                rewrite_manifest["last_processed_volume"] = volume_material["volume_number"]
                save_rewrite_manifest(rewrite_manifest)
                update_volume_review_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    status="passed",
                    attempts=attempt,
                    chapters_to_revise=[],
                    blocking_issues=[],
                )
                print_progress(f"第 {volume_material['volume_number']} 卷已通过卷级审核。")
                return True

            chapters_to_revise = [item.zfill(4) for item in volume_review.chapters_to_revise if item]
            revision_plan = build_multi_chapter_revision_plan(
                chapters_to_revise=chapters_to_revise,
                rewrite_targets=volume_review.rewrite_targets,
            )
            update_volume_review_state(
                rewrite_manifest,
                volume_material["volume_number"],
                status="needs_revision",
                attempts=attempt,
                chapters_to_revise=chapters_to_revise,
                blocking_issues=volume_review.blocking_issues,
            )
            for chapter_number in chapters_to_revise:
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    status="needs_revision",
                    blocking_issues=volume_review.blocking_issues,
                    pending_phases=revision_plan.get(chapter_number, full_chapter_workflow_plan()),
                    rewrite_targets=rewrite_targets_for_chapter(chapter_number, volume_review.rewrite_targets),
                    revision_origin="volume_review",
                )
            print_progress(
                f"第 {volume_material['volume_number']} 卷卷级审核未通过，需要返工章节："
                f"{'、'.join(chapters_to_revise) or '未返回明确章节，请人工检查卷级审核文档。'}"
            )
            return False
        except Exception as error:
            if isinstance(error, llm_runtime.ModelOutputError):
                write_response_debug_snapshot(
                    paths["volume_response_debug"],
                    error_message=str(error),
                    preview=error.preview,
                    raw_body_text=getattr(error, "raw_body_text", ""),
                )
            update_volume_review_state(
                rewrite_manifest,
                volume_material["volume_number"],
                status="failed",
                attempts=attempt,
            )
            write_volume_stage_snapshot(
                paths["volume_stage_manifest"],
                volume_number=volume_material["volume_number"],
                status="failed",
                note=str(error),
                attempt=attempt,
            )
            raise

    fail(f"第 {volume_material['volume_number']} 卷连续 {MAX_VOLUME_REVIEW_ATTEMPTS} 次卷级审核未通过。")


def process_volume_workflow(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    run_mode: str,
    requested_chapter: str | None = None,
) -> tuple[str, Any]:
    manual_requested_chapter = requested_chapter
    target_group = None
    if run_mode == RUN_MODE_GROUP:
        target_group = (
            find_group_for_chapter(volume_material, requested_chapter)
            if requested_chapter
            else next_pending_group(volume_material, rewrite_manifest)
        )

    while True:
        next_chapter = select_next_chapter(
            rewrite_manifest,
            volume_material,
            requested_chapter=manual_requested_chapter,
            allowed_chapters=target_group if run_mode == RUN_MODE_GROUP else None,
        )
        manual_requested_chapter = None

        if next_chapter is None:
            if run_mode == RUN_MODE_GROUP:
                if target_group is None:
                    return ("group", None)
                if not all_group_chapters_passed(rewrite_manifest, volume_material, target_group):
                    fail(f"当前组 {target_group[0]}-{target_group[-1]} 仍有章节未完成，但未识别到可处理章节。")
                if not run_due_five_chapter_reviews(
                    client=client,
                    model=model,
                    rewrite_manifest=rewrite_manifest,
                    volume_material=volume_material,
                    target_group=target_group,
                ):
                    continue
                return ("group", next_group_after(volume_material, rewrite_manifest, target_group))

            if not all_chapters_passed(rewrite_manifest, volume_material):
                fail(f"第 {volume_material['volume_number']} 卷仍有章节未完成，但未识别到可处理章节。")
            if not run_due_five_chapter_reviews(
                client=client,
                model=model,
                rewrite_manifest=rewrite_manifest,
                volume_material=volume_material,
            ):
                continue

            if run_mode == RUN_MODE_CHAPTER:
                return ("chapter", None)

            review_passed = run_volume_review(
                client=client,
                model=model,
                rewrite_manifest=rewrite_manifest,
                volume_material=volume_material,
            )
            if review_passed:
                return ("volume", None)
            continue

        print_progress(f"准备处理第 {volume_material['volume_number']} 卷第 {next_chapter} 章。")
        run_chapter_workflow(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_number=next_chapter,
        )

        if run_mode == RUN_MODE_CHAPTER:
            return ("chapter", select_next_chapter(rewrite_manifest, volume_material))

        if not run_due_five_chapter_reviews(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            target_group=target_group if run_mode == RUN_MODE_GROUP else None,
        ):
            continue

        if run_mode == RUN_MODE_GROUP and target_group is not None and group_review_passed(
            rewrite_manifest,
            volume_material["volume_number"],
            target_group,
        ):
            return ("group", next_group_after(volume_material, rewrite_manifest, target_group))


def main() -> int:
    args = parse_args()
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH)

    try:
        print_progress("开始识别小说工程目录。")
        project_root, source_root, project_manifest = resolve_project_input(args.input_root, global_config)
        migration_warnings = ensure_rewrite_dirs(project_root)
        for warning in migration_warnings:
            print_progress(warning, error=True)
        volume_dirs = discover_volume_dirs(source_root)
        readiness_map = {
            volume_dir.name: assess_volume_readiness(project_root, source_root, volume_dir.name)
            for volume_dir in volume_dirs
        }
        print_volume_readiness_summary(readiness_map)

        rewrite_manifest = init_or_load_rewrite_manifest(project_root, source_root, project_manifest, volume_dirs)
        run_mode = resolve_run_mode(args)
        global_config = openai_config.update_global_config(
            GLOBAL_CONFIG_PATH,
            global_config,
            {
                "last_chapter_rewrite_input_root": str(project_root),
                "last_project_root": str(project_root),
                "last_source_root": str(source_root),
            },
        )

        target_volume = select_volume_to_process(volume_dirs, rewrite_manifest, readiness_map, args.volume)
        target_chapter = args.chapter.zfill(4) if args.chapter else None
        if args.dry_run:
            render_dry_run_summary(rewrite_manifest, readiness_map, target_volume, target_chapter, run_mode)
            return 0

        if target_volume is None:
            print_progress("当前没有可进入章节工作流的卷。")
            return 0

        print_progress(f"本次运行模式：{RUN_MODE_LABELS.get(run_mode, run_mode)}")
        print_progress("开始准备 API 客户端。")
        api_key, global_config = openai_config.resolve_api_key(
            cli_api_key=args.api_key,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
        )
        openai_settings, _ = openai_config.resolve_openai_settings(
            cli_base_url=args.base_url,
            cli_model=args.model,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
        )
        print_progress(f"本次使用 base_url：{openai_settings['base_url']}")
        print_progress(f"本次使用模型：{openai_settings['model']}")
        print_progress(f"本次使用协议：{openai_settings.get('protocol', 'responses')}")
        client = openai_config.create_openai_client(
            api_key=api_key,
            base_url=openai_settings["base_url"],
            protocol=openai_settings.get("protocol", openai_config.PROTOCOL_RESPONSES),
            provider=openai_settings.get("provider", openai_config.PROVIDER_OPENAI),
        )

        requested_volume = target_volume.name
        requested_chapter = target_chapter

        while True:
            current_volume = select_volume_to_process(volume_dirs, rewrite_manifest, readiness_map, requested_volume)
            requested_volume = None
            if current_volume is None:
                print_progress("当前没有新的可处理卷。")
                return 0

            volume_material = load_volume_material(current_volume)
            print_progress(
                f"已加载第 {current_volume.name} 卷："
                f"{len(volume_material['chapters'])} 个章节文件，"
                f"{len(volume_material['extras'])} 个补充文件。"
            )
            completed_scope, next_target = process_volume_workflow(
                client=client,
                model=openai_settings["model"],
                rewrite_manifest=rewrite_manifest,
                volume_material=volume_material,
                run_mode=run_mode,
                requested_chapter=requested_chapter,
            )
            requested_chapter = None
            if args.workflow_controlled:
                print_progress("当前重写范围已完成，统一工作流将接管后续调度。")
                return 0

            if completed_scope == "chapter":
                if next_target is not None:
                    if not prompt_next_chapter(next_target):
                        return 0
                    requested_volume = current_volume.name
                    requested_chapter = next_target
                    continue
                next_volume = find_next_volume_after(volume_dirs, current_volume.name, readiness_map)
                if not prompt_continue_same_mode_next_volume(run_mode, next_volume):
                    return 0
                if next_volume is None:
                    return 0
                requested_volume = next_volume.name
                requested_chapter = None
                continue

            if completed_scope == "group":
                if next_target is not None:
                    if not prompt_next_group(next_target):
                        return 0
                    requested_volume = current_volume.name
                    requested_chapter = next_target[0]
                    continue
                next_volume = find_next_volume_after(volume_dirs, current_volume.name, readiness_map)
                if not prompt_continue_same_mode_next_volume(run_mode, next_volume):
                    return 0
                if next_volume is None:
                    return 0
                requested_volume = next_volume.name
                requested_chapter = None
                continue

            next_volume = select_volume_to_process(volume_dirs, rewrite_manifest, readiness_map, None)
            if not prompt_next_volume(next_volume):
                return 0
            if next_volume is None:
                return 0
            requested_volume = next_volume.name
    except KeyboardInterrupt:
        print_progress("已取消。", error=True)
        pause_before_exit()
        return 1
    except Exception as error:
        print_progress(f"处理失败：{error}", error=True)
        pause_before_exit()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
