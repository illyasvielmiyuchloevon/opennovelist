from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from novelist.workflows import novel_chapter_rewrite as rewrite_workflow
from novelist.workflows.chapter_rewrite import chapter_runner as chapter_runner_module
from novelist.workflows.chapter_rewrite import review as chapter_review_module


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest(project_root: Path) -> dict:
    return {
        "project_root": str(project_root),
        "source_root": str(project_root / "source"),
        "new_book_title": "测试书",
        "target_worldview": "玄幻",
        "rewrite_output_root": str(project_root / rewrite_workflow.REWRITTEN_ROOT_DIRNAME),
        "processed_volumes": [],
        "last_processed_volume": None,
        "last_processed_chapter": None,
        "chapter_states": {},
        "volume_review_states": {},
        "five_chapter_review_states": {},
    }


def _volume_material(chapter_numbers: list[str]) -> dict:
    return {
        "volume_number": "001",
        "chapters": [
            {
                "chapter_number": chapter_number,
                "file_name": f"{chapter_number}.txt",
                "file_path": f"source/001/{chapter_number}.txt",
                "source_title": f"第{int(chapter_number)}章 测试",
                "text": f"参考源章节 {chapter_number}。",
            }
            for chapter_number in chapter_numbers
        ],
        "extras": [],
    }


def _seed_rewrite_files(project_root: Path, chapter_numbers: list[str]) -> None:
    for chapter_number in chapter_numbers:
        paths = rewrite_workflow.rewrite_paths(project_root, "001", chapter_number)
        _write_text(paths["chapter_outline"], f"# {chapter_number} 章纲\n")
        _write_text(paths["chapter_review"], f"# {chapter_number} 章级审核\n")
        _write_text(paths["rewritten_chapter"], f"{chapter_number} 原正文问题。\n")
    volume_paths = rewrite_workflow.rewrite_paths(project_root, "001")
    _write_text(volume_paths["volume_outline"], "# 卷级大纲\n")
    _write_text(volume_paths["volume_plot_progress"], "# 卷级剧情进程\n")
    _write_text(volume_paths["volume_review"], "# 卷级审核\n")


def _applied_fix(file_key: str, path: Path) -> rewrite_workflow.document_ops.AppliedDocumentOperation:
    return rewrite_workflow.document_ops.AppliedDocumentOperation(
        mode="edit",
        files=[
            rewrite_workflow.document_ops.AppliedDocumentFile(
                file_key=file_key,
                path=path,
                mode="edit",
                emitted=True,
                changed=True,
                edit_count=1,
            )
        ],
    )


class VolumeReadinessTests(unittest.TestCase):
    def test_world_design_file_is_not_required_after_world_model_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            source_root = Path(temp_dir) / "source"
            source_volume = source_root / "001"
            source_volume.mkdir(parents=True)
            _write_text(source_volume / "0001.txt", "参考源章节。\n")

            paths = rewrite_workflow.rewrite_paths(project_root, "001")
            _write_text(paths["world_model"], "# 世界模型\n")
            _write_text(paths["style_guide"], "# 文笔写作风格\n")
            _write_text(paths["book_outline"], "# 全书大纲\n")
            _write_text(paths["foreshadowing"], "# 伏笔管理\n")
            _write_text(paths["storyline_blueprint"], "# 全书故事线蓝图\n")
            _write_text(paths["volume_outline"], "# 卷级大纲\n")

            readiness = rewrite_workflow.assess_volume_readiness(project_root, source_root, "001")

        self.assertTrue(readiness["eligible"])
        self.assertNotIn("01_world_design.md", "\n".join(readiness["missing"]))


class ReviewPayloadNormalizationTests(unittest.TestCase):
    def test_finalize_review_payload_infers_passed_from_review_text(self) -> None:
        payload = rewrite_workflow.WorkflowSubmissionPayload(
            review_md="## 一、总体结论\n本章**通过**。\n",
        )

        finalized = rewrite_workflow.finalize_review_payload(payload, review_kind="chapter")

        self.assertTrue(finalized.passed)
        self.assertIn("# 章级审核", finalized.review_md)
        self.assertIn("## 总体结论", finalized.review_md)
        self.assertIn("**通过**", finalized.review_md)

    def test_finalize_group_review_payload_extracts_chapters_to_revise(self) -> None:
        payload = rewrite_workflow.WorkflowSubmissionPayload(
            review_md="## 一、总体结论\n当前组审查不通过。\n需要返工章节：0003、0005。\n",
            blocking_issues=["当前组剧情推进与卷纲发生偏移。"],
        )

        finalized = rewrite_workflow.finalize_review_payload(
            payload,
            review_kind="group",
            allowed_chapters=["0001", "0002", "0003", "0004", "0005"],
        )

        self.assertFalse(finalized.passed)
        self.assertEqual(finalized.chapters_to_revise, ["0003", "0005"])
        self.assertIn("# 组审查", finalized.review_md)
        self.assertIn("## 需要返工的章节", finalized.review_md)

    def test_finalize_review_payload_uses_content_md_as_fallback(self) -> None:
        payload = rewrite_workflow.WorkflowSubmissionPayload(
            content_md="本章通过，可进入下一阶段。",
        )

        finalized = rewrite_workflow.finalize_review_payload(payload, review_kind="chapter")

        self.assertTrue(finalized.passed)
        self.assertTrue(finalized.review_md.strip())
        self.assertIn("## 修改建议", finalized.review_md)


class WritingSkillInjectionTests(unittest.TestCase):
    def test_phase2_chapter_text_includes_writing_skill_reference(self) -> None:
        volume_material = {
            "volume_number": "001",
            "chapters": [
                {
                    "chapter_number": "0001",
                    "file_name": "0001.txt",
                    "source_title": "第1章 测试",
                    "text": "这是当前参考章节正文。",
                }
            ],
            "extras": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0001")
            with patch.object(
                rewrite_workflow,
                "load_chapter_writing_skill_reference",
                return_value={"label": "写作规范 Skill", "content": "写作规范内容"},
            ):
                payload, _, _ = rewrite_workflow.build_phase_request_payload(
                    phase_key="phase2_chapter_text",
                    project_root=project_root,
                    volume_material=volume_material,
                    volume_number="001",
                    chapter_number="0001",
                    catalog=catalog,
                )

        self.assertIn("writing_skill_reference", payload)
        self.assertNotIn("review_skill_reference", payload)
        self.assertEqual(payload["writing_skill_reference"]["label"], "写作规范 Skill")

    def test_phase3_review_does_not_include_writing_skill_reference(self) -> None:
        volume_material = {
            "volume_number": "001",
            "chapters": [
                {
                    "chapter_number": "0001",
                    "file_name": "0001.txt",
                    "source_title": "第1章 测试",
                    "text": "这是当前参考章节正文。",
                }
            ],
            "extras": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0001")
            payload, _, _ = rewrite_workflow.build_phase_request_payload(
                phase_key="phase3_review",
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_number="0001",
                catalog=catalog,
                chapter_text="这是仿写正文。",
            )

        self.assertNotIn("writing_skill_reference", payload)
        self.assertIn("review_skill_reference", payload)

    def test_phase2_chapter_text_revision_payload_uses_existing_chapter_context(self) -> None:
        volume_material = {
            "volume_number": "001",
            "chapters": [
                {
                    "chapter_number": "0001",
                    "file_name": "0001.txt",
                    "source_title": "第1章 测试",
                    "text": "这是当前参考章节正文。",
                }
            ],
            "extras": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0001")
            payload, _, _ = rewrite_workflow.build_phase_request_payload(
                phase_key="phase2_chapter_text",
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_number="0001",
                catalog=catalog,
                chapter_text="这是现有章节正文。",
                chapter_text_revision=True,
            )

        self.assertEqual(payload["document_request"]["role"], "章节仿写修订作者")
        self.assertIn("update_target_files", payload)
        self.assertIn("current_generated_chapter", payload)
        self.assertEqual(payload["update_target_files"][0]["preferred_mode"], "edit_or_patch")
        self.assertEqual(payload["update_target_files"][0]["write_policy"], "no_write_if_exists")
        self.assertIn("按修改意图选择工具", payload["update_target_files"][0]["tool_selection_policy"])
        self.assertEqual(payload["current_generated_chapter"]["content"], "这是现有章节正文。")


class RevisionPlanTests(unittest.TestCase):
    def test_build_chapter_revision_plan_for_text_only(self) -> None:
        plan = rewrite_workflow.build_chapter_revision_plan(rewrite_targets=["chapter_text"])
        self.assertEqual(plan, ["phase2_chapter_text", "phase3_review"])

    def test_build_chapter_revision_plan_for_text_and_support_updates(self) -> None:
        plan = rewrite_workflow.build_chapter_revision_plan(
            rewrite_targets=["chapter_text", "world_state"],
        )
        self.assertEqual(
            plan,
            ["phase2_chapter_text", "phase2_support_updates", "phase3_review"],
        )

    def test_build_multi_chapter_revision_plan_uses_per_chapter_targets(self) -> None:
        plan = rewrite_workflow.build_multi_chapter_revision_plan(
            chapters_to_revise=["0003", "0005"],
            rewrite_targets=["0003:chapter_text", "0005:support_updates"],
        )
        self.assertEqual(plan["0003"], ["phase2_chapter_text", "phase3_review"])
        self.assertEqual(plan["0005"], ["phase2_support_updates", "phase3_review"])


class DocumentOperationRepairTests(unittest.TestCase):
    def test_apply_document_operation_with_repair_retries_bad_old_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "0005.txt"
            target.write_text("第一段原文。\n\n第二段原文。\n", encoding="utf-8")
            debug_path = Path(temp_dir) / "debug.md"

            failed_operation = rewrite_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_initial",
                status="completed",
                output_types=["function_call"],
                preview="bad edit",
                raw_body_text="",
                raw_json={},
                edit_payload=rewrite_workflow.document_ops.DocumentEditPayload(
                    files=[
                        rewrite_workflow.document_ops.DocumentEditFile(
                            file_key="rewritten_chapter",
                            edits=[
                                rewrite_workflow.document_ops.DocumentEditEdit(
                                    old_text="第二段被模型改写后的文字。",
                                    new_text="第二段修订后文本。",
                                )
                            ],
                        )
                    ]
                ),
            )
            repaired_operation = rewrite_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_repair",
                status="completed",
                output_types=["function_call"],
                preview="fixed edit",
                raw_body_text="",
                raw_json={},
                edit_payload=rewrite_workflow.document_ops.DocumentEditPayload(
                    files=[
                        rewrite_workflow.document_ops.DocumentEditFile(
                            file_key="rewritten_chapter",
                            edits=[
                                rewrite_workflow.document_ops.DocumentEditEdit(
                                    old_text="第二段原文。",
                                    new_text="第二段修订后文本。",
                                )
                            ],
                        )
                    ]
                ),
            )

            with patch.object(
                rewrite_workflow.document_ops,
                "call_document_operation_tools",
                return_value=repaired_operation,
            ) as call_tools:
                applied, response_id, repair_response_ids = rewrite_workflow.apply_document_operation_with_repair(
                    client=Mock(),
                    model="test-model",
                    instructions="instructions",
                    shared_prompt="shared prompt\n",
                    operation=failed_operation,
                    allowed_files={"rewritten_chapter": target},
                    previous_response_id="resp_initial",
                    prompt_cache_key="cache-key",
                    phase_key=rewrite_workflow.PHASE2_CHAPTER_TEXT,
                    repair_role="章节仿写修订作者",
                    repair_task="修正定位文本。",
                    debug_path=debug_path,
                )

            self.assertEqual(response_id, "resp_repair")
            self.assertEqual(repair_response_ids, ["resp_repair"])
            self.assertEqual(applied.changed_keys, ["rewritten_chapter"])
            self.assertIn("第二段修订后文本。", target.read_text(encoding="utf-8"))
            call_tools.assert_called_once()
            repair_input = call_tools.call_args.kwargs["user_input"]
            self.assertIn("第二段被模型改写后的文字。", repair_input)
            self.assertIn("第二段原文。", repair_input)
            self.assertIn("逐字复制", repair_input)


class ReviewFixLoopTests(unittest.TestCase):
    def test_review_fix_without_targets_writes_debug_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            target = project_root / "rewritten_novel" / "001" / "0001.txt"
            _write_text(target, "原正文。\n")
            debug_path = project_root / "debug.md"
            review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="不通过，但没有返回返工对象。",
                blocking_issues=["缺少目标"],
            )

            with (
                self.assertRaises(rewrite_workflow.llm_runtime.ModelOutputError),
                patch.object(rewrite_workflow.document_ops, "call_document_operation_tools") as call_tools,
            ):
                rewrite_workflow.apply_review_fix_with_repair(
                    client=Mock(),
                    model="test-model",
                    review_kind="chapter",
                    shared_prompt="shared\n",
                    review=review,
                    allowed_files={"rewritten_chapter": target},
                    previous_response_id="resp_review",
                    prompt_cache_key="cache-key",
                    debug_path=debug_path,
                )

            call_tools.assert_not_called()
            self.assertTrue(debug_path.exists())
            self.assertIn("未返回可修复目标", debug_path.read_text(encoding="utf-8"))

    def test_chapter_review_failure_repairs_without_restarting_generation_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material(["0001"])
            _seed_rewrite_files(project_root, ["0001"])
            rewrite_workflow.update_chapter_state(
                manifest,
                "001",
                "0001",
                status="in_progress",
                pending_phases=[rewrite_workflow.PHASE3_REVIEW],
            )
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            failed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="不通过，需要修改正文。",
                blocking_issues=["原正文问题"],
                rewrite_targets=["chapter_text"],
            )
            passed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=True,
                review_md="通过。",
            )
            fix_operation = rewrite_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_fix",
                status="completed",
                output_types=["function_call"],
                preview="fix",
                raw_body_text="",
                raw_json={},
                edit_payload=rewrite_workflow.document_ops.DocumentEditPayload(
                    files=[
                        rewrite_workflow.document_ops.DocumentEditFile(
                            file_key="rewritten_chapter",
                            edits=[
                                rewrite_workflow.document_ops.DocumentEditEdit(
                                    old_text="0001 原正文问题。",
                                    new_text="0001 修复后的正文。",
                                )
                            ],
                        )
                    ]
                ),
            )

            with (
                patch.object(
                    rewrite_workflow,
                    "call_chapter_review_response",
                    side_effect=[
                        (failed_review, "resp_review_1", Mock(response_id="resp_review_1")),
                        (passed_review, "resp_review_2", Mock(response_id="resp_review_2")),
                    ],
                ) as review_call,
                patch.object(rewrite_workflow.document_ops, "call_document_operation_tools", return_value=fix_operation),
                patch.object(rewrite_workflow, "call_chapter_text_revision_response", side_effect=AssertionError("should not restart text phase")),
                patch.object(rewrite_workflow, "print_request_context_summary"),
            ):
                rewrite_workflow.run_chapter_workflow(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                    chapter_number="0001",
                )

            state = rewrite_workflow.get_chapter_state(manifest, "001", "0001")
            self.assertEqual(review_call.call_count, 2)
            self.assertEqual(state["status"], "passed")
            self.assertEqual(state["pending_phases"], [])
            self.assertIn("修复后的正文", paths["rewritten_chapter"].read_text(encoding="utf-8"))

    def test_chapter_review_allows_five_total_review_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material(["0001"])
            _seed_rewrite_files(project_root, ["0001"])
            rewrite_workflow.update_chapter_state(
                manifest,
                "001",
                "0001",
                status="in_progress",
                pending_phases=[rewrite_workflow.PHASE3_REVIEW],
            )
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            failed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="章审仍不通过。",
                blocking_issues=["正文仍需修复"],
                rewrite_targets=["chapter_text"],
            )
            applied_fix = _applied_fix("rewritten_chapter", paths["rewritten_chapter"])

            with (
                self.assertRaisesRegex(ValueError, "连续 5 次审核"),
                patch.object(
                    chapter_runner_module,
                    "call_chapter_review_response",
                    side_effect=[
                        (failed_review, f"resp_review_{index}", Mock(response_id=f"resp_review_{index}"))
                        for index in range(1, 6)
                    ],
                ) as review_call,
                patch.object(
                    chapter_runner_module,
                    "apply_review_fix_with_repair",
                    side_effect=[
                        (applied_fix, f"resp_fix_{index}", [f"resp_fix_{index}"])
                        for index in range(1, 5)
                    ],
                ) as fix_call,
                patch.object(chapter_runner_module, "print_request_context_summary"),
            ):
                chapter_runner_module.run_chapter_workflow(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                    chapter_number="0001",
                )

            state = rewrite_workflow.get_chapter_state(manifest, "001", "0001")
            self.assertEqual(rewrite_workflow.MAX_CHAPTER_REVIEW_ATTEMPTS, 5)
            self.assertEqual(rewrite_workflow.MAX_CHAPTER_REVIEW_FIX_ATTEMPTS, 4)
            self.assertEqual(review_call.call_count, 5)
            self.assertEqual(fix_call.call_count, 4)
            self.assertEqual(review_call.call_args_list[4].kwargs["previous_response_id"], "resp_fix_4")
            self.assertEqual(state["status"], "failed")

    def test_chapter_review_resume_uses_previous_stage_response_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material(["0001"])
            _seed_rewrite_files(project_root, ["0001"])
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            rewrite_workflow.update_chapter_state(
                manifest,
                "001",
                "0001",
                status="in_progress",
                pending_phases=[rewrite_workflow.PHASE3_REVIEW],
            )
            rewrite_workflow.write_chapter_stage_snapshot(
                paths["chapter_stage_manifest"],
                volume_number="001",
                chapter_number="0001",
                status="in_progress",
                note="配套状态文档已完成，准备进入章级审核。",
                attempt=1,
                last_phase=rewrite_workflow.PHASE3_REVIEW,
                response_ids=["resp_outline", "resp_text", "resp_support"],
            )
            passed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=True,
                review_md="通过。",
            )

            with (
                patch.object(
                    rewrite_workflow,
                    "call_chapter_review_response",
                    return_value=(passed_review, "resp_review", Mock(response_id="resp_review")),
                ) as review_call,
                patch.object(rewrite_workflow, "print_request_context_summary"),
            ):
                rewrite_workflow.run_chapter_workflow(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                    chapter_number="0001",
                )

            review_call.assert_called_once()
            self.assertEqual(review_call.call_args.kwargs["previous_response_id"], "resp_support")
            payload = rewrite_workflow.load_chapter_stage_manifest_payload(paths["chapter_stage_manifest"])
            self.assertEqual(payload["last_response_id"], "resp_review")
            self.assertEqual(payload["response_ids"], ["resp_outline", "resp_text", "resp_support", "resp_review"])

    def test_group_review_failure_repairs_in_review_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            chapter_numbers = ["0001", "0002", "0003", "0004", "0005"]
            volume_material = _volume_material(chapter_numbers)
            _seed_rewrite_files(project_root, chapter_numbers)
            failed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="组审查不通过，需要修复 0003。",
                blocking_issues=["0003 偏移"],
                rewrite_targets=["0003:chapter_text"],
                chapters_to_revise=["0003"],
            )
            passed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=True,
                review_md="组审查通过。",
            )
            fix_operation = rewrite_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_group_fix",
                status="completed",
                output_types=["function_call"],
                preview="fix",
                raw_body_text="",
                raw_json={},
                edit_payload=rewrite_workflow.document_ops.DocumentEditPayload(
                    files=[
                        rewrite_workflow.document_ops.DocumentEditFile(
                            file_key="0003_rewritten_chapter",
                            edits=[
                                rewrite_workflow.document_ops.DocumentEditEdit(
                                    old_text="0003 原正文问题。",
                                    new_text="0003 组审修复后的正文。",
                                )
                            ],
                        )
                    ]
                ),
            )

            with (
                patch.object(
                    rewrite_workflow,
                    "call_five_chapter_review_response",
                    side_effect=[
                        (failed_review, "resp_group_review_1", Mock(response_id="resp_group_review_1")),
                        (passed_review, "resp_group_review_2", Mock(response_id="resp_group_review_2")),
                    ],
                ) as review_call,
                patch.object(rewrite_workflow.document_ops, "call_document_operation_tools", return_value=fix_operation) as fix_call,
                patch.object(rewrite_workflow, "print_request_context_summary"),
            ):
                passed = rewrite_workflow.run_five_chapter_review(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                    chapter_numbers=chapter_numbers,
                )

            group_state = rewrite_workflow.get_five_chapter_review_state(
                manifest,
                "001",
                rewrite_workflow.five_chapter_batch_id(chapter_numbers),
                chapter_numbers,
            )
            chapter_state = manifest.get("chapter_states", {}).get("001", {}).get("0003", {})
            self.assertTrue(passed)
            self.assertEqual(review_call.call_count, 2)
            self.assertIsNone(review_call.call_args_list[0].kwargs["previous_response_id"])
            self.assertEqual(review_call.call_args_list[1].kwargs["previous_response_id"], "resp_group_fix")
            fix_call.assert_called_once()
            self.assertEqual(fix_call.call_args.kwargs["previous_response_id"], "resp_group_review_1")
            self.assertEqual(group_state["status"], "passed")
            self.assertEqual(group_state["last_response_id"], "resp_group_review_2")
            self.assertEqual(
                group_state["response_ids"],
                ["resp_group_review_1", "resp_group_fix", "resp_group_review_2"],
            )
            self.assertNotEqual(chapter_state.get("status"), "needs_revision")

    def test_group_review_resume_uses_persisted_response_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            chapter_numbers = ["0001", "0002", "0003", "0004", "0005"]
            volume_material = _volume_material(chapter_numbers)
            _seed_rewrite_files(project_root, chapter_numbers)
            batch_id = rewrite_workflow.five_chapter_batch_id(chapter_numbers)
            rewrite_workflow.update_five_chapter_review_state(
                manifest,
                "001",
                batch_id,
                chapter_numbers,
                status="in_review_fix",
                attempts=1,
                response_ids=["resp_group_review_1", "resp_group_fix"],
                last_response_id="resp_group_fix",
            )
            reloaded_manifest = rewrite_workflow.load_rewrite_manifest(project_root)
            self.assertIsNotNone(reloaded_manifest)
            passed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=True,
                review_md="组审查通过。",
            )

            with (
                patch.object(
                    rewrite_workflow,
                    "call_five_chapter_review_response",
                    return_value=(passed_review, "resp_group_review_2", Mock(response_id="resp_group_review_2")),
                ) as review_call,
                patch.object(rewrite_workflow, "print_request_context_summary"),
            ):
                passed = rewrite_workflow.run_five_chapter_review(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=reloaded_manifest,
                    volume_material=volume_material,
                    chapter_numbers=chapter_numbers,
                )

            group_state = rewrite_workflow.get_five_chapter_review_state(
                reloaded_manifest,
                "001",
                batch_id,
                chapter_numbers,
            )
            self.assertTrue(passed)
            review_call.assert_called_once()
            self.assertEqual(review_call.call_args.kwargs["previous_response_id"], "resp_group_fix")
            self.assertEqual(group_state["status"], "passed")
            self.assertEqual(group_state["last_response_id"], "resp_group_review_2")
            self.assertEqual(
                group_state["response_ids"],
                ["resp_group_review_1", "resp_group_fix", "resp_group_review_2"],
            )

    def test_group_review_allows_ten_total_review_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            chapter_numbers = ["0001", "0002", "0003", "0004", "0005"]
            volume_material = _volume_material(chapter_numbers)
            _seed_rewrite_files(project_root, chapter_numbers)
            batch_id = rewrite_workflow.five_chapter_batch_id(chapter_numbers)
            failed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="组审查仍不通过。",
                blocking_issues=["0003 仍需修复"],
                rewrite_targets=["0003:chapter_text"],
                chapters_to_revise=["0003"],
            )
            applied_fix = _applied_fix(
                "0003_rewritten_chapter",
                rewrite_workflow.rewrite_paths(project_root, "001", "0003")["rewritten_chapter"],
            )

            with (
                self.assertRaisesRegex(ValueError, "连续 10 次审核"),
                patch.object(
                    chapter_review_module,
                    "call_five_chapter_review_response",
                    side_effect=[
                        (failed_review, f"resp_group_review_{index}", Mock(response_id=f"resp_group_review_{index}"))
                        for index in range(1, 11)
                    ],
                ) as review_call,
                patch.object(
                    chapter_review_module,
                    "apply_review_fix_with_repair",
                    side_effect=[
                        (applied_fix, f"resp_group_fix_{index}", [f"resp_group_fix_{index}"])
                        for index in range(1, 10)
                    ],
                ) as fix_call,
                patch.object(chapter_review_module, "print_request_context_summary"),
            ):
                chapter_review_module.run_five_chapter_review(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                    chapter_numbers=chapter_numbers,
                )

            group_state = rewrite_workflow.get_five_chapter_review_state(
                manifest,
                "001",
                batch_id,
                chapter_numbers,
            )
            self.assertEqual(rewrite_workflow.MAX_GROUP_REVIEW_ATTEMPTS, 10)
            self.assertEqual(rewrite_workflow.MAX_GROUP_REVIEW_FIX_ATTEMPTS, 9)
            self.assertEqual(review_call.call_count, 10)
            self.assertEqual(fix_call.call_count, 9)
            self.assertEqual(review_call.call_args_list[9].kwargs["previous_response_id"], "resp_group_fix_9")
            self.assertEqual(group_state["status"], "failed")
            self.assertEqual(group_state["attempts"], 10)

    def test_volume_review_failure_repairs_in_review_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            chapter_numbers = ["0001", "0002"]
            volume_material = _volume_material(chapter_numbers)
            _seed_rewrite_files(project_root, chapter_numbers)
            failed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="卷级审核不通过，需要修复 0002。",
                blocking_issues=["0002 偏移"],
                rewrite_targets=["0002:chapter_text"],
                chapters_to_revise=["0002"],
            )
            passed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=True,
                review_md="卷级审核通过。",
            )
            fix_operation = rewrite_workflow.document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_volume_fix",
                status="completed",
                output_types=["function_call"],
                preview="fix",
                raw_body_text="",
                raw_json={},
                edit_payload=rewrite_workflow.document_ops.DocumentEditPayload(
                    files=[
                        rewrite_workflow.document_ops.DocumentEditFile(
                            file_key="0002_rewritten_chapter",
                            edits=[
                                rewrite_workflow.document_ops.DocumentEditEdit(
                                    old_text="0002 原正文问题。",
                                    new_text="0002 卷审修复后的正文。",
                                )
                            ],
                        )
                    ]
                ),
            )

            with (
                patch.object(
                    rewrite_workflow,
                    "call_volume_review_response",
                    side_effect=[
                        (failed_review, "resp_volume_review_1", Mock(response_id="resp_volume_review_1")),
                        (passed_review, "resp_volume_review_2", Mock(response_id="resp_volume_review_2")),
                    ],
                ) as review_call,
                patch.object(rewrite_workflow.document_ops, "call_document_operation_tools", return_value=fix_operation) as fix_call,
                patch.object(rewrite_workflow, "print_request_context_summary"),
            ):
                passed = rewrite_workflow.run_volume_review(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                )

            volume_state = rewrite_workflow.get_volume_review_state(manifest, "001")
            chapter_state = manifest.get("chapter_states", {}).get("001", {}).get("0002", {})
            self.assertTrue(passed)
            self.assertEqual(review_call.call_count, 2)
            self.assertIsNone(review_call.call_args_list[0].kwargs["previous_response_id"])
            self.assertEqual(review_call.call_args_list[1].kwargs["previous_response_id"], "resp_volume_fix")
            fix_call.assert_called_once()
            self.assertEqual(fix_call.call_args.kwargs["previous_response_id"], "resp_volume_review_1")
            self.assertEqual(volume_state["status"], "passed")
            self.assertEqual(volume_state["last_response_id"], "resp_volume_review_2")
            self.assertEqual(
                volume_state["response_ids"],
                ["resp_volume_review_1", "resp_volume_fix", "resp_volume_review_2"],
            )
            self.assertIn("001", manifest["processed_volumes"])
            self.assertNotEqual(chapter_state.get("status"), "needs_revision")

    def test_volume_review_resume_uses_persisted_response_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            chapter_numbers = ["0001", "0002"]
            volume_material = _volume_material(chapter_numbers)
            _seed_rewrite_files(project_root, chapter_numbers)
            rewrite_workflow.update_volume_review_state(
                manifest,
                "001",
                status="in_review_fix",
                attempts=1,
                response_ids=["resp_volume_review_1", "resp_volume_fix"],
                last_response_id="resp_volume_fix",
            )
            reloaded_manifest = rewrite_workflow.load_rewrite_manifest(project_root)
            self.assertIsNotNone(reloaded_manifest)
            passed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=True,
                review_md="卷级审核通过。",
            )

            with (
                patch.object(
                    rewrite_workflow,
                    "call_volume_review_response",
                    return_value=(passed_review, "resp_volume_review_2", Mock(response_id="resp_volume_review_2")),
                ) as review_call,
                patch.object(rewrite_workflow, "print_request_context_summary"),
            ):
                passed = rewrite_workflow.run_volume_review(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=reloaded_manifest,
                    volume_material=volume_material,
                )

            volume_state = rewrite_workflow.get_volume_review_state(reloaded_manifest, "001")
            self.assertTrue(passed)
            review_call.assert_called_once()
            self.assertEqual(review_call.call_args.kwargs["previous_response_id"], "resp_volume_fix")
            self.assertEqual(volume_state["status"], "passed")
            self.assertEqual(volume_state["last_response_id"], "resp_volume_review_2")
            self.assertEqual(
                volume_state["response_ids"],
                ["resp_volume_review_1", "resp_volume_fix", "resp_volume_review_2"],
            )
            self.assertIn("001", reloaded_manifest["processed_volumes"])

    def test_volume_review_allows_ten_total_review_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            chapter_numbers = ["0001", "0002"]
            volume_material = _volume_material(chapter_numbers)
            _seed_rewrite_files(project_root, chapter_numbers)
            failed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="卷审查仍不通过。",
                blocking_issues=["0002 仍需修复"],
                rewrite_targets=["0002:chapter_text"],
                chapters_to_revise=["0002"],
            )
            applied_fix = _applied_fix(
                "0002_rewritten_chapter",
                rewrite_workflow.rewrite_paths(project_root, "001", "0002")["rewritten_chapter"],
            )

            with (
                self.assertRaisesRegex(ValueError, "连续 10 次审核"),
                patch.object(
                    chapter_review_module,
                    "call_volume_review_response",
                    side_effect=[
                        (failed_review, f"resp_volume_review_{index}", Mock(response_id=f"resp_volume_review_{index}"))
                        for index in range(1, 11)
                    ],
                ) as review_call,
                patch.object(
                    chapter_review_module,
                    "apply_review_fix_with_repair",
                    side_effect=[
                        (applied_fix, f"resp_volume_fix_{index}", [f"resp_volume_fix_{index}"])
                        for index in range(1, 10)
                    ],
                ) as fix_call,
                patch.object(chapter_review_module, "print_request_context_summary"),
            ):
                chapter_review_module.run_volume_review(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                )

            volume_state = rewrite_workflow.get_volume_review_state(manifest, "001")
            self.assertEqual(rewrite_workflow.MAX_VOLUME_REVIEW_ATTEMPTS, 10)
            self.assertEqual(rewrite_workflow.MAX_VOLUME_REVIEW_FIX_ATTEMPTS, 9)
            self.assertEqual(review_call.call_count, 10)
            self.assertEqual(fix_call.call_count, 9)
            self.assertEqual(review_call.call_args_list[9].kwargs["previous_response_id"], "resp_volume_fix_9")
            self.assertEqual(volume_state["status"], "failed")
            self.assertEqual(volume_state["attempts"], 10)


class SupportUpdateScopeTests(unittest.TestCase):
    def test_support_update_targets_do_not_include_adaptation_owned_globals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            target_paths = rewrite_workflow.support_update_target_paths(paths)
        self.assertNotIn("world_model", target_paths)
        self.assertNotIn("storyline_blueprint", target_paths)

    def test_rewrite_paths_reads_storyline_blueprint_from_new_global_number(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
        self.assertEqual(paths["storyline_blueprint"].name, "05_storyline_blueprint.md")

    def test_support_updates_and_review_do_not_duplicate_current_chapter_text(self) -> None:
        volume_material = {
            "volume_number": "001",
            "chapters": [
                {
                    "chapter_number": "0001",
                    "file_name": "0001.txt",
                    "source_title": "第1章 测试",
                    "text": "这是当前参考章节正文。",
                }
            ],
            "extras": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            for file_path, content in (
                (paths["book_outline"], "# 全书大纲\n"),
                (paths["world_model"], "# 世界模型\n"),
                (paths["style_guide"], "# 文笔写作风格\n"),
                (paths["volume_plot_progress"], "# 卷级剧情进程\n"),
                (paths["chapter_outline"], "# 章纲\n"),
                (paths["chapter_review"], "# 章级审核\n"),
                (paths["rewritten_chapter"], "这是当前已生成正文。"),
            ):
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")

            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0001")
            support_payload, _, _ = rewrite_workflow.build_phase_request_payload(
                phase_key="phase2_support_updates",
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_number="0001",
                catalog=catalog,
                chapter_text="这是当前已生成正文。",
            )
            review_payload, _, _ = rewrite_workflow.build_phase_request_payload(
                phase_key="phase3_review",
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_number="0001",
                catalog=catalog,
                chapter_text="这是当前已生成正文。",
            )

        self.assertNotIn("rewritten_chapter", support_payload["rolling_injected_chapter_docs"])
        self.assertNotIn("rewritten_chapter", review_payload["rolling_injected_chapter_docs"])
        self.assertIn("current_generated_chapter", support_payload)
        self.assertIn("current_generated_chapter", review_payload)
        existing_target = next(
            item for item in support_payload["update_target_files"] if item["current_content"].strip()
        )
        self.assertEqual(existing_target["preferred_mode"], "edit_or_patch")
        self.assertEqual(existing_target["write_policy"], "no_write_if_exists")
        self.assertIn("改已有条目", existing_target["tool_selection_policy"])


class StableGlobalInjectionOrderingTests(unittest.TestCase):
    def test_serialized_global_docs_keep_full_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "04_foreshadowing.md"
            path.write_text("伏" * 20000, encoding="utf-8")
            serialized = rewrite_workflow.serialize_doc_for_prompt(
                {
                    "key": "foreshadowing",
                    "category": "global",
                    "label": "伏笔管理",
                    "path": path,
                    "content": path.read_text(encoding="utf-8"),
                }
            )

        self.assertEqual(set(serialized), {"label", "file_name", "file_path", "char_count", "content"})
        self.assertEqual(serialized["content"], "伏" * 20000)
        self.assertEqual(serialized["char_count"], 20000)

    def test_world_model_and_storyline_blueprint_are_promoted_to_stable_global_docs(self) -> None:
        volume_material = {
            "volume_number": "001",
            "chapters": [
                {
                    "chapter_number": "0001",
                    "file_name": "0001.txt",
                    "source_title": "第1章 测试",
                    "text": "这是当前参考章节正文。",
                }
            ],
            "extras": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            for file_path in (
                paths["book_outline"],
                paths["style_guide"],
                paths["world_model"],
                paths["storyline_blueprint"],
                paths["foreshadowing"],
                paths["world_state"],
            ):
                file_path.parent.mkdir(parents=True, exist_ok=True)
            paths["book_outline"].write_text("# 全书大纲\n", encoding="utf-8")
            paths["style_guide"].write_text("# 文笔写作风格\n", encoding="utf-8")
            paths["world_model"].write_text("# 世界模型\n", encoding="utf-8")
            paths["storyline_blueprint"].write_text("# 全书故事线蓝图\n", encoding="utf-8")
            paths["foreshadowing"].write_text("# 伏笔管理\n", encoding="utf-8")
            paths["world_state"].write_text("# 世界状态\n", encoding="utf-8")

            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0001")
            payload, _, _ = rewrite_workflow.build_phase_request_payload(
                phase_key="phase1_outline",
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_number="0001",
                catalog=catalog,
            )

        stable_keys = list(payload["stable_injected_global_docs"].keys())
        rolling_keys = list(payload["rolling_injected_global_docs"].keys())

        self.assertEqual(
            stable_keys,
            [
                "world_model",
                "style_guide",
                "book_outline",
                "storyline_blueprint",
            ],
        )
        self.assertNotIn("world_model", rolling_keys)
        self.assertNotIn("storyline_blueprint", rolling_keys)
        self.assertEqual(rolling_keys, ["foreshadowing", "world_state"])


class VolumePlotProgressStructureTests(unittest.TestCase):
    def test_volume_plot_progress_template_uses_fixed_third_level_progress_headings(self) -> None:
        template = rewrite_workflow.HEADING_MANAGED_DOC_SPECS["volume_plot_progress"]["template"]
        self.assertIn("## 卷主线", template)
        self.assertIn("### 起始", template)
        self.assertIn("### 已发生发展", template)
        self.assertIn("### 关键转折", template)
        self.assertIn("### 当前状态", template)
        self.assertIn("### 待推进", template)

    def test_volume_plot_progress_rules_require_patching_affected_third_level_blocks(self) -> None:
        rules = "\n".join(rewrite_workflow.HEADING_MANAGED_DOC_SPECS["volume_plot_progress"]["update_rules"])
        self.assertIn("三级标题", rules)
        self.assertIn("不要整段替换整条故事线", rules)


if __name__ == "__main__":
    unittest.main()
