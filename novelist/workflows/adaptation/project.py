from __future__ import annotations

import shutil

from ._shared import *  # noqa: F401,F403


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "基于 split_novel 拆分后的书名文件夹或已有工程目录，逐卷生成改编规划文档，"
            "使用 OpenAI Responses API。"
        )
    )
    parser.add_argument(
        "source_root",
        nargs="?",
        help=(
            "split_novel 拆分完成后的书名文件夹路径，或已有工程目录路径，"
            "支持任意位置的绝对路径或相对路径，例如 D:\\books\\某本书；不传时启动后提示输入。"
        ),
    )
    parser.add_argument("--new-title", help="新书名。")
    parser.add_argument("--target-worldview", help="目标世界观。")
    parser.add_argument("--base-url", help="OpenAI Responses API 的 base_url。")
    parser.add_argument("--api-key", help="OpenAI API Key。")
    parser.add_argument("--model", help="调用的模型名称。")
    parser.add_argument(
        "--style-mode",
        choices=(STYLE_MODE_CUSTOM, STYLE_MODE_SOURCE),
        help="写作风格来源模式。",
    )
    parser.add_argument("--style-file", help="自定义写作风格文件路径。")
    parser.add_argument(
        "--protagonist-mode",
        choices=(PROTAGONIST_MODE_CUSTOM, PROTAGONIST_MODE_ADAPTIVE),
        help="主角设定来源模式。",
    )
    parser.add_argument("--protagonist-text", help="自定义主角设定和性格描述。")
    parser.add_argument("--volume", help="指定处理某一卷，例如 001。默认自动处理下一卷。")
    parser.add_argument(
        "--run-mode",
        choices=(RUN_MODE_STAGE, RUN_MODE_BOOK),
        help="运行方式：按阶段运行（每卷结束后确认）或按全书运行（自动连续处理后续卷）。",
    )
    parser.add_argument(
        "--project-root",
        help="输出工程目录；默认使用新书名自动创建。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只初始化工程和识别待处理卷，不调用 API。",
    )
    parser.add_argument(
        "--workflow-controlled",
        action="store_true",
        help="由统一工作流入口调度时启用：当前只处理本次目标卷，完成后直接返回，不在子流程 内继续下一卷。",
    )
    return parser.parse_args()

def validate_source_root(source_root: Path) -> None:
    if not source_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{source_root}")

    volume_dirs = discover_volume_dirs(source_root)
    if not volume_dirs:
        fail(
            "当前目录下未识别到编号卷目录，例如 001、002。"
            "请传入 split_novel 拆分完成后的书名文件夹，或传入已有工程目录。"
        )

def manifest_matches_source_root(manifest: dict[str, Any], source_root: Path) -> bool:
    manifest_source = manifest.get("source_root")
    if not manifest_source:
        return False
    try:
        return normalize_path(str(manifest_source)) == source_root.resolve()
    except Exception:
        return False

def find_existing_project_for_source(source_root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []

    for child in source_root.parent.iterdir():
        if not child.is_dir() or child.resolve() == source_root.resolve():
            continue
        manifest = load_manifest(child)
        if manifest and manifest_matches_source_root(manifest, source_root):
            candidates.append((str(manifest.get("updated_at", "")), child, manifest))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, project_root, manifest = candidates[0]
    return project_root, manifest

def resolve_input_root(
    raw_path: str | None,
    global_config: dict[str, Any],
) -> tuple[Path, Path | None, dict[str, Any] | None]:
    default_path = (
        global_config.get("last_project_root")
        or global_config.get("last_source_root")
        or global_config.get("last_input_root")
    )
    if raw_path is None:
        raw_path = prompt_text(
            "请输入 split_novel 拆分完成后的书名文件夹路径或已有工程目录路径（可输入任意位置）",
            default=str(default_path) if default_path else None,
        )

    input_root = normalize_path(raw_path)
    if not input_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{input_root}")
    if not input_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{input_root}")

    manifest = load_manifest(input_root)
    if manifest is not None:
        source_root = normalize_path(str(manifest["source_root"]))
        validate_source_root(source_root)
        return source_root, input_root, manifest

    source_root = input_root
    validate_source_root(source_root)
    project_root, manifest = find_existing_project_for_source(source_root)
    return source_root, project_root, manifest

def discover_volume_dirs(source_root: Path) -> list[Path]:
    volume_dirs = [
        child
        for child in source_root.iterdir()
        if child.is_dir() and re.fullmatch(r"\d{3}", child.name)
    ]
    return sorted(volume_dirs, key=lambda item: int(item.name))

def discover_volume_files(volume_dir: Path) -> tuple[list[Path], list[Path]]:
    chapter_files: list[Path] = []
    extra_files: list[Path] = []

    for child in volume_dir.iterdir():
        if not child.is_file():
            continue
        if re.fullmatch(r"\d{4}", child.stem):
            chapter_files.append(child)
        else:
            extra_files.append(child)

    chapter_files.sort(key=lambda item: int(item.stem))
    extra_files.sort(key=lambda item: item.name)
    return chapter_files, extra_files

def choose_project_root(
    source_root: Path,
    new_title: str,
    requested_root: str | None,
) -> Path:
    if requested_root:
        return normalize_path(requested_root)

    base_name = sanitize_file_name(new_title)
    candidate = source_root.parent / base_name
    try:
        if candidate.resolve() == source_root.resolve():
            candidate = source_root.parent / f"{base_name}_project"
    except FileNotFoundError:
        pass

    manifest_paths = [
        candidate / PROJECT_MANIFEST_NAME,
        candidate / LEGACY_PROJECT_MANIFEST_NAME,
    ]
    if any(path.exists() for path in manifest_paths):
        return candidate
    if not candidate.exists() or not any(candidate.iterdir()):
        return candidate

    suffix = 1
    while True:
        alt = source_root.parent / f"{base_name}_{suffix}"
        manifest_paths = [
            alt / PROJECT_MANIFEST_NAME,
            alt / LEGACY_PROJECT_MANIFEST_NAME,
        ]
        if any(path.exists() for path in manifest_paths):
            return alt
        if not alt.exists():
            return alt
        suffix += 1

def load_manifest(project_root: Path) -> dict[str, Any] | None:
    manifest_path = project_root / PROJECT_MANIFEST_NAME
    if manifest_path.exists():
        return extract_json_payload(manifest_path.read_text(encoding="utf-8"))

    legacy_manifest_path = project_root / LEGACY_PROJECT_MANIFEST_NAME
    if legacy_manifest_path.exists():
        return json.loads(legacy_manifest_path.read_text(encoding="utf-8"))

    return None

def save_manifest(manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = now_iso()
    payload = dict(manifest)
    payload.pop("openai", None)
    write_markdown_data(
        Path(manifest["project_root"]) / PROJECT_MANIFEST_NAME,
        title="Project Manifest",
        payload=payload,
        summary_lines=[
            f"new_book_title: {manifest['new_book_title']}",
            f"source_root: {manifest['source_root']}",
            f"total_volumes: {manifest['total_volumes']}",
            f"processed_volumes: {', '.join(manifest.get('processed_volumes', [])) or 'none'}",
            f"last_processed_volume: {manifest.get('last_processed_volume') or 'none'}",
        ],
    )

def ensure_project_dirs(project_root: Path) -> list[str]:
    global_dir = project_root / GLOBAL_DIRNAME
    global_dir.mkdir(parents=True, exist_ok=True)
    warnings = migrate_renamed_files(global_dir, LEGACY_GLOBAL_FILE_RENAMES)
    migrate_numbered_injection_dirs(
        project_root,
        container_dirname=VOLUME_ROOT_DIRNAME,
        suffix=VOLUME_DIR_SUFFIX,
    )
    migrate_numbered_injection_dirs(
        project_root,
        container_dirname=GROUP_ROOT_DIRNAME,
        suffix=GROUP_DIR_SUFFIX,
    )
    return warnings

def resolve_style_mode(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.style_mode:
        style_mode = args.style_mode
    else:
        style_mode = prompt_choice(
            "输入写作风格",
            [
                (STYLE_MODE_CUSTOM, "自定义导入写作风格文件"),
                (STYLE_MODE_SOURCE, "参考书源写作风格"),
            ],
        )

    style_file: str | None = None
    if style_mode == STYLE_MODE_CUSTOM:
        raw_path = args.style_file or prompt_text("请输入写作风格文件路径")
        style_path = normalize_path(raw_path)
        if not style_path.exists():
            raise FileNotFoundError(f"写作风格文件不存在：{style_path}")
        if not style_path.is_file():
            raise IsADirectoryError(f"写作风格路径不是文件：{style_path}")
        style_file = str(style_path)

    return style_mode, style_file

def resolve_protagonist_mode(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.protagonist_mode:
        protagonist_mode = args.protagonist_mode
    else:
        protagonist_mode = prompt_choice(
            "输入主角设定和性格",
            [
                (PROTAGONIST_MODE_CUSTOM, "自定义设计"),
                (PROTAGONIST_MODE_ADAPTIVE, "根据世界观不同和参考书源柔和设定"),
            ],
        )

    protagonist_text: str | None = None
    if protagonist_mode == PROTAGONIST_MODE_CUSTOM:
        protagonist_text = args.protagonist_text or prompt_text(
            "请输入主角设定和性格描述"
        )

    return protagonist_mode, protagonist_text

def init_or_load_project(
    args: argparse.Namespace,
    source_root: Path,
    volume_dirs: list[Path],
    global_config: dict[str, Any],
    existing_project_root: Path | None = None,
    existing_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if existing_manifest is not None and existing_project_root is not None:
        manifest = dict(existing_manifest)
        project_root = existing_project_root
    else:
        manifest = None
        project_root = None

    if manifest is None and args.project_root:
        requested_project_root = normalize_path(args.project_root)
        requested_manifest = load_manifest(requested_project_root)
        if requested_manifest is not None:
            manifest = dict(requested_manifest)
            project_root = requested_project_root

    if manifest is None:
        new_title_default = global_config.get("last_new_book_title")
        new_title = args.new_title or prompt_text("输入新书名", default=new_title_default)
        project_root = choose_project_root(source_root, new_title, args.project_root)
        manifest = load_manifest(project_root)

    if manifest is not None:
        if Path(manifest["source_root"]).resolve() != source_root.resolve():
            fail(
                f"工程目录已存在，但来源目录不同：{project_root}\n"
                f"当前来源：{source_root}\n"
                f"工程记录来源：{manifest['source_root']}"
            )
        manifest["total_volumes"] = len(volume_dirs)
        save_manifest(manifest)
        return manifest

    assert project_root is not None
    target_worldview = args.target_worldview or prompt_text("输入仿写成什么世界观")
    style_mode, style_file = resolve_style_mode(args)
    protagonist_mode, protagonist_text = resolve_protagonist_mode(args)

    project_root.mkdir(parents=True, exist_ok=True)
    ensure_project_dirs(project_root)

    manifest = {
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source_root": str(source_root),
        "project_root": str(project_root),
        "new_book_title": new_title,
        "target_worldview": target_worldview,
        "style": {
            "mode": style_mode,
            "style_file": style_file,
        },
        "protagonist": {
            "mode": protagonist_mode,
            "description": protagonist_text,
        },
        "total_volumes": len(volume_dirs),
        "processed_volumes": [],
        "last_processed_volume": None,
    }
    save_manifest(manifest)
    return manifest

def select_volume_to_process(
    volume_dirs: list[Path],
    manifest: dict[str, Any],
    requested_volume: str | None,
) -> Path | None:
    volume_map = {volume_dir.name: volume_dir for volume_dir in volume_dirs}

    if requested_volume:
        normalized = requested_volume.zfill(3)
        if normalized not in volume_map:
            fail(f"未找到指定卷：{normalized}")
        return volume_map[normalized]

    processed = set(manifest.get("processed_volumes", []))
    for volume_dir in volume_dirs:
        if volume_dir.name not in processed:
            return volume_dir
    return None

def select_source_rebalance_start_volume(
    volume_dirs: list[Path],
    manifest: dict[str, Any],
    requested_volume: str | None,
) -> Path | None:
    volume_map = {volume_dir.name: volume_dir for volume_dir in volume_dirs}
    processed = {str(item).zfill(3) for item in manifest.get("processed_volumes", [])}

    if requested_volume:
        normalized = requested_volume.zfill(3)
        if normalized not in volume_map:
            fail(f"未找到指定卷：{normalized}")
        return None if normalized in processed else volume_map[normalized]

    for volume_dir in volume_dirs:
        if volume_dir.name not in processed:
            return volume_dir
    return None

def find_next_pending_volume_after(
    volume_dirs: list[Path],
    manifest: dict[str, Any],
    current_volume_name: str,
) -> Path | None:
    processed = set(manifest.get("processed_volumes", []))
    found_current = False
    for volume_dir in volume_dirs:
        if not found_current:
            if volume_dir.name == current_volume_name:
                found_current = True
            continue
        if volume_dir.name in processed:
            continue
        return volume_dir
    return None


def _source_rebalance_volume_payload(volume: Any) -> dict[str, Any]:
    chapters = list(getattr(volume, "chapters", []) or [])
    return {
        "volume_number": getattr(volume, "volume_number", ""),
        "chapter_count": len(chapters),
        "chapter_range": (
            f"{chapters[0].chapter_number}-{chapters[-1].chapter_number}"
            if chapters
            else ""
        ),
        "source_char_count": getattr(volume, "source_char_count", 0),
        "over_budget": bool(getattr(volume, "over_budget", False)),
        "warning": getattr(volume, "warning", ""),
    }


def append_source_rebalance_history(
    manifest: dict[str, Any],
    report: RebalanceReport,
    *,
    project_backup_dir: Path | None = None,
) -> None:
    history = manifest.setdefault("source_rebalance_history", [])
    history.append(
        {
            "timestamp": now_iso(),
            "start_volume": report.start_volume,
            "target_chars": TARGET_VOLUME_SOURCE_CHARS,
            "affected_volumes": report.affected_volumes,
            "locked_volumes": report.locked_volumes,
            "source_backup_dir": str(report.backup_dir) if report.backup_dir is not None else "",
            "project_backup_dir": str(project_backup_dir) if project_backup_dir is not None else "",
            "old_volumes": [_source_rebalance_volume_payload(volume) for volume in report.old_volumes],
            "new_volumes": [_source_rebalance_volume_payload(volume) for volume in report.new_volumes],
            "warnings": report.warnings,
        }
    )


def backup_project_outputs_for_source_rebalance(
    manifest: dict[str, Any],
    report: RebalanceReport,
) -> Path | None:
    project_root = Path(manifest["project_root"])
    processed = {str(item).zfill(3) for item in manifest.get("processed_volumes", [])}
    affected = [volume for volume in report.affected_volumes if volume not in processed]
    if not affected:
        return None

    timestamp = now_iso().replace(":", "").replace("+", "_")
    backup_root = project_root / "source_rebalance_backups" / timestamp
    moved_any = False
    for volume_number in affected:
        candidates = [
            project_root / VOLUME_ROOT_DIRNAME / f"{volume_number}{VOLUME_DIR_SUFFIX}",
            project_root / GROUP_ROOT_DIRNAME / f"{volume_number}{GROUP_DIR_SUFFIX}",
            project_root / REWRITTEN_ROOT_DIRNAME / volume_number,
        ]
        for source in candidates:
            if not source.exists():
                continue
            target = backup_root / source.relative_to(project_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved_any = True

    return backup_root if moved_any else None


def clear_unprocessed_manifest_state_after_rebalance(
    manifest: dict[str, Any],
    report: RebalanceReport,
    *,
    volume_count: int,
) -> None:
    affected = set(report.affected_volumes)
    processed = [str(item).zfill(3) for item in manifest.get("processed_volumes", [])]
    manifest["processed_volumes"] = sorted(volume for volume in processed if volume not in affected)
    manifest["total_volumes"] = volume_count
    if str(manifest.get("last_processed_volume") or "").zfill(3) in affected:
        manifest["last_processed_volume"] = manifest["processed_volumes"][-1] if manifest["processed_volumes"] else None
    clear_rewrite_manifest_after_source_rebalance(manifest, affected_volumes=affected, volume_count=volume_count)


def clear_rewrite_manifest_after_source_rebalance(
    manifest: dict[str, Any],
    *,
    affected_volumes: set[str],
    volume_count: int,
) -> None:
    project_root = Path(manifest["project_root"])
    rewrite_manifest_path = project_root / "00_chapter_rewrite_manifest.md"
    if not rewrite_manifest_path.exists():
        return
    rewrite_manifest = extract_json_payload(rewrite_manifest_path.read_text(encoding="utf-8"))
    for key in (
        "chapter_states",
        "volume_review_states",
        "five_chapter_review_states",
    ):
        value = rewrite_manifest.get(key)
        if isinstance(value, dict):
            for volume_number in affected_volumes:
                value.pop(volume_number, None)
    processed = [str(item).zfill(3) for item in rewrite_manifest.get("processed_volumes", [])]
    rewrite_manifest["processed_volumes"] = sorted(volume for volume in processed if volume not in affected_volumes)
    rewrite_manifest["total_volumes"] = volume_count
    if str(rewrite_manifest.get("last_processed_volume") or "").zfill(3) in affected_volumes:
        rewrite_manifest["last_processed_volume"] = (
            rewrite_manifest["processed_volumes"][-1] if rewrite_manifest["processed_volumes"] else None
        )
    if str(rewrite_manifest.get("last_processed_chapter") or "").strip():
        last_volume = str(rewrite_manifest.get("last_processed_volume") or "").zfill(3)
        if last_volume in affected_volumes or not last_volume:
            rewrite_manifest["last_processed_chapter"] = None
    write_markdown_data(
        rewrite_manifest_path,
        title="Chapter Rewrite Manifest",
        payload=rewrite_manifest,
        summary_lines=[
            f"new_book_title: {rewrite_manifest.get('new_book_title', '')}",
            f"source_root: {rewrite_manifest.get('source_root', '')}",
            f"rewrite_output_root: {rewrite_manifest.get('rewrite_output_root', '')}",
            f"processed_volumes: {', '.join(rewrite_manifest.get('processed_volumes', [])) or 'none'}",
            f"last_processed_volume: {rewrite_manifest.get('last_processed_volume') or 'none'}",
            f"last_processed_chapter: {rewrite_manifest.get('last_processed_chapter') or 'none'}",
        ],
    )


def prepare_source_volumes_for_adaptation(
    *,
    source_root: Path,
    manifest: dict[str, Any],
    target_volume: Path,
    dry_run: bool,
) -> list[Path]:
    report = rebalance_source_volumes(
        source_root,
        start_volume=target_volume.name,
        locked_volumes=set(manifest.get("processed_volumes", [])),
        dry_run=dry_run,
    )
    if report.needed or report.warnings:
        for line in rebalance_summary_lines(report):
            print_progress(line, error=report.blocked)
    if report.blocked and not dry_run:
        fail("参考源自适应分卷被阻止，请按上方提示处理后再继续。")

    if dry_run:
        return discover_volume_dirs(source_root)

    project_backup_dir: Path | None = None
    if report.needed and report.changed:
        project_backup_dir = backup_project_outputs_for_source_rebalance(manifest, report)
        if project_backup_dir is not None:
            print_progress(f"已备份受影响的未完成产物目录：{project_backup_dir}")
        volume_dirs = discover_volume_dirs(source_root)
        clear_unprocessed_manifest_state_after_rebalance(
            manifest,
            report,
            volume_count=len(volume_dirs),
        )
        append_source_rebalance_history(manifest, report, project_backup_dir=project_backup_dir)
        save_manifest(manifest)
        return volume_dirs

    volume_dirs = discover_volume_dirs(source_root)
    manifest["total_volumes"] = len(volume_dirs)
    save_manifest(manifest)
    return volume_dirs

def resolve_run_mode(args: argparse.Namespace) -> str:
    if args.run_mode:
        return args.run_mode
    if not sys.stdin or not sys.stdin.isatty():
        return RUN_MODE_STAGE
    return prompt_choice(
        "请选择运行方式",
        [
            (RUN_MODE_STAGE, f"{RUN_MODE_LABELS[RUN_MODE_STAGE]}（每卷结束后确认下一卷）"),
            (RUN_MODE_BOOK, f"{RUN_MODE_LABELS[RUN_MODE_BOOK]}（自动连续处理后续卷）"),
        ],
    )

def stage_paths(project_root: Path, volume_number: str) -> dict[str, Path]:
    global_dir = project_root / GLOBAL_DIRNAME
    volume_root_dir = project_root / VOLUME_ROOT_DIRNAME
    volume_dir = volume_root_dir / f"{volume_number}{VOLUME_DIR_SUFFIX}"
    return {
        "global_dir": global_dir,
        "volume_root_dir": volume_root_dir,
        "volume_dir": volume_dir,
        "book_outline": global_dir / GLOBAL_FILE_NAMES["book_outline"],
        "world_design": global_dir / GLOBAL_FILE_NAMES["world_design"],
        "style_guide": global_dir / GLOBAL_FILE_NAMES["style_guide"],
        "foreshadowing": global_dir / GLOBAL_FILE_NAMES["foreshadowing"],
        "world_model": global_dir / GLOBAL_FILE_NAMES["world_model"],
        "volume_outline": volume_dir / f"{volume_number}_volume_outline.md",
        "adaptation_review": volume_dir / f"{volume_number}_adaptation_review.md",
        "source_digest": volume_dir / "00_source_digest.md",
        "stage_manifest": volume_dir / "00_stage_manifest.md",
        "response_debug": volume_dir / "00_last_response_debug.md",
    }

def write_source_inventory_snapshot(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    note: str,
    total_batches: int | None = None,
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    loaded_files = build_loaded_file_inventory(volume_material)

    payload = {
        "generated_at": now_iso(),
        "status": "loaded_source_files",
        "note": note,
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "chapter_count": len(volume_material["chapters"]),
        "extra_file_count": len(volume_material["extras"]),
        "total_batches": total_batches,
        "loaded_files": loaded_files,
    }
    write_markdown_data(
        paths["source_digest"],
        title=f"Source Inventory {volume_material['volume_number']}",
        payload=payload,
        summary_lines=[
            f"processed_volume: {volume_material['volume_number']}",
            f"chapter_count: {len(volume_material['chapters'])}",
            f"extra_file_count: {len(volume_material['extras'])}",
            f"total_batches: {total_batches if total_batches is not None else 'pending'}",
            f"note: {note}",
        ],
    )

def write_stage_outputs(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    generated_documents: list[dict[str, Any]],
    source_char_count: int,
    loaded_file_count: int,
) -> dict[str, Path]:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    write_markdown_data(
        paths["source_digest"],
        title=f"Source Inventory {volume_material['volume_number']}",
        payload={
            "generated_at": now_iso(),
            "status": "loaded_source_files",
            "processed_volume": volume_material["volume_number"],
            "source_volume_dir": volume_material["volume_dir"],
            "chapter_count": len(volume_material["chapters"]),
            "extra_file_count": len(volume_material["extras"]),
            "loaded_files": build_loaded_file_inventory(volume_material),
            "source_char_count": source_char_count,
        },
        summary_lines=[
            f"processed_volume: {volume_material['volume_number']}",
            f"source_volume_dir: {volume_material['volume_dir']}",
            f"chapter_count: {len(volume_material['chapters'])}",
            f"extra_file_count: {len(volume_material['extras'])}",
            f"source_char_count: {source_char_count or 'unknown'}",
        ],
    )

    stage_manifest_payload = {
        "generated_at": now_iso(),
        "status": "review_pending",
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "request_mode": "per_document_function_call_with_volume_session",
        "api_calls": generated_documents,
        "loaded_file_count": loaded_file_count,
        "source_char_count": source_char_count,
        "generated_document_keys": [item.get("key") for item in generated_documents],
        "global_files": {
            key: str(paths[key])
            for key in ("book_outline", "style_guide", "world_model", "foreshadowing")
            if paths[key].exists()
        },
        "volume_files": {
            "volume_outline": str(paths["volume_outline"]),
            "adaptation_review": str(paths["adaptation_review"]),
            "source_digest": str(paths["source_digest"]),
        },
        "adaptation_review": {
            "status": "pending",
            "review_file": str(paths["adaptation_review"]),
            "note": "资料文档已生成，等待卷资料审核通过后才会标记本卷完成。",
        },
        "stage_summary": {
            "processed_volume": volume_material["volume_number"],
            "generated_documents": [item.get("label") for item in generated_documents],
            "loaded_file_count": loaded_file_count,
            "source_char_count": source_char_count,
        },
    }
    write_markdown_data(
        paths["stage_manifest"],
        title=f"Stage Manifest {volume_material['volume_number']}",
        payload=stage_manifest_payload,
        summary_lines=[
            f"status: review_pending",
            f"processed_volume: {volume_material['volume_number']}",
            f"request_mode: per_document_function_call_with_volume_session",
            f"global_dir: {paths['global_dir']}",
            f"volume_dir: {paths['volume_dir']}",
            f"adaptation_review: {paths['adaptation_review']}",
        ],
    )

    return paths

def chapter_group_plan_root(project_root: Path, volume_number: str) -> Path:
    return project_root / GROUP_ROOT_DIRNAME / f"{volume_number}{GROUP_DIR_SUFFIX}"

def chapter_group_plan_path(project_root: Path, volume_number: str) -> Path:
    return chapter_group_plan_root(project_root, volume_number) / CHAPTER_GROUP_PLAN_MANIFEST_NAME

def build_adaptive_chapter_group_plan(volume_material: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current_numbers: list[str] = []
    current_chars = 0
    extra_chars = sum(len(str(extra.get("text", ""))) for extra in volume_material.get("extras", []))

    def flush_current() -> None:
        nonlocal current_numbers, current_chars
        if not current_numbers:
            return
        groups.append(
            {
                "chapter_numbers": list(current_numbers),
                "chapter_count": len(current_numbers),
                "source_chapter_range": f"{current_numbers[0]}-{current_numbers[-1]}",
                "source_char_count": current_chars + extra_chars,
            }
        )
        current_numbers = []
        current_chars = 0

    for chapter in volume_material.get("chapters", []):
        raw_chapter_number = str(chapter.get("chapter_number") or "").strip()
        if not raw_chapter_number:
            continue
        chapter_number = raw_chapter_number.zfill(4)
        chapter_chars = len(str(chapter.get("text", "")))
        projected_chars = current_chars + chapter_chars + extra_chars
        if current_numbers and (
            len(current_numbers) >= MAX_CHAPTERS_PER_GROUP
            or projected_chars > TARGET_CHAPTER_GROUP_SOURCE_CHARS
        ):
            flush_current()
        current_numbers.append(chapter_number)
        current_chars += chapter_chars
    flush_current()
    return groups

def write_chapter_group_plan_manifest(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    source_char_count: int,
) -> Path | None:
    groups = build_adaptive_chapter_group_plan(volume_material)
    if not groups:
        return None

    project_root = Path(manifest["project_root"])
    volume_number = volume_material["volume_number"]
    plan_path = chapter_group_plan_path(project_root, volume_number)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now_iso(),
        "status": "ready",
        "volume_number": volume_number,
        "source_volume_dir": volume_material.get("volume_dir", ""),
        "target_group_source_chars": TARGET_CHAPTER_GROUP_SOURCE_CHARS,
        "max_chapters_per_group": MAX_CHAPTERS_PER_GROUP,
        "source_char_count": source_char_count,
        "total_groups": len(groups),
        "total_chapters": sum(int(group["chapter_count"]) for group in groups),
        "groups": groups,
    }
    write_markdown_data(
        plan_path,
        title=f"Chapter Group Plan {volume_number}",
        payload=payload,
        summary_lines=[
            "status: ready",
            f"volume_number: {volume_number}",
            f"total_groups: {len(groups)}",
            f"total_chapters: {payload['total_chapters']}",
        ],
    )
    return plan_path

def write_stage_outputs_after_adaptation_review(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    generated_documents: list[dict[str, Any]],
    source_char_count: int,
    loaded_file_count: int,
    review_result: AdaptationReviewResult,
) -> dict[str, Path]:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, volume_material["volume_number"])
    chapter_group_plan = write_chapter_group_plan_manifest(
        manifest,
        volume_material,
        source_char_count=source_char_count,
    )
    processed = set(manifest.get("processed_volumes", []))
    processed.add(volume_material["volume_number"])
    manifest["processed_volumes"] = sorted(processed)
    manifest["last_processed_volume"] = volume_material["volume_number"]
    save_manifest(manifest)

    review = review_result.payload
    stage_manifest_payload = {
        "generated_at": now_iso(),
        "status": "completed",
        "processed_volume": volume_material["volume_number"],
        "source_volume_dir": volume_material["volume_dir"],
        "request_mode": "per_document_function_call_with_volume_session_with_volume_review",
        "api_calls": generated_documents,
        "loaded_file_count": loaded_file_count,
        "source_char_count": source_char_count,
        "generated_document_keys": [item.get("key") for item in generated_documents],
        "global_files": {
            key: str(paths[key])
            for key in ("book_outline", "style_guide", "world_model", "foreshadowing")
            if paths[key].exists()
        },
        "volume_files": {
            "volume_outline": str(paths["volume_outline"]),
            "adaptation_review": str(paths["adaptation_review"]),
            "source_digest": str(paths["source_digest"]),
        },
        "chapter_group_plan": str(chapter_group_plan) if chapter_group_plan is not None else "",
        "adaptation_review": {
            "status": "passed" if review.passed else "failed",
            "passed": review.passed,
            "review_file": review_result.review_path,
            "response_ids": review_result.response_ids,
            "fix_attempts": review_result.fix_attempts,
            "blocking_issues": review.blocking_issues,
            "rewrite_targets": review.rewrite_targets,
        },
        "stage_summary": {
            "processed_volume": volume_material["volume_number"],
            "generated_documents": [item.get("label") for item in generated_documents],
            "loaded_file_count": loaded_file_count,
            "source_char_count": source_char_count,
            "adaptation_review_status": "passed" if review.passed else "failed",
            "chapter_group_plan": str(chapter_group_plan) if chapter_group_plan is not None else "",
        },
    }
    write_markdown_data(
        paths["stage_manifest"],
        title=f"Stage Manifest {volume_material['volume_number']}",
        payload=stage_manifest_payload,
        summary_lines=[
            "status: completed",
            f"processed_volume: {volume_material['volume_number']}",
            "request_mode: per_document_function_call_with_volume_session_with_volume_review",
            f"global_dir: {paths['global_dir']}",
            f"volume_dir: {paths['volume_dir']}",
            f"adaptation_review: {paths['adaptation_review']}",
        ],
    )

    return paths


def mark_volume_processed_after_review(
    manifest: dict[str, Any],
    volume_material: dict[str, Any],
    *,
    generated_documents: list[dict[str, Any]],
    source_char_count: int,
    loaded_file_count: int,
    review_result: AdaptationReviewResult,
) -> dict[str, Path]:
    return write_stage_outputs_after_adaptation_review(
        manifest,
        volume_material,
        generated_documents=generated_documents,
        source_char_count=source_char_count,
        loaded_file_count=loaded_file_count,
        review_result=review_result,
    )

__all__ = [
    'parse_args',
    'validate_source_root',
    'manifest_matches_source_root',
    'find_existing_project_for_source',
    'resolve_input_root',
    'discover_volume_dirs',
    'discover_volume_files',
    'choose_project_root',
    'load_manifest',
    'save_manifest',
    'ensure_project_dirs',
    'resolve_style_mode',
    'resolve_protagonist_mode',
    'init_or_load_project',
    'select_volume_to_process',
    'select_source_rebalance_start_volume',
    'find_next_pending_volume_after',
    'append_source_rebalance_history',
    'backup_project_outputs_for_source_rebalance',
    'clear_unprocessed_manifest_state_after_rebalance',
    'clear_rewrite_manifest_after_source_rebalance',
    'prepare_source_volumes_for_adaptation',
    'resolve_run_mode',
    'stage_paths',
    'write_source_inventory_snapshot',
    'write_stage_outputs',
    'chapter_group_plan_root',
    'chapter_group_plan_path',
    'build_adaptive_chapter_group_plan',
    'write_chapter_group_plan_manifest',
    'write_stage_outputs_after_adaptation_review',
    'mark_volume_processed_after_review',
]
