from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


CHAPTER_PATTERN = re.compile(
    r"^[ \t\u3000]*(?:"
    r"(?:正文[ \t\u3000]*)?第[0-9零一二三四五六七八九十百千万两〇○]+[章节回卷部篇集册季][^\r\n]*"
    r"|(?:序章|楔子|引子|前言|后记|终章|尾声|番外)[^\r\n]*"
    r")$",
    re.MULTILINE,
)
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16")
CHAPTERS_PER_VOLUME = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按章节拆分小说文本，并按每 50 章归档到卷目录。"
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        help="小说文本文件路径；不传时会在启动后提示输入。",
    )
    return parser.parse_args()


def get_source_path(raw_path: str | None) -> Path:
    if raw_path is None:
        raw_path = input("请输入小说文件路径：").strip()

    normalized = raw_path.strip().strip('"').strip("'")
    if not normalized:
        raise ValueError("未输入文件路径。")

    source_path = Path(normalized).expanduser()
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path

    source_path = source_path.resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"文件不存在：{source_path}")
    if not source_path.is_file():
        raise IsADirectoryError(f"路径不是文件：{source_path}")

    return source_path


def read_text(source_path: Path) -> tuple[str, str]:
    for encoding in ENCODINGS:
        try:
            return source_path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"无法读取文件编码，请确认文本是以下编码之一：{', '.join(ENCODINGS)}",
    )


def split_chapters(text: str) -> tuple[str, list[str]]:
    matches = list(CHAPTER_PATTERN.finditer(text))
    if not matches:
        raise ValueError(
            "未识别到章节标题。请确认小说文本中每章标题独占一行，且类似“第1章”或“第一章”。"
        )

    intro = text[: matches[0].start()].strip()
    chapters: list[str] = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chapter_text = text[start:end].strip()
        chapters.append(f"{chapter_text}\n")

    return intro, chapters


def ensure_output_root(source_path: Path) -> Path:
    base_dir = source_path.parent
    base_name = source_path.stem
    output_root = base_dir / base_name
    suffix = 1

    while output_root.exists():
        output_root = base_dir / f"{base_name}_{suffix}"
        suffix += 1

    output_root.mkdir(parents=True, exist_ok=False)
    return output_root


def write_intro_file(intro: str, source_path: Path, output_root: Path) -> Path:
    extension = source_path.suffix or ".txt"
    volume_dir = output_root / "001"
    volume_dir.mkdir(parents=True, exist_ok=True)

    intro_path = volume_dir / f"{source_path.stem}{extension}"
    intro_content = intro.strip()
    if intro_content:
        intro_content = f"{intro_content}\n"
    intro_path.write_text(intro_content, encoding="utf-8")
    return intro_path


def write_chapters(chapters: list[str], source_path: Path, output_root: Path) -> int:
    extension = source_path.suffix or ".txt"

    for index, chapter in enumerate(chapters, start=1):
        volume_number = (index - 1) // CHAPTERS_PER_VOLUME + 1
        volume_dir = output_root / f"{volume_number:03d}"
        volume_dir.mkdir(parents=True, exist_ok=True)

        chapter_path = volume_dir / f"{index:04d}{extension}"
        chapter_path.write_text(chapter, encoding="utf-8")

    return (len(chapters) - 1) // CHAPTERS_PER_VOLUME + 1


def main() -> None:
    args = parse_args()
    source_path = get_source_path(args.file_path)
    text, encoding = read_text(source_path)
    intro, chapters = split_chapters(text)
    output_root = ensure_output_root(source_path)
    intro_path = write_intro_file(intro, source_path, output_root)
    volume_count = write_chapters(chapters, source_path, output_root)

    print(f"源文件：{source_path}")
    print(f"读取编码：{encoding}")
    print(f"拆分章节数：{len(chapters)}")
    print(f"生成卷数：{volume_count}")
    print(f"简介文件：{intro_path}")
    print(f"输出目录：{output_root}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"处理失败：{error}", file=sys.stderr)
        raise SystemExit(1)
