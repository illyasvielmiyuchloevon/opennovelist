from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from novelist.core.agent_runtime import run_agent_stage


def write_group_stage_snapshot(
    project_root: Path,
    stage_manifest_path: Path,
    *,
    volume_number: str,
    chapter_numbers: list[str],
    status: str,
    note: str,
    attempt: int,
    response_ids: list[str] | None = None,
) -> None:
    last_response_id = next(
        (str(response_id or "").strip() for response_id in reversed(response_ids or []) if str(response_id or "").strip()),
        None,
    )
    write_markdown_data(
        stage_manifest_path,
        title=f"Group Generation Manifest {volume_number}-{five_chapter_batch_id(chapter_numbers)}",
        payload={
            "generated_at": now_iso(),
            "volume_number": volume_number,
            "batch_id": five_chapter_batch_id(chapter_numbers),
            "chapter_numbers": chapter_numbers,
            "status": status,
            "note": note,
            "attempt": attempt,
            "response_ids": response_ids or [],
            "last_response_id": last_response_id,
            "group_outline_path": str(group_outline_path(project_root, volume_number, chapter_numbers)),
        },
        summary_lines=[
            f"volume_number: {volume_number}",
            f"batch_id: {five_chapter_batch_id(chapter_numbers)}",
            f"chapters: {chapter_numbers[0]}-{chapter_numbers[-1]}",
            f"status: {status}",
            f"attempt: {attempt}",
            f"last_response_id: {last_response_id or 'none'}",
            f"note: {note}",
        ],
    )


def validate_group_generation_outputs(
    project_root: Path,
    volume_number: str,
    chapter_numbers: list[str],
) -> None:
    missing: list[str] = []
    outline_path = group_outline_path(project_root, volume_number, chapter_numbers)
    outline_content = read_text_if_exists(outline_path).strip()
    if not outline_content:
        missing.append(f"group_outline: {outline_path}")
    else:
        expected_title = f"# {chapter_numbers[0]}-{chapter_numbers[-1]} 组纲"
        if expected_title not in outline_content:
            missing.append(f"group_outline_title: {outline_path} 缺少 {expected_title}")
        for chapter_number in chapter_numbers:
            if f"## {chapter_number}" not in outline_content:
                missing.append(f"group_outline_chapter_block: {outline_path} 缺少 ## {chapter_number}")
    for chapter_number in chapter_numbers:
        chapter_path = rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]
        if not read_text_if_exists(chapter_path).strip():
            missing.append(f"{chapter_number}_rewritten_chapter: {chapter_path}")
    if missing:
        fail("组生成结束后仍缺少必要产物：\n" + "\n".join(f"- {item}" for item in missing))


def run_group_generation_workflow(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
) -> bool:
    project_root = Path(rewrite_manifest["project_root"])
    volume_number = volume_material["volume_number"]
    batch_id = five_chapter_batch_id(chapter_numbers)
    group_dir = group_injection_dir(project_root, volume_number, chapter_numbers)
    group_dir.mkdir(parents=True, exist_ok=True)
    rewrite_paths(project_root, volume_number, chapter_numbers[0])["rewritten_volume_dir"].mkdir(parents=True, exist_ok=True)

    state = get_group_generation_state(rewrite_manifest, volume_number, batch_id, chapter_numbers)
    if state.get("status") == "passed":
        validate_group_generation_outputs(project_root, volume_number, chapter_numbers)
        return True

    prompt_cache_key = build_group_generation_session_key(rewrite_manifest, volume_number, chapter_numbers)
    previous_response_id = str(state.get("last_response_id") or "").strip() or None
    stored_response_ids = state.get("response_ids")
    response_ids = [str(item) for item in stored_response_ids if str(item or "").strip()] if isinstance(stored_response_ids, list) else []
    source_volume_material = group_source_material(volume_material, chapter_numbers)
    source_bundle, source_char_count = build_five_chapter_source_bundle(source_volume_material, chapter_numbers)
    shared_prompt = build_five_chapter_generation_shared_prompt(
        manifest=rewrite_manifest,
        volume_material=source_volume_material,
        chapter_numbers=chapter_numbers,
        source_bundle=source_bundle,
        source_char_count=source_char_count,
    )

    for attempt in range(1, MAX_CHAPTER_REWRITE_ATTEMPTS + 1):
        update_group_generation_state(
            rewrite_manifest,
            volume_number,
            batch_id,
            chapter_numbers,
            status="in_progress",
            attempts=attempt,
            group_outline_path=str(group_outline_path(project_root, volume_number, chapter_numbers)),
            response_ids=response_ids,
            last_response_id=previous_response_id,
        )
        write_group_stage_snapshot(
            project_root,
            group_stage_manifest_path(project_root, volume_number, chapter_numbers),
            volume_number=volume_number,
            chapter_numbers=chapter_numbers,
            status="in_progress",
            note="开始五章组 agent 生成。",
            attempt=attempt,
            response_ids=response_ids,
        )
        catalog = read_doc_catalog(project_root, volume_number, chapter_numbers[0])
        payload, included_docs, omitted_docs = build_group_generation_payload(
            project_root=project_root,
            volume_material=source_volume_material,
            volume_number=volume_number,
            chapter_numbers=chapter_numbers,
            catalog=catalog,
        )
        print_progress(
            f"组生成第 {attempt}/{MAX_CHAPTER_REWRITE_ATTEMPTS} 次调用："
            f"生成第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]} 组纲、正文与状态文档。"
        )
        print_request_context_summary(
            request_label=f"组生成（{chapter_numbers[0]}-{chapter_numbers[-1]}）",
            volume_number=volume_number,
            chapter_number=None,
            location_label=f"第 {volume_number} 卷，第 {chapter_numbers[0]}-{chapter_numbers[-1]} 组生成。",
            source_summary_lines=group_generation_source_summary_lines(
                source_volume_material,
                chapter_numbers,
                source_char_count,
            ),
            included_docs=included_docs,
            omitted_docs=omitted_docs,
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
            shared_prefix_lines=[
                *group_generation_shared_prefix_summary_lines(
                    rewrite_manifest,
                    source_volume_material,
                    chapter_numbers,
                    source_char_count,
                ),
                *payload_prefix_doc_summary_lines(payload),
            ],
            dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
            payload=payload,
            user_input_char_count=len(shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)),
            session_status_line=(
                "会话：OpenCode 风格本地 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
                "不依赖 provider previous_response_id。"
            ),
        )

        def report_tool(application: Any) -> None:
            if application.applied is None:
                print_progress(f"组生成工具调用未应用：{application.output}", error=True)
                return
            changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
            print_progress(f"组生成工具已应用：{application.tool_name}，变更={changed}。")

        try:
            agent_result = run_agent_stage(
                client,
                model=model,
                instructions=COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                user_input=shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                allowed_files=group_generation_target_paths(project_root, volume_number, chapter_numbers),
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                on_tool_result=report_tool,
            )
            previous_response_id = agent_result.response_id
            response_ids.extend(response_id for response_id in agent_result.response_ids if response_id not in response_ids)
            validate_group_generation_outputs(project_root, volume_number, chapter_numbers)
        except Exception as error:
            update_group_generation_state(
                rewrite_manifest,
                volume_number,
                batch_id,
                chapter_numbers,
                status="failed",
                attempts=attempt,
                response_ids=response_ids,
                last_response_id=previous_response_id,
                blocking_issues=[str(error)],
            )
            write_group_stage_snapshot(
                project_root,
                group_stage_manifest_path(project_root, volume_number, chapter_numbers),
                volume_number=volume_number,
                chapter_numbers=chapter_numbers,
                status="failed",
                note=str(error),
                attempt=attempt,
                response_ids=response_ids,
            )
            if attempt >= MAX_CHAPTER_REWRITE_ATTEMPTS:
                raise
            print_progress(f"组生成失败，将重试：{error}", error=True)
            continue

        for chapter_number in chapter_numbers:
            update_chapter_state(
                rewrite_manifest,
                volume_number,
                chapter_number,
                status="passed",
                attempts=attempt,
                last_stage="group_generation",
                pending_phases=[],
                blocking_issues=[],
                rewrite_targets=[],
            )
        update_group_generation_state(
            rewrite_manifest,
            volume_number,
            batch_id,
            chapter_numbers,
            status="passed",
            attempts=attempt,
            group_outline_path=str(group_outline_path(project_root, volume_number, chapter_numbers)),
            response_ids=response_ids,
            last_response_id=previous_response_id,
            blocking_issues=[],
        )
        write_group_stage_snapshot(
            project_root,
            group_stage_manifest_path(project_root, volume_number, chapter_numbers),
            volume_number=volume_number,
            chapter_numbers=chapter_numbers,
            status="passed",
            note="五章组生成已完成。",
            attempt=attempt,
            response_ids=response_ids,
        )
        print_progress(f"五章组生成已完成：{volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]}。")
        return True

    return False


__all__ = [
    "write_group_stage_snapshot",
    "validate_group_generation_outputs",
    "run_group_generation_workflow",
]
