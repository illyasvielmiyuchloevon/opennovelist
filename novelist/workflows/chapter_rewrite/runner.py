from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def render_dry_run_summary(
    rewrite_manifest: dict[str, Any],
    readiness_map: dict[str, dict[str, Any]],
    target_volume: Path | None,
    target_chapter: str | None,
    run_mode: str,
) -> None:
    print(f"工程目录：{rewrite_manifest['project_root']}")
    print(f"重写输出目录：{rewrite_manifest['rewrite_output_root']}")
    print(f"已处理完成的卷：{', '.join(rewrite_manifest.get('processed_volumes', [])) or 'none'}")
    print("卷检测结果：")
    for volume_number in sorted(readiness_map):
        info = readiness_map[volume_number]
        status = "ready" if info["eligible"] else "blocked"
        print(f"  - {volume_number}: {status}")
        for reason in info["missing"]:
            print(f"      * {reason}")
    if target_volume is not None:
        print(f"本次准备处理卷：{target_volume.name}")
    if target_chapter is not None:
        print(f"本次准备处理章：{target_chapter}")
    print(f"本次运行模式：{run_mode}")
    print("本次 dry-run 不调用 API，也不会生成正文。")

def main() -> int:
    args = parse_args()
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH, legacy_path=LEGACY_GLOBAL_CONFIG_PATH)

    try:
        print_progress("开始识别小说工程目录。")
        project_root, source_root, project_manifest = resolve_project_input(args.input_root, global_config)
        migration_warnings = ensure_rewrite_dirs(project_root)
        for warning in migration_warnings:
            print_progress(warning, error=True)
        volume_dirs = discover_volume_dirs(source_root)
        readiness_map = {
            volume_dir.name: assess_volume_readiness(project_root, source_root, volume_dir.name)
            for volume_dir in volume_dirs
        }
        print_volume_readiness_summary(readiness_map)

        rewrite_manifest = init_or_load_rewrite_manifest(project_root, source_root, project_manifest, volume_dirs)
        run_mode = resolve_run_mode(args)
        global_config = openai_config.update_global_config(
            GLOBAL_CONFIG_PATH,
            global_config,
            {
                "last_chapter_rewrite_input_root": str(project_root),
                "last_project_root": str(project_root),
                "last_source_root": str(source_root),
            },
        )

        target_volume = select_volume_to_process(volume_dirs, rewrite_manifest, readiness_map, args.volume)
        target_chapter = args.chapter.zfill(4) if args.chapter else None
        if args.dry_run:
            render_dry_run_summary(rewrite_manifest, readiness_map, target_volume, target_chapter, run_mode)
            return 0

        if target_volume is None:
            print_progress("当前没有可进入章节工作流的卷。")
            return 0

        print_progress(f"本次运行模式：{RUN_MODE_LABELS.get(run_mode, run_mode)}")
        print_progress("开始准备 API 客户端。")
        api_key, global_config = openai_config.resolve_api_key(
            cli_api_key=args.api_key,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
        )
        openai_settings, _ = openai_config.resolve_openai_settings(
            cli_base_url=args.base_url,
            cli_model=args.model,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
        )
        print_progress(f"本次使用 base_url：{openai_settings['base_url']}")
        print_progress(f"本次使用模型：{openai_settings['model']}")
        print_progress(f"本次使用协议：{openai_settings.get('protocol', 'responses')}")
        client = openai_config.create_openai_client(
            api_key=api_key,
            base_url=openai_settings["base_url"],
            protocol=openai_settings.get("protocol", openai_config.PROTOCOL_RESPONSES),
            provider=openai_settings.get("provider", openai_config.PROVIDER_OPENAI),
        )

        requested_volume = target_volume.name
        requested_chapter = target_chapter

        while True:
            current_volume = select_volume_to_process(volume_dirs, rewrite_manifest, readiness_map, requested_volume)
            requested_volume = None
            if current_volume is None:
                print_progress("当前没有新的可处理卷。")
                return 0

            volume_material = load_volume_material(current_volume)
            print_progress(
                f"已加载第 {current_volume.name} 卷："
                f"{len(volume_material['chapters'])} 个章节文件，"
                f"{len(volume_material['extras'])} 个补充文件。"
            )
            completed_scope, next_target = process_volume_workflow(
                client=client,
                model=openai_settings["model"],
                rewrite_manifest=rewrite_manifest,
                volume_material=volume_material,
                run_mode=run_mode,
                requested_chapter=requested_chapter,
            )
            requested_chapter = None
            if args.workflow_controlled:
                print_progress("当前重写范围已完成，统一工作流将接管后续调度。")
                return 0

            if completed_scope == "chapter":
                if next_target is not None:
                    if not prompt_next_chapter(next_target):
                        return 0
                    requested_volume = current_volume.name
                    requested_chapter = next_target
                    continue
                next_volume = find_next_volume_after(volume_dirs, current_volume.name, readiness_map)
                if not prompt_continue_same_mode_next_volume(run_mode, next_volume):
                    return 0
                if next_volume is None:
                    return 0
                requested_volume = next_volume.name
                requested_chapter = None
                continue

            if completed_scope == "group":
                if next_target is not None:
                    if not prompt_next_group(next_target):
                        return 0
                    requested_volume = current_volume.name
                    requested_chapter = next_target[0]
                    continue
                next_volume = find_next_volume_after(volume_dirs, current_volume.name, readiness_map)
                if not prompt_continue_same_mode_next_volume(run_mode, next_volume):
                    return 0
                if next_volume is None:
                    return 0
                requested_volume = next_volume.name
                requested_chapter = None
                continue

            next_volume = select_volume_to_process(volume_dirs, rewrite_manifest, readiness_map, None)
            if not prompt_next_volume(next_volume):
                return 0
            if next_volume is None:
                return 0
            requested_volume = next_volume.name
    except KeyboardInterrupt:
        print_progress("已取消。", error=True)
        pause_before_exit()
        return 1
    except Exception as error:
        print_progress(f"处理失败：{error}", error=True)
        pause_before_exit()
        return 1

__all__ = [
    'render_dry_run_summary',
    'main',
]
