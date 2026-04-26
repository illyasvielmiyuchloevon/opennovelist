from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def load_chapter_review_skill_reference() -> dict[str, Any]:
    content = read_text_if_exists(CHAPTER_REVIEW_SKILL_PATH).strip()
    if not content:
        return {
            "label": "AI 痕迹审查 Skill",
            "file_name": CHAPTER_REVIEW_SKILL_PATH.name,
            "file_path": str(CHAPTER_REVIEW_SKILL_PATH),
            "content": "",
        }
    return {
        "label": "AI 痕迹审查 Skill",
        "file_name": CHAPTER_REVIEW_SKILL_PATH.name,
        "file_path": str(CHAPTER_REVIEW_SKILL_PATH),
        "content": content,
    }

def load_chapter_writing_skill_reference() -> dict[str, Any]:
    content = read_text_if_exists(CHAPTER_WRITING_SKILL_PATH).strip()
    if not content:
        return {
            "label": "写作规范 Skill",
            "file_name": CHAPTER_WRITING_SKILL_PATH.name,
            "file_path": str(CHAPTER_WRITING_SKILL_PATH),
            "content": "",
        }
    return {
        "label": "写作规范 Skill",
        "file_name": CHAPTER_WRITING_SKILL_PATH.name,
        "file_path": str(CHAPTER_WRITING_SKILL_PATH),
        "content": content,
    }

def normalize_review_chapter_numbers(
    values: list[str],
    *,
    allowed_chapters: set[str] | None = None,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            continue
        chapter_number = digits.zfill(4)
        if allowed_chapters is not None and chapter_number not in allowed_chapters:
            continue
        if chapter_number in seen:
            continue
        seen.add(chapter_number)
        normalized.append(chapter_number)
    return normalized

def infer_review_passed(
    payload: WorkflowSubmissionPayload,
    *,
    fallback_text: str = "",
) -> bool | None:
    if payload.passed is not None:
        return payload.passed

    if payload.blocking_issues or payload.rewrite_targets or payload.chapters_to_revise:
        return False

    combined_text = "\n".join(
        part.strip()
        for part in (payload.review_md, payload.content_md, fallback_text)
        if isinstance(part, str) and part.strip()
    )
    if not combined_text:
        return None

    normalized_text = combined_text.replace("*", "").replace("`", "")

    if re.search(r"(未通过|不通过|需返工|需要返工|必须返工|存在重大问题)", normalized_text):
        return False
    if re.search(r"(审核通过|本章通过|通过。|通过$|总体结论[^。\n]*通过|结论[^。\n]*通过)", normalized_text):
        return True
    return None

def extract_chapter_numbers_from_text(
    text: str,
    *,
    allowed_chapters: set[str] | None = None,
) -> list[str]:
    matches = re.findall(r"\b\d{1,4}\b", text)
    return normalize_review_chapter_numbers(matches, allowed_chapters=allowed_chapters)

def build_canonical_review_markdown(
    *,
    review_kind: str,
    passed: bool,
    review_md: str,
    blocking_issues: list[str],
    rewrite_targets: list[str],
    chapters_to_revise: list[str],
) -> str:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    original = review_md.strip()
    canonical_sections = {
        "总体结论",
        "核心问题",
        "需要返工的章节",
        "需要返工的对象",
        "修改建议",
        "详细审查说明",
    }
    if original and any(f"## {heading}" in original for heading in canonical_sections):
        return original

    lines = [
        f"# {label}",
        "",
        "## 总体结论",
        f"- **{'通过' if passed else '不通过'}**",
        "",
        "## 核心问题",
    ]

    if blocking_issues:
        lines.extend(f"- {item}" for item in blocking_issues)
    else:
        lines.append("- 无。")

    if review_kind in {"group", "volume"}:
        lines.extend(["", "## 需要返工的章节"])
        if chapters_to_revise:
            lines.extend(f"- {item}" for item in chapters_to_revise)
        else:
            lines.append("- 无。")
    else:
        lines.extend(["", "## 需要返工的对象"])
        if rewrite_targets:
            lines.extend(f"- {item}" for item in rewrite_targets)
        else:
            lines.append("- 无。")

    lines.extend(["", "## 修改建议"])
    if original:
        lines.append(original)
    elif not passed:
        lines.append("- 请根据上述问题返工。")
    else:
        lines.append("- 当前产物可继续进入下一阶段。")

    return "\n".join(lines).strip()

def review_output_contract_lines(review_kind: str) -> list[str]:
    label = REVIEW_KIND_LABELS.get(review_kind, "审核")
    range_section_title = "需要返工的章节" if review_kind in {"group", "volume"} else "需要返工的对象"
    lines = [
        f"必须通过函数工具返回完整的{label}结果，至少包含 passed 和 review_md。",
        f"review_md 必须使用固定骨架：# {label} / ## 总体结论 / ## 核心问题 / ## {range_section_title} / ## 修改建议。",
        "如果 passed=true，review_md 的总体结论必须明确写“通过”。",
        "如果 passed=false，review_md 的总体结论必须明确写“不通过”，并在对应返工章节或返工对象小节中列出需要返工的内容。",
    ]
    if review_kind == "chapter":
        lines.append(
            "章级审核不通过时，rewrite_targets 必须只使用这些返工对象："
            " full_workflow / chapter_outline / chapter_text / support_updates，"
            "如果只需改正文就只写 chapter_text；如果只需改配套状态文档就只写 support_updates；"
            "如果章纲到正文都要重来就写 full_workflow。"
        )
    else:
        lines.append(
            f"{label}不通过时，rewrite_targets 必须使用“章节号:返工对象”格式，"
            "例如 0003:chapter_text、0004:support_updates、0005:full_workflow。"
        )
    return lines

def finalize_review_payload(
    payload: WorkflowSubmissionPayload,
    *,
    review_kind: str,
    allowed_chapters: list[str] | None = None,
) -> WorkflowSubmissionPayload:
    allowed_set = set(allowed_chapters or [])
    fallback_text = payload.content_md.strip()
    chapters_to_revise = normalize_review_chapter_numbers(
        payload.chapters_to_revise,
        allowed_chapters=allowed_set if allowed_set else None,
    )

    if not chapters_to_revise and review_kind in {"group", "volume"}:
        inferred = extract_chapter_numbers_from_text(
            "\n".join(
                [
                    payload.review_md.strip(),
                    payload.content_md.strip(),
                    "\n".join(payload.blocking_issues),
                    "\n".join(payload.rewrite_targets),
                ]
            ),
            allowed_chapters=allowed_set if allowed_set else None,
        )
        chapters_to_revise = inferred

    passed = infer_review_passed(
        payload.model_copy(update={"chapters_to_revise": chapters_to_revise}),
        fallback_text=fallback_text,
    )
    if passed is None:
        raise llm_runtime.ModelOutputError("模型未通过统一函数工具返回明确的审核结论。")

    review_md_source = payload.review_md.strip() or fallback_text
    if not review_md_source:
        review_md_source = "模型未提供审核正文，已根据结构化字段生成标准化审查摘要。"

    canonical_review_md = build_canonical_review_markdown(
        review_kind=review_kind,
        passed=passed,
        review_md=review_md_source,
        blocking_issues=payload.blocking_issues,
        rewrite_targets=payload.rewrite_targets,
        chapters_to_revise=chapters_to_revise,
    )

    return payload.model_copy(
        update={
            "passed": passed,
            "review_md": canonical_review_md,
            "chapters_to_revise": chapters_to_revise,
        }
    )

__all__ = [
    'load_chapter_review_skill_reference',
    'load_chapter_writing_skill_reference',
    'normalize_review_chapter_numbers',
    'infer_review_passed',
    'extract_chapter_numbers_from_text',
    'build_canonical_review_markdown',
    'review_output_contract_lines',
    'finalize_review_payload',
]
