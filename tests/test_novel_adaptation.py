from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from novelist.workflows import novel_adaptation as adaptation_workflow


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest(project_root: Path) -> dict[str, object]:
    return {
        "version": 1,
        "source_root": str(project_root / "source"),
        "project_root": str(project_root),
        "new_book_title": "仙道至尊",
        "target_worldview": "玄幻世界观",
        "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
        "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
        "total_volumes": 2,
        "processed_volumes": [],
        "last_processed_volume": None,
    }


def _volume_material(volume_number: str = "001") -> dict[str, object]:
    return {
        "volume_number": volume_number,
        "volume_dir": f"F:/source/{volume_number}",
        "chapters": [],
        "extras": [],
    }


def _seed_adaptation_docs(project_root: Path, volume_number: str = "001") -> dict[str, Path]:
    paths = adaptation_workflow.stage_paths(project_root, volume_number)
    for doc_key in adaptation_workflow.GLOBAL_INJECTION_DOC_ORDER:
        _write_text(paths[doc_key], f"{adaptation_workflow.adaptation_doc_label(doc_key)}：源人物名仍残留。\n")
    _write_text(paths["volume_outline"], "卷级大纲：源人物名仍残留。\n")
    return paths


class AdaptationDocumentPlanTests(unittest.TestCase):
    def test_first_volume_plan_uses_new_generation_order(self) -> None:
        plan = adaptation_workflow.build_document_plan("001")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
                "storyline_blueprint",
                "volume_outline",
            ],
        )

    def test_later_volume_plan_uses_new_generation_order(self) -> None:
        plan = adaptation_workflow.build_document_plan("002")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "world_model",
                "book_outline",
                "foreshadowing",
                "storyline_blueprint",
                "volume_outline",
            ],
        )


class SourceContaminationGuardrailTests(unittest.TestCase):
    def test_common_stage_instructions_forbid_source_names_and_discourse_system(self) -> None:
        instructions = adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS

        self.assertIn("严禁把参考源的人名、地名、姓氏、势力名、事件名、专用术语", instructions)
        self.assertIn("话语体系", instructions)
        self.assertIn("转换成新书自己的命名、设定与表达", instructions)

    def test_stage_shared_prompt_contains_source_contamination_guardrails(self) -> None:
        prompt = adaptation_workflow.build_stage_shared_prompt(
            manifest=_manifest(Path("F:/project")),
            volume_material=_volume_material("001"),
            loaded_files=[],
            source_bundle="",
            source_char_count=0,
        )

        self.assertIn("严禁把参考源的人名", prompt)
        self.assertIn("话语体系", prompt)
        self.assertIn("功能映射", prompt)

    def test_document_request_contains_explicit_source_material_boundary(self) -> None:
        for doc_key in [
            "world_model",
            "style_guide",
            "book_outline",
            "foreshadowing",
            "storyline_blueprint",
            "volume_outline",
        ]:
            with self.subTest(doc_key=doc_key):
                request = adaptation_workflow.build_document_request(doc_key)
                boundary = request["source_material_boundary"]
                self.assertIn("参考源只提供", boundary["core_boundary"])
                self.assertIn("参考源功能 -> 新书设计", boundary["mapping_required"])
                self.assertIn("不得把原书人物", "\n".join(boundary["hard_ban"]))
                self.assertIn("新书设定主体必须使用新书自己的姓名", "\n".join(boundary["required_conversion"]))

    def test_every_adaptation_document_generation_payload_has_source_contamination_guardrails(self) -> None:
        captured: dict[str, dict[str, object]] = {}

        def fake_call(*args, **kwargs):
            payload = adaptation_workflow.json.loads(str(args[3]))
            captured[payload["document_request"]["doc_key"]] = payload
            return (object(), None)

        manifest = {
            "new_book_title": "测试书",
            "target_worldview": "测试世界",
            "total_volumes": 2,
            "processed_volumes": ["001"],
            "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
            "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
        }
        current_docs = {
            "style_guide": "文风",
            "book_outline": "全书大纲",
            "world_design": "世界观设计",
            "world_model": "世界模型",
            "storyline_blueprint": "全书故事线蓝图",
            "foreshadowing": "伏笔管理",
        }

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            for doc_key in [
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
                "storyline_blueprint",
                "volume_outline",
            ]:
                adaptation_workflow.generate_document_operation(
                    client=None,  # type: ignore[arg-type]
                    model="gpt-test",
                    manifest=manifest,
                    volume_material={"volume_number": "002", "chapters": [], "extras": []},
                    current_docs=current_docs,
                    doc_key=doc_key,
                    output_path=Path(f"F:/novelist/.tmp_{doc_key}_test.md"),
                    stage_shared_prompt="",
                    previous_response_id=None,
                    prompt_cache_key="test-cache-key",
                )

        self.assertEqual(
            set(captured),
            {"world_model", "style_guide", "book_outline", "foreshadowing", "storyline_blueprint", "volume_outline"},
        )
        for doc_key, payload in captured.items():
            requirements = "\n".join(payload["requirements"])
            boundary_text = adaptation_workflow.json.dumps(payload["source_material_boundary"], ensure_ascii=False)
            with self.subTest(doc_key=doc_key):
                self.assertIn("参考源只提供", boundary_text)
                self.assertIn("不是新书资料正文", boundary_text)
                self.assertIn("参考源功能 -> 新书设计", boundary_text)
                self.assertIn("不得把原书人物", boundary_text)
                self.assertIn("严禁把参考源的人名", requirements)
                self.assertIn("绝不能把原书内容直接写成新书内容", requirements)
                self.assertIn("势力名", requirements)
                self.assertIn("等级名", requirements)
                self.assertIn("话语体系", requirements)
                self.assertIn("新命名、新术语和新表达", requirements)
                self.assertIn("不得把原作实体名保留为新书实体", requirements)
                self.assertIn("残留参考源实体名或话语体系", requirements)


class WorldModelDefinitionTests(unittest.TestCase):
    def test_world_model_default_sections_has_sixteen_entries(self) -> None:
        self.assertEqual(len(adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS), 16)
        self.assertEqual(adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS[-1], "可扩展世界专题")

    def test_world_model_scope_text_mentions_expansion_section(self) -> None:
        scope = adaptation_workflow.world_model_scope_text()
        self.assertIn("可扩展世界专题", scope)
        self.assertIn("16 个二级标题", scope)
        self.assertIn("多个三级标题", scope)
        self.assertIn("世界观设计", scope)
        self.assertIn("背景故事", scope)
        self.assertIn("角色功能位", scope)
        self.assertIn("不要另建或依赖独立世界观设计文档", scope)


class StorylineBlueprintDefinitionTests(unittest.TestCase):
    def test_storyline_blueprint_scope_mentions_storyline_owned_structure(self) -> None:
        scope = adaptation_workflow.storyline_blueprint_scope_text()
        self.assertIn("全书故事线蓝图", scope)
        self.assertIn("故事线为 owner", scope)
        self.assertIn("二级标题", scope)
        self.assertIn("三级标题", scope)
        self.assertIn("分卷蓝图", scope)
        self.assertIn("待后续补全", scope)
        self.assertNotIn("待推进", scope)
        self.assertNotIn("当前状态", scope)

    def test_storyline_blueprint_file_number_is_six(self) -> None:
        self.assertEqual(adaptation_workflow.GLOBAL_FILE_NAMES["storyline_blueprint"], "06_storyline_blueprint.md")

    def test_storyline_blueprint_request_definition_is_blueprint_planning(self) -> None:
        request = adaptation_workflow.build_document_request("storyline_blueprint")
        self.assertIn("全书故事线蓝图文档", request["task"])
        self.assertIn("三级标题", request["scope"])
        self.assertNotIn("全书故事线规划", request["task"])


class ForeshadowingDefinitionTests(unittest.TestCase):
    def test_foreshadowing_request_is_design_index_not_runtime_status(self) -> None:
        request = adaptation_workflow.build_document_request("foreshadowing")
        scope = request["scope"]

        self.assertIn("伏笔设计索引", scope)
        self.assertIn("参考源功能映射", scope)
        self.assertIn("新书伏笔设计", scope)
        self.assertIn("埋设意图", scope)
        self.assertIn("后续呼应方向", scope)
        self.assertIn("不要判断伏笔是否已经推进或兑现", scope)
        self.assertIn("受保护内容", scope)
        self.assertIn("原样保留", scope)
        for forbidden in ("待埋设", "已埋设", "待回收", "已回收", "回收记录", "状态推进"):
            self.assertNotIn(forbidden, scope)

    def test_foreshadowing_generation_payload_keeps_adaptation_scope(self) -> None:
        captured: dict[str, object] = {}

        def fake_call(*args, **kwargs):
            captured["user_input"] = args[3]
            return (object(), None)

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            adaptation_workflow.generate_document_operation(
                client=None,  # type: ignore[arg-type]
                model="gpt-test",
                manifest={
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界",
                    "total_volumes": 2,
                    "processed_volumes": ["001"],
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                },
                volume_material={"volume_number": "002", "chapters": [], "extras": []},
                current_docs={
                    "foreshadowing": "# 伏笔设计索引\n\n## 伏笔：旧设计\n",
                    "book_outline": "全书大纲",
                    "world_design": "世界观设计",
                    "world_model": "世界模型",
                    "storyline_blueprint": "全书故事线蓝图",
                },
                doc_key="foreshadowing",
                output_path=Path("F:/novelist/.tmp_foreshadowing_test.md"),
                stage_shared_prompt="",
                previous_response_id=None,
                prompt_cache_key="test-cache-key",
            )

        payload = adaptation_workflow.json.loads(str(captured["user_input"]))
        requirements = "\n".join(payload["requirements"])
        self.assertEqual(payload["required_file"], "05_foreshadowing.md")
        self.assertEqual(payload["target_file"]["preferred_mode"], "edit_or_patch")
        self.assertIn("伏笔设计索引", requirements)
        self.assertIn("参考源承担的功能", requirements)
        self.assertIn("新书对应设计", requirements)
        self.assertIn("后续呼应方向", requirements)
        self.assertIn("资料适配阶段只做设计索引", requirements)
        self.assertIn("运行时记录", requirements)
        self.assertIn("不得删除、压缩、归并、重命名", requirements)
        for forbidden in ("待埋设", "已埋设", "待回收", "已回收", "回收记录", "状态推进"):
            self.assertNotIn(forbidden, requirements)

    def test_stage_shared_prompt_keeps_foreshadowing_as_design_index(self) -> None:
        prompt = adaptation_workflow.build_stage_shared_prompt(
            manifest=_manifest(Path("F:/project")),
            volume_material=_volume_material("001"),
            loaded_files=[],
            source_bundle="",
            source_char_count=0,
        )

        self.assertIn("伏笔设计索引", prompt)
        self.assertIn("设计意图、功能映射和后续呼应方向", prompt)
        self.assertIn("已有运行时记录必须原样保留", prompt)
        for forbidden in ("待埋设", "已埋设", "待回收", "已回收", "回收记录", "状态推进"):
            self.assertNotIn(forbidden, prompt)


class AdaptationInjectionOrderTests(unittest.TestCase):
    def test_build_injected_global_docs_uses_requested_generation_order(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "book_outline": "全书大纲",
                "world_design": "世界观设计",
                "style_guide": "文笔写作风格",
                "world_model": "世界模型",
                "storyline_blueprint": "全书故事线蓝图",
                "foreshadowing": "伏笔管理",
            }
        )
        self.assertEqual(
            list(injected.keys()),
            [
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
                "storyline_blueprint",
            ],
        )

    def test_build_injected_global_docs_excludes_current_target_document(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "book_outline": "全书大纲",
                "world_design": "世界观设计",
                "style_guide": "文笔写作风格",
                "world_model": "世界模型",
                "storyline_blueprint": "全书故事线蓝图",
                "foreshadowing": "伏笔管理",
            },
            exclude_keys={"style_guide"},
        )
        self.assertNotIn("style_guide", injected)

    def test_build_injected_global_docs_uses_per_document_context_budget(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "foreshadowing": "伏" * 20000,
                "storyline_blueprint": "线" * 20000,
            }
        )

        self.assertLessEqual(len(injected["foreshadowing"]), adaptation_workflow.ADAPTATION_DOC_CONTEXT_LIMITS["foreshadowing"] + 80)
        self.assertLessEqual(len(injected["storyline_blueprint"]), adaptation_workflow.ADAPTATION_DOC_CONTEXT_LIMITS["storyline_blueprint"] + 80)

    def test_style_guide_generation_does_not_duplicate_existing_content(self) -> None:
        captured: dict[str, object] = {}

        def fake_call(*args, **kwargs):
            captured["instructions"] = args[2]
            captured["user_input"] = args[3]
            return (object(), None)

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            adaptation_workflow.generate_document_operation(
                client=None,  # type: ignore[arg-type]
                model="gpt-test",
                manifest={
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界",
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                },
                volume_material={"volume_number": "001", "chapters": [], "extras": []},
                current_docs={
                    "style_guide": "已有风格正文",
                    "book_outline": "全书大纲",
                    "world_design": "世界观设计",
                    "world_model": "世界模型",
                    "storyline_blueprint": "全书故事线蓝图",
                    "foreshadowing": "伏笔管理",
                },
                doc_key="style_guide",
                output_path=Path("F:/novelist/.tmp_style_guide_test.md"),
                stage_shared_prompt="",
                previous_response_id=None,
                prompt_cache_key="test-cache-key",
            )

        user_input = captured["user_input"]
        self.assertIsInstance(user_input, str)
        payload = adaptation_workflow.json.loads(str(user_input))
        self.assertIn("target_file", payload)
        self.assertEqual(payload["target_file"]["preferred_mode"], "edit_or_patch")
        self.assertIn("按修改意图选择工具", payload["target_file"]["tool_selection_policy"])
        self.assertNotIn("existing_style_guide", payload)
        self.assertNotIn("style_guide", payload["injected_global_docs"])

    def test_storyline_blueprint_generation_keeps_global_blueprint_compact(self) -> None:
        captured: dict[str, object] = {}

        def fake_call(*args, **kwargs):
            captured["user_input"] = args[3]
            return (object(), None)

        with patch.object(adaptation_workflow, "call_document_operation_response", side_effect=fake_call):
            adaptation_workflow.generate_document_operation(
                client=None,  # type: ignore[arg-type]
                model="gpt-test",
                manifest={
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界",
                    "total_volumes": 2,
                    "processed_volumes": ["001"],
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                },
                volume_material={"volume_number": "002", "chapters": [], "extras": []},
                current_docs={
                    "storyline_blueprint": "# 全书故事线蓝图\n\n## 故事线：主线\n### 分卷蓝图\n#### 第001卷\n- 既有蓝图。",
                    "book_outline": "全书大纲",
                    "world_design": "世界观设计",
                    "world_model": "世界模型",
                    "foreshadowing": "伏笔管理",
                },
                doc_key="storyline_blueprint",
                output_path=Path("F:/novelist/.tmp_storyline_blueprint_test.md"),
                stage_shared_prompt="",
                previous_response_id=None,
                prompt_cache_key="test-cache-key",
            )

        payload = adaptation_workflow.json.loads(str(captured["user_input"]))
        requirements = "\n".join(payload["requirements"])
        self.assertEqual(payload["required_file"], "06_storyline_blueprint.md")
        self.assertEqual(payload["target_file"]["preferred_mode"], "edit_or_patch")
        self.assertIn("该卷在全书故事线中的功能", requirements)
        self.assertIn("已有卷级设计压缩到信息缺失", requirements)
        self.assertIn("严禁在全书故事线蓝图中记录章内事件", requirements)
        self.assertIn("重复", requirements)
        self.assertNotIn("卷级锚点摘要", requirements)
        self.assertNotIn("6000-12000", requirements)
        self.assertNotIn("当前状态", requirements)
        self.assertNotIn("待推进", requirements)


class AdaptationVolumeReviewTests(unittest.TestCase):
    def test_adaptation_review_targets_recommend_tools_by_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = _seed_adaptation_docs(project_root, "001")

            snapshot = adaptation_workflow.adaptation_review_target_snapshot(
                {"world_model": paths["world_model"]}
            )

        self.assertEqual(snapshot[0]["preferred_mode"], "edit_or_patch")
        self.assertIn("替换已有内容", snapshot[0]["tool_selection_policy"])
        self.assertIn("按 Markdown 标题", snapshot[0]["tool_selection_policy"])

    def test_apply_document_operation_with_repair_retries_bad_generation_old_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            paths["world_model"].write_text("第一段。\n\n第二段原文。\n", encoding="utf-8")
            failed_operation = adaptation_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_bad",
                status="completed",
                output_types=["function_call"],
                preview="bad edit",
                raw_body_text="",
                raw_json={},
                edit_payload=adaptation_workflow.document_ops.DocumentEditPayload(
                    files=[
                        adaptation_workflow.document_ops.DocumentEditFile(
                            file_key="world_model",
                            edits=[
                                adaptation_workflow.document_ops.DocumentEditEdit(
                                    old_text="第二段模型误写。",
                                    new_text="第二段修订后。",
                                )
                            ],
                        )
                    ]
                ),
            )
            repaired_operation = adaptation_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_repair",
                status="completed",
                output_types=["function_call"],
                preview="fixed edit",
                raw_body_text="",
                raw_json={},
                edit_payload=adaptation_workflow.document_ops.DocumentEditPayload(
                    files=[
                        adaptation_workflow.document_ops.DocumentEditFile(
                            file_key="world_model",
                            edits=[
                                adaptation_workflow.document_ops.DocumentEditEdit(
                                    old_text="第二段原文。",
                                    new_text="第二段修订后。",
                                )
                            ],
                        )
                    ]
                ),
            )

            with patch.object(
                adaptation_workflow.document_ops,
                "call_document_operation_tools",
                return_value=repaired_operation,
            ) as call_tools:
                applied, response_id, repair_response_ids = adaptation_workflow.apply_document_operation_with_repair(
                    client=Mock(),
                    model="test-model",
                    instructions=adaptation_workflow.COMMON_STAGE_DOCUMENT_INSTRUCTIONS,
                    shared_prompt="shared prompt\n",
                    operation=failed_operation,
                    allowed_files={"world_model": paths["world_model"]},
                    previous_response_id="resp_bad",
                    prompt_cache_key="cache-key",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                    repair_phase_key="adaptation_stage_document_locator_repair",
                    repair_role="资深网络小说改编规划编辑",
                    repair_task="修正定位文本。",
                )

            self.assertEqual(response_id, "resp_repair")
            self.assertEqual(repair_response_ids, ["resp_repair"])
            self.assertEqual(applied.changed_keys, ["world_model"])
            self.assertIn("第二段修订后。", paths["world_model"].read_text(encoding="utf-8"))
            call_tools.assert_called_once()
            repair_input = call_tools.call_args.kwargs["user_input"]
            self.assertIn("第二段模型误写。", repair_input)
            self.assertIn("第二段原文。", repair_input)
            self.assertIn("逐字复制", repair_input)

    def test_generation_resume_state_uses_current_batch_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = _seed_adaptation_docs(project_root, "002")
            plan = adaptation_workflow.build_document_plan("002")
            adaptation_workflow.write_markdown_data(
                paths["stage_manifest"],
                title="Stage Status 002",
                payload={
                    "status": "generating_document",
                    "processed_volume": "002",
                    "current_batch": 6,
                    "current_batch_range": "volume_outline",
                },
                summary_lines=["status: generating_document"],
            )

            resume_state = adaptation_workflow.load_document_generation_resume_state(paths, plan)

            self.assertEqual(
                resume_state["completed_keys"],
                ["world_model", "book_outline", "foreshadowing", "storyline_blueprint"],
            )
            self.assertEqual(
                [item["key"] for item in resume_state["generated_documents"]],
                ["world_model", "book_outline", "foreshadowing", "storyline_blueprint"],
            )

    def test_generation_resume_state_recovers_corrupted_stage_from_file_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            manifest["processed_volumes"] = ["001"]
            manifest["last_processed_volume"] = "001"
            paths_001 = adaptation_workflow.stage_paths(project_root, "001")
            paths_002 = adaptation_workflow.stage_paths(project_root, "002")
            plan = adaptation_workflow.build_document_plan("002")

            _write_text(paths_001["stage_manifest"], "previous volume completed\n")
            previous_done = 1_700_000_000
            os.utime(paths_001["stage_manifest"], (previous_done, previous_done))

            _write_text(paths_002["world_model"], "第二卷世界模型已生成。\n")
            _write_text(paths_002["book_outline"], "第二卷全书大纲已生成。\n")
            _write_text(paths_002["foreshadowing"], "旧伏笔文档，还不能算本轮完成。\n")
            os.utime(paths_002["world_model"], (previous_done + 1010, previous_done + 1010))
            os.utime(paths_002["book_outline"], (previous_done + 1020, previous_done + 1020))
            os.utime(paths_002["foreshadowing"], (previous_done + 100, previous_done + 100))

            adaptation_workflow.write_markdown_data(
                paths_002["stage_manifest"],
                title="Stage Status 002",
                payload={
                    "status": "generating_document",
                    "processed_volume": "002",
                    "current_batch": 1,
                    "current_batch_range": "world_model",
                    "api_calls": [],
                    "generated_document_keys": [],
                },
                summary_lines=["status: generating_document"],
            )

            resume_state = adaptation_workflow.load_document_generation_resume_state(
                paths_002,
                plan,
                manifest=manifest,  # type: ignore[arg-type]
                volume_number="002",
            )

            self.assertEqual(resume_state["resume_source"], "file_mtime_prefix")
            self.assertEqual(resume_state["completed_keys"], ["world_model", "book_outline"])
            self.assertEqual(
                [item["key"] for item in resume_state["generated_documents"]],
                ["world_model", "book_outline"],
            )
            self.assertTrue(all(item["resumed"] for item in resume_state["generated_documents"]))

    def test_stage_status_snapshot_persists_generated_document_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")

            adaptation_workflow.write_stage_status_snapshot(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                status="document_generated",
                note="世界模型文档已生成，断点已保存。",
                total_batches=6,
                current_batch=1,
                current_batch_range="world_model",
                generated_documents=[
                    {
                        "index": 1,
                        "key": "world_model",
                        "label": "世界模型文档",
                        "response_id": "resp_world",
                        "output_path": str(paths["world_model"]),
                    }
                ],
                previous_response_id="resp_world",
            )

            payload = adaptation_workflow.load_stage_manifest_payload(paths["stage_manifest"])
            self.assertEqual(payload["generated_document_keys"], ["world_model"])
            self.assertEqual(payload["last_response_id"], "resp_world")

    def test_failed_stage_snapshot_can_preserve_document_resume_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            generated_documents = [
                {
                    "index": 1,
                    "key": "world_model",
                    "label": "世界模型文档",
                    "response_id": "resp_world",
                    "output_path": str(paths["world_model"]),
                }
            ]

            adaptation_workflow.write_stage_status_snapshot(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                status="failed",
                note="阶段执行失败，等待人工排查。",
                total_batches=6,
                error_message="接口请求失败",
                generated_documents=generated_documents,
                previous_response_id="resp_world",
            )

            resume_state = adaptation_workflow.load_document_generation_resume_state(
                paths,
                adaptation_workflow.build_document_plan("001"),
            )
            self.assertEqual(resume_state["completed_keys"], ["world_model"])
            self.assertEqual(resume_state["last_response_id"], "resp_world")

    def test_stage_outputs_wait_for_review_before_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")

            adaptation_workflow.write_stage_outputs(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                generated_documents=[{"key": "world_model", "label": "世界模型文档"}],
                source_char_count=123,
                loaded_file_count=1,
            )

            self.assertEqual(manifest["processed_volumes"], [])
            stage_manifest = paths["stage_manifest"].read_text(encoding="utf-8")
            self.assertIn('"status": "review_pending"', stage_manifest)
            self.assertIn("001_adaptation_review.md", stage_manifest)

            review_result = adaptation_workflow.AdaptationReviewResult(
                payload=adaptation_workflow.AdaptationReviewPayload(passed=True, review_md="审核通过。"),
                response_ids=["resp_review"],
                review_path=str(paths["adaptation_review"]),
                fix_attempts=0,
            )
            adaptation_workflow.mark_volume_processed_after_review(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                generated_documents=[{"key": "world_model", "label": "世界模型文档"}],
                source_char_count=123,
                loaded_file_count=1,
                review_result=review_result,
            )

            self.assertEqual(manifest["processed_volumes"], ["001"])
            self.assertEqual(manifest["last_processed_volume"], "001")
            stage_manifest = paths["stage_manifest"].read_text(encoding="utf-8")
            self.assertIn('"status": "completed"', stage_manifest)
            self.assertIn('"status": "passed"', stage_manifest)

    def test_review_payload_for_later_volume_includes_existing_style_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "002")
            request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("002"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )

            file_keys = [item["file_key"] for item in request["adaptation_documents"]]
            self.assertEqual(
                file_keys,
                [
                    "world_model",
                    "style_guide",
                    "book_outline",
                    "foreshadowing",
                    "storyline_blueprint",
                    "volume_outline",
                ],
            )
            self.assertIn("后续卷审核本卷更新文档，并带上已存在的文笔写作风格文档", request["review_scope"]["document_set_policy"])
            self.assertIn("后续卷只能读取与审核，不得把 style_guide 写入 rewrite_targets", "\n".join(request["requirements"]))

    def test_later_volume_review_fix_targets_exclude_style_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = _seed_adaptation_docs(project_root, "002")

            review_files = adaptation_workflow.adaptation_review_document_files(paths)
            allowed_fix_files = adaptation_workflow.adaptation_review_allowed_files(paths, volume_number="002")

        self.assertIn("style_guide", review_files)
        self.assertNotIn("style_guide", allowed_fix_files)

    def test_review_payload_protects_chapter_runtime_foreshadowing_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "002")
            request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("002"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )

        requirements = "\n".join(request["requirements"])
        self.assertIn("运行时记录", requirements)
        self.assertIn("受保护内容", requirements)
        self.assertIn("不得要求删除、压缩或改写", requirements)
        self.assertIn("不得仅因其存在判定资料适配不通过", requirements)

    def test_review_payload_checks_source_names_and_discourse_system(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            paths = _seed_adaptation_docs(project_root, "001")
            request = adaptation_workflow.build_adaptation_review_request(
                manifest=manifest,  # type: ignore[arg-type]
                volume_material=_volume_material("001"),  # type: ignore[arg-type]
                allowed_files=adaptation_workflow.adaptation_review_document_files(paths),
            )

        requirements = "\n".join(request["requirements"])
        self.assertIn("参考源人物名、地名、姓氏、势力名", requirements)
        self.assertIn("标志性台词", requirements)
        self.assertIn("话语体系", requirements)
        self.assertIn("不得直接照搬", requirements)

    def test_review_failure_repairs_documents_then_passes_without_marking_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            failed_review = adaptation_workflow.AdaptationReviewPayload(
                passed=False,
                review_md="不通过，世界模型还残留参考源人物名。",
                blocking_issues=["世界模型残留参考源人物名"],
                rewrite_targets=["world_model"],
            )
            passed_review = adaptation_workflow.AdaptationReviewPayload(
                passed=True,
                review_md="审核通过。",
            )
            fix_operation = adaptation_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_fix",
                status="completed",
                output_types=["function_call"],
                preview="fix",
                raw_body_text="",
                raw_json={},
                edit_payload=adaptation_workflow.document_ops.DocumentEditPayload(
                    files=[
                        adaptation_workflow.document_ops.DocumentEditFile(
                            file_key="world_model",
                            edits=[
                                adaptation_workflow.document_ops.DocumentEditEdit(
                                    old_text="源人物名仍残留",
                                    new_text="新书主角名已替换",
                                )
                            ],
                        )
                    ]
                ),
            )

            with (
                patch.object(
                    adaptation_workflow,
                    "call_adaptation_review_response",
                    side_effect=[
                        (failed_review, "resp_review_1", Mock(response_id="resp_review_1")),
                        (passed_review, "resp_review_2", Mock(response_id="resp_review_2")),
                    ],
                ) as review_call,
                patch.object(adaptation_workflow.document_ops, "call_document_operation_tools", return_value=fix_operation) as fix_call,
            ):
                result, response_id = adaptation_workflow.run_adaptation_review_until_passed(
                    client=Mock(),
                    model="test-model",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                    stage_shared_prompt="shared\n",
                    previous_response_id="resp_docs",
                    prompt_cache_key="cache-key",
                )

            self.assertTrue(result.payload.passed)
            self.assertEqual(response_id, "resp_review_2")
            self.assertEqual(review_call.call_count, 2)
            fix_call.assert_called_once()
            self.assertEqual(manifest["processed_volumes"], [])
            self.assertIn("新书主角名已替换", paths["world_model"].read_text(encoding="utf-8"))
            self.assertTrue(paths["adaptation_review"].exists())

    def test_review_fix_rejects_unauthorized_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            review = adaptation_workflow.AdaptationReviewPayload(
                passed=False,
                review_md="不通过。",
                blocking_issues=["越权测试"],
                rewrite_targets=["world_model"],
            )
            bad_operation = adaptation_workflow.document_ops.DocumentOperationCallResult(
                mode="patch",
                response_id="resp_bad",
                status="completed",
                output_types=["function_call"],
                preview="bad",
                raw_body_text="",
                raw_json={},
                patch_payload=adaptation_workflow.document_ops.DocumentPatchPayload(
                    files=[
                        adaptation_workflow.document_ops.DocumentPatchFile(
                            file_path=str(project_root / "outside.md"),
                            edits=[
                                adaptation_workflow.document_ops.DocumentPatchEdit(
                                    action="append",
                                    new_text="越权内容",
                                )
                            ],
                        )
                    ]
                ),
            )

            with (
                self.assertRaises(ValueError),
                patch.object(adaptation_workflow, "MAX_DOCUMENT_OPERATION_REPAIR_ATTEMPTS", 0),
                patch.object(adaptation_workflow.document_ops, "call_document_operation_tools", return_value=bad_operation),
            ):
                adaptation_workflow.apply_adaptation_review_fix_with_repair(
                    client=Mock(),
                    model="test-model",
                    shared_prompt="shared\n",
                    review=review,
                    allowed_files=adaptation_workflow.adaptation_review_allowed_files(paths),
                    previous_response_id="resp_review",
                    prompt_cache_key="cache-key",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                )

            self.assertEqual(manifest["processed_volumes"], [])
            self.assertTrue(paths["response_debug"].exists())
            self.assertIn("未授权文件路径", paths["response_debug"].read_text(encoding="utf-8"))

    def test_later_volume_review_fix_rejects_style_guide_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("002")
            paths = _seed_adaptation_docs(project_root, "002")
            review = adaptation_workflow.AdaptationReviewPayload(
                passed=False,
                review_md="不通过，但错误地要求修改文风。",
                blocking_issues=["文风问题"],
                rewrite_targets=["style_guide"],
            )

            with self.assertRaises(adaptation_workflow.llm_runtime.ModelOutputError):
                adaptation_workflow.apply_adaptation_review_fix_with_repair(
                    client=Mock(),
                    model="test-model",
                    shared_prompt="shared\n",
                    review=review,
                    allowed_files=adaptation_workflow.adaptation_review_allowed_files(paths, volume_number="002"),
                    previous_response_id="resp_review",
                    prompt_cache_key="cache-key",
                    manifest=manifest,  # type: ignore[arg-type]
                    volume_material=volume_material,  # type: ignore[arg-type]
                )

            self.assertTrue(paths["response_debug"].exists())
            debug_text = paths["response_debug"].read_text(encoding="utf-8")
            self.assertIn("不允许原地修复的目标", debug_text)
            self.assertIn("style_guide", debug_text)


if __name__ == "__main__":
    unittest.main()
