from __future__ import annotations

from ._shared import *  # noqa: F401,F403


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

def style_reference_context(manifest: dict[str, Any]) -> str:
    style_mode = manifest["style"]["mode"]
    if style_mode == STYLE_MODE_CUSTOM:
        style_file = manifest["style"]["style_file"]
        if not style_file:
            return "未提供自定义风格文件。"
        return read_text(Path(style_file)).strip()
    return "请从当前卷参考源中提炼写作风格，不额外加载外部风格文件。"

def protagonist_context(manifest: dict[str, Any]) -> str:
    protagonist = manifest["protagonist"]
    if protagonist["mode"] == PROTAGONIST_MODE_CUSTOM:
        return protagonist["description"] or "未提供详细主角设定。"
    return "请结合目标世界观与参考卷人物功能，柔和改造出新的主角设定和性格。"

def read_existing_global_docs(project_root: Path) -> dict[str, str]:
    global_dir = project_root / GLOBAL_DIRNAME
    docs: dict[str, str] = {}
    for key in GLOBAL_INJECTION_DOC_ORDER:
        file_name = GLOBAL_FILE_NAMES[key]
        path = global_dir / file_name
        docs[key] = path.read_text(encoding="utf-8") if path.exists() else ""
    return docs

__all__ = [
    'build_phase_session_key',
    'first_non_empty_line',
    'load_volume_material',
    'build_loaded_file_inventory',
    'build_volume_source_bundle',
    'chunk_text_items',
    'prompt_next_stage',
    'style_reference_context',
    'protagonist_context',
    'read_existing_global_docs',
]
