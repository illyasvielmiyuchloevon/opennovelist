from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def run_chapter_workflow(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
) -> None:
    project_root = Path(rewrite_manifest["project_root"])
    paths = rewrite_paths(project_root, volume_material["volume_number"], chapter_number)
    paths["chapter_dir"].mkdir(parents=True, exist_ok=True)
    paths["rewritten_volume_dir"].mkdir(parents=True, exist_ok=True)

    source_bundle, source_char_count = build_chapter_source_bundle(volume_material, chapter_number)
    chapter_session_key = build_chapter_session_key(
        rewrite_manifest,
        volume_material["volume_number"],
        chapter_number,
    )

    for attempt in range(1, MAX_CHAPTER_REWRITE_ATTEMPTS + 1):
        phase_plan = chapter_pending_phase_plan(
            rewrite_manifest,
            volume_material["volume_number"],
            chapter_number,
        )
        if not phase_plan:
            phase_plan = full_chapter_workflow_plan()
        total_steps = len(phase_plan)
        step_map = {phase: index + 1 for index, phase in enumerate(phase_plan)}
        response_ids: list[str] = []
        previous_response_id: str | None = None
        stage_shared_prompt = build_chapter_shared_prompt(
            manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_number=chapter_number,
            source_bundle=source_bundle,
            source_char_count=source_char_count,
        )
        update_chapter_state(
            rewrite_manifest,
            volume_material["volume_number"],
            chapter_number,
            status="in_progress",
            attempts=attempt,
            last_stage=phase_plan[0],
            pending_phases=phase_plan,
        )
        write_chapter_stage_snapshot(
            paths["chapter_stage_manifest"],
            volume_number=volume_material["volume_number"],
            chapter_number=chapter_number,
            status="in_progress",
            note=f"开始当前章节工作流。本轮重跑计划：{revision_plan_label(phase_plan)}。",
            attempt=attempt,
            last_phase=phase_plan[0],
            response_ids=response_ids,
        )

        try:
            current_chapter_text = read_text_if_exists(paths["rewritten_chapter"]).strip()

            if PHASE1_OUTLINE in phase_plan:
                catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                payload, included_docs, omitted_docs = build_phase_request_payload(
                    phase_key=PHASE1_OUTLINE,
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    catalog=catalog,
                )
                print_progress(f"第 {step_map[PHASE1_OUTLINE]}/{total_steps} 次调用：生成第 {chapter_number} 章章纲。")
                print_request_context_summary(
                    request_label="第一阶段：章纲生成",
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                    included_docs=included_docs,
                    omitted_docs=omitted_docs,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    shared_prefix_lines=[
                        *chapter_shared_prefix_summary_lines(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                            source_char_count,
                        ),
                        *payload_prefix_doc_summary_lines(payload),
                    ],
                    dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                )
                outline_md, previous_response_id, outline_result = call_markdown_tool_response(
                    client,
                    model,
                    COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                    stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                )
                response_ids.append(str(outline_result.response_id or ""))
                chapter_outline_changed = write_artifact(paths["chapter_outline"], outline_md)
                print_call_artifact_report(
                    f"第 {step_map[PHASE1_OUTLINE]}/{total_steps} 次调用",
                    [("章纲", paths["chapter_outline"])],
                    ["chapter_outline"] if chapter_outline_changed else [],
                )

                remaining = [phase for phase in phase_plan if phase != PHASE1_OUTLINE]
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    last_stage=remaining[0] if remaining else PHASE1_OUTLINE,
                    pending_phases=remaining,
                )
                if remaining:
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="in_progress",
                        note=f"章纲已完成，准备进入下一阶段：{remaining[0]}。",
                        attempt=attempt,
                        last_phase=remaining[0],
                        response_ids=response_ids,
                    )

            if PHASE2_CHAPTER_TEXT not in phase_plan and not read_text_if_exists(paths["chapter_outline"]).strip():
                fail(f"第 {chapter_number} 章缺少章纲，无法跳过章纲阶段直接继续后续流程。")

            if PHASE2_CHAPTER_TEXT in phase_plan:
                chapter_text_revision_mode = bool(current_chapter_text.strip())
                catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                payload, included_docs, omitted_docs = build_phase_request_payload(
                    phase_key=PHASE2_CHAPTER_TEXT,
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    catalog=catalog,
                    chapter_text=current_chapter_text,
                    chapter_text_revision=chapter_text_revision_mode,
                )
                print_progress(
                    f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用："
                    + (f"修订第 {chapter_number} 章现有正文。" if chapter_text_revision_mode else f"生成第 {chapter_number} 章完整正文。")
                )
                print_request_context_summary(
                    request_label="第二阶段-第一部分：正文修订" if chapter_text_revision_mode else "第二阶段-第一部分：正文生成",
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                    included_docs=included_docs,
                    omitted_docs=omitted_docs,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    shared_prefix_lines=[
                        *chapter_shared_prefix_summary_lines(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                            source_char_count,
                        ),
                        *payload_prefix_doc_summary_lines(payload),
                    ],
                    dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                )
                if chapter_text_revision_mode:
                    chapter_text_update, previous_response_id, chapter_text_result = call_chapter_text_revision_response(
                        client,
                        model,
                        COMMON_CHAPTER_TEXT_REVISION_INSTRUCTIONS,
                        stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                    )
                    response_ids.append(str(chapter_text_result.response_id or ""))
                    applied_chapter_text_update, previous_response_id, repair_response_ids = apply_document_operation_with_repair(
                        client=client,
                        model=model,
                        instructions=COMMON_CHAPTER_TEXT_REVISION_INSTRUCTIONS,
                        shared_prompt=stage_shared_prompt,
                        operation=chapter_text_update,
                        allowed_files={"rewritten_chapter": paths["rewritten_chapter"]},
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                        phase_key=PHASE2_CHAPTER_TEXT,
                        repair_role="章节仿写修订作者",
                        repair_task="修正上一次正文修订工具调用中无法定位的 old_text 或 match_text，并重新提交可应用的局部编辑。",
                        debug_path=paths["chapter_response_debug"],
                    )
                    response_ids.extend(repair_response_ids)
                    current_chapter_text = read_text_if_exists(paths["rewritten_chapter"]).strip()
                    print_call_artifact_report(
                        f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用",
                        [("仿写章节正文", paths["rewritten_chapter"])],
                        applied_chapter_text_update.changed_keys,
                    )
                else:
                    chapter_txt, previous_response_id, chapter_text_result = call_chapter_text_tool_response(
                        client,
                        model,
                        COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                        stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                    )
                    response_ids.append(str(chapter_text_result.response_id or ""))
                    chapter_text_changed = write_artifact(paths["rewritten_chapter"], chapter_txt)
                    current_chapter_text = chapter_txt
                    print_call_artifact_report(
                        f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用",
                        [("仿写章节正文", paths["rewritten_chapter"])],
                        ["rewritten_chapter"] if chapter_text_changed else [],
                    )

                remaining = [phase for phase in phase_plan if phase not in {PHASE1_OUTLINE, PHASE2_CHAPTER_TEXT}]
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    last_stage=remaining[0] if remaining else PHASE2_CHAPTER_TEXT,
                    pending_phases=remaining,
                )
                if remaining:
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="in_progress",
                        note=f"正文已完成，准备进入下一阶段：{remaining[0]}。",
                        attempt=attempt,
                        last_phase=remaining[0],
                        response_ids=response_ids,
                    )

            if PHASE2_SUPPORT_UPDATES in phase_plan:
                current_chapter_text = current_chapter_text or read_text_if_exists(paths["rewritten_chapter"]).strip()
                if not current_chapter_text:
                    fail(f"第 {chapter_number} 章缺少正文，无法执行配套状态文档更新。")
                catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                payload, included_docs, omitted_docs = build_phase_request_payload(
                    phase_key=PHASE2_SUPPORT_UPDATES,
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    catalog=catalog,
                    chapter_text=current_chapter_text,
                )
                print_progress(f"第 {step_map[PHASE2_SUPPORT_UPDATES]}/{total_steps} 次调用：更新第 {chapter_number} 章配套状态文档。")
                print_request_context_summary(
                    request_label="第二阶段-第二部分：状态文档更新",
                    volume_number=volume_material["volume_number"],
                    chapter_number=chapter_number,
                    source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                    included_docs=included_docs,
                    omitted_docs=omitted_docs,
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    shared_prefix_lines=[
                        *chapter_shared_prefix_summary_lines(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                            source_char_count,
                        ),
                        *payload_prefix_doc_summary_lines(payload),
                    ],
                    dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                )
                support_updates, previous_response_id, support_result = call_support_updates_response(
                    client,
                    model,
                    COMMON_SUPPORT_UPDATE_INSTRUCTIONS,
                    stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                )
                response_ids.append(str(support_result.response_id or ""))
                applied_updates, previous_response_id, repair_response_ids = apply_document_operation_with_repair(
                    client=client,
                    model=model,
                    instructions=COMMON_SUPPORT_UPDATE_INSTRUCTIONS,
                    shared_prompt=stage_shared_prompt,
                    operation=support_updates,
                    allowed_files=support_update_target_paths(paths),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    phase_key=PHASE2_SUPPORT_UPDATES,
                    repair_role="连续性编辑与状态维护编辑",
                    repair_task="修正上一次配套文档更新工具调用中无法定位的 old_text 或 match_text，并重新提交可应用的局部编辑。",
                    debug_path=paths["chapter_response_debug"],
                )
                response_ids.extend(repair_response_ids)
                emitted_docs = applied_updates.emitted_keys
                changed_docs = applied_updates.changed_keys
                print_call_artifact_report(
                    f"第 {step_map[PHASE2_SUPPORT_UPDATES]}/{total_steps} 次调用",
                    [(doc_label_for_key(key), paths[key]) for key in emitted_docs],
                    changed_docs,
                )
                print_progress(
                    "本轮配套文档更新结果："
                    + (", ".join(doc_label_for_key(key) for key in changed_docs) if changed_docs else "模型判定当前无需要落盘的文档更新。")
                )

                remaining = [phase for phase in phase_plan if phase not in {PHASE1_OUTLINE, PHASE2_CHAPTER_TEXT, PHASE2_SUPPORT_UPDATES}]
                update_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                    last_stage=remaining[0] if remaining else PHASE2_SUPPORT_UPDATES,
                    pending_phases=remaining,
                )
                if remaining:
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="in_progress",
                        note=f"配套状态文档已完成，准备进入下一阶段：{remaining[0]}。",
                        attempt=attempt,
                        last_phase=remaining[0],
                        response_ids=response_ids,
                    )

            if PHASE3_REVIEW in phase_plan:
                for review_cycle in range(1, MAX_REVIEW_FIX_ATTEMPTS + 2):
                    current_chapter_text = read_text_if_exists(paths["rewritten_chapter"]).strip()
                    if not current_chapter_text:
                        fail(f"第 {chapter_number} 章缺少正文，无法执行章级审核。")
                    catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_number)
                    payload, included_docs, omitted_docs = build_phase_request_payload(
                        phase_key=PHASE3_REVIEW,
                        project_root=project_root,
                        volume_material=volume_material,
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        catalog=catalog,
                        chapter_text=current_chapter_text,
                    )
                    review_label = "第三阶段：章级审核" if review_cycle == 1 else "第三阶段：章级复审"
                    print_progress(
                        f"第 {step_map[PHASE3_REVIEW]}/{total_steps} 次调用："
                        f"{'审核' if review_cycle == 1 else '复审'}第 {chapter_number} 章全部产物。"
                    )
                    print_request_context_summary(
                        request_label=review_label,
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        source_summary_lines=chapter_source_summary_lines(volume_material, chapter_number, source_char_count),
                        included_docs=included_docs,
                        omitted_docs=omitted_docs,
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                        shared_prefix_lines=[
                            *chapter_shared_prefix_summary_lines(
                                rewrite_manifest,
                                volume_material,
                                chapter_number,
                                source_char_count,
                            ),
                            *payload_prefix_doc_summary_lines(payload),
                        ],
                        dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                    )
                    chapter_review, previous_response_id, review_result = call_chapter_review_response(
                        client,
                        model,
                        COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                        stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                    )
                    response_ids.append(str(review_result.response_id or ""))
                    chapter_review_changed = write_artifact(paths["chapter_review"], chapter_review.review_md)
                    print_call_artifact_report(
                        f"第 {step_map[PHASE3_REVIEW]}/{total_steps} 次调用",
                        [("章级审核文档", paths["chapter_review"])],
                        ["chapter_review"] if chapter_review_changed else [],
                    )

                    if chapter_review.passed:
                        update_chapter_state(
                            rewrite_manifest,
                            volume_material["volume_number"],
                            chapter_number,
                            status="passed",
                            attempts=attempt,
                            last_stage=PHASE3_REVIEW,
                            blocking_issues=[],
                            pending_phases=[],
                            rewrite_targets=[],
                            revision_origin=None,
                        )
                        write_chapter_stage_snapshot(
                            paths["chapter_stage_manifest"],
                            volume_number=volume_material["volume_number"],
                            chapter_number=chapter_number,
                            status="passed",
                            note="当前章节已通过章级审核。",
                            attempt=attempt,
                            last_phase=PHASE3_REVIEW,
                            response_ids=response_ids,
                        )
                        mark_five_chapter_group_pending_for_chapter(
                            rewrite_manifest,
                            volume_material,
                            chapter_number,
                        )
                        print_progress(f"第 {chapter_number} 章已通过章级审核。")
                        return

                    if review_cycle > MAX_REVIEW_FIX_ATTEMPTS:
                        update_chapter_state(
                            rewrite_manifest,
                            volume_material["volume_number"],
                            chapter_number,
                            status="failed",
                            attempts=attempt,
                            last_stage=PHASE3_REVIEW,
                            blocking_issues=chapter_review.blocking_issues,
                            pending_phases=[],
                            rewrite_targets=chapter_review.rewrite_targets,
                            revision_origin="chapter_review",
                        )
                        write_chapter_stage_snapshot(
                            paths["chapter_stage_manifest"],
                            volume_number=volume_material["volume_number"],
                            chapter_number=chapter_number,
                            status="failed",
                            note="章级审核原地返修次数耗尽，仍未通过。",
                            attempt=attempt,
                            last_phase=PHASE3_REVIEW,
                            response_ids=response_ids,
                        )
                        fail(f"第 {chapter_number} 章章级审核原地返修 {MAX_REVIEW_FIX_ATTEMPTS} 次后仍未通过。")

                    update_chapter_state(
                        rewrite_manifest,
                        volume_material["volume_number"],
                        chapter_number,
                        status="in_review_fix",
                        attempts=attempt,
                        last_stage=PHASE3_REVIEW,
                        blocking_issues=chapter_review.blocking_issues,
                        pending_phases=[],
                        rewrite_targets=chapter_review.rewrite_targets,
                        revision_origin="chapter_review",
                    )
                    write_chapter_stage_snapshot(
                        paths["chapter_stage_manifest"],
                        volume_number=volume_material["volume_number"],
                        chapter_number=chapter_number,
                        status="in_review_fix",
                        note="章级审核未通过，正在当前审核阶段直接修复目标文件。",
                        attempt=attempt,
                        last_phase=PHASE3_REVIEW,
                        response_ids=response_ids,
                    )
                    print_progress(
                        f"第 {chapter_number} 章章级审核未通过，将在审核阶段直接修复。"
                        f" 本轮问题：{'; '.join(chapter_review.blocking_issues) or '见审核文档'}。"
                    )
                    applied_fix, previous_response_id, fix_response_ids = apply_review_fix_with_repair(
                        client=client,
                        model=model,
                        review_kind="chapter",
                        shared_prompt=stage_shared_prompt,
                        review=chapter_review,
                        allowed_files=chapter_review_fix_target_paths(paths),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                        debug_path=paths["chapter_response_debug"],
                    )
                    response_ids.extend(fix_response_ids)
                    print_call_artifact_report(
                        "章级审核原地返修调用",
                        [(doc_label_for_key(item.file_key), item.path) for item in applied_fix.files],
                        applied_fix.changed_keys,
                    )
        except Exception as error:
            if isinstance(error, llm_runtime.ModelOutputError):
                write_response_debug_snapshot(
                    paths["chapter_response_debug"],
                    error_message=str(error),
                    preview=error.preview,
                    raw_body_text=getattr(error, "raw_body_text", ""),
                )
            update_chapter_state(
                rewrite_manifest,
                volume_material["volume_number"],
                chapter_number,
                status="failed",
                attempts=attempt,
            )
            write_chapter_stage_snapshot(
                paths["chapter_stage_manifest"],
                volume_number=volume_material["volume_number"],
                chapter_number=chapter_number,
                status="failed",
                note=str(error),
                attempt=attempt,
                last_phase=get_chapter_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    chapter_number,
                ).get("last_stage"),
                response_ids=response_ids,
            )
            raise

    fail(f"第 {volume_material['volume_number']} 卷第 {chapter_number} 章连续 {MAX_CHAPTER_REWRITE_ATTEMPTS} 次仍未通过章级审核。")

__all__ = [
    'run_chapter_workflow',
]
