from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

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


DOCUMENT_WRITE_TOOL_NAME = "write"
DOCUMENT_WRITE_TOOL_LEGACY_ALIASES: tuple[str, ...] = ()
DOCUMENT_WRITE_TOOL_DESCRIPTION = (
    "写入单个目标文件完整内容。参数固定为：filePath + content。"
    "仅在首次创建文件、文件为空、或确实需要完整新建文档结构时使用。"
    "如果目标文件已经存在且只需要修改已有内容，优先改用 edit 工具。"
)
DOCUMENT_EDIT_TOOL_NAME = "edit"
DOCUMENT_EDIT_TOOL_LEGACY_ALIASES: tuple[str, ...] = ()
DOCUMENT_EDIT_TOOL_DESCRIPTION = (
    "精确编辑单个已存在文件。参数固定为：filePath + oldString + newString + replaceAll。"
    "适用于已有文件中的某一段、某一条记录、某几行或某个已有块的局部修改。"
    "如果只是修改已有内容本身，优先使用 edit 工具。"
)
DOCUMENT_PATCH_TOOL_NAME = "apply_patch"
DOCUMENT_PATCH_TOOL_COMPATIBLE_ALIASES: tuple[str, ...] = ()
DOCUMENT_PATCH_TOOL_DESCRIPTION = (
    "对一个或多个目标文件提交 patchText。"
    "patchText 需使用 *** Begin Patch / *** End Patch 格式。"
)
DOCUMENT_OPERATION_RULE = (
    "请按本次修改意图选择工具，而不是按文件是否已存在固定选择工具。"
    "修改已有句子、段落、记录、名词、术语、时间线表述或已有小块正文时，优先使用 edit 工具。"
    "插入新段落、追加新条目、或需要跨文件结构化变更时，使用 apply_patch 工具并提供 patchText。"
    "批量清理参考源残留名、人名、地名、术语时，优先使用 edit 工具并按需要使用 replace_all。"
    "只有在文件缺失、文件为空、或确实需要整体新建结构时，才使用整篇写入工具。"
)


def _pick_file_path(value: dict[str, Any]) -> str:
    file_path = value.get("file_path", value.get("filePath", ""))
    if isinstance(file_path, str) and file_path.strip():
        return file_path.strip()
    file_name = value.get("file_name", value.get("fileName", ""))
    if isinstance(file_name, str):
        return file_name.strip()
    return ""


def _pick_file_key(value: dict[str, Any]) -> str:
    file_key = value.get("file_key", value.get("fileKey", ""))
    if isinstance(file_key, str):
        return file_key.strip()
    return ""


def _as_entry_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    return None


class DocumentWriteToolArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_path: str = Field(
        ...,
        validation_alias=AliasChoices("filePath", "file_path", "file_name", "fileName"),
        serialization_alias="filePath",
        description="目标文件路径（filePath）。",
    )
    content: str = Field(..., description="目标文件完整内容。")

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("files"), list):
            for entry in value.get("files", []):
                parsed_entry = _as_entry_dict(entry)
                if parsed_entry is None:
                    continue
                file_path = _pick_file_path(parsed_entry)
                content = parsed_entry.get("content")
                if file_path and content is not None:
                    return {
                        "filePath": file_path,
                        "content": content,
                    }
            return value
        file_path = _pick_file_path(value)
        content = value.get("content")
        if file_path and content is not None:
            return {
                "filePath": file_path,
                "content": content,
            }
        return value


class DocumentEditToolArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_path: str = Field(
        ...,
        validation_alias=AliasChoices("filePath", "file_path", "file_name", "fileName"),
        serialization_alias="filePath",
        description="目标文件路径（filePath）。",
    )
    old_text: str = Field(
        ...,
        validation_alias=AliasChoices("oldString", "old_text"),
        serialization_alias="oldString",
        description="需要替换的原文片段（oldString）。",
    )
    new_text: str = Field(
        ...,
        validation_alias=AliasChoices("newString", "new_text"),
        serialization_alias="newString",
        description="替换后的新内容（newString）。",
    )
    replace_all: bool = Field(
        False,
        validation_alias=AliasChoices("replaceAll", "replace_all"),
        serialization_alias="replaceAll",
        description="是否替换该文件中的所有匹配（replaceAll）。",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("files"), list):
            for entry in value.get("files", []):
                parsed_entry = _as_entry_dict(entry)
                if parsed_entry is None:
                    continue
                file_path = _pick_file_path(parsed_entry)
                if not file_path:
                    continue
                edits = parsed_entry.get("edits")
                if isinstance(edits, list):
                    for edit in edits:
                        parsed_edit = _as_entry_dict(edit)
                        if parsed_edit is None:
                            continue
                        old_text = parsed_edit.get("old_text", parsed_edit.get("oldString"))
                        new_text = parsed_edit.get("new_text", parsed_edit.get("newString"))
                        if old_text is None or new_text is None:
                            continue
                        return {
                            "filePath": file_path,
                            "oldString": old_text,
                            "newString": new_text,
                            "replaceAll": parsed_edit.get("replace_all", parsed_edit.get("replaceAll", False)),
                        }
                old_text = parsed_entry.get("old_text", parsed_entry.get("oldString"))
                new_text = parsed_entry.get("new_text", parsed_entry.get("newString"))
                if old_text is None or new_text is None:
                    continue
                return {
                    "filePath": file_path,
                    "oldString": old_text,
                    "newString": new_text,
                    "replaceAll": parsed_entry.get("replace_all", parsed_entry.get("replaceAll", False)),
                }
            return value
        file_path = _pick_file_path(value)
        old_text = value.get("old_text", value.get("oldString"))
        new_text = value.get("new_text", value.get("newString"))
        if file_path and old_text is not None and new_text is not None:
            return {
                "filePath": file_path,
                "oldString": old_text,
                "newString": new_text,
                "replaceAll": value.get("replace_all", value.get("replaceAll", False)),
            }
        return value


class DocumentApplyPatchToolArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    patch_text: str = Field(
        ...,
        validation_alias=AliasChoices("patchText", "patch_text"),
        serialization_alias="patchText",
        description="完整 patch 文本（patchText），必须含 *** Begin Patch / *** End Patch。",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        patch_text = value.get("patchText", value.get("patch_text"))
        if isinstance(patch_text, str):
            return {"patchText": patch_text}
        if isinstance(value.get("files"), list):
            raise ValueError("apply_patch 必须提供 patchText，不能再使用 files[].edits。")
        return value


class DocumentWriteFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field("", description="目标文件的逻辑 key；可选。如果提供，必须来自输入中允许写入的 file_key。")
    file_path: str = Field(
        "",
        validation_alias=AliasChoices("file_path", "filePath", "file_name", "fileName"),
        description="目标文件路径；优先使用 filePath。",
    )
    file_name: str = Field("", validation_alias=AliasChoices("file_name", "fileName"), description="可选，兼容字段。")
    content: str = Field(..., description="目标文件的完整正文内容。")


class DocumentWritePayload(BaseModel):
    files: list[DocumentWriteFile] = Field(default_factory=list, description="需要整篇写入的文件列表。")
    note: str = Field("", description="本次写入的简短说明。")

    @model_validator(mode="before")
    @classmethod
    def wrap_single_file_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("files"), list):
            normalized_files: list[dict[str, Any]] = []
            for entry in value.get("files", []):
                parsed_entry = _as_entry_dict(entry)
                if parsed_entry is None:
                    continue
                file_path = _pick_file_path(parsed_entry)
                file_key = _pick_file_key(parsed_entry)
                content = parsed_entry.get("content")
                if content is None or (not file_path and not file_key):
                    continue
                normalized_files.append(
                    {
                        "file_key": file_key,
                        "file_path": file_path,
                        "content": content,
                    }
                )
            return {"files": normalized_files, "note": value.get("note", "")}
        file_path = _pick_file_path(value)
        content = value.get("content")
        file_key = _pick_file_key(value)
        if content is None or (not file_path and not file_key):
            return value
        return {
            "files": [
                {
                    "file_key": file_key,
                    "file_path": file_path,
                    "content": content,
                }
            ],
            "note": value.get("note", ""),
        }


class DocumentEditEdit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    old_text: str = Field(..., validation_alias=AliasChoices("old_text", "oldString"), description="需要替换的原文片段。")
    new_text: str = Field(..., validation_alias=AliasChoices("new_text", "newString"), description="替换后的新内容。")
    replace_all: bool = Field(
        False,
        validation_alias=AliasChoices("replace_all", "replaceAll"),
        description="是否替换该文件内所有匹配。默认 false；为 true 且未匹配时视为无变化。",
    )
    description: str = Field("", description="当前编辑块的目的说明。")


class DocumentEditFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field("", description="目标文件的逻辑 key；可选。如果提供，必须来自输入中允许写入的 file_key。")
    file_path: str = Field(
        "",
        validation_alias=AliasChoices("file_path", "filePath", "file_name", "fileName"),
        description="目标文件路径；优先使用 filePath。",
    )
    file_name: str = Field("", validation_alias=AliasChoices("file_name", "fileName"), description="可选，兼容字段。")
    edits: list[DocumentEditEdit] = Field(default_factory=list, description="按顺序执行的编辑块。")


class DocumentEditPayload(BaseModel):
    files: list[DocumentEditFile] = Field(default_factory=list, description="需要进行精确编辑的文件列表。")
    note: str = Field("", description="本次编辑的简短说明。")

    @model_validator(mode="before")
    @classmethod
    def wrap_single_file_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("files"), list):
            normalized_files: list[dict[str, Any]] = []
            for entry in value.get("files", []):
                parsed_entry = _as_entry_dict(entry)
                if parsed_entry is None:
                    continue
                file_path = _pick_file_path(parsed_entry)
                file_key = _pick_file_key(parsed_entry)
                edits = parsed_entry.get("edits")
                if isinstance(edits, list):
                    normalized_files.append(
                        {
                            "file_key": file_key,
                            "file_path": file_path,
                            "edits": edits,
                        }
                    )
                    continue
                old_text = parsed_entry.get("old_text", parsed_entry.get("oldString"))
                new_text = parsed_entry.get("new_text", parsed_entry.get("newString"))
                if old_text is None or new_text is None or (not file_path and not file_key):
                    continue
                normalized_files.append(
                    {
                        "file_key": file_key,
                        "file_path": file_path,
                        "edits": [
                            {
                                "old_text": old_text,
                                "new_text": new_text,
                                "replace_all": parsed_entry.get("replace_all", parsed_entry.get("replaceAll", False)),
                                "description": parsed_entry.get("description", ""),
                            }
                        ],
                    }
                )
            return {"files": normalized_files, "note": value.get("note", "")}
        file_path = _pick_file_path(value)
        file_key = _pick_file_key(value)
        old_text = value.get("old_text", value.get("oldString"))
        new_text = value.get("new_text", value.get("newString"))
        if old_text is None or new_text is None or (not file_path and not file_key):
            return value
        return {
            "files": [
                {
                    "file_key": file_key,
                    "file_path": file_path,
                    "edits": [
                        {
                            "old_text": old_text,
                            "new_text": new_text,
                            "replace_all": value.get("replace_all", value.get("replaceAll", False)),
                            "description": value.get("description", ""),
                        }
                    ],
                }
            ],
            "note": value.get("note", ""),
        }


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
        description="仅 replace 动作可用；为 true 时替换所有匹配，未匹配时视为无变化。",
    )
    description: str = Field("", description="当前编辑块的目的说明。")


class DocumentPatchFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field("", description="目标文件的逻辑 key；可选。如果提供，必须来自输入中允许写入的 file_key。")
    file_path: str = Field(
        "",
        validation_alias=AliasChoices("file_path", "filePath", "file_name", "fileName"),
        description="目标文件路径；优先使用 filePath。",
    )
    file_name: str = Field("", validation_alias=AliasChoices("file_name", "fileName"), description="可选，兼容字段。")
    edits: list[DocumentPatchEdit] = Field(default_factory=list, description="按顺序执行的编辑块。")


class DocumentPatchPayload(BaseModel):
    files: list[DocumentPatchFile] = Field(default_factory=list, description="需要 patch 的文件列表。")
    note: str = Field("", description="本次 patch 的简短说明。")

    @model_validator(mode="before")
    @classmethod
    def wrap_single_file_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("files"), list):
            normalized_files: list[dict[str, Any]] = []
            for entry in value.get("files", []):
                parsed_entry = _as_entry_dict(entry)
                if parsed_entry is None:
                    continue
                file_path = _pick_file_path(parsed_entry)
                file_key = _pick_file_key(parsed_entry)
                edits = parsed_entry.get("edits")
                if isinstance(edits, list):
                    normalized_files.append(
                        {
                            "file_key": file_key,
                            "file_path": file_path,
                            "edits": edits,
                        }
                    )
                    continue
                action = parsed_entry.get("action")
                new_text = parsed_entry.get("new_text", parsed_entry.get("newString"))
                if action is None or new_text is None or (not file_path and not file_key):
                    continue
                normalized_files.append(
                    {
                        "file_key": file_key,
                        "file_path": file_path,
                        "edits": [
                            {
                                "action": action,
                                "match_text": parsed_entry.get(
                                    "match_text",
                                    parsed_entry.get("matchText", parsed_entry.get("oldString", "")),
                                ),
                                "new_text": new_text,
                                "replace_all": parsed_entry.get(
                                    "replace_all",
                                    parsed_entry.get("replaceAll", False),
                                ),
                                "description": parsed_entry.get("description", ""),
                            }
                        ],
                    }
                )
            return {"files": normalized_files, "note": value.get("note", "")}
        file_path = _pick_file_path(value)
        file_key = _pick_file_key(value)
        action = value.get("action")
        new_text = value.get("new_text", value.get("newString"))
        if action is None or new_text is None or (not file_path and not file_key):
            return value
        return {
            "files": [
                {
                    "file_key": file_key,
                    "file_path": file_path,
                    "edits": [
                        {
                            "action": action,
                            "match_text": value.get("match_text", value.get("matchText", value.get("oldString", ""))),
                            "new_text": new_text,
                            "replace_all": value.get("replace_all", value.get("replaceAll", False)),
                            "description": value.get("description", ""),
                        }
                    ],
                }
            ],
            "note": value.get("note", ""),
        }


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
    patch_text: str | None = None


def _as_document_write_payload(parsed: Any) -> DocumentWritePayload:
    if isinstance(parsed, DocumentWritePayload):
        return parsed
    args = parsed if isinstance(parsed, DocumentWriteToolArgs) else DocumentWriteToolArgs.model_validate(parsed)
    return DocumentWritePayload(
        files=[
            DocumentWriteFile(
                file_path=args.file_path,
                content=args.content,
            )
        ]
    )


def _as_document_edit_payload(parsed: Any) -> DocumentEditPayload:
    if isinstance(parsed, DocumentEditPayload):
        return parsed
    args = parsed if isinstance(parsed, DocumentEditToolArgs) else DocumentEditToolArgs.model_validate(parsed)
    return DocumentEditPayload(
        files=[
            DocumentEditFile(
                file_path=args.file_path,
                edits=[
                    DocumentEditEdit(
                        old_text=args.old_text,
                        new_text=args.new_text,
                        replace_all=args.replace_all,
                    )
                ],
            )
        ]
    )


def document_operation_result_from_tool_result(
    result: llm_runtime.MultiFunctionToolResult,
) -> DocumentOperationCallResult:
    if result.tool_name == DOCUMENT_WRITE_TOOL_NAME:
        return DocumentOperationCallResult(
            mode="write",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            write_payload=_as_document_write_payload(result.parsed),
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
            edit_payload=_as_document_edit_payload(result.parsed),
        )
    if result.tool_name == DOCUMENT_PATCH_TOOL_NAME:
        if isinstance(result.parsed, DocumentPatchPayload):
            return DocumentOperationCallResult(
                mode="patch",
                response_id=result.response_id,
                status=result.status,
                output_types=result.output_types,
                preview=result.preview,
                raw_body_text=result.raw_body_text,
                raw_json=result.raw_json,
                patch_payload=result.parsed,
            )
        args = result.parsed if isinstance(result.parsed, DocumentApplyPatchToolArgs) else DocumentApplyPatchToolArgs.model_validate(result.parsed)
        return DocumentOperationCallResult(
            mode="patch",
            response_id=result.response_id,
            status=result.status,
            output_types=result.output_types,
            preview=result.preview,
            raw_body_text=result.raw_body_text,
            raw_json=result.raw_json,
            patch_text=args.patch_text,
        )
    raise llm_runtime.ModelOutputError(f"模型调用了未支持的文档工具：{result.tool_name}", preview=result.preview)


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
    reject_full_content_replacement: bool = False
    min_output_char_ratio_on_update: float | None = None


def protected_rewritten_chapter_target(path: Path) -> DocumentTarget:
    return DocumentTarget(path=path)


def _normalized_nonempty_text(value: str) -> str:
    return normalize_line_endings(value).strip()


def _looks_like_full_content_replacement_for_edit(
    current: str,
    edits: list[DocumentEditEdit],
) -> bool:
    normalized_current = _normalized_nonempty_text(current)
    if not normalized_current:
        return False
    for edit in edits:
        if _normalized_nonempty_text(edit.old_text) != normalized_current:
            continue
        if _normalized_nonempty_text(edit.new_text) == normalized_current:
            continue
        return True
    return False


def _looks_like_full_content_replacement_for_patch(
    current: str,
    edits: list[DocumentPatchEdit],
) -> bool:
    normalized_current = _normalized_nonempty_text(current)
    if not normalized_current:
        return False
    for edit in edits:
        if edit.action != "replace":
            continue
        if _normalized_nonempty_text(edit.match_text) != normalized_current:
            continue
        if _normalized_nonempty_text(edit.new_text) == normalized_current:
            continue
        return True
    return False


def _validate_protected_target_update(
    *,
    target: DocumentTarget,
    resolved_key: str,
    current: str,
    updated: str,
    mode: Literal["edit", "patch"],
    edit_payload: list[DocumentEditEdit] | None = None,
    patch_payload: list[DocumentPatchEdit] | None = None,
) -> None:
    normalized_current = _normalized_nonempty_text(current)
    normalized_updated = _normalized_nonempty_text(updated)
    if not normalized_current:
        return
    if target.reject_full_content_replacement:
        if mode == "edit" and _looks_like_full_content_replacement_for_edit(current, edit_payload or []):
            raise ValueError(f"{resolved_key} 当前为受保护正文，禁止把整章全文作为单个 old_text 直接整体替换。")
        if mode == "patch" and _looks_like_full_content_replacement_for_patch(current, patch_payload or []):
            raise ValueError(f"{resolved_key} 当前为受保护正文，禁止通过 replace patch 直接整体替换整章全文。")
    ratio = target.min_output_char_ratio_on_update
    if ratio is not None:
        if len(normalized_updated) < int(len(normalized_current) * ratio):
            raise ValueError(
                f"{resolved_key} 修改后正文长度从 {len(normalized_current)} 降到 {len(normalized_updated)}，"
                "低于受保护正文允许的最小比例，疑似通过过度删减来规避正文返修要求。"
            )


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

    def _match_targets_by_path_or_name(path_value: str) -> list[tuple[str, DocumentTarget]]:
        requested = Path(path_value)
        matches = [
            (candidate_key, candidate_target)
            for candidate_key, candidate_target in normalized_targets.items()
            if _same_resolved_path(requested, candidate_target.path)
        ]
        if matches:
            return matches
        file_name = requested.name.casefold()
        return [
            (candidate_key, candidate_target)
            for candidate_key, candidate_target in normalized_targets.items()
            if candidate_target.path.name.casefold() == file_name
        ]

    if cleaned_key:
        if cleaned_key not in normalized_targets:
            if cleaned_path:
                matches = _match_targets_by_path_or_name(cleaned_path)
                if len(matches) == 1:
                    return matches[0]
                if len(matches) > 1:
                    raise ValueError(f"{operation_label} 返回的 file_path/file_name 匹配到多个授权文件：{cleaned_path}")
            raise ValueError(f"{operation_label} 返回了未授权文件：{cleaned_key}")
        target = normalized_targets[cleaned_key]
        if cleaned_path and not _same_resolved_path(Path(cleaned_path), target.path):
            matches = _match_targets_by_path_or_name(cleaned_path)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ValueError(f"{operation_label} 返回的 file_path/file_name 匹配到多个授权文件：{cleaned_path}")
            raise ValueError(f"{operation_label} 返回的 file_key 与 file_path 不一致：{cleaned_key} -> {cleaned_path}")
        return cleaned_key, target

    if cleaned_path:
        matches = _match_targets_by_path_or_name(cleaned_path)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"{operation_label} 返回的 file_path/file_name 匹配到多个授权文件：{cleaned_path}")
        if not normalized_targets:
            requested = Path(cleaned_path)
            return cleaned_path, DocumentTarget(path=requested.expanduser().resolve())
        raise ValueError(f"{operation_label} 返回了未授权文件路径：{cleaned_path}")

    raise ValueError(f"{operation_label} 必须提供 file_key 或 file_path。")


def document_tool_specs() -> list[llm_runtime.FunctionToolSpec[Any]]:
    return [
        llm_runtime.FunctionToolSpec(
            model=DocumentWriteToolArgs,
            name=DOCUMENT_WRITE_TOOL_NAME,
            description=DOCUMENT_WRITE_TOOL_DESCRIPTION,
            compatible_aliases=DOCUMENT_WRITE_TOOL_LEGACY_ALIASES,
        ),
        llm_runtime.FunctionToolSpec(
            model=DocumentEditToolArgs,
            name=DOCUMENT_EDIT_TOOL_NAME,
            description=DOCUMENT_EDIT_TOOL_DESCRIPTION,
            compatible_aliases=DOCUMENT_EDIT_TOOL_LEGACY_ALIASES,
        ),
        llm_runtime.FunctionToolSpec(
            model=DocumentApplyPatchToolArgs,
            name=DOCUMENT_PATCH_TOOL_NAME,
            description=DOCUMENT_PATCH_TOOL_DESCRIPTION,
            compatible_aliases=DOCUMENT_PATCH_TOOL_COMPATIBLE_ALIASES,
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

    return document_operation_result_from_tool_result(result)


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


@dataclass(frozen=True)
class ApplyPatchChunk:
    old_lines: list[str]
    new_lines: list[str]
    change_context: str | None = None
    is_end_of_file: bool = False


@dataclass(frozen=True)
class ApplyPatchHunk:
    type: Literal["add", "update", "delete"]
    path: str
    move_path: str | None = None
    contents: str = ""
    chunks: list[ApplyPatchChunk] = field(default_factory=list)


def _strip_patch_heredoc(patch_text: str) -> str:
    stripped = patch_text.strip()
    if not stripped:
        return ""
    lines = stripped.split("\n")
    first = lines[0].strip()
    if not first.startswith("<<"):
        return stripped
    marker = first[2:].strip().strip("'\"")
    if not marker:
        return stripped
    if len(lines) < 2:
        return stripped
    if lines[-1].strip() != marker:
        return stripped
    return "\n".join(lines[1:-1]).strip()


def parse_patch_text(patch_text: str) -> list[ApplyPatchHunk]:
    cleaned = _strip_patch_heredoc(patch_text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        raise ValueError("apply_patch 验证失败：patchText 为空。")
    lines = cleaned.split("\n")
    begin_index = next((idx for idx, line in enumerate(lines) if line.strip() == "*** Begin Patch"), -1)
    end_index = next((idx for idx, line in enumerate(lines) if line.strip() == "*** End Patch"), -1)
    if begin_index < 0 or end_index < 0 or begin_index >= end_index:
        raise ValueError("apply_patch 验证失败：patchText 缺少合法的 Begin/End 标记。")

    hunks: list[ApplyPatchHunk] = []
    cursor = begin_index + 1
    while cursor < end_index:
        line = lines[cursor]
        if line.startswith("*** Add File:"):
            patch_path = line[len("*** Add File:") :].strip()
            if not patch_path:
                raise ValueError("apply_patch 验证失败：Add File 目标路径为空。")
            cursor += 1
            contents: list[str] = []
            while cursor < end_index and not lines[cursor].startswith("*** "):
                current = lines[cursor]
                if not current.startswith("+"):
                    raise ValueError("apply_patch 验证失败：Add File 内容行必须以 + 开头。")
                contents.append(current[1:])
                cursor += 1
            hunks.append(
                ApplyPatchHunk(
                    type="add",
                    path=patch_path,
                    contents="\n".join(contents),
                )
            )
            continue
        if line.startswith("*** Delete File:"):
            patch_path = line[len("*** Delete File:") :].strip()
            if not patch_path:
                raise ValueError("apply_patch 验证失败：Delete File 目标路径为空。")
            hunks.append(ApplyPatchHunk(type="delete", path=patch_path))
            cursor += 1
            continue
        if line.startswith("*** Update File:"):
            patch_path = line[len("*** Update File:") :].strip()
            if not patch_path:
                raise ValueError("apply_patch 验证失败：Update File 目标路径为空。")
            cursor += 1
            move_path = None
            if cursor < end_index and lines[cursor].startswith("*** Move to:"):
                move_path = lines[cursor][len("*** Move to:") :].strip()
                if not move_path:
                    raise ValueError("apply_patch 验证失败：Move to 目标路径为空。")
                cursor += 1
            chunks: list[ApplyPatchChunk] = []
            while cursor < end_index and not lines[cursor].startswith("*** "):
                if not lines[cursor].startswith("@@"):
                    cursor += 1
                    continue
                context_text = lines[cursor][2:].strip() or None
                cursor += 1
                old_lines: list[str] = []
                new_lines: list[str] = []
                is_end_of_file = False
                while cursor < end_index and not lines[cursor].startswith("@@") and not lines[cursor].startswith("*** "):
                    chunk_line = lines[cursor]
                    if chunk_line == "*** End of File":
                        is_end_of_file = True
                        cursor += 1
                        break
                    if chunk_line.startswith(" "):
                        text = chunk_line[1:]
                        old_lines.append(text)
                        new_lines.append(text)
                    elif chunk_line.startswith("-"):
                        old_lines.append(chunk_line[1:])
                    elif chunk_line.startswith("+"):
                        new_lines.append(chunk_line[1:])
                    cursor += 1
                chunks.append(
                    ApplyPatchChunk(
                        old_lines=old_lines,
                        new_lines=new_lines,
                        change_context=context_text,
                        is_end_of_file=is_end_of_file,
                    )
                )
            hunks.append(
                ApplyPatchHunk(
                    type="update",
                    path=patch_path,
                    move_path=move_path,
                    chunks=chunks,
                )
            )
            continue
        cursor += 1

    if not hunks:
        if cleaned == "*** Begin Patch\n*** End Patch":
            raise ValueError("patch 被拒绝：空 patch。")
        raise ValueError("apply_patch 验证失败：未找到任何可执行 hunk。")
    return hunks


def _normalize_unicode_for_patch_match(value: str) -> str:
    return (
        value.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201A", "'")
        .replace("\u201B", "'")
        .replace("\u201C", '"')
        .replace("\u201D", '"')
        .replace("\u201E", '"')
        .replace("\u201F", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
        .replace("\u2026", "...")
        .replace("\u00A0", " ")
    )


def _try_match_patch_sequence(
    *,
    lines: list[str],
    pattern: list[str],
    start_index: int,
    compare: Any,
    is_end_of_file: bool,
) -> int:
    if is_end_of_file:
        from_end = len(lines) - len(pattern)
        if from_end >= start_index and from_end >= 0:
            if all(compare(lines[from_end + offset], pattern[offset]) for offset in range(len(pattern))):
                return from_end

    for index in range(start_index, len(lines) - len(pattern) + 1):
        if all(compare(lines[index + offset], pattern[offset]) for offset in range(len(pattern))):
            return index
    return -1


def _seek_patch_sequence(
    *,
    lines: list[str],
    pattern: list[str],
    start_index: int,
    is_end_of_file: bool = False,
) -> int:
    if not pattern:
        return -1
    exact = _try_match_patch_sequence(
        lines=lines,
        pattern=pattern,
        start_index=start_index,
        compare=lambda left, right: left == right,
        is_end_of_file=is_end_of_file,
    )
    if exact >= 0:
        return exact
    rstrip_match = _try_match_patch_sequence(
        lines=lines,
        pattern=pattern,
        start_index=start_index,
        compare=lambda left, right: left.rstrip() == right.rstrip(),
        is_end_of_file=is_end_of_file,
    )
    if rstrip_match >= 0:
        return rstrip_match
    trim_match = _try_match_patch_sequence(
        lines=lines,
        pattern=pattern,
        start_index=start_index,
        compare=lambda left, right: left.strip() == right.strip(),
        is_end_of_file=is_end_of_file,
    )
    if trim_match >= 0:
        return trim_match
    return _try_match_patch_sequence(
        lines=lines,
        pattern=pattern,
        start_index=start_index,
        compare=lambda left, right: _normalize_unicode_for_patch_match(left.strip())
        == _normalize_unicode_for_patch_match(right.strip()),
        is_end_of_file=is_end_of_file,
    )


def _derive_new_content_from_patch_chunks(
    *,
    content: str,
    chunks: list[ApplyPatchChunk],
    file_path: Path,
) -> str:
    original_ending = detect_line_ending(content)
    normalized_content = normalize_line_endings(content)
    lines = normalized_content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    replacements: list[tuple[int, int, list[str]]] = []
    line_index = 0
    for chunk in chunks:
        if chunk.change_context:
            context_index = _seek_patch_sequence(
                lines=lines,
                pattern=[chunk.change_context],
                start_index=line_index,
                is_end_of_file=False,
            )
            if context_index < 0:
                raise ValueError(f"apply_patch 未能在文件中定位上下文：{chunk.change_context}")
            line_index = context_index + 1

        if not chunk.old_lines:
            replacements.append((len(lines), 0, list(chunk.new_lines)))
            continue

        pattern = list(chunk.old_lines)
        replacement = list(chunk.new_lines)
        found = _seek_patch_sequence(
            lines=lines,
            pattern=pattern,
            start_index=line_index,
            is_end_of_file=chunk.is_end_of_file,
        )
        if found < 0 and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if replacement and replacement[-1] == "":
                replacement = replacement[:-1]
            found = _seek_patch_sequence(
                lines=lines,
                pattern=pattern,
                start_index=line_index,
                is_end_of_file=chunk.is_end_of_file,
            )
        if found < 0:
            raise ValueError(
                f"apply_patch 未能在文件中定位目标片段：{file_path}\n"
                f"{chr(10).join(chunk.old_lines)}"
            )
        replacements.append((found, len(pattern), replacement))
        line_index = found + len(pattern)

    replacements.sort(key=lambda item: item[0])
    updated_lines = list(lines)
    for start, length, replacement_lines in reversed(replacements):
        updated_lines[start : start + length] = replacement_lines
    if not updated_lines or updated_lines[-1] != "":
        updated_lines.append("")
    updated_text = "\n".join(updated_lines)
    return convert_to_line_ending(updated_text, original_ending)


def _apply_patch_text(
    *,
    patch_text: str,
    normalized_targets: dict[str, DocumentTarget],
) -> list[AppliedDocumentFile]:
    hunks = parse_patch_text(patch_text)
    file_results: list[AppliedDocumentFile] = []

    for hunk in hunks:
        resolved_key, target = _resolve_document_target(
            file_key="",
            file_path=hunk.path,
            normalized_targets=normalized_targets,
            operation_label="apply_patch",
        )
        path = target.path

        if hunk.type == "add":
            content = hunk.contents
            if content and not content.endswith("\n"):
                content = f"{content}\n"
            changed = write_text_if_changed(path, content)
            file_results.append(
                AppliedDocumentFile(
                    file_key=resolved_key,
                    path=path,
                    mode="patch",
                    emitted=True,
                    changed=changed,
                    edit_count=1,
                )
            )
            continue

        if hunk.type == "delete":
            if not path.exists():
                raise ValueError(f"apply_patch 删除失败，文件不存在：{path}")
            path.unlink()
            file_results.append(
                AppliedDocumentFile(
                    file_key=resolved_key,
                    path=path,
                    mode="patch",
                    emitted=True,
                    changed=True,
                    edit_count=1,
                )
            )
            continue

        current = read_text_if_exists(path)
        updated = _derive_new_content_from_patch_chunks(
            content=current,
            chunks=hunk.chunks,
            file_path=path,
        )
        target_key = resolved_key
        target_path = path
        target_config = target
        if hunk.move_path:
            move_key, move_target = _resolve_document_target(
                file_key="",
                file_path=hunk.move_path,
                normalized_targets=normalized_targets,
                operation_label="apply_patch.move",
            )
            target_key = move_key
            target_path = move_target.path
            target_config = move_target
        _validate_protected_target_update(
            target=target_config,
            resolved_key=target_key,
            current=current,
            updated=updated,
            mode="patch",
        )
        changed = write_text_if_changed(target_path, updated)
        if hunk.move_path and _same_resolved_path(path, target_path) is False and path.exists():
            path.unlink()
            changed = True
        file_results.append(
            AppliedDocumentFile(
                file_key=target_key,
                path=target_path,
                mode="patch",
                emitted=True,
                changed=changed,
                edit_count=max(len(hunk.chunks), 1),
            )
        )
    return file_results


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
            _validate_protected_target_update(
                target=target,
                resolved_key=resolved_key,
                current=current,
                updated=updated,
                mode="edit",
                edit_payload=item.edits,
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

    if operation.patch_text and operation.patch_text.strip():
        file_results.extend(
            _apply_patch_text(
                patch_text=operation.patch_text,
                normalized_targets=normalized_targets,
            )
        )
        return AppliedDocumentOperation(mode="patch", files=file_results)

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
        _validate_protected_target_update(
            target=target,
            resolved_key=resolved_key,
            current=current,
            updated=updated,
            mode="patch",
            patch_payload=item.edits,
        )
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
