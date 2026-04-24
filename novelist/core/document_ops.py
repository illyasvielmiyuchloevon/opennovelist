from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .files import (
    convert_to_line_ending,
    detect_line_ending,
    find_unique_text_match,
    normalize_line_endings,
    read_text_if_exists,
    replace_text_with_fallbacks,
    write_text_if_changed,
)
from . import responses_runtime as llm_runtime


DOCUMENT_WRITE_TOOL_NAME = "submit_document_writes"
DOCUMENT_WRITE_TOOL_DESCRIPTION = (
    "提交一个或多个完整目标文件正文，目标文件可以是章节正文 txt、Markdown 状态文档或其他工作流文件。"
    "仅在首次创建文件、文件为空、或确实需要完整新建文档结构时使用。"
    "如果目标文件已经存在且只需要局部修改，优先改用 patch 工具。"
)
DOCUMENT_EDIT_TOOL_NAME = "submit_document_edits"
DOCUMENT_EDIT_TOOL_DESCRIPTION = (
    "提交一个或多个目标文件的精确编辑计划，目标文件可以是章节正文 txt、Markdown 状态文档或其他工作流文件。"
    "适用于已有文件中的某一段、某一条记录、某几行或某个已有块的局部修改。"
    "每个文件可以包含多个顺序执行的 old_text -> new_text 编辑。"
    "如果只是修改已有内容本身，优先使用 edit 工具，而不是大块 patch 替换。"
)
DOCUMENT_PATCH_TOOL_NAME = "submit_document_patches"
DOCUMENT_PATCH_TOOL_DESCRIPTION = (
    "提交一个或多个目标文件的增量 patch 计划，目标文件可以是章节正文 txt、Markdown 状态文档或其他工作流文件。"
    "一次调用可以更新多个文件，每个文件可以包含多个编辑块。"
    "优先保留未变化内容，只对受当前任务影响的局部做替换、插入、追加或前置更新。"
    "对于带 Markdown 标题结构的文档，优先使用按标题锚点的局部编辑动作，而不是整段替换。"
    "如果只需要在某一段、某条记录或某个小块后面补充内容，可以直接使用 insert_after。"
)
DOCUMENT_OPERATION_RULE = (
    "如果目标文件已经存在，请优先使用 patch 工具做增量更新，保留未变化内容。"
    "如果只是修改已有段落、已有记录、已有几行内容，优先使用 edit 工具。"
    "对于带固定标题或分节结构的文档，优先按标题锚点编辑受影响的小节。"
    "如果只是延续某一段、某条记录或某个小块，优先使用 insert_after 直接追加在该块后面。"
    "只有在文件缺失、文件为空、或确实需要整体新建结构时，才使用整篇写入工具。"
)


class DocumentWriteFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field("", description="目标文件的逻辑 key；可选。如果提供，必须来自输入中允许写入的 file_key。")
    file_path: str = Field(
        "",
        validation_alias=AliasChoices("file_path", "filePath"),
        description="目标文件路径；可选。可以直接使用输入中 update_target_files 的 file_path。",
    )
    content: str = Field(..., description="目标文件的完整正文内容。")


class DocumentWritePayload(BaseModel):
    files: list[DocumentWriteFile] = Field(default_factory=list, description="需要整篇写入的文件列表。")
    note: str = Field("", description="本次写入的简短说明。")


class DocumentEditEdit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    old_text: str = Field(..., validation_alias=AliasChoices("old_text", "oldString"), description="需要替换的原文片段。")
    new_text: str = Field(..., validation_alias=AliasChoices("new_text", "newString"), description="替换后的新内容。")
    replace_all: bool = Field(
        False,
        validation_alias=AliasChoices("replace_all", "replaceAll"),
        description="是否替换该文件内所有匹配。默认 false。",
    )
    description: str = Field("", description="当前编辑块的目的说明。")


class DocumentEditFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field("", description="目标文件的逻辑 key；可选。如果提供，必须来自输入中允许写入的 file_key。")
    file_path: str = Field(
        "",
        validation_alias=AliasChoices("file_path", "filePath"),
        description="目标文件路径；可选。可以直接使用输入中 update_target_files 的 file_path。",
    )
    edits: list[DocumentEditEdit] = Field(default_factory=list, description="按顺序执行的编辑块。")


class DocumentEditPayload(BaseModel):
    files: list[DocumentEditFile] = Field(default_factory=list, description="需要进行精确编辑的文件列表。")
    note: str = Field("", description="本次编辑的简短说明。")


class DocumentPatchEdit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action: Literal[
        "replace",
        "insert_before",
        "insert_after",
        "append",
        "prepend",
        "append_under_heading",
        "replace_section_body",
    ] = Field(
        ...,
        description="编辑动作类型。",
    )
    match_text: str = Field(
        "",
        validation_alias=AliasChoices("match_text", "matchText", "oldString"),
        description=(
            "replace/insert_before/insert_after 时用于定位原文片段；"
            "append_under_heading/replace_section_body 时用于定位 Markdown 标题；"
            "append/prepend 留空。"
        ),
    )
    new_text: str = Field(..., validation_alias=AliasChoices("new_text", "newString"), description="替换或插入的新内容。")
    replace_all: bool = Field(
        False,
        validation_alias=AliasChoices("replace_all", "replaceAll"),
        description="仅 replace 动作可用；为 true 时替换所有匹配。",
    )
    description: str = Field("", description="当前编辑块的目的说明。")


class DocumentPatchFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field("", description="目标文件的逻辑 key；可选。如果提供，必须来自输入中允许写入的 file_key。")
    file_path: str = Field(
        "",
        validation_alias=AliasChoices("file_path", "filePath"),
        description="目标文件路径；可选。可以直接使用输入中 update_target_files 的 file_path。",
    )
    edits: list[DocumentPatchEdit] = Field(default_factory=list, description="按顺序执行的编辑块。")


class DocumentPatchPayload(BaseModel):
    files: list[DocumentPatchFile] = Field(default_factory=list, description="需要 patch 的文件列表。")
    note: str = Field("", description="本次 patch 的简短说明。")


@dataclass
class DocumentOperationCallResult:
    mode: Literal["write", "edit", "patch"]
    response_id: str | None
    status: str
    output_types: list[str]
    preview: str
    raw_body_text: str
    raw_json: Any
    write_payload: DocumentWritePayload | None = None
    edit_payload: DocumentEditPayload | None = None
    patch_payload: DocumentPatchPayload | None = None


@dataclass
class AppliedDocumentFile:
    file_key: str
    path: Path
    mode: Literal["write", "edit", "patch"]
    emitted: bool
    changed: bool
    edit_count: int


@dataclass
class AppliedDocumentOperation:
    mode: Literal["write", "edit", "patch"]
    files: list[AppliedDocumentFile]

    @property
    def emitted_keys(self) -> list[str]:
        return [item.file_key for item in self.files if item.emitted]

    @property
    def changed_keys(self) -> list[str]:
        return [item.file_key for item in self.files if item.changed]


@dataclass(frozen=True)
class DocumentTarget:
    path: Path
    allow_write_on_existing: bool = False


def _same_resolved_path(left: Path, right: Path) -> bool:
    return str(left.expanduser().resolve()).casefold() == str(right.expanduser().resolve()).casefold()


def _resolve_document_target(
    *,
    file_key: str,
    file_path: str,
    normalized_targets: dict[str, DocumentTarget],
    operation_label: str,
) -> tuple[str, DocumentTarget]:
    cleaned_key = file_key.strip()
    cleaned_path = file_path.strip().strip('"').strip("'")

    if cleaned_key:
        if cleaned_key not in normalized_targets:
            raise ValueError(f"{operation_label} 返回了未授权文件：{cleaned_key}")
        target = normalized_targets[cleaned_key]
        if cleaned_path and not _same_resolved_path(Path(cleaned_path), target.path):
            raise ValueError(f"{operation_label} 返回的 file_key 与 file_path 不一致：{cleaned_key} -> {cleaned_path}")
        return cleaned_key, target

    if cleaned_path:
        requested = Path(cleaned_path)
        matches = [
            (candidate_key, candidate_target)
            for candidate_key, candidate_target in normalized_targets.items()
            if _same_resolved_path(requested, candidate_target.path)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"{operation_label} 返回的 file_path 匹配到多个授权文件：{cleaned_path}")
        if not normalized_targets:
            return cleaned_path, DocumentTarget(path=requested.expanduser().resolve())
        raise ValueError(f"{operation_label} 返回了未授权文件路径：{cleaned_path}")

    raise ValueError(f"{operation_label} 必须提供 file_key 或 file_path。")


def document_tool_specs() -> list[llm_runtime.FunctionToolSpec[Any]]:
    return [
        llm_runtime.FunctionToolSpec(
            model=DocumentWritePayload,
            name=DOCUMENT_WRITE_TOOL_NAME,
            description=DOCUMENT_WRITE_TOOL_DESCRIPTION,
        ),
        llm_runtime.FunctionToolSpec(
            model=DocumentEditPayload,
            name=DOCUMENT_EDIT_TOOL_NAME,
            description=DOCUMENT_EDIT_TOOL_DESCRIPTION,
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
    if result.tool_name == DOCUMENT_EDIT_TOOL_NAME:
        return DocumentOperationCallResult(
            mode="edit",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            edit_payload=DocumentEditPayload.model_validate(result.parsed),
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


def _normalize_heading_key(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("#"):
        stripped = stripped.lstrip("#").strip()
    return stripped


def _find_heading_index(lines: list[str], match_text: str) -> tuple[int, int]:
    target_line = match_text.strip()
    target_key = _normalize_heading_key(match_text)
    candidates: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading_level = len(stripped) - len(stripped.lstrip("#"))
        if heading_level <= 0 or heading_level > 6:
            continue
        if stripped == target_line or _normalize_heading_key(stripped) == target_key:
            candidates.append((index, heading_level))
    if not candidates:
        raise ValueError("未找到用于定位的标题，请提供更稳定的标题锚点。")
    if len(candidates) > 1:
        raise ValueError("标题锚点匹配到多个位置，请补充更具体的标题。")
    return candidates[0]


def _section_body_bounds(lines: list[str], heading_index: int, heading_level: int) -> tuple[int, int]:
    body_start = heading_index + 1
    body_end = len(lines)
    for index in range(body_start, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if 0 < level <= heading_level:
            body_end = index
            break
    return body_start, body_end


def _apply_append_under_heading(content: str, match_text: str, new_text: str) -> str:
    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content)
    lines = normalized_content.split("\n")
    heading_index, heading_level = _find_heading_index(lines, match_text)
    body_start, body_end = _section_body_bounds(lines, heading_index, heading_level)
    existing_body = "\n".join(lines[body_start:body_end]).strip("\n")
    normalized_new = normalize_line_endings(new_text).strip("\n")
    if not normalized_new:
        return content
    if existing_body:
        updated_body = existing_body + "\n\n" + normalized_new
    else:
        updated_body = normalized_new
    updated_lines = lines[:body_start] + updated_body.split("\n") + lines[body_end:]
    return convert_to_line_ending("\n".join(updated_lines), original_ending)


def _apply_replace_section_body(content: str, match_text: str, new_text: str) -> str:
    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content)
    lines = normalized_content.split("\n")
    heading_index, heading_level = _find_heading_index(lines, match_text)
    body_start, body_end = _section_body_bounds(lines, heading_index, heading_level)
    normalized_new = normalize_line_endings(new_text).strip("\n")
    replacement_lines = normalized_new.split("\n") if normalized_new else []
    updated_lines = lines[:body_start] + replacement_lines + lines[body_end:]
    return convert_to_line_ending("\n".join(updated_lines), original_ending)


def apply_patch_edits_to_text(content: str, edits: list[DocumentPatchEdit]) -> str:
    updated = content
    for edit in edits:
        if edit.action == "replace":
            if normalize_line_endings(edit.match_text) == normalize_line_endings(edit.new_text):
                continue
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
        if edit.action == "append_under_heading":
            updated = _apply_append_under_heading(updated, edit.match_text, edit.new_text)
            continue
        if edit.action == "replace_section_body":
            updated = _apply_replace_section_body(updated, edit.match_text, edit.new_text)
            continue
        raise ValueError(f"不支持的 patch 动作：{edit.action}")
    return updated


def apply_document_operation(
    operation: DocumentOperationCallResult,
    *,
    allowed_files: dict[str, Path | DocumentTarget],
) -> AppliedDocumentOperation:
    normalized_targets: dict[str, DocumentTarget] = {}
    for file_key, target in allowed_files.items():
        if isinstance(target, DocumentTarget):
            normalized_targets[file_key] = target
        else:
            normalized_targets[file_key] = DocumentTarget(path=target)

    file_results: list[AppliedDocumentFile] = []

    if operation.mode == "write":
        payload = operation.write_payload or DocumentWritePayload()
        for item in payload.files:
            resolved_key, target = _resolve_document_target(
                file_key=item.file_key,
                file_path=item.file_path,
                normalized_targets=normalized_targets,
                operation_label="整篇写入",
            )
            path = target.path
            if path.exists() and read_text_if_exists(path).strip() and not target.allow_write_on_existing:
                raise ValueError(f"目标文件已存在，禁止整篇写入：{resolved_key}")
            changed = write_text_if_changed(path, item.content)
            file_results.append(
                AppliedDocumentFile(
                    file_key=resolved_key,
                    path=path,
                    mode="write",
                    emitted=True,
                    changed=changed,
                    edit_count=1,
                )
            )
        return AppliedDocumentOperation(mode="write", files=file_results)

    if operation.mode == "edit":
        payload = operation.edit_payload or DocumentEditPayload()
        for item in payload.files:
            resolved_key, target = _resolve_document_target(
                file_key=item.file_key,
                file_path=item.file_path,
                normalized_targets=normalized_targets,
                operation_label="Edit",
            )
            path = target.path
            current = read_text_if_exists(path)
            updated = current
            for edit in item.edits:
                updated = replace_text_with_fallbacks(
                    updated,
                    edit.old_text,
                    edit.new_text,
                    replace_all=edit.replace_all,
                )
            changed = write_text_if_changed(path, updated)
            file_results.append(
                AppliedDocumentFile(
                    file_key=resolved_key,
                    path=path,
                    mode="edit",
                    emitted=True,
                    changed=changed,
                    edit_count=len(item.edits),
                )
            )
        return AppliedDocumentOperation(mode="edit", files=file_results)

    payload = operation.patch_payload or DocumentPatchPayload()
    for item in payload.files:
        resolved_key, target = _resolve_document_target(
            file_key=item.file_key,
            file_path=item.file_path,
            normalized_targets=normalized_targets,
            operation_label="Patch",
        )
        path = target.path
        current = read_text_if_exists(path)
        updated = apply_patch_edits_to_text(current, item.edits)
        changed = write_text_if_changed(path, updated)
        file_results.append(
            AppliedDocumentFile(
                file_key=resolved_key,
                path=path,
                mode="patch",
                emitted=True,
                changed=changed,
                edit_count=len(item.edits),
            )
        )
    return AppliedDocumentOperation(mode="patch", files=file_results)
