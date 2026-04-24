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
    "global_plot_progress": "06_global_plot_progress.md",
}
GLOBAL_INJECTION_DOC_ORDER = [
    "world_design",
    "world_model",
    "style_guide",
    "book_outline",
    "foreshadowing",
    "global_plot_progress",
]
LEGACY_GLOBAL_FILE_RENAMES = {
    "01_book_outline.md": GLOBAL_FILE_NAMES["book_outline"],
    "02_world_design.md": GLOBAL_FILE_NAMES["world_design"],
    "04_world_model.md": GLOBAL_FILE_NAMES["world_model"],
    "05_global_plot_progress.md": GLOBAL_FILE_NAMES["global_plot_progress"],
    "06_foreshadowing.md": GLOBAL_FILE_NAMES["foreshadowing"],
    "04_foreshadowing.md": GLOBAL_FILE_NAMES["foreshadowing"],
    "05_foreshadowing.md": GLOBAL_FILE_NAMES["foreshadowing"],
    "08_world_model.md": GLOBAL_FILE_NAMES["world_model"],
    "07_global_plot_progress.md": GLOBAL_FILE_NAMES["global_plot_progress"],
    "08_global_plot_progress.md": GLOBAL_FILE_NAMES["global_plot_progress"],
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
GLOBAL_PLOT_PROGRESS_DEFAULT_SUBSECTIONS = [
    "起始",
    "已发生发展",
    "关键转折",
    "当前状态",
    "待推进",
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


class AdaptationReviewTarget(BaseModel):
    file_key: str = Field(..., description="审核或修复目标的逻辑 key。")
    file_name: str = Field(..., description="文件名。")
    file_path: str = Field(..., description="文件绝对路径或工程内路径。")
    label: str = Field("", description="中文文档名。")
    scope: str = Field("", description="global 或 volume。")
    exists: bool = Field(False, description="文件当前是否存在。")
    current_char_count: int = Field(0, description="当前正文字符数。")
    current_content: str = Field("", description="当前文件正文，必要时会被截断。")
    preferred_mode: str = Field("patch", description="建议工具模式。")


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


def world_model_scope_text() -> str:
    section_text = "、".join(WORLD_MODEL_DEFAULT_SECTIONS)
    return (
        "文档要沉淀到当前卷为止已知的世界知识，默认按 16 个二级标题组织："
        f"{section_text}。每个二级标题下可以根据实际需要继续展开多个三级标题，用于管理该栏目的不同知识子类。"
        "并写出与原书的功能映射。"
    )


def global_plot_progress_scope_text() -> str:
    subsection_text = "、".join(GLOBAL_PLOT_PROGRESS_DEFAULT_SUBSECTIONS)
    return (
        "文档是全书级故事线规划文档，需仿写参考源并规划出新的主线、关键支线、反派线、终局线、跨卷线与新增故事线。"
        "每条故事线优先使用二级标题单独维护，并在其下使用三级标题组织进度。"
        f"默认三级标题为：{subsection_text}。"
        "如某条故事线有额外维度，可按需要增加更多三级标题。"
        "文档必须覆盖到当前卷为止的全部重要故事线，并写出与原书的功能映射。"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "基于 split_novel 拆分后的书名文件夹或已有工程目录，逐卷生成改编规划文档，"
            "使用 OpenAI Responses API。"
        )
    )
    parser.add_argument(
        "source_root",
        nargs="?",
        help=(
            "split_novel 拆分完成后的书名文件夹路径，或已有工程目录路径，"
            "支持任意位置的绝对路径或相对路径，例如 D:\\books\\某本书；不传时启动后提示输入。"
        ),
    )
    parser.add_argument("--new-title", help="新书名。")
    parser.add_argument("--target-worldview", help="目标世界观。")
    parser.add_argument("--base-url", help="OpenAI Responses API 的 base_url。")
    parser.add_argument("--api-key", help="OpenAI API Key。")
    parser.add_argument("--model", help="调用的模型名称。")
    parser.add_argument(
        "--style-mode",
        choices=(STYLE_MODE_CUSTOM, STYLE_MODE_SOURCE),
        help="写作风格来源模式。",
    )
    parser.add_argument("--style-file", help="自定义写作风格文件路径。")
    parser.add_argument(
        "--protagonist-mode",
        choices=(PROTAGONIST_MODE_CUSTOM, PROTAGONIST_MODE_ADAPTIVE),
        help="主角设定来源模式。",
    )
    parser.add_argument("--protagonist-text", help="自定义主角设定和性格描述。")
    parser.add_argument("--volume", help="指定处理某一卷，例如 001。默认自动处理下一卷。")
    parser.add_argument(
        "--run-mode",
        choices=(RUN_MODE_STAGE, RUN_MODE_BOOK),
        help="运行方式：按阶段运行（每卷结束后确认）或按全书运行（自动连续处理后续卷）。",
    )
    parser.add_argument(
        "--project-root",
        help="输出工程目录；默认使用新书名自动创建。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只初始化工程和识别待处理卷，不调用 API。",
    )
    parser.add_argument(
        "--workflow-controlled",
        action="store_true",
        help="由统一工作流入口调度时启用：当前只处理本次目标卷，完成后直接返回，不在子流程 内继续下一卷。",
    )
    return parser.parse_args()
def validate_source_root(source_root: Path) -> None:
    if not source_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{source_root}")

    volume_dirs = discover_volume_dirs(source_root)
    if not volume_dirs:
        fail(
            "当前目录下未识别到编号卷目录，例如 001、002。"
            "请传入 split_novel 拆分完成后的书名文件夹，或传入已有工程目录。"
        )


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
        manifest = load_manifest(child)
        if manifest and manifest_matches_source_root(manifest, source_root):
            candidates.append((str(manifest.get("updated_at", "")), child, manifest))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, project_root, manifest = candidates[0]
    return project_root, manifest


def resolve_input_root(
    raw_path: str | None,
    global_config: dict[str, Any],
) -> tuple[Path, Path | None, dict[str, Any] | None]:
    default_path = (
        global_config.get("last_project_root")
        or global_config.get("last_source_root")
        or global_config.get("last_input_root")
    )
    if raw_path is None:
        raw_path = prompt_text(
            "请输入 split_novel 拆分完成后的书名文件夹路径或已有工程目录路径（可输入任意位置）",
            default=str(default_path) if default_path else None,
        )

    input_root = normalize_path(raw_path)
    if not input_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{input_root}")
    if not input_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{input_root}")

    manifest = load_manifest(input_root)
    if manifest is not None:
        source_root = normalize_path(str(manifest["source_root"]))
        validate_source_root(source_root)
        return source_root, input_root, manifest

    source_root = input_root
    validate_source_root(source_root)
    project_root, manifest = find_existing_project_for_source(source_root)
    return source_root, project_root, manifest


def discover_volume_dirs(source_root: Path) -> list[Path]:
    volume_dirs = [
        child
        for child in source_root.iterdir()
        if child.is_dir() and re.fullmatch(r"\d{3}", child.name)
    ]
    return sorted(volume_dirs, key=lambda item: int(item.name))


def discover_volume_files(volume_dir: Path) -> tuple[list[Path], list[Path]]:
    chapter_files: list[Path] = []
    extra_files: list[Path] = []

    for child in volume_dir.iterdir():
        if not child.is_file():
            continue
        if re.fullmatch(r"\d{4}", child.stem):
            chapter_files.append(child)
        else:
            extra_files.append(child)

    chapter_files.sort(key=lambda item: int(item.stem))
    extra_files.sort(key=lambda item: item.name)
    return chapter_files, extra_files


def choose_project_root(
    source_root: Path,
    new_title: str,
    requested_root: str | None,
) -> Path:
    if requested_root:
        return normalize_path(requested_root)

    base_name = sanitize_file_name(new_title)
    candidate = source_root.parent / base_name
    try:
        if candidate.resolve() == source_root.resolve():
            candidate = source_root.parent / f"{base_name}_project"
    except FileNotFoundError:
        pass

    manifest_paths = [
        candidate / PROJECT_MANIFEST_NAME,
        candidate / LEGACY_PROJECT_MANIFEST_NAME,
    ]
    if any(path.exists() for path in manifest_paths):
        return candidate
    if not candidate.exists() or not any(candidate.iterdir()):
        return candidate

    suffix = 1
    while True:
        alt = source_root.parent / f"{base_name}_{suffix}"
        manifest_paths = [
            alt / PROJECT_MANIFEST_NAME,
            alt / LEGACY_PROJECT_MANIFEST_NAME,
        ]
        if any(path.exists() for path in manifest_paths):
            return alt
        if not alt.exists():
            return alt
        suffix += 1


def load_manifest(project_root: Path) -> dict[str, Any] | None:
    manifest_path = project_root / PROJECT_MANIFEST_NAME
    if manifest_path.exists():
        return extract_json_payload(manifest_path.read_text(encoding="utf-8"))

    legacy_manifest_path = project_root / LEGACY_PROJECT_MANIFEST_NAME
    if legacy_manifest_path.exists():
        return json.loads(legacy_manifest_path.read_text(encoding="utf-8"))

    return None


def save_manifest(manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = now_iso()
    payload = dict(manifest)
    payload.pop("openai", None)
    write_markdown_data(
        Path(manifest["project_root"]) / PROJECT_MANIFEST_NAME,
        title="Project Manifest",
        payload=payload,
        summary_lines=[
            f"new_book_title: {manifest['new_book_title']}",
            f"source_root: {manifest['source_root']}",
            f"total_volumes: {manifest['total_volumes']}",
            f"processed_volumes: {', '.join(manifest.get('processed_volumes', [])) or 'none'}",
            f"last_processed_volume: {manifest.get('last_processed_volume') or 'none'}",
        ],
    )


def ensure_project_dirs(project_root: Path) -> list[str]:
    global_dir = project_root / GLOBAL_DIRNAME
    global_dir.mkdir(parents=True, exist_ok=True)
    warnings = migrate_renamed_files(global_dir, LEGACY_GLOBAL_FILE_RENAMES)
    migrate_numbered_injection_dirs(
        project_root,
        container_dirname=VOLUME_ROOT_DIRNAME,
        suffix=VOLUME_DIR_SUFFIX,
    )
    return warnings


def resolve_style_mode(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.style_mode:
        style_mode = args.style_mode
    else:
        style_mode = prompt_choice(
            "输入写作风格",
            [
                (STYLE_MODE_CUSTOM, "自定义导入写作风格文件"),
                (STYLE_MODE_SOURCE, "参考书源写作风格"),
            ],
        )

    style_file: str | None = None
    if style_mode == STYLE_MODE_CUSTOM:
        raw_path = args.style_file or prompt_text("请输入写作风格文件路径")
        style_path = normalize_path(raw_path)
        if not style_path.exists():
            raise FileNotFoundError(f"写作风格文件不存在：{style_path}")
        if not style_path.is_file():
            raise IsADirectoryError(f"写作风格路径不是文件：{style_path}")
        style_file = str(style_path)

    return style_mode, style_file


def resolve_protagonist_mode(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.protagonist_mode:
        protagonist_mode = args.protagonist_mode
    else:
        protagonist_mode = prompt_choice(
            "输入主角设定和性格",
            [
                (PROTAGONIST_MODE_CUSTOM, "自定义设计"),
                (PROTAGONIST_MODE_ADAPTIVE, "根据世界观不同和参考书源柔和设定"),
            ],
        )

    protagonist_text: str | None = None
    if protagonist_mode == PROTAGONIST_MODE_CUSTOM:
        protagonist_text = args.protagonist_text or prompt_text(
            "请输入主角设定和性格描述"
        )

    return protagonist_mode, protagonist_text


def init_or_load_project(
    args: argparse.Namespace,
    source_root: Path,
    volume_dirs: list[Path],
    global_config: dict[str, Any],
    existing_project_root: Path | None = None,
    existing_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if existing_manifest is not None and existing_project_root is not None:
        manifest = dict(existing_manifest)
        project_root = existing_project_root
    else:
        manifest = None
        project_root = None

    if manifest is None and args.project_root:
        requested_project_root = normalize_path(args.project_root)
        requested_manifest = load_manifest(requested_project_root)
        if requested_manifest is not None:
            manifest = dict(requested_manifest)
            project_root = requested_project_root

    if manifest is None:
        new_title_default = global_config.get("last_new_book_title")
        new_title = args.new_title or prompt_text("输入新书名", default=new_title_default)
        project_root = choose_project_root(source_root, new_title, args.project_root)
        manifest = load_manifest(project_root)

    if manifest is not None:
        if Path(manifest["source_root"]).resolve() != source_root.resolve():
            fail(
                f"工程目录已存在，但来源目录不同：{project_root}\n"
                f"当前来源：{source_root}\n"
                f"工程记录来源：{manifest['source_root']}"
            )
        manifest["total_volumes"] = len(volume_dirs)
        save_manifest(manifest)
        return manifest

    assert project_root is not None
    target_worldview = args.target_worldview or prompt_text("输入仿写成什么世界观")
    style_mode, style_file = resolve_style_mode(args)
    protagonist_mode, protagonist_text = resolve_protagonist_mode(args)

    project_root.mkdir(parents=True, exist_ok=True)
    ensure_project_dirs(project_root)

    manifest = {
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source_root": str(source_root),
        "project_root": str(project_root),
        "new_book_title": new_title,
        "target_worldview": target_worldview,
        "style": {
            "mode": style_mode,
            "style_file": style_file,
        },
        "protagonist": {
            "mode": protagonist_mode,
            "description": protagonist_text,
        },
        "total_volumes": len(volume_dirs),
        "processed_volumes": [],
        "last_processed_volume": None,
    }
    save_manifest(manifest)
    return manifest


def select_volume_to_process(
    volume_dirs: list[Path],
    manifest: dict[str, Any],
    requested_volume: str | None,
) -> Path | None:
    volume_map = {volume_dir.name: volume_dir for volume_dir in volume_dirs}

    if requested_volume:
        normalized = requested_volume.zfill(3)
        if normalized not in volume_map:
            fail(f"未找到指定卷：{normalized}")
        return volume_map[normalized]

    processed = set(manifest.get("processed_volumes", []))
    for volume_dir in volume_dirs:
        if volume_dir.name not in processed:
            return volume_dir
    return None


def find_next_pending_volume_after(
    volume_dirs: list[Path],
    manifest: dict[str, Any],
    current_volume_name: str,
) -> Path | None:
    processed = set(manifest.get("processed_volumes", []))
    found_current = False
    for volume_dir in volume_dirs:
        if not found_current:
            if volume_dir.name == current_volume_name:
                found_current = True
            continue
        if volume_dir.name in processed:
            continue
        return volume_dir
    return None


def resolve_run_mode(args: argparse.Namespace) -> str:
    if args.run_mode:
        return args.run_mode
    if not sys.stdin or not sys.stdin.isatty():
        return RUN_MODE_STAGE
    return prompt_choice(
        "请选择运行方式",
        [
            (RUN_MODE_STAGE, f"{RUN_MODE_LABELS[RUN_MODE_STAGE]}（每卷结束后确认下一卷）"),
            (RUN_MODE_BOOK, f"{RUN_MODE_LABELS[RUN_MODE_BOOK]}（自动连续处理后续卷）"),
        ],
    )


def build_phase_session_key(manifest: dict[str, Any], volume_number: str) -> str:
    seed = f"{manifest['project_root']}|{manifest['source_root']}|{volume_number}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"novel-adaptation-{digest}"


def first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def clip_for_context(text: str, limit: int = 18000) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    head = int(limit * 0.55)
    tail = limit - head
    return (
        stripped[:head].rstrip()
        + "\n\n[...中间内容为节省上下文已省略...]\n\n"
        + stripped[-tail:].lstrip()
    )


def load_volume_material(volume_dir: Path) -> dict[str, Any]:
    chapter_files, extra_files = discover_volume_files(volume_dir)
    if not chapter_files:
        fail(f"卷目录中未找到章节文件：{volume_dir}")

    chapters: list[dict[str, Any]] = []
    for chapter_file in chapter_files:
        text = read_text(chapter_file)
        chapters.append(
            {
                "chapter_number": chapter_file.stem,
                "file_name": chapter_file.name,
                "file_path": str(chapter_file),
                "source_title": first_non_empty_line(text) or chapter_file.stem,
                "text": text.strip(),
            }
        )

    extras: list[dict[str, Any]] = []
    for extra_file in extra_files:
        text = read_text(extra_file)
        extras.append(
            {
                "file_name": extra_file.name,
                "file_path": str(extra_file),
                "label": extra_file.stem,
                "text": text.strip(),
            }
        )

    return {
        "volume_number": volume_dir.name,
        "volume_dir": str(volume_dir),
        "chapters": chapters,
        "extras": extras,
    }


def build_loaded_file_inventory(volume_material: dict[str, Any]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []

    for extra in volume_material["extras"]:
        inventory.append(
            {
                "type": "extra",
                "file_name": extra["file_name"],
                "file_path": extra["file_path"],
                "label": extra["label"],
                "char_count": len(extra["text"]),
            }
        )

    for chapter in volume_material["chapters"]:
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


def build_volume_source_bundle(volume_material: dict[str, Any]) -> tuple[str, int]:
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


def chunk_text_items(items: list[str], size: int) -> list[str]:
    return ["、".join(items[index : index + size]) for index in range(0, len(items), size)]


def print_request_context_summary(
    *,
    doc_label: str,
    current_doc_key: str,
    volume_material: dict[str, Any],
    current_docs: dict[str, str],
    loaded_files: list[dict[str, Any]],
    source_char_count: int,
    previous_response_id: str | None,
) -> None:
    print_progress(f"{doc_label} 本次请求将携带以下内容：")
    print_progress("  提示词缓存共享前缀：项目上下文 + 阶段规则 + 文件清单 + 整卷原文。")
    print_progress(
        f"  当前卷整卷原文：{len(volume_material['chapters'])} 个章节文件，"
        f"{len(volume_material['extras'])} 个补充文件，总字符数约 {source_char_count}。"
    )

    extra_names = [item["file_name"] for item in volume_material["extras"]]
    if extra_names:
        extra_chunks = chunk_text_items(extra_names, 8)
        for index, chunk in enumerate(extra_chunks, start=1):
            print_progress(f"  补充文件[{index}/{len(extra_chunks)}]：{chunk}")
    else:
        print_progress("  补充文件：无。")

    chapter_names = [item["file_name"] for item in volume_material["chapters"]]
    chapter_chunks = chunk_text_items(chapter_names, 10)
    for index, chunk in enumerate(chapter_chunks, start=1):
        print_progress(f"  章节文件[{index}/{len(chapter_chunks)}]：{chunk}")

    print_progress(f"  已附带文件清单：{len(loaded_files)} 项。")

    for doc_key, label in (
        ("world_design", "世界观设计"),
        ("world_model", "世界模型"),
        ("style_guide", "文笔写作风格"),
        ("book_outline", "全书大纲"),
        ("foreshadowing", "伏笔文档"),
        ("global_plot_progress", "全局剧情进程"),
    ):
        content = (current_docs.get(doc_key) or "").strip()
        file_name = GLOBAL_FILE_NAMES[doc_key]
        if doc_key == current_doc_key:
            if content:
                print_progress(f"  目标文件 {file_name}（{label}）：当前内容将通过 target_file.current_content 附带，字符数约 {len(content)}。")
            else:
                print_progress(f"  目标文件 {file_name}（{label}）：当前为空。")
        elif content:
            print_progress(f"  全局注入 {file_name}（{label}）：已附带，字符数约 {len(content)}。")
        else:
            print_progress(f"  全局注入 {file_name}（{label}）：当前为空。")

    if previous_response_id:
        print_progress(f"  阶段会话：沿用 previous_response_id={previous_response_id}")
    else:
        print_progress("  阶段会话：本阶段首次请求，将创建新的阶段会话。")


def prompt_next_stage(next_volume: Path | None) -> bool:
    if next_volume is None:
        print_progress("当前卷之后没有新的待处理卷可继续了。")
        return False

    if not sys.stdin or not sys.stdin.isatty():
        print_progress(
            f"当前阶段已完成，下一阶段是第 {next_volume.name} 卷；"
            "当前环境无法交互确认，程序将退出。"
        )
        return False

    choice = prompt_choice(
        f"当前阶段已完成，下一阶段是第 {next_volume.name} 卷。请选择后续操作",
        [
            ("next", f"开始下一阶段（第 {next_volume.name} 卷）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"


def call_document_operation_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
    retries: int = DEFAULT_API_RETRIES,
) -> tuple[document_ops.DocumentOperationCallResult, str | None]:
    result = document_ops.call_document_operation_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=retries,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
    )
    return result, result.response_id


def call_adaptation_review_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    AdaptationReviewPayload,
    str | None,
    llm_runtime.FunctionToolResult[AdaptationReviewPayload],
]:
    result = llm_runtime.call_function_tool(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_model=AdaptationReviewPayload,
        tool_name=ADAPTATION_REVIEW_TOOL_NAME,
        tool_description=ADAPTATION_REVIEW_TOOL_DESCRIPTION,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=DEFAULT_API_RETRIES,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
    )
    payload = result.parsed
    if payload.passed is None or not payload.review_md.strip():
        raise llm_runtime.ModelOutputError(
            "模型未通过卷资料审核工具返回完整的 passed / review_md 字段。",
            preview=result.preview,
            raw_body_text=result.raw_body_text,
        )
    return payload, result.response_id, result


def style_reference_context(manifest: dict[str, Any]) -> str:
    style_mode = manifest["style"]["mode"]
    if style_mode == STYLE_MODE_CUSTOM:
        style_file = manifest["style"]["style_file"]
        if not style_file:
            return "未提供自定义风格文件。"
        text = read_text(Path(style_file))
        return clip_for_context(text, limit=12000)
    return "请从当前卷参考源中提炼写作风格，不额外加载外部风格文件。"


def protagonist_context(manifest: dict[str, Any]) -> str:
    protagonist = manifest["protagonist"]
    if protagonist["mode"] == PROTAGONIST_MODE_CUSTOM:
        return protagonist["description"] or "未提供详细主角设定。"
    return "请结合目标世界观与参考卷人物功能，柔和改造出新的主角设定和性格。"


def read_existing_global_docs(project_root: Path) -> dict[str, str]:
    global_dir = project_root / GLOBAL_DIRNAME
    docs: dict[str, str] = {}
    for key, file_name in GLOBAL_FILE_NAMES.items():
        path = global_dir / file_name
        docs[key] = path.read_text(encoding="utf-8") if path.exists() else ""
    return docs


def should_generate_style_guide(volume_number: str) -> bool:
    return volume_number == "001"


def build_document_request(doc_key: str) -> dict[str, Any]:
    request_specs: dict[str, dict[str, Any]] = {
        "style_guide": {
            "role": "资深网络小说文风策划编辑",
            "task": "当前任务只产出 1 份文笔写作风格文档正文。",
            "scope": (
                "文档必须覆盖写作方式、文风、情绪渲染方式、爽点铺垫与释放方式、剧情转折方式、叙事节奏、情节结构、"
                "符号使用习惯、段落分割、章节结尾钩子与收尾方式、句长偏好、对话密度、描写密度、铺垫、高潮、收束，"
                "并说明与原书的功能映射，避免只给空泛风格形容词。"
            ),
        },
        "world_design": {
            "role": "资深网络小说世界观设定编辑",
            "task": "当前任务只产出 1 份世界观设计文档正文。",
            "scope": (
                "文档需覆盖世界观设定、背景故事、能力设计、道具设计、势力设计、角色功能位、故事类型与原书映射关系。"
            ),
        },
        "book_outline": {
            "role": "资深网络小说总纲编辑",
            "task": "当前任务只产出 1 份全书大纲文档正文。",
            "scope": (
                "把当前卷纳入整本书的大纲中，但只能增量补写已读取参考源的卷。"
                "未读取的卷只能写成占位，或暂时不写，等后续阶段再补充，不得提前展开细纲。"
            ),
        },
        "global_plot_progress": {
            "role": "资深网络小说全书故事线规划编辑",
            "task": "当前任务只产出 1 份全书故事线规划文档正文。",
            "scope": global_plot_progress_scope_text(),
        },
        "foreshadowing": {
            "role": "资深网络小说伏笔统筹编辑",
            "task": "当前任务只产出 1 份伏笔文档正文。",
            "scope": "文档要同时管理全书伏笔和当前卷伏笔，区分已埋设、待回收、已回收，并写出与原书的功能映射。",
        },
        "world_model": {
            "role": "资深网络小说世界知识建模编辑",
            "task": "当前任务只产出 1 份世界模型文档正文。",
            "scope": world_model_scope_text(),
        },
        "volume_outline": {
            "role": "资深小说分卷策划编辑",
            "task": "当前任务只产出 1 份当前卷的卷级大纲正文。",
            "scope": "只产出当前卷的卷级大纲文档。",
        },
    }
    if doc_key not in request_specs:
        fail(f"不支持的文档类型：{doc_key}")
    return {"doc_key": doc_key, **request_specs[doc_key]}


def build_document_plan(volume_number: str) -> list[dict[str, Any]]:
    if should_generate_style_guide(volume_number):
        return [
            {"key": "world_design", "label": "世界观设计文档", "scope": "global"},
            {"key": "world_model", "label": "世界模型文档", "scope": "global"},
            {"key": "style_guide", "label": "文笔写作风格文档", "scope": "global"},
            {"key": "book_outline", "label": "全书大纲文档", "scope": "global"},
            {"key": "foreshadowing", "label": "伏笔文档", "scope": "global"},
            {"key": "global_plot_progress", "label": "全局剧情进程文档", "scope": "global"},
            {"key": "volume_outline", "label": "卷级大纲文档", "scope": "volume"},
        ]
    return [
        {"key": "world_design", "label": "世界观设计文档", "scope": "global"},
        {"key": "world_model", "label": "世界模型文档", "scope": "global"},
        {"key": "book_outline", "label": "全书大纲文档", "scope": "global"},
        {"key": "foreshadowing", "label": "伏笔文档", "scope": "global"},
        {"key": "global_plot_progress", "label": "全局剧情进程文档", "scope": "global"},
        {"key": "volume_outline", "label": "卷级大纲文档", "scope": "volume"},
    ]


def build_injected_global_docs(
    current_docs: dict[str, str],
    *,
    exclude_keys: set[str] | None = None,
) -> dict[str, str]:
    excluded = exclude_keys or set()
    injected_docs: dict[str, str] = {}
    for doc_key in GLOBAL_INJECTION_DOC_ORDER:
        if doc_key in excluded:
            continue
        injected_docs[doc_key] = clip_for_context(current_docs.get(doc_key, ""), limit=30000)
    return injected_docs


def build_stage_project_context(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> dict[str, Any]:
    processed_before_current = list(manifest.get("processed_volumes", []))
    processed_including_current = sorted(
        {str(item) for item in [*processed_before_current, volume_material["volume_number"]]}
    )
    return {
        "new_book_title": manifest["new_book_title"],
        "target_worldview": manifest["target_worldview"],
        "current_volume": volume_material["volume_number"],
        "total_volumes": manifest["total_volumes"],
        "processed_volumes_before_current": processed_before_current,
        "processed_volumes_including_current": processed_including_current,
        "remaining_volume_count": max(manifest["total_volumes"] - len(processed_including_current), 0),
        "style_mode": manifest["style"]["mode"],
        "style_reference": style_reference_context(manifest),
        "protagonist_mode": manifest["protagonist"]["mode"],
        "protagonist_context": protagonist_context(manifest),
    }


def build_stage_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    loaded_files: list[dict[str, Any]],
    source_bundle: str,
    source_char_count: int,
) -> str:
    stage_shared_payload = {
        "project": build_stage_project_context(manifest, volume_material),
        "stage_rules": [
            "这一卷的全部生成都属于同一个阶段会话，请沿用同一会话的上下文连续工作。",
            "全书大纲、世界观文档、全局剧情进程文档、伏笔文档、世界模型文档是每阶段都要注入的全局资料。",
            "卷级大纲不作为全局注入资料，不要把卷级大纲当成下一份文档的依赖前提。",
            "所有映射关系都写成功能映射，不要照抄参考源原文句子。",
            "本阶段的每一次请求都会重新附带当前卷全部文件原文与文件清单。",
        ],
        "loaded_files": loaded_files,
        "source_char_count": source_char_count,
        "current_volume_source_bundle": source_bundle,
    }
    return (
        "## Stage Shared Context\n"
        + json.dumps(stage_shared_payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )


def build_payload_with_trailing_docs(
    *,
    stable_fields: dict[str, Any],
    trailing_doc_fields: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    payload.update(stable_fields)
    payload.update(trailing_doc_fields)
    return payload


def document_output_path(paths: dict[str, Path], doc_key: str) -> Path:
    if doc_key in paths:
        return paths[doc_key]
    fail(f"未找到文档输出路径：{doc_key}")


def build_target_file_context(
    *,
    doc_key: str,
    output_path: Path,
    current_content: str,
) -> dict[str, Any]:
    return {
        "file_key": doc_key,
        "file_name": output_path.name,
        "file_path": str(output_path),
        "exists": output_path.exists(),
        "current_content": clip_for_context(current_content, limit=18000),
        "preferred_mode": "patch" if current_content.strip() else "write",
    }


def generate_document_operation(
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    current_docs: dict[str, str],
    *,
    doc_key: str,
    output_path: Path,
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
) -> tuple[document_ops.DocumentOperationCallResult, str | None]:
    injected_globals = build_injected_global_docs(current_docs, exclude_keys={doc_key})
    document_request = build_document_request(doc_key)
    target_file = build_target_file_context(
        doc_key=doc_key,
        output_path=output_path,
        current_content=current_docs.get(doc_key, ""),
    )

    if doc_key == "style_guide":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": GLOBAL_FILE_NAMES["style_guide"],
                "requirements": [
                    "标题稳定，适合后续工作流长期注入。",
                    "这是全书级写作风格文档，仅在第一卷阶段生成与定稿。",
                    "必须明确提炼爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式这些可执行维度。",
                    "不要只写抽象评价，要写成后续章节生成与审核可以直接照着执行的风格规则。",
                    "如果当前文件已存在且只需局部补充，请优先使用 patch 工具；不要为了重组措辞而整篇重写。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "world_design":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": GLOBAL_FILE_NAMES["world_design"],
                "requirements": [
                    "保留历史世界观设计的连续性，并把当前卷新增内容补充进去。",
                    "优先使用 patch 工具对已有条目、段落或小节做增量更新，不要整篇重写世界观文档。",
                    "未变化的世界知识、术语、层级结构、历史背景必须保留。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "book_outline":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": GLOBAL_FILE_NAMES["book_outline"],
                "requirements": [
                    "这是整本书的大纲文档，不是单卷总结。",
                    "当前阶段只允许新增或改写当前卷对应的全书大纲段落，以及与已处理卷直接相关的衔接说明。",
                    "只展开 processed_volumes_including_current 中列出的卷；未读取参考源的后续卷必须二选一：要么不写，要么仅保留“第X卷：待后续阶段补全”这类占位说明。",
                    "未读取卷不得出现剧情梗概、角色推进、冲突设计、伏笔安排、高潮设计或结局走向。",
                    "如果旧版全书大纲里已经提前写了未读取卷的详细内容，本次要把那些未读取卷删掉，或回收为占位状态，不能继续保留伪细纲。",
                    "第一卷阶段尤其不能提前写第二卷及之后的详细大纲。",
                    "优先使用 patch 工具对当前卷对应段落做增量修改，不要把整份全书大纲改写成只剩最近一卷的信息。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "global_plot_progress":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": GLOBAL_FILE_NAMES["global_plot_progress"],
                "requirements": [
                    "这是全书级故事线规划文档，但采用按卷增量维护方式：每卷只补充、修正到当前卷为止的故事线规划。",
                    "文档必须覆盖到当前卷为止仍然有效的重要故事线，包括主线、关键支线、反派线、终局线、跨卷线，以及当前卷新出现的故事线。",
                    "如果当前文件已存在，必须优先使用 edit / patch 工具做增量更新，不得整篇覆盖式重写全局剧情进程。",
                    "每条故事线优先使用独立二级标题管理；不要把多条故事线混写在同一个总段落里。",
                    "每条故事线下默认使用三级标题：起始、已发生发展、关键转折、当前状态、待推进；必要时可增加更多三级标题。",
                    "如果当前卷引入了新的故事线，必须新增对应二级标题，并补齐该线当前可确定的三级标题内容。",
                    "未变化的故事线规划必须保留，不要把整份文档改写成只剩最近一卷或只剩最新推进。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "foreshadowing":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": GLOBAL_FILE_NAMES["foreshadowing"],
                "requirements": [
                    "优先保持伏笔清单的可追踪性和后续工作流可读性。",
                    "请基于全书大纲、世界观文档和当前卷原文上下文补充更新。",
                    "优先使用 patch 工具做增量补充、状态推进或局部修订。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "world_model":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": GLOBAL_FILE_NAMES["world_model"],
                "requirements": [
                    "这是全书级世界模型文档，但采用按卷增量维护方式：每卷只补充、修正到当前卷为止新增的世界知识。",
                    "如果当前文件已存在，必须优先使用 patch 工具做增量更新，不得整篇覆盖式重写世界模型。",
                    "未变化的世界知识、术语、势力、地点、历史背景与规则结构必须保留。",
                    "本次只允许补充、修正与当前卷直接相关的世界知识，不要把文档改写成只剩最近一卷。",
                    "默认使用 scope 中给出的 16 个二级标题组织世界模型；如果某些栏目当前卷暂无信息，可以保留简短占位说明，但不要删除默认标题。",
                    "每个二级标题下可以根据实际小说内容需要展开多个三级标题，用于细分该栏目的不同知识类型；不要强行把所有内容挤在一个段落里。",
                    "只有当默认 16 个栏目确实无法容纳本书特有世界知识时，才使用“可扩展世界专题”新增专题。新增专题必须长期可复用。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "volume_outline":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": f"{volume_material['volume_number']}_volume_outline.md",
                "requirements": [
                    "卷纲要包含本卷定位、主要冲突、角色推进、高潮设计、结尾钩子、与原卷映射关系。",
                    "这是卷级注入文档，不要改写成全书文档。",
                    "如果当前卷纲文件已存在且只需局部补写，请优先使用 patch 工具；否则可整篇写入。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    fail(f"不支持的文档类型：{doc_key}")


def stage_paths(project_root: Path, volume_number: str) -> dict[str, Path]:
    global_dir = project_root / GLOBAL_DIRNAME
    volume_root_dir = project_root / VOLUME_ROOT_DIRNAME
    volume_dir = volume_root_dir / f"{volume_number}{VOLUME_DIR_SUFFIX}"
    return {
        "global_dir": global_dir,
        "volume_root_dir": volume_root_dir,
        "volume_dir": volume_dir,
        "book_outline": global_dir / GLOBAL_FILE_NAMES["book_outline"],
        "world_design": global_dir / GLOBAL_FILE_NAMES["world_design"],
        "style_guide": global_dir / GLOBAL_FILE_NAMES["style_guide"],
        "global_plot_progress": global_dir / GLOBAL_FILE_NAMES["global_plot_progress"],
        "foreshadowing": global_dir / GLOBAL_FILE_NAMES["foreshadowing"],
        "world_model": global_dir / GLOBAL_FILE_NAMES["world_model"],
        "volume_outline": volume_dir / f"{volume_number}_volume_outline.md",
        "adaptation_review": volume_dir / f"{volume_number}_adaptation_review.md",
        "source_digest": volume_dir / "00_source_digest.md",
        "stage_manifest": volume_dir / "00_stage_manifest.md",
        "response_debug": volume_dir / "00_last_response_debug.md",
    }


def write_stage_status_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    status: str,
    note: str | None = None,
    total_batches: int | None = None,
    current_batch: int | None = None,
    current_batch_range: str | None = None,
    error_message: str | None = None,
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    loaded_files = build_loaded_file_inventory(volume_material)

    payload = {
        "generated_at": now_iso(),
        "status": status,
        "note": note,
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "chapter_count": len(volume_material["chapters"]),
        "extra_file_count": len(volume_material["extras"]),
        "total_batches": total_batches,
        "current_batch": current_batch,
        "current_batch_range": current_batch_range,
        "error_message": error_message,
        "loaded_files": loaded_files,
    }
    write_markdown_data(
        paths["stage_manifest"],
        title=f"Stage Status {volume_material['volume_number']}",
        payload=payload,
        summary_lines=[
            f"status: {status}",
            f"processed_volume: {volume_material['volume_number']}",
            f"chapter_count: {len(volume_material['chapters'])}",
            f"extra_file_count: {len(volume_material['extras'])}",
            f"total_batches: {total_batches if total_batches is not None else 'pending'}",
            f"current_batch: {current_batch if current_batch is not None else 'pending'}",
            f"current_batch_range: {current_batch_range or 'pending'}",
            f"note: {note or 'none'}",
            f"error_message: {error_message or 'none'}",
        ],
    )


def write_source_inventory_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    note: str,
    total_batches: int | None = None,
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    loaded_files = build_loaded_file_inventory(volume_material)

    payload = {
        "generated_at": now_iso(),
        "status": "loaded_source_files",
        "note": note,
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "chapter_count": len(volume_material["chapters"]),
        "extra_file_count": len(volume_material["extras"]),
        "total_batches": total_batches,
        "loaded_files": loaded_files,
    }
    write_markdown_data(
        paths["source_digest"],
        title=f"Source Inventory {volume_material['volume_number']}",
        payload=payload,
        summary_lines=[
            f"processed_volume: {volume_material['volume_number']}",
            f"chapter_count: {len(volume_material['chapters'])}",
            f"extra_file_count: {len(volume_material['extras'])}",
            f"total_batches: {total_batches if total_batches is not None else 'pending'}",
            f"note: {note}",
        ],
    )


def write_response_debug_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    error_message: str,
    preview: str,
    raw_body_text: str = "",
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    payload = {
        "generated_at": now_iso(),
        "processed_volume": volume_material["volume_number"],
        "error_message": error_message,
        "preview": preview,
        "raw_body_text": raw_body_text,
    }
    write_markdown_data(
        paths["response_debug"],
        title=f"Last Response Debug {volume_material['volume_number']}",
        payload=payload,
        summary_lines=[
            f"processed_volume: {volume_material['volume_number']}",
            f"error_message: {error_message}",
            f"preview_length: {len(preview)}",
            f"raw_body_length: {len(raw_body_text)}",
        ],
    )


def adaptation_doc_label(doc_key: str) -> str:
    labels = {
        "world_design": "世界观设计",
        "world_model": "世界模型",
        "style_guide": "文笔写作风格",
        "book_outline": "全书大纲",
        "foreshadowing": "伏笔管理",
        "global_plot_progress": "全局剧情进程",
        "volume_outline": "卷级大纲",
    }
    return labels.get(doc_key, doc_key)


def adaptation_doc_scope(doc_key: str) -> str:
    return "volume" if doc_key == "volume_outline" else "global"


def adaptation_review_allowed_files(paths: dict[str, Path]) -> dict[str, Path]:
    targets = {doc_key: paths[doc_key] for doc_key in GLOBAL_INJECTION_DOC_ORDER}
    targets["volume_outline"] = paths["volume_outline"]
    return targets


def adaptation_review_target_snapshot(
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    *,
    content_limit: int = 30000,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for file_key, target in allowed_files.items():
        path = target.path if isinstance(target, document_ops.DocumentTarget) else target
        current_content = read_text_if_exists(path).strip()
        snapshots.append(
            AdaptationReviewTarget(
                file_key=file_key,
                file_name=path.name,
                file_path=str(path),
                label=adaptation_doc_label(file_key),
                scope=adaptation_doc_scope(file_key),
                exists=path.exists(),
                current_char_count=len(current_content),
                current_content=clip_for_context(current_content, limit=content_limit),
                preferred_mode="patch" if current_content else "write",
            ).model_dump(mode="json")
        )
    return snapshots


def document_operation_payload(operation: document_ops.DocumentOperationCallResult) -> dict[str, Any]:
    if operation.mode == "write":
        payload = operation.write_payload or document_ops.DocumentWritePayload()
    elif operation.mode == "edit":
        payload = operation.edit_payload or document_ops.DocumentEditPayload()
    else:
        payload = operation.patch_payload or document_ops.DocumentPatchPayload()
    return {
        "mode": operation.mode,
        "response_id": operation.response_id,
        "output_types": operation.output_types,
        "payload": payload.model_dump(mode="json"),
    }


def build_document_operation_repair_payload(
    *,
    apply_error: Exception,
    failed_operation: document_ops.DocumentOperationCallResult,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Any]:
    return {
        "document_request": {
            "phase": "adaptation_review_fix_locator_repair",
            "role": "卷资料审核原地返修定位修正",
            "task": "修正上一次工具调用中无法定位的 old_text 或 match_text，并重新提交可应用的局部编辑。",
        },
        "previous_tool_call_failed": {
            "error": str(apply_error),
            "failed_operation": document_operation_payload(failed_operation),
        },
        "update_target_files": adaptation_review_target_snapshot(allowed_files),
        "requirements": [
            "只修正上一次工具调用中无法定位的 old_text 或 match_text，不要改成整篇写入。",
            "所有 old_text 或 match_text 必须从 update_target_files.current_content 中逐字复制。",
            "replace、insert_before、insert_after 的定位文本必须在当前文件中唯一匹配。",
            "如果短句无法唯一定位，必须扩大到包含前后连续段落的稳定上下文块。",
            "保留原本的修改意图，只修正定位与必要的新文本，不要额外改写无关内容。",
        ],
    }


def write_document_operation_apply_debug_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    error_message: str,
    operation: document_ops.DocumentOperationCallResult,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> None:
    write_response_debug_snapshot(
        manifest,
        volume_material,
        error_message=error_message,
        preview=operation.preview,
        raw_body_text=json.dumps(
            {
                "failed_operation": document_operation_payload(operation),
                "target_files": adaptation_review_target_snapshot(allowed_files),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def apply_document_operation_with_repair(
    *,
    client: OpenAI,
    model: str,
    instructions: str,
    shared_prompt: str,
    operation: document_ops.DocumentOperationCallResult,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    previous_response_id: str | None,
    prompt_cache_key: str | None,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> tuple[document_ops.AppliedDocumentOperation, str | None, list[str]]:
    current_operation = operation
    current_response_id = previous_response_id
    repair_response_ids: list[str] = []

    for repair_attempt in range(MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS + 1):
        try:
            applied = document_ops.apply_document_operation(
                current_operation,
                allowed_files=allowed_files,
            )
            return applied, current_response_id, repair_response_ids
        except ValueError as error:
            if repair_attempt >= MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS:
                write_document_operation_apply_debug_snapshot(
                    manifest,
                    volume_material,
                    error_message=str(error),
                    operation=current_operation,
                    allowed_files=allowed_files,
                )
                raise

            print_progress(
                "模型返回的资料修复定位未能应用："
                f"{error} 正在请求修正定位块（{repair_attempt + 1}/{MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS}）。",
                error=True,
            )
            repair_payload = build_document_operation_repair_payload(
                apply_error=error,
                failed_operation=current_operation,
                allowed_files=allowed_files,
            )
            current_operation = document_ops.call_document_operation_tools(
                client,
                model=model,
                instructions=instructions,
                user_input=shared_prompt + json.dumps(repair_payload, ensure_ascii=False, indent=2),
                previous_response_id=current_response_id,
                prompt_cache_key=prompt_cache_key,
                retries=DEFAULT_API_RETRIES,
                retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
            )
            current_response_id = current_operation.response_id
            repair_response_ids.append(str(current_operation.response_id or ""))

    raise RuntimeError("卷资料审核修复定位流程异常结束。")


def build_adaptation_review_request(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Any]:
    volume_number = volume_material["volume_number"]
    return {
        "document_request": {
            "phase": "adaptation_volume_review",
            "role": "卷资料审核",
            "task": "审核当前卷资料文档是否已经满足后续章节仿写需要，并判断是否可以结束本卷资料阶段。",
        },
        "review_scope": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest["target_worldview"],
            "current_volume": volume_number,
            "document_set_policy": (
                "审核下游仿写实际会用到的完整当前资料集。第 001 卷应包含 7 个核心资料文档；"
                "后续卷审核本卷更新文档，并带上已存在的文笔写作风格文档。"
            ),
        },
        "requirements": [
            "判断资料是否足够支撑后续章节仿写，而不是只检查格式是否完整。",
            "检查参考源人物名、地名、姓氏、事件名称、专用术语是否已经替换或映射，不得直接照搬。",
            "检查世界观设定是否已经改成目标世界观，并与参考源明显区分。",
            "检查世界模型中的地点、势力、能力、资源、规则和术语是否有清晰的新书映射。",
            "检查全书大纲是否是仿写书籍的大纲，不得把参考源原大纲照抄为新书大纲。",
            "检查当前卷卷级大纲的角色推进、冲突、高潮、结尾钩子是否正确映射。",
            "检查全局剧情进程是否把时间线、故事线、支线、反派线和跨卷线整理清楚。",
            "检查伏笔文档是否保留功能映射，同时改成新书自己的伏笔、回收点与命名。",
            "检查文风文档是否可执行，且只提炼写法与节奏，不复制参考源实体内容。",
            "如果不通过，rewrite_targets 必须只填写需要修复的 file_key，例如 world_design、world_model、book_outline、volume_outline。",
        ],
        "adaptation_documents": adaptation_review_target_snapshot(allowed_files),
        "output_contract": {
            "passed": "布尔值；只有所有阻塞问题解决才为 true。",
            "review_md": "Markdown 审核报告；必须写清通过/不通过原因。",
            "blocking_issues": "不通过时列出会阻塞后续仿写的具体问题。",
            "rewrite_targets": "不通过时列出需要原地修复的目标 file_key；通过时为空数组。",
        },
    }


def write_adaptation_review_report(
    path: Path,
    *,
    volume_number: str,
    review: AdaptationReviewPayload,
    attempt: int,
    response_id: str | None,
) -> None:
    lines = [
        f"# Adaptation Review {volume_number}",
        "",
        f"- generated_at: {now_iso()}",
        f"- volume_number: {volume_number}",
        f"- attempt: {attempt}",
        f"- passed: {review.passed}",
        f"- response_id: {response_id or 'none'}",
        f"- rewrite_targets: {', '.join(review.rewrite_targets) if review.rewrite_targets else 'none'}",
        "",
    ]
    if review.blocking_issues:
        lines.append("## Blocking Issues")
        lines.append("")
        lines.extend(f"- {issue}" for issue in review.blocking_issues)
        lines.append("")
    lines.append("## Review")
    lines.append("")
    lines.append(review.review_md.strip())
    write_text_if_changed(path, "\n".join(lines))


def build_adaptation_review_fix_request(
    *,
    review: AdaptationReviewPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Any]:
    return {
        "document_request": {
            "phase": "adaptation_review_fix",
            "role": "卷资料审核原地返修编辑",
            "task": "根据刚才未通过的卷资料审核结果，直接修复允许范围内的目标资料文档；不要重新生成整卷资料阶段。",
        },
        "failed_review_result": {
            "passed": review.passed,
            "review_md": review.review_md,
            "blocking_issues": review.blocking_issues,
            "rewrite_targets": review.rewrite_targets,
        },
        "update_target_files": adaptation_review_target_snapshot(allowed_files),
        "requirements": [
            "这是卷资料审核不通过后的原地修复步骤，不要返回新的审核报告。",
            "必须调用目标文件 write/edit/patch 工具提交修改；已有非空文件优先 patch 或 edit，禁止无理由整篇覆盖。",
            "只修改 failed_review_result 指出的阻塞问题直接影响的文件和局部。",
            "所有 file_key 或 file_path 必须来自 update_target_files，禁止修改未授权文件。",
            "所有 old_text 或 match_text 必须从 update_target_files.current_content 中逐字复制。",
            "不得把审核失败降级为重新跑整卷资料生成阶段。",
            "修复后仍必须符合目标世界观、实体改名、事件改名、术语映射、时间线和故事线整理要求。",
        ],
    }


def apply_adaptation_review_fix_with_repair(
    *,
    client: OpenAI,
    model: str,
    shared_prompt: str,
    review: AdaptationReviewPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    previous_response_id: str | None,
    prompt_cache_key: str | None,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> tuple[document_ops.AppliedDocumentOperation, str | None, list[str]]:
    if not review.rewrite_targets:
        error_message = "卷资料审核未通过，但模型未返回可修复目标。"
        write_response_debug_snapshot(
            manifest,
            volume_material,
            error_message=error_message,
            preview=review.review_md,
            raw_body_text=json.dumps(
                {
                    "failed_review_result": review.model_dump(mode="json"),
                    "target_files": adaptation_review_target_snapshot(allowed_files),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        raise llm_runtime.ModelOutputError(error_message, preview=review.review_md)

    fix_payload = build_adaptation_review_fix_request(
        review=review,
        allowed_files=allowed_files,
    )
    operation = document_ops.call_document_operation_tools(
        client,
        model=model,
        instructions=COMMON_ADAPTATION_REVIEW_FIX_INSTRUCTIONS,
        user_input=shared_prompt + json.dumps(fix_payload, ensure_ascii=False, indent=2),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=DEFAULT_API_RETRIES,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
    )
    response_ids = [str(operation.response_id or "")]
    applied, current_response_id, repair_response_ids = apply_document_operation_with_repair(
        client=client,
        model=model,
        instructions=COMMON_ADAPTATION_REVIEW_FIX_INSTRUCTIONS,
        shared_prompt=shared_prompt,
        operation=operation,
        allowed_files=allowed_files,
        previous_response_id=operation.response_id,
        prompt_cache_key=prompt_cache_key,
        manifest=manifest,
        volume_material=volume_material,
    )
    response_ids.extend(repair_response_ids)
    if not applied.emitted_keys or not applied.changed_keys:
        error_message = "卷资料审核原地返修没有实际修改任何目标文件。"
        write_document_operation_apply_debug_snapshot(
            manifest,
            volume_material,
            error_message=error_message,
            operation=operation,
            allowed_files=allowed_files,
        )
        raise llm_runtime.ModelOutputError(error_message, preview=operation.preview, raw_body_text=operation.raw_body_text)
    return applied, current_response_id, response_ids


def run_adaptation_review_until_passed(
    *,
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
) -> tuple[AdaptationReviewResult, str | None]:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    allowed_files = adaptation_review_allowed_files(paths)
    response_ids: list[str] = []
    current_response_id = previous_response_id
    last_review: AdaptationReviewPayload | None = None

    for attempt in range(1, MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS + 2):
        write_stage_status_snapshot(
            manifest,
            volume_material,
            status="adaptation_reviewing",
            note=f"正在进行第 {attempt} 次卷资料审核；审核通过后才会标记本卷完成。",
        )
        print_progress(f"卷资料审核第 {attempt}/{MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS + 1} 次调用：审核第 {volume_material['volume_number']} 卷资料。")
        review_payload = build_adaptation_review_request(
            manifest=manifest,
            volume_material=volume_material,
            allowed_files=allowed_files,
        )
        review, current_response_id, _ = call_adaptation_review_response(
            client,
            model,
            COMMON_ADAPTATION_REVIEW_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(review_payload, ensure_ascii=False, indent=2),
            previous_response_id=current_response_id,
            prompt_cache_key=prompt_cache_key,
        )
        if current_response_id:
            response_ids.append(current_response_id)
        last_review = review
        write_adaptation_review_report(
            paths["adaptation_review"],
            volume_number=volume_material["volume_number"],
            review=review,
            attempt=attempt,
            response_id=current_response_id,
        )

        if review.passed:
            print_progress("卷资料审核已通过。")
            return (
                AdaptationReviewResult(
                    payload=review,
                    response_ids=response_ids,
                    review_path=str(paths["adaptation_review"]),
                    fix_attempts=attempt - 1,
                ),
                current_response_id,
            )

        if attempt > MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS:
            error_message = f"卷资料审核原地返修 {MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS} 次后仍未通过。"
            write_response_debug_snapshot(
                manifest,
                volume_material,
                error_message=error_message,
                preview=review.review_md,
                raw_body_text=json.dumps(
                    {
                        "failed_review_result": review.model_dump(mode="json"),
                        "target_files": adaptation_review_target_snapshot(allowed_files),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            raise llm_runtime.ModelOutputError(error_message, preview=review.review_md)

        print_progress(
            "卷资料审核未通过，进入当前审核阶段原地返修；"
            f"目标：{', '.join(review.rewrite_targets) if review.rewrite_targets else '未返回'}。"
        )
        applied_fix, current_response_id, fix_response_ids = apply_adaptation_review_fix_with_repair(
            client=client,
            model=model,
            shared_prompt=stage_shared_prompt,
            review=review,
            allowed_files=allowed_files,
            previous_response_id=current_response_id,
            prompt_cache_key=prompt_cache_key,
            manifest=manifest,
            volume_material=volume_material,
        )
        response_ids.extend(fix_response_ids)
        print_progress(
            "卷资料审核返修已应用："
            f"模式={applied_fix.mode}，文件={', '.join(applied_fix.changed_keys)}。"
        )

    error_message = "卷资料审核流程异常结束。"
    raise llm_runtime.ModelOutputError(error_message, preview=last_review.review_md if last_review else "")


def write_stage_outputs(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    generated_documents: list[dict[str, Any]],
    source_char_count: int,
    loaded_file_count: int,
) -> dict[str, Path]:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    write_markdown_data(
        paths["source_digest"],
        title=f"Source Inventory {volume_material['volume_number']}",
        payload={
            "generated_at": now_iso(),
            "status": "loaded_source_files",
            "processed_volume": volume_material["volume_number"],
            "source_volume_dir": volume_material["volume_dir"],
            "chapter_count": len(volume_material["chapters"]),
            "extra_file_count": len(volume_material["extras"]),
            "loaded_files": build_loaded_file_inventory(volume_material),
            "source_char_count": source_char_count,
        },
        summary_lines=[
            f"processed_volume: {volume_material['volume_number']}",
            f"source_volume_dir: {volume_material['volume_dir']}",
            f"chapter_count: {len(volume_material['chapters'])}",
            f"extra_file_count: {len(volume_material['extras'])}",
            f"source_char_count: {source_char_count or 'unknown'}",
        ],
    )

    stage_manifest_payload = {
        "generated_at": now_iso(),
        "status": "review_pending",
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "request_mode": "per_document_function_call_with_volume_session",
        "api_calls": generated_documents,
        "loaded_file_count": loaded_file_count,
        "source_char_count": source_char_count,
        "generated_document_keys": [item.get("key") for item in generated_documents],
        "global_files": {
            key: str(paths[key])
            for key in ("book_outline", "world_design", "style_guide", "world_model", "global_plot_progress", "foreshadowing")
            if paths[key].exists()
        },
        "volume_files": {
            "volume_outline": str(paths["volume_outline"]),
            "adaptation_review": str(paths["adaptation_review"]),
            "source_digest": str(paths["source_digest"]),
        },
        "adaptation_review": {
            "status": "pending",
            "review_file": str(paths["adaptation_review"]),
            "note": "资料文档已生成，等待卷资料审核通过后才会标记本卷完成。",
        },
        "stage_summary": {
            "processed_volume": volume_material["volume_number"],
            "generated_documents": [item.get("label") for item in generated_documents],
            "loaded_file_count": loaded_file_count,
            "source_char_count": source_char_count,
        },
    }
    write_markdown_data(
        paths["stage_manifest"],
        title=f"Stage Manifest {volume_material['volume_number']}",
        payload=stage_manifest_payload,
        summary_lines=[
            f"status: review_pending",
            f"processed_volume: {volume_material['volume_number']}",
            f"request_mode: per_document_function_call_with_volume_session",
            f"global_dir: {paths['global_dir']}",
            f"volume_dir: {paths['volume_dir']}",
            f"adaptation_review: {paths['adaptation_review']}",
        ],
    )

    return paths


def mark_volume_processed_after_review(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    generated_documents: list[dict[str, Any]],
    source_char_count: int,
    loaded_file_count: int,
    review_result: AdaptationReviewResult,
) -> dict[str, Path]:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    processed = set(manifest.get("processed_volumes", []))
    processed.add(volume_material["volume_number"])
    manifest["processed_volumes"] = sorted(processed)
    manifest["last_processed_volume"] = volume_material["volume_number"]
    save_manifest(manifest)

    review = review_result.payload
    stage_manifest_payload = {
        "generated_at": now_iso(),
        "status": "completed",
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "request_mode": "per_document_function_call_with_volume_session_with_volume_review",
        "api_calls": generated_documents,
        "loaded_file_count": loaded_file_count,
        "source_char_count": source_char_count,
        "generated_document_keys": [item.get("key") for item in generated_documents],
        "global_files": {
            key: str(paths[key])
            for key in ("book_outline", "world_design", "style_guide", "world_model", "global_plot_progress", "foreshadowing")
            if paths[key].exists()
        },
        "volume_files": {
            "volume_outline": str(paths["volume_outline"]),
            "adaptation_review": str(paths["adaptation_review"]),
            "source_digest": str(paths["source_digest"]),
        },
        "adaptation_review": {
            "status": "passed" if review.passed else "failed",
            "passed": review.passed,
            "review_file": review_result.review_path,
            "response_ids": review_result.response_ids,
            "fix_attempts": review_result.fix_attempts,
            "blocking_issues": review.blocking_issues,
            "rewrite_targets": review.rewrite_targets,
        },
        "stage_summary": {
            "processed_volume": volume_material["volume_number"],
            "generated_documents": [item.get("label") for item in generated_documents],
            "loaded_file_count": loaded_file_count,
            "source_char_count": source_char_count,
            "adaptation_review_status": "passed" if review.passed else "failed",
        },
    }
    write_markdown_data(
        paths["stage_manifest"],
        title=f"Stage Manifest {volume_material['volume_number']}",
        payload=stage_manifest_payload,
        summary_lines=[
            "status: completed",
            f"processed_volume: {volume_material['volume_number']}",
            "request_mode: per_document_function_call_with_volume_session_with_volume_review",
            f"global_dir: {paths['global_dir']}",
            f"volume_dir: {paths['volume_dir']}",
            f"adaptation_review: {paths['adaptation_review']}",
        ],
    )

    return paths


def render_dry_run_summary(
    manifest: dict[str, Any],
    target_volume: Path,
    volume_material: dict[str, Any],
    run_mode: str,
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, target_volume.name)
    _, source_char_count = build_volume_source_bundle(volume_material)
    plan = build_document_plan(target_volume.name)
    print(f"工程目录：{project_root}")
    print(f"全局注入目录：{paths['global_dir']}")
    print(f"待处理卷：{target_volume.name}")
    print(f"卷级注入目录：{paths['volume_dir']}")
    print(f"章节数：{len(volume_material['chapters'])}")
    print(f"补充资料数：{len(volume_material['extras'])}")
    print(f"总字符数：{source_char_count}")
    print(
        f"请求模式：逐文档函数工具调用生成 {len(plan)} 次，随后至少 1 次卷资料审核；"
        "每次调用都会携带当前卷全部文件原文，并沿用同一卷 previous_response_id 会话链。"
    )
    print(f"运行方式：{RUN_MODE_LABELS.get(run_mode, run_mode)}")
    print("本次 dry-run 不调用 API，也不会生成文档正文。")


def main() -> int:
    args = parse_args()
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH, legacy_path=LEGACY_GLOBAL_CONFIG_PATH)
    manifest: dict[str, Any] | None = None
    volume_material: dict[str, Any] | None = None
    target_volume: Path | None = None
    planned_calls = 0
    client: OpenAI | None = None
    openai_settings: dict[str, str] | None = None
    run_mode = RUN_MODE_STAGE

    try:
        print_progress("开始解析参考源目录。")
        source_root, existing_project_root, existing_manifest = resolve_input_root(
            args.source_root,
            global_config,
        )
        volume_dirs = discover_volume_dirs(source_root)
        manifest = init_or_load_project(
            args,
            source_root,
            volume_dirs,
            global_config,
            existing_project_root=existing_project_root,
            existing_manifest=existing_manifest,
        )
        global_config = openai_config.update_global_config(
            GLOBAL_CONFIG_PATH,
            global_config,
            {
                "last_input_root": str(existing_project_root or source_root),
                "last_source_root": str(source_root),
                "last_project_root": manifest["project_root"],
                "last_new_book_title": manifest["new_book_title"],
            },
        )
        run_mode = resolve_run_mode(args)
        print_progress(f"工程目录：{manifest['project_root']}")
        print_progress(f"参考源目录：{source_root}")
        print_progress(f"本次运行方式：{RUN_MODE_LABELS.get(run_mode, run_mode)}")
        if existing_manifest is not None or existing_project_root is not None:
            print_progress("已加载已有工程配置，将直接继续上次进度。")

        requested_volume = args.volume
        first_target_volume = select_volume_to_process(volume_dirs, manifest, requested_volume)
        if first_target_volume is None:
            print_progress("所有卷都已处理完成，没有新的卷需要生成。")
            return 0

        migration_warnings = ensure_project_dirs(Path(manifest["project_root"]))
        for warning in migration_warnings:
            print_progress(warning, error=True)
        if args.dry_run:
            print_progress(f"本次准备处理第 {first_target_volume.name} 卷。")
            volume_material = load_volume_material(first_target_volume)
            render_dry_run_summary(manifest, first_target_volume, volume_material, run_mode)
            return 0

        print_progress("开始准备 API 客户端。")
        api_key, global_config = openai_config.resolve_api_key(
            cli_api_key=args.api_key,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
        )
        openai_settings, global_config = openai_config.resolve_openai_settings(
            cli_base_url=args.base_url,
            cli_model=args.model,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
            legacy_settings=manifest.get("openai") if isinstance(manifest, dict) else None,
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

        while True:
            target_volume = select_volume_to_process(volume_dirs, manifest, requested_volume)
            requested_volume = None
            if target_volume is None:
                print_progress("所有卷都已处理完成，没有新的卷需要生成。")
                return 0

            print_progress(f"本次准备处理第 {target_volume.name} 卷。")
            volume_material = load_volume_material(target_volume)
            source_bundle, source_char_count = build_volume_source_bundle(volume_material)
            loaded_files = build_loaded_file_inventory(volume_material)
            document_plan = build_document_plan(volume_material["volume_number"])
            planned_calls = len(document_plan)
            paths = stage_paths(Path(manifest["project_root"]), volume_material["volume_number"])
            write_source_inventory_snapshot(
                manifest,
                volume_material,
                note="已完成当前卷全部源文件扫描，本阶段后续每一次请求都会携带当前卷全部文件原文与文件清单。",
                total_batches=planned_calls,
            )
            write_stage_status_snapshot(
                manifest,
                volume_material,
                status="stage_session_started",
                note="已读取当前卷全部文件，准备按单文档顺序生成；本阶段每次请求都会重新附带整卷内容。",
                total_batches=planned_calls,
                current_batch=1,
                current_batch_range=document_plan[0]["key"],
            )
            print_progress(
                f"已加载 {volume_material['volume_number']} 卷全部文件："
                f"{len(volume_material['chapters'])} 个章节文件，"
                f"{len(volume_material['extras'])} 个补充文件，"
                f"总字符数约 {source_char_count}。"
            )
            print_progress(
                f"本阶段将使用 {planned_calls} 次 API 调用逐份生成函数工具文档，"
                f"每次调用都会携带当前卷全部文件原文，共加载 {len(loaded_files)} 个文件。"
            )
            print_progress("本阶段已启用稳定共享前缀，提示词缓存将复用：项目上下文、阶段规则、文件清单与整卷原文。")
            existing_docs = read_existing_global_docs(Path(manifest["project_root"]))
            current_docs = dict(existing_docs)
            previous_response_id: str | None = None
            prompt_cache_key = build_phase_session_key(manifest, volume_material["volume_number"])
            stage_shared_prompt = build_stage_shared_prompt(
                manifest=manifest,
                volume_material=volume_material,
                loaded_files=loaded_files,
                source_bundle=source_bundle,
                source_char_count=source_char_count,
            )
            generated_documents: list[dict[str, Any]] = []

            for index, doc_spec in enumerate(document_plan, start=1):
                doc_key = str(doc_spec["key"])
                doc_label = str(doc_spec["label"])
                write_stage_status_snapshot(
                    manifest,
                    volume_material,
                    status="generating_document",
                    note=f"正在生成 {doc_label}；本次请求将重新附带当前卷全部文件原文。",
                    total_batches=planned_calls,
                    current_batch=index,
                    current_batch_range=doc_key,
                )
                print_progress(f"第 {index}/{planned_calls} 次调用：生成{doc_label}。")
                print_request_context_summary(
                    doc_label=doc_label,
                    current_doc_key=doc_key,
                    volume_material=volume_material,
                    current_docs=current_docs,
                    loaded_files=loaded_files,
                    source_char_count=source_char_count,
                    previous_response_id=previous_response_id,
                )
                output_path = document_output_path(paths, doc_key)
                operation_result, previous_response_id = generate_document_operation(
                    client,
                    openai_settings["model"],
                    manifest,
                    volume_material,
                    current_docs,
                    doc_key=doc_key,
                    output_path=output_path,
                    stage_shared_prompt=stage_shared_prompt,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=prompt_cache_key,
                )
                print_progress(f"{doc_label} 已返回，开始写入文件。")
                applied = document_ops.apply_document_operation(
                    operation_result,
                    allowed_files={doc_key: output_path},
                )
                current_docs[doc_key] = read_text(output_path) if output_path.exists() else ""
                generated_documents.append(
                    {
                        "index": index,
                        "key": doc_key,
                        "label": doc_label,
                        "response_id": previous_response_id,
                        "output_path": str(output_path),
                        "operation_mode": applied.mode,
                        "changed": bool(applied.changed_keys),
                    }
                )
                print_progress(
                    f"{doc_label} 已处理：{output_path}，模式={applied.mode}，"
                    f"{'已更新' if applied.changed_keys else '内容无变化'}。"
                )

            print_progress("本阶段文档生成完成，开始更新阶段索引文件并进入卷资料审核。")
            paths = write_stage_outputs(
                manifest=manifest,
                volume_material=volume_material,
                generated_documents=generated_documents,
                source_char_count=source_char_count,
                loaded_file_count=len(loaded_files),
            )
            review_result, previous_response_id = run_adaptation_review_until_passed(
                client=client,
                model=openai_settings["model"],
                manifest=manifest,
                volume_material=volume_material,
                stage_shared_prompt=stage_shared_prompt,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
            )
            paths = mark_volume_processed_after_review(
                manifest,
                volume_material,
                generated_documents=generated_documents,
                source_char_count=source_char_count,
                loaded_file_count=len(loaded_files),
                review_result=review_result,
            )

            print_progress(f"已处理卷：{volume_material['volume_number']}")
            print_progress(f"工程目录：{manifest['project_root']}")
            print_progress(f"全局注入目录：{paths['global_dir']}")
            print_progress(f"卷级注入目录：{paths['volume_dir']}")
            print_progress(f"全书大纲：{paths['book_outline']}")
            print_progress(f"世界观设计：{paths['world_design']}")
            print_progress(f"世界模型：{paths['world_model']}")
            print_progress(f"全局剧情进程：{paths['global_plot_progress']}")
            if any(item.get("key") == "style_guide" for item in generated_documents):
                print_progress(f"文笔风格：{paths['style_guide']}")
            elif paths["style_guide"].exists():
                print_progress(f"文笔风格：沿用已有文档 {paths['style_guide']}")
            else:
                print_progress("文笔风格：本阶段未生成，当前工程中也暂无现成文档。")
            print_progress(f"伏笔文档：{paths['foreshadowing']}")
            print_progress(f"卷级大纲：{paths['volume_outline']}")
            print_progress(f"卷资料审核：{paths['adaptation_review']}")

            next_volume = find_next_pending_volume_after(
                volume_dirs,
                manifest,
                volume_material["volume_number"],
            )
            if args.workflow_controlled:
                print_progress("当前卷阶段已完成，统一工作流将接管后续调度。")
                return 0
            if run_mode == RUN_MODE_STAGE:
                if not prompt_next_stage(next_volume):
                    return 0
                print_progress(f"准备进入下一阶段：第 {next_volume.name} 卷。")
                requested_volume = next_volume.name
                continue

            if next_volume is None:
                print_progress("当前卷之后没有新的待处理卷可继续了。")
                return 0
            print_progress(f"按全书运行，自动进入下一阶段：第 {next_volume.name} 卷。")
            requested_volume = next_volume.name
    except KeyboardInterrupt:
        print_progress("已取消。", error=True)
        pause_before_exit()
        return 1
    except Exception as error:
        if manifest is not None and volume_material is not None:
            try:
                if isinstance(error, llm_runtime.ModelOutputError) and error.preview:
                    write_response_debug_snapshot(
                        manifest,
                        volume_material,
                        error_message=str(error),
                        preview=error.preview,
                        raw_body_text=getattr(error, "raw_body_text", ""),
                    )
                write_stage_status_snapshot(
                    manifest,
                    volume_material,
                    status="failed",
                    note="阶段执行失败，等待人工排查。",
                    total_batches=planned_calls or None,
                    error_message=str(error),
                )
            except Exception:
                pass
        print_progress(f"处理失败：{error}", error=True)
        pause_before_exit()
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
