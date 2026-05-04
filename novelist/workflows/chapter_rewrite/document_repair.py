from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .models import chapter_rewrite_stage_tool_specs, document_operation_result_from_stage_tool_result


def write_response_debug_snapshot(
    debug_path: Path,
    *,
    error_message: str,
    preview: str,
    raw_body_text: str = "",
) -> None:
    write_markdown_data(
        debug_path,
        title="Last Response Debug",
        payload={
            "generated_at": now_iso(),
            "error_message": error_message,
            "preview": preview,
            "raw_body_text": raw_body_text,
        },
        summary_lines=[
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

def document_operation_target_snapshot(
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for file_key, target in allowed_files.items():
        path = target.path if isinstance(target, document_ops.DocumentTarget) else target
        current_content = read_text_if_exists(path).strip()
        snapshots.append(
            {
                "file_key": file_key,
                "file_name": path.name,
                "file_path": str(path),
                "exists": path.exists(),
                "current_char_count": len(current_content),
                "current_content": current_content,
            }
        )
    return snapshots

def build_document_operation_repair_payload(
    *,
    phase_key: str,
    role: str,
    task: str,
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
        "update_target_files": document_operation_target_snapshot(allowed_files),
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
    debug_path: Path,
    *,
    error_message: str,
    operation: document_ops.DocumentOperationCallResult,
    allowed_files: dict[str, Path | document_ops.DocumentTarget],
) -> None:
    write_response_debug_snapshot(
        debug_path,
        error_message=error_message,
        preview=operation.preview,
        raw_body_text=json.dumps(
            {
                "failed_operation": document_operation_payload(operation),
                "target_files": document_operation_target_snapshot(allowed_files),
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
    phase_key: str,
    repair_role: str,
    repair_task: str,
    debug_path: Path,
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
                    debug_path,
                    error_message=str(error),
                    operation=current_operation,
                    allowed_files=allowed_files,
                )
                raise

            print_progress(
                "模型返回的编辑定位未能应用："
                f"{error} 正在请求修正定位块（{repair_attempt + 1}/{MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS}）。",
                error=True,
            )
            repair_payload = build_document_operation_repair_payload(
                phase_key=phase_key,
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
                tool_specs=chapter_rewrite_stage_tool_specs(),
                previous_response_id=current_response_id,
                prompt_cache_key=prompt_cache_key,
                tool_choice="auto",
            )
            current_operation = document_operation_result_from_stage_tool_result(repair_result)
            current_response_id = current_operation.response_id
            repair_response_ids.append(str(current_operation.response_id or ""))

    raise RuntimeError("目标文件编辑修正流程异常结束。")

def load_chapter_stage_manifest_payload(stage_manifest_path: Path) -> dict[str, Any]:
    text = read_text_if_exists(stage_manifest_path)
    if not text.strip():
        return {}
    try:
        return extract_json_payload(text)
    except Exception:
        return {}

def latest_chapter_stage_response_id(stage_manifest_path: Path) -> str | None:
    payload = load_chapter_stage_manifest_payload(stage_manifest_path)
    last_response_id = str(payload.get("last_response_id") or "").strip()
    if last_response_id:
        return last_response_id
    response_ids = payload.get("response_ids")
    if isinstance(response_ids, list):
        for response_id in reversed(response_ids):
            value = str(response_id or "").strip()
            if value:
                return value
    return None

def write_chapter_stage_snapshot(
    stage_manifest_path: Path,
    *,
    volume_number: str,
    chapter_number: str,
    status: str,
    note: str,
    attempt: int,
    last_phase: str | None = None,
    response_ids: list[str] | None = None,
) -> None:
    existing_payload = load_chapter_stage_manifest_payload(stage_manifest_path)
    if response_ids is None and isinstance(existing_payload.get("response_ids"), list):
        current_response_ids = list(existing_payload["response_ids"])
    else:
        current_response_ids = response_ids or []
    last_response_id = next(
        (str(response_id or "").strip() for response_id in reversed(current_response_ids) if str(response_id or "").strip()),
        None,
    )
    write_markdown_data(
        stage_manifest_path,
        title=f"Chapter Stage Manifest {volume_number}-{chapter_number}",
        payload={
            "generated_at": now_iso(),
            "volume_number": volume_number,
            "chapter_number": chapter_number,
            "status": status,
            "note": note,
            "attempt": attempt,
            "last_phase": last_phase,
            "response_ids": current_response_ids,
            "last_response_id": last_response_id,
        },
        summary_lines=[
            f"volume_number: {volume_number}",
            f"chapter_number: {chapter_number}",
            f"status: {status}",
            f"attempt: {attempt}",
            f"last_phase: {last_phase or 'none'}",
            f"last_response_id: {last_response_id or 'none'}",
            f"note: {note}",
        ],
    )

def write_volume_stage_snapshot(
    stage_manifest_path: Path,
    *,
    volume_number: str,
    status: str,
    note: str,
    attempt: int,
    response_id: str | None = None,
) -> None:
    write_markdown_data(
        stage_manifest_path,
        title=f"Volume Rewrite Manifest {volume_number}",
        payload={
            "generated_at": now_iso(),
            "volume_number": volume_number,
            "status": status,
            "note": note,
            "attempt": attempt,
            "response_id": response_id,
        },
        summary_lines=[
            f"volume_number: {volume_number}",
            f"status: {status}",
            f"attempt: {attempt}",
            f"response_id: {response_id or 'none'}",
            f"note: {note}",
        ],
    )

def doc_label_for_key(doc_key: str) -> str:
    if doc_key == "group_review":
        return f"{FIVE_CHAPTER_REVIEW_NAME}文档"
    return (
        GLOBAL_DOC_LABELS.get(doc_key)
        or VOLUME_DOC_LABELS.get(doc_key)
        or GROUP_DOC_LABELS.get(doc_key)
        or CHAPTER_DOC_LABELS.get(doc_key)
        or doc_key
    )

def write_artifact(path: Path, content: str) -> bool:
    return write_text_if_changed(path, content)

__all__ = [
    'write_response_debug_snapshot',
    'document_operation_payload',
    'document_operation_target_snapshot',
    'build_document_operation_repair_payload',
    'write_document_operation_apply_debug_snapshot',
    'apply_document_operation_with_repair',
    'load_chapter_stage_manifest_payload',
    'latest_chapter_stage_response_id',
    'write_chapter_stage_snapshot',
    'write_volume_stage_snapshot',
    'doc_label_for_key',
    'write_artifact',
]
