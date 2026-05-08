from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def chapter_text_target_inventory(paths: dict[str, Path], current_text: str) -> list[dict[str, Any]]:
    current_content = current_text.strip()
    return [
        {
            "file_key": "rewritten_chapter",
            "file_name": paths["rewritten_chapter"].name,
            "file_path": str(paths["rewritten_chapter"]),
            "exists": paths["rewritten_chapter"].exists(),
            "preferred_mode": "edit_or_patch" if current_text.strip() else "write",
            "tool_argument_policy": "工具参数可使用 file_key 与 file_path（或 file_name）定位文件；write 使用 content；edit/patch 使用 files[].edits，并确保 old_text 或 match_text 来自当前内容。",
            "write_policy": "no_write_if_exists",
            "structure_mode": "existing_chapter_text_revision",
            "tool_selection_policy": (
                "按修改意图与可定位性自行选择 edit 或 patch；"
                "当段落存在冗余、矛盾、重复或确需移除的信息时，可以删除或重组对应内容；"
                "插入新段落、移动前后衔接块或追加过渡内容时，优先选择定位更稳定、改动更小的工具；"
                "当前文件为空时才用 write。"
            ),
            "update_rules": [
                "当前文件已存在时，只能基于现有正文做局部改写、增删、替换与重组。",
                "不要把整章当成全新生成任务推倒重写。",
                "未变化段落应尽量保留，优先只修改受审核意见影响的局部。",
            ],
            "current_char_count": len(current_content),
        }
    ]

def support_update_general_rules() -> list[str]:
    return [
        "这是长期知识文档更新步骤，只更新当前章节真实发生变化且确有必要更新的文档。",
        "无变化的文档不要返回，也不要为了统一措辞重写旧内容。",
        "已有非空文件默认禁止整篇写入，必须按修改意图选择 edit 或 patch 做局部增量更新。",
        "修改已有句段、条目、状态、名称或术语，或插入新条目、追加新段落、按标题补充小节时，按可定位性与最小改动原则自行选择 edit 或 patch。",
        "如果只是给某一段、某条记录或某个小块后面补充新内容，可以使用 patch 的 insert_after 直接追加，不要改写整段。",
        "长期知识文档采用“固定标题 + 可扩展二级标题”的管理方式，不要写成数据库字段表、代码 schema 或过度表格化文档。",
        "一级标题固定；二级标题用于管理不同类型的信息。已有二级标题结构如果已经适合本书，应优先沿用。",
        "每本书的信息类型都可能不同。出现新知识类型时，可以按实际小说内容新增新的二级标题，而不是硬套少数预设分类。",
        "除卷级剧情进程外，其余滚动文档默认都不是章节回顾、逐章变化日志或剧情流水账，只记录仍然有效的关键变化。",
        "人物状态卡和人物关系链属于“当前状态快照”，不是章节回顾、逐章变化日志或剧情流水账。",
        "当人物状态或人物关系发生变化时，应把变化折叠进对应条目的当前描述，直接改写旧结论，不要保留“第00xx章补充 / 推进 / 变化”式累计记录。",
        "如果旧条目里堆积了大量已经失效、重复或只用于交代章节经过的内容，应在本次更新中主动压缩，只保留当前仍然影响判断的状态、关系、压力和风险。",
        "世界状态只记录公共世界层面的真实变化；伏笔文档只记录伏笔埋设、关键推进和正式回收，不记录普通章节内容。",
        "如果人物状态卡、人物关系链、世界状态或伏笔文档里已经堆积了按章节号逐条追加的旧记录，本次更新应主动把它们折叠回文档真实用途：当前状态快照、当前关系快照、公共世界状态或仍然有效的伏笔索引，而不是继续向下续写。",
    ]

def support_update_doc_rules() -> dict[str, dict[str, Any]]:
    return HEADING_MANAGED_DOC_SPECS

def support_update_target_inventory(paths: dict[str, Path]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    doc_rules = support_update_doc_rules()
    for file_key, path in support_update_target_paths(paths).items():
        current_content = read_text_if_exists(path).strip()
        rule = doc_rules.get(file_key, {})
        inventory.append(
            {
                "file_key": file_key,
                "file_name": path.name,
                "file_path": str(path),
                "exists": path.exists(),
                "preferred_mode": "edit_or_patch" if current_content else "write",
                "tool_argument_policy": "工具参数可使用 file_key 与 file_path（或 file_name）定位文件；write 使用 content；edit/patch 使用 files[].edits，并确保 old_text 或 match_text 来自当前内容。",
                "write_policy": "no_write_if_exists",
                "structure_mode": "fixed_title_expandable_sections_document",
                "tool_selection_policy": (
                    "按修改意图与可定位性自行选择 edit 或 patch；"
                    "优先采用改动范围更小、定位更稳定的方式完成目标；"
                    "文件为空或首次创建时才用 write。"
                ),
                "template": rule.get("template", []),
                "section_policy": rule.get("section_policy", []),
                "update_rules": rule.get("update_rules", []),
                "current_char_count": len(current_content),
            }
        )
    return inventory


def current_generated_chapter_payload(
    *,
    project_root: Path,
    volume_number: str,
    chapter_number: str,
    chapter_text: str,
) -> dict[str, Any]:
    return {
        "label": "当前章节正文",
        "file_name": f"{chapter_number}.txt",
        "file_path": str(rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]),
        "content": chapter_text.strip(),
    }


def chapter_cache_prefix_fields(
    *,
    stable_global_docs: dict[str, dict[str, Any]],
    stable_volume_docs: dict[str, dict[str, Any]],
    rolling_global_docs: dict[str, dict[str, Any]],
    rolling_volume_docs: dict[str, dict[str, Any]],
    five_chapter_review_docs: list[dict[str, Any]],
    stable_chapter_docs: dict[str, dict[str, Any]],
    current_generated_chapter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "stable_injected_global_docs": stable_global_docs,
        "stable_injected_volume_docs": stable_volume_docs,
        "rolling_injected_global_docs": rolling_global_docs,
        "rolling_injected_volume_docs": rolling_volume_docs,
        "rolling_injected_group_docs": five_chapter_review_docs,
        # Keep the heavier long-lived docs ahead of chapter-born artifacts so a
        # newly produced outline/review file does not cut off cache reuse for
        # the larger shared context that already existed before this phase.
        "stable_injected_chapter_docs": stable_chapter_docs,
    }
    if current_generated_chapter is not None:
        fields["current_generated_chapter"] = current_generated_chapter
    return fields

def latest_work_target(
    instruction: str,
    *,
    required_tool: str | None = None,
    forbidden_tool: str | None = None,
) -> dict[str, Any]:
    target: dict[str, Any] = {
        "type": "latest_user_input",
        "instruction": instruction,
    }
    if required_tool:
        target["required_tool"] = required_tool
    if forbidden_tool:
        target["forbidden_tool"] = forbidden_tool
    return target

def print_call_artifact_report(
    call_label: str,
    artifacts: list[tuple[str, Path]],
    changed_keys: list[str],
) -> None:
    print_progress(f"{call_label} 产出物：")
    if artifacts:
        for label, path in artifacts:
            content = read_text_if_exists(path).strip()
            print_progress(f"  - {label} -> {path}（字符数约 {len(content)}）")
    else:
        print_progress("  - 无。")

    print_progress(f"{call_label} 改动文档：")
    if changed_keys:
        for key in changed_keys:
            print_progress(f"  - {doc_label_for_key(key)}")
    else:
        print_progress("  - 无，本次生成结果与现有文件一致。")

def build_phase_request_payload(
    *,
    phase_key: str,
    project_root: Path,
    volume_material: dict[str, Any],
    volume_number: str,
    chapter_number: str,
    catalog: dict[str, dict[str, Any]],
    chapter_text: str = "",
    chapter_text_revision: bool = False,
) -> tuple[dict[str, Any], list[str], list[str]]:
    paths = rewrite_paths(project_root, volume_number, chapter_number)
    selection = PHASE_DOC_SELECTIONS[phase_key]
    five_chapter_review_docs, included_five_reviews, omitted_five_reviews = load_relevant_five_chapter_review_docs(
        project_root,
        volume_material=volume_material,
        chapter_number=chapter_number,
    )
    stable_global_docs, rolling_global_docs, included_globals, omitted_globals = prepare_cache_ordered_injected_docs(
        catalog,
        selection["global"],
        category="global",
    )
    stable_volume_docs, rolling_volume_docs, included_volumes, omitted_volumes = prepare_cache_ordered_injected_docs(
        catalog,
        selection["volume"],
        category="volume",
    )
    stable_chapter_docs, rolling_chapter_docs, included_chapters, omitted_chapters = prepare_cache_ordered_injected_docs(
        catalog,
        selection["chapter"],
        category="chapter",
    )

    included_docs = [*included_globals, *included_volumes, *included_chapters, *included_five_reviews]
    omitted_docs = [*omitted_globals, *omitted_volumes, *omitted_chapters, *omitted_five_reviews]
    reference_chapter = get_chapter_material(volume_material, chapter_number)
    reference_char_count = len(reference_chapter["text"])
    source_file_path = str(reference_chapter.get("file_path") or reference_chapter.get("file_name") or f"{chapter_number}.txt")
    source_bundle = "\n".join(
        [
            f"[章节文件 {reference_chapter['file_name']}]",
            f"章节编号：{reference_chapter['chapter_number']}",
            f"文件路径：{source_file_path}",
            str(reference_chapter["text"]),
        ]
    )
    source_char_count = len(source_bundle)
    min_target_chars = max(1, reference_char_count - 300)
    max_target_chars = max(min_target_chars, reference_char_count + 300)

    if phase_key == "phase1_outline":
        payload = build_payload_with_cache_layers(
            shared_prefix_fields=chapter_cache_prefix_fields(
                stable_global_docs=stable_global_docs,
                stable_volume_docs=stable_volume_docs,
                rolling_global_docs=rolling_global_docs,
                rolling_volume_docs=rolling_volume_docs,
                five_chapter_review_docs=five_chapter_review_docs,
                stable_chapter_docs=stable_chapter_docs,
            ),
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "章纲策划编辑",
                    "task": "只生成当前章的章纲 Markdown。",
                    "required_file": f"{chapter_number}_chapter_outline.md",
                },
                "reference_chapter_metrics": {
                    "source_title": reference_chapter["source_title"],
                    "source_char_count": reference_char_count,
                    "target_length_guideline": "章纲粒度应服务于后续正文保持与参考源当前章相近的篇幅和节奏。",
                },
                "requirements": [
                    "章纲必须体现与参考源当前章的功能映射关系，但不能照搬原名词。",
                    "章纲必须先把参考源抽象成剧情功能，再在新书体系下做差异化重建；不能写成参考源桥段的顺序平移或换词版复述。",
                    "章纲要能直接服务后续正文生成与审核返工。",
                    "章纲标题必须重新命名，不得与参考源标题同构、近义或只做换词式改名。",
                    "章纲 Markdown 必须显式写出“差异化重建要点”或等价小节，至少列出 3 处主动拉开与参考源距离的设计。",
                    "章纲粒度要贴近参考源当前章，不要为了发挥把单章扩成更多场景、更多推进点或更多转折层次。",
                    "章纲应尽量对齐参考源当前章的场景数量、冲突层级、叙事节奏与收尾功能，但开场切入、冲突载体、信息揭露顺序、收尾挂钩中至少要重建其中 2 项，不能整体镜像参考源。",
                ],
            },
            trailing_doc_fields={
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "source_bundle_policy": "当前阶段显式注入参考源章节原文，供章纲映射、篇幅与节奏参照。",
                "source_files": source_context_inventory(volume_material, chapter_number),
                "source_char_count": source_char_count,
                "current_chapter_source_bundle": source_bundle,
                "latest_work_target": latest_work_target(
                    "这是本次请求的最新工作目标：只生成当前章的章纲 Markdown。必须调用 result，不要调用 write/edit/apply_patch 文档工具。",
                    required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
                ),
            },
        )
        return payload, included_docs, omitted_docs

    if phase_key == "phase2_chapter_text":
        writing_skill = load_chapter_writing_skill_reference()
        if chapter_text_revision:
            current_generated_chapter = current_generated_chapter_payload(
                project_root=project_root,
                volume_number=volume_number,
                chapter_number=chapter_number,
                chapter_text=chapter_text,
            )
            payload = build_payload_with_cache_layers(
                shared_prefix_fields=chapter_cache_prefix_fields(
                    stable_global_docs=stable_global_docs,
                    stable_volume_docs=stable_volume_docs,
                    rolling_global_docs=rolling_global_docs,
                    rolling_volume_docs=rolling_volume_docs,
                    five_chapter_review_docs=five_chapter_review_docs,
                    stable_chapter_docs=stable_chapter_docs,
                    current_generated_chapter=current_generated_chapter,
                ),
                request_fields={
                    "document_request": {
                        "phase": phase_key,
                        "role": "章节仿写修订作者",
                        "task": "基于当前章现有正文、当前章上下文与审核意见，对已有章节正文做增量改写/修改。",
                        "required_file": str(rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]),
                    },
                    "reference_chapter_metrics": {
                        "source_char_count": reference_char_count,
                        "target_char_count_range": [min_target_chars, max_target_chars],
                    },
                    "writing_skill_reference": writing_skill,
                    "requirements": [
                        "必须把注入的写作规范 skill 作为当前章正文修订的主写作规则。",
                        "这是基于现有正文的修订任务，不是从零整篇重写任务。",
                        "如果当前文件已经存在，必须按修改意图使用 edit 或 patch 对现有正文做局部或分段修改；不要用整篇写入覆盖旧正文。",
                        "替换、改写、补强、删除、重组或插入内容时，按可定位性与最小改动原则自行选择 edit 或 patch。",
                        "正文返修的重点是优化问题段落和内容，修复语言、节奏、逻辑、衔接、信息表达与人物状态，而不是只做机械删减。",
                        "如果问题主要是语言、节奏、AI 感或表达不稳，优先改写原段、补强衔接、重写句群和调整节奏；如果需要删除，也应同步保证对应场景功能、人物动机、信息揭露和收尾作用仍然完整。",
                        "优先保留未变化段落，只修改受审核意见影响的局部；只有在局部无法修正时，才扩大修改范围。",
                        "正文修订后必须直接采用文笔写作风格文档规定的写作风格来改写，不要写解释说明或提纲。",
                        "修订时不能把参考源的人名、地名、宗门、术语原样照搬。",
                        "当前阶段不直接提供参考源原文；修订时必须优先执行章纲中的差异化重建设计，不要自行回退成参考源桥段的近义复述或结构平移。",
                        "严禁把正文修订成参考源桥段的换名重写；不得出现连续段落的近义复述、场景顺序平移、信息揭露顺序镜像或对话功能一一映射。",
                        "修订后的正文必须能承接章纲、卷纲、全局大纲、世界模型与当前状态文档，人物关系、术语、世界观和剧情推进不得偏离这些注入文档。",
                        f"修订后的正文字符数应尽量与参考源当前章接近，通常控制在约 {min_target_chars}-{max_target_chars} 字符；如果进行了删除或重组，也要保持总体篇幅不要明显缩水或明显扩写。",
                        "修订后的正文必须同时贴合文笔写作风格文档中的这些维度：爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式。",
                        "不得沿用参考源的章节标题、人物名、地点名、事件名、物品名、数值体系和具体数值；如果正文出现标题式文本或强识别设定，也必须转换为新书体系下的对应表达。",
                    ],
                },
                trailing_doc_fields={
                    "rolling_injected_chapter_docs": rolling_chapter_docs,
                    "update_target_files": chapter_text_target_inventory(
                        rewrite_paths(project_root, volume_number, chapter_number),
                        chapter_text,
                    ),
                    "latest_work_target": latest_work_target(
                        "这是本次请求的最新工作目标：对当前章现有正文做增量修订。必须先调用 write/edit/apply_patch 文档工具落盘修订，然后调用 result 提交阶段完成摘要。",
                        required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
                    ),
                },
            )
            return payload, included_docs, omitted_docs
        payload = build_payload_with_cache_layers(
            shared_prefix_fields=chapter_cache_prefix_fields(
                stable_global_docs=stable_global_docs,
                stable_volume_docs=stable_volume_docs,
                rolling_global_docs=rolling_global_docs,
                rolling_volume_docs=rolling_volume_docs,
                five_chapter_review_docs=five_chapter_review_docs,
                stable_chapter_docs=stable_chapter_docs,
            ),
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "章节仿写作者",
                    "task": "只生成当前章的完整仿写章节正文。",
                    "required_file": str(rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]),
                },
                "reference_chapter_metrics": {
                    "source_char_count": reference_char_count,
                    "target_char_count_range": [min_target_chars, max_target_chars],
                },
                "writing_skill_reference": writing_skill,
                "requirements": [
                    "必须把注入的写作规范 skill 作为当前章正文仿写的主写作规则。",
                    "正文必须直接采用文笔写作风格文档规定的写作风格来写，不要写解释说明或提纲。",
                    "不能把参考源的人名、地名、宗门、术语原样照搬。",
                    "当前阶段不直接注入参考源章节原文；正文必须依据章纲中的差异化重建要点、卷纲、全局大纲和状态文档，在新书体系下重建本章。",
                    "严禁把正文写成参考源桥段的换名重写；不得出现标题近似、连续段落近义复述、场景顺序平移、信息揭露顺序镜像或对话功能一一映射。",
                    "正文必须能承接章纲、卷纲、全局大纲、世界模型与当前状态文档，人物关系、术语、世界观和剧情推进不得偏离这些注入文档。",
                    f"正文目标篇幅要贴近参考源当前章，通常控制在约 {min_target_chars}-{max_target_chars} 字符；除非审核意见明确要求，不要明显扩写。",
                    "正文必须同时贴合文笔写作风格文档中的这些维度：爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式。",
                    "不得沿用参考源的章节标题、人物名、地点名、事件名、物品名、数值体系和具体数值；如果正文出现标题式文本或强识别设定，也必须转换为新书体系下的对应表达。",
                    "如果参考源当前章是短促推进型，就保持短促；如果是对话驱动型，就保持相近的对话密度；不要额外补写解释性段落、总结性抒情、世界观说明或重复心理复述来硬性扩字。",
                ],
            },
            trailing_doc_fields={
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "latest_work_target": latest_work_target(
                    "这是本次请求的最新工作目标：只生成当前章的完整仿写章节正文。必须调用 result，不要调用 write/edit/apply_patch 文档工具。",
                    required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
                ),
            },
        )
        return payload, included_docs, omitted_docs

    if phase_key == "phase2_support_updates":
        current_generated_chapter = current_generated_chapter_payload(
            project_root=project_root,
            volume_number=volume_number,
            chapter_number=chapter_number,
            chapter_text=chapter_text,
        )
        payload = build_payload_with_cache_layers(
            shared_prefix_fields=chapter_cache_prefix_fields(
                stable_global_docs=stable_global_docs,
                stable_volume_docs=stable_volume_docs,
                rolling_global_docs=rolling_global_docs,
                rolling_volume_docs=rolling_volume_docs,
                five_chapter_review_docs=five_chapter_review_docs,
                stable_chapter_docs=stable_chapter_docs,
                current_generated_chapter=current_generated_chapter,
            ),
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "连续性编辑与状态维护编辑",
                    "task": "根据刚写完的章节，按需更新人物状态卡、人物关系链、卷级剧情进程、伏笔、世界状态。",
                },
                "requirements": [
                    *support_update_general_rules(),
                    "人物关系链、卷级剧情进程、世界状态要保持固定标题，并通过贴合本书内容的二级标题来组织信息。",
                    "这些长期知识文档如果已有内容，必须优先沿用现有有效的二级标题结构，只对受当前章节影响的段落、小节或记录做 edit/patch。",
                    "不要把这些小说参考文档改写成字段表、节点表、边表、数据库表或代码化 schema。",
                    "不要每次都更新全部文档；只返回当前章节确实发生变化、必须更新的文档。",
                    "如果某个文档在当前章节没有真实变化，就不要返回该文件，也不要做空更新。",
                    "卷级剧情进程只写当前卷内容。",
                    "卷级剧情进程必须尽量按“故事线二级标题 + 固定三级标题（起始、已发生发展、关键转折、当前状态、待推进）”维护。",
                    "更新卷级剧情进程时，只修改当前受影响故事线下的对应三级标题；修改已有记录用 edit，追加新记录或按标题补充小节用 patch，不要整段覆盖整条故事线，更不要让不同故事线互相覆盖。",
                ],
            },
            trailing_doc_fields={
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "update_target_files": support_update_target_inventory(paths),
                "latest_work_target": latest_work_target(
                    "这是本次请求的最新工作目标：根据刚写完的章节按需更新配套状态文档。若有必要更新，必须先调用 write/edit/apply_patch 文档工具落盘；随后调用 result 提交阶段完成摘要。",
                    required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
                ),
            },
        )
        return payload, included_docs, omitted_docs

    if phase_key == "phase3_review":
        review_skill = load_chapter_review_skill_reference()
        current_generated_chapter = current_generated_chapter_payload(
            project_root=project_root,
            volume_number=volume_number,
            chapter_number=chapter_number,
            chapter_text=chapter_text,
        )
        payload = build_payload_with_cache_layers(
            shared_prefix_fields=chapter_cache_prefix_fields(
                stable_global_docs=stable_global_docs,
                stable_volume_docs=stable_volume_docs,
                rolling_global_docs=rolling_global_docs,
                rolling_volume_docs=rolling_volume_docs,
                five_chapter_review_docs=five_chapter_review_docs,
                stable_chapter_docs=stable_chapter_docs,
                current_generated_chapter=current_generated_chapter,
            ),
            request_fields={
                "document_request": {
                    "phase": phase_key,
                    "role": "章级审核编辑",
                    "task": "审核当前章的全部产物，并判断是否需要返工。",
                    "required_file": f"{chapter_number}_chapter_review.md",
                },
                "reference_chapter_metrics": {
                    "source_title": reference_chapter["source_title"],
                    "source_char_count": reference_char_count,
                    "target_char_count_range": [min_target_chars, max_target_chars],
                },
                "requirements": [
                    "必须把注入的 chapter_review skill 作为主要审查方向。skill 中列出的 AI 痕迹、句法污染、节奏问题、术语一致性规则优先参与判断。",
                    "重点检查参考源原人名地名是否被照搬，若照搬则不合格。",
                    "重点检查章纲标题与正文标题是否和参考源标题同构、近义或只做换词式改名；若近似，直接不合格。",
                    "重点检查正文是否存在近义复述、场景顺序平移、信息揭露顺序镜像、桥段功能一一映射、段落组织机械同构；若明显贴着参考源改写，直接不合格。",
                    "重点检查 AI 感、机械感、逻辑断裂、幻觉错位、风格偏移。",
                    "重点检查是否出现过度修饰的排比、意象堆砌、诗化抒情过量、句式整齐得过头等问题；"
                    "如果语言明显非常符合当前主流大模型常见腔调，例如像 Claude 或 GPT-4 常见的华丽总结式文风，也视为不合格。",
                    "重点检查正文篇幅是否明显偏离参考源当前章；如果出现接近翻倍的扩写、明显灌水、明显缩水，或远离目标区间，也视为不合格。",
                    "如果正文为了规避审核而删掉关键场景、人物动机、关键信息揭露或应有收尾作用，哪怕语言问题有所减少，也视为不合格。",
                    "重点检查正文是否真正符合文笔写作风格文档中对爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式的要求；若显著漂移则不合格。",
                    "如果判定存在标题近似、近义复述或结构平移，review_md 必须明确写出对应标题、场景或段落证据，而不是只给抽象结论。",
                    "如果相似性问题根源已经进入章纲，rewrite_targets 必须同时包含 chapter_outline 与 chapter_text，不能只要求返工正文。",
                    "如果不通过，rewrite_targets 必须写出需要返工的对象，例如 chapter_text、world_state 等。",
                    *review_output_contract_lines("chapter"),
                ],
            },
            trailing_doc_fields={
                "rolling_injected_chapter_docs": rolling_chapter_docs,
                "source_bundle_policy": "当前阶段显式注入参考源章节原文，供对照审核（仅用于审核判断，不得照搬名词或桥段）。",
                "source_files": source_context_inventory(volume_material, chapter_number),
                "source_char_count": source_char_count,
                "current_chapter_source_bundle": source_bundle,
                "review_skill_reference": review_skill,
                "latest_work_target": latest_work_target(
                    "这是本次请求的最新工作目标：审核当前章全部产物并提交章级审核结果。必须调用 result，不要调用 write/edit/apply_patch 文档工具。",
                    required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
                ),
            },
        )
        return payload, included_docs, omitted_docs

    fail(f"不支持的阶段：{phase_key}")

def build_volume_review_payload(
    *,
    project_root: Path,
    volume_material: dict[str, Any],
    volume_number: str,
    catalog: dict[str, dict[str, Any]],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    review_skill = load_chapter_review_skill_reference()
    stable_global_docs, rolling_global_docs, included_globals, omitted_globals = prepare_cache_ordered_injected_docs(
        catalog,
        [
            "book_outline",
            "style_guide",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "world_model",
            "world_state",
        ],
        category="global",
    )
    stable_volume_docs, rolling_volume_docs, included_volumes, omitted_volumes = prepare_cache_ordered_injected_docs(
        catalog,
        ["volume_outline", "volume_plot_progress", "volume_review"],
        category="volume",
    )
    payload = build_payload_with_cache_layers(
        shared_prefix_fields={
            "stable_injected_global_docs": stable_global_docs,
            "stable_injected_volume_docs": stable_volume_docs,
        },
        request_fields={
            "document_request": {
                "phase": "volume_review",
                "role": "卷级审核编辑",
                "task": "审核当前卷所有已生成章节与卷级文档是否一致、合理、符合风格。",
                "required_file": f"{volume_number}_volume_review.md",
            },
            "requirements": [
                "必须把注入的 chapter_review skill 作为主要审查方向。skill 中列出的 AI 痕迹、句法污染、节奏问题、术语一致性规则优先参与判断。",
                "需要检查与卷级大纲、世界模型、文风规范和全书大纲是否一致。",
                "需要检查卷内章节的文风是否稳定符合文笔写作风格文档，尤其是爽点铺垫、剧情转折、叙事节奏、情节结构、段落分割、对话密度、句长与收尾方式是否持续一致。",
                "卷级审核以已生成章节、卷纲、全局注入和必要的章节审核记录为准。",
                "如果不通过，chapters_to_revise 必须列出需要返工的章节编号。",
                "本阶段是 agent 审核阶段：如果发现可在允许目标内原地修复的问题，可以先调用 write/edit/apply_patch 修复，再继续审核并最终提交 result。",
                *review_output_contract_lines("volume"),
            ],
        },
        trailing_doc_fields={
            "rolling_injected_global_docs": rolling_global_docs,
            "rolling_injected_volume_docs": rolling_volume_docs,
            "review_skill_reference": review_skill,
            "rewritten_chapters": rewritten_chapters,
            "latest_work_target": latest_work_target(
                "这是本次请求的最新工作目标：审核当前卷所有已生成章节与卷级文档。可以先调用 write/edit/apply_patch 原地修复允许目标，最终必须调用 result 提交卷级审核结果。",
                required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
            ),
        },
    )
    included_docs = [*included_globals, *included_volumes]
    omitted_docs = [*omitted_globals, *omitted_volumes]
    return payload, included_docs, omitted_docs

def build_five_chapter_review_payload(
    *,
    project_root: Path,
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    catalog: dict[str, dict[str, Any]],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    current_batch_id = five_chapter_batch_id(chapter_numbers)
    current_batch_doc_name = f"{current_batch_id}_group_review.md"
    current_review_path = five_chapter_review_path(project_root, volume_material["volume_number"], chapter_numbers)
    current_review_content = read_text_if_exists(current_review_path).strip()
    if current_review_content:
        five_chapter_review_docs = [
            {
                "label": f"{FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}）",
                "file_name": current_review_path.name,
                "file_path": str(current_review_path),
                "content": current_review_content,
            }
        ]
        included_five_reviews = [
            f"[group] {FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}） -> "
            f"{current_review_path}（字符数约 {len(current_review_content)}）"
        ]
        omitted_five_reviews: list[str] = []
    else:
        five_chapter_review_docs = []
        included_five_reviews = []
        omitted_five_reviews = [
            f"[group] {FIVE_CHAPTER_REVIEW_NAME}（{chapter_numbers[0]}-{chapter_numbers[-1]}）：当前无上一轮审查文档。"
        ]
    stable_global_docs, rolling_global_docs, included_globals, omitted_globals = prepare_cache_ordered_injected_docs(
        catalog,
        [
            "book_outline",
            "style_guide",
            "foreshadowing",
            "character_status_cards",
            "character_relationship_graph",
            "world_model",
            "world_state",
        ],
        category="global",
    )
    stable_volume_docs, rolling_volume_docs, included_volumes, omitted_volumes = prepare_cache_ordered_injected_docs(
        catalog,
        ["volume_outline", "volume_plot_progress", "volume_review"],
        category="volume",
    )
    payload = build_payload_with_cache_layers(
        shared_prefix_fields={
            "stable_injected_global_docs": stable_global_docs,
            "stable_injected_volume_docs": stable_volume_docs,
        },
        request_fields={
            "document_request": {
                "phase": "five_chapter_alignment_review",
                "role": FIVE_CHAPTER_REVIEW_NAME,
                "task": f"审核当前这组章节 {chapter_numbers[0]}-{chapter_numbers[-1]} 是否沿着正确方向推进。",
                "required_file": current_batch_doc_name,
            },
            "requirements": [
                "重点检查剧情连续性与时间线是否顺畅，组内章节承接、事件先后和因果链是否自洽。",
                "重点检查卷纲/全书大纲对齐，确认当前组推进方向没有偏离当前卷目标和全书主线。",
                "重点检查人物关系、动机、状态是否稳定，是否与已生成正文和当前注入状态文档保持一致。",
                "重点检查术语与世界观是否一致，修炼体系、机构称谓、地理势力、规则表达不得互相打架。",
                "重点检查状态文档一致性；如果人物状态卡、人物关系链、卷级剧情进程、伏笔或世界状态与当前组正文冲突，必须指出。",
                "重点检查文体风险是否已经影响组通过；只有当语言问题明显破坏阅读流畅度、角色辨识度、叙事稳定性或世界观表达时，才作为组审阻塞项。",
                "组审依据为卷级/全局注入、相关状态文档和已生成正文，不以参考源贴近度作为通过标准。",
                "如果不通过，chapters_to_revise 必须只列当前区间内需要返工的章节编号。",
                "本阶段是 agent 审核阶段：如果发现可在允许目标内原地修复的问题，可以先调用 write/edit/apply_patch 修复，再继续审核并最终提交 result。",
                *review_output_contract_lines("group"),
            ],
        },
        trailing_doc_fields={
            "rolling_injected_global_docs": rolling_global_docs,
            "rolling_injected_volume_docs": rolling_volume_docs,
            "rolling_injected_group_docs": five_chapter_review_docs,
            "rewritten_chapters": rewritten_chapters,
            "latest_work_target": latest_work_target(
                f"这是本次请求的最新工作目标：审核当前组区间 {chapter_numbers[0]}-{chapter_numbers[-1]} 是否沿着正确方向推进。当前组只包含 {len(chapter_numbers)} 章，不得涉及下一卷章节。可以先调用 write/edit/apply_patch 原地修复允许目标，最终必须调用 result 提交组审查结果。",
                required_tool=WORKFLOW_SUBMISSION_TOOL_NAME,
            ),
        },
    )
    included_docs = [*included_globals, *included_volumes, *included_five_reviews]
    omitted_docs = [*omitted_globals, *omitted_volumes, *omitted_five_reviews]
    return payload, included_docs, omitted_docs

__all__ = [
    'chapter_text_target_inventory',
    'support_update_general_rules',
    'support_update_doc_rules',
    'support_update_target_inventory',
    'latest_work_target',
    'print_call_artifact_report',
    'build_phase_request_payload',
    'build_volume_review_payload',
    'build_five_chapter_review_payload',
]

