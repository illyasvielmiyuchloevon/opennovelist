from __future__ import annotations

from pathlib import Path
from typing import Any

from .files import extract_json_payload, now_iso, read_text_if_exists, write_markdown_data
from .ui import fail


GROUP_ROOT_DIRNAME = "group_injection"
GROUP_DIR_SUFFIX = "_group_injection"
GROUP_STAGE_MANIFEST_NAME = "00_group_stage_manifest.md"
GROUP_OUTLINE_PLAN_MANIFEST_NAME = "00_group_outline_plan.md"


def group_batch_id(chapter_numbers: list[str]) -> str:
    if not chapter_numbers:
        fail("章节组不能为空。")
    return f"{chapter_numbers[0]}_{chapter_numbers[-1]}"


def group_injection_root(project_root: Path, volume_number: str) -> Path:
    return project_root / GROUP_ROOT_DIRNAME / f"{volume_number}{GROUP_DIR_SUFFIX}"


def group_injection_dir(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    batch_id = group_batch_id(chapter_numbers)
    return group_injection_root(project_root, volume_number) / f"{batch_id}{GROUP_DIR_SUFFIX}"


def group_outline_path(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    group_dir = group_injection_dir(project_root, volume_number, chapter_numbers)
    return group_dir / f"{group_batch_id(chapter_numbers)}_group_outline.md"


def group_review_path(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    group_dir = group_injection_dir(project_root, volume_number, chapter_numbers)
    return group_dir / f"{group_batch_id(chapter_numbers)}_group_review.md"


def group_stage_manifest_path(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    return group_injection_dir(project_root, volume_number, chapter_numbers) / GROUP_STAGE_MANIFEST_NAME


def group_response_debug_path(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    group_dir = group_injection_dir(project_root, volume_number, chapter_numbers)
    return group_dir / f"{group_batch_id(chapter_numbers)}_group_generation_debug.md"


def group_outline_plan_path(project_root: Path, volume_number: str) -> Path:
    return group_injection_root(project_root, volume_number) / GROUP_OUTLINE_PLAN_MANIFEST_NAME


def group_outline_plan_review_path(project_root: Path, volume_number: str) -> Path:
    return group_injection_root(project_root, volume_number) / f"{volume_number}_group_outline_review.md"


def _chapter_numbers_for_count(start: int, chapter_count: int) -> list[str]:
    if chapter_count <= 0:
        fail("组纲计划中的 chapter_count 必须大于 0。")
    return [f"{number:04d}" for number in range(start, start + chapter_count)]


def normalize_group_outline_groups(
    project_root: Path,
    volume_number: str,
    raw_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    next_chapter = 1
    for index, raw in enumerate(raw_groups, start=1):
        if not isinstance(raw, dict):
            fail("组纲计划中的 groups 必须是对象列表。")
        chapter_count = int(raw.get("chapter_count") or len(raw.get("chapter_numbers") or []))
        chapter_numbers = _chapter_numbers_for_count(next_chapter, chapter_count)
        next_chapter += chapter_count
        group_id = group_batch_id(chapter_numbers)
        outline_path = group_outline_path(project_root, volume_number, chapter_numbers)
        groups.append(
            {
                "index": index,
                "group_id": group_id,
                "chapter_numbers": chapter_numbers,
                "chapter_start": chapter_numbers[0],
                "chapter_end": chapter_numbers[-1],
                "chapter_count": len(chapter_numbers),
                "source_chapter_range": str(raw.get("source_chapter_range") or "").strip(),
                "group_title": str(raw.get("group_title") or raw.get("title") or f"{group_id} 组纲").strip(),
                "guidance": str(raw.get("guidance") or raw.get("outline_guidance") or "").strip(),
                "group_outline_path": str(outline_path),
            }
        )
    if not groups:
        fail("组纲计划必须至少包含一个章节组。")
    return groups


def write_group_outline_plan_manifest(
    project_root: Path,
    volume_number: str,
    *,
    status: str,
    groups: list[dict[str, Any]],
    source_volume_dir: str = "",
    note: str = "",
    response_ids: list[str] | None = None,
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_groups = normalize_group_outline_groups(project_root, volume_number, groups)
    plan_path = group_outline_plan_path(project_root, volume_number)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now_iso(),
        "status": status,
        "processed_volume": volume_number,
        "source_volume_dir": source_volume_dir,
        "note": note,
        "total_groups": len(normalized_groups),
        "total_chapters": sum(group["chapter_count"] for group in normalized_groups),
        "groups": normalized_groups,
        "response_ids": response_ids or [],
        "review": review or {},
    }
    write_markdown_data(
        plan_path,
        title=f"Group Outline Plan {volume_number}",
        payload=payload,
        summary_lines=[
            f"status: {status}",
            f"processed_volume: {volume_number}",
            f"total_groups: {payload['total_groups']}",
            f"total_chapters: {payload['total_chapters']}",
            f"review_status: {(review or {}).get('status', 'pending')}",
            f"note: {note}",
        ],
    )
    return payload


def load_group_outline_plan(project_root: Path, volume_number: str, *, require_passed: bool = False) -> dict[str, Any]:
    path = group_outline_plan_path(project_root, volume_number)
    if not path.exists():
        fail(
            f"缺少第 {volume_number} 卷组纲计划：{path}。"
            "请先补跑 novel_adaptation 的整卷组纲生成与组纲审核阶段。"
        )
    payload = extract_json_payload(path.read_text(encoding="utf-8"))
    status = str(payload.get("status") or "").strip()
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    review_status = str(review.get("status") or "").strip()
    if require_passed and status != "passed":
        fail(
            f"第 {volume_number} 卷组纲计划尚未审核通过：{path}（status={status or 'unknown'}，"
            f"review_status={review_status or 'unknown'}）。请先补跑整卷组纲审核。"
        )
    return payload


def group_plan_groups(project_root: Path, volume_number: str, *, require_passed: bool = True) -> list[list[str]]:
    payload = load_group_outline_plan(project_root, volume_number, require_passed=require_passed)
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        fail(f"第 {volume_number} 卷组纲计划没有可用章节组。")
    result: list[list[str]] = []
    for group in groups:
        if not isinstance(group, dict):
            fail(f"第 {volume_number} 卷组纲计划包含无效章节组。")
        chapter_numbers = [str(item).zfill(4) for item in group.get("chapter_numbers", []) if str(item).strip()]
        if not chapter_numbers:
            fail(f"第 {volume_number} 卷组纲计划中的章节组缺少 chapter_numbers。")
        result.append(chapter_numbers)
    return result


def group_outline_docs_from_plan(project_root: Path, volume_number: str, *, require_passed: bool = True) -> list[dict[str, Any]]:
    payload = load_group_outline_plan(project_root, volume_number, require_passed=require_passed)
    docs: list[dict[str, Any]] = []
    for group in payload.get("groups", []):
        if not isinstance(group, dict):
            continue
        chapter_numbers = [str(item).zfill(4) for item in group.get("chapter_numbers", []) if str(item).strip()]
        if not chapter_numbers:
            continue
        path = Path(str(group.get("group_outline_path") or group_outline_path(project_root, volume_number, chapter_numbers)))
        docs.append(
            {
                "group_id": str(group.get("group_id") or group_batch_id(chapter_numbers)),
                "label": f"组纲（{chapter_numbers[0]}-{chapter_numbers[-1]}）",
                "file_name": path.name,
                "file_path": str(path),
                "chapter_numbers": chapter_numbers,
                "source_chapter_range": str(group.get("source_chapter_range") or ""),
                "group_title": str(group.get("group_title") or ""),
                "guidance": str(group.get("guidance") or ""),
                "content": read_text_if_exists(path).strip(),
            }
        )
    return docs


def validate_group_outline_files(project_root: Path, volume_number: str, *, require_passed: bool = False) -> None:
    docs = group_outline_docs_from_plan(project_root, volume_number, require_passed=require_passed)
    missing: list[str] = []
    for doc in docs:
        content = str(doc.get("content") or "").strip()
        chapter_numbers = list(doc.get("chapter_numbers") or [])
        path = str(doc.get("file_path") or "")
        if not content:
            missing.append(f"group_outline: {path}")
            continue
        expected_title = f"# {chapter_numbers[0]}-{chapter_numbers[-1]} 组纲"
        if expected_title not in content:
            missing.append(f"group_outline_title: {path} 缺少 {expected_title}")
        for chapter_number in chapter_numbers:
            if f"## {chapter_number}" not in content:
                missing.append(f"group_outline_chapter_block: {path} 缺少 ## {chapter_number}")
    if missing:
        fail("组纲计划对应文件不完整：\n" + "\n".join(f"- {item}" for item in missing))


__all__ = [
    "GROUP_ROOT_DIRNAME",
    "GROUP_DIR_SUFFIX",
    "GROUP_STAGE_MANIFEST_NAME",
    "GROUP_OUTLINE_PLAN_MANIFEST_NAME",
    "group_batch_id",
    "group_injection_root",
    "group_injection_dir",
    "group_outline_path",
    "group_review_path",
    "group_stage_manifest_path",
    "group_response_debug_path",
    "group_outline_plan_path",
    "group_outline_plan_review_path",
    "normalize_group_outline_groups",
    "write_group_outline_plan_manifest",
    "load_group_outline_plan",
    "group_plan_groups",
    "group_outline_docs_from_plan",
    "validate_group_outline_files",
]
