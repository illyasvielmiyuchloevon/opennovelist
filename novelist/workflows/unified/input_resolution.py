from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def resolve_startup_mode(args: argparse.Namespace) -> str:
    if args.reconfigure_openai:
        return STARTUP_MODE_CONFIG_AND_WORKFLOW
    if args.startup_mode:
        return args.startup_mode
    if args.input_path:
        return STARTUP_MODE_WORKFLOW
    if not sys.stdin or not sys.stdin.isatty():
        return STARTUP_MODE_WORKFLOW
    return prompt_choice(
        "请选择启动方式",
        [
            (STARTUP_MODE_WORKFLOW, STARTUP_MODE_LABELS[STARTUP_MODE_WORKFLOW]),
            (STARTUP_MODE_CONFIG_AND_WORKFLOW, STARTUP_MODE_LABELS[STARTUP_MODE_CONFIG_AND_WORKFLOW]),
            (STARTUP_MODE_CONFIG_ONLY, STARTUP_MODE_LABELS[STARTUP_MODE_CONFIG_ONLY]),
        ],
    )

def prompt_next_startup_mode(*, after_error: bool = False) -> str | None:
    if not sys.stdin or not sys.stdin.isatty():
        return None
    label = "本次执行失败。请选择下一步" if after_error else "本次执行完成。请选择下一步"
    choice = prompt_choice(
        label,
        [
            (STARTUP_MODE_WORKFLOW, STARTUP_MODE_LABELS[STARTUP_MODE_WORKFLOW]),
            (STARTUP_MODE_CONFIG_AND_WORKFLOW, STARTUP_MODE_LABELS[STARTUP_MODE_CONFIG_AND_WORKFLOW]),
            (STARTUP_MODE_CONFIG_ONLY, STARTUP_MODE_LABELS[STARTUP_MODE_CONFIG_ONLY]),
            ("exit", "退出程序"),
        ],
    )
    return None if choice == "exit" else choice

def resolve_input_path(raw_path: str | None) -> Path:
    if raw_path:
        return normalize_path(raw_path)

    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH, legacy_path=LEGACY_GLOBAL_CONFIG_PATH)
    default_path = (
        global_config.get("last_workflow_input")
        or global_config.get("last_project_root")
        or global_config.get("last_source_root")
        or global_config.get("last_input_root")
    )
    raw_path = prompt_text(
        "请输入原始小说 txt、split_novel 书名目录或已有工程目录路径",
        default=str(default_path) if default_path else None,
    )
    return normalize_path(raw_path)

def detect_input_kind(input_path: Path) -> str:
    if not input_path.exists():
        raise FileNotFoundError(f"路径不存在：{input_path}")

    if input_path.is_file():
        return INPUT_RAW_TEXT

    manifest = adaptation_workflow.load_manifest(input_path)
    if manifest is not None:
        return INPUT_PROJECT_ROOT

    if input_path.is_dir() and adaptation_workflow.discover_volume_dirs(input_path):
        return INPUT_SPLIT_ROOT

    fail(
        "无法识别输入路径类型。"
        "请输入原始小说 txt、split_novel 的书名目录，或已有工程目录。"
    )

def direct_input_kind(input_path: Path) -> str | None:
    if not input_path.exists():
        return None
    if input_path.is_file():
        return INPUT_RAW_TEXT
    manifest = adaptation_workflow.load_manifest(input_path)
    if manifest is not None:
        return INPUT_PROJECT_ROOT
    if input_path.is_dir() and adaptation_workflow.discover_volume_dirs(input_path):
        return INPUT_SPLIT_ROOT
    return None

def discover_nested_input_candidates(root_path: Path) -> list[dict[str, Any]]:
    if not root_path.exists() or not root_path.is_dir():
        return []

    candidates: list[dict[str, Any]] = []
    for child in root_path.iterdir():
        kind = direct_input_kind(child)
        if kind is None:
            continue
        try:
            modified_at = child.stat().st_mtime
        except OSError:
            modified_at = 0.0
        candidates.append(
            {
                "kind": kind,
                "path": child,
                "modified_at": modified_at,
            }
        )
    return candidates

def resolve_workflow_entry(input_path: Path) -> tuple[Path, str]:
    kind = direct_input_kind(input_path)
    if kind is not None:
        return input_path, kind

    candidates = discover_nested_input_candidates(input_path)
    if not candidates:
        fail(
            "无法识别输入路径类型。请输入原始小说 txt、split_novel 的书名目录，或已有工程目录。"
        )

    priority = {
        INPUT_PROJECT_ROOT: 0,
        INPUT_SPLIT_ROOT: 1,
        INPUT_RAW_TEXT: 2,
    }
    candidates.sort(
        key=lambda item: (
            priority.get(str(item["kind"]), 99),
            -float(item["modified_at"]),
            str(item["path"]).lower(),
        )
    )
    selected = candidates[0]
    print_progress(
        f"已从目录 {input_path} 自动识别工作流入口：{selected['path']} "
        f"（类型：{selected['kind']}）。"
    )
    if len(candidates) > 1:
        print_progress(f"同目录下共识别到 {len(candidates)} 个可用入口，已按优先级自动选择最合适的一个。")
    return Path(selected["path"]), str(selected["kind"])

def resolve_adaptation_run_mode(args: argparse.Namespace) -> str:
    adaptation_run_mode = getattr(args, "adaptation_run_mode", None)
    if adaptation_run_mode:
        return adaptation_run_mode
    if getattr(args, "input_path", None):
        return adaptation_workflow.RUN_MODE_BOOK
    if not sys.stdin or not sys.stdin.isatty():
        return adaptation_workflow.RUN_MODE_BOOK
    return prompt_choice(
        "选择 novel_adaptation 的运行方式",
        [
            (adaptation_workflow.RUN_MODE_STAGE, "按阶段运行（每卷结束后确认）"),
            (adaptation_workflow.RUN_MODE_BOOK, "按全书运行（自动连续处理后续卷）"),
        ],
    )

def resolve_rewrite_run_mode(args: argparse.Namespace) -> str:
    rewrite_run_mode = getattr(args, "rewrite_run_mode", None)
    if rewrite_run_mode:
        return rewrite_run_mode
    if getattr(args, "input_path", None):
        return rewrite_workflow.RUN_MODE_VOLUME
    if not sys.stdin or not sys.stdin.isatty():
        return rewrite_workflow.RUN_MODE_VOLUME
    return prompt_choice(
        "选择 novel_chapter_rewrite 的运行方式",
        [
            (rewrite_workflow.RUN_MODE_CHAPTER, "按章节运行"),
            (rewrite_workflow.RUN_MODE_GROUP, "按组运行"),
            (rewrite_workflow.RUN_MODE_VOLUME, "按卷运行"),
        ],
    )

def maybe_configure_openai(
    args: argparse.Namespace,
    *,
    llm_needed: bool,
    force_reconfigure: bool = False,
) -> None:
    if not llm_needed:
        return

    should_configure = force_reconfigure or args.reconfigure_openai or any(
        [args.base_url, args.api_key, args.model]
    )
    if not should_configure:
        return

    print_progress("开始处理统一入口的 OpenAI 全局设置。")
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH, legacy_path=LEGACY_GLOBAL_CONFIG_PATH)
    if force_reconfigure:
        _, settings, _ = openai_config.force_reconfigure_openai(
            cli_provider=args.provider,
            cli_protocol=args.protocol,
            cli_base_url=args.base_url,
            cli_api_key=args.api_key,
            cli_model=args.model,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
        )
        print_progress(
            f"统一入口已重新写入提供商：{openai_config.PROVIDER_LABELS.get(settings['provider'], settings['provider'])}"
        )
        print_progress(
            f"统一入口已重新写入协议：{openai_config.PROTOCOL_LABELS.get(settings['protocol'], settings['protocol'])}"
        )
        print_progress(f"统一入口已重新写入 base_url：{settings['base_url']}")
        print_progress(f"统一入口已重新写入模型：{settings['model']}")
        return

    _, global_config = openai_config.resolve_api_key(
        cli_api_key=args.api_key,
        global_config=global_config,
        config_path=GLOBAL_CONFIG_PATH,
    )
    settings, _ = openai_config.resolve_openai_settings(
        cli_provider=args.provider,
        cli_protocol=args.protocol,
        cli_base_url=args.base_url,
        cli_model=args.model,
        global_config=global_config,
        config_path=GLOBAL_CONFIG_PATH,
    )
    print_progress(
        f"统一入口已写入提供商：{openai_config.PROVIDER_LABELS.get(settings['provider'], settings['provider'])}"
    )
    print_progress(
        f"统一入口已写入协议：{openai_config.PROTOCOL_LABELS.get(settings['protocol'], settings['protocol'])}"
    )
    print_progress(f"统一入口已写入 base_url：{settings['base_url']}")
    print_progress(f"统一入口已写入模型：{settings['model']}")

def remember_workflow_input(input_path: Path) -> None:
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH, legacy_path=LEGACY_GLOBAL_CONFIG_PATH)
    openai_config.update_global_config(
        GLOBAL_CONFIG_PATH,
        global_config,
        {"last_workflow_input": str(input_path)},
    )

def run_split_stage(source_file: Path) -> Path:
    print_progress("开始执行 split_novel 阶段。")
    text, encoding = split_novel.read_text(source_file)
    intro, chapters = split_novel.split_chapters(text)
    output_root = split_novel.ensure_output_root(source_file)
    intro_path = split_novel.write_intro_file(intro, source_file, output_root)
    volume_count = split_novel.write_chapters(chapters, source_file, output_root)
    print_progress(f"split_novel 已完成：源文件 {source_file}")
    print_progress(f"读取编码：{encoding}")
    print_progress(f"拆分章节数：{len(chapters)}")
    print_progress(f"生成卷数：{volume_count}")
    print_progress(f"简介文件：{intro_path}")
    print_progress(f"拆分输出目录：{output_root}")
    return output_root

def resolve_project_root_for_source(
    source_root: Path,
    requested_project_root: str | None,
) -> Path:
    if requested_project_root:
        candidate = normalize_path(requested_project_root)
        manifest = adaptation_workflow.load_manifest(candidate)
        if manifest is None:
            fail(f"指定工程目录中未找到项目清单：{candidate}")
        return candidate

    project_root, manifest = adaptation_workflow.find_existing_project_for_source(source_root)
    if project_root is not None and manifest is not None:
        return project_root

    fail(
        f"未能从来源目录找到对应工程：{source_root}\n"
        "请先运行 novel_adaptation，或通过 --project-root 指定工程目录。"
    )

def try_resolve_existing_project_root(
    source_root: Path,
    requested_project_root: str | None,
) -> Path | None:
    if requested_project_root:
        candidate = normalize_path(requested_project_root)
        return candidate if adaptation_workflow.load_manifest(candidate) is not None else None

    project_root, manifest = adaptation_workflow.find_existing_project_for_source(source_root)
    if project_root is not None and manifest is not None:
        return project_root
    return None

def try_resolve_existing_project_from_raw_text(
    source_file: Path,
    requested_project_root: str | None,
) -> tuple[Path | None, Path | None]:
    if requested_project_root:
        candidate = normalize_path(requested_project_root)
        manifest = adaptation_workflow.load_manifest(candidate)
        if manifest is None:
            return None, None
        try:
            source_root = normalize_path(str(manifest["source_root"]))
        except Exception:
            return None, None
        if source_root.parent != source_file.parent:
            return None, None
        if source_root.name != source_file.stem and not source_root.name.startswith(f"{source_file.stem}_"):
            return None, None
        return source_root, candidate

    matches: list[tuple[str, Path, Path]] = []
    for child in source_file.parent.iterdir():
        if not child.is_dir():
            continue
        if child.name != source_file.stem and not child.name.startswith(f"{source_file.stem}_"):
            continue
        if not adaptation_workflow.discover_volume_dirs(child):
            continue
        project_root, manifest = adaptation_workflow.find_existing_project_for_source(child)
        if project_root is None or manifest is None:
            continue
        matches.append((str(manifest.get("updated_at", "")), child, project_root))

    if not matches:
        return None, None

    matches.sort(key=lambda item: item[0], reverse=True)
    _, source_root, project_root = matches[0]
    return source_root, project_root

__all__ = [
    'resolve_startup_mode',
    'prompt_next_startup_mode',
    'resolve_input_path',
    'detect_input_kind',
    'direct_input_kind',
    'discover_nested_input_candidates',
    'resolve_workflow_entry',
    'resolve_adaptation_run_mode',
    'resolve_rewrite_run_mode',
    'maybe_configure_openai',
    'remember_workflow_input',
    'run_split_stage',
    'resolve_project_root_for_source',
    'try_resolve_existing_project_root',
    'try_resolve_existing_project_from_raw_text',
]
