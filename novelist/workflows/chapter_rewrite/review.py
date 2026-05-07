from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from novelist.core.agent_runtime import run_agent_stage


def review_fix_instructions(review_kind: str) -> str:
    return COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS

def review_fix_phase_key(review_kind: str) -> str:
    if review_kind == "group":
        return "five_chapter_review_fix"
    if review_kind == "volume":
        return "volume_review_fix"
    return "chapter_review_fix"

def review_fix_role(review_kind: str) -> str:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    return f"{label}原地返修编辑"

def review_has_fix_target(review_kind: str, review: WorkflowSubmissionPayload) -> bool:
    if review_kind == "chapter":
        return bool(review.rewrite_targets)
    return bool(review.chapters_to_revise or review.rewrite_targets)

def chapter_review_fix_target_paths(paths: dict[str, Path]) -> dict[str, Path | document_ops.DocumentTarget]:
    return {
        "rewritten_chapter": document_ops.protected_rewritten_chapter_target(paths["rewritten_chapter"]),
        "chapter_outline": paths["chapter_outline"],
        "chapter_review": paths["chapter_review"],
        **support_update_target_paths(paths),
    }

def multi_chapter_review_fix_target_paths(
    project_root: Path,
    volume_number: str,
    chapter_numbers: list[str],
    *,
    group_review_path: Path | None = None,
    include_volume_docs: bool = False,
) -> dict[str, Path | document_ops.DocumentTarget]:
    if not chapter_numbers:
        return {}
    paths = rewrite_paths(project_root, volume_number, chapter_numbers[0])
    targets: dict[str, Path | document_ops.DocumentTarget] = dict(support_update_target_paths(paths))
    if include_volume_docs:
        volume_paths = rewrite_paths(project_root, volume_number)
        targets["volume_outline"] = volume_paths["volume_outline"]
        targets["volume_review"] = volume_paths["volume_review"]
        volume_material = {
            "volume_number": volume_number,
            "chapters": [{"chapter_number": chapter_number} for chapter_number in chapter_numbers],
            "project_root": str(project_root),
        }
        for current_group in build_five_chapter_groups(volume_material):
            batch_id = five_chapter_batch_id(current_group)
            targets[f"{batch_id}_group_review"] = five_chapter_review_path(project_root, volume_number, current_group)
    if group_review_path is not None:
        targets["group_review"] = group_review_path
    for chapter_number in chapter_numbers:
        chapter_paths = rewrite_paths(project_root, volume_number, chapter_number)
        targets[f"{chapter_number}_rewritten_chapter"] = document_ops.protected_rewritten_chapter_target(
            chapter_paths["rewritten_chapter"]
        )
    return targets

def build_review_fix_payload(
    *,
    review_kind: str,
    review: WorkflowSubmissionPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    original_review_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    payload: dict[str, Any] = {
        "document_request": {
            "phase": review_fix_phase_key(review_kind),
            "role": review_fix_role(review_kind),
            "task": f"根据刚才未通过的{label}结果，直接修复允许范围内的目标文件；不要重新生成整章工作流。",
        },
    }
    if original_review_payload is not None:
        payload["original_review_request_context"] = {
            "source": "上一轮审核使用的 Dynamic Request。返修必须继续沿用这些审核上下文、注入文档和已生成正文，不要自行切换审查依据。",
            "payload_without_latest_work_target": {
                key: value
                for key, value in original_review_payload.items()
                if key != "latest_work_target"
            },
        }
    payload.update(
        {
        "failed_review_result": {
            "passed": review.passed,
            "review_md": review.review_md,
            "blocking_issues": review.blocking_issues,
            "rewrite_targets": review.rewrite_targets,
            "chapters_to_revise": review.chapters_to_revise,
        },
        "update_target_files": document_operation_target_snapshot(allowed_files),
        "requirements": [
            "这是审核不通过后的原地修复步骤，不要返回新的审核报告。",
            "必须调用目标文件 write/edit/apply_patch 工具提交修改；已有非空文件按修改意图选择 edit 或 patch，禁止无理由整篇覆盖。",
            "由返修 agent 自行判断修复策略、修改范围和工具调用次数；可以一次工具调用完成，也可以多次工具调用完成。",
            "替换、改写、补强已有章节正文或状态文档内容时优先使用 edit；当段落存在冗余、矛盾、重复或确需移除的信息时，也允许删除或重组对应内容；插入新段落、追加新记录或按标题补充小节时使用 patch。",
            "返修的重点是优化问题段落和内容，修复语言、节奏、逻辑、衔接、信息表达与人物状态，而不是只做机械删减。",
            "如果问题主要是 AI 感、句式僵硬、节奏不稳、逻辑衔接、信息表达或人物状态不清，优先改写原段、补强衔接、重写句群和修复推进；如果需要删除，也应同步保证对应场景功能、人物动机、关键信息和收尾作用仍然完整。",
            "只修改 failed_review_result 指出的阻塞问题直接影响的文件和局部。",
            "如果问题只涉及章节正文，只修改对应章节 txt；如果问题只涉及状态或进度文档，只修改对应文档。",
            "所有 old_text 或 match_text 必须从 update_target_files.current_content 中逐字复制。",
            "不要把审核失败降级为重新跑章纲、正文生成或配套文档生成阶段。",
            "如果返修涉及章节正文，修复后的内容仍必须承接当前章章纲、卷纲、全局大纲、世界模型、文笔写作风格文档与当前状态文档；人物关系、术语、世界观和剧情推进不得偏离这些注入文档。",
            "如果上一轮审核上下文里提供了 reference_chapter_metrics.target_char_count_range 或 source_char_count，返修后的正文字符数应尽量控制在接近该目标区间的范围内；如果进行了删除或重组，也要保持总体篇幅不要明显缩水或明显扩写。",
            "修复后仍必须符合原审核阶段的风格、连续性、反 AI 痕迹和参考源转换要求。",
            "完成全部必要文件修改后，必须调用 result 提交返修完成摘要。",
        ],
        "latest_work_target": {
            "type": "latest_user_input",
            "instruction": (
                f"这是本次请求的最新工作目标：根据 failed_review_result 直接原地返修{label}指出的问题。"
                "必须先调用 write/edit/apply_patch 文档工具提交修改，然后调用 result 提交返修完成摘要；"
                "不要重新生成章纲、正文或配套文档阶段。"
                "如果 failed_review_result 列出多个章节或多个 rewrite_targets，必须在本次返修阶段处理全部目标，"
                "不能只修其中一章就结束。"
            ),
            "required_tool": WORKFLOW_SUBMISSION_TOOL_NAME,
        },
        }
    )
    return payload

def _review_fix_support_update_keys(
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> list[str]:
    support_keys = [
        "character_status_cards",
        "character_relationship_graph",
        "volume_plot_progress",
        "foreshadowing",
        "world_state",
    ]
    return [key for key in support_keys if key in allowed_files]

def _filter_allowed_files_for_review_fix(
    review_kind: str,
    review: WorkflowSubmissionPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Path | document_ops.DocumentTarget]:
    if review_kind == "chapter":
        return dict(allowed_files)

    selected_keys: list[str] = []

    def include(key: str) -> None:
        if key in allowed_files and key not in selected_keys:
            selected_keys.append(key)

    for chapter_number in review.chapters_to_revise:
        normalized = "".join(ch for ch in str(chapter_number or "") if ch.isdigit()).zfill(4)
        include(f"{normalized}_rewritten_chapter")

    for raw_target in review.rewrite_targets:
        target = str(raw_target or "").strip()
        if not target:
            continue
        parts = target.split(":", 1)
        if len(parts) == 1:
            include(parts[0].strip())
            continue
        chapter_number = "".join(ch for ch in parts[0] if ch.isdigit()).zfill(4)
        target_kind = parts[1].strip()
        if target_kind in {"chapter_text", "rewritten_chapter"}:
            include(f"{chapter_number}_rewritten_chapter")
            continue
        if target_kind == "support_updates":
            for key in _review_fix_support_update_keys(allowed_files):
                include(key)
            continue
        include(target_kind)

    if not selected_keys:
        return dict(allowed_files)
    return {key: allowed_files[key] for key in selected_keys}

def _should_reuse_review_transcript(review_kind: str) -> bool:
    return review_kind == "chapter"

def _combined_applied_operation(agent_result: Any) -> document_ops.AppliedDocumentOperation:
    files: list[document_ops.AppliedDocumentFile] = []
    mode = "edit"
    for application in list(getattr(agent_result, "applications", []) or []):
        applied = getattr(application, "applied", None)
        if applied is None:
            continue
        mode = getattr(applied, "mode", mode)
        files.extend(list(getattr(applied, "files", []) or []))
    return document_ops.AppliedDocumentOperation(mode=mode, files=files)  # type: ignore[arg-type]

def _expected_review_fix_changed_keys(
    review_kind: str,
    review: WorkflowSubmissionPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> list[str]:
    if review_kind not in {"group", "volume"}:
        return []
    expected: list[str] = []
    for target in review.rewrite_targets:
        parts = str(target or "").split(":", 1)
        if len(parts) != 2:
            continue
        chapter_number = "".join(ch for ch in parts[0] if ch.isdigit()).zfill(4)
        target_kind = parts[1].strip()
        if target_kind not in {"chapter_text", "rewritten_chapter"}:
            continue
        key = f"{chapter_number}_rewritten_chapter"
        if key in allowed_files and key not in expected:
            expected.append(key)
    return expected

def _missing_review_fix_changed_keys(
    *,
    review_kind: str,
    review: WorkflowSubmissionPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    applied: document_ops.AppliedDocumentOperation,
) -> list[str]:
    expected = _expected_review_fix_changed_keys(review_kind, review, allowed_files)
    if not expected:
        return []
    changed = set(applied.changed_keys)
    return [key for key in expected if key not in changed]

def _merge_applied_document_operations(
    *operations: document_ops.AppliedDocumentOperation,
) -> document_ops.AppliedDocumentOperation:
    files: list[document_ops.AppliedDocumentFile] = []
    mode: Literal["write", "edit", "patch"] = "edit"
    for operation in operations:
        mode = operation.mode
        files.extend(operation.files)
    return document_ops.AppliedDocumentOperation(mode=mode, files=files)

def _review_fix_narrowed_to_missing_targets(
    review: WorkflowSubmissionPayload,
    missing_keys: list[str],
) -> WorkflowSubmissionPayload:
    missing_set = set(missing_keys)
    narrowed_targets: list[str] = []
    for target in review.rewrite_targets:
        parts = str(target or "").split(":", 1)
        if len(parts) != 2:
            continue
        chapter_number = "".join(ch for ch in parts[0] if ch.isdigit()).zfill(4)
        target_kind = parts[1].strip()
        if target_kind not in {"chapter_text", "rewritten_chapter"}:
            continue
        key = f"{chapter_number}_rewritten_chapter"
        if key in missing_set and target not in narrowed_targets:
            narrowed_targets.append(target)
    if not narrowed_targets:
        narrowed_targets = [f"{key.removesuffix('_rewritten_chapter')}:chapter_text" for key in missing_keys]
    narrowed_chapters = [key.removesuffix("_rewritten_chapter") for key in missing_keys]
    return WorkflowSubmissionPayload(
        passed=False,
        review_md=review.review_md,
        blocking_issues=list(review.blocking_issues),
        rewrite_targets=narrowed_targets,
        chapters_to_revise=narrowed_chapters,
    )

def _ensure_review_fix_covered_required_targets(
    *,
    review_kind: str,
    review: WorkflowSubmissionPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    applied: document_ops.AppliedDocumentOperation,
    debug_path: Path,
) -> None:
    expected = _expected_review_fix_changed_keys(review_kind, review, allowed_files)
    missing = _missing_review_fix_changed_keys(
        review_kind=review_kind,
        review=review,
        allowed_files=allowed_files,
        applied=applied,
    )
    if not missing:
        return
    error_message = (
        f"{REVIEW_KIND_LABELS.get(review_kind, '审核')}原地返修未覆盖全部审核要求的章节正文目标："
        + ", ".join(missing)
    )
    write_response_debug_snapshot(
        debug_path,
        error_message=error_message,
        preview=review.review_md,
        raw_body_text=json.dumps(
            {
                "required_changed_keys": expected,
                "actual_changed_keys": applied.changed_keys,
                "failed_review_result": {
                    "passed": review.passed,
                    "review_md": review.review_md,
                    "blocking_issues": review.blocking_issues,
                    "rewrite_targets": review.rewrite_targets,
                    "chapters_to_revise": review.chapters_to_revise,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    raise llm_runtime.ModelOutputError(error_message, preview=review.review_md)

def apply_review_fix_with_repair(
    *,
    client: OpenAI,
    model: str,
    review_kind: str,
    shared_prompt: str,
    review: WorkflowSubmissionPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    previous_response_id: str | None,
    prompt_cache_key: str | None,
    debug_path: Path,
    original_review_payload: dict[str, Any] | None = None,
    review_transcript_state: Any | None = None,
) -> tuple[document_ops.AppliedDocumentOperation, str | None, list[str]]:
    if not review_has_fix_target(review_kind, review):
        error_message = f"{REVIEW_KIND_LABELS.get(review_kind, '审核')}未通过，但模型未返回可修复目标。"
        write_response_debug_snapshot(
            debug_path,
            error_message=error_message,
            preview=review.review_md,
            raw_body_text=json.dumps(
                {
                    "failed_review_result": {
                        "passed": review.passed,
                        "review_md": review.review_md,
                        "blocking_issues": review.blocking_issues,
                        "rewrite_targets": review.rewrite_targets,
                        "chapters_to_revise": review.chapters_to_revise,
                    },
                    "target_files": document_operation_target_snapshot(allowed_files),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        raise llm_runtime.ModelOutputError(error_message, preview=review.review_md)
    fix_allowed_files = _filter_allowed_files_for_review_fix(review_kind, review, allowed_files)
    reuse_review_transcript = _should_reuse_review_transcript(review_kind)
    fix_payload = build_review_fix_payload(
        review_kind=review_kind,
        review=review,
        allowed_files=fix_allowed_files,
        original_review_payload=(
            None
            if (review_transcript_state is not None or not reuse_review_transcript)
            else original_review_payload
        ),
    )
    def report_tool(application: Any) -> None:
        if application.applied is None:
            print_progress(f"{REVIEW_KIND_LABELS.get(review_kind, '审核')}返修 agent 工具调用未应用：{application.output}", error=True)
            return
        changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
        print_progress(f"{REVIEW_KIND_LABELS.get(review_kind, '审核')}返修 agent 工具已应用：{application.tool_name}，变更={changed}。")

    agent_result = run_agent_stage(
        client,
        model=model,
        instructions=review_fix_instructions(review_kind),
        user_input=shared_prompt + json.dumps(fix_payload, ensure_ascii=False, indent=2),
        allowed_files=fix_allowed_files,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        on_tool_result=report_tool,
        transcript_state=review_transcript_state if reuse_review_transcript else None,
    )
    applied = _combined_applied_operation(agent_result)
    if not applied.emitted_keys or not applied.changed_keys:
        error_message = "审核原地返修没有实际修改任何目标文件。"
        write_response_debug_snapshot(
            debug_path,
            error_message=error_message,
            preview=agent_result.submission.summary or agent_result.submission.content_md,
            raw_body_text=json.dumps(
                {
                    "failed_review_result": {
                        "passed": review.passed,
                        "review_md": review.review_md,
                        "blocking_issues": review.blocking_issues,
                        "rewrite_targets": review.rewrite_targets,
                        "chapters_to_revise": review.chapters_to_revise,
                    },
                    "submission": agent_result.submission.model_dump(),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        raise llm_runtime.ModelOutputError(error_message, preview=agent_result.submission.summary)
    current_response_id = agent_result.response_id
    current_response_ids = list(agent_result.response_ids)
    current_transcript_state = agent_result.transcript_state if reuse_review_transcript else None
    missing = _missing_review_fix_changed_keys(
        review_kind=review_kind,
        review=review,
        allowed_files=fix_allowed_files,
        applied=applied,
    )
    if missing:
        print_progress(
            f"{REVIEW_KIND_LABELS.get(review_kind, '审核')}返修首轮未覆盖全部目标，正在补做遗漏章节："
            f"{', '.join(key.removesuffix('_rewritten_chapter') for key in missing)}。",
            error=True,
        )
        narrowed_review = _review_fix_narrowed_to_missing_targets(review, missing)
        narrowed_allowed_files = {
            key: fix_allowed_files[key]
            for key in missing
            if key in fix_allowed_files
        }
        retry_payload = build_review_fix_payload(
            review_kind=review_kind,
            review=narrowed_review,
            allowed_files=narrowed_allowed_files,
            original_review_payload=(
                None
                if (current_transcript_state is not None or not reuse_review_transcript)
                else original_review_payload
            ),
        )
        retry_payload["previous_fix_progress"] = {
            "source": "上一轮返修未覆盖全部必须修改的章节正文目标。",
            "already_changed_keys": applied.changed_keys,
            "missing_required_chapter_targets": missing,
            "instruction": "本轮只补齐 missing_required_chapter_targets，已完成的文件不要重复改写。",
        }
        retry_result = run_agent_stage(
            client,
            model=model,
            instructions=review_fix_instructions(review_kind),
            user_input=shared_prompt + json.dumps(retry_payload, ensure_ascii=False, indent=2),
            allowed_files=narrowed_allowed_files,
            previous_response_id=current_response_id,
            prompt_cache_key=prompt_cache_key,
            on_tool_result=report_tool,
            transcript_state=current_transcript_state,
        )
        retry_applied = _combined_applied_operation(retry_result)
        if not retry_applied.emitted_keys or not retry_applied.changed_keys:
            error_message = "审核原地返修补做轮未实际修改遗漏目标文件。"
            write_response_debug_snapshot(
                debug_path,
                error_message=error_message,
                preview=retry_result.submission.summary or retry_result.submission.content_md,
                raw_body_text=json.dumps(
                    {
                        "failed_review_result": {
                            "passed": review.passed,
                            "review_md": review.review_md,
                            "blocking_issues": review.blocking_issues,
                            "rewrite_targets": review.rewrite_targets,
                            "chapters_to_revise": review.chapters_to_revise,
                        },
                        "missing_required_targets": missing,
                        "submission": retry_result.submission.model_dump(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            raise llm_runtime.ModelOutputError(error_message, preview=retry_result.submission.summary)
        applied = _merge_applied_document_operations(applied, retry_applied)
        current_response_id = retry_result.response_id
        for response_id in retry_result.response_ids:
            value = str(response_id or "").strip()
            if value and value not in current_response_ids:
                current_response_ids.append(value)
        current_transcript_state = retry_result.transcript_state if reuse_review_transcript else None
    _ensure_review_fix_covered_required_targets(
        review_kind=review_kind,
        review=review,
        allowed_files=fix_allowed_files,
        applied=applied,
        debug_path=debug_path,
    )
    return applied, current_response_id, current_response_ids

def _extend_unique_response_ids(response_ids: list[str], new_response_ids: list[str]) -> None:
    for response_id in new_response_ids:
        value = str(response_id or "").strip()
        if value and value not in response_ids:
            response_ids.append(value)

def run_five_chapter_review(
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
    review_path = five_chapter_review_path(project_root, volume_number, chapter_numbers)
    rewritten_chapters = build_rewritten_chapters_payload(project_root, volume_number, chapter_numbers)
    prompt_cache_key = f"{build_volume_review_session_key(rewrite_manifest, volume_number)}-{batch_id}"
    shared_prompt = build_five_chapter_review_shared_prompt(
        manifest=rewrite_manifest,
        volume_material=volume_material,
        chapter_numbers=chapter_numbers,
        rewritten_chapters=rewritten_chapters,
    )
    review_state = get_five_chapter_review_state(rewrite_manifest, volume_number, batch_id, chapter_numbers)
    previous_response_id = str(review_state.get("last_response_id") or "").strip() or None
    stored_response_ids = review_state.get("response_ids")
    response_ids = [str(item) for item in stored_response_ids if str(item or "").strip()] if isinstance(stored_response_ids, list) else []

    def report_tool(application: Any) -> None:
        if application.applied is None:
            print_progress(f"{FIVE_CHAPTER_REVIEW_NAME} agent 工具调用未应用：{application.output}", error=True)
            return
        changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
        print_progress(f"{FIVE_CHAPTER_REVIEW_NAME} agent 工具已应用：{application.tool_name}，变更={changed}。")

    for attempt in range(1, MAX_GROUP_REVIEW_ATTEMPTS + 1):
        rewritten_chapters = build_rewritten_chapters_payload(project_root, volume_number, chapter_numbers)
        update_five_chapter_review_state(
            rewrite_manifest,
            volume_number,
            batch_id,
            chapter_numbers,
            status="in_progress",
            attempts=attempt,
            chapters_to_revise=[],
            blocking_issues=[],
            response_ids=response_ids,
            last_response_id=previous_response_id,
        )
        catalog = read_doc_catalog(project_root, volume_number, chapter_numbers[0])
        payload, included_docs, omitted_docs = build_five_chapter_review_payload(
            project_root=project_root,
            volume_material=volume_material,
            chapter_numbers=chapter_numbers,
            catalog=catalog,
            rewritten_chapters=rewritten_chapters,
        )
        print_progress(
            f"{FIVE_CHAPTER_REVIEW_NAME} 第 {attempt}/{MAX_GROUP_REVIEW_ATTEMPTS} 次调用："
            f"审核第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]}。"
        )
        print_request_context_summary(
            request_label=f"{FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}）",
            volume_number=volume_number,
            chapter_number=None,
            location_label=f"第 {volume_number} 卷，第 {chapter_numbers[0]}-{chapter_numbers[-1]} 组审查。",
            source_summary_lines=five_chapter_review_source_summary_lines(
                volume_material,
                chapter_numbers,
                rewritten_chapters,
            ),
            included_docs=included_docs,
            omitted_docs=omitted_docs,
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
            shared_prefix_lines=[
                *group_review_shared_prefix_summary_lines(
                    rewrite_manifest,
                    volume_material,
                    chapter_numbers,
                    rewritten_chapters,
                ),
                *payload_prefix_doc_summary_lines(payload),
            ],
            dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
            payload=payload,
            user_input_char_count=len(shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)),
            session_status_line=(
                "会话：本地 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
                "不依赖 provider previous_response_id。"
            ),
        )
        try:
            allowed_files = multi_chapter_review_fix_target_paths(
                project_root,
                volume_number,
                chapter_numbers,
                group_review_path=review_path,
            )
            agent_result = run_agent_stage(
                client,
                model=model,
                instructions=COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS,
                user_input=shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                allowed_files=allowed_files,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                on_tool_result=report_tool,
            )
            previous_response_id = agent_result.response_id
            _extend_unique_response_ids(response_ids, agent_result.response_ids)
            review = finalize_review_payload(
                agent_result.submission,
                review_kind="group",
                allowed_chapters=chapter_numbers,
            )
            if review.passed is None or not review.review_md.strip():
                raise llm_runtime.ModelOutputError(
                    "组审查 agent 未通过 result 返回完整 passed / review_md。",
                    preview=agent_result.submission.summary or agent_result.submission.content_md,
                )
            print_agent_application_summary(
                agent_result,
                agent_label=f"{FIVE_CHAPTER_REVIEW_NAME} agent",
                no_tool_message=f"{FIVE_CHAPTER_REVIEW_NAME} agent 本轮未调用文档修复工具，直接提交审核结论。",
            )
            print_agent_review_submission_summary(review, agent_label=f"{FIVE_CHAPTER_REVIEW_NAME} agent")
        except Exception as error:
            if isinstance(error, llm_runtime.ModelOutputError):
                write_response_debug_snapshot(
                    review_path.with_name(f"{batch_id}_group_review_debug.md"),
                    error_message=str(error),
                    preview=error.preview,
                    raw_body_text=getattr(error, "raw_body_text", ""),
                )
            update_five_chapter_review_state(
                rewrite_manifest,
                volume_number,
                batch_id,
                chapter_numbers,
                status="failed",
                attempts=attempt,
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            raise
        group_review_changed = write_artifact(review_path, review.review_md)
        print_call_artifact_report(
            f"{FIVE_CHAPTER_REVIEW_NAME}调用",
            [(f"{FIVE_CHAPTER_REVIEW_NAME}文档", review_path)],
            ["group_review"] if group_review_changed else [],
        )

        if review.passed:
            update_five_chapter_review_state(
                rewrite_manifest,
                volume_number,
                batch_id,
                chapter_numbers,
                status="passed",
                attempts=attempt,
                chapters_to_revise=[],
                blocking_issues=[],
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            print_progress(
                f"{FIVE_CHAPTER_REVIEW_NAME} 已通过：第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]}。"
            )
            return True

        chapters_to_revise = [item.zfill(4) for item in review.chapters_to_revise if item]
        if attempt >= MAX_GROUP_REVIEW_ATTEMPTS:
            update_five_chapter_review_state(
                rewrite_manifest,
                volume_number,
                batch_id,
                chapter_numbers,
                status="failed",
                attempts=attempt,
                chapters_to_revise=chapters_to_revise,
                blocking_issues=review.blocking_issues,
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            fail(
                f"第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]} "
                f"{FIVE_CHAPTER_REVIEW_NAME}连续 {MAX_GROUP_REVIEW_ATTEMPTS} 次审核、"
                f"原地返修 {MAX_GROUP_REVIEW_FIX_ATTEMPTS} 次后仍未通过。"
            )

        update_five_chapter_review_state(
            rewrite_manifest,
            volume_number,
            batch_id,
            chapter_numbers,
            status="in_review_fix",
            attempts=attempt,
            chapters_to_revise=chapters_to_revise,
            blocking_issues=review.blocking_issues,
            response_ids=response_ids,
            last_response_id=previous_response_id,
        )
        print_progress(
            f"{FIVE_CHAPTER_REVIEW_NAME} 未通过，开始按审核结论原地返修。"
            f" 目标章节：{'、'.join(chapters_to_revise) or '未明确'}。"
        )
        try:
            applied, previous_response_id, fix_response_ids = apply_review_fix_with_repair(
                client=client,
                model=model,
                review_kind="group",
                shared_prompt=shared_prompt,
                review=review,
                allowed_files=allowed_files,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                debug_path=review_path.with_name(f"{batch_id}_group_review_debug.md"),
                original_review_payload=payload,
                review_transcript_state=agent_result.transcript_state,
            )
        except Exception as error:
            update_five_chapter_review_state(
                rewrite_manifest,
                volume_number,
                batch_id,
                chapter_numbers,
                status="failed",
                attempts=attempt,
                chapters_to_revise=chapters_to_revise,
                blocking_issues=review.blocking_issues,
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            raise
        _extend_unique_response_ids(response_ids, fix_response_ids)
        update_five_chapter_review_state(
            rewrite_manifest,
            volume_number,
            batch_id,
            chapter_numbers,
            status="in_review_fix",
            attempts=attempt,
            chapters_to_revise=chapters_to_revise,
            blocking_issues=review.blocking_issues,
            response_ids=response_ids,
            last_response_id=previous_response_id,
        )
        print_call_artifact_report(
            f"{FIVE_CHAPTER_REVIEW_NAME}原地返修调用",
            [(doc_label_for_key(item.file_key), item.path) for item in applied.files],
            applied.changed_keys,
        )
        print_progress(f"{FIVE_CHAPTER_REVIEW_NAME}原地返修已完成，下一轮将复审当前组。")

    fail(f"第 {volume_number} 卷 {chapter_numbers[0]}-{chapter_numbers[-1]} 连续审查失败。")

def run_due_five_chapter_reviews(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    target_group: list[str] | None = None,
) -> bool:
    while True:
        if target_group is not None:
            due_group = target_group if (
                all_group_chapters_passed(rewrite_manifest, volume_material, target_group)
                and not group_review_passed(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    target_group,
                )
            ) else None
        else:
            due_group = current_due_group_review(
                rewrite_manifest,
                volume_material,
            )
        if due_group is None:
            return True
        if not run_five_chapter_review(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_numbers=due_group,
        ):
            return False
        if target_group is not None:
            return True

def run_volume_review(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> bool:
    project_root = Path(rewrite_manifest["project_root"])
    paths = rewrite_paths(project_root, volume_material["volume_number"])
    chapter_numbers = [chapter["chapter_number"] for chapter in volume_material["chapters"]]
    rewritten_chapters = build_rewritten_chapters_payload(project_root, volume_material["volume_number"], chapter_numbers)
    prompt_cache_key = build_volume_review_session_key(rewrite_manifest, volume_material["volume_number"])
    shared_prompt = build_volume_review_shared_prompt(
        manifest=rewrite_manifest,
        volume_material=volume_material,
        rewritten_chapters=rewritten_chapters,
    )
    review_state = get_volume_review_state(rewrite_manifest, volume_material["volume_number"])
    previous_response_id = str(review_state.get("last_response_id") or "").strip() or None
    stored_response_ids = review_state.get("response_ids")
    response_ids = [str(item) for item in stored_response_ids if str(item or "").strip()] if isinstance(stored_response_ids, list) else []

    def report_tool(application: Any) -> None:
        if application.applied is None:
            print_progress(f"卷级审核 agent 工具调用未应用：{application.output}", error=True)
            return
        changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
        print_progress(f"卷级审核 agent 工具已应用：{application.tool_name}，变更={changed}。")

    for attempt in range(1, MAX_VOLUME_REVIEW_ATTEMPTS + 1):
        update_volume_review_state(
            rewrite_manifest,
            volume_material["volume_number"],
            status="in_progress",
            attempts=attempt,
            chapters_to_revise=[],
            blocking_issues=[],
            response_ids=response_ids,
            last_response_id=previous_response_id,
        )
        write_volume_stage_snapshot(
            paths["volume_stage_manifest"],
            volume_number=volume_material["volume_number"],
            status="in_progress",
            note="开始卷级审核。",
            attempt=attempt,
        )
        try:
            rewritten_chapters = build_rewritten_chapters_payload(
                project_root,
                volume_material["volume_number"],
                chapter_numbers,
            )
            catalog = read_doc_catalog(project_root, volume_material["volume_number"], chapter_numbers[0])
            payload, included_docs, omitted_docs = build_volume_review_payload(
                project_root=project_root,
                volume_material=volume_material,
                volume_number=volume_material["volume_number"],
                catalog=catalog,
                rewritten_chapters=rewritten_chapters,
            )
            print_progress(
                f"卷级审核第 {attempt}/{MAX_VOLUME_REVIEW_ATTEMPTS} 次调用："
                f"审核第 {volume_material['volume_number']} 卷。"
            )
            print_request_context_summary(
                request_label="卷级审核",
                volume_number=volume_material["volume_number"],
                chapter_number=None,
                location_label=f"第 {volume_material['volume_number']} 卷，卷级审核。",
                source_summary_lines=volume_review_source_summary_lines(rewritten_chapters),
                included_docs=included_docs,
                omitted_docs=omitted_docs,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                shared_prefix_lines=[
                    *volume_review_shared_prefix_summary_lines(
                        rewrite_manifest,
                        volume_material,
                        rewritten_chapters,
                    ),
                    *payload_prefix_doc_summary_lines(payload),
                ],
                dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
                payload=payload,
                user_input_char_count=len(shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2)),
                session_status_line=(
                    "会话：本地 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
                    "不依赖 provider previous_response_id。"
                ),
            )
            allowed_files = multi_chapter_review_fix_target_paths(
                project_root,
                volume_material["volume_number"],
                chapter_numbers,
                include_volume_docs=True,
            )
            agent_result = run_agent_stage(
                client,
                model=model,
                instructions=COMMON_VOLUME_REVIEW_INSTRUCTIONS,
                user_input=shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                allowed_files=allowed_files,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                on_tool_result=report_tool,
            )
            previous_response_id = agent_result.response_id
            _extend_unique_response_ids(response_ids, agent_result.response_ids)
            volume_review = finalize_review_payload(
                agent_result.submission,
                review_kind="volume",
                allowed_chapters=list(rewritten_chapters.keys()),
            )
            if volume_review.passed is None or not volume_review.review_md.strip():
                raise llm_runtime.ModelOutputError(
                    "卷级审核 agent 未通过 result 返回完整 passed / review_md。",
                    preview=agent_result.submission.summary or agent_result.submission.content_md,
                )
            print_agent_application_summary(
                agent_result,
                agent_label="卷级审核 agent",
                no_tool_message="卷级审核 agent 本轮未调用文档修复工具，直接提交审核结论。",
            )
            print_agent_review_submission_summary(volume_review, agent_label="卷级审核 agent")
            volume_review_changed = write_artifact(paths["volume_review"], volume_review.review_md)
            print_call_artifact_report(
                "卷级审核调用",
                [("卷级审核文档", paths["volume_review"])],
                ["volume_review"] if volume_review_changed else [],
            )
            write_volume_stage_snapshot(
                paths["volume_stage_manifest"],
                volume_number=volume_material["volume_number"],
                status="completed",
                note="卷级审核已完成。",
                attempt=attempt,
                response_id=previous_response_id,
            )

            if volume_review.passed:
                processed = set(rewrite_manifest.get("processed_volumes", []))
                processed.add(volume_material["volume_number"])
                rewrite_manifest["processed_volumes"] = sorted(processed)
                rewrite_manifest["last_processed_volume"] = volume_material["volume_number"]
                save_rewrite_manifest(rewrite_manifest)
                update_volume_review_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    status="passed",
                    attempts=attempt,
                    chapters_to_revise=[],
                    blocking_issues=[],
                    response_ids=response_ids,
                    last_response_id=previous_response_id,
                )
                print_progress(f"第 {volume_material['volume_number']} 卷已通过卷级审核。")
                return True

            chapters_to_revise = [item.zfill(4) for item in volume_review.chapters_to_revise if item]
            if attempt >= MAX_VOLUME_REVIEW_ATTEMPTS:
                update_volume_review_state(
                    rewrite_manifest,
                    volume_material["volume_number"],
                    status="failed",
                    attempts=attempt,
                    chapters_to_revise=chapters_to_revise,
                    blocking_issues=volume_review.blocking_issues,
                    response_ids=response_ids,
                    last_response_id=previous_response_id,
                )
                fail(
                    f"第 {volume_material['volume_number']} 卷卷级审核连续 "
                    f"{MAX_VOLUME_REVIEW_ATTEMPTS} 次审核、原地返修 "
                    f"{MAX_VOLUME_REVIEW_FIX_ATTEMPTS} 次后仍未通过。"
                )

            update_volume_review_state(
                rewrite_manifest,
                volume_material["volume_number"],
                status="in_review_fix",
                attempts=attempt,
                chapters_to_revise=chapters_to_revise,
                blocking_issues=volume_review.blocking_issues,
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            print_progress(
                f"第 {volume_material['volume_number']} 卷卷级审核未通过，开始按审核结论原地返修。"
                f" 目标章节：{'、'.join(chapters_to_revise) or '未明确'}。"
            )
            applied, previous_response_id, fix_response_ids = apply_review_fix_with_repair(
                client=client,
                model=model,
                review_kind="volume",
                shared_prompt=shared_prompt,
                review=volume_review,
                allowed_files=allowed_files,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                debug_path=paths["volume_response_debug"],
                original_review_payload=payload,
                review_transcript_state=agent_result.transcript_state,
            )
            _extend_unique_response_ids(response_ids, fix_response_ids)
            update_volume_review_state(
                rewrite_manifest,
                volume_material["volume_number"],
                status="in_review_fix",
                attempts=attempt,
                chapters_to_revise=chapters_to_revise,
                blocking_issues=volume_review.blocking_issues,
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            print_call_artifact_report(
                "卷级审核原地返修调用",
                [(doc_label_for_key(item.file_key), item.path) for item in applied.files],
                applied.changed_keys,
            )
            print_progress(f"第 {volume_material['volume_number']} 卷卷级审核原地返修已完成，下一轮将复审当前卷。")
        except Exception as error:
            if isinstance(error, llm_runtime.ModelOutputError):
                write_response_debug_snapshot(
                    paths["volume_response_debug"],
                    error_message=str(error),
                    preview=error.preview,
                    raw_body_text=getattr(error, "raw_body_text", ""),
                )
            update_volume_review_state(
                rewrite_manifest,
                volume_material["volume_number"],
                status="failed",
                attempts=attempt,
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            write_volume_stage_snapshot(
                paths["volume_stage_manifest"],
                volume_number=volume_material["volume_number"],
                status="failed",
                note=str(error),
                attempt=attempt,
            )
            raise

    fail(f"第 {volume_material['volume_number']} 卷连续 {MAX_VOLUME_REVIEW_ATTEMPTS} 次卷级审核未通过。")

__all__ = [
    'review_fix_instructions',
    'review_fix_phase_key',
    'review_fix_role',
    'review_has_fix_target',
    'chapter_review_fix_target_paths',
    'multi_chapter_review_fix_target_paths',
    'build_review_fix_payload',
    'apply_review_fix_with_repair',
    'run_five_chapter_review',
    'run_due_five_chapter_reviews',
    'run_volume_review',
]
