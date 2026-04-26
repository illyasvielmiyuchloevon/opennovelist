from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .document_generation import call_document_operation_response
from .materials import chunk_text_items, protagonist_context, style_reference_context


def world_model_scope_text() -> str:
    section_text = "、".join(WORLD_MODEL_DEFAULT_SECTIONS)
    return (
        "文档是新书的全书世界观、世界知识模型和设定唯一来源；只沉淀后续章节会反复引用、会影响规则判断或设定一致性的稳定世界知识。"
        "完整卷原文只是判断依据，不是待抽取清单。默认二级标题只是可选组织参考，不是必须填满的结构："
        f"{section_text}。没有稳定世界知识的标题不要硬写，已有空栏目可以保留为空或暂不出现。"
        "世界模型只允许写世界观与世界知识：背景、历史源流、地图地点、势力制度、社会结构、能力体系、资源规则、术语、世界真相和公开常识。"
        "故事类型、角色功能位、卷内剧情衔接、人物关系推进、主角状态和故事进程不属于世界模型。"
        "严禁写卷内已发生大事件、主角个人战绩、考试排名、活动结果、家庭进展、治疗进度、奖励记录、剧情推进清单或故事进程。"
        "必须写出与原书的功能映射，但新书世界模型的主体内容必须使用新书自己的命名、数值体系、等级体系、术语体系和话语体系，不能与参考源出现相同命名、数值或话语体系；不要另建或依赖独立世界观设计文档。"
        "判断命名污染时要区分通用语素、通用话语术语与参考源专用术语：通用语素和玄幻/仙侠常见通用术语可以使用；参考源自造名词、专属组合词、专用话语体系必须改名或重构。"
        "修炼境界名属于等级名和专用术语：如果参考源境界名是“XX境”，新书不能沿用相同“XX”前缀；但“境”作为通用后缀可以保留。"
    )

def source_contamination_guardrails() -> list[str]:
    return [
        "【强制禁止】参考源只能作为功能映射来源，绝不能把原书内容直接写成新书内容。",
        "【强制禁止】严禁把参考源的人名、地名、姓氏、势力名、宗门名、家族名、事件名、功法名、招式名、道具名、等级名、职业名、专用术语、数值体系直接写成新书设定。",
        "【强制禁止】严禁把参考源的称谓口吻、固定句式、标志性台词、叙述腔调、概念话语体系直接代入新书资料；文档必须使用目标世界观下的新命名、新术语和新表达。",
        "【允许但必须转换】映射关系是必须保留的，但只能写成功能映射或抽象职责映射，例如“参考源对应的师门压迫功能 -> 新书宗门规训压力”“参考源对应的升级资源功能 -> 新书灵脉资源压力”。",
        "【禁止混淆】不得把原作实体名保留为新书实体；不得把原作实体名、原作术语或原作话语体系保留为新书实体；如果确需说明参考源侧，只能放在“参考源功能映射”语境中，且不得作为新书设定主体。",
        "【污染清理】如果当前已有资料在新书设定正文中残留参考源实体名或话语体系，本次更新必须优先用 edit 清理或替换；明确标注为参考源功能映射或参考源侧说明的信息可以保留，但不得占据新书设定主体。",
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
                "文档只提炼后续章节仿写会反复使用的稳定写法规则。可以按需要涉及写作方式、文风、情绪渲染、爽点铺垫与释放、"
                "剧情转折、叙事节奏、情节结构、段落分割、章节结尾、句长、对话密度、描写密度等维度；"
                "没有稳定规律或后续执行价值的维度不要写。不要收集参考源例句、章节素材、桥段清单或泛泛风格形容词。"
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
        "foreshadowing": {
            "role": "资深网络小说伏笔统筹编辑",
            "task": "当前任务只产出 1 份伏笔文档正文。",
            "scope": (
                "文档是资料适配阶段的伏笔设计索引，只记录真正会跨章、跨卷或贯穿全书回收的伏笔设计。"
                "合格伏笔必须同时满足：当前阶段有意埋下或保留未解信息；后续存在明确触发、反转、兑现或呼应价值；不记录会破坏后续连续性。"
                "普通剧情细节、阶段性战绩、考试排名、奖励记录、资源获得、治疗进度、一次性物件、已完成小冲突、单章情绪铺垫和普通关系进展都不算全局伏笔。"
                "每条合格伏笔只记录参考源功能映射、新书伏笔设计、埋设意图、适用范围和后续呼应方向。"
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
            {"key": "volume_outline", "label": "卷级大纲文档", "scope": "volume"},
        ]
    return [
        {"key": "world_model", "label": "世界模型文档", "scope": "global"},
        {"key": "book_outline", "label": "全书大纲文档", "scope": "global"},
        {"key": "foreshadowing", "label": "伏笔文档", "scope": "global"},
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
            "全书大纲、伏笔文档、世界模型文档是每阶段都要注入的全局资料；世界模型只承载全书世界观和世界知识设定。",
            "卷级大纲不作为全局注入资料，不要把卷级大纲当成下一份文档的依赖前提。",
            "伏笔文档是全局伏笔设计索引；资料适配阶段只维护通过准入门槛的伏笔设计意图、功能映射和后续呼应方向，章节工作流已有运行时记录必须原样保留。",
            "资料适配不是从当前卷原文抽取百科资料；完整卷原文只提供判断依据，不代表要全量搬运。每份资料只写后续仿写会反复使用的稳定设计、结构规则、功能映射和约束。",
            "所有资料都按需要编写：只有对后续章节生成、审核或资料维护有实际用途的信息才写入；没有实际用途的信息应当不写、不新增，不要为了显得完整、填满结构或覆盖全部素材而硬塞内容。",
            "普通剧情事实、单章细节、阶段性成果、战绩、奖励、排名、过场信息和一次性设定不得写入全局资料。",
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
                    "按实际需要提炼爽点铺垫、剧情转折、叙事节奏、情节结构、段落分割、对话密度、句长、收尾方式等可执行维度；不要求每个维度都写，没有稳定规律的维度不写。",
                    "写成后续章节生成与审核可以直接照着执行的少量风格规则，避免风格百科、参考源例句堆积、桥段清单或泛泛评价。",
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
                    "只维护 processed_volumes_including_current 中列出的卷；未读取参考源的后续卷必须二选一：要么不写，要么仅保留“第X卷：待后续阶段补全”这类占位说明。",
                    "未读取卷不得出现剧情梗概、角色推进、冲突设计、伏笔安排、高潮设计或结局走向。",
                    "如果旧版全书大纲里已经提前写了未读取卷的详细内容，本次要把那些未读取卷删掉，或降级为占位状态，不能继续保留伪细纲。",
                    "第一卷阶段尤其不能提前写第二卷及之后的详细大纲。",
                    "全书大纲是卷级方向文档，只保留会改变全书方向、卷际衔接、角色长期目标或主线结构的关键推进；普通章节事件、普通战斗、临时小冲突不进入全书大纲。",
                    "如果当前卷没有全书层新增信息，可以不新增段落，只做必要的局部修正。",
                    "全书大纲要保持卷级粒度；不要写章级细纲、逐场景流水账、战斗过程清单、奖励清单或单章事件表。",
                    "“卷末阶段状态与衔接”只允许保留在当前已处理的最新卷段落中；当本次写入新卷时，旧卷里的“卷末阶段状态与衔接”必须删除，或把仍有效的极少量衔接约束并入新卷开篇/当前卷方向。",
                    "如果全书大纲已经和伏笔文档、卷级大纲或卷级剧情进程重复，本次不要继续追加同类重复内容，只补充大纲层真正需要的方向和约束。",
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

    if doc_key == "foreshadowing":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "source_material_boundary": source_material_boundary("伏笔文档"),
                "required_file": GLOBAL_FILE_NAMES["foreshadowing"],
                "requirements": [
                    *source_contamination_guardrails(),
                    "优先保持伏笔设计索引的可追踪性、后续工作流可读性和严格准入。",
                    "只在当前卷确实产生合格伏笔设计时补充更新；没有通过伏笔准入门槛的内容时，不新增伏笔条目。",
                    "伏笔准入门槛：必须是作者有意埋下或保留的未解信息、异常、承诺、缺口、隐性因果、身份/规则/资源线索，并且后续存在明确触发、反转、兑现或呼应价值。",
                    "不满足准入门槛的内容不得写入伏笔文档：普通剧情细节、阶段性战绩、考试排名、榜单变化、奖励记录、资源获得、治疗进度、一次性物件、已完成小冲突、普通关系进展、单章情绪铺垫、场景气氛和过场信息。",
                    "每条伏笔必须能写清“埋设内容 -> 后续触发/兑现方向”；如果写不出未来触发或兑现方向，就不是全书/卷级伏笔。",
                    "世界知识放世界模型，剧情方向放全书大纲或卷级大纲，卷内推进放卷级剧情进程；不要把这些内容伪装成伏笔。",
                    "每条合格伏笔应写清参考源承担的功能、新书对应设计、埋设意图、适用范围和后续呼应方向；不要写成章节运行时进度或状态表。",
                    "不要判断或记录伏笔是否已经推进、兑现、闭合；资料适配阶段只做设计索引，不做伏笔状态管理。",
                    "如果目标文件已有章节工作流写入的运行时记录，必须原样保留这些记录；不得删除、归并、重命名或根据资料适配审核意见改写它们。",
                    "如果已有伏笔文档的资料适配设计索引已经包含同类设计，本次不要继续追加同等粒度的重复条目；只补充当前卷确实新增且通过准入门槛的全书级/卷级伏笔设计。",
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
                    "这是新书的全书世界观、世界知识模型和设定唯一来源；章节生成、卷纲、伏笔和审核都应以本文件中的新书设定为准。",
                    "世界模型主体必须设计新书自己的命名系统、数值系统、等级体系、能力术语、势力称谓、地点命名、资源命名和叙述话语体系；不得沿用参考源的同名实体、同一套数值、同一套等级名或同一套概念话语。",
                    "命名和术语判断要区分通用语素、通用话语术语与专用话语术语：通用语素和玄幻/仙侠常见通用术语可以使用；参考源自造名词、专属组合词、标志性称谓和专用话语体系必须改名或重构。",
                    "修炼境界名称按等级名和专用术语处理：参考源若使用“XX境”，新书必须替换“XX”前缀，不能出现同前缀境界名；“境”这个通用后缀允许继续使用。",
                    "参考源内容只能作为功能映射输入，必须转换成“参考源功能 -> 新书世界模型设计”；不得把参考源设定说明直接复制为新书设定说明。",
                    "这是全书级世界模型文档，只承载世界观和世界知识设定；采用按卷增量维护方式，每卷只补充、修正当前卷揭示出的长期有效世界知识与世界观设定。",
                    "如果当前文件已存在，必须按修改意图使用 edit 或 patch 做增量更新，不得整篇覆盖式重写世界模型。",
                    "未变化且仍然有效的世界观设定、背景故事、世界知识、术语、势力、地点、历史背景与规则结构必须保留；非世界知识内容不要塞进世界模型。",
                    "本次只允许补充、修正与当前卷直接相关的世界观和世界知识，不要把文档改写成只剩最近一卷。",
                    "完整卷原文只是世界设计判断依据，不是设定清单；不要把当前卷所有地点、人物、事件、资源、术语逐项搬入世界模型。",
                    "只有后续章节会反复使用、影响规则判断或设定一致性的稳定世界知识，才进入世界模型；临时场景、一次性设定和只服务当前卷剧情的事实不进入世界模型。",
                    "世界模型严禁记录卷内已发生大事件、主角个人战绩、考试名次、榜单变化、竞技活动结果、家庭债务或治疗进度、奖励记录、角色关系进展、剧情推进清单；这些内容应属于全书大纲、卷级大纲、卷级剧情进程或章节状态，不属于世界模型。",
                    "如果当前文件中已有“卷XXX内已发生/已公开大事件”“本卷剧情进展”“主角战绩记录”等非世界知识小节，本次必须用 edit/patch 将其移出世界模型语义，改为只保留由这些事件揭示出的世界规则、制度、地点、势力、资源、术语或公开常识。",
                    "世界模型只承载原世界观设计阶段中属于世界知识和设定的部分：目标世界观、背景故事、能力设计、道具资源、势力制度、地图地点、常识规则与原书功能映射；角色功能位、故事类型和剧情结构不写入世界模型。",
                    "scope 中给出的默认二级标题只是组织参考，不是填空任务；如果某些栏目当前卷暂无稳定信息，可以留空、暂不出现或不修改，禁止为了填满标题而添加低价值信息。",
                    "只在确有稳定设定类别时使用必要三级标题；不要为了让每个标题显得完整而铺陈当前卷材料，不要把当前卷原文逐项抽取成设定库。",
                    "默认栏目无法容纳本书特有世界知识时，可以按实际需要新增长期可复用专题；不要新增只服务当前卷剧情或填补版面的专题。",
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
                    "卷纲只写支撑后续章节仿写的本卷结构：本卷定位、主要冲突、角色推进、高潮设计、结尾钩子和必要的参考源卷级功能映射关系；没有设计价值的普通事件不写。",
                    "这是卷级注入文档，不要改写成全书文档。",
                    "卷纲要服务后续章节仿写，不要把 50 章逐章展开成超长流水账；优先写可执行的卷级结构、阶段推进和关键映射。",
                    "卷纲应保持卷级规划粒度，按阶段写推进和转折，不登记章节素材，不为了覆盖全部原文而补齐所有事件。",
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
        "volume_outline": "卷级大纲",
    }
    return labels.get(doc_key, doc_key)

def adaptation_doc_scope(doc_key: str) -> str:
    return "volume" if doc_key == "volume_outline" else "global"

__all__ = [
    'world_model_scope_text',
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
