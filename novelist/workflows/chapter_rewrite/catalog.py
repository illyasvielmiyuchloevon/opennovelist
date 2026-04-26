from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def rewrite_paths(project_root: Path, volume_number: str, chapter_number: str | None = None) -> dict[str, Path]:
    global_dir = project_root / GLOBAL_DIRNAME
    volume_root_dir = project_root / VOLUME_ROOT_DIRNAME
    volume_dir = volume_root_dir / f"{volume_number}{VOLUME_DIR_SUFFIX}"
    rewritten_root = project_root / REWRITTEN_ROOT_DIRNAME
    rewritten_volume_dir = rewritten_root / volume_number
    paths: dict[str, Path] = {
        "global_dir": global_dir,
        "volume_root_dir": volume_root_dir,
        "volume_dir": volume_dir,
        "rewritten_root": rewritten_root,
        "rewritten_volume_dir": rewritten_volume_dir,
        "book_outline": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["book_outline"],
        "world_design": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["world_design"],
        "style_guide": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["style_guide"],
        "storyline_blueprint": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["storyline_blueprint"],
        "world_model": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["world_model"],
        "foreshadowing": global_dir / ADAPTATION_GLOBAL_FILE_NAMES["foreshadowing"],
        "character_status_cards": global_dir / REWRITE_GLOBAL_FILE_NAMES["character_status_cards"],
        "character_relationship_graph": global_dir / REWRITE_GLOBAL_FILE_NAMES["character_relationship_graph"],
        "world_state": global_dir / REWRITE_GLOBAL_FILE_NAMES["world_state"],
        "volume_outline": volume_dir / f"{volume_number}_volume_outline.md",
        "volume_plot_progress": volume_dir / f"{volume_number}_volume_plot_progress.md",
        "volume_review": volume_dir / f"{volume_number}_volume_review.md",
    }
    if chapter_number is not None:
        chapter_dir = volume_dir / f"{chapter_number}{CHAPTER_DIR_SUFFIX}"
        paths.update(
            {
                "chapter_dir": chapter_dir,
                "chapter_outline": chapter_dir / f"{chapter_number}_chapter_outline.md",
                "chapter_review": chapter_dir / f"{chapter_number}_chapter_review.md",
                "chapter_stage_manifest": chapter_dir / "00_stage_manifest.md",
                "chapter_response_debug": chapter_dir / "00_last_response_debug.md",
                "rewritten_chapter": rewritten_volume_dir / f"{chapter_number}.txt",
            }
        )
    else:
        paths.update(
            {
                "volume_stage_manifest": volume_dir / "00_volume_rewrite_manifest.md",
                "volume_response_debug": volume_dir / "00_volume_review_debug.md",
            }
        )
    return paths

def build_five_chapter_groups(volume_material: dict[str, Any]) -> list[list[str]]:
    chapter_numbers = [chapter["chapter_number"] for chapter in volume_material["chapters"]]
    return [
        chapter_numbers[index : index + FIVE_CHAPTER_REVIEW_SIZE]
        for index in range(0, len(chapter_numbers), FIVE_CHAPTER_REVIEW_SIZE)
    ]

def five_chapter_batch_id(chapter_numbers: list[str]) -> str:
    if not chapter_numbers:
        fail("组审查区间不能为空。")
    return f"{chapter_numbers[0]}_{chapter_numbers[-1]}"

def group_injection_root(project_root: Path, volume_number: str) -> Path:
    return project_root / GROUP_ROOT_DIRNAME / f"{volume_number}{GROUP_DIR_SUFFIX}"

def group_injection_dir(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    batch_id = five_chapter_batch_id(chapter_numbers)
    return group_injection_root(project_root, volume_number) / f"{batch_id}_group_injection"

def five_chapter_review_path(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> Path:
    group_dir = group_injection_dir(project_root, volume_number, chapter_numbers)
    return group_dir / f"{five_chapter_batch_id(chapter_numbers)}_group_review.md"

def find_group_for_chapter(volume_material: dict[str, Any], chapter_number: str) -> list[str]:
    normalized = chapter_number.zfill(4)
    for group in build_five_chapter_groups(volume_material):
        if normalized in group:
            return group
    fail(f"未找到章节 {normalized} 对应的五章区间。")

def build_chapter_session_key(manifest: dict[str, Any], volume_number: str, chapter_number: str) -> str:
    seed = f"{manifest['project_root']}|{manifest['source_root']}|{volume_number}|{chapter_number}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"chapter-rewrite-{digest}"

def build_volume_review_session_key(manifest: dict[str, Any], volume_number: str) -> str:
    seed = f"{manifest['project_root']}|{manifest['source_root']}|{volume_number}|volume-review"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"volume-review-{digest}"

def read_doc_catalog(project_root: Path, volume_number: str, chapter_number: str) -> dict[str, dict[str, Any]]:
    paths = rewrite_paths(project_root, volume_number, chapter_number)
    catalog: dict[str, dict[str, Any]] = {}

    for key, label in GLOBAL_DOC_LABELS.items():
        catalog[key] = {
            "key": key,
            "category": "global",
            "label": label,
            "path": paths[key],
            "content": read_text_if_exists(paths[key]).strip(),
        }

    for key, label in VOLUME_DOC_LABELS.items():
        catalog[key] = {
            "key": key,
            "category": "volume",
            "label": label,
            "path": paths[key],
            "content": read_text_if_exists(paths[key]).strip(),
        }

    for key, label in CHAPTER_DOC_LABELS.items():
        catalog[key] = {
            "key": key,
            "category": "chapter",
            "label": label,
            "path": paths[key],
            "content": read_text_if_exists(paths[key]).strip(),
        }

    return catalog

def serialize_doc_for_prompt(entry: dict[str, Any]) -> dict[str, Any]:
    content = str(entry["content"]).strip()
    return {
        "label": entry["label"],
        "file_name": Path(entry["path"]).name,
        "file_path": str(entry["path"]),
        "char_count": len(content),
        "content": content,
    }

def prepare_injected_docs(
    catalog: dict[str, dict[str, Any]],
    include_keys: list[str],
    *,
    category: str,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    payload_docs: dict[str, dict[str, Any]] = {}
    included: list[str] = []
    omitted: list[str] = []

    for key, entry in catalog.items():
        if entry["category"] != category:
            continue
        label = f"[{entry['category']}] {entry['label']}"
        if key not in include_keys:
            omitted.append(f"{label}：本阶段不注入。")
            continue
        if not entry["content"]:
            omitted.append(f"{label}：当前文件不存在或内容为空。")
            continue
        payload_docs[key] = serialize_doc_for_prompt(entry)
        included.append(f"{label} -> {entry['path']}（字符数约 {len(entry['content'])}）")

    return payload_docs, included, omitted

def prepare_cache_ordered_injected_docs(
    catalog: dict[str, dict[str, Any]],
    include_keys: list[str],
    *,
    category: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str], list[str]]:
    stable_docs: dict[str, dict[str, Any]] = {}
    rolling_docs: dict[str, dict[str, Any]] = {}
    included: list[str] = []
    omitted: list[str] = []
    stable_keys = set(STABLE_INJECTION_KEYS.get(category, []))

    for key, entry in catalog.items():
        if entry["category"] != category:
            continue
        label = f"[{entry['category']}] {entry['label']}"
        if key not in include_keys:
            omitted.append(f"{label}：本阶段不注入。")
            continue
        if not entry["content"]:
            omitted.append(f"{label}：当前文件不存在或内容为空。")
            continue
        serialized = serialize_doc_for_prompt(entry)
        if key in stable_keys:
            stable_docs[key] = serialized
        else:
            rolling_docs[key] = serialized
        included.append(f"{label} -> {entry['path']}（字符数约 {len(entry['content'])}）")

    return stable_docs, rolling_docs, included, omitted

def build_payload_with_trailing_docs(
    *,
    stable_fields: dict[str, Any],
    trailing_doc_fields: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    payload.update(stable_fields)
    payload.update(trailing_doc_fields)
    return payload

def build_payload_with_cache_layers(
    *,
    shared_prefix_fields: dict[str, Any],
    request_fields: dict[str, Any],
    trailing_doc_fields: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    payload.update(shared_prefix_fields)
    payload.update(request_fields)
    payload.update(trailing_doc_fields)
    return payload

def source_context_inventory(
    volume_material: dict[str, Any],
    chapter_number: str,
) -> list[dict[str, Any]]:
    chapter = get_chapter_material(volume_material, chapter_number)
    inventory: list[dict[str, Any]] = []
    for extra in volume_material["extras"]:
        inventory.append(
            {
                "type": "extra",
                "file_name": extra["file_name"],
                "file_path": extra["file_path"],
                "char_count": len(extra["text"]),
            }
        )
    inventory.append(
        {
            "type": "chapter",
            "file_name": chapter["file_name"],
            "file_path": chapter["file_path"],
            "chapter_number": chapter["chapter_number"],
            "source_title": chapter["source_title"],
            "char_count": len(chapter["text"]),
        }
    )
    return inventory

def build_chapter_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_number: str,
    source_bundle: str,
    source_char_count: int,
) -> str:
    chapter = get_chapter_material(volume_material, chapter_number)
    payload = {
        "project": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest.get("target_worldview", ""),
            "current_volume": volume_material["volume_number"],
            "current_chapter": chapter_number,
            "source_title": chapter["source_title"],
            "rewrite_output_root": manifest["rewrite_output_root"],
        },
        "workflow_rules": [
            "当前章节的章纲生成、正文生成、配套文档更新、审核与返工属于同一个章节会话，请沿用同一会话上下文。",
            "每一次请求都会重新附带当前章节参考源与本阶段要求注入的全局/卷级/章级文档。",
            "全局注入是每卷每章都要看的资料；卷级注入只限当前卷；章级注入只限当前章。",
            "严禁把参考源的人名、地名、宗门名、术语名、招式名原样照搬到仿写结果里。",
            "参考源当前章不仅提供情节功能映射，也提供篇幅、叙事节奏、情节结构、对话密度、句长、段落分割与收尾方式的直接参照；除非审核意见明确要求，不得明显扩写。",
            "遇到旧审核意见时要显式吸收并修正，不要重复犯同样的问题。",
        ],
        "source_files": source_context_inventory(volume_material, chapter_number),
        "source_char_count": source_char_count,
        "current_chapter_source_bundle": source_bundle,
    }
    return (
        "## Chapter Shared Context\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )

def build_volume_review_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    rewritten_chapters: dict[str, dict[str, Any]],
) -> str:
    payload = {
        "project": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest.get("target_worldview", ""),
            "current_volume": volume_material["volume_number"],
            "rewrite_output_root": manifest["rewrite_output_root"],
        },
        "workflow_rules": [
            "当前任务是卷级审核，只审核当前卷。",
            "需要检查卷内章节彼此之间的逻辑连续性、角色状态一致性、设定一致性和风格一致性。",
            "如果审核不通过，必须给出需要返工的章节编号。",
        ],
        "rewritten_chapter_inventory": [
            {
                "chapter_number": chapter_number,
                "file_name": data["file_name"],
                "file_path": data["file_path"],
                "char_count": len(data["text"]),
            }
            for chapter_number, data in rewritten_chapters.items()
        ],
    }
    return (
        "## Volume Review Shared Context\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )

def build_five_chapter_source_bundle(
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
) -> tuple[str, int]:
    selected = {item.zfill(4) for item in chapter_numbers}
    blocks: list[str] = []

    for extra in volume_material["extras"]:
        blocks.append(
            "\n".join(
                [
                    f"[补充文件 {extra['file_name']}]",
                    f"文件路径：{extra['file_path']}",
                    extra["text"],
                ]
            )
        )

    for chapter in volume_material["chapters"]:
        if chapter["chapter_number"] not in selected:
            continue
        blocks.append(
            "\n".join(
                [
                    f"[章节文件 {chapter['file_name']}]",
                    f"章节编号：{chapter['chapter_number']}",
                    f"文件路径：{chapter['file_path']}",
                    chapter["text"],
                ]
            )
        )

    source_bundle = "\n\n".join(blocks)
    return source_bundle, len(source_bundle)

def build_five_chapter_review_shared_prompt(
    *,
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    chapter_numbers: list[str],
    source_bundle: str,
    rewritten_chapters: dict[str, dict[str, Any]],
) -> str:
    payload = {
        "project": {
            "new_book_title": manifest["new_book_title"],
            "target_worldview": manifest.get("target_worldview", ""),
            "current_volume": volume_material["volume_number"],
            "review_range": chapter_numbers,
            "rewrite_output_root": manifest["rewrite_output_root"],
        },
        "workflow_rules": [
            f"当前任务是{FIVE_CHAPTER_REVIEW_NAME}，只审查当前这一个五章区间。",
            "需要检查最近这组章节之间是否前后矛盾、逻辑是否通畅、剧情是否偏离参考源、卷纲与全书大纲。",
            "如果审核不通过，必须明确指出需要返工的章节编号。",
        ],
        "current_range_source_bundle": source_bundle,
        "rewritten_chapters": rewritten_chapters,
    }
    return (
        "## Five Chapter Alignment Review Context\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "## Dynamic Request\n"
    )

def load_relevant_five_chapter_review_docs(
    project_root: Path,
    volume_material: dict[str, Any],
    chapter_number: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    group = find_group_for_chapter(volume_material, chapter_number)
    path = five_chapter_review_path(project_root, volume_material["volume_number"], group)
    content = read_text_if_exists(path).strip()
    label = f"[group] {FIVE_CHAPTER_REVIEW_NAME}（{group[0]}-{group[-1]}）"
    if content:
        return (
            [
                {
                    "label": f"{FIVE_CHAPTER_REVIEW_NAME}（{group[0]}-{group[-1]}）",
                    "file_name": path.name,
                    "file_path": str(path),
                    "content": content,
                }
            ],
            [f"{label} -> {path}（字符数约 {len(content)}）"],
            [],
        )
    return [], [], [f"{label}：当前无相关审查文档。"]

def build_rewritten_chapters_payload(project_root: Path, volume_number: str, chapter_numbers: list[str]) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for chapter_number in chapter_numbers:
        chapter_path = rewrite_paths(project_root, volume_number, chapter_number)["rewritten_chapter"]
        chapter_text = read_text_if_exists(chapter_path).strip()
        if not chapter_text:
            fail(f"卷级审核时缺少章节正文：{chapter_path}")
        payload[chapter_number] = {
            "file_name": chapter_path.name,
            "file_path": str(chapter_path),
            "content": chapter_text,
            "text": chapter_text,
        }
    return payload

def support_update_target_paths(paths: dict[str, Path]) -> dict[str, Path]:
    return {
        "character_status_cards": paths["character_status_cards"],
        "character_relationship_graph": paths["character_relationship_graph"],
        "volume_plot_progress": paths["volume_plot_progress"],
        "foreshadowing": paths["foreshadowing"],
        "world_state": paths["world_state"],
    }

__all__ = [
    'rewrite_paths',
    'build_five_chapter_groups',
    'five_chapter_batch_id',
    'group_injection_root',
    'group_injection_dir',
    'five_chapter_review_path',
    'find_group_for_chapter',
    'build_chapter_session_key',
    'build_volume_review_session_key',
    'read_doc_catalog',
    'serialize_doc_for_prompt',
    'prepare_injected_docs',
    'prepare_cache_ordered_injected_docs',
    'build_payload_with_trailing_docs',
    'build_payload_with_cache_layers',
    'source_context_inventory',
    'build_chapter_shared_prompt',
    'build_volume_review_shared_prompt',
    'build_five_chapter_source_bundle',
    'build_five_chapter_review_shared_prompt',
    'load_relevant_five_chapter_review_docs',
    'build_rewritten_chapters_payload',
    'support_update_target_paths',
]
