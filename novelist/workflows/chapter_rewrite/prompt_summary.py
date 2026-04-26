from __future__ import annotations

from ._shared import *  # noqa: F401,F403


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
    if previous_response_id:
        print_progress(f"  会话：沿用 previous_response_id={previous_response_id}")
    else:
        print_progress("  会话：本轮首次请求，将创建新的会话。")

    print_progress("  提示词缓存共享前缀：")
    for line in shared_prefix_lines:
        print_progress(f"    - {line}")

    print_progress("  动态后缀（本次请求会变化）：")
    for line in dynamic_suffix_lines:
        print_progress(f"    - {line}")

    print_progress("  参考源输入：")
    for line in source_summary_lines:
        print_progress(f"    - {line}")

    print_progress("  已输入文档：")
    if included_docs:
        for line in included_docs:
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
    lines = [
        f"当前审查区间：{chapter_numbers[0]}-{chapter_numbers[-1]}。",
        f"当前区间参考源总字符数约 {source_char_count}。",
        f"当前区间已生成章节数：{len(rewritten_chapters)}。",
    ]
    for chapter_number in chapter_numbers:
        chapter = get_chapter_material(volume_material, chapter_number)
        lines.append(
            f"参考源章节：{chapter['file_name']}（标题：{chapter['source_title']}，字符数约 {len(chapter['text'])}）"
        )
    return lines

def chapter_source_summary_lines(volume_material: dict[str, Any], chapter_number: str, source_char_count: int) -> list[str]:
    chapter = get_chapter_material(volume_material, chapter_number)
    lines = [
        f"当前源章节：{chapter['file_name']}（标题：{chapter['source_title']}，字符数约 {len(chapter['text'])}）",
        f"当前卷补充文件：{len(volume_material['extras'])} 个，当前请求会一并注入。",
        f"当前章节参考源总字符数约 {source_char_count}。",
    ]
    for extra in volume_material["extras"]:
        lines.append(f"补充文件：{extra['file_name']}（字符数约 {len(extra['text'])}）")
    lines.append("未输入的来源章节：同卷其他章节当前不注入。")
    return lines

def volume_review_source_summary_lines(rewritten_chapters: dict[str, dict[str, Any]]) -> list[str]:
    lines = [f"当前卷已生成章节数：{len(rewritten_chapters)}。"]
    for chapter_number, data in rewritten_chapters.items():
        lines.append(f"已生成章节：{chapter_number}.txt（字符数约 {len(data['text'])}）")
    return lines

__all__ = [
    'chapter_shared_prefix_summary_lines',
    'group_review_shared_prefix_summary_lines',
    'volume_review_shared_prefix_summary_lines',
    'payload_prefix_doc_summary_lines',
    'payload_dynamic_suffix_summary_lines',
    'print_request_context_summary',
    'five_chapter_review_source_summary_lines',
    'chapter_source_summary_lines',
    'volume_review_source_summary_lines',
]
