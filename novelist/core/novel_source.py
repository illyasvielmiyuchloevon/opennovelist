from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .files import read_text
from .ui import fail


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


def get_chapter_material(volume_material: dict[str, Any], chapter_number: str) -> dict[str, Any]:
    normalized = chapter_number.zfill(4)
    for chapter in volume_material["chapters"]:
        if chapter["chapter_number"] == normalized:
            return chapter
    fail(f"未在当前卷中找到章节：{normalized}")


def build_chapter_source_bundle(
    volume_material: dict[str, Any],
    chapter_number: str,
) -> tuple[str, int]:
    chapter = get_chapter_material(volume_material, chapter_number)
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
