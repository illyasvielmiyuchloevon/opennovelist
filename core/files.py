from __future__ import annotations

import json
import re
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
