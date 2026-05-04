from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "基于 novel_adaptation 产出的工程目录，按已审核组纲计划生成仿写章节、配套状态文档与审核文档，"
            "使用 OpenAI Responses API 与 core 运行时。"
        )
    )
    parser.add_argument(
        "input_root",
        nargs="?",
        help="已有小说工程目录路径，或 split_novel 的来源目录路径；不传则启动后提示输入。",
    )
    parser.add_argument("--base-url", help="OpenAI Responses API 的 base_url。")
    parser.add_argument("--api-key", help="OpenAI API Key。")
    parser.add_argument("--model", help="调用的模型名称。")
    parser.add_argument("--volume", help="指定处理某一卷，例如 001。")
    parser.add_argument("--chapter", help="指定处理某一章，例如 0001。")
    parser.add_argument(
        "--run-mode",
        metavar="{group,volume}",
        help="运行模式：group=按章节组运行，volume=按卷运行；旧值 chapter 会兼容为 group。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只识别工程、卷状态和待处理章节，不调用 API。",
    )
    parser.add_argument(
        "--workflow-controlled",
        action="store_true",
        help="由统一工作流入口调度时启用：当前只处理本次目标范围，完成后直接返回，不在子流程 内继续下一章/组/卷。",
    )
    return parser.parse_args()

def validate_source_root(source_root: Path) -> None:
    if not source_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{source_root}")
    if not discover_volume_dirs(source_root):
        fail("当前目录下未识别到 split_novel 产出的编号卷目录，例如 001、002。")

def load_project_manifest(project_root: Path) -> dict[str, Any] | None:
    manifest_path = project_root / PROJECT_MANIFEST_NAME
    if manifest_path.exists():
        return extract_json_payload(manifest_path.read_text(encoding="utf-8"))

    legacy_path = project_root / LEGACY_PROJECT_MANIFEST_NAME
    if legacy_path.exists():
        return json.loads(legacy_path.read_text(encoding="utf-8"))
    return None

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
        manifest = load_project_manifest(child)
        if manifest and manifest_matches_source_root(manifest, source_root):
            candidates.append((str(manifest.get("updated_at", "")), child, manifest))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, project_root, manifest = candidates[0]
    return project_root, manifest

def resolve_project_input(
    raw_path: str | None,
    global_config: dict[str, Any],
) -> tuple[Path, Path, dict[str, Any]]:
    default_path = (
        global_config.get("last_chapter_rewrite_input_root")
        or global_config.get("last_project_root")
        or global_config.get("last_input_root")
        or global_config.get("last_source_root")
    )
    if raw_path is None:
        raw_path = prompt_text(
            "请输入 novel_adaptation 的工程目录路径，或 split_novel 的来源目录路径",
            default=str(default_path) if default_path else None,
        )

    input_root = normalize_path(raw_path)
    if not input_root.exists():
        raise FileNotFoundError(f"文件夹不存在：{input_root}")
    if not input_root.is_dir():
        raise NotADirectoryError(f"路径不是文件夹：{input_root}")

    manifest = load_project_manifest(input_root)
    if manifest is not None:
        source_root = normalize_path(str(manifest["source_root"]))
        validate_source_root(source_root)
        return input_root, source_root, manifest

    source_root = input_root
    validate_source_root(source_root)
    project_root, manifest = find_existing_project_for_source(source_root)
    if project_root is None or manifest is None:
        fail(
            "未在该来源目录旁边识别到 novel_adaptation 的工程目录。"
            "请传入已有工程目录，或先运行 novel_adaptation。"
        )
    return project_root, source_root, manifest

def resolve_run_mode(args: argparse.Namespace) -> str:
    if args.run_mode:
        return normalize_rewrite_run_mode(args.run_mode)
    return prompt_choice(
        "请选择运行方式",
        [
            (RUN_MODE_GROUP, "按章节组运行"),
            (RUN_MODE_VOLUME, "按卷运行"),
        ],
    )

def assess_volume_readiness(project_root: Path, source_root: Path, volume_number: str) -> dict[str, Any]:
    paths = rewrite_paths(project_root, volume_number)
    missing: list[str] = []

    source_volume_dir = source_root / volume_number
    if not source_volume_dir.exists():
        missing.append(f"缺少来源卷目录：{source_volume_dir}")

    for key, file_name in ADAPTATION_GLOBAL_FILE_NAMES.items():
        if not paths[key].exists():
            missing.append(f"缺少全局注入文档：{file_name}")

    if not paths["volume_outline"].exists():
        missing.append(f"缺少卷级大纲：{paths['volume_outline'].name}")
    try:
        load_group_outline_plan(project_root, volume_number, require_passed=True)
    except Exception as error:
        missing.append(str(error))

    return {
        "volume_number": volume_number,
        "eligible": not missing,
        "missing": missing,
    }

def print_volume_readiness_summary(readiness_map: dict[str, dict[str, Any]]) -> None:
    print_progress("卷可进入章节工作流的检测结果：")
    for volume_number in sorted(readiness_map):
        info = readiness_map[volume_number]
        if info["eligible"]:
            print_progress(f"  第 {volume_number} 卷：可进入章节工作流。")
        else:
            print_progress(f"  第 {volume_number} 卷：暂不可进入章节工作流。")
            for reason in info["missing"]:
                print_progress(f"    - {reason}")


def ensure_source_volumes_stable_for_rewrite(
    *,
    source_root: Path,
    project_manifest: dict[str, Any],
    target_volume: Path,
    dry_run: bool,
) -> None:
    report = rebalance_source_volumes(
        source_root,
        start_volume=target_volume.name,
        locked_volumes=set(project_manifest.get("processed_volumes", [])),
        dry_run=True,
    )
    if report.needed or report.warnings:
        for line in rebalance_summary_lines(report):
            print_progress(line, error=bool(report.needed and report.changed and not dry_run))
    if report.needed and report.changed and not dry_run:
        fail(
            "参考源当前卷或后续卷超过自适应分卷预算，章节工作流不会直接重排源卷。"
            "请先运行 novel_adaptation，让卷资料适配阶段自动重分卷并重跑受影响卷资料。"
        )

def select_volume_to_process(
    volume_dirs: list[Path],
    manifest: dict[str, Any],
    readiness_map: dict[str, dict[str, Any]],
    requested_volume: str | None,
) -> Path | None:
    volume_map = {volume_dir.name: volume_dir for volume_dir in volume_dirs}

    if requested_volume:
        normalized = requested_volume.zfill(3)
        if normalized not in volume_map:
            fail(f"未找到指定卷：{normalized}")
        readiness = readiness_map.get(normalized)
        if readiness and not readiness["eligible"]:
            fail(
                f"第 {normalized} 卷的 novel_adaptation 产物尚不完善，暂不可进入章节工作流：\n"
                + "\n".join(readiness["missing"])
            )
        return volume_map[normalized]

    processed = set(manifest.get("processed_volumes", []))
    for volume_dir in volume_dirs:
        if volume_dir.name in processed:
            continue
        readiness = readiness_map.get(volume_dir.name, {})
        if not readiness.get("eligible", False):
            print_progress(f"第 {volume_dir.name} 卷暂不可进入章节工作流，已停止在这一卷。")
            for reason in readiness.get("missing", []):
                print_progress(f"  - {reason}")
            return None
        return volume_dir
    return None

def prompt_next_volume(next_volume: Path | None) -> bool:
    if next_volume is None:
        print_progress("本书完整结束。")
        return False
    if not sys.stdin or not sys.stdin.isatty():
        print_progress(
            f"当前卷已通过审核，下一卷是第 {next_volume.name} 卷；当前环境无法交互确认，程序将退出。"
        )
        return False
    choice = prompt_choice(
        f"当前卷已通过审核。下一卷是第 {next_volume.name} 卷。请选择后续操作",
        [
            ("next", f"开始下一卷（第 {next_volume.name} 卷）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"

def prompt_next_group(next_group: list[str] | None) -> bool:
    if next_group is None:
        return False
    if not sys.stdin or not sys.stdin.isatty():
        print_progress(
            f"当前组已通过审查，下一组是 {next_group[0]}-{next_group[-1]}；当前环境无法交互确认，程序将退出。"
        )
        return False
    choice = prompt_choice(
        f"当前组已通过审查。下一组是 {next_group[0]}-{next_group[-1]}。请选择后续操作",
        [
            ("next", f"继续下一组（{next_group[0]}-{next_group[-1]}）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"

def find_next_volume_after(
    volume_dirs: list[Path],
    current_volume_name: str,
    readiness_map: dict[str, dict[str, Any]],
) -> Path | None:
    found_current = False
    for volume_dir in volume_dirs:
        if not found_current:
            if volume_dir.name == current_volume_name:
                found_current = True
            continue
        readiness = readiness_map.get(volume_dir.name, {})
        if not readiness.get("eligible", False):
            print_progress(f"第 {volume_dir.name} 卷暂不可进入章节工作流，无法继续下一卷。")
            for reason in readiness.get("missing", []):
                print_progress(f"  - {reason}")
            return None
        return volume_dir
    return None

def prompt_continue_same_mode_next_volume(run_mode: str, next_volume: Path | None) -> bool:
    if next_volume is None:
        print_progress("本书完整结束。")
        return False
    mode_label = RUN_MODE_LABELS.get(run_mode, run_mode)
    if not sys.stdin or not sys.stdin.isatty():
        print_progress(
            f"当前卷已经没有可继续的内容，下一卷是第 {next_volume.name} 卷；"
            f"当前环境无法交互确认，程序将退出。"
        )
        return False
    choice = prompt_choice(
        f"当前卷已处理到末尾。请选择后续操作",
        [
            ("next", f"继续下一卷（第 {next_volume.name} 卷，保持{mode_label}）"),
            ("exit", "退出程序"),
        ],
    )
    return choice == "next"

__all__ = [
    'parse_args',
    'validate_source_root',
    'load_project_manifest',
    'manifest_matches_source_root',
    'find_existing_project_for_source',
    'resolve_project_input',
    'resolve_run_mode',
    'assess_volume_readiness',
    'print_volume_readiness_summary',
    'ensure_source_volumes_stable_for_rewrite',
    'select_volume_to_process',
    'prompt_next_volume',
    'prompt_next_group',
    'find_next_volume_after',
    'prompt_continue_same_mode_next_volume',
]
