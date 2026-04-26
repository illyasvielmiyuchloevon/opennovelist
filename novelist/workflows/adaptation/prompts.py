from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def world_model_scope_text() -> str:
    section_text = "、".join(WORLD_MODEL_DEFAULT_SECTIONS)
    return (
        "文档是新书的全书世界观、世界知识模型和设定唯一来源，合并承载原“世界观设计”和“世界模型”的职责：既要确立目标世界观、背景故事、故事类型、角色功能位与参考源功能映射，"
        "也要沉淀到当前卷为止已知的地点、势力、能力、资源、规则、术语和世界知识。默认按 16 个二级标题组织："
        f"{section_text}。每个二级标题下可以根据实际需要继续展开多个三级标题，用于管理该栏目的不同知识子类。"
        "必须写出与原书的功能映射，但新书世界模型的主体内容必须使用新书自己的命名、数值体系、等级体系、术语体系和话语体系，不能与参考源出现相同命名、数值或话语体系；不要另建或依赖独立世界观设计文档。"
    )

def storyline_blueprint_scope_text() -> str:
    field_text = "、".join(STORYLINE_BLUEPRINT_INLINE_FIELDS)
    return (
        "文档是全书故事线蓝图，用于把参考源的主线、关键支线、反派线、终局线、跨卷线与新增故事线转化为新书可长期复用的连续性设计。"
        "文档采用“故事线为 owner”的结构：二级标题只允许用于不同故事线，标题建议写成“## 故事线：<名称>”，不按卷汇总成实时进展。"
        f"每条故事线下用紧凑标签或短条目维护：{field_text}；不要使用固定三级标题模板。"
        "后续卷更新时在对应故事线内增量补充或修正长期有效的卷际连续性与后续约束，不新增待补全占位。"
        "禁止记录章内事件、场景清单、战斗过程、逐章剧情、卷内时间线或最新进度。"
        "不要写成运行时状态表、故事进程文档、单卷总结或卷级剧情进程的替代品。"
    )

def source_contamination_guardrails() -> list[str]:
    return [
        "【强制禁止】参考源只能作为功能映射来源，绝不能把原书内容直接写成新书内容。",
        "【强制禁止】严禁把参考源的人名、地名、姓氏、势力名、宗门名、家族名、事件名、功法名、招式名、道具名、等级名、职业名、专用术语、数值体系直接写成新书设定。",
        "【强制禁止】严禁把参考源的称谓口吻、固定句式、标志性台词、叙述腔调、概念话语体系直接代入新书资料；文档必须使用目标世界观下的新命名、新术语和新表达。",
        "【允许但必须转换】映射关系是必须保留的，但只能写成功能映射或抽象职责映射，例如“参考源对应的师门压迫功能 -> 新书宗门规训压力”“参考源对应的升级资源功能 -> 新书灵脉资源压力”。",
        "【禁止混淆】不得把原作实体名保留为新书实体；不得把原作实体名、原作术语或原作话语体系保留为新书实体；如果确需说明参考源侧，只能放在“参考源功能映射”语境中，且不得作为新书设定主体。",
        "【污染清理】如果当前已有资料里残留参考源实体名或话语体系，本次更新必须优先用 edit 清理或替换；不能继续沿用污染内容。",
    ]

def source_material_boundary(doc_label: str) -> dict[str, Any]:
    return {
        "applies_to": doc_label,
        "core_boundary": "参考源只提供情节功能、结构功能、爽点功能、角色功能位和设定功能的映射依据，不是新书资料正文。",
        "mapping_required": "必须保留映射，但映射要写成“参考源功能 -> 新书设计”的转换关系。",
        "hard_ban": [
            "不得把原书人物、地点、势力、事件、物品、功法、等级、术语、数值体系直接当成新书人物、地点、势力、事件、物品、功法、等级、术语或数值体系。",
            "不得把原书的称谓口吻、标志性台词、叙述腔调、概念话语体系直接当成新书的话语体系。",
            "不得把参考源原句、原段落或原设定说明直接搬入新书资料文档。",
        ],
        "required_conversion": [
            "新书设定主体必须使用新书自己的姓名、地名、势力名、术语名、等级名、事件名和表达方式。",
            "参考源侧信息只能作为功能映射依据，不得占据新书设定正文的位置。",
            "如果已有目标文档把原书内容当成新书内容，本次必须优先清理污染，再补充新书设定。",
        ],
    }

def print_request_context_summary(
    *,
    doc_label: str,
    current_doc_key: str,
    volume_material: dict[str, Any],
    current_docs: dict[str, str],
    loaded_files: list[dict[str, Any]],
    source_char_count: int,
    previous_response_id: str | None,
) -> None:
    print_progress(f"{doc_label} 本次请求将携带以下内容：")
    print_progress("  提示词缓存共享前缀：项目上下文 + 阶段规则 + 文件清单 + 整卷原文。")
    print_progress(
        f"  当前卷整卷原文：{len(volume_material['chapters'])} 个章节文件，"
        f"{len(volume_material['extras'])} 个补充文件，总字符数约 {source_char_count}。"
    )

    extra_names = [item["file_name"] for item in volume_material["extras"]]
    if extra_names:
        extra_chunks = chunk_text_items(extra_names, 8)
        for index, chunk in enumerate(extra_chunks, start=1):
            print_progress(f"  补充文件[{index}/{len(extra_chunks)}]：{chunk}")
    else:
        print_progress("  补充文件：无。")

    chapter_names = [item["file_name"] for item in volume_material["chapters"]]
    chapter_chunks = chunk_text_items(chapter_names, 10)
    for index, chunk in enumerate(chapter_chunks, start=1):
        print_progress(f"  章节文件[{index}/{len(chapter_chunks)}]：{chunk}")

    print_progress(f"  已附带文件清单：{len(loaded_files)} 项。")

    for doc_key, label in (
        ("world_model", "世界模型"),
        ("style_guide", "文笔写作风格"),
        ("book_outline", "全书大纲"),
        ("foreshadowing", "伏笔文档"),
        ("storyline_blueprint", "全书故事线蓝图"),
    ):
        content = (current_docs.get(doc_key) or "").strip()
        file_name = GLOBAL_FILE_NAMES[doc_key]
        if doc_key == current_doc_key:
            if content:
                print_progress(f"  目标文件 {file_name}（{label}）：当前内容将通过 target_file.current_content 附带，字符数约 {len(content)}。")
            else:
                print_progress(f"  目标文件 {file_name}（{label}）：当前为空。")
        elif content:
            print_progress(f"  全局注入 {file_name}（{label}）：已附带，字符数约 {len(content)}。")
        else:
            print_progress(f"  全局注入 {file_name}（{label}）：当前为空。")

    if previous_response_id:
        print_progress(f"  阶段会话：沿用 previous_response_id={previous_response_id}")
    else:
        print_progress("  阶段会话：本阶段首次请求，将创建新的阶段会话。")

def should_generate_style_guide(volume_number: str) -> bool:
    return volume_number == "001"

def build_document_request(doc_key: str) -> dict[str, Any]:
    request_specs: dict[str, dict[str, Any]] = {
        "style_guide": {
            "role": "资深网络小说文风策划编辑",
            "task": "当前任务只产出 1 份文笔写作风格文档正文。",
            "scope": (
                "文档必须覆盖写作方式、文风、情绪渲染方式、爽点铺垫与释放方式、剧情转折方式、叙事节奏、情节结构、"
                "符号使用习惯、段落分割、章节结尾钩子与收尾方式、句长偏好、对话密度、描写密度、铺垫、高潮、收束，"
                "并说明与原书的功能映射，避免只给空泛风格形容词。"
            ),
        },
        "book_outline": {
            "role": "资深网络小说总纲编辑",
            "task": "当前任务只产出 1 份全书大纲文档正文。",
            "scope": (
                "把当前卷纳入整本书的大纲中，但只能增量补写已读取参考源的卷。"
                "未读取的卷只能写成占位，或暂时不写，等后续阶段再补充，不得提前展开细纲。"
            ),
        },
        "storyline_blueprint": {
            "role": "资深网络小说全书故事线蓝图编辑",
            "task": "当前任务只产出 1 份全书故事线蓝图文档正文。",
            "scope": storyline_blueprint_scope_text(),
        },
        "foreshadowing": {
            "role": "资深网络小说伏笔统筹编辑",
            "task": "当前任务只产出 1 份伏笔文档正文。",
            "scope": (
                "文档是资料适配阶段的伏笔设计索引，只记录全书级或卷级伏笔的参考源功能映射、新书伏笔设计、埋设意图、适用范围和后续呼应方向。"
                "不要判断伏笔是否已经推进或兑现，不新增、不改写运行时推进、兑现、闭合等记录；如果文件里已有章节工作流写入的运行时记录，必须视为受保护内容并原样保留。"
            ),
        },
        "world_model": {
            "role": "资深网络小说世界观与世界知识建模统筹编辑",
            "task": "当前任务只产出 1 份世界模型文档正文。",
            "scope": world_model_scope_text(),
        },
        "volume_outline": {
            "role": "资深小说分卷策划编辑",
            "task": "当前任务只产出 1 份当前卷的卷级大纲正文。",
            "scope": "只产出当前卷的卷级大纲文档。",
        },
    }
    if doc_key not in request_specs:
        fail(f"不支持的文档类型：{doc_key}")
    spec = {"doc_key": doc_key, **request_specs[doc_key]}
    spec["source_material_boundary"] = source_material_boundary(spec["task"])
    return spec

def build_document_plan(volume_number: str) -> list[dict[str, Any]]:
    if should_generate_style_guide(volume_number):
        return [
            {"key": "world_model", "label": "世界模型文档", "scope": "global"},
            {"key": "style_guide", "label": "文笔写作风格文档", "scope": "global"},
            {"key": "book_outline", "label": "全书大纲文档", "scope": "global"},
            {"key": "foreshadowing", "label": "伏笔文档", "scope": "global"},
            {"key": "storyline_blueprint", "label": "全书故事线蓝图文档", "scope": "global"},
            {"key": "volume_outline", "label": "卷级大纲文档", "scope": "volume"},
        ]
    return [
        {"key": "world_model", "label": "世界模型文档", "scope": "global"},
        {"key": "book_outline", "label": "全书大纲文档", "scope": "global"},
        {"key": "foreshadowing", "label": "伏笔文档", "scope": "global"},
        {"key": "storyline_blueprint", "label": "全书故事线蓝图文档", "scope": "global"},
        {"key": "volume_outline", "label": "卷级大纲文档", "scope": "volume"},
    ]

def build_injected_global_docs(
    current_docs: dict[str, str],
    *,
    exclude_keys: set[str] | None = None,
) -> dict[str, str]:
    excluded = exclude_keys or set()
    injected_docs: dict[str, str] = {}
    for doc_key in GLOBAL_INJECTION_DOC_ORDER:
        if doc_key in excluded:
            continue
        injected_docs[doc_key] = current_docs.get(doc_key, "").strip()
    return injected_docs

def build_stage_project_context(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
) -> dict[str, Any]:
    processed_before_current = list(manifest.get("processed_volumes", []))
    processed_including_current = sorted(
        {str(item) for item in [*processed_before_current, volume_material["volume_number"]]}
    )
    return {
        "new_book_title": manifest["new_book_title"],
        "target_worldview": manifest["target_worldview"],
        "current_volume": volume_material["volume_number"],
        "total_volumes": manifest["total_volumes"],
        "processed_volumes_before_current": processed_before_current,
        "processed_volumes_including_current": processed_including_current,
        "remaining_volume_count": max(manifest["total_volumes"] - len(processed_including_current), 0),
        "style_mode": manifest["style"]["mode"],
        "style_reference": style_reference_context(manifest),
        "protagonist_mode": manifest["protagonist"]["mode"],
        "protagonist_context": protagonist_context(manifest),
    }

def build_stage_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    loaded_files: list[dict[str, Any]],
    source_bundle: str,
    source_char_count: int,
) -> str:
    stage_shared_payload = {
        "project": build_stage_project_context(manifest, volume_material),
        "stage_rules": [
            "这一卷的全部生成都属于同一个阶段会话，请沿用同一会话的上下文连续工作。",
            "全书大纲、全书故事线蓝图文档、伏笔文档、世界模型文档是每阶段都要注入的全局资料；世界模型已经合并承载世界观设计职责。",
            "卷级大纲不作为全局注入资料，不要把卷级大纲当成下一份文档的依赖前提。",
            "伏笔文档是全局伏笔设计索引；资料适配阶段只维护设计意图、功能映射和后续呼应方向，章节工作流已有运行时记录必须原样保留。",
            "所有映射关系都写成功能映射，不要照抄参考源原文句子。",
            "严禁把参考源的人名、地名、势力名、事件名、专用术语、等级体系、称谓口吻或话语体系直接代入新书资料；必须转换为目标世界观下的新命名与新表达。",
            "本阶段的每一次请求都会重新附带当前卷全部文件原文与文件清单。",
        ],
        "loaded_files": loaded_files,
        "source_char_count": source_char_count,
        "current_volume_source_bundle": source_bundle,
    }
    return (
        "## Stage Shared Context\n"
        + json.dumps(stage_shared_payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )

def build_payload_with_trailing_docs(
    *,
    stable_fields: dict[str, Any],
    trailing_doc_fields: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    payload.update(stable_fields)
    payload.update(trailing_doc_fields)
    return payload

def document_output_path(paths: dict[str, Path], doc_key: str) -> Path:
    if doc_key in paths:
        return paths[doc_key]
    fail(f"未找到文档输出路径：{doc_key}")

def build_target_file_context(
    *,
    doc_key: str,
    output_path: Path,
    current_content: str,
) -> dict[str, Any]:
    return {
        "file_key": doc_key,
        "file_name": output_path.name,
        "file_path": str(output_path),
        "exists": output_path.exists(),
        "current_content": current_content.strip(),
        "preferred_mode": "edit_or_patch" if current_content.strip() else "write",
        "tool_selection_policy": (
            "按修改意图选择工具：改已有正文、清理名称术语或替换已有段落用 edit；"
            "插入新段落、追加新条目、按标题补充或替换小节正文用 patch；"
            "文件为空或首次创建时才用 write。"
        ),
    }

def generate_document_operation(
    client: OpenAI,
    model: str,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    current_docs: dict[str, str],
    *,
    doc_key: str,
    output_path: Path,
    stage_shared_prompt: str,
    previous_response_id: str | None,
    prompt_cache_key: str,
) -> tuple[document_ops.DocumentOperationCallResult, str | None]:
    injected_globals = build_injected_global_docs(current_docs, exclude_keys={doc_key})
    document_request = build_document_request(doc_key)
    target_file = build_target_file_context(
        doc_key=doc_key,
        output_path=output_path,
        current_content=current_docs.get(doc_key, ""),
    )

    if doc_key == "style_guide":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "source_material_boundary": source_material_boundary("文笔写作风格文档"),
                "required_file": GLOBAL_FILE_NAMES["style_guide"],
                "requirements": [
                    *source_contamination_guardrails(),
                    "标题稳定，适合后续工作流长期注入。",
                    "这是全书级写作风格文档，仅在第一卷阶段生成与定稿。",
                    "必须明确提炼爽点铺垫、剧情转折、叙事节奏、情节结构、符号使用习惯、段落分割、对话密度、句长、收尾方式这些可执行维度。",
                    "不要只写抽象评价，要写成后续章节生成与审核可以直接照着执行的风格规则。",
                    "如果当前文件已存在，请按修改意图选择 edit 或 patch；不要为了重组措辞而整篇重写。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "book_outline":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "source_material_boundary": source_material_boundary("全书大纲文档"),
                "required_file": GLOBAL_FILE_NAMES["book_outline"],
                "requirements": [
                    *source_contamination_guardrails(),
                    "这是整本书的大纲文档，不是单卷总结。",
                    "当前阶段只允许新增或改写当前卷对应的全书大纲段落，以及与已处理卷直接相关的衔接说明。",
                    "只展开 processed_volumes_including_current 中列出的卷；未读取参考源的后续卷必须二选一：要么不写，要么仅保留“第X卷：待后续阶段补全”这类占位说明。",
                    "未读取卷不得出现剧情梗概、角色推进、冲突设计、伏笔安排、高潮设计或结局走向。",
                    "如果旧版全书大纲里已经提前写了未读取卷的详细内容，本次要把那些未读取卷删掉，或降级为占位状态，不能继续保留伪细纲。",
                    "第一卷阶段尤其不能提前写第二卷及之后的详细大纲。",
                    "全书大纲是卷级方向文档，不要把每章剧情、每场战斗、每个小事件都写进去；当前卷通常保留 6-10 个关键推进点即可。",
                    "如果全书大纲已经和故事线蓝图、伏笔文档或卷级大纲重复，本次不要继续追加同类重复内容，只补充大纲层真正需要的方向和约束。",
                    "按修改意图使用 edit 或 patch 对当前卷对应段落做增量修改，不要把整份全书大纲改写成只剩最近一卷的信息。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "storyline_blueprint":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "source_material_boundary": source_material_boundary("全书故事线蓝图文档"),
                "required_file": GLOBAL_FILE_NAMES["storyline_blueprint"],
                "requirements": [
                    *source_contamination_guardrails(),
                    "这是全书故事线蓝图文档，用于沉淀参考源故事线的功能映射、新书全书走向与跨卷设计，不是实时进度账本、卷级剧情进程或章节细纲。",
                    "文档只覆盖到当前卷为止仍然有效的重要故事线，包括主线、关键支线、反派线、终局线、跨卷线，以及当前卷新出现且会长期影响后续仿写的故事线。",
                    "如果当前文件已存在，必须按修改意图使用 edit 或 patch 工具做增量更新，不得整篇覆盖式重写全书故事线蓝图。",
                    "每条故事线必须使用独立二级标题管理，标题建议写成“## 故事线：<名称>”；二级标题只允许用于不同故事线，不要把多条故事线混写在同一个总段落里。",
                    "每条故事线下只使用紧凑标签或短条目维护：功能定位、参考源功能映射、新书主轴、卷际连续性、后续约束；不要强制生成三级标题模板。",
                    "后续卷更新时，优先定位已有故事线并在该故事线内部增量补充或修正长期有效的卷际连续性与后续约束；只有当前卷引入了长期有效的新故事线时，才新增一个二级标题。",
                    "不得按“#### 第001卷”“#### 第002卷”建立分卷模板，也不得写未读取后续卷的待补全占位。",
                    "已处理内容应保持故事线设计信息稳定；后续卷不得把旧内容改写成章级细节，也不得把已有故事线设计改到信息缺失。",
                    "严禁在全书故事线蓝图中记录章内事件、场景清单、战斗过程、逐章剧情或普通过场。",
                    "如果当前卷引入了新的故事线，只新增长期有效的故事线；一次性事件、单章冲突或普通支线不进入全书故事线蓝图。",
                    "如果已有内容写成故事进程、最新状态、任务清单、单卷总结或分卷流水账，本次要改回故事线连续性蓝图。",
                    "如果已有内容和全书大纲、卷级大纲、伏笔文档或卷级剧情进程重复，本次不要继续追加同类重复内容，只补充这些文档之外真正需要的故事线连续性信息。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "foreshadowing":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "source_material_boundary": source_material_boundary("伏笔文档"),
                "required_file": GLOBAL_FILE_NAMES["foreshadowing"],
                "requirements": [
                    *source_contamination_guardrails(),
                    "优先保持伏笔设计索引的可追踪性、后续工作流可读性和体量可控。",
                    "请基于全书大纲、世界模型文档和当前卷原文上下文补充更新。",
                    "只记录会影响后续章节、后续卷、角色关系、世界真相或核心爽点兑现的全书级/卷级伏笔设计。",
                    "每条伏笔应写清参考源承担的功能、新书对应设计、埋设意图、适用范围和后续呼应方向；不要写成章节运行时进度或状态表。",
                    "单章内完成的小提示、普通物品、一次性情绪铺垫和普通剧情细节，不要作为资料适配阶段新增的全局伏笔设计。",
                    "不要判断或记录伏笔是否已经推进、兑现、闭合；资料适配阶段只做设计索引，不做伏笔状态管理。",
                    "如果目标文件已有章节工作流写入的运行时记录，必须原样保留这些记录；不得删除、归并、重命名或根据资料适配审核意见改写它们。",
                    "如果已有伏笔文档的资料适配设计索引已经包含同类设计，本次不要继续追加同等粒度的重复条目；只补充当前卷确实新增的全书级/卷级设计意图。",
                    "按修改意图使用 edit 或 patch 做新增设计、补充映射或局部修订。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "world_model":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "source_material_boundary": source_material_boundary("世界模型文档"),
                "required_file": GLOBAL_FILE_NAMES["world_model"],
                "requirements": [
                    *source_contamination_guardrails(),
                    "这是新书的全书世界观、世界知识模型和设定唯一来源；章节生成、卷纲、故事线、伏笔和审核都应以本文件中的新书设定为准。",
                    "世界模型主体必须设计新书自己的命名系统、数值系统、等级体系、能力术语、势力称谓、地点命名、资源命名和叙述话语体系；不得沿用参考源的同名实体、同一套数值、同一套等级名或同一套概念话语。",
                    "参考源内容只能作为功能映射输入，必须转换成“参考源功能 -> 新书世界模型设计”；不得把参考源设定说明直接复制为新书设定说明。",
                    "这是全书级世界模型文档，并合并承载世界观设计职责；采用按卷增量维护方式，每卷只补充、修正到当前卷为止新增或变化的世界知识与世界观设定。",
                    "如果当前文件已存在，必须按修改意图使用 edit 或 patch 做增量更新，不得整篇覆盖式重写世界模型。",
                    "未变化的世界观设定、背景故事、故事类型、角色功能位、世界知识、术语、势力、地点、历史背景与规则结构必须保留。",
                    "本次只允许补充、修正与当前卷直接相关的世界观和世界知识，不要把文档改写成只剩最近一卷。",
                    "必须覆盖原世界观设计阶段独有职责：目标世界观设定、背景故事、能力设计、道具设计、势力设计、角色功能位、故事类型与原书功能映射。",
                    "默认使用 scope 中给出的 16 个二级标题组织世界模型；如果某些栏目当前卷暂无信息，可以保留简短占位说明，但不要删除默认标题。",
                    "每个二级标题下可以根据实际小说内容需要展开多个三级标题，用于细分该栏目的不同知识类型；不要强行把所有内容挤在一个段落里。",
                    "只有当默认 16 个栏目确实无法容纳本书特有世界知识时，才使用“可扩展世界专题”新增专题。新增专题必须长期可复用。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    if doc_key == "volume_outline":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "source_material_boundary": source_material_boundary("卷级大纲文档"),
                "required_file": f"{volume_material['volume_number']}_volume_outline.md",
                "requirements": [
                    *source_contamination_guardrails(),
                    "卷纲要包含本卷定位、主要冲突、角色推进、高潮设计、结尾钩子、与参考源卷级功能映射关系。",
                    "这是卷级注入文档，不要改写成全书文档。",
                    "卷纲要服务后续章节仿写，不要把 50 章逐章展开成超长流水账；优先写可执行的卷级结构、阶段推进和关键映射。",
                    "卷纲应保持卷级规划粒度，避免逐章流水账；必要信息必须完整保留。",
                    "如果当前卷纲文件已存在，请按修改意图选择 edit 或 patch；文件为空或需要首次创建结构时才整篇写入。",
                ],
            },
            trailing_doc_fields={
                "target_file": target_file,
                "injected_global_docs": injected_globals,
            },
        )
        return call_document_operation_response(
            client,
            model,
            COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
            stage_shared_prompt + json.dumps(payload, ensure_ascii=False, indent=2),
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
        )

    fail(f"不支持的文档类型：{doc_key}")

def adaptation_doc_label(doc_key: str) -> str:
    labels = {
        "world_design": "世界观设计",
        "world_model": "世界模型",
        "style_guide": "文笔写作风格",
        "book_outline": "全书大纲",
        "foreshadowing": "伏笔设计索引",
        "storyline_blueprint": "全书故事线蓝图",
        "volume_outline": "卷级大纲",
    }
    return labels.get(doc_key, doc_key)

def adaptation_doc_scope(doc_key: str) -> str:
    return "volume" if doc_key == "volume_outline" else "global"

__all__ = [
    'world_model_scope_text',
    'storyline_blueprint_scope_text',
    'source_contamination_guardrails',
    'source_material_boundary',
    'print_request_context_summary',
    'should_generate_style_guide',
    'build_document_request',
    'build_document_plan',
    'build_injected_global_docs',
    'build_stage_project_context',
    'build_stage_shared_prompt',
    'build_payload_with_trailing_docs',
    'document_output_path',
    'build_target_file_context',
    'generate_document_operation',
    'adaptation_doc_label',
    'adaptation_doc_scope',
]
