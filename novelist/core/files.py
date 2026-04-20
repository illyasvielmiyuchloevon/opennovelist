from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .ui import fail


DEFAULT_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_path(raw_path: str, *, cwd: Path | None = None) -> Path:
    normalized = raw_path.strip().strip('"').strip("'")
    if not normalized:
        fail("路径不能为空。")
    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    return path.resolve()


def read_text(path: Path, *, encodings: tuple[str, ...] = DEFAULT_TEXT_ENCODINGS) -> str:
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"无法读取文件编码：{path}",
    )


def read_text_if_exists(path: Path, *, encodings: tuple[str, ...] = DEFAULT_TEXT_ENCODINGS) -> str:
    if not path.exists():
        return ""
    return read_text(path, encodings=encodings)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def write_text_if_changed(path: Path, content: str) -> bool:
    normalized = content.rstrip() + "\n"
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == normalized:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")
    return True


def merge_directory_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)

    for child in list(src.iterdir()):
        target = dst / child.name
        if child.is_dir():
            merge_directory_tree(child, target)
            try:
                child.rmdir()
            except OSError:
                pass
            continue

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(target))
            continue

        if child.read_bytes() == target.read_bytes():
            child.unlink()
            continue

        if child.stat().st_mtime > target.stat().st_mtime:
            target.unlink()
            shutil.move(str(child), str(target))
        else:
            child.unlink()

    try:
        src.rmdir()
    except OSError:
        pass


def migrate_numbered_injection_dirs(
    project_root: Path,
    *,
    container_dirname: str,
    suffix: str,
) -> Path:
    container_dir = project_root / container_dirname
    container_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^\d{{3}}{re.escape(suffix)}$")

    for child in list(project_root.iterdir()):
        if not child.is_dir():
            continue
        if child.parent != project_root:
            continue
        if child.name == container_dirname:
            continue
        if not pattern.fullmatch(child.name):
            continue
        merge_directory_tree(child, container_dir / child.name)

    return container_dir


def migrate_renamed_files(directory: Path, rename_map: dict[str, str]) -> None:
    if not directory.exists():
        return

    for old_name, new_name in rename_map.items():
        if old_name == new_name:
            continue
        src = directory / old_name
        dst = directory / new_name
        if not src.exists():
            continue
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            continue
        try:
            if src.read_bytes() == dst.read_bytes():
                src.unlink()
        except OSError:
            continue


def normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def detect_line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def convert_to_line_ending(text: str, ending: str) -> str:
    normalized = normalize_line_endings(text)
    if ending == "\r\n":
        return normalized.replace("\n", "\r\n")
    return normalized


def _trimmed_block_candidates(content: str, find: str) -> list[str]:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if not search_lines:
        return []

    candidates: list[str] = []
    search_len = len(search_lines)
    for start in range(0, len(original_lines) - search_len + 1):
        block = original_lines[start : start + search_len]
        if all(block[index].strip() == search_lines[index].strip() for index in range(search_len)):
            candidates.append("\n".join(block))
    return candidates


def _remove_common_indentation(text: str) -> str:
    lines = text.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return text
    min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    return "\n".join(line[min_indent:] if line.strip() else line for line in lines)


def _indentation_flexible_candidates(content: str, find: str) -> list[str]:
    content_lines = content.split("\n")
    search_lines = find.split("\n")
    if not search_lines:
        return []
    normalized_find = _remove_common_indentation(find)
    search_len = len(search_lines)
    candidates: list[str] = []
    for start in range(0, len(content_lines) - search_len + 1):
        block = "\n".join(content_lines[start : start + search_len])
        if _remove_common_indentation(block) == normalized_find:
            candidates.append(block)
    return candidates


def _block_anchor_candidates(content: str, find: str) -> list[str]:
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if len(search_lines) < 3:
        return []

    original_lines = content.split("\n")
    first_line = search_lines[0].strip()
    last_line = search_lines[-1].strip()
    candidates: list[str] = []
    for start in range(len(original_lines)):
        if original_lines[start].strip() != first_line:
            continue
        for end in range(start + 2, len(original_lines)):
            if original_lines[end].strip() == last_line:
                block = "\n".join(original_lines[start : end + 1])
                candidates.append(block)
                break
    return candidates


def _whitespace_normalized_candidates(content: str, find: str) -> list[str]:
    normalized_find = " ".join(find.split())
    if not normalized_find:
        return []
    candidates: list[str] = []
    lines = content.split("\n")
    for line in lines:
        if " ".join(line.split()) == normalized_find:
            candidates.append(line)
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        block_len = len(find_lines)
        for start in range(0, len(lines) - block_len + 1):
            block = "\n".join(lines[start : start + block_len])
            if " ".join(block.split()) == normalized_find:
                candidates.append(block)
    return candidates


def _dedupe_candidates(candidates: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def find_unique_text_match(content: str, target: str) -> str:
    if not target:
        raise ValueError("match_text 不能为空。")

    normalized_content = normalize_line_endings(content)
    normalized_target = normalize_line_endings(target)
    candidates = _dedupe_candidates(
        ([normalized_target] if normalized_target in normalized_content else [])
        + _trimmed_block_candidates(normalized_content, normalized_target)
        + _indentation_flexible_candidates(normalized_content, normalized_target)
        + _block_anchor_candidates(normalized_content, normalized_target)
        + _whitespace_normalized_candidates(normalized_content, normalized_target)
    )

    not_found = True
    for candidate in candidates:
        count = normalized_content.count(candidate)
        if count == 0:
            continue
        not_found = False
        if count == 1:
            return candidate

    if not_found:
        raise ValueError("未能在文件中定位 match_text，请提供更稳定的上下文块。")
    raise ValueError("match_text 在文件中匹配到多个位置，请补充更多上下文以确保唯一。")


def replace_text_with_fallbacks(content: str, old_text: str, new_text: str, *, replace_all: bool = False) -> str:
    if old_text == new_text:
        raise ValueError("old_text 与 new_text 相同，无法执行替换。")
    if not old_text:
        raise ValueError("replace 操作要求 old_text 非空。")

    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content)
    normalized_old = normalize_line_endings(old_text)
    normalized_new = normalize_line_endings(new_text)

    candidates = _dedupe_candidates(
        ([normalized_old] if normalized_old in normalized_content else [])
        + _trimmed_block_candidates(normalized_content, normalized_old)
        + _indentation_flexible_candidates(normalized_content, normalized_old)
        + _block_anchor_candidates(normalized_content, normalized_old)
        + _whitespace_normalized_candidates(normalized_content, normalized_old)
    )

    not_found = True
    for candidate in candidates:
        count = normalized_content.count(candidate)
        if count == 0:
            continue
        not_found = False
        if replace_all:
            return convert_to_line_ending(normalized_content.replace(candidate, normalized_new), original_ending)
        if count == 1:
            return convert_to_line_ending(
                normalized_content.replace(candidate, normalized_new, 1),
                original_ending,
            )

    if not_found:
        raise ValueError("未找到 old_text，请提供更稳定的上下文块。")
    raise ValueError("old_text 在文件中匹配到多个位置，请补充更多上下文以确保唯一。")


def extract_json_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        fail("结构化文档为空，无法读取。")

    try:
        loaded = json.loads(stripped)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    fenced_match = re.search(r"```json\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced_match:
        loaded = json.loads(fenced_match.group(1))
        if isinstance(loaded, dict):
            return loaded

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        loaded = json.loads(stripped[start : end + 1])
        if isinstance(loaded, dict):
            return loaded

    fail("未在 Markdown 中识别到可读取的 JSON 数据。")


def write_markdown_data(
    path: Path,
    *,
    title: str,
    payload: Any,
    summary_lines: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]

    if summary_lines:
        lines.extend(f"- {line}" for line in summary_lines)
        lines.append("")

    lines.extend(
        [
            "## Structured Data",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def sanitize_file_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip().rstrip(".")
    return cleaned or "untitled_project"


def normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def load_json_file(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})
    return loaded if isinstance(loaded, dict) else dict(default or {})


def save_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_dict_updates(data: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(data)
    merged.update({key: value for key, value in updates.items() if value is not None})
    return merged
