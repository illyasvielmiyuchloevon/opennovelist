from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def call_adaptation_review_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    *,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
) -> tuple[
    AdaptationReviewPayload,
    str | None,
    llm_runtime.FunctionToolResult[AdaptationReviewPayload],
]:
    result = llm_runtime.call_function_tool(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_model=AdaptationReviewPayload,
        tool_name=ADAPTATION_REVIEW_TOOL_NAME,
        tool_description=ADAPTATION_REVIEW_TOOL_DESCRIPTION,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=DEFAULT_API_RETRIES,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
    )
    payload = result.parsed
    if payload.passed is None or not payload.review_md.strip():
        raise llm_runtime.ModelOutputError(
            "模型未通过卷资料审核工具返回完整的 passed / review_md 字段。",
            preview=result.preview,
            raw_body_text=result.raw_body_text,
        )
    return payload, result.response_id, result

def adaptation_review_allowed_files(paths: dict[str, Path]) -> dict[str, Path]:
    targets = {doc_key: paths[doc_key] for doc_key in GLOBAL_INJECTION_DOC_ORDER}
    targets["volume_outline"] = paths["volume_outline"]
    return targets

def adaptation_review_target_snapshot(
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    *,
    content_limit: int = 30000,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for file_key, target in allowed_files.items():
        path = target.path if isinstance(target, document_ops.DocumentTarget) else target
        current_content = read_text_if_exists(path).strip()
        snapshots.append(
            AdaptationReviewTarget(
                file_key=file_key,
                file_name=path.name,
                file_path=str(path),
                label=adaptation_doc_label(file_key),
                scope=adaptation_doc_scope(file_key),
                exists=path.exists(),
                current_char_count=len(current_content),
                current_content=clip_for_context(current_content, limit=content_limit),
                preferred_mode="edit_or_patch" if current_content else "write",
            ).model_dump(mode="json")
        )
        snapshots[-1]["tool_selection_policy"] = (
            "按修复意图选择工具：替换已有内容、批量清理参考源残留名词或修正已有条目用 edit；"
            "插入新条目、追加新段落、按 Markdown 标题补充或替换小节正文用 patch；"
            "文件为空或首次创建时才用 write。"
        )
    return snapshots

def build_adaptation_review_request(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Any]:
    volume_number = volume_material["volume_number"]
    return {
        "document_request": {
            "phase": "adaptation_volume_review",
            "role": "卷资料审核",
            "task": "审核当前卷资料文档是否已经满足后续章节仿写需要，并判断是否可以结束本卷资料阶段。",
        },
        "review_scope": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest["target_worldview"],
            "current_volume": volume_number,
            "document_set_policy": (
                "审核下游仿写实际会用到的完整当前资料集。第 001 卷应包含 7 个核心资料文档；"
                "后续卷审核本卷更新文档，并带上已存在的文笔写作风格文档。"
            ),
        },
        "requirements": [
            "判断资料是否足够支撑后续章节仿写，而不是只检查格式是否完整。",
            "检查参考源人物名、地名、姓氏、事件名称、专用术语是否已经替换或映射，不得直接照搬。",
            "检查世界观设定是否已经改成目标世界观，并与参考源明显区分。",
            "检查世界模型中的地点、势力、能力、资源、规则和术语是否有清晰的新书映射。",
            "检查全书大纲是否是仿写书籍的大纲，不得把参考源原大纲照抄为新书大纲。",
            "检查当前卷卷级大纲的角色推进、冲突、高潮、结尾钩子是否正确映射。",
            "检查全书故事线蓝图是否按故事线独立保留蓝图、参考源功能映射、分卷设计与跨卷递进，并且没有把旧卷压缩替换成简化摘要。",
            "检查伏笔文档是否保留功能映射，同时改成新书自己的伏笔、回收点与命名。",
            "检查文风文档是否可执行，且只提炼写法与节奏，不复制参考源实体内容。",
            "如果不通过，rewrite_targets 必须只填写需要修复的 file_key，例如 world_design、world_model、book_outline、volume_outline。",
        ],
        "adaptation_documents": adaptation_review_target_snapshot(allowed_files),
        "output_contract": {
            "passed": "布尔值；只有所有阻塞问题解决才为 true。",
            "review_md": "Markdown 审核报告；必须写清通过/不通过原因。",
            "blocking_issues": "不通过时列出会阻塞后续仿写的具体问题。",
            "rewrite_targets": "不通过时列出需要原地修复的目标 file_key；通过时为空数组。",
        },
    }

def write_adaptation_review_report(
    path: Path,
    *,
    volume_number: str,
    review: AdaptationReviewPayload,
    attempt: int,
    response_id: str | None,
) -> None:
    lines = [
        f"# Adaptation Review {volume_number}",
        "",
        f"- generated_at: {now_iso()}",
        f"- volume_number: {volume_number}",
        f"- attempt: {attempt}",
        f"- passed: {review.passed}",
        f"- response_id: {response_id or 'none'}",
        f"- rewrite_targets: {', '.join(review.rewrite_targets) if review.rewrite_targets else 'none'}",
        "",
    ]
    if review.blocking_issues:
        lines.append("## Blocking Issues")
        lines.append("")
        lines.extend(f"- {issue}" for issue in review.blocking_issues)
        lines.append("")
    lines.append("## Review")
    lines.append("")
    lines.append(review.review_md.strip())
    write_text_if_changed(path, "\n".join(lines))

def build_adaptation_review_fix_request(
    *,
    review: AdaptationReviewPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Any]:
    return {
        "document_request": {
            "phase": "adaptation_review_fix",
            "role": "卷资料审核原地返修编辑",
            "task": "根据刚才未通过的卷资料审核结果，直接修复允许范围内的目标资料文档；不要重新生成整卷资料阶段。",
        },
        "failed_review_result": {
            "passed": review.passed,
            "review_md": review.review_md,
            "blocking_issues": review.blocking_issues,
            "rewrite_targets": review.rewrite_targets,
        },
        "update_target_files": adaptation_review_target_snapshot(allowed_files),
        "requirements": [
            "这是卷资料审核不通过后的原地修复步骤，不要返回新的审核报告。",
            "必须调用目标文件 write/edit/patch 工具提交修改；已有非空文件按修改意图选择 edit 或 patch，禁止无理由整篇覆盖。",
            "替换已有正文、清理参考源残留人物名/地名/术语/事件名时，优先使用 edit，可按需要使用 replace_all。",
            "插入新条目、追加新段落、按标题补充或替换小节正文时，使用 patch。",
            "只修改 failed_review_result 指出的阻塞问题直接影响的文件和局部。",
            "所有 file_key 或 file_path 必须来自 update_target_files，禁止修改未授权文件。",
            "所有 old_text 或 match_text 必须从 update_target_files.current_content 中逐字复制。",
            "不得把审核失败降级为重新跑整卷资料生成阶段。",
            "修复后仍必须符合目标世界观、实体改名、事件改名、术语映射、时间线和故事线整理要求。",
        ],
    }

def apply_adaptation_review_fix_with_repair(
    *,
    client: OpenAI,
    model: str,
    shared_prompt: str,
    review: AdaptationReviewPayload,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    previous_response_id: str | None,
    prompt_cache_key: str | None,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> tuple[document_ops.AppliedDocumentOperation, str | None, list[str]]:
    if not review.rewrite_targets:
        error_message = "卷资料审核未通过，但模型未返回可修复目标。"
        write_response_debug_snapshot(
            manifest,
            volume_material,
            error_message=error_message,
            preview=review.review_md,
            raw_body_text=json.dumps(
                {
                    "failed_review_result": review.model_dump(mode="json"),
                    "target_files": adaptation_review_target_snapshot(allowed_files),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        raise llm_runtime.ModelOutputError(error_message, preview=review.review_md)

    fix_payload = build_adaptation_review_fix_request(
        review=review,
        allowed_files=allowed_files,
    )
    operation = document_ops.call_document_operation_tools(
        client,
        model=model,
        instructions=COMMON_ADAPTATION_REVIEW_FIX_INSTRUCTIONS,
        user_input=shared_prompt + json.dumps(fix_payload, ensure_ascii=False, indent=2),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=DEFAULT_API_RETRIES,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
    )
    response_ids = [str(operation.response_id or "")]
    applied, current_response_id, repair_response_ids = apply_document_operation_with_repair(
        client=client,
        model=model,
        instructions=COMMON_ADAPTATION_REVIEW_FIX_INSTRUCTIONS,
        shared_prompt=shared_prompt,
        operation=operation,
        allowed_files=allowed_files,
        previous_response_id=operation.response_id,
        prompt_cache_key=prompt_cache_key,
        manifest=manifest,
        volume_material=volume_material,
    )
    response_ids.extend(repair_response_ids)
    if not applied.emitted_keys or not applied.changed_keys:
        error_message = "卷资料审核原地返修没有实际修改任何目标文件。"
        write_document_operation_apply_debug_snapshot(
            manifest,
            volume_material,
            error_message=error_message,
            operation=operation,
            allowed_files=allowed_files,
        )
        raise llm_runtime.ModelOutputError(error_message, preview=operation.preview, raw_body_text=operation.raw_body_text)
    return applied, current_response_id, response_ids

def run_adaptation_review_until_passed(
    *,
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
) -> tuple[AdaptationReviewResult, str | None]:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    allowed_files = adaptation_review_allowed_files(paths)
    response_ids: list[str] = []
    current_response_id = previous_response_id
    last_review: AdaptationReviewPayload | None = None

    for attempt in range(1, MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS + 2):
        write_stage_status_snapshot(
            manifest,
            volume_material,
            status="adaptation_reviewing",
            note=f"正在进行第 {attempt} 次卷资料审核；审核通过后才会标记本卷完成。",
        )
        print_progress(f"卷资料审核第 {attempt}/{MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS + 1} 次调用：审核第 {volume_material['volume_number']} 卷资料。")
        review_payload = build_adaptation_review_request(
            manifest=manifest,
            volume_material=volume_material,
            allowed_files=allowed_files,
        )
        review, current_response_id, _ = call_adaptation_review_response(
            client,
            model,
            COMMON_ADAPTATION_REVIEW_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(review_payload, ensure_ascii=False, indent=2),
            previous_response_id=current_response_id,
            prompt_cache_key=prompt_cache_key,
        )
        if current_response_id:
            response_ids.append(current_response_id)
        last_review = review
        write_adaptation_review_report(
            paths["adaptation_review"],
            volume_number=volume_material["volume_number"],
            review=review,
            attempt=attempt,
            response_id=current_response_id,
        )

        if review.passed:
            print_progress("卷资料审核已通过。")
            return (
                AdaptationReviewResult(
                    payload=review,
                    response_ids=response_ids,
                    review_path=str(paths["adaptation_review"]),
                    fix_attempts=attempt - 1,
                ),
                current_response_id,
            )

        if attempt > MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS:
            error_message = f"卷资料审核原地返修 {MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS} 次后仍未通过。"
            write_response_debug_snapshot(
                manifest,
                volume_material,
                error_message=error_message,
                preview=review.review_md,
                raw_body_text=json.dumps(
                    {
                        "failed_review_result": review.model_dump(mode="json"),
                        "target_files": adaptation_review_target_snapshot(allowed_files),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            raise llm_runtime.ModelOutputError(error_message, preview=review.review_md)

        print_progress(
            "卷资料审核未通过，进入当前审核阶段原地返修；"
            f"目标：{', '.join(review.rewrite_targets) if review.rewrite_targets else '未返回'}。"
        )
        applied_fix, current_response_id, fix_response_ids = apply_adaptation_review_fix_with_repair(
            client=client,
            model=model,
            shared_prompt=stage_shared_prompt,
            review=review,
            allowed_files=allowed_files,
            previous_response_id=current_response_id,
            prompt_cache_key=prompt_cache_key,
            manifest=manifest,
            volume_material=volume_material,
        )
        response_ids.extend(fix_response_ids)
        print_progress(
            "卷资料审核返修已应用："
            f"模式={applied_fix.mode}，文件={', '.join(applied_fix.changed_keys)}。"
        )

    error_message = "卷资料审核流程异常结束。"
    raise llm_runtime.ModelOutputError(error_message, preview=last_review.review_md if last_review else "")

__all__ = [
    'call_adaptation_review_response',
    'adaptation_review_allowed_files',
    'adaptation_review_target_snapshot',
    'build_adaptation_review_request',
    'write_adaptation_review_report',
    'build_adaptation_review_fix_request',
    'apply_adaptation_review_fix_with_repair',
    'run_adaptation_review_until_passed',
]
