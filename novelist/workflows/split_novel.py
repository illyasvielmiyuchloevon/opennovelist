from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from novelist.core.files import now_iso, read_text as read_existing_text


CHAPTER_PATTERN = re.compile(
    r"^[ \t\u3000]*(?:"
    r"(?:正文[ \t\u3000]*)?第[0-9零一二三四五六七八九十百千万两〇○]+[章节回卷部篇集册季][^\r\n]*"
    r"|(?:序章|楔子|引子|前言|后记|终章|尾声|番外)[^\r\n]*"
    r")$",
    re.MULTILINE,
)
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16")
CHAPTERS_PER_VOLUME = 50
TARGET_VOLUME_SOURCE_CHARS = 150_000
SOURCE_REBALANCE_BACKUP_DIRNAME = ".source_rebalance_backups"


@dataclass(frozen=True)
class PartitionChapter:
    chapter_number: str
    file_name: str
    text: str
    file_path: str = ""


@dataclass(frozen=True)
class PartitionExtra:
    file_name: str
    label: str
    text: str = ""
    raw_bytes: bytes | None = None
    file_path: str = ""


@dataclass(frozen=True)
class VolumePartition:
    volume_number: str
    chapters: list[PartitionChapter]
    source_char_count: int
    extra_char_count: int = 0
    over_budget: bool = False
    warning: str = ""


@dataclass(frozen=True)
class PartitionPlan:
    volumes: list[VolumePartition]
    target_chars: int
    max_chapters: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RebalanceReport:
    source_root: Path
    start_volume: str
    locked_volumes: list[str]
    dry_run: bool
    needed: bool
    changed: bool
    blocked: bool
    backup_dir: Path | None
    affected_volumes: list[str]
    old_volumes: list[VolumePartition]
    new_volumes: list[VolumePartition]
    warnings: list[str] = field(default_factory=list)


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


def _chapter_from_any(item: Any, index: int, *, extension: str = ".txt") -> PartitionChapter:
    if isinstance(item, PartitionChapter):
        return item
    if isinstance(item, dict):
        chapter_number = str(item.get("chapter_number") or item.get("number") or index).zfill(4)
        file_name = str(item.get("file_name") or f"{chapter_number}{extension}")
        text = str(item.get("text") or "")
        file_path = str(item.get("file_path") or item.get("path") or "")
        return PartitionChapter(chapter_number=chapter_number, file_name=file_name, text=text, file_path=file_path)
    return PartitionChapter(
        chapter_number=str(index).zfill(4),
        file_name=f"{index:04d}{extension}",
        text=str(item),
    )


def _chapter_source_block(chapter: PartitionChapter) -> str:
    return "\n".join(
        [
            f"[章节文件 {chapter.file_name}]",
            f"章节编号：{chapter.chapter_number}",
            f"文件路径：{chapter.file_path or chapter.file_name}",
            chapter.text.strip(),
        ]
    )


def _extra_source_block(extra: PartitionExtra) -> str:
    return "\n".join(
        [
            f"[补充文件 {extra.file_name}]",
            f"文件路径：{extra.file_path or extra.file_name}",
            extra.text.strip(),
        ]
    )


def estimate_source_bundle_chars(
    chapters: list[PartitionChapter],
    extras: list[PartitionExtra] | None = None,
) -> int:
    blocks = [_extra_source_block(extra) for extra in (extras or [])]
    blocks.extend(_chapter_source_block(chapter) for chapter in chapters)
    return len("\n\n".join(blocks))


def partition_chapters_by_budget(
    chapters: list[Any],
    *,
    max_chapters: int = CHAPTERS_PER_VOLUME,
    target_chars: int = TARGET_VOLUME_SOURCE_CHARS,
    start_volume: int | str = 1,
    extension: str = ".txt",
    extras_by_volume: dict[str, list[PartitionExtra]] | None = None,
) -> PartitionPlan:
    if max_chapters <= 0:
        raise ValueError("max_chapters 必须大于 0。")
    if target_chars <= 0:
        raise ValueError("target_chars 必须大于 0。")

    normalized = [_chapter_from_any(item, index, extension=extension) for index, item in enumerate(chapters, start=1)]
    extras_by_volume = extras_by_volume or {}
    volume_index = int(str(start_volume).zfill(3))
    offset = 0
    volumes: list[VolumePartition] = []
    warnings: list[str] = []

    while offset < len(normalized):
        volume_number = f"{volume_index:03d}"
        extras = extras_by_volume.get(volume_number, [])
        take = min(max_chapters, len(normalized) - offset)
        candidate = normalized[offset : offset + take]
        char_count = estimate_source_bundle_chars(candidate, extras)

        while len(candidate) > 1 and char_count > target_chars:
            candidate = candidate[:-1]
            char_count = estimate_source_bundle_chars(candidate, extras)

        over_budget = char_count > target_chars
        warning = ""
        if over_budget:
            warning = (
                f"第 {volume_number} 卷单章或补充文件已超过 {target_chars} 字符预算，"
                "无法继续拆小。"
            )
            warnings.append(warning)

        volumes.append(
            VolumePartition(
                volume_number=volume_number,
                chapters=candidate,
                source_char_count=char_count,
                extra_char_count=estimate_source_bundle_chars([], extras) if extras else 0,
                over_budget=over_budget,
                warning=warning,
            )
        )
        offset += len(candidate)
        volume_index += 1

    return PartitionPlan(volumes=volumes, target_chars=target_chars, max_chapters=max_chapters, warnings=warnings)


def partition_summary_lines(plan: PartitionPlan) -> list[str]:
    lines: list[str] = []
    for volume in plan.volumes:
        if volume.chapters:
            chapter_range = f"{volume.chapters[0].chapter_number}-{volume.chapters[-1].chapter_number}"
        else:
            chapter_range = "无章节"
        status = "超限" if volume.over_budget else "合格"
        lines.append(
            f"  - 第 {volume.volume_number} 卷：{chapter_range}，"
            f"{len(volume.chapters)} 章，source bundle 字符数约 {volume.source_char_count}，{status}。"
        )
        if volume.warning:
            lines.append(f"    * {volume.warning}")
    return lines


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


def _extras_for_existing_volumes(root: Path) -> dict[str, list[PartitionExtra]]:
    extras_by_volume: dict[str, list[PartitionExtra]] = {}
    for child in root.iterdir() if root.exists() else []:
        if not child.is_dir() or not re.fullmatch(r"\d{3}", child.name):
            continue
        extras: list[PartitionExtra] = []
        for path in sorted(child.iterdir(), key=lambda item: item.name):
            if not path.is_file() or re.fullmatch(r"\d{4}", path.stem):
                continue
            raw_bytes = path.read_bytes()
            extras.append(
                PartitionExtra(
                    file_name=path.name,
                    label=path.stem,
                    text=read_existing_text(path).strip(),
                    raw_bytes=raw_bytes,
                    file_path=str(path),
                )
            )
        if extras:
            extras_by_volume[child.name] = extras
    return extras_by_volume


def write_chapters(
    chapters: list[str],
    source_path: Path,
    output_root: Path,
    *,
    emit_summary: Any | None = None,
) -> int:
    extension = source_path.suffix or ".txt"
    partition_input = [
        PartitionChapter(
            chapter_number=f"{index:04d}",
            file_name=f"{index:04d}{extension}",
            text=chapter,
            file_path=str(output_root / "000" / f"{index:04d}{extension}"),
        )
        for index, chapter in enumerate(chapters, start=1)
    ]
    plan = partition_chapters_by_budget(
        partition_input,
        extension=extension,
        extras_by_volume=_extras_for_existing_volumes(output_root),
    )

    if emit_summary is not None:
        emit_summary(
            f"split_novel 自适应分卷：最多 {CHAPTERS_PER_VOLUME} 章/卷，"
            f"目标 source bundle 字符数 {TARGET_VOLUME_SOURCE_CHARS}。"
        )
        for line in partition_summary_lines(plan):
            emit_summary(line)

    for volume in plan.volumes:
        volume_dir = output_root / volume.volume_number
        volume_dir.mkdir(parents=True, exist_ok=True)
        for chapter in volume.chapters:
            chapter_path = volume_dir / chapter.file_name
            chapter_path.write_text(chapter.text, encoding="utf-8")

    return len(plan.volumes)


def _load_source_chapters_from_volumes(volume_dirs: list[Path]) -> list[PartitionChapter]:
    chapters: list[PartitionChapter] = []
    for volume_dir in volume_dirs:
        for path in sorted(volume_dir.iterdir(), key=lambda item: int(item.stem) if item.stem.isdigit() else 0):
            if not path.is_file() or not re.fullmatch(r"\d{4}", path.stem):
                continue
            chapters.append(
                PartitionChapter(
                    chapter_number=path.stem,
                    file_name=path.name,
                    text=read_existing_text(path).strip(),
                    file_path=str(path),
                )
            )
    chapters.sort(key=lambda chapter: int(chapter.chapter_number))
    return chapters


def _load_extras_by_volume(volume_dirs: list[Path]) -> dict[str, list[PartitionExtra]]:
    extras_by_volume: dict[str, list[PartitionExtra]] = {}
    for volume_dir in volume_dirs:
        extras: list[PartitionExtra] = []
        for path in sorted(volume_dir.iterdir(), key=lambda item: item.name):
            if not path.is_file() or re.fullmatch(r"\d{4}", path.stem):
                continue
            extras.append(
                PartitionExtra(
                    file_name=path.name,
                    label=path.stem,
                    text=read_existing_text(path).strip(),
                    raw_bytes=path.read_bytes(),
                    file_path=str(path),
                )
            )
        if extras:
            extras_by_volume[volume_dir.name] = extras
    return extras_by_volume


def _discover_numbered_volume_dirs(source_root: Path) -> list[Path]:
    return sorted(
        [
            child
            for child in source_root.iterdir()
            if child.is_dir() and re.fullmatch(r"\d{3}", child.name)
        ],
        key=lambda item: int(item.name),
    )


def _volume_partition_for_existing_dir(volume_dir: Path) -> VolumePartition:
    chapters = _load_source_chapters_from_volumes([volume_dir])
    extras = _load_extras_by_volume([volume_dir]).get(volume_dir.name, [])
    char_count = estimate_source_bundle_chars(chapters, extras)
    return VolumePartition(
        volume_number=volume_dir.name,
        chapters=chapters,
        source_char_count=char_count,
        extra_char_count=estimate_source_bundle_chars([], extras) if extras else 0,
        over_budget=char_count > TARGET_VOLUME_SOURCE_CHARS,
        warning=(
            f"第 {volume_dir.name} 卷超过 {TARGET_VOLUME_SOURCE_CHARS} 字符预算。"
            if char_count > TARGET_VOLUME_SOURCE_CHARS
            else ""
        ),
    )


def _chapter_assignment(volumes: list[VolumePartition]) -> dict[str, str]:
    assignment: dict[str, str] = {}
    for volume in volumes:
        for chapter in volume.chapters:
            assignment[chapter.chapter_number] = volume.volume_number
    return assignment


def rebalance_source_volumes(
    source_root: Path,
    *,
    start_volume: str,
    locked_volumes: list[str] | set[str],
    dry_run: bool = False,
    max_chapters: int = CHAPTERS_PER_VOLUME,
    target_chars: int = TARGET_VOLUME_SOURCE_CHARS,
) -> RebalanceReport:
    source_root = source_root.resolve()
    normalized_start = str(start_volume).zfill(3)
    locked = sorted({str(item).zfill(3) for item in locked_volumes if str(item).strip()})
    volume_dirs = _discover_numbered_volume_dirs(source_root)
    affected_dirs = [
        volume_dir
        for volume_dir in volume_dirs
        if volume_dir.name >= normalized_start
    ]
    old_volumes = [_volume_partition_for_existing_dir(volume_dir) for volume_dir in affected_dirs]

    if not affected_dirs:
        return RebalanceReport(
            source_root=source_root,
            start_volume=normalized_start,
            locked_volumes=locked,
            dry_run=dry_run,
            needed=False,
            changed=False,
            blocked=False,
            backup_dir=None,
            affected_volumes=[],
            old_volumes=[],
            new_volumes=[],
        )

    warnings: list[str] = []
    future_locked = [volume for volume in locked if volume >= normalized_start]
    if future_locked:
        warnings.append(
            "检测到起始卷之后存在已完成适配卷，无法安全重排："
            + "、".join(future_locked)
        )

    chapters = _load_source_chapters_from_volumes(affected_dirs)
    extras_by_volume = _load_extras_by_volume(affected_dirs)
    plan = partition_chapters_by_budget(
        chapters,
        max_chapters=max_chapters,
        target_chars=target_chars,
        start_volume=normalized_start,
        extras_by_volume=extras_by_volume,
    )
    warnings.extend(plan.warnings)

    new_volume_names = {volume.volume_number for volume in plan.volumes}
    orphan_extra_volumes = sorted(set(extras_by_volume) - new_volume_names)
    if orphan_extra_volumes:
        warnings.append(
            "检测到补充文件所在卷会在重排后消失，已停止自动重排："
            + "、".join(orphan_extra_volumes)
        )

    needed = any(volume.source_char_count > target_chars for volume in old_volumes)
    changed = needed and _chapter_assignment(old_volumes) != _chapter_assignment(plan.volumes)
    blocked = bool(future_locked or orphan_extra_volumes)
    affected_names = [volume_dir.name for volume_dir in affected_dirs]
    backup_dir: Path | None = None

    if not dry_run and needed and changed and not blocked:
        timestamp = now_iso().replace(":", "").replace("+", "_")
        backup_dir = source_root / SOURCE_REBALANCE_BACKUP_DIRNAME / timestamp
        backup_dir.mkdir(parents=True, exist_ok=False)

        for volume_dir in affected_dirs:
            target = backup_dir / volume_dir.name
            shutil.move(str(volume_dir), str(target))

        for volume in plan.volumes:
            volume_dir = source_root / volume.volume_number
            volume_dir.mkdir(parents=True, exist_ok=True)
            for extra in extras_by_volume.get(volume.volume_number, []):
                extra_path = volume_dir / extra.file_name
                extra_path.write_bytes(extra.raw_bytes or extra.text.encode("utf-8"))
            for chapter in volume.chapters:
                chapter_path = volume_dir / chapter.file_name
                chapter_path.write_text(chapter.text.rstrip() + "\n", encoding="utf-8")

    return RebalanceReport(
        source_root=source_root,
        start_volume=normalized_start,
        locked_volumes=locked,
        dry_run=dry_run,
        needed=needed,
        changed=changed,
        blocked=blocked,
        backup_dir=backup_dir,
        affected_volumes=affected_names,
        old_volumes=old_volumes,
        new_volumes=plan.volumes,
        warnings=warnings,
    )


def rebalance_summary_lines(report: RebalanceReport) -> list[str]:
    if not report.affected_volumes:
        return [f"参考源自适应分卷：从第 {report.start_volume} 卷开始没有可重排卷。"]
    status = "需要重排" if report.needed else "无需重排"
    if report.blocked:
        status = "重排被阻止"
    elif report.changed and report.dry_run:
        status = "dry-run 将重排"
    elif report.changed:
        status = "已重排"
    elif report.needed:
        status = "需要重排但无法通过移动章节改善"
    lines = [
        f"参考源自适应分卷：起始卷 {report.start_volume}，冻结卷：{', '.join(report.locked_volumes) or '无'}，{status}。",
        f"受影响旧卷：{', '.join(report.affected_volumes)}。",
    ]
    if report.backup_dir is not None:
        lines.append(f"备份目录：{report.backup_dir}")
    lines.append("旧卷字符统计：")
    lines.extend(partition_summary_lines(PartitionPlan(report.old_volumes, TARGET_VOLUME_SOURCE_CHARS, CHAPTERS_PER_VOLUME)))
    lines.append("新卷计划：")
    lines.extend(partition_summary_lines(PartitionPlan(report.new_volumes, TARGET_VOLUME_SOURCE_CHARS, CHAPTERS_PER_VOLUME)))
    for warning in report.warnings:
        lines.append(f"警告：{warning}")
    return lines


def main() -> None:
    args = parse_args()
    source_path = get_source_path(args.file_path)
    text, encoding = read_text(source_path)
    intro, chapters = split_chapters(text)
    output_root = ensure_output_root(source_path)
    intro_path = write_intro_file(intro, source_path, output_root)
    volume_count = write_chapters(chapters, source_path, output_root, emit_summary=print)

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
