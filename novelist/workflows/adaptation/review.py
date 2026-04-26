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

def adaptation_review_document_files(paths: dict[str, Path]) -> dict[str, Path]:
    targets = {doc_key: paths[doc_key] for doc_key in GLOBAL_INJECTION_DOC_ORDER}
    targets["volume_outline"] = paths["volume_outline"]
    return targets

def adaptation_review_allowed_files(paths: dict[str, Path], *, volume_number: str | None = None) -> dict[str, Path]:
    targets = adaptation_review_document_files(paths)
    if volume_number is not None and volume_number != "001":
        targets.pop("style_guide", None)
    return targets

def adaptation_review_target_snapshot(
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
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
                current_content=current_content,
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
                "审核下游仿写实际会用到的完整当前资料集。第 001 卷应包含 6 个核心资料文档；"
                "后续卷审核本卷更新文档，并带上已存在的文笔写作风格文档。"
            ),
        },
        "requirements": [
            "判断资料是否足够支撑后续章节仿写，而不是只检查格式是否完整。",
            "检查参考源人物名、地名、姓氏、势力名、事件名称、专用术语、等级体系、称谓口吻、标志性台词和话语体系是否已经替换或映射，不得直接照搬。",
            "检查世界模型是否已经合并承载目标世界观、背景故事、故事类型、角色功能位和世界知识，并与参考源明显区分。",
            "检查世界模型是否作为新书全书世界知识模型和设定唯一来源，是否使用新书自己的命名系统、数值系统、等级体系、能力术语、势力称谓、地点命名、资源命名和话语体系；不得与参考源出现相同命名、数值体系或概念话语体系。",
            "审核命名和术语污染时必须区分通用语素、通用话语术语与专用话语术语：通用语素和玄幻/仙侠常见通用术语可以使用，不应判为污染；参考源自造名词、专属组合词、标志性称谓和专用话语体系必须改名或重构。",
            "修炼境界名称按等级名和专用术语审核：参考源若使用“XX境”，新书必须替换“XX”前缀，不能出现同前缀境界名；“境”这个通用后缀允许继续使用，不应因后缀相同判定为污染。",
            "检查世界模型中的地点、势力、能力、资源、规则、术语、道具设计与原书功能映射是否清晰。",
            "检查世界模型是否只包含世界观与世界知识；不得出现卷内已发生大事件、主角战绩、考试排名、榜单变化、活动结果、家庭进展、治疗进度、奖励记录、剧情推进清单或故事进程。",
            "检查全书大纲是否是仿写书籍的大纲，不得把参考源原大纲照抄为新书大纲。",
            "检查当前卷卷级大纲的角色推进、冲突、高潮、结尾钩子是否正确映射。",
            "检查全书故事线蓝图是否只用二级标题维护不同故事线，并在故事线内部紧凑保留功能映射、全书走向、卷际连续性与后续约束；不得写成待补全模板、逐章复述、卷级剧情进程或进度台账。",
            "检查伏笔文档中由资料适配新增或修改的部分是否是全书级/卷级伏笔设计索引，是否保留参考源功能映射、新书伏笔设计、埋设意图、后续呼应方向与命名映射；如果文件中已有章节工作流写入的运行时记录，应视为受保护内容，不得要求删除或改写，也不得仅因其存在判定资料适配不通过。",
            "检查伏笔文档是否严格执行伏笔准入门槛；普通剧情细节、阶段性战绩、考试排名、榜单变化、奖励记录、资源获得、治疗进度、一次性物件、已完成小冲突、普通关系进展、场景气氛和过场信息不得被当成全书/卷级伏笔。",
            "检查每条资料适配伏笔是否能说明未来触发、反转、兑现或呼应方向；如果只能说明已经发生的剧情事实，就不应写入伏笔文档。",
            "检查全局资料之间是否重复承载同一信息；世界规则应在世界模型，卷内推进应在卷级剧情进程，章节细节应在章纲或审核文档。",
            "检查文风文档是否可执行，且只提炼写法与节奏，不复制参考源实体内容；文风文档只在第 001 卷生成和定稿，后续卷只能读取与审核，不得把 style_guide 写入 rewrite_targets。",
            "如果不通过，rewrite_targets 必须只填写需要修复的 file_key，例如 world_model、book_outline、volume_outline。",
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
            "如果修复目标包含伏笔文档，只能修复资料适配设计索引相关内容；已有章节工作流写入的运行时记录是受保护内容，禁止删除、归并或改写。",
            "所有 file_key 或 file_path 必须来自 update_target_files，禁止修改未授权文件。",
            "所有 old_text 或 match_text 必须从 update_target_files.current_content 中逐字复制。",
            "不得把审核失败降级为重新跑整卷资料生成阶段。",
            "修复后仍必须符合目标世界观、实体改名、事件改名、术语映射、时间线和故事线整理要求。",
            "修复后不得残留参考源人物名、地名、势力名、事件名、专用术语、等级体系、称谓口吻、标志性台词或话语体系。",
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
    unauthorized_targets = [target for target in review.rewrite_targets if target not in allowed_files]
    if unauthorized_targets:
        error_message = "卷资料审核返回了当前卷不允许原地修复的目标：" + ", ".join(unauthorized_targets)
        write_response_debug_snapshot(
            manifest,
            volume_material,
            error_message=error_message,
            preview=review.review_md,
            raw_body_text=json.dumps(
                {
                    "failed_review_result": review.model_dump(mode="json"),
                    "allowed_fix_targets": sorted(allowed_files.keys()),
                    "unauthorized_targets": unauthorized_targets,
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
    review_files = adaptation_review_document_files(paths)
    allowed_files = adaptation_review_allowed_files(paths, volume_number=volume_material["volume_number"])
    response_ids: list[str] = []
    current_response_id = previous_response_id
    last_review: AdaptationReviewPayload | None = None

    for attempt in range(1, MAX_ADAPTATION_REVIEW_ATTEMPTS + 1):
        write_stage_status_snapshot(
            manifest,
            volume_material,
            status="adaptation_reviewing",
            note=f"正在进行第 {attempt} 次卷资料审核；审核通过后才会标记本卷完成。",
            previous_response_id=current_response_id,
        )
        print_progress(f"卷资料审核第 {attempt}/{MAX_ADAPTATION_REVIEW_ATTEMPTS} 次调用：审核第 {volume_material['volume_number']} 卷资料。")
        review_payload = build_adaptation_review_request(
            manifest=manifest,
            volume_material=volume_material,
            allowed_files=review_files,
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

        if attempt >= MAX_ADAPTATION_REVIEW_ATTEMPTS:
            error_message = (
                f"卷资料审核连续 {MAX_ADAPTATION_REVIEW_ATTEMPTS} 次审核、"
                f"原地返修 {MAX_ADAPTATION_REVIEW_FIX_ATTEMPTS} 次后仍未通过。"
            )
            write_response_debug_snapshot(
                manifest,
                volume_material,
                error_message=error_message,
                preview=review.review_md,
                raw_body_text=json.dumps(
                    {
                        "failed_review_result": review.model_dump(mode="json"),
                        "target_files": adaptation_review_target_snapshot(review_files),
                        "allowed_fix_targets": sorted(allowed_files.keys()),
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
    'adaptation_review_document_files',
    'adaptation_review_allowed_files',
    'adaptation_review_target_snapshot',
    'build_adaptation_review_request',
    'write_adaptation_review_report',
    'build_adaptation_review_fix_request',
    'apply_adaptation_review_fix_with_repair',
    'run_adaptation_review_until_passed',
]
