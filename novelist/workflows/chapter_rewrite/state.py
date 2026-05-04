from __future__ import annotations

from ._shared import *  # noqa: F401,F403


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

def reconcile_chapter_phase_plan_with_artifacts(
    manifest: dict[str, Any],
    volume_number: str,
    chapter_number: str,
    phase_plan: list[str],
) -> tuple[list[str], str | None]:
    normalized = normalize_phase_plan(phase_plan)
    if not normalized:
        return full_chapter_workflow_plan(), None

    project_root = Path(manifest["project_root"])
    paths = rewrite_paths(project_root, volume_number, chapter_number)
    needs_outline = any(
        phase in normalized
        for phase in (PHASE2_CHAPTER_TEXT, PHASE2_SUPPORT_UPDATES, PHASE3_REVIEW)
    )
    if PHASE1_OUTLINE not in normalized and needs_outline and not read_text_if_exists(paths["chapter_outline"]).strip():
        return (
            full_chapter_workflow_plan(),
            f"断点计划从 {normalized[0]} 继续，但缺少章纲文件 {paths['chapter_outline'].name}",
        )

    needs_chapter_text = any(phase in normalized for phase in (PHASE2_SUPPORT_UPDATES, PHASE3_REVIEW))
    if (
        PHASE2_CHAPTER_TEXT not in normalized
        and needs_chapter_text
        and not read_text_if_exists(paths["rewritten_chapter"]).strip()
    ):
        repaired = merge_phase_plans([PHASE2_CHAPTER_TEXT], normalized)
        return (
            repaired,
            f"断点计划从 {normalized[0]} 继续，但缺少章节正文文件 {paths['rewritten_chapter'].name}",
        )

    return normalized, None

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
            "response_ids": [],
            "last_response_id": None,
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
            "response_ids": [],
            "last_response_id": None,
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

def mark_five_chapter_group_pending_for_chapter(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
) -> None:
    group = find_group_for_chapter({**volume_material, "project_root": manifest["project_root"]}, chapter_number)
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

def chapter_artifacts_complete(
    manifest: dict[str, Any],
    volume_number: str,
    chapter_number: str,
) -> bool:
    project_root = Path(manifest["project_root"])
    paths = rewrite_paths(project_root, volume_number, chapter_number)
    required_paths = [
        paths["chapter_outline"],
        paths["rewritten_chapter"],
        paths["chapter_review"],
    ]
    return all(read_text_if_exists(path).strip() for path in required_paths)

def chapter_is_passed_and_complete(
    manifest: dict[str, Any],
    volume_number: str,
    chapter_number: str,
) -> bool:
    state = get_chapter_state(manifest, volume_number, chapter_number)
    return state.get("status") == "passed" and chapter_artifacts_complete(
        manifest,
        volume_number,
        chapter_number,
    )

def all_group_chapters_passed(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
) -> bool:
    for chapter_number in chapter_numbers:
        if not chapter_is_passed_and_complete(
            manifest,
            volume_material["volume_number"],
            chapter_number,
        ):
            return False
    return True

def next_pending_group(volume_material: dict[str, Any], manifest: dict[str, Any]) -> list[str] | None:
    groups = build_five_chapter_groups({**volume_material, "project_root": manifest["project_root"]})
    for group in groups:
        batch_id = five_chapter_batch_id(group)
        state = get_five_chapter_review_state(manifest, volume_material["volume_number"], batch_id, group)
        if not all_group_chapters_passed(manifest, volume_material, group) or state.get("status") != "passed":
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
    groups = build_five_chapter_groups({**volume_material, "project_root": manifest["project_root"]})
    found_current = False
    for group in groups:
        if not found_current:
            if group == current_group:
                found_current = True
            continue
        if not all_group_chapters_passed(manifest, volume_material, group) or not group_review_passed(
            manifest,
            volume_material["volume_number"],
            group,
        ):
            return group
    return None

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
        if chapter_number in available and (allowed is None or chapter_number in allowed) and not chapter_is_passed_and_complete(
            manifest,
            volume_material["volume_number"],
            chapter_number,
        ):
            return chapter_number

    five_review_states = manifest.get("five_chapter_review_states", {}).get(volume_material["volume_number"], {})
    groups = build_five_chapter_groups({**volume_material, "project_root": manifest["project_root"]})
    for group in groups:
        batch_id = five_chapter_batch_id(group)
        state = five_review_states.get(batch_id, {})
        for chapter_number in [item.zfill(4) for item in state.get("chapters_to_revise", []) if item]:
            if chapter_number in available and (allowed is None or chapter_number in allowed) and not chapter_is_passed_and_complete(
                manifest,
                volume_material["volume_number"],
                chapter_number,
            ):
                return chapter_number

    for chapter in volume_material["chapters"]:
        chapter_number = chapter["chapter_number"]
        if allowed is not None and chapter_number not in allowed:
            continue
        if not chapter_is_passed_and_complete(
            manifest,
            volume_material["volume_number"],
            chapter_number,
        ):
            return chapter_number
    return None

def all_chapters_passed(manifest: dict[str, Any], volume_material: dict[str, Any]) -> bool:
    for chapter in volume_material["chapters"]:
        if not chapter_is_passed_and_complete(
            manifest,
            volume_material["volume_number"],
            chapter["chapter_number"],
        ):
            return False
    return True

__all__ = [
    'load_rewrite_manifest',
    'ensure_rewrite_dirs',
    'save_rewrite_manifest',
    'init_or_load_rewrite_manifest',
    'get_chapter_state',
    'update_chapter_state',
    'full_chapter_workflow_plan',
    'normalize_phase_plan',
    'revision_plan_label',
    'normalize_rewrite_target_token',
    'phase_plan_from_single_rewrite_target',
    'merge_phase_plans',
    'build_chapter_revision_plan',
    'build_multi_chapter_revision_plan',
    'rewrite_targets_for_chapter',
    'chapter_pending_phase_plan',
    'reconcile_chapter_phase_plan_with_artifacts',
    'get_volume_review_state',
    'update_volume_review_state',
    'get_five_chapter_review_state',
    'update_five_chapter_review_state',
    'mark_five_chapter_group_pending_for_chapter',
    'chapter_artifacts_complete',
    'chapter_is_passed_and_complete',
    'all_group_chapters_passed',
    'next_pending_group',
    'current_due_group_review',
    'group_review_passed',
    'next_group_after',
    'select_next_chapter',
    'all_chapters_passed',
]
