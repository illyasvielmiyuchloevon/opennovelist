from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .materials import build_loaded_file_inventory, build_volume_source_bundle
from .project import stage_paths
from .prompts import print_adaptation_request_context_summary
from novelist.core.agent_runtime import run_agent_stage
from novelist.core.workflow_tools import WorkflowSubmissionPayload


GROUP_OUTLINE_PLAN_TOOL_NAME = "submit_group_outline_plan"


class GroupOutlinePlanGroup(BaseModel):
    chapter_count: int = Field(..., ge=1, description="这一组在新书中要生成的章节数。")
    source_chapter_range: str = Field("", description="本组对应理解的参考源章节范围，例如 01-06。")
    group_title: str = Field("", description="本组在新书中的推进主题。")
    guidance: str = Field("", description="写给组纲生成环节的详细指导，说明源功能如何转换为新书推进。")


class GroupOutlinePlanPayload(BaseModel):
    groups: list[GroupOutlinePlanGroup] = Field(default_factory=list, description="按新书阅读顺序排列的章节组。")
    summary: str = Field("", description="整卷分组思路摘要。")


class GroupOutlineStageResult(BaseModel):
    payload: WorkflowSubmissionPayload
    response_ids: list[str] = Field(default_factory=list)
    review_path: str = ""
    fix_attempts: int = 0


def group_outline_plan_tool_spec() -> llm_runtime.FunctionToolSpec[Any]:
    return llm_runtime.FunctionToolSpec(
        model=GroupOutlinePlanPayload,
        name=GROUP_OUTLINE_PLAN_TOOL_NAME,
        description=(
            "提交当前卷的新书章节组规划。只提交结构化分组，不写正文。"
            "每组必须给出新书章节数、对应参考源范围和组纲生成指导。"
        ),
    )


def _read_doc(path: Path) -> dict[str, Any]:
    content = read_text_if_exists(path).strip()
    return {
        "file_name": path.name,
        "file_path": str(path),
        "char_count": len(content),
        "content": content,
    }


def _group_outline_docs(project_root: Path, volume_number: str) -> list[dict[str, Any]]:
    return group_outline_docs_from_plan(project_root, volume_number, require_passed=False)


def build_group_outline_plan_request(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    requirements = [
        "不要机械按 5 章一组；必须根据卷纲、参考源推进阶段和新书正文需要决定每组章数。",
        "本阶段决定的是新书章节组，不要求与参考源章节一一对应；每组只需要说明对应参考源范围和功能理解。",
        "每组的 guidance 必须能指导后续生成独立组纲：写清本组要承接的卷纲任务、参考源功能映射、新书冲突推进、高潮/收束位置、篇幅和节奏倾向。",
        "后续章节生成不会再读取参考源章节正文，所以本阶段必须把参考源功能理解转化成可执行的组纲指导。",
        "目标新书章节号会按分组顺序自动从 0001 连续编号；你只需要给每组 chapter_count。",
        "严禁把参考源人物名、地名、势力名、事件名、术语名、等级体系或话语体系写成新书主体设定；只能写功能映射和转换要求。",
        "必须调用 submit_group_outline_plan 提交结构化 groups；不要调用文档工具，不要提交普通文本。",
    ]
    migration_context: dict[str, Any] = {}
    if volume_material.get("legacy_group_outline_backfill"):
        migration_context = {
            "mode": "legacy_group_outline_backfill",
            "rule": "旧工程当前卷资料已经适配完成，本阶段只在现有源卷和既有卷资料基础上补齐动态章节组计划，不要求重分卷、不重写卷资料。",
        }
        requirements.insert(0, "这是旧工程补齐组纲：必须尊重当前源卷边界和既有卷资料，不得要求自适应重分卷或重做卷资料适配。")
    return {
        "document_request": {
            "phase": "volume_group_outline_planning",
            "role": "当前卷章节组规划编辑",
            "task": "基于当前卷整卷参考源、卷级大纲和全局资料，决定本卷新书正文要拆成多少个章节组，以及每组写多少章。",
        },
        "migration_context": migration_context,
        "planning_scope": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest["target_worldview"],
            "current_volume": volume_material["volume_number"],
            "source_chapter_count": len(volume_material["chapters"]),
            "source_extra_count": len(volume_material["extras"]),
        },
        "injected_documents": {
            "world_model": _read_doc(paths["world_model"]),
            "style_guide": _read_doc(paths["style_guide"]),
            "book_outline": _read_doc(paths["book_outline"]),
            "foreshadowing": _read_doc(paths["foreshadowing"]),
            "volume_outline": _read_doc(paths["volume_outline"]),
        },
        "requirements": requirements,
        "latest_work_target": {
            "type": "latest_user_input",
            "instruction": "这是本次请求的最新工作目标：提交当前卷动态章节组计划。必须调用 submit_group_outline_plan。",
            "required_tool": GROUP_OUTLINE_PLAN_TOOL_NAME,
        },
    }


def submit_group_outline_plan(
    *,
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    paths: dict[str, Path],
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
) -> tuple[dict[str, Any], str | None, list[str]]:
    request_payload = build_group_outline_plan_request(
        manifest=manifest,
        volume_material=volume_material,
        paths=paths,
    )
    user_input = stage_shared_prompt + json.dumps(request_payload, ensure_ascii=False, indent=2)
    print_adaptation_request_context_summary(
        request_label="整卷组纲计划",
        volume_material=volume_material,
        loaded_files=build_loaded_file_inventory(volume_material),
        source_char_count=build_volume_source_bundle(volume_material)[1],
        payload=request_payload,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        user_input_char_count=len(user_input),
        session_status_line="会话：沿用当前卷资料适配逻辑阶段，继续使用整卷参考源和最新资料上下文。",
    )
    result = llm_runtime.call_function_tools(
        client,
        model=model,
        instructions=COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
        user_input=user_input,
        tool_specs=[group_outline_plan_tool_spec()],
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=DEFAULT_API_RETRIES,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
        tool_choice={"type": "function", "name": GROUP_OUTLINE_PLAN_TOOL_NAME},
    )
    plan = GroupOutlinePlanPayload.model_validate(result.parsed)
    if not plan.groups:
        raise llm_runtime.ModelOutputError("组纲计划阶段未返回任何章节组。", preview=result.preview)
    raw_groups = [group.model_dump(mode="json") for group in plan.groups]
    payload = write_group_outline_plan_manifest(
        Path(manifest["project_root"]),
        volume_material["volume_number"],
        status="planned",
        groups=raw_groups,
        source_volume_dir=str(volume_material.get("volume_dir", "")),
        note=plan.summary or "整卷组纲计划已生成，等待写入组纲文件。",
        response_ids=[str(result.response_id)] if result.response_id else [],
        review={"status": "pending", "review_file": str(group_outline_plan_review_path(Path(manifest["project_root"]), volume_material["volume_number"]))},
    )
    return payload, result.response_id, [str(result.response_id)] if result.response_id else []


def group_outline_generation_allowed_files(project_root: Path, volume_number: str) -> dict[str, Path]:
    targets: dict[str, Path] = {}
    for doc in _group_outline_docs(project_root, volume_number):
        group_id = str(doc["group_id"])
        targets[f"{group_id}_group_outline"] = Path(str(doc["file_path"]))
    return targets


def group_outline_generation_target_inventory(project_root: Path, volume_number: str) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for doc in _group_outline_docs(project_root, volume_number):
        path = Path(str(doc["file_path"]))
        current_content = read_text_if_exists(path).strip()
        chapter_numbers = list(doc["chapter_numbers"])
        inventory.append(
            {
                "file_key": f"{doc['group_id']}_group_outline",
                "label": doc["label"],
                "file_name": path.name,
                "file_path": str(path),
                "exists": path.exists(),
                "preferred_mode": "edit_or_patch" if current_content else "write",
                "current_content": current_content,
                "chapter_numbers": chapter_numbers,
                "source_chapter_range": doc.get("source_chapter_range", ""),
                "group_title": doc.get("group_title", ""),
                "guidance": doc.get("guidance", ""),
                "required_structure": {
                    "title": f"# {chapter_numbers[0]}-{chapter_numbers[-1]} 组纲",
                    "chapter_headings": [f"## {chapter_number}" for chapter_number in chapter_numbers],
                    "chapter_outline_policy": "每章细纲必须足以支撑后续正文生成，因为章节正文阶段不会再读取参考源章节。",
                },
                "tool_selection_policy": (
                    "文件为空时用 write；已有内容只做局部修订时用 edit 或 patch。"
                    "每个组纲必须落盘到对应 file_key。"
                ),
            }
        )
    return inventory


def build_group_outline_generation_request(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    project_root = Path(manifest["project_root"])
    plan = load_group_outline_plan(project_root, volume_material["volume_number"], require_passed=False)
    requirements = [
        "必须为 group_outline_plan.groups 中每一个章节组写入一个组纲文件，不得漏组。",
        "每个组纲顶层标题必须是 # 起始章-结束章 组纲，并为本组每章写一个二级标题，例如 ## 0001。",
        "每章细纲必须包含：本章写作目标、剧情推进、冲突/爽点、角色状态变化、篇幅/节奏建议、参考源功能映射到新书的转换说明。",
        "组纲可以根据新书需要重新安排章节内容，不要求与参考源章节一一对应。",
        "后续正文阶段不再加载参考源章节，所以组纲必须把源功能理解转化为可直接执行的章纲。",
        "严禁把参考源原名词、原事件名、原术语、原等级体系和话语体系写成新书主体内容。",
        "全部组纲文件写入后，必须调用 submit_workflow_result，并在 generated_files 中列出所有组纲 file_key。",
    ]
    if volume_material.get("legacy_group_outline_backfill"):
        requirements.insert(0, "这是旧工程补齐组纲：只生成缺失的组纲文件，不重写已有卷资料，不要求重排参考源分卷。")
    return {
        "document_request": {
            "phase": "volume_group_outline_generation",
            "role": "当前卷组纲生成 agent",
            "task": "根据已提交的动态章节组计划，为当前卷所有章节组生成组纲文件。",
        },
        "group_outline_plan": plan,
        "injected_documents": {
            "world_model": _read_doc(paths["world_model"]),
            "style_guide": _read_doc(paths["style_guide"]),
            "book_outline": _read_doc(paths["book_outline"]),
            "foreshadowing": _read_doc(paths["foreshadowing"]),
            "volume_outline": _read_doc(paths["volume_outline"]),
        },
        "requirements": requirements,
        "target_files": group_outline_generation_target_inventory(project_root, volume_material["volume_number"]),
        "latest_work_target": {
            "type": "latest_user_input",
            "instruction": "这是本次请求的最新工作目标：写入当前卷所有组纲文件，最后调用 submit_workflow_result。",
            "required_tool": WORKFLOW_SUBMISSION_TOOL_NAME,
        },
    }


def run_group_outline_generation_agent(
    *,
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    paths: dict[str, Path],
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
    response_ids: list[str],
) -> tuple[WorkflowSubmissionPayload, str | None, list[str]]:
    project_root = Path(manifest["project_root"])
    request_payload = build_group_outline_generation_request(
        manifest=manifest,
        volume_material=volume_material,
        paths=paths,
    )
    allowed_files = group_outline_generation_allowed_files(project_root, volume_material["volume_number"])
    user_input = stage_shared_prompt + json.dumps(request_payload, ensure_ascii=False, indent=2)
    print_adaptation_request_context_summary(
        request_label="整卷组纲生成",
        volume_material=volume_material,
        loaded_files=build_loaded_file_inventory(volume_material),
        source_char_count=build_volume_source_bundle(volume_material)[1],
        payload=request_payload,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        user_input_char_count=len(user_input),
        allowed_files=allowed_files,
        session_status_line="会话：卷资料审核通过后继续当前卷资料适配逻辑阶段，写入本卷全部组纲。",
    )

    def report_tool(application: Any) -> None:
        if application.applied is None:
            print_progress(f"整卷组纲生成工具调用未应用：{application.output}", error=True)
            return
        changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
        print_progress(f"整卷组纲生成工具已应用：{application.tool_name}，变更={changed}。")

    result = run_agent_stage(
        client,
        model=model,
        instructions=COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
        user_input=user_input,
        allowed_files=allowed_files,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=DEFAULT_API_RETRIES,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
        on_tool_result=report_tool,
    )
    validate_group_outline_files(project_root, volume_material["volume_number"], require_passed=False)
    all_response_ids = [*response_ids]
    all_response_ids.extend(response_id for response_id in result.response_ids if response_id not in all_response_ids)
    plan = load_group_outline_plan(project_root, volume_material["volume_number"], require_passed=False)
    write_group_outline_plan_manifest(
        project_root,
        volume_material["volume_number"],
        status="review_pending",
        groups=list(plan["groups"]),
        source_volume_dir=str(volume_material.get("volume_dir", "")),
        note="整卷组纲文件已生成，等待组纲审核通过。",
        response_ids=all_response_ids,
        review={"status": "pending", "review_file": str(group_outline_plan_review_path(project_root, volume_material["volume_number"]))},
    )
    print_agent_application_summary(
        result,
        agent_label="整卷组纲生成 agent",
        no_tool_message="整卷组纲生成 agent 本轮未调用文档工具，直接提交阶段完成结果。",
    )
    print_agent_generation_submission_summary(result, agent_label="整卷组纲生成 agent")
    return result.submission, result.response_id, all_response_ids


def build_group_outline_review_request(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    project_root = Path(manifest["project_root"])
    plan = load_group_outline_plan(project_root, volume_material["volume_number"], require_passed=False)
    return {
        "document_request": {
            "phase": "volume_group_outline_review",
            "role": "当前卷组纲审核编辑",
            "task": "审核当前卷所有组纲是否足以支撑后续章节正文生成。",
            "required_file": group_outline_plan_review_path(project_root, volume_material["volume_number"]).name,
        },
        "review_scope": {
            "current_volume": volume_material["volume_number"],
            "total_groups": plan.get("total_groups"),
            "total_chapters": plan.get("total_chapters"),
            "review_policy": "这是卷资料适配的一部分；组纲审核通过前，本卷不得标记完成，也不得进入章节正文生成。",
        },
        "injected_documents": {
            "world_model": _read_doc(paths["world_model"]),
            "style_guide": _read_doc(paths["style_guide"]),
            "book_outline": _read_doc(paths["book_outline"]),
            "foreshadowing": _read_doc(paths["foreshadowing"]),
            "volume_outline": _read_doc(paths["volume_outline"]),
        },
        "group_outline_plan": plan,
        "group_outlines": _group_outline_docs(project_root, volume_material["volume_number"]),
        "requirements": [
            "必须检查每个组纲是否对应卷纲中的推进指导，并覆盖 group_outline_plan 中的每组章节。",
            "必须检查组纲是否已经把参考源功能理解转化为新书章纲；后续章节正文阶段不会再读取参考源章节。",
            "必须检查每章细纲是否足够可执行：写作目标、冲突推进、角色变化、节奏篇幅和新书转换说明是否明确。",
            "必须检查是否残留参考源人物名、地名、势力名、事件名、术语名、等级体系或话语体系作为新书主体。",
            "如果不通过，rewrite_targets 必须填写需要修复的组纲 file_key，例如 0001_0006_group_outline。",
            "本阶段是 agent 审核阶段：可以先调用 write/edit/patch 原地修复允许范围内的组纲，再继续审核并最终提交 submit_workflow_result。",
        ],
        "latest_work_target": {
            "type": "latest_user_input",
            "instruction": "这是本次请求的最新工作目标：审核并必要时修复当前卷所有组纲，最终调用 submit_workflow_result。",
            "required_tool": WORKFLOW_SUBMISSION_TOOL_NAME,
        },
    }


def run_group_outline_review_until_passed(
    *,
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    paths: dict[str, Path],
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
    response_ids: list[str],
) -> tuple[GroupOutlineStageResult, str | None]:
    project_root = Path(manifest["project_root"])
    review_path = group_outline_plan_review_path(project_root, volume_material["volume_number"])
    current_response_id = previous_response_id
    all_response_ids = [*response_ids]
    last_review: WorkflowSubmissionPayload | None = None

    def allowed_files() -> dict[str, Path]:
        return group_outline_generation_allowed_files(project_root, volume_material["volume_number"])

    def report_tool(application: Any) -> None:
        if application.applied is None:
            print_progress(f"组纲审核 agent 工具调用未应用：{application.output}", error=True)
            return
        changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
        print_progress(f"组纲审核 agent 工具已应用：{application.tool_name}，变更={changed}。")

    for attempt in range(1, MAX_ADAPTATION_REVIEW_ATTEMPTS + 1):
        request_payload = build_group_outline_review_request(
            manifest=manifest,
            volume_material=volume_material,
            paths=paths,
        )
        user_input = stage_shared_prompt + json.dumps(request_payload, ensure_ascii=False, indent=2)
        print_adaptation_request_context_summary(
            request_label=f"组纲审核第 {attempt}/{MAX_ADAPTATION_REVIEW_ATTEMPTS} 次",
            volume_material=volume_material,
            loaded_files=build_loaded_file_inventory(volume_material),
            source_char_count=build_volume_source_bundle(volume_material)[1],
            payload=request_payload,
            previous_response_id=current_response_id,
            prompt_cache_key=prompt_cache_key,
            user_input_char_count=len(user_input),
            allowed_files=allowed_files(),
            session_status_line="会话：组纲审核属于当前卷资料适配逻辑阶段，可原地修复组纲后提交审核结论。",
        )
        agent_result = run_agent_stage(
            client,
            model=model,
            instructions=COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            user_input=user_input,
            allowed_files=allowed_files(),
            previous_response_id=current_response_id,
            prompt_cache_key=prompt_cache_key,
            retries=DEFAULT_API_RETRIES,
            retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
            on_tool_result=report_tool,
        )
        current_response_id = agent_result.response_id
        all_response_ids.extend(response_id for response_id in agent_result.response_ids if response_id not in all_response_ids)
        review = agent_result.submission
        last_review = review
        if review.passed is None or not (review.review_md or review.content_md or review.summary).strip():
            raise llm_runtime.ModelOutputError(
                "组纲审核 agent 未通过 submit_workflow_result 返回完整 passed / review_md。",
                preview=review.summary or review.content_md,
            )
        review_md = review.review_md or review.content_md or review.summary
        write_text_if_changed(review_path, review_md)
        print_agent_application_summary(
            agent_result,
            agent_label="组纲审核 agent",
            no_tool_message="组纲审核 agent 本轮未调用文档修复工具，直接提交审核结论。",
        )
        print_agent_review_submission_summary(review, agent_label="组纲审核 agent")
        if review.passed:
            plan = load_group_outline_plan(project_root, volume_material["volume_number"], require_passed=False)
            write_group_outline_plan_manifest(
                project_root,
                volume_material["volume_number"],
                status="passed",
                groups=list(plan["groups"]),
                source_volume_dir=str(volume_material.get("volume_dir", "")),
                note="整卷组纲已审核通过，可进入章节正文生成。",
                response_ids=all_response_ids,
                review={
                    "status": "passed",
                    "passed": True,
                    "review_file": str(review_path),
                    "response_ids": all_response_ids,
                    "fix_attempts": max(attempt - 1, 0),
                    "blocking_issues": review.blocking_issues,
                    "rewrite_targets": review.rewrite_targets,
                },
            )
            validate_group_outline_files(project_root, volume_material["volume_number"], require_passed=True)
            return (
                GroupOutlineStageResult(
                    payload=review,
                    response_ids=all_response_ids,
                    review_path=str(review_path),
                    fix_attempts=max(attempt - 1, 0),
                ),
                current_response_id,
            )

        plan = load_group_outline_plan(project_root, volume_material["volume_number"], require_passed=False)
        write_group_outline_plan_manifest(
            project_root,
            volume_material["volume_number"],
            status="review_pending",
            groups=list(plan["groups"]),
            source_volume_dir=str(volume_material.get("volume_dir", "")),
            note="组纲审核未通过，等待下一轮原地返修。",
            response_ids=all_response_ids,
            review={
                "status": "failed",
                "passed": False,
                "review_file": str(review_path),
                "response_ids": all_response_ids,
                "fix_attempts": attempt,
                "blocking_issues": review.blocking_issues,
                "rewrite_targets": review.rewrite_targets,
            },
        )
        if attempt >= MAX_ADAPTATION_REVIEW_ATTEMPTS:
            raise llm_runtime.ModelOutputError(
                f"第 {volume_material['volume_number']} 卷组纲审核连续 {MAX_ADAPTATION_REVIEW_ATTEMPTS} 次仍未通过。",
                preview=review.review_md,
            )
        print_progress(
            "组纲审核未通过，将再次进入同一 agent 审核/返修循环；"
            f"返修目标={', '.join(review.rewrite_targets) if review.rewrite_targets else '未返回'}。"
        )

    raise llm_runtime.ModelOutputError(
        f"第 {volume_material['volume_number']} 卷组纲审核失败。",
        preview=last_review.review_md if last_review else "",
    )


def run_group_outline_workflow_until_passed(
    *,
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
) -> tuple[GroupOutlineStageResult, str | None]:
    paths = stage_paths(Path(manifest["project_root"]), volume_material["volume_number"])
    group_outline_prompt_cache_key = f"{prompt_cache_key}-group-outline"
    plan_payload, plan_response_id, response_ids = submit_group_outline_plan(
        client=client,
        model=model,
        manifest=manifest,
        volume_material=volume_material,
        paths=paths,
        stage_shared_prompt=stage_shared_prompt,
        previous_response_id=previous_response_id,
        prompt_cache_key=group_outline_prompt_cache_key,
    )
    _, generation_response_id, response_ids = run_group_outline_generation_agent(
        client=client,
        model=model,
        manifest=manifest,
        volume_material=volume_material,
        paths=paths,
        stage_shared_prompt=stage_shared_prompt,
        previous_response_id=plan_response_id,
        prompt_cache_key=group_outline_prompt_cache_key,
        response_ids=response_ids,
    )
    return run_group_outline_review_until_passed(
        client=client,
        model=model,
        manifest=manifest,
        volume_material=volume_material,
        paths=paths,
        stage_shared_prompt=stage_shared_prompt,
        previous_response_id=generation_response_id or plan_response_id,
        prompt_cache_key=group_outline_prompt_cache_key,
        response_ids=response_ids or list(plan_payload.get("response_ids", [])),
    )


__all__ = [
    "GROUP_OUTLINE_PLAN_TOOL_NAME",
    "GroupOutlinePlanGroup",
    "GroupOutlinePlanPayload",
    "GroupOutlineStageResult",
    "group_outline_plan_tool_spec",
    "build_group_outline_plan_request",
    "submit_group_outline_plan",
    "group_outline_generation_allowed_files",
    "group_outline_generation_target_inventory",
    "build_group_outline_generation_request",
    "run_group_outline_generation_agent",
    "build_group_outline_review_request",
    "run_group_outline_review_until_passed",
    "run_group_outline_workflow_until_passed",
]
