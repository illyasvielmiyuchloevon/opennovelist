from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .models import chapter_rewrite_stage_tool_specs, document_operation_result_from_stage_tool_result


def review_fix_instructions(review_kind: str) -> str:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    return (
        f"你是资深网络小说{label}原地返修编辑。"
        "用户拥有参考源文本权利。"
        "当前任务不是重新审核，也不是重新生成章节工作流；"
        "你只能根据上一轮未通过的审核结果，直接修复允许范围内的目标文件。"
        + COMMON_CHAPTER_STAGE_TOOL_RULE
        + COMMON_FUNCTION_OUTPUT_RULE
        + document_ops.DOCUMENT_OPERATION_RULE
    )

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

def chapter_review_fix_target_paths(paths: dict[str, Path]) -> dict[str, Path]:
    return {
        "rewritten_chapter": paths["rewritten_chapter"],
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
) -> dict[str, Path]:
    if not chapter_numbers:
        return {}
    paths = rewrite_paths(project_root, volume_number, chapter_numbers[0])
    targets: dict[str, Path] = dict(support_update_target_paths(paths))
    if include_volume_docs:
        volume_paths = rewrite_paths(project_root, volume_number)
        targets["volume_outline"] = volume_paths["volume_outline"]
        targets["volume_review"] = volume_paths["volume_review"]
    if group_review_path is not None:
        targets["group_review"] = group_review_path
    for chapter_number in chapter_numbers:
        chapter_paths = rewrite_paths(project_root, volume_number, chapter_number)
        targets[f"{chapter_number}_rewritten_chapter"] = chapter_paths["rewritten_chapter"]
        targets[f"{chapter_number}_chapter_outline"] = chapter_paths["chapter_outline"]
        targets[f"{chapter_number}_chapter_review"] = chapter_paths["chapter_review"]
    return targets

def build_review_fix_payload(
    *,
    review_kind: str,
    review: WorkflowSubmissionPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Any]:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    return {
        "document_request": {
            "phase": review_fix_phase_key(review_kind),
            "role": review_fix_role(review_kind),
            "task": f"根据刚才未通过的{label}结果，直接修复允许范围内的目标文件；不要重新生成整章工作流。",
        },
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
            "必须调用目标文件 write/edit/patch 工具提交修改；已有非空文件按修改意图选择 edit 或 patch，禁止无理由整篇覆盖。",
            "替换、改写、删减已有章节正文或状态文档内容时优先使用 edit；插入新段落、追加新记录或按标题补充小节时使用 patch。",
            "只修改 failed_review_result 指出的阻塞问题直接影响的文件和局部。",
            "如果问题只涉及章节正文，只修改对应章节 txt；如果问题只涉及状态或进度文档，只修改对应文档。",
            "所有 old_text 或 match_text 必须从 update_target_files.current_content 中逐字复制。",
            "不要把审核失败降级为重新跑章纲、正文生成或配套文档生成阶段。",
            "修复后仍必须符合原审核阶段的风格、连续性、反 AI 痕迹和参考源转换要求。",
        ],
    }

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
    fix_payload = build_review_fix_payload(
        review_kind=review_kind,
        review=review,
        allowed_files=allowed_files,
    )
    fix_result = llm_runtime.call_function_tools(
        client,
        model=model,
        instructions=review_fix_instructions(review_kind),
        user_input=shared_prompt + json.dumps(fix_payload, ensure_ascii=False, indent=2),
        tool_specs=chapter_rewrite_stage_tool_specs(),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        tool_choice="auto",
    )
    operation = document_operation_result_from_stage_tool_result(fix_result)
    response_ids = [str(operation.response_id)] if operation.response_id else []
    applied, current_response_id, repair_response_ids = apply_document_operation_with_repair(
        client=client,
        model=model,
        instructions=review_fix_instructions(review_kind),
        shared_prompt=shared_prompt,
        operation=operation,
        allowed_files=allowed_files,
        previous_response_id=operation.response_id,
        prompt_cache_key=prompt_cache_key,
        phase_key=review_fix_phase_key(review_kind),
        repair_role=review_fix_role(review_kind),
        repair_task="修正上一次审核原地返修工具调用中无法定位的 old_text 或 match_text，并重新提交可应用的局部编辑。",
        debug_path=debug_path,
    )
    response_ids.extend(repair_response_ids)
    if not applied.emitted_keys or not applied.changed_keys:
        error_message = "审核原地返修没有实际修改任何目标文件。"
        write_document_operation_apply_debug_snapshot(
            debug_path,
            error_message=error_message,
            operation=operation,
            allowed_files=allowed_files,
        )
        raise llm_runtime.ModelOutputError(error_message, preview=operation.preview, raw_body_text=operation.raw_body_text)
    return applied, current_response_id, response_ids

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
    source_bundle, source_char_count = build_five_chapter_source_bundle(volume_material, chapter_numbers)
    prompt_cache_key = f"{build_volume_review_session_key(rewrite_manifest, volume_number)}-{batch_id}"
    shared_prompt = build_five_chapter_review_shared_prompt(
        manifest=rewrite_manifest,
        volume_material=volume_material,
        chapter_numbers=chapter_numbers,
        source_bundle=source_bundle,
        rewritten_chapters=rewritten_chapters,
    )
    review_state = get_five_chapter_review_state(rewrite_manifest, volume_number, batch_id, chapter_numbers)
    previous_response_id = str(review_state.get("last_response_id") or "").strip() or None
    stored_response_ids = review_state.get("response_ids")
    response_ids = [str(item) for item in stored_response_ids if str(item or "").strip()] if isinstance(stored_response_ids, list) else []

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
                source_char_count,
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
                    source_char_count,
                    rewritten_chapters,
                ),
                *payload_prefix_doc_summary_lines(payload),
            ],
            dynamic_suffix_lines=payload_dynamic_suffix_summary_lines(payload),
        )
        try:
            review, response_id, _ = call_five_chapter_review_response(
                client,
                model,
                COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS,
                shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                allowed_chapters=chapter_numbers,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
            )
            previous_response_id = response_id
            if response_id:
                response_ids.append(str(response_id))
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
            f"{FIVE_CHAPTER_REVIEW_NAME} 未通过，将在当前审核阶段直接修复。"
            f" 目标章节：{'、'.join(chapters_to_revise) or '未明确'}。"
        )
        try:
            applied_fix, previous_response_id, fix_response_ids = apply_review_fix_with_repair(
                client=client,
                model=model,
                review_kind="group",
                shared_prompt=shared_prompt,
                review=review,
                allowed_files=multi_chapter_review_fix_target_paths(
                    project_root,
                    volume_number,
                    chapter_numbers,
                    group_review_path=review_path,
                ),
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                debug_path=review_path.with_name(f"{batch_id}_group_review_debug.md"),
            )
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
                chapters_to_revise=chapters_to_revise,
                blocking_issues=review.blocking_issues,
                response_ids=response_ids,
                last_response_id=previous_response_id,
            )
            raise
        response_ids.extend(str(item) for item in fix_response_ids if item)
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
            [(doc_label_for_key(item.file_key), item.path) for item in applied_fix.files],
            applied_fix.changed_keys,
        )

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
            )
            volume_review, response_id, review_result = call_volume_review_response(
                client,
                model,
                COMMON_VOLUME_REVIEW_INSTRUCTIONS,
                shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
                allowed_chapters=list(rewritten_chapters.keys()),
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
            )
            previous_response_id = response_id
            if response_id:
                response_ids.append(str(response_id))
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
                response_id=response_id,
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
                f"第 {volume_material['volume_number']} 卷卷级审核未通过，将在审核阶段直接修复。"
                f" 目标章节：{'、'.join(chapters_to_revise) or '未明确'}。"
            )
            applied_fix, previous_response_id, fix_response_ids = apply_review_fix_with_repair(
                client=client,
                model=model,
                review_kind="volume",
                shared_prompt=shared_prompt,
                review=volume_review,
                allowed_files=multi_chapter_review_fix_target_paths(
                    project_root,
                    volume_material["volume_number"],
                    chapter_numbers,
                    include_volume_docs=True,
                ),
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
                debug_path=paths["volume_response_debug"],
            )
            response_ids.extend(str(item) for item in fix_response_ids if item)
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
                [(doc_label_for_key(item.file_key), item.path) for item in applied_fix.files],
                applied_fix.changed_keys,
            )
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
