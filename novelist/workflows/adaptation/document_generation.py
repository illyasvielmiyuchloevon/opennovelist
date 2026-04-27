from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .models import adaptation_stage_tool_specs
from novelist.core.workflow_tools import WORKFLOW_SUBMISSION_TOOL_NAME
from novelist.core.agent_runtime import run_agent_stage


def call_document_operation_response(
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
    retries: int = DEFAULT_API_RETRIES,
) -> tuple[document_ops.DocumentOperationCallResult, str | None]:
    result = llm_runtime.call_function_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_specs=adaptation_stage_tool_specs(),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=retries,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
        tool_choice="auto",
    )
    operation = document_operation_result_from_stage_tool_result(result)
    return operation, operation.response_id

def document_operation_result_from_stage_tool_result(
    result: llm_runtime.MultiFunctionToolResult,
) -> document_ops.DocumentOperationCallResult:
    if result.tool_name == document_ops.DOCUMENT_WRITE_TOOL_NAME:
        return document_ops.DocumentOperationCallResult(
            mode="write",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            write_payload=document_ops.DocumentWritePayload.model_validate(result.parsed),
        )
    if result.tool_name == document_ops.DOCUMENT_EDIT_TOOL_NAME:
        return document_ops.DocumentOperationCallResult(
            mode="edit",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            edit_payload=document_ops.DocumentEditPayload.model_validate(result.parsed),
        )
    if result.tool_name == document_ops.DOCUMENT_PATCH_TOOL_NAME:
        return document_ops.DocumentOperationCallResult(
            mode="patch",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            patch_payload=document_ops.DocumentPatchPayload.model_validate(result.parsed),
        )
    if result.tool_name == WORKFLOW_SUBMISSION_TOOL_NAME:
        raise llm_runtime.ModelOutputError(
            "当前资料生成/修复步骤必须调用文档 write/edit/patch 工具，不能调用 submit_workflow_result。",
            preview=result.preview,
            raw_body_text=result.raw_body_text,
        )
    raise llm_runtime.ModelOutputError(f"模型调用了未支持的资料阶段工具：{result.tool_name}")

def load_stage_manifest_payload(stage_manifest_path: Path) -> dict[str, Any]:
    if not stage_manifest_path.exists():
        return {}
    try:
        return extract_json_payload(read_text(stage_manifest_path))
    except Exception:
        return {}

def completed_document_keys_from_stage_payload(
    payload: dict[str, Any],
    document_plan: list[dict[str, Any]],
) -> list[str]:
    plan_keys = [str(item["key"]) for item in document_plan]
    generated_keys = payload.get("generated_document_keys")
    if isinstance(generated_keys, list) and generated_keys:
        generated_set = {str(item) for item in generated_keys if str(item).strip()}
        return [key for key in plan_keys if key in generated_set]

    api_calls = payload.get("api_calls")
    if isinstance(api_calls, list) and api_calls:
        generated_set = {
            str(item.get("key"))
            for item in api_calls
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }
        return [key for key in plan_keys if key in generated_set]

    if payload.get("status") == "generating_document":
        current_key = str(payload.get("current_batch_range") or "").strip()
        if current_key in plan_keys:
            return plan_keys[: plan_keys.index(current_key)]
        try:
            current_batch = int(payload.get("current_batch") or 0)
        except (TypeError, ValueError):
            current_batch = 0
        if current_batch > 1:
            return plan_keys[: max(current_batch - 1, 0)]

    return []

def previous_processed_volume_number(
    manifest: dict[str, Any],
    current_volume_number: str,
) -> str | None:
    try:
        current_number = int(str(current_volume_number))
    except ValueError:
        current_number = -1

    previous_numbers: list[str] = []
    for item in manifest.get("processed_volumes", []):
        volume_number = str(item).zfill(3)
        try:
            if int(volume_number) < current_number:
                previous_numbers.append(volume_number)
        except ValueError:
            continue
    if not previous_numbers:
        return None
    return sorted(previous_numbers, key=lambda item: int(item))[-1]

def previous_processed_stage_mtime(
    manifest: dict[str, Any],
    current_volume_number: str,
) -> float | None:
    previous_volume = previous_processed_volume_number(manifest, current_volume_number)
    if previous_volume is None:
        return None

    project_root = Path(manifest["project_root"])
    previous_stage_manifest = stage_paths(project_root, previous_volume)["stage_manifest"]
    if not previous_stage_manifest.exists():
        return None
    return previous_stage_manifest.stat().st_mtime

def infer_completed_document_keys_from_file_prefix(
    paths: dict[str, Path],
    document_plan: list[dict[str, Any]],
    *,
    manifest: dict[str, Any] | None,
    volume_number: str | None,
) -> list[str]:
    if manifest is None:
        project_root = paths["global_dir"].parent
        manifest = load_manifest(project_root)
    if manifest is None:
        return []

    raw_volume_number = str(volume_number or "").strip()
    if not raw_volume_number:
        return []
    current_volume_number = raw_volume_number.zfill(3)
    processed_volumes = {str(item).zfill(3) for item in manifest.get("processed_volumes", [])}
    if current_volume_number in processed_volumes:
        return []

    cutoff_mtime = previous_processed_stage_mtime(manifest, current_volume_number) or 0.0
    completed_keys: list[str] = []
    last_mtime: float | None = None
    for doc_spec in document_plan:
        doc_key = str(doc_spec["key"])
        output_path = document_output_path(paths, doc_key)
        if not read_text_if_exists(output_path).strip():
            break
        doc_mtime = output_path.stat().st_mtime
        if doc_mtime <= cutoff_mtime:
            break
        if last_mtime is not None and doc_mtime + 2.0 < last_mtime:
            break
        completed_keys.append(doc_key)
        last_mtime = max(last_mtime or doc_mtime, doc_mtime)

    return completed_keys

def load_document_generation_resume_state(
    paths: dict[str, Path],
    document_plan: list[dict[str, Any]],
    *,
    manifest: dict[str, Any] | None = None,
    volume_number: str | None = None,
) -> dict[str, Any]:
    payload = load_stage_manifest_payload(paths["stage_manifest"])
    completed_keys = completed_document_keys_from_stage_payload(payload, document_plan)
    resume_source = "stage_manifest"
    if not completed_keys:
        payload_volume = str(payload.get("processed_volume") or "").strip()
        inferred_volume_number = volume_number or payload_volume
        completed_keys = infer_completed_document_keys_from_file_prefix(
            paths,
            document_plan,
            manifest=manifest,
            volume_number=inferred_volume_number,
        )
        if completed_keys:
            resume_source = "file_mtime_prefix"

    verified_completed_keys: list[str] = []
    generated_by_key: dict[str, dict[str, Any]] = {}
    api_calls = payload.get("api_calls")
    if isinstance(api_calls, list):
        generated_by_key = {
            str(item.get("key")): item
            for item in api_calls
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }

    generated_documents: list[dict[str, Any]] = []
    for index, doc_spec in enumerate(document_plan, start=1):
        doc_key = str(doc_spec["key"])
        if doc_key not in completed_keys:
            continue
        output_path = document_output_path(paths, doc_key)
        if not read_text_if_exists(output_path).strip():
            continue
        verified_completed_keys.append(doc_key)
        stored = dict(generated_by_key.get(doc_key, {}))
        stored.update(
            {
                "index": stored.get("index", index),
                "key": doc_key,
                "label": stored.get("label", doc_spec["label"]),
                "output_path": stored.get("output_path", str(output_path)),
                "resumed": True,
                "resume_source": stored.get("resume_source", resume_source),
            }
        )
        generated_documents.append(stored)

    last_response_id = str(payload.get("last_response_id") or "").strip() or None
    if last_response_id is None:
        for item in reversed(generated_documents):
            response_id = str(item.get("response_id") or "").strip()
            if response_id:
                last_response_id = response_id
                break

    return {
        "payload": payload,
        "completed_keys": verified_completed_keys,
        "generated_documents": generated_documents,
        "last_response_id": last_response_id,
        "resume_source": resume_source if verified_completed_keys else None,
    }

def adaptation_generation_allowed_files(
    paths: dict[str, Path],
    document_plan: list[dict[str, Any]],
) -> dict[str, Path]:
    return {str(item["key"]): document_output_path(paths, str(item["key"])) for item in document_plan}

def run_adaptation_generation_agent(
    *,
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    paths: dict[str, Path],
    document_plan: list[dict[str, Any]],
    current_docs: dict[str, str],
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
) -> tuple[list[dict[str, Any]], str | None]:
    request_payload = build_adaptation_generation_agent_request(
        manifest=manifest,
        volume_material=volume_material,
        paths=paths,
        document_plan=document_plan,
        current_docs=current_docs,
    )
    allowed_files = adaptation_generation_allowed_files(paths, document_plan)
    loaded_files = build_loaded_file_inventory(volume_material)
    _, source_char_count = build_volume_source_bundle(volume_material)
    request_json = json.dumps(request_payload, ensure_ascii=False, indent=2)
    user_input = stage_shared_prompt + request_json
    print_adaptation_request_context_summary(
        request_label="资料生成 agent 会话",
        volume_material=volume_material,
        loaded_files=loaded_files,
        source_char_count=source_char_count,
        payload=request_payload,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        user_input_char_count=len(user_input),
        allowed_files=allowed_files,
        session_status_line=(
            "会话：OpenCode 风格本地 agent transcript；工具轮会重发本阶段完整上下文和工具历史，"
            "不依赖 provider previous_response_id。"
        ),
    )

    def report_tool(application: Any) -> None:
        if application.applied is None:
            print_progress(f"资料生成工具调用未应用：{application.output}", error=True)
            return
        changed = ", ".join(application.applied.changed_keys) if application.applied.changed_keys else "无内容变化"
        print_progress(f"资料生成工具已应用：{application.tool_name}，变更={changed}。")

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

    missing = [
        key
        for key, path in allowed_files.items()
        if not read_text_if_exists(path).strip()
    ]
    if missing:
        raise llm_runtime.ModelOutputError(
            "资料生成 agent 阶段结束，但以下目标文件仍为空或不存在：" + ", ".join(missing),
            preview=result.submission.summary or result.submission.content_md,
        )

    generated_documents: list[dict[str, Any]] = []
    changed_keys = set(result.changed_keys)
    for index, doc_spec in enumerate(document_plan, start=1):
        doc_key = str(doc_spec["key"])
        generated_documents.append(
            {
                "index": index,
                "key": doc_key,
                "label": doc_spec["label"],
                "response_id": result.response_id,
                "response_ids": result.response_ids,
                "output_path": str(allowed_files[doc_key]),
                "operation_mode": "agent",
                "changed": doc_key in changed_keys,
            }
        )
    return generated_documents, result.response_id

def write_stage_status_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    status: str,
    note: str | None = None,
    total_batches: int | None = None,
    current_batch: int | None = None,
    current_batch_range: str | None = None,
    error_message: str | None = None,
    generated_documents: list[dict[str, Any]] | None = None,
    previous_response_id: str | None = None,
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    loaded_files = build_loaded_file_inventory(volume_material)
    existing_payload = load_stage_manifest_payload(paths["stage_manifest"])
    existing_api_calls = existing_payload.get("api_calls") if isinstance(existing_payload, dict) else None
    existing_generated_keys = existing_payload.get("generated_document_keys") if isinstance(existing_payload, dict) else None
    api_calls = generated_documents if generated_documents is not None else (existing_api_calls or [])
    generated_keys = (
        [item.get("key") for item in generated_documents]
        if generated_documents is not None
        else (existing_generated_keys or [item.get("key") for item in api_calls])
    )

    payload = {
        "generated_at": now_iso(),
        "status": status,
        "note": note,
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "chapter_count": len(volume_material["chapters"]),
        "extra_file_count": len(volume_material["extras"]),
        "total_batches": total_batches,
        "current_batch": current_batch,
        "current_batch_range": current_batch_range,
        "error_message": error_message,
        "api_calls": api_calls,
        "generated_document_keys": generated_keys,
        "last_response_id": previous_response_id,
        "loaded_files": loaded_files,
    }
    write_markdown_data(
        paths["stage_manifest"],
        title=f"Stage Status {volume_material['volume_number']}",
        payload=payload,
        summary_lines=[
            f"status: {status}",
            f"processed_volume: {volume_material['volume_number']}",
            f"chapter_count: {len(volume_material['chapters'])}",
            f"extra_file_count: {len(volume_material['extras'])}",
            f"total_batches: {total_batches if total_batches is not None else 'pending'}",
            f"current_batch: {current_batch if current_batch is not None else 'pending'}",
            f"current_batch_range: {current_batch_range or 'pending'}",
            f"note: {note or 'none'}",
            f"error_message: {error_message or 'none'}",
        ],
    )

def write_response_debug_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    error_message: str,
    preview: str,
    raw_body_text: str = "",
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    payload = {
        "generated_at": now_iso(),
        "processed_volume": volume_material["volume_number"],
        "error_message": error_message,
        "preview": preview,
        "raw_body_text": raw_body_text,
    }
    write_markdown_data(
        paths["response_debug"],
        title=f"Last Response Debug {volume_material['volume_number']}",
        payload=payload,
        summary_lines=[
            f"processed_volume: {volume_material['volume_number']}",
            f"error_message: {error_message}",
            f"preview_length: {len(preview)}",
            f"raw_body_length: {len(raw_body_text)}",
        ],
    )

def document_operation_payload(operation: document_ops.DocumentOperationCallResult) -> dict[str, Any]:
    if operation.mode == "write":
        payload = operation.write_payload or document_ops.DocumentWritePayload()
    elif operation.mode == "edit":
        payload = operation.edit_payload or document_ops.DocumentEditPayload()
    else:
        payload = operation.patch_payload or document_ops.DocumentPatchPayload()
    return {
        "mode": operation.mode,
        "response_id": operation.response_id,
        "output_types": operation.output_types,
        "payload": payload.model_dump(mode="json"),
    }

def build_document_operation_repair_payload(
    *,
    phase_key: str = "adaptation_review_fix_locator_repair",
    role: str = "卷资料审核原地返修定位修正",
    task: str = "修正上一次工具调用中无法定位的 old_text 或 match_text，并重新提交可应用的局部编辑。",
    apply_error: Exception,
    failed_operation: document_ops.DocumentOperationCallResult,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> dict[str, Any]:
    return {
        "document_request": {
            "phase": phase_key,
            "role": role,
            "task": task,
        },
        "previous_tool_call_failed": {
            "error": str(apply_error),
            "failed_operation": document_operation_payload(failed_operation),
        },
        "update_target_files": adaptation_review_target_snapshot(allowed_files),
        "requirements": [
            "只修正上一次工具调用中无法定位的 old_text 或 match_text，不要改成整篇写入。",
            "所有 old_text 或 match_text 必须从 update_target_files.current_content 中逐字复制。",
            "replace、insert_before、insert_after 的定位文本必须在当前文件中唯一匹配。",
            "如果短句无法唯一定位，必须扩大到包含前后连续段落的稳定上下文块。",
            "保留原本的修改意图，只修正定位与必要的新文本，不要额外改写无关内容。",
        ],
        "latest_work_target": {
            "type": "latest_user_input",
            "instruction": (
                "这是本次请求的最新工作目标：修正上一轮无法定位的 old_text 或 match_text。"
                "必须调用 write/edit/patch 文档工具重新提交可应用的局部编辑，"
                "不要调用 submit_workflow_result。"
            ),
            "forbidden_tool": WORKFLOW_SUBMISSION_TOOL_NAME,
        },
    }

def write_document_operation_apply_debug_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    error_message: str,
    operation: document_ops.DocumentOperationCallResult,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> None:
    write_response_debug_snapshot(
        manifest,
        volume_material,
        error_message=error_message,
        preview=operation.preview,
        raw_body_text=json.dumps(
            {
                "failed_operation": document_operation_payload(operation),
                "target_files": adaptation_review_target_snapshot(allowed_files),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

def apply_document_operation_with_repair(
    *,
    client: OpenAI,
    model: str,
    instructions: str,
    shared_prompt: str,
    operation: document_ops.DocumentOperationCallResult,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
    previous_response_id: str | None,
    prompt_cache_key: str | None,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    repair_phase_key: str = "adaptation_review_fix_locator_repair",
    repair_role: str = "卷资料审核原地返修定位修正",
    repair_task: str = "修正上一次工具调用中无法定位的 old_text 或 match_text，并重新提交可应用的局部编辑。",
) -> tuple[document_ops.AppliedDocumentOperation, str | None, list[str]]:
    current_operation = operation
    current_response_id = previous_response_id
    repair_response_ids: list[str] = []

    for repair_attempt in range(MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS + 1):
        try:
            applied = document_ops.apply_document_operation(
                current_operation,
                allowed_files=allowed_files,
            )
            return applied, current_response_id, repair_response_ids
        except ValueError as error:
            if repair_attempt >= MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS:
                write_document_operation_apply_debug_snapshot(
                    manifest,
                    volume_material,
                    error_message=str(error),
                    operation=current_operation,
                    allowed_files=allowed_files,
                )
                raise

            print_progress(
                "模型返回的资料文档编辑定位未能应用："
                f"{error} 正在请求修正定位块（{repair_attempt + 1}/{MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS}）。",
                error=True,
            )
            repair_payload = build_document_operation_repair_payload(
                phase_key=repair_phase_key,
                role=repair_role,
                task=repair_task,
                apply_error=error,
                failed_operation=current_operation,
                allowed_files=allowed_files,
            )
            repair_result = llm_runtime.call_function_tools(
                client,
                model=model,
                instructions=instructions,
                user_input=shared_prompt + json.dumps(repair_payload, ensure_ascii=False, indent=2),
                tool_specs=adaptation_stage_tool_specs(),
                previous_response_id=current_response_id,
                prompt_cache_key=prompt_cache_key,
                retries=DEFAULT_API_RETRIES,
                retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
                tool_choice="auto",
            )
            current_operation = document_operation_result_from_stage_tool_result(repair_result)
            current_response_id = current_operation.response_id
            repair_response_ids.append(str(current_operation.response_id or ""))

    raise RuntimeError("卷资料审核修复定位流程异常结束。")

__all__ = [
    'call_document_operation_response',
    'document_operation_result_from_stage_tool_result',
    'load_stage_manifest_payload',
    'completed_document_keys_from_stage_payload',
    'previous_processed_volume_number',
    'previous_processed_stage_mtime',
    'infer_completed_document_keys_from_file_prefix',
    'load_document_generation_resume_state',
    'adaptation_generation_allowed_files',
    'run_adaptation_generation_agent',
    'write_stage_status_snapshot',
    'write_response_debug_snapshot',
    'document_operation_payload',
    'build_document_operation_repair_payload',
    'write_document_operation_apply_debug_snapshot',
    'apply_document_operation_with_repair',
]
