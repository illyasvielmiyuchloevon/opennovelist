from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def render_dry_run_summary(
    manifest: dict[str, Any],
    target_volume: Path,
    volume_material: dict[str, Any],
    run_mode: str,
) -> None:
    project_root = Path(manifest["project_root"])
    paths = stage_paths(project_root, target_volume.name)
    _, source_char_count = build_volume_source_bundle(volume_material)
    plan = build_document_plan(target_volume.name)
    print(f"工程目录：{project_root}")
    print(f"全局注入目录：{paths['global_dir']}")
    print(f"待处理卷：{target_volume.name}")
    print(f"卷级注入目录：{paths['volume_dir']}")
    print(f"章节数：{len(volume_material['chapters'])}")
    print(f"补充资料数：{len(volume_material['extras'])}")
    print(f"总字符数：{source_char_count}")
    print(
        f"请求模式：资料生成 agent 会话覆盖 {len(plan)} 个目标文档，随后进入卷资料审核；"
        "生成和审核阶段都使用 OpenCode 风格本地 transcript，多轮工具调用会重发本阶段上下文与工具历史。"
    )
    print(f"运行方式：{RUN_MODE_LABELS.get(run_mode, run_mode)}")
    print("本次 dry-run 不调用 API，也不会生成文档正文。")

def main() -> int:
    args = parse_args()
    global_config = openai_config.load_global_config(GLOBAL_CONFIG_PATH, legacy_path=LEGACY_GLOBAL_CONFIG_PATH)
    manifest: dict[str, Any] | None = None
    volume_material: dict[str, Any] | None = None
    target_volume: Path | None = None
    planned_calls = 0
    client: OpenAI | None = None
    openai_settings: dict[str, str] | None = None
    run_mode = RUN_MODE_STAGE
    generated_documents: list[dict[str, Any]] = []
    previous_response_id: str | None = None

    try:
        print_progress("开始解析参考源目录。")
        source_root, existing_project_root, existing_manifest = resolve_input_root(
            args.source_root,
            global_config,
        )
        volume_dirs = discover_volume_dirs(source_root)
        manifest = init_or_load_project(
            args,
            source_root,
            volume_dirs,
            global_config,
            existing_project_root=existing_project_root,
            existing_manifest=existing_manifest,
        )
        global_config = openai_config.update_global_config(
            GLOBAL_CONFIG_PATH,
            global_config,
            {
                "last_input_root": str(existing_project_root or source_root),
                "last_source_root": str(source_root),
                "last_project_root": manifest["project_root"],
                "last_new_book_title": manifest["new_book_title"],
            },
        )
        run_mode = resolve_run_mode(args)
        print_progress(f"工程目录：{manifest['project_root']}")
        print_progress(f"参考源目录：{source_root}")
        print_progress(f"本次运行方式：{RUN_MODE_LABELS.get(run_mode, run_mode)}")
        if existing_manifest is not None or existing_project_root is not None:
            print_progress("已加载已有工程配置，将直接继续上次进度。")

        requested_volume = args.volume
        first_target_volume = select_volume_to_process(volume_dirs, manifest, requested_volume)
        if first_target_volume is None:
            print_progress("所有卷都已处理完成，没有新的卷需要生成。")
            return 0

        migration_warnings = ensure_project_dirs(Path(manifest["project_root"]))
        for warning in migration_warnings:
            print_progress(warning, error=True)
        if args.dry_run:
            print_progress(f"本次准备处理第 {first_target_volume.name} 卷。")
            volume_material = load_volume_material(first_target_volume)
            render_dry_run_summary(manifest, first_target_volume, volume_material, run_mode)
            return 0

        print_progress("开始准备 API 客户端。")
        api_key, global_config = openai_config.resolve_api_key(
            cli_api_key=args.api_key,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
        )
        openai_settings, global_config = openai_config.resolve_openai_settings(
            cli_base_url=args.base_url,
            cli_model=args.model,
            global_config=global_config,
            config_path=GLOBAL_CONFIG_PATH,
            legacy_settings=manifest.get("openai") if isinstance(manifest, dict) else None,
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

        while True:
            target_volume = select_volume_to_process(volume_dirs, manifest, requested_volume)
            requested_volume = None
            if target_volume is None:
                print_progress("所有卷都已处理完成，没有新的卷需要生成。")
                return 0

            print_progress(f"本次准备处理第 {target_volume.name} 卷。")
            volume_material = load_volume_material(target_volume)
            source_bundle, source_char_count = build_volume_source_bundle(volume_material)
            loaded_files = build_loaded_file_inventory(volume_material)
            document_plan = build_document_plan(volume_material["volume_number"])
            planned_calls = len(document_plan)
            paths = stage_paths(Path(manifest["project_root"]), volume_material["volume_number"])
            resume_state = load_document_generation_resume_state(
                paths,
                document_plan,
                manifest=manifest,
                volume_number=volume_material["volume_number"],
            )
            resumed_completed_keys = set(resume_state["completed_keys"])
            generated_documents = list(resume_state["generated_documents"])
            previous_response_id = resume_state["last_response_id"]
            write_source_inventory_snapshot(
                manifest,
                volume_material,
                note="已完成当前卷全部源文件扫描，资料生成 agent 会话会携带当前卷全部文件原文与文件清单。",
                total_batches=planned_calls,
            )
            write_stage_status_snapshot(
                manifest,
                volume_material,
                status="stage_session_started",
                note="已读取当前卷全部文件，准备进入资料生成 agent 会话；本阶段会话会附带整卷内容与全部目标文档清单。",
                total_batches=planned_calls,
                current_batch=1,
                current_batch_range=document_plan[0]["key"],
                generated_documents=generated_documents,
                previous_response_id=previous_response_id,
            )
            if resumed_completed_keys:
                resumed_labels = [
                    str(item["label"])
                    for item in document_plan
                    if str(item["key"]) in resumed_completed_keys
                ]
                resume_source = str(resume_state.get("resume_source") or "stage_manifest")
                if resume_source == "file_mtime_prefix":
                    print_progress("未在阶段清单中找到完整断点，已根据当前卷文件更新时间恢复连续完成前缀。")
                print_progress(
                    "检测到当前卷已有已完成资料文档，将跳过重新生成："
                    + "、".join(resumed_labels)
                )
            print_progress(
                f"已加载 {volume_material['volume_number']} 卷全部文件："
                f"{len(volume_material['chapters'])} 个章节文件，"
                f"{len(volume_material['extras'])} 个补充文件，"
                f"总字符数约 {source_char_count}。"
            )
            print_progress(
                f"本阶段将使用 agent 生成模式：一个会话覆盖 {planned_calls} 个目标资料文档，"
                f"会话会携带当前卷全部文件原文，共加载 {len(loaded_files)} 个文件；AI 可多轮调用工具完成落盘。"
            )
            print_progress("本阶段已启用稳定共享前缀，提示词缓存将复用：项目上下文、阶段规则、文件清单与整卷原文。")
            existing_docs = read_existing_global_docs(Path(manifest["project_root"]))
            current_docs = dict(existing_docs)
            prompt_cache_key = build_phase_session_key(manifest, volume_material["volume_number"])
            stage_shared_prompt = build_stage_shared_prompt(
                manifest=manifest,
                volume_material=volume_material,
                loaded_files=loaded_files,
                source_bundle=source_bundle,
                source_char_count=source_char_count,
            )

            write_stage_status_snapshot(
                manifest,
                volume_material,
                status="generating_document",
                note="正在以 agent 模式生成当前卷全部资料文档。",
                total_batches=1,
                current_batch=1,
                current_batch_range="adaptation_generation_agent",
                generated_documents=generated_documents,
                previous_response_id=previous_response_id,
            )
            print_progress("资料生成 agent 会话：处理当前卷全部目标资料文档，允许多轮工具调用。")
            generated_documents, previous_response_id = run_adaptation_generation_agent(
                client=client,
                model=openai_settings["model"],
                manifest=manifest,
                volume_material=volume_material,
                paths=paths,
                document_plan=document_plan,
                current_docs=current_docs,
                stage_shared_prompt=stage_shared_prompt,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
            )
            write_stage_status_snapshot(
                manifest,
                volume_material,
                status="document_generated",
                note="资料生成 agent 已完成全部目标文档，断点已保存。",
                total_batches=1,
                current_batch=1,
                current_batch_range="adaptation_generation_agent",
                generated_documents=generated_documents,
                previous_response_id=previous_response_id,
            )

            print_progress("本阶段文档生成完成，开始更新阶段索引文件并进入卷资料审核。")
            paths = write_stage_outputs(
                manifest=manifest,
                volume_material=volume_material,
                generated_documents=generated_documents,
                source_char_count=source_char_count,
                loaded_file_count=len(loaded_files),
            )
            review_result, previous_response_id = run_adaptation_review_until_passed(
                client=client,
                model=openai_settings["model"],
                manifest=manifest,
                volume_material=volume_material,
                stage_shared_prompt=stage_shared_prompt,
                previous_response_id=previous_response_id,
                prompt_cache_key=prompt_cache_key,
            )
            paths = mark_volume_processed_after_review(
                manifest,
                volume_material,
                generated_documents=generated_documents,
                source_char_count=source_char_count,
                loaded_file_count=len(loaded_files),
                review_result=review_result,
            )

            print_progress(f"已处理卷：{volume_material['volume_number']}")
            print_progress(f"工程目录：{manifest['project_root']}")
            print_progress(f"全局注入目录：{paths['global_dir']}")
            print_progress(f"卷级注入目录：{paths['volume_dir']}")
            print_progress(f"全书大纲：{paths['book_outline']}")
            print_progress(f"世界模型：{paths['world_model']}")
            if any(item.get("key") == "style_guide" for item in generated_documents):
                print_progress(f"文笔风格：{paths['style_guide']}")
            elif paths["style_guide"].exists():
                print_progress(f"文笔风格：沿用已有文档 {paths['style_guide']}")
            else:
                print_progress("文笔风格：本阶段未生成，当前工程中也暂无现成文档。")
            print_progress(f"伏笔文档：{paths['foreshadowing']}")
            print_progress(f"卷级大纲：{paths['volume_outline']}")
            print_progress(f"卷资料审核：{paths['adaptation_review']}")

            next_volume = find_next_pending_volume_after(
                volume_dirs,
                manifest,
                volume_material["volume_number"],
            )
            if args.workflow_controlled:
                print_progress("当前卷阶段已完成，统一工作流将接管后续调度。")
                return 0
            if run_mode == RUN_MODE_STAGE:
                if not prompt_next_stage(next_volume):
                    return 0
                print_progress(f"准备进入下一阶段：第 {next_volume.name} 卷。")
                requested_volume = next_volume.name
                continue

            if next_volume is None:
                print_progress("当前卷之后没有新的待处理卷可继续了。")
                return 0
            print_progress(f"按全书运行，自动进入下一阶段：第 {next_volume.name} 卷。")
            requested_volume = next_volume.name
    except KeyboardInterrupt:
        print_progress("已取消。", error=True)
        pause_before_exit()
        return 1
    except Exception as error:
        if manifest is not None and volume_material is not None:
            try:
                if isinstance(error, llm_runtime.ModelOutputError) and error.preview:
                    write_response_debug_snapshot(
                        manifest,
                        volume_material,
                        error_message=str(error),
                        preview=error.preview,
                        raw_body_text=getattr(error, "raw_body_text", ""),
                    )
                write_stage_status_snapshot(
                    manifest,
                    volume_material,
                    status="failed",
                    note="阶段执行失败，等待人工排查。",
                    total_batches=planned_calls or None,
                    error_message=str(error),
                    generated_documents=generated_documents,
                    previous_response_id=previous_response_id,
                )
            except Exception:
                pass
        print_progress(f"处理失败：{error}", error=True)
        pause_before_exit()
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

__all__ = [
    'render_dry_run_summary',
    'main',
]
