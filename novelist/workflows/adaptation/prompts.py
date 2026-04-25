from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def world_model_scope_text() -> str:
    section_text = "、".join(WORLD_MODEL_DEFAULT_SECTIONS)
    return (
        "文档要沉淀到当前卷为止已知的世界知识，默认按 16 个二级标题组织："
        f"{section_text}。每个二级标题下可以根据实际需要继续展开多个三级标题，用于管理该栏目的不同知识子类。"
        "并写出与原书的功能映射。"
    )

def storyline_blueprint_scope_text() -> str:
    section_text = "、".join(STORYLINE_BLUEPRINT_DEFAULT_SECTIONS)
    return (
        "文档是全书故事线蓝图，用于把参考源的主线、关键支线、反派线、终局线、跨卷线与新增故事线转化为新书可长期复用的蓝图。"
        "文档采用“故事线为 owner”的结构：每条故事线使用独立二级标题维护，不按卷汇总成实时进展。"
        f"每条故事线下默认使用三级标题：{section_text}。"
        "分卷蓝图只记录卷级故事线设计：该卷在全书故事线中的作用、主要转折方向、与后续卷的衔接约束。"
        "禁止记录章内事件、场景清单、战斗过程或逐章剧情。"
        "不要写成运行时状态表、最新推进清单或单卷总结。"
    )

def global_document_compaction_policy() -> list[str]:
    return [
        "全局资料是长期复用的索引、规则、蓝图和映射，不是逐章流水账。",
        "全书大纲只保留卷级主线、核心转折和后续约束；章节级细节放在卷纲、章纲或卷级剧情进程。",
        "全书故事线蓝图只保留故事线 owner、功能映射、全书走向、卷级作用、跨卷锚点和待后续补全；禁止章级情节、场景或事件清单。",
        "伏笔文档只保留会跨章、跨卷影响后续仿写的高价值伏笔；一次性细节、已完全回收且不再影响后文的内容应压缩为一行归档或移出全局层。",
        "同一信息只放在最合适的一份文档中：世界规则放世界模型，剧情推进放卷级剧情进程，章节细节放章纲/正文审核，伏笔只记录未来需要记住的钩子。",
        "如果现有全局文档已经过细，本次更新应顺手压缩重复和低价值明细，而不是继续追加同等粒度内容。",
    ]

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
        ("world_design", "世界观设计"),
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
        "world_design": {
            "role": "资深网络小说世界观设定编辑",
            "task": "当前任务只产出 1 份世界观设计文档正文。",
            "scope": (
                "文档需覆盖世界观设定、背景故事、能力设计、道具设计、势力设计、角色功能位、故事类型与原书映射关系。"
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
                "文档只管理后续仿写必须长期记住的高价值伏笔索引，区分待埋设、已埋设、待回收、已回收。"
                "当前卷细节、普通剧情提示和已闭合小事件不要写进全局伏笔文档；只保留功能映射、回收约束和后续影响。"
            ),
        },
        "world_model": {
            "role": "资深网络小说世界知识建模编辑",
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
    return {"doc_key": doc_key, **request_specs[doc_key]}

def build_document_plan(volume_number: str) -> list[dict[str, Any]]:
    if should_generate_style_guide(volume_number):
        return [
            {"key": "world_design", "label": "世界观设计文档", "scope": "global"},
            {"key": "world_model", "label": "世界模型文档", "scope": "global"},
            {"key": "style_guide", "label": "文笔写作风格文档", "scope": "global"},
            {"key": "book_outline", "label": "全书大纲文档", "scope": "global"},
            {"key": "foreshadowing", "label": "伏笔文档", "scope": "global"},
            {"key": "storyline_blueprint", "label": "全书故事线蓝图文档", "scope": "global"},
            {"key": "volume_outline", "label": "卷级大纲文档", "scope": "volume"},
        ]
    return [
        {"key": "world_design", "label": "世界观设计文档", "scope": "global"},
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
        injected_docs[doc_key] = clip_for_context(current_docs.get(doc_key, ""), limit=adaptation_doc_context_limit(doc_key))
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
            "全书大纲、世界观文档、全书故事线蓝图文档、伏笔文档、世界模型文档是每阶段都要注入的全局资料。",
            "卷级大纲不作为全局注入资料，不要把卷级大纲当成下一份文档的依赖前提。",
            "所有映射关系都写成功能映射，不要照抄参考源原文句子。",
            "本阶段的每一次请求都会重新附带当前卷全部文件原文与文件清单。",
        ],
        "global_document_compaction_policy": global_document_compaction_policy(),
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
        "current_content": clip_for_context(current_content, limit=adaptation_doc_context_limit(doc_key)),
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
                "required_file": GLOBAL_FILE_NAMES["style_guide"],
                "requirements": [
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

    if doc_key == "world_design":
        payload = build_payload_with_trailing_docs(
            stable_fields={
                "document_request": document_request,
                "required_file": GLOBAL_FILE_NAMES["world_design"],
                "requirements": [
                    "保留历史世界观设计的连续性，并把当前卷新增内容补充进去。",
                    "按修改意图使用 edit 或 patch 对已有条目、段落或小节做增量更新，不要整篇重写世界观文档。",
                    "未变化的世界知识、术语、层级结构、历史背景必须保留。",
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
                "required_file": GLOBAL_FILE_NAMES["book_outline"],
                "requirements": [
                    "这是整本书的大纲文档，不是单卷总结。",
                    "当前阶段只允许新增或改写当前卷对应的全书大纲段落，以及与已处理卷直接相关的衔接说明。",
                    "只展开 processed_volumes_including_current 中列出的卷；未读取参考源的后续卷必须二选一：要么不写，要么仅保留“第X卷：待后续阶段补全”这类占位说明。",
                    "未读取卷不得出现剧情梗概、角色推进、冲突设计、伏笔安排、高潮设计或结局走向。",
                    "如果旧版全书大纲里已经提前写了未读取卷的详细内容，本次要把那些未读取卷删掉，或回收为占位状态，不能继续保留伪细纲。",
                    "第一卷阶段尤其不能提前写第二卷及之后的详细大纲。",
                    "全书大纲是卷级方向文档，不要把每章剧情、每场战斗、每个小事件都写进去；当前卷通常保留 6-10 个关键推进点即可。",
                    "如果全书大纲已经和故事线蓝图、伏笔文档或卷级大纲重复，本次要压缩重复内容，只保留大纲层真正需要的方向和约束。",
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
                "required_file": GLOBAL_FILE_NAMES["storyline_blueprint"],
                "requirements": [
                    "这是全书故事线蓝图文档，用于沉淀参考源故事线的功能映射、新书全书走向与跨卷设计，不是实时进度账本、卷级剧情进程或章节细纲。",
                    "文档只覆盖到当前卷为止仍然有效的重要故事线，包括主线、关键支线、反派线、终局线、跨卷线，以及当前卷新出现且会长期影响后续仿写的故事线。",
                    "如果当前文件已存在，必须按修改意图使用 edit 或 patch 工具做增量更新，不得整篇覆盖式重写全书故事线蓝图。",
                    "每条故事线必须使用独立二级标题管理，标题建议写成“## 故事线：<名称>”；不要把多条故事线混写在同一个总段落里。",
                    "每条故事线下默认使用三级标题：蓝图定位、参考源功能映射、分卷蓝图、跨卷递进、待后续补全；不要额外增加会膨胀成流水账的三级标题。",
                    "分卷蓝图下按“#### 第001卷”“#### 第002卷”等区块维护；每个卷区块只写该卷在全书故事线中的功能、主要转折方向、跨卷衔接和后续约束。",
                    "已处理卷区块应保持卷级设计信息稳定；后续卷不得把旧卷改写成章级细节，也不得把已有卷级设计压缩到信息缺失。",
                    "严禁在全书故事线蓝图中记录章内事件、场景清单、战斗过程、逐章剧情或普通过场。",
                    "如果当前卷引入了新的故事线，只新增长期有效的故事线；一次性事件、单章冲突或普通支线不进入全书故事线蓝图。",
                    "未读取参考源的后续卷只允许写入“待后续补全”占位，不得提前展开细纲。",
                    "如果已有内容和全书大纲、伏笔文档或卷级剧情进程重复，本次要压缩重复，只保留故事线层的蓝图信息。",
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
                "required_file": GLOBAL_FILE_NAMES["foreshadowing"],
                "requirements": [
                    "优先保持伏笔清单的可追踪性、后续工作流可读性和体量可控。",
                    "请基于全书大纲、世界观文档和当前卷原文上下文补充更新。",
                    "只记录会影响后续章节、后续卷、角色关系、世界真相或核心爽点回收的高价值伏笔。",
                    "单章内完成的小提示、普通物品、一次性情绪铺垫、已经完全回收且后续不再影响剧情的内容，不要进入全局伏笔文档。",
                    "已回收伏笔只保留一行归档摘要和影响结果，不要保留完整铺垫过程。",
                    "当前卷的细节推进应留给卷级剧情进程或章级文档；伏笔文档只保留未来还必须记住的钩子。",
                    "如果已有伏笔文档过细，本次更新要压缩重复、归并同类项、删除低价值明细，而不是继续追加同等粒度内容。",
                    "建议整份文档控制在 5000-10000 字符；除非确有必要，不要超过 12000 字符。",
                    "按修改意图使用 edit 或 patch 做增量补充、状态推进或局部修订。",
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
                "required_file": GLOBAL_FILE_NAMES["world_model"],
                "requirements": [
                    "这是全书级世界模型文档，但采用按卷增量维护方式：每卷只补充、修正到当前卷为止新增的世界知识。",
                    "如果当前文件已存在，必须按修改意图使用 edit 或 patch 做增量更新，不得整篇覆盖式重写世界模型。",
                    "未变化的世界知识、术语、势力、地点、历史背景与规则结构必须保留。",
                    "本次只允许补充、修正与当前卷直接相关的世界知识，不要把文档改写成只剩最近一卷。",
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
                "required_file": f"{volume_material['volume_number']}_volume_outline.md",
                "requirements": [
                    "卷纲要包含本卷定位、主要冲突、角色推进、高潮设计、结尾钩子、与原卷映射关系。",
                    "这是卷级注入文档，不要改写成全书文档。",
                    "卷纲要服务后续章节仿写，不要把 50 章逐章展开成超长流水账；优先写可执行的卷级结构、阶段推进和关键映射。",
                    "建议控制在 6000-10000 字符左右；除非确有必要，不要输出超过 12000 字符的卷纲。",
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
        "foreshadowing": "伏笔管理",
        "storyline_blueprint": "全书故事线蓝图",
        "volume_outline": "卷级大纲",
    }
    return labels.get(doc_key, doc_key)

def adaptation_doc_scope(doc_key: str) -> str:
    return "volume" if doc_key == "volume_outline" else "global"

__all__ = [
    'world_model_scope_text',
    'storyline_blueprint_scope_text',
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
