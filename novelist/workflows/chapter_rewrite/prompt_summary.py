from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def _chunk_text_items(items: list[str], size: int) -> list[str]:
    return ["，".join(items[index:index + size]) for index in range(0, len(items), size)]


def chapter_shared_prefix_summary_lines(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
    source_char_count: int,
) -> list[str]:
    chapter = get_chapter_material(volume_material, chapter_number)
    return [
        "共享前缀构造：COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS + build_chapter_shared_prompt()。",
        (
            "固定函数工具："
            f"{document_ops.DOCUMENT_WRITE_TOOL_NAME} / {document_ops.DOCUMENT_EDIT_TOOL_NAME} / "
            f"{document_ops.DOCUMENT_PATCH_TOOL_NAME} / {WORKFLOW_SUBMISSION_TOOL_NAME}。"
        ),
        (
            f"固定项目上下文：新书《{manifest['new_book_title']}》 / 目标世界观："
            f"{manifest.get('target_worldview', '') or '未设置'} / 当前卷：{volume_material['volume_number']} / 当前章：{chapter_number}。"
        ),
        f"固定工作流规则：章节工作流规则 {5} 条。",
        f"固定参考源文件清单：补充文件 {len(volume_material['extras'])} 个 + 当前源章节 1 个（{chapter['file_name']}）。",
        f"固定参考源原文：当前章 source bundle，字符数约 {source_char_count}。",
    ]

def group_generation_shared_prefix_summary_lines(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_char_count: int,
) -> list[str]:
    return [
        "共享前缀构造：COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS + build_five_chapter_generation_shared_prompt()。",
        (
            "固定函数工具："
            f"{document_ops.DOCUMENT_WRITE_TOOL_NAME} / {document_ops.DOCUMENT_EDIT_TOOL_NAME} / "
            f"{document_ops.DOCUMENT_PATCH_TOOL_NAME} / {WORKFLOW_SUBMISSION_TOOL_NAME}。"
        ),
        (
            f"固定项目上下文：新书《{manifest['new_book_title']}》 / 目标世界观："
            f"{manifest.get('target_worldview', '') or '未设置'} / 当前卷：{volume_material['volume_number']} / 当前组："
            f"{chapter_numbers[0]}-{chapter_numbers[-1]}。"
        ),
        "固定工作流规则：五章组生成规则 7 条。",
        (
            f"固定参考源原文：当前组 source bundle，只包含本组 {len(chapter_numbers)} 章参考源与 "
            f"{len(volume_material['extras'])} 个补充文件，字符数约 {source_char_count}。"
        ),
        "固定参考源范围：同卷其他章节正文不注入本次组生成请求。",
    ]

def group_review_shared_prefix_summary_lines(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_char_count: int,
    rewritten_chapters: dict[str, dict[str, Any]],
) -> list[str]:
    return [
        "共享前缀构造：COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS + build_five_chapter_review_shared_prompt()。",
        (
            "固定函数工具："
            f"{document_ops.DOCUMENT_WRITE_TOOL_NAME} / {document_ops.DOCUMENT_EDIT_TOOL_NAME} / "
            f"{document_ops.DOCUMENT_PATCH_TOOL_NAME} / {WORKFLOW_SUBMISSION_TOOL_NAME}。"
        ),
        (
            f"固定项目上下文：新书《{manifest['new_book_title']}》 / 目标世界观："
            f"{manifest.get('target_worldview', '') or '未设置'} / 当前卷：{volume_material['volume_number']} / 当前组："
            f"{chapter_numbers[0]}-{chapter_numbers[-1]}。"
        ),
        f"固定工作流规则：{FIVE_CHAPTER_REVIEW_NAME}规则 3 条。",
        f"固定参考源原文：当前组 source bundle，包含 {len(chapter_numbers)} 章参考源与 {len(volume_material['extras'])} 个补充文件，字符数约 {source_char_count}。",
        f"固定已生成章节清单：当前组待审章节 {len(rewritten_chapters)} 章。",
    ]

def volume_review_shared_prefix_summary_lines(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> list[str]:
    total_chars = sum(len(data.get("text", "")) for data in rewritten_chapters.values())
    return [
        "共享前缀构造：COMMON_VOLUME_REVIEW_INSTRUCTIONS + build_volume_review_shared_prompt()。",
        (
            "固定函数工具："
            f"{document_ops.DOCUMENT_WRITE_TOOL_NAME} / {document_ops.DOCUMENT_EDIT_TOOL_NAME} / "
            f"{document_ops.DOCUMENT_PATCH_TOOL_NAME} / {WORKFLOW_SUBMISSION_TOOL_NAME}。"
        ),
        (
            f"固定项目上下文：新书《{manifest['new_book_title']}》 / 目标世界观："
            f"{manifest.get('target_worldview', '') or '未设置'} / 当前卷：{volume_material['volume_number']}。"
        ),
        "固定工作流规则：卷级审核规则 3 条。",
        f"固定已生成章节清单：当前卷 {len(rewritten_chapters)} 章，正文总字符数约 {total_chars}。",
    ]

def payload_prefix_doc_summary_lines(payload: dict[str, Any]) -> list[str]:
    doc_bucket_labels = {
        "stable_injected_global_docs": "稳定全局注入文档",
        "stable_injected_volume_docs": "稳定卷级注入文档",
        "stable_injected_chapter_docs": "稳定章级注入文档",
    }
    lines: list[str] = []
    for key, label in doc_bucket_labels.items():
        value = payload.get(key, {})
        count = len(value) if isinstance(value, dict) else 0
        lines.append(f"Dynamic Request 前段固定注入：{label} {count} 项。")
    return lines

def payload_dynamic_suffix_summary_lines(payload: dict[str, Any]) -> list[str]:
    document_request = payload.get("document_request", {})
    phase = str(document_request.get("phase", "unknown"))
    role = str(document_request.get("role", "")).strip()
    task = str(document_request.get("task", "")).strip()
    required_file = str(document_request.get("required_file", "")).strip()
    requirements = payload.get("requirements", [])
    lines = [
        f"动态请求构造：document_request.phase={phase}" + (f" / role={role}" if role else "") + "。",
    ]
    if task:
        lines.append(f"本次动态任务：{task}")
    if required_file:
        lines.append(f"目标输出文件：{required_file}")
    if isinstance(requirements, list):
        lines.append(f"本次阶段要求：{len(requirements)} 条。")

    doc_bucket_labels = {
        "rolling_injected_global_docs": "滚动全局注入文档",
        "rolling_injected_volume_docs": "滚动卷级注入文档",
        "rolling_injected_chapter_docs": "滚动章级注入文档",
        "rolling_injected_group_docs": "滚动组级注入文档",
        "writing_skill_reference": "写作规范 skill 参考",
        "review_skill_reference": "审核 skill 参考",
        "update_target_files": "待更新目标文件清单",
        "rewritten_chapters": "已生成章节正文清单",
    }
    for key, label in doc_bucket_labels.items():
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, dict):
            count = len(value)
        elif isinstance(value, list):
            count = len(value)
        else:
            count = 1 if value else 0
        lines.append(f"本次动态附带：{label} {count} 项。")

    if "current_generated_chapter" in payload:
        lines.append("本次动态附带：当前章节正文 1 项。")

    return lines

def _payload_entry_text(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ""
    for key in ("content", "current_content", "text"):
        value = entry.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""

def _payload_entry_char_count(entry: Any) -> int:
    text = _payload_entry_text(entry)
    if text:
        return len(text)
    if isinstance(entry, dict):
        for key in ("char_count", "current_char_count", "source_char_count"):
            value = entry.get(key)
            if isinstance(value, int):
                return value
    return 0

def _payload_entry_name(entry: Any, fallback: str) -> str:
    if not isinstance(entry, dict):
        return fallback
    label = str(entry.get("label") or "").strip()
    file_name = str(entry.get("file_name") or "").strip()
    if label and file_name:
        return f"{label}（{file_name}）"
    return label or file_name or fallback

def _payload_entry_path(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("file_path") or entry.get("required_file") or "").strip()

def _payload_doc_line(section_label: str, key: str, entry: Any) -> str:
    name = _payload_entry_name(entry, key)
    path = _payload_entry_path(entry)
    location = f" -> {path}" if path else ""
    return f"{section_label}：{name}{location}（字符数约 {_payload_entry_char_count(entry)}）"

def payload_actual_input_summary_lines(payload: dict[str, Any]) -> list[str]:
    bucket_labels = {
        "stable_injected_global_docs": "稳定全局注入文档",
        "stable_injected_volume_docs": "稳定卷级注入文档",
        "stable_injected_chapter_docs": "稳定章级注入文档",
        "rolling_injected_global_docs": "滚动全局注入文档",
        "rolling_injected_volume_docs": "滚动卷级注入文档",
        "rolling_injected_chapter_docs": "滚动章级注入文档",
        "rolling_injected_group_docs": "滚动组级注入文档",
        "legacy_chapter_outlines": "旧章纲兼容输入",
        "group_outlines": "组纲输入",
    }
    lines: list[str] = []
    for field_name, section_label in bucket_labels.items():
        value = payload.get(field_name)
        if isinstance(value, dict):
            for key, entry in value.items():
                lines.append(_payload_doc_line(section_label, str(key), entry))
        elif isinstance(value, list):
            for index, entry in enumerate(value, start=1):
                lines.append(_payload_doc_line(section_label, str(index), entry))

    single_doc_fields = {
        "current_group_outline": "当前组纲",
        "current_generated_chapter": "当前已生成章节正文",
        "writing_skill_reference": "写作规范 skill",
        "review_skill_reference": "审核 skill",
    }
    for field_name, section_label in single_doc_fields.items():
        if field_name in payload:
            lines.append(_payload_doc_line(section_label, field_name, payload[field_name]))

    rewritten = payload.get("rewritten_chapters")
    if isinstance(rewritten, dict):
        for key, entry in rewritten.items():
            lines.append(_payload_doc_line("已生成章节正文", str(key), entry))

    return lines

def payload_target_file_summary_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for field_name, section_label in (
        ("update_target_files", "可写/可修改目标文件"),
        ("target_files", "目标文件"),
        ("adaptation_documents", "待审核文档"),
    ):
        value = payload.get(field_name)
        if not isinstance(value, list):
            continue
        for index, entry in enumerate(value, start=1):
            key = str(entry.get("file_key") or index) if isinstance(entry, dict) else str(index)
            preferred_mode = str(entry.get("preferred_mode") or "").strip() if isinstance(entry, dict) else ""
            suffix = f"，建议工具={preferred_mode}" if preferred_mode else ""
            lines.append(_payload_doc_line(section_label, key, entry) + suffix)
    return lines

def print_request_context_summary(
    *,
    request_label: str,
    volume_number: str,
    chapter_number: str | None,
    location_label: str | None = None,
    source_summary_lines: list[str],
    included_docs: list[str],
    omitted_docs: list[str],
    previous_response_id: str | None,
    prompt_cache_key: str | None,
    shared_prefix_lines: list[str],
    dynamic_suffix_lines: list[str],
    payload: dict[str, Any] | None = None,
    user_input_char_count: int | None = None,
    session_status_line: str | None = None,
) -> None:
    print_progress(f"{request_label} 本次请求将携带以下内容：")
    if location_label:
        print_progress(f"  当前定位：{location_label}")
    elif chapter_number is not None:
        print_progress(f"  当前定位：第 {volume_number} 卷，第 {chapter_number} 章。")
    else:
        print_progress(f"  当前定位：第 {volume_number} 卷，卷级审核。")
    if prompt_cache_key:
        print_progress(f"  提示词缓存键：{prompt_cache_key}")
    if session_status_line:
        print_progress(f"  {session_status_line}")
    elif previous_response_id:
        print_progress(f"  会话：沿用 previous_response_id={previous_response_id}")
    else:
        print_progress("  会话：本轮首次请求，将创建新的会话。")
    if user_input_char_count is not None:
        print_progress(f"  user_input 字符数约 {user_input_char_count}。")

    print_progress("  提示词缓存共享前缀：")
    for line in shared_prefix_lines:
        print_progress(f"    - {line}")

    print_progress("  动态后缀（本次请求会变化）：")
    for line in dynamic_suffix_lines:
        print_progress(f"    - {line}")

    print_progress("  参考源输入：")
    for line in source_summary_lines:
        print_progress(f"    - {line}")

    actual_doc_lines = payload_actual_input_summary_lines(payload) if payload is not None else included_docs
    print_progress("  已输入文档/文件（按最终 payload 展开）：")
    if actual_doc_lines:
        for line in actual_doc_lines:
            print_progress(f"    - {line}")
    else:
        print_progress("    - 无。")

    if payload is not None:
        target_lines = payload_target_file_summary_lines(payload)
        print_progress("  目标/可修改文件当前内容：")
        if target_lines:
            for line in target_lines:
                print_progress(f"    - {line}")
        else:
            print_progress("    - 无。")

    print_progress("  未输入文档：")
    if omitted_docs:
        for line in omitted_docs:
            print_progress(f"    - {line}")
    else:
        print_progress("    - 无。")

def five_chapter_review_source_summary_lines(
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_char_count: int,
    rewritten_chapters: dict[str, dict[str, Any]],
) -> list[str]:
    rewritten_total = sum(len(data.get("text", "")) for data in rewritten_chapters.values())
    lines = [
        f"当前审查区间：{chapter_numbers[0]}-{chapter_numbers[-1]}。",
        f"当前区间参考源总字符数约 {source_char_count}。",
        f"当前区间已生成章节数：{len(rewritten_chapters)}，正文总字符数约 {rewritten_total}。",
    ]
    chapter_names = [str(get_chapter_material(volume_material, chapter_number)["file_name"]) for chapter_number in chapter_numbers]
    for index, chunk in enumerate(_chunk_text_items(chapter_names, 10), start=1):
        lines.append(f"参考源章节文件[{index}]：{chunk}")
    return lines

def group_generation_source_summary_lines(
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_char_count: int,
) -> list[str]:
    lines = [
        f"当前生成区间：{chapter_numbers[0]}-{chapter_numbers[-1]}。",
        f"当前区间参考源总字符数约 {source_char_count}。",
        f"当前卷补充文件：{len(volume_material['extras'])} 个，当前请求会一并注入。",
    ]
    extra_names = [str(extra["file_name"]) for extra in volume_material["extras"]]
    for index, chunk in enumerate(_chunk_text_items(extra_names, 8), start=1):
        lines.append(f"补充文件[{index}]：{chunk}")
    chapter_names = [str(get_chapter_material(volume_material, chapter_number)["file_name"]) for chapter_number in chapter_numbers]
    for index, chunk in enumerate(_chunk_text_items(chapter_names, 10), start=1):
        lines.append(f"参考源章节文件[{index}]：{chunk}")
    lines.append("未输入的来源章节：同卷其他章节正文当前不注入。")
    return lines

def chapter_source_summary_lines(volume_material: dict[str, Any], chapter_number: str, source_char_count: int) -> list[str]:
    chapter = get_chapter_material(volume_material, chapter_number)
    lines = [
        f"当前源章节：{chapter['file_name']}（标题：{chapter['source_title']}，字符数约 {len(chapter['text'])}）",
        f"当前卷补充文件：{len(volume_material['extras'])} 个，当前请求会一并注入。",
        f"当前章节参考源总字符数约 {source_char_count}。",
    ]
    extra_names = [str(extra["file_name"]) for extra in volume_material["extras"]]
    for index, chunk in enumerate(_chunk_text_items(extra_names, 8), start=1):
        lines.append(f"补充文件[{index}]：{chunk}")
    lines.append("未输入的来源章节：同卷其他章节当前不注入。")
    return lines

def volume_review_source_summary_lines(rewritten_chapters: dict[str, dict[str, Any]]) -> list[str]:
    total_chars = sum(len(data.get("text", "")) for data in rewritten_chapters.values())
    lines = [f"当前卷已生成章节数：{len(rewritten_chapters)}，正文总字符数约 {total_chars}。"]
    chapter_names = [str(data.get("file_name") or f"{chapter_number}.txt") for chapter_number, data in rewritten_chapters.items()]
    for index, chunk in enumerate(_chunk_text_items(chapter_names, 10), start=1):
        lines.append(f"已生成章节文件[{index}]：{chunk}")
    return lines

__all__ = [
    'chapter_shared_prefix_summary_lines',
    'group_generation_shared_prefix_summary_lines',
    'group_review_shared_prefix_summary_lines',
    'volume_review_shared_prefix_summary_lines',
    'payload_prefix_doc_summary_lines',
    'payload_dynamic_suffix_summary_lines',
    'payload_actual_input_summary_lines',
    'payload_target_file_summary_lines',
    'print_request_context_summary',
    'five_chapter_review_source_summary_lines',
    'group_generation_source_summary_lines',
    'chapter_source_summary_lines',
    'volume_review_source_summary_lines',
]
