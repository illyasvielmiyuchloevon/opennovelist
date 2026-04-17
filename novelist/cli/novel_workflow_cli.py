from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from novelist.cli import novel_adaptation_cli as adaptation_cli
from novelist.cli import novel_chapter_rewrite_cli as rewrite_cli
from novelist.cli import split_novel
import novelist.core.openai_config as openai_config
from novelist.core.files import normalize_path
from novelist.core.ui import fail, pause_before_exit, print_progress, prompt_choice, prompt_text


INPUT_RAW_TEXT = "raw_text"
INPUT_SPLIT_ROOT = "split_root"
INPUT_PROJECT_ROOT = "project_root"
STARTUP_MODE_WORKFLOW = "workflow"
STARTUP_MODE_CONFIG_AND_WORKFLOW = "configure_and_workflow"
STARTUP_MODE_CONFIG_ONLY = "configure_only"
STARTUP_MODE_LABELS = {
    STARTUP_MODE_WORKFLOW: "直接进入统一工作流",
    STARTUP_MODE_CONFIG_AND_WORKFLOW: "先重新配置 OpenAI 设置，再进入统一工作流",
    STARTUP_MODE_CONFIG_ONLY: "只重新配置 OpenAI 设置",
}
WORKDIR = Path(__file__).resolve().parents[2]
GLOBAL_CONFIG_PATH = adaptation_cli.GLOBAL_CONFIG_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "统一调度 split_novel、novel_adaptation_cli、novel_chapter_rewrite_cli，"
            "支持从原始小说文本、拆分后的书名目录或已有工程目录启动全流程。"
        )
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help="原始小说 txt、split_novel 的书名目录、或已有工程目录路径；不传时启动后提示输入。",
    )
    parser.add_argument("--new-title", help="新书名。")
    parser.add_argument("--target-worldview", help="目标世界观。")
    parser.add_argument(
        "--style-mode",
        choices=(adaptation_cli.STYLE_MODE_CUSTOM, adaptation_cli.STYLE_MODE_SOURCE),
        help="写作风格来源模式。",
    )
    parser.add_argument("--style-file", help="自定义写作风格文件路径。")
    parser.add_argument(
        "--protagonist-mode",
        choices=(adaptation_cli.PROTAGONIST_MODE_CUSTOM, adaptation_cli.PROTAGONIST_MODE_ADAPTIVE),
        help="主角设定来源模式。",
    )
    parser.add_argument("--protagonist-text", help="自定义主角设定和性格描述。")
    parser.add_argument("--project-root", help="工程目录路径。")
    parser.add_argument(
        "--adaptation-run-mode",
        choices=(adaptation_cli.RUN_MODE_STAGE, adaptation_cli.RUN_MODE_BOOK),
        help="novel_adaptation_cli 的运行方式。",
    )
    parser.add_argument(
        "--rewrite-run-mode",
        choices=(rewrite_cli.RUN_MODE_CHAPTER, rewrite_cli.RUN_MODE_GROUP, rewrite_cli.RUN_MODE_VOLUME),
        help="novel_chapter_rewrite_cli 的运行方式。",
    )
    parser.add_argument("--adaptation-volume", help="只让 novel_adaptation_cli 处理指定卷，例如 001。")
    parser.add_argument("--rewrite-volume", help="只让 novel_chapter_rewrite_cli 处理指定卷，例如 001。")
    parser.add_argument("--rewrite-chapter", help="只让 novel_chapter_rewrite_cli 处理指定章，例如 0001。")
    parser.add_argument("--base-url", help="OpenAI Responses API 的 base_url。")
    parser.add_argument("--api-key", help="OpenAI API Key。")
    parser.add_argument("--model", help="调用的模型名称。")
    parser.add_argument(
        "--provider",
        choices=(openai_config.PROVIDER_OPENAI, openai_config.PROVIDER_OPENAI_COMPATIBLE),
        help="API 提供商。",
    )
    parser.add_argument(
        "--protocol",
        choices=(openai_config.PROTOCOL_RESPONSES, openai_config.PROTOCOL_OPENAI_COMPATIBLE),
        help="API 协议。",
    )
    parser.add_argument(
        "--startup-mode",
        choices=(STARTUP_MODE_WORKFLOW, STARTUP_MODE_CONFIG_AND_WORKFLOW, STARTUP_MODE_CONFIG_ONLY),
        help="启动方式：直接进入工作流、先重新配置 OpenAI 再进入工作流、或只重新配置 OpenAI。",
    )
    parser.add_argument(
        "--reconfigure-openai",
        "--reset-openai-settings",
        dest="reconfigure_openai",
        action="store_true",
        help="重新设置并记住 base_url、api_key、model。",
    )
    parser.add_argument("--skip-split", action="store_true", help="跳过 split_novel 阶段。")
    parser.add_argument("--skip-adaptation", action="store_true", help="跳过 novel_adaptation_cli 阶段。")
    parser.add_argument("--skip-rewrite", action="store_true", help="跳过 novel_chapter_rewrite_cli 阶段。")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="对子 CLI 传递 dry-run；split 阶段仍会真实执行本地拆分。",
    )
    return parser.parse_args()


def resolve_startup_mode(args: argparse.Namespace) -> str:
    if args.reconfigure_openai:
        return STARTUP_MODE_CONFIG_AND_WORKFLOW
    if args.startup_mode:
        return args.startup_mode
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

    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH)
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

    manifest = adaptation_cli.load_manifest(input_path)
    if manifest is not None:
        return INPUT_PROJECT_ROOT

    if input_path.is_dir() and adaptation_cli.discover_volume_dirs(input_path):
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
    manifest = adaptation_cli.load_manifest(input_path)
    if manifest is not None:
        return INPUT_PROJECT_ROOT
    if input_path.is_dir() and adaptation_cli.discover_volume_dirs(input_path):
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
    if args.adaptation_run_mode:
        return args.adaptation_run_mode
    if not sys.stdin or not sys.stdin.isatty():
        return adaptation_cli.RUN_MODE_BOOK
    return prompt_choice(
        "选择 novel_adaptation_cli 的运行方式",
        [
            (adaptation_cli.RUN_MODE_STAGE, "按阶段运行（每卷结束后确认）"),
            (adaptation_cli.RUN_MODE_BOOK, "按全书运行（自动连续处理后续卷）"),
        ],
    )


def resolve_rewrite_run_mode(args: argparse.Namespace) -> str:
    if args.rewrite_run_mode:
        return args.rewrite_run_mode
    if not sys.stdin or not sys.stdin.isatty():
        return rewrite_cli.RUN_MODE_VOLUME
    return prompt_choice(
        "选择 novel_chapter_rewrite_cli 的运行方式",
        [
            (rewrite_cli.RUN_MODE_CHAPTER, "按章节运行"),
            (rewrite_cli.RUN_MODE_GROUP, "按组运行"),
            (rewrite_cli.RUN_MODE_VOLUME, "按卷运行"),
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
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH)
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
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH)
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
        manifest = adaptation_cli.load_manifest(candidate)
        if manifest is None:
            fail(f"指定工程目录中未找到项目清单：{candidate}")
        return candidate

    project_root, manifest = adaptation_cli.find_existing_project_for_source(source_root)
    if project_root is not None and manifest is not None:
        return project_root

    fail(
        f"未能从来源目录找到对应工程：{source_root}\n"
        "请先运行 novel_adaptation_cli，或通过 --project-root 指定工程目录。"
    )


def try_resolve_existing_project_root(
    source_root: Path,
    requested_project_root: str | None,
) -> Path | None:
    if requested_project_root:
        candidate = normalize_path(requested_project_root)
        return candidate if adaptation_cli.load_manifest(candidate) is not None else None

    project_root, manifest = adaptation_cli.find_existing_project_for_source(source_root)
    if project_root is not None and manifest is not None:
        return project_root
    return None


def sorted_volume_numbers(volume_numbers: list[str]) -> list[str]:
    normalized = [str(item).zfill(3) for item in volume_numbers if str(item).strip()]
    return sorted(dict.fromkeys(normalized), key=lambda item: int(item))


def pending_rewrite_volumes(project_root: Path) -> list[str]:
    adaptation_manifest = adaptation_cli.load_manifest(project_root)
    if adaptation_manifest is None:
        return []

    rewrite_manifest = rewrite_cli.load_rewrite_manifest(project_root)
    adapted_volumes = sorted_volume_numbers(list(adaptation_manifest.get("processed_volumes", [])))
    rewritten_volumes = set(
        sorted_volume_numbers(list((rewrite_manifest or {}).get("processed_volumes", [])))
    )
    return [volume for volume in adapted_volumes if volume not in rewritten_volumes]


def build_adaptation_cli_args(
    args: argparse.Namespace,
    *,
    input_root: Path,
    run_mode: str,
    workflow_controlled: bool = False,
    volume_override: str | None = None,
) -> list[str]:
    cli_args = [str(input_root), "--run-mode", run_mode]
    if args.new_title:
        cli_args.extend(["--new-title", args.new_title])
    if args.target_worldview:
        cli_args.extend(["--target-worldview", args.target_worldview])
    if args.style_mode:
        cli_args.extend(["--style-mode", args.style_mode])
    if args.style_file:
        cli_args.extend(["--style-file", args.style_file])
    if args.protagonist_mode:
        cli_args.extend(["--protagonist-mode", args.protagonist_mode])
    if args.protagonist_text:
        cli_args.extend(["--protagonist-text", args.protagonist_text])
    if args.project_root:
        cli_args.extend(["--project-root", args.project_root])
    target_volume = volume_override or args.adaptation_volume
    if target_volume:
        cli_args.extend(["--volume", target_volume])
    if args.dry_run:
        cli_args.append("--dry-run")
    if workflow_controlled:
        cli_args.append("--workflow-controlled")
    return cli_args


def build_rewrite_cli_args(
    args: argparse.Namespace,
    *,
    project_root: Path,
    run_mode: str,
    workflow_controlled: bool = False,
    volume_override: str | None = None,
) -> list[str]:
    cli_args = [str(project_root), "--run-mode", run_mode]
    target_volume = volume_override or args.rewrite_volume
    if target_volume:
        cli_args.extend(["--volume", target_volume])
    if args.rewrite_chapter:
        cli_args.extend(["--chapter", args.rewrite_chapter])
    if args.dry_run:
        cli_args.append("--dry-run")
    if workflow_controlled:
        cli_args.append("--workflow-controlled")
    return cli_args


def run_python_cli(script_name: str, cli_args: list[str]) -> None:
    module_name = f"novelist.cli.{script_name.removesuffix('.py')}"
    command = [sys.executable, "-m", module_name, *cli_args]
    print_progress(f"开始执行 {module_name}：{' '.join(cli_args)}")
    result = subprocess.run(command, cwd=str(WORKDIR), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{module_name} 执行失败，退出码：{result.returncode}")


def main() -> int:
    args = parse_args()
    next_startup_mode: str | None = None

    while True:
        try:
            startup_mode = next_startup_mode or resolve_startup_mode(args)
            next_startup_mode = None
            print_progress(f"本次启动方式：{STARTUP_MODE_LABELS.get(startup_mode, startup_mode)}")
            llm_needed = not args.skip_adaptation or not args.skip_rewrite

            if startup_mode in (STARTUP_MODE_CONFIG_AND_WORKFLOW, STARTUP_MODE_CONFIG_ONLY):
                maybe_configure_openai(
                    args,
                    llm_needed=True,
                    force_reconfigure=True,
                )
                if startup_mode == STARTUP_MODE_CONFIG_ONLY:
                    print_progress("OpenAI 设置已重新配置完成。")
                    if not sys.stdin or not sys.stdin.isatty():
                        return 0
                    next_startup_mode = prompt_next_startup_mode()
                    if next_startup_mode is None:
                        return 0
                    continue
            else:
                maybe_configure_openai(args, llm_needed=llm_needed)

            input_path = resolve_input_path(args.input_path)
            workflow_entry, input_kind = resolve_workflow_entry(input_path)
            remember_workflow_input(workflow_entry)

            source_root: Path | None = None
            project_root: Path | None = None

            print_progress(f"统一入口已识别输入类型：{input_kind}")
            if input_kind == INPUT_RAW_TEXT:
                if args.skip_split:
                    fail("输入为原始小说 txt 时不能跳过 split 阶段。")
                source_root = run_split_stage(workflow_entry)
            elif input_kind == INPUT_SPLIT_ROOT:
                source_root = workflow_entry
                print_progress(f"已识别为 split_novel 书名目录：{source_root}")
            elif input_kind == INPUT_PROJECT_ROOT:
                project_root = workflow_entry
                manifest = adaptation_cli.load_manifest(project_root)
                if manifest is None:
                    fail(f"工程目录缺少项目清单：{project_root}")
                source_root = normalize_path(str(manifest["source_root"]))
                print_progress(f"已识别为已有工程目录：{project_root}")
                print_progress(f"工程来源目录：{source_root}")
            else:
                fail(f"不支持的输入类型：{input_kind}")

            if project_root is None and source_root is not None:
                existing_project_root = try_resolve_existing_project_root(source_root, args.project_root)
                if existing_project_root is not None:
                    project_root = existing_project_root
                    print_progress(f"已识别到来源目录对应的已有工程：{project_root}")

            rewrite_backlog_volumes: list[str] = []
            if project_root is not None and not args.skip_rewrite:
                rewrite_backlog_volumes = pending_rewrite_volumes(project_root)

            adaptation_enabled = not args.skip_adaptation
            if adaptation_enabled and not args.skip_rewrite and rewrite_backlog_volumes:
                adaptation_enabled = False
                print_progress(
                    "检测到已有已适配但未完成重写的卷："
                    + "、".join(rewrite_backlog_volumes)
                )
                print_progress("统一工作流将优先续跑章节重写，当前轮次暂时跳过继续处理下一卷适配。")

            adaptation_run_mode = resolve_adaptation_run_mode(args) if adaptation_enabled else ""
            rewrite_run_mode = resolve_rewrite_run_mode(args) if not args.skip_rewrite else ""
            adaptation_workflow_controlled = (
                adaptation_enabled
                and not args.skip_rewrite
                and adaptation_run_mode == adaptation_cli.RUN_MODE_STAGE
            )
            rewrite_workflow_controlled = adaptation_workflow_controlled
            adapted_volume_number: str | None = None

            if adaptation_enabled:
                adaptation_input = project_root or source_root
                assert adaptation_input is not None
                run_python_cli(
                    "novel_adaptation_cli.py",
                    build_adaptation_cli_args(
                        args,
                        input_root=adaptation_input,
                        run_mode=adaptation_run_mode,
                        workflow_controlled=adaptation_workflow_controlled,
                    ),
                )
                assert source_root is not None
                project_root = resolve_project_root_for_source(source_root, args.project_root)
                manifest = adaptation_cli.load_manifest(project_root)
                adapted_volume_number = str((manifest or {}).get("last_processed_volume") or "").strip() or None
                print_progress(f"novel_adaptation_cli 完成后工程目录：{project_root}")
                if adapted_volume_number:
                    print_progress(f"本轮统一工作流已完成适配卷：{adapted_volume_number}")

            if not args.skip_rewrite:
                if project_root is None:
                    assert source_root is not None
                    project_root = resolve_project_root_for_source(source_root, args.project_root)
                rewrite_volume_override = args.rewrite_volume or adapted_volume_number
                if rewrite_volume_override is None and rewrite_backlog_volumes:
                    rewrite_volume_override = rewrite_backlog_volumes[0]
                run_python_cli(
                    "novel_chapter_rewrite_cli.py",
                    build_rewrite_cli_args(
                        args,
                        project_root=project_root,
                        run_mode=rewrite_run_mode,
                        workflow_controlled=rewrite_workflow_controlled,
                        volume_override=rewrite_volume_override,
                    ),
                )

            if args.skip_adaptation and args.skip_rewrite and input_kind != INPUT_RAW_TEXT:
                print_progress("未启用 adaptation / rewrite 阶段，本次没有更多可执行步骤。")

            print_progress("统一工作流执行完成。")
            if not sys.stdin or not sys.stdin.isatty():
                return 0
            next_startup_mode = prompt_next_startup_mode()
            if next_startup_mode is None:
                return 0
        except KeyboardInterrupt:
            print_progress("已取消。", error=True)
            if not sys.stdin or not sys.stdin.isatty():
                pause_before_exit()
                return 1
            next_startup_mode = prompt_next_startup_mode(after_error=True)
            if next_startup_mode is None:
                return 1
        except Exception as error:
            print_progress(f"统一工作流处理失败：{error}", error=True)
            if not sys.stdin or not sys.stdin.isatty():
                pause_before_exit()
                return 1
            try:
                input("按回车键返回启动菜单...")
            except EOFError:
                return 1
            next_startup_mode = prompt_next_startup_mode(after_error=True)
            if next_startup_mode is None:
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
