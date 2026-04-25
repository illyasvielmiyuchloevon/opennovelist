from __future__ import annotations

from pathlib import Path


def format_file_inventory_line(index: int, path: Path, *, char_count: int | None = None) -> str:
    suffix = f"，字符数约 {char_count}" if char_count is not None else ""
    return f"文件[{index}]：{path.name}{suffix}"


__all__ = ["format_file_inventory_line"]
