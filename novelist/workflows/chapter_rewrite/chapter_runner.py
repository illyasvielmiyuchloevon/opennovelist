from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from novelist.core.agent_runtime import AgentStageResult, run_agent_stage


def _append_unique_response_ids(response_ids: list[str], new_response_ids: list[str]) -> None:
    for response_id in new_response_ids:
        value = str(response_id or "").strip()
        if value and value not in response_ids:
            response_ids.append(value)


def _chapter_agent_allowed_files(paths: dict[str, Path], phase_key: str) -> dict[str, Path]:
    if phase_key == PHASE1_OUTLINE:
        return {"chapter_outline": paths["chapter_outline"]}
    if phase_key == PHASE2_CHAPTER_TEXT:
        return {"rewritten_chapter": paths["rewritten_chapter"]}
    if phase_key == PHASE2_SUPPORT_UPDATES:
        return support_update_target_paths(paths)
    if phase_key == PHASE3_REVIEW:
        return {"chapter_review": paths["chapter_review"]}
    return {}


def _run_chapter_agent_stage(
    *,
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    previous_response_id: str | None,
    prompt_cache_key: str,
    agent_label: str,
) -> AgentStageResult:
    def report_tool(application: Any) -> None:
        if application.applied is None:
            print_progress(f"{agent_label} 工具调用未应用：{application.output}", error=True)
            return
        changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
        print_progress(f"{agent_label} 工具已应用：{application.tool_name}，变更={changed}。")

    result = run_agent_stage(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        allowed_files=allowed_files,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        on_tool_result=report_tool,
    )
    print_agent_application_summary(
        result,
        agent_label=agent_label,
        no_tool_message=f"{agent_label} 本轮未调用文档工具，直接提交阶段结果。",
    )
    print_agent_generation_submission_summary(result, agent_label=agent_label)
    return result


def _submission_content_or_file(
    submission_text: str,
    path: Path,
    *,
    error_message: str,
) -> str:
    content = submission_text.strip()
    if content:
        return content
    content = read_text_if_exists(path).strip()
    if content:
        return content
    raise llm_runtime.ModelOutputError(error_message)


def run_chapter_workflow(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
) -> None:
    project_root = Path(rewrite_manifest["project_root"])
    source_chapter = get_chapter_material(volume_material, chapter_number)
    if not str(source_chapter.get("text") or "").strip():
        fail(f"第 {chapter_number} 章参考源正文为空：{source_chapter.get('file_path') or source_chapter.get('file_name')}")
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
        full_plan = full_chapter_workflow_plan()
        if not phase_plan:
            phase_plan = full_plan
        phase_plan, artifact_rewind_reason = reconcile_chapter_phase_plan_with_artifacts(
            rewrite_manifest,
            volume_material["volume_number"],
            chapter_number,
            phase_plan,
        )
        if artifact_rewind_reason:
            print_progress(
                "检测到章节断点状态与落盘产物不一致："
                f"{artifact_rewind_reason}；本轮重跑计划已调整为 {revision_plan_label(phase_plan)}，"
                "并重新开始本章响应链。"
            )
        total_steps = len(phase_plan)
        step_map = {phase: index + 1 for index, phase in enumerate(phase_plan)}
        stage_payload = load_chapter_stage_manifest_payload(paths["chapter_stage_manifest"])
        if (
            not artifact_rewind_reason
            and phase_plan != full_plan
            and isinstance(stage_payload.get("response_ids"), list)
        ):
            response_ids = [str(response_id) for response_id in stage_payload["response_ids"] if str(response_id or "").strip()]
            previous_response_id = latest_chapter_stage_response_id(paths["chapter_stage_manifest"])
        else:
            response_ids = []
            previous_response_id = None
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
                    payload=payload,
                    user_input_char_count=len(stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)),
                    session_status_line=(
                        "会话：本阶段使用独立 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
                        "后续阶段重新读取落盘文件并重拼最新 payload。"
                    ),
                )
                phase_user_input = stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)
                outline_result = _run_chapter_agent_stage(
                    client=client,
                    model=model,
                    instructions=COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                    user_input=phase_user_input,
                    allowed_files=_chapter_agent_allowed_files(paths, PHASE1_OUTLINE),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    agent_label="章纲生成 agent",
                )
                previous_response_id = outline_result.response_id
                _append_unique_response_ids(response_ids, outline_result.response_ids)
                outline_md = _submission_content_or_file(
                    outline_result.submission.content_md,
                    paths["chapter_outline"],
                    error_message="章纲生成 agent 未通过 submit_workflow_result 返回 Markdown，也未写入章纲文件。",
                )
                chapter_outline_changed = write_artifact(paths["chapter_outline"], outline_md)
                print_call_artifact_report(
                    f"第 {step_map[PHASE1_OUTLINE]}/{total_steps} 次调用",
                    [("章纲", paths["chapter_outline"])],
                    sorted(set(agent_changed_keys(outline_result) + (["chapter_outline"] if chapter_outline_changed else []))),
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
                    payload=payload,
                    user_input_char_count=len(stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)),
                    session_status_line=(
                        "会话：本阶段使用独立 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
                        "后续阶段重新读取落盘文件并重拼最新 payload。"
                    ),
                )
                phase_user_input = stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)
                if chapter_text_revision_mode:
                    chapter_text_result = _run_chapter_agent_stage(
                        client=client,
                        model=model,
                        instructions=COMMON_CHAPTER_TEXT_REVISION_INSTRUCTIONS,
                        user_input=phase_user_input,
                        allowed_files=_chapter_agent_allowed_files(paths, PHASE2_CHAPTER_TEXT),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                        agent_label="正文修订 agent",
                    )
                    previous_response_id = chapter_text_result.response_id
                    _append_unique_response_ids(response_ids, chapter_text_result.response_ids)
                    current_chapter_text = read_text_if_exists(paths["rewritten_chapter"]).strip()
                    if not current_chapter_text:
                        raise llm_runtime.ModelOutputError("正文修订 agent 未写入章节正文。")
                    print_call_artifact_report(
                        f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用",
                        [("仿写章节正文", paths["rewritten_chapter"])],
                        agent_changed_keys(chapter_text_result),
                    )
                else:
                    chapter_text_result = _run_chapter_agent_stage(
                        client=client,
                        model=model,
                        instructions=COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                        user_input=phase_user_input,
                        allowed_files=_chapter_agent_allowed_files(paths, PHASE2_CHAPTER_TEXT),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                        agent_label="正文生成 agent",
                    )
                    previous_response_id = chapter_text_result.response_id
                    _append_unique_response_ids(response_ids, chapter_text_result.response_ids)
                    chapter_txt = _submission_content_or_file(
                        chapter_text_result.submission.chapter_txt,
                        paths["rewritten_chapter"],
                        error_message="正文生成 agent 未通过 submit_workflow_result 返回章节正文，也未写入正文文件。",
                    )
                    chapter_text_changed = write_artifact(paths["rewritten_chapter"], chapter_txt)
                    current_chapter_text = chapter_txt
                    print_call_artifact_report(
                        f"第 {step_map[PHASE2_CHAPTER_TEXT]}/{total_steps} 次调用",
                        [("仿写章节正文", paths["rewritten_chapter"])],
                        sorted(set(agent_changed_keys(chapter_text_result) + (["rewritten_chapter"] if chapter_text_changed else []))),
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
                    payload=payload,
                    user_input_char_count=len(stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)),
                    session_status_line=(
                        "会话：本阶段使用独立 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
                        "后续阶段重新读取落盘文件并重拼最新 payload。"
                    ),
                )
                phase_user_input = stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)
                support_result = _run_chapter_agent_stage(
                    client=client,
                    model=model,
                    instructions=COMMON_SUPPORT_UPDATE_INSTRUCTIONS,
                    user_input=phase_user_input,
                    allowed_files=_chapter_agent_allowed_files(paths, PHASE2_SUPPORT_UPDATES),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=chapter_session_key,
                    agent_label="状态文档更新 agent",
                )
                previous_response_id = support_result.response_id
                _append_unique_response_ids(response_ids, support_result.response_ids)
                emitted_docs = []
                changed_docs = agent_changed_keys(support_result)
                for application in support_result.applications:
                    if application.applied is None:
                        continue
                    for key in application.applied.emitted_keys:
                        if key not in emitted_docs:
                            emitted_docs.append(key)
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
                for review_cycle in range(1, MAX_CHAPTER_REVIEW_ATTEMPTS + 1):
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
                        f"{'审核' if review_cycle == 1 else '复审'}第 {chapter_number} 章全部产物"
                        f"（章审第 {review_cycle}/{MAX_CHAPTER_REVIEW_ATTEMPTS} 次）。"
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
                        payload=payload,
                        user_input_char_count=len(stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)),
                        session_status_line=(
                            "会话：本阶段使用独立 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
                            "后续阶段重新读取落盘文件并重拼最新 payload。"
                        ),
                    )
                    phase_user_input = stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)
                    review_result = _run_chapter_agent_stage(
                        client=client,
                        model=model,
                        instructions=COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
                        user_input=phase_user_input,
                        allowed_files=_chapter_agent_allowed_files(paths, PHASE3_REVIEW),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                        agent_label="章级审核 agent",
                    )
                    previous_response_id = review_result.response_id
                    _append_unique_response_ids(response_ids, review_result.response_ids)
                    chapter_review = finalize_review_payload(review_result.submission, review_kind="chapter")
                    if chapter_review.passed is None or not chapter_review.review_md.strip():
                        raise llm_runtime.ModelOutputError("章级审核 agent 未通过 submit_workflow_result 返回完整的 passed / review_md。")
                    chapter_review_changed = write_artifact(paths["chapter_review"], chapter_review.review_md)
                    print_call_artifact_report(
                        f"第 {step_map[PHASE3_REVIEW]}/{total_steps} 次调用",
                        [("章级审核文档", paths["chapter_review"])],
                        sorted(set(agent_changed_keys(review_result) + (["chapter_review"] if chapter_review_changed else []))),
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

                    if review_cycle >= MAX_CHAPTER_REVIEW_ATTEMPTS:
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
                            note="章级审核调用次数耗尽，仍未通过。",
                            attempt=attempt,
                            last_phase=PHASE3_REVIEW,
                            response_ids=response_ids,
                        )
                        fail(
                            f"第 {chapter_number} 章章级审核连续 {MAX_CHAPTER_REVIEW_ATTEMPTS} 次审核、"
                            f"原地返修 {MAX_CHAPTER_REVIEW_FIX_ATTEMPTS} 次后仍未通过。"
                        )

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
                    fix_payload = build_review_fix_payload(
                        review_kind="chapter",
                        review=chapter_review,
                        allowed_files=chapter_review_fix_target_paths(paths),
                    )
                    fix_payload["latest_work_target"] = latest_work_target(
                        "这是本次请求的最新工作目标：根据 failed_review_result 直接原地返修章级审核指出的问题。必须先调用 write/edit/patch 文档工具提交修改，然后调用 submit_workflow_result 提交返修完成摘要。",
                        required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
                    )
                    fix_user_input = stage_shared_prompt + json.dumps(fix_payload, ensure_ascii=False, indent=2)
                    fix_result = _run_chapter_agent_stage(
                        client=client,
                        model=model,
                        instructions=review_fix_instructions("chapter"),
                        user_input=fix_user_input,
                        allowed_files=chapter_review_fix_target_paths(paths),
                        previous_response_id=previous_response_id,
                        prompt_cache_key=chapter_session_key,
                        agent_label="章级审核返修 agent",
                    )
                    previous_response_id = fix_result.response_id
                    _append_unique_response_ids(response_ids, fix_result.response_ids)
                    print_call_artifact_report(
                        "章级审核原地返修调用",
                        [
                            (doc_label_for_key(item.file_key), item.path)
                            for application in fix_result.applications
                            if application.applied is not None
                            for item in application.applied.files
                        ],
                        agent_changed_keys(fix_result),
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
