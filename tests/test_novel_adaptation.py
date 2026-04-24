from __future__ import annotations

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
                "world_design",
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
                "global_plot_progress",
                "volume_outline",
            ],
        )

    def test_later_volume_plan_uses_new_generation_order(self) -> None:
        plan = adaptation_workflow.build_document_plan("002")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "world_design",
                "world_model",
                "book_outline",
                "foreshadowing",
                "global_plot_progress",
                "volume_outline",
            ],
        )


class WorldModelDefinitionTests(unittest.TestCase):
    def test_world_model_default_sections_has_sixteen_entries(self) -> None:
        self.assertEqual(len(adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS), 16)
        self.assertEqual(adaptation_workflow.WORLD_MODEL_DEFAULT_SECTIONS[-1], "可扩展世界专题")

    def test_world_model_scope_text_mentions_expansion_section(self) -> None:
        scope = adaptation_workflow.world_model_scope_text()
        self.assertIn("可扩展世界专题", scope)
        self.assertIn("16 个二级标题", scope)
        self.assertIn("多个三级标题", scope)


class GlobalPlotProgressDefinitionTests(unittest.TestCase):
    def test_global_plot_progress_scope_mentions_storyline_structure(self) -> None:
        scope = adaptation_workflow.global_plot_progress_scope_text()
        self.assertIn("全书级故事线规划文档", scope)
        self.assertIn("二级标题", scope)
        self.assertIn("三级标题", scope)
        self.assertIn("待推进", scope)

    def test_global_plot_progress_file_number_is_six(self) -> None:
        self.assertEqual(adaptation_workflow.GLOBAL_FILE_NAMES["global_plot_progress"], "06_global_plot_progress.md")

    def test_global_plot_progress_request_definition_is_storyline_planning(self) -> None:
        request = adaptation_workflow.build_document_request("global_plot_progress")
        self.assertIn("全书故事线规划文档", request["task"])
        self.assertIn("三级标题", request["scope"])


class AdaptationInjectionOrderTests(unittest.TestCase):
    def test_build_injected_global_docs_uses_requested_generation_order(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "book_outline": "全书大纲",
                "world_design": "世界观设计",
                "style_guide": "文笔写作风格",
                "world_model": "世界模型",
                "global_plot_progress": "全局剧情进程",
                "foreshadowing": "伏笔管理",
            }
        )
        self.assertEqual(
            list(injected.keys()),
            [
                "world_design",
                "world_model",
                "style_guide",
                "book_outline",
                "foreshadowing",
                "global_plot_progress",
            ],
        )

    def test_build_injected_global_docs_excludes_current_target_document(self) -> None:
        injected = adaptation_workflow.build_injected_global_docs(
            {
                "book_outline": "全书大纲",
                "world_design": "世界观设计",
                "style_guide": "文笔写作风格",
                "world_model": "世界模型",
                "global_plot_progress": "全局剧情进程",
                "foreshadowing": "伏笔管理",
            },
            exclude_keys={"style_guide"},
        )
        self.assertNotIn("style_guide", injected)

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
                    "global_plot_progress": "全局剧情进程",
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
        self.assertNotIn("existing_style_guide", payload)
        self.assertNotIn("style_guide", payload["injected_global_docs"])


class AdaptationVolumeReviewTests(unittest.TestCase):
    def test_stage_outputs_wait_for_review_before_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")

            adaptation_workflow.write_stage_outputs(
                manifest,  # type: ignore[arg-type]
                volume_material,  # type: ignore[arg-type]
                generated_documents=[{"key": "world_design", "label": "世界观设计文档"}],
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
                generated_documents=[{"key": "world_design", "label": "世界观设计文档"}],
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
                allowed_files=adaptation_workflow.adaptation_review_allowed_files(paths),
            )

            file_keys = [item["file_key"] for item in request["adaptation_documents"]]
            self.assertEqual(
                file_keys,
                [
                    "world_design",
                    "world_model",
                    "style_guide",
                    "book_outline",
                    "foreshadowing",
                    "global_plot_progress",
                    "volume_outline",
                ],
            )
            self.assertIn("后续卷审核本卷更新文档，并带上已存在的文笔写作风格文档", request["review_scope"]["document_set_policy"])

    def test_review_failure_repairs_documents_then_passes_without_marking_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material("001")
            paths = _seed_adaptation_docs(project_root, "001")
            failed_review = adaptation_workflow.AdaptationReviewPayload(
                passed=False,
                review_md="不通过，世界观还残留参考源人物名。",
                blocking_issues=["世界观残留参考源人物名"],
                rewrite_targets=["world_design"],
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
                            file_key="world_design",
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
            self.assertIn("新书主角名已替换", paths["world_design"].read_text(encoding="utf-8"))
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
                rewrite_targets=["world_design"],
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


if __name__ == "__main__":
    unittest.main()
