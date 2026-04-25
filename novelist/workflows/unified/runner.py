from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def build_adaptation_workflow_args(
    args: argparse.Namespace,
    *,
    input_root: Path,
    run_mode: str,
    workflow_controlled: bool = False,
    volume_override: str | None = None,
) -> list[str]:
    workflow_args = [str(input_root), "--run-mode", run_mode]
    if args.new_title:
        workflow_args.extend(["--new-title", args.new_title])
    if args.target_worldview:
        workflow_args.extend(["--target-worldview", args.target_worldview])
    if args.style_mode:
        workflow_args.extend(["--style-mode", args.style_mode])
    if args.style_file:
        workflow_args.extend(["--style-file", args.style_file])
    if args.protagonist_mode:
        workflow_args.extend(["--protagonist-mode", args.protagonist_mode])
    if args.protagonist_text:
        workflow_args.extend(["--protagonist-text", args.protagonist_text])
    if args.project_root:
        workflow_args.extend(["--project-root", args.project_root])
    target_volume = volume_override or args.adaptation_volume
    if target_volume:
        workflow_args.extend(["--volume", target_volume])
    if args.dry_run:
        workflow_args.append("--dry-run")
    if workflow_controlled:
        workflow_args.append("--workflow-controlled")
    return workflow_args

def build_rewrite_workflow_args(
    args: argparse.Namespace,
    *,
    project_root: Path,
    run_mode: str,
    workflow_controlled: bool = False,
    volume_override: str | None = None,
) -> list[str]:
    workflow_args = [str(project_root), "--run-mode", run_mode]
    target_volume = volume_override or args.rewrite_volume
    if target_volume:
        workflow_args.extend(["--volume", target_volume])
    if args.rewrite_chapter:
        workflow_args.extend(["--chapter", args.rewrite_chapter])
    if args.dry_run:
        workflow_args.append("--dry-run")
    if workflow_controlled:
        workflow_args.append("--workflow-controlled")
    return workflow_args

def run_python_workflow(script_name: str, workflow_args: list[str]) -> None:
    module_name = f"novelist.workflows.{script_name.removesuffix('.py')}"
    command = [sys.executable, "-m", module_name, *workflow_args]
    print_progress(f"开始执行 {module_name}：{' '.join(workflow_args)}")
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
                source_root, project_root = try_resolve_existing_project_from_raw_text(
                    workflow_entry,
                    args.project_root,
                )
                if source_root is not None and project_root is not None:
                    print_progress(f"已从原始 txt 匹配到已有拆分目录：{source_root}")
                    print_progress(f"已从原始 txt 匹配到已有工程目录：{project_root}")
                    print_progress("统一工作流将直接续跑已有工程，不再重复执行 split_novel。")
                else:
                    source_root = run_split_stage(workflow_entry)
            elif input_kind == INPUT_SPLIT_ROOT:
                source_root = workflow_entry
                print_progress(f"已识别为 split_novel 书名目录：{source_root}")
            elif input_kind == INPUT_PROJECT_ROOT:
                project_root = workflow_entry
                manifest = adaptation_workflow.load_manifest(project_root)
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

            adaptation_backlog_volumes: list[str] = []
            if project_root is not None and not args.skip_adaptation:
                adaptation_backlog_volumes = pending_adaptation_volumes(project_root)

            workflow_scope = resolve_workflow_scope(args, adaptation_backlog_volumes, rewrite_backlog_volumes)
            effective_skip_adaptation, effective_skip_rewrite = effective_stage_skips(args, workflow_scope)
            if workflow_scope == WORKFLOW_SCOPE_CONTINUE_ADAPTATION and adaptation_backlog_volumes:
                print_progress(
                    "统一工作流将续跑资料适配断点，当前轮次暂时不进入章节重写："
                    + "、".join(adaptation_backlog_volumes)
                )
            elif workflow_scope == WORKFLOW_SCOPE_CONTINUE_INTERRUPTED and rewrite_backlog_volumes:
                if not should_prompt_interrupted_workflow(args, adaptation_backlog_volumes, rewrite_backlog_volumes):
                    print_progress(
                        "检测到已有已适配但未完成重写的卷："
                        + "、".join(rewrite_backlog_volumes)
                    )
                print_progress("统一工作流将优先续跑章节重写，当前轮次暂时跳过继续处理下一卷适配。")
            elif workflow_scope == WORKFLOW_SCOPE_ADAPTATION_ONLY:
                print_progress("本轮统一工作流只运行资料适配阶段。")
            elif workflow_scope == WORKFLOW_SCOPE_REWRITE_ONLY:
                print_progress("本轮统一工作流只运行章节重写阶段。")
            elif workflow_scope == WORKFLOW_SCOPE_FULL and rewrite_backlog_volumes:
                print_progress("本轮统一工作流将按完整流程运行，不自动跳过资料适配。")

            adaptation_enabled = not effective_skip_adaptation
            adaptation_run_mode = resolve_adaptation_run_mode(args) if adaptation_enabled else ""
            rewrite_run_mode = resolve_rewrite_run_mode(args) if not effective_skip_rewrite else ""
            adaptation_workflow_controlled = (
                adaptation_enabled
                and not effective_skip_rewrite
                and adaptation_run_mode == adaptation_workflow.RUN_MODE_STAGE
            )
            rewrite_workflow_controlled = adaptation_workflow_controlled
            adapted_volume_number: str | None = None

            if adaptation_enabled:
                adaptation_input = project_root or source_root
                assert adaptation_input is not None
                run_python_workflow(
                    "novel_adaptation.py",
                    build_adaptation_workflow_args(
                        args,
                        input_root=adaptation_input,
                        run_mode=adaptation_run_mode,
                        workflow_controlled=adaptation_workflow_controlled,
                    ),
                )
                assert source_root is not None
                project_root = resolve_project_root_for_source(source_root, args.project_root)
                manifest = adaptation_workflow.load_manifest(project_root)
                adapted_volume_number = str((manifest or {}).get("last_processed_volume") or "").strip() or None
                print_progress(f"novel_adaptation 完成后工程目录：{project_root}")
                if adapted_volume_number:
                    print_progress(f"本轮统一工作流已完成适配卷：{adapted_volume_number}")

            if not effective_skip_rewrite:
                if project_root is None:
                    assert source_root is not None
                    project_root = resolve_project_root_for_source(source_root, args.project_root)
                rewrite_volume_override = resolve_rewrite_volume_override(
                    args,
                    adapted_volume_number=adapted_volume_number,
                    rewrite_backlog_volumes=rewrite_backlog_volumes,
                )
                run_python_workflow(
                    "novel_chapter_rewrite.py",
                    build_rewrite_workflow_args(
                        args,
                        project_root=project_root,
                        run_mode=rewrite_run_mode,
                        workflow_controlled=rewrite_workflow_controlled,
                        volume_override=rewrite_volume_override,
                    ),
                )

            if effective_skip_adaptation and effective_skip_rewrite and input_kind != INPUT_RAW_TEXT:
                print_progress("未启用 adaptation / rewrite 阶段，本次没有更多可执行步骤。")

            print_progress("统一工作流执行完成。")
            if args.input_path or not sys.stdin or not sys.stdin.isatty():
                return 0
            next_startup_mode = prompt_next_startup_mode()
            if next_startup_mode is None:
                return 0
        except KeyboardInterrupt:
            print_progress("已取消。", error=True)
            if args.input_path:
                return 1
            if not sys.stdin or not sys.stdin.isatty():
                pause_before_exit()
                return 1
            next_startup_mode = prompt_next_startup_mode(after_error=True)
            if next_startup_mode is None:
                return 1
        except Exception as error:
            print_progress(f"统一工作流处理失败：{error}", error=True)
            if args.input_path:
                return 1
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

__all__ = [
    'build_adaptation_workflow_args',
    'build_rewrite_workflow_args',
    'run_python_workflow',
    'main',
]
