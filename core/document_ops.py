from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from .files import (
    convert_to_line_ending,
    detect_line_ending,
    find_unique_text_match,
    normalize_line_endings,
    read_text_if_exists,
    replace_text_with_fallbacks,
    write_text_if_changed,
)
import core.responses_runtime as llm_runtime


DOCUMENT_WRITE_TOOL_NAME = "submit_document_writes"
DOCUMENT_WRITE_TOOL_DESCRIPTION = (
    "提交一个或多个完整文档正文。"
    "仅在首次创建文件、文件为空、或确实需要完整新建文档结构时使用。"
    "如果目标文件已经存在且只需要局部修改，优先改用 patch 工具。"
)
DOCUMENT_PATCH_TOOL_NAME = "submit_document_patches"
DOCUMENT_PATCH_TOOL_DESCRIPTION = (
    "提交一个或多个文档的增量 patch 计划。"
    "一次调用可以更新多个文件，每个文件可以包含多个编辑块。"
    "优先保留未变化内容，只对受当前任务影响的局部做替换、插入、追加或前置更新。"
)
DOCUMENT_OPERATION_RULE = (
    "如果目标文件已经存在，请优先使用 patch 工具做增量更新，保留未变化内容。"
    "只有在文件缺失、文件为空、或确实需要整体新建结构时，才使用整篇写入工具。"
)


class DocumentWriteFile(BaseModel):
    file_key: str = Field(..., description="目标文件的逻辑 key。必须来自输入中允许写入的 file_key。")
    content: str = Field(..., description="目标文件的完整正文内容。")


class DocumentWritePayload(BaseModel):
    files: list[DocumentWriteFile] = Field(default_factory=list, description="需要整篇写入的文件列表。")
    note: str = Field("", description="本次写入的简短说明。")


class DocumentPatchEdit(BaseModel):
    action: Literal["replace", "insert_before", "insert_after", "append", "prepend"] = Field(
        ...,
        description="编辑动作类型。",
    )
    match_text: str = Field(
        "",
        description="replace/insert_before/insert_after 时用于定位的原文片段；append/prepend 留空。",
    )
    new_text: str = Field(..., description="替换或插入的新内容。")
    replace_all: bool = Field(False, description="仅 replace 动作可用；为 true 时替换所有匹配。")
    description: str = Field("", description="当前编辑块的目的说明。")


class DocumentPatchFile(BaseModel):
    file_key: str = Field(..., description="目标文件的逻辑 key。必须来自输入中允许写入的 file_key。")
    edits: list[DocumentPatchEdit] = Field(default_factory=list, description="按顺序执行的编辑块。")


class DocumentPatchPayload(BaseModel):
    files: list[DocumentPatchFile] = Field(default_factory=list, description="需要 patch 的文件列表。")
    note: str = Field("", description="本次 patch 的简短说明。")


@dataclass
class DocumentOperationCallResult:
    mode: Literal["write", "patch"]
    response_id: str | None
    status: str
    output_types: list[str]
    preview: str
    raw_body_text: str
    raw_json: Any
    write_payload: DocumentWritePayload | None = None
    patch_payload: DocumentPatchPayload | None = None


@dataclass
class AppliedDocumentFile:
    file_key: str
    path: Path
    mode: Literal["write", "patch"]
    emitted: bool
    changed: bool
    edit_count: int


@dataclass
class AppliedDocumentOperation:
    mode: Literal["write", "patch"]
    files: list[AppliedDocumentFile]

    @property
    def emitted_keys(self) -> list[str]:
        return [item.file_key for item in self.files if item.emitted]

    @property
    def changed_keys(self) -> list[str]:
        return [item.file_key for item in self.files if item.changed]


def document_tool_specs() -> list[llm_runtime.FunctionToolSpec[Any]]:
    return [
        llm_runtime.FunctionToolSpec(
            model=DocumentWritePayload,
            name=DOCUMENT_WRITE_TOOL_NAME,
            description=DOCUMENT_WRITE_TOOL_DESCRIPTION,
        ),
        llm_runtime.FunctionToolSpec(
            model=DocumentPatchPayload,
            name=DOCUMENT_PATCH_TOOL_NAME,
            description=DOCUMENT_PATCH_TOOL_DESCRIPTION,
        ),
    ]


def call_document_operation_tools(
    client: OpenAI,
    *,
    model: str,
    instructions: str,
    user_input: str,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
    retries: int = llm_runtime.DEFAULT_API_RETRIES,
    retry_delay_seconds: int = llm_runtime.DEFAULT_RETRY_DELAY_SECONDS,
) -> DocumentOperationCallResult:
    result = llm_runtime.call_function_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_specs=document_tool_specs(),
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
        tool_choice="auto",
    )

    if result.tool_name == DOCUMENT_WRITE_TOOL_NAME:
        return DocumentOperationCallResult(
            mode="write",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            write_payload=DocumentWritePayload.model_validate(result.parsed),
        )
    if result.tool_name == DOCUMENT_PATCH_TOOL_NAME:
        return DocumentOperationCallResult(
            mode="patch",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            patch_payload=DocumentPatchPayload.model_validate(result.parsed),
        )
    raise llm_runtime.ModelOutputError(f"模型调用了未支持的文档工具：{result.tool_name}")


def _apply_insert_before(content: str, match_text: str, new_text: str) -> str:
    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content)
    normalized_match = find_unique_text_match(normalized_content, match_text)
    normalized_new = normalize_line_endings(new_text)
    updated = normalized_content.replace(normalized_match, normalized_new + normalized_match, 1)
    return convert_to_line_ending(updated, original_ending)


def _apply_insert_after(content: str, match_text: str, new_text: str) -> str:
    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content)
    normalized_match = find_unique_text_match(normalized_content, match_text)
    normalized_new = normalize_line_endings(new_text)
    updated = normalized_content.replace(normalized_match, normalized_match + normalized_new, 1)
    return convert_to_line_ending(updated, original_ending)


def _apply_append(content: str, new_text: str) -> str:
    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content).rstrip("\n")
    normalized_new = normalize_line_endings(new_text).strip("\n")
    if not normalized_new:
        return content
    if not normalized_content:
        return convert_to_line_ending(normalized_new, original_ending)
    return convert_to_line_ending(normalized_content + "\n\n" + normalized_new, original_ending)


def _apply_prepend(content: str, new_text: str) -> str:
    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content).lstrip("\n")
    normalized_new = normalize_line_endings(new_text).strip("\n")
    if not normalized_new:
        return content
    if not normalized_content:
        return convert_to_line_ending(normalized_new, original_ending)
    return convert_to_line_ending(normalized_new + "\n\n" + normalized_content, original_ending)


def apply_patch_edits_to_text(content: str, edits: list[DocumentPatchEdit]) -> str:
    updated = content
    for edit in edits:
        if edit.action == "replace":
            updated = replace_text_with_fallbacks(
                updated,
                edit.match_text,
                edit.new_text,
                replace_all=edit.replace_all,
            )
            continue
        if edit.action == "insert_before":
            updated = _apply_insert_before(updated, edit.match_text, edit.new_text)
            continue
        if edit.action == "insert_after":
            updated = _apply_insert_after(updated, edit.match_text, edit.new_text)
            continue
        if edit.action == "append":
            updated = _apply_append(updated, edit.new_text)
            continue
        if edit.action == "prepend":
            updated = _apply_prepend(updated, edit.new_text)
            continue
        raise ValueError(f"不支持的 patch 动作：{edit.action}")
    return updated


def apply_document_operation(
    operation: DocumentOperationCallResult,
    *,
    allowed_files: dict[str, Path],
) -> AppliedDocumentOperation:
    file_results: list[AppliedDocumentFile] = []

    if operation.mode == "write":
        payload = operation.write_payload or DocumentWritePayload()
        for item in payload.files:
            if item.file_key not in allowed_files:
                raise ValueError(f"整篇写入返回了未授权文件：{item.file_key}")
            path = allowed_files[item.file_key]
            changed = write_text_if_changed(path, item.content)
            file_results.append(
                AppliedDocumentFile(
                    file_key=item.file_key,
                    path=path,
                    mode="write",
                    emitted=True,
                    changed=changed,
                    edit_count=1,
                )
            )
        return AppliedDocumentOperation(mode="write", files=file_results)

    payload = operation.patch_payload or DocumentPatchPayload()
    for item in payload.files:
        if item.file_key not in allowed_files:
            raise ValueError(f"Patch 返回了未授权文件：{item.file_key}")
        path = allowed_files[item.file_key]
        current = read_text_if_exists(path)
        updated = apply_patch_edits_to_text(current, item.edits)
        changed = write_text_if_changed(path, updated)
        file_results.append(
            AppliedDocumentFile(
                file_key=item.file_key,
                path=path,
                mode="patch",
                emitted=True,
                changed=changed,
                edit_count=len(item.edits),
            )
        )
    return AppliedDocumentOperation(mode="patch", files=file_results)
