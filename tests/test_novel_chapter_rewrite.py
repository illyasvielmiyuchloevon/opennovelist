from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from novelist.core import novel_source as novel_source_module
from novelist.workflows import novel_chapter_rewrite as rewrite_workflow
from novelist.workflows.chapter_rewrite import _shared as chapter_shared_module
from novelist.workflows.chapter_rewrite import chapter_runner as chapter_runner_module
from novelist.workflows.chapter_rewrite import group_runner as group_runner_module
from novelist.workflows.chapter_rewrite import responses as chapter_responses_module
from novelist.workflows.chapter_rewrite import review as chapter_review_module
from novelist.workflows.chapter_rewrite import volume_runner as volume_runner_module


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


def _seed_group_plan(project_root: Path, groups: list[list[str]], *, status: str = "passed") -> None:
    raw_groups = [
        {
            "chapter_count": len(group),
            "source_chapter_range": f"{group[0]}-{group[-1]}",
            "group_title": f"{group[0]}-{group[-1]} 测试组",
            "guidance": "测试组纲指导。",
        }
        for group in groups
    ]
    rewrite_workflow.write_group_outline_plan_manifest(
        project_root,
        "001",
        status=status,
        groups=raw_groups,
        source_volume_dir=str(project_root / "source" / "001"),
        note="测试组纲计划。",
        response_ids=["resp_group_plan"],
        review={"status": status, "passed": status == "passed"},
    )
    for group in groups:
        outline_path = rewrite_workflow.group_outline_path(project_root, "001", group)
        outline = "\n\n".join(
            [
                f"# {group[0]}-{group[-1]} 组纲",
                *[f"## {chapter_number}\n- 写作目标：测试。\n- 节奏建议：按组纲推进。\n- 参考源功能映射：测试转换。" for chapter_number in group],
            ]
        )
        _write_text(outline_path, outline)


def _seed_rewrite_files(project_root: Path, chapter_numbers: list[str]) -> None:
    _seed_group_plan(project_root, [chapter_numbers])
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

def _multi_tool_from_operation(
    operation: rewrite_workflow.document_ops.DocumentOperationCallResult,
) -> rewrite_workflow.llm_runtime.MultiFunctionToolResult:
    if operation.mode == "write":
        tool_name = rewrite_workflow.document_ops.DOCUMENT_WRITE_TOOL_NAME
        parsed = operation.write_payload or rewrite_workflow.document_ops.DocumentWritePayload()
    elif operation.mode == "edit":
        tool_name = rewrite_workflow.document_ops.DOCUMENT_EDIT_TOOL_NAME
        parsed = operation.edit_payload or rewrite_workflow.document_ops.DocumentEditPayload()
    else:
        tool_name = rewrite_workflow.document_ops.DOCUMENT_PATCH_TOOL_NAME
        parsed = operation.patch_payload or rewrite_workflow.document_ops.DocumentPatchPayload()
    return rewrite_workflow.llm_runtime.MultiFunctionToolResult(
        tool_name=tool_name,
        parsed=parsed,
        response_id=operation.response_id,
        status=operation.status,
        output_types=operation.output_types,
        preview=operation.preview,
        raw_body_text=operation.raw_body_text,
        raw_json=operation.raw_json,
    )

def _workflow_multi_tool_result(
    payload: rewrite_workflow.WorkflowSubmissionPayload,
    response_id: str = "resp_workflow",
) -> rewrite_workflow.llm_runtime.MultiFunctionToolResult:
    return rewrite_workflow.llm_runtime.MultiFunctionToolResult(
        tool_name=rewrite_workflow.WORKFLOW_SUBMISSION_TOOL_NAME,
        parsed=payload,
        response_id=response_id,
        status="completed",
        output_types=["function_call"],
        preview="workflow",
        raw_body_text="",
        raw_json={},
    )


def _rewrite_agent_stage_result(
    payload: rewrite_workflow.WorkflowSubmissionPayload,
    response_id: str,
    response_ids: list[str] | None = None,
    applications: list[Mock] | None = None,
) -> Mock:
    return Mock(
        submission=payload,
        response_id=response_id,
        response_ids=response_ids or [response_id],
        applications=applications or [],
    )


def _agent_application(tool_name: str, changed_keys: list[str]) -> Mock:
    return Mock(
        tool_name=tool_name,
        applied=Mock(changed_keys=changed_keys),
        output="ok",
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
            _write_text(paths["volume_outline"], "# 卷级大纲\n")
            _seed_group_plan(project_root, [["0001"]])

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


class ChapterStageToolContractTests(unittest.TestCase):
    def test_review_instructions_reuse_common_chapter_stage_prefix(self) -> None:
        self.assertEqual(
            rewrite_workflow.COMMON_FIVE_CHAPTER_REVIEW_INSTRUCTIONS,
            rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
        )
        self.assertEqual(
            rewrite_workflow.COMMON_VOLUME_REVIEW_INSTRUCTIONS,
            rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
        )
        self.assertEqual(
            rewrite_workflow.review_fix_instructions("chapter"),
            rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS,
        )
        self.assertIn("Dynamic Request", rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)
        self.assertIn("submit_workflow_result", rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)
        self.assertIn("write/edit/patch", rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)

    def test_chapter_stage_exposes_only_unified_four_tools(self) -> None:
        tool_names = [spec.name for spec in rewrite_workflow.chapter_rewrite_stage_tool_specs()]

        self.assertEqual(
            tool_names,
            [
                rewrite_workflow.document_ops.DOCUMENT_WRITE_TOOL_NAME,
                rewrite_workflow.document_ops.DOCUMENT_EDIT_TOOL_NAME,
                rewrite_workflow.document_ops.DOCUMENT_PATCH_TOOL_NAME,
                rewrite_workflow.WORKFLOW_SUBMISSION_TOOL_NAME,
            ],
        )
        self.assertNotIn("submit_adaptation_review", tool_names)

    def test_workflow_submission_uses_unified_chapter_stage_tools(self) -> None:
        result = _workflow_multi_tool_result(
            rewrite_workflow.WorkflowSubmissionPayload(content_md="# 章纲\n"),
            response_id="resp_outline",
        )

        with patch.object(chapter_responses_module.llm_runtime, "call_function_tools", return_value=result) as call_tools:
            payload, response_id, _ = chapter_responses_module.call_workflow_submission_response(
                Mock(),
                model="test-model",
                instructions="instructions",
                user_input="input",
                previous_response_id="resp_prev",
                prompt_cache_key="cache-key",
            )

        self.assertEqual(payload.content_md, "# 章纲\n")
        self.assertEqual(response_id, "resp_outline")
        self.assertEqual(
            [spec.name for spec in call_tools.call_args.kwargs["tool_specs"]],
            [spec.name for spec in rewrite_workflow.chapter_rewrite_stage_tool_specs()],
        )
        self.assertEqual(
            call_tools.call_args.kwargs["tool_choice"],
            {"type": "function", "name": rewrite_workflow.WORKFLOW_SUBMISSION_TOOL_NAME},
        )

    def test_document_operation_stage_uses_same_unified_tools(self) -> None:
        operation = rewrite_workflow.document_ops.DocumentOperationCallResult(
            mode="edit",
            response_id="resp_edit",
            status="completed",
            output_types=["function_call"],
            preview="edit",
            raw_body_text="",
            raw_json={},
            edit_payload=rewrite_workflow.document_ops.DocumentEditPayload(
                files=[
                    rewrite_workflow.document_ops.DocumentEditFile(
                        file_key="rewritten_chapter",
                        edits=[
                            rewrite_workflow.document_ops.DocumentEditEdit(
                                old_text="旧正文",
                                new_text="新正文",
                            )
                        ],
                    )
                ]
            ),
        )

        with patch.object(chapter_responses_module.llm_runtime, "call_function_tools", return_value=_multi_tool_from_operation(operation)) as call_tools:
            result, response_id, _ = chapter_responses_module.call_chapter_text_revision_response(
                Mock(),
                model="test-model",
                instructions="instructions",
                user_input="input",
                previous_response_id="resp_prev",
                prompt_cache_key="cache-key",
            )

        self.assertEqual(result.mode, "edit")
        self.assertEqual(response_id, "resp_edit")
        self.assertEqual(
            [spec.name for spec in call_tools.call_args.kwargs["tool_specs"]],
            [spec.name for spec in rewrite_workflow.chapter_rewrite_stage_tool_specs()],
        )
        self.assertEqual(call_tools.call_args.kwargs["tool_choice"], "auto")


class GroupGenerationWorkflowTests(unittest.TestCase):
    def test_group_and_volume_source_summaries_group_chapter_files(self) -> None:
        chapter_numbers = ["0001", "0002", "0003", "0004", "0005"]

        group_lines = rewrite_workflow.group_generation_source_summary_lines(
            chapter_numbers,
        )
        volume_lines = rewrite_workflow.volume_review_source_summary_lines(
            {
                chapter_number: {
                    "file_name": f"{chapter_number}.txt",
                    "text": f"{chapter_number} 正文。",
                }
                for chapter_number in chapter_numbers
            }
        )
        joined_group = "\n".join(group_lines)
        joined_volume = "\n".join(volume_lines)

        self.assertIn("不注入参考源章节正文", joined_group)
        self.assertIn("已审核组纲", joined_group)
        self.assertNotIn("参考源章节文件", joined_group)
        self.assertNotIn("标题：", joined_group)
        self.assertNotIn("0001.txt（", joined_group)
        self.assertIn("正文总字符数约", joined_volume)
        self.assertIn("已生成章节文件[1]：0001.txt，0002.txt，0003.txt，0004.txt，0005.txt", joined_volume)
        self.assertNotIn("0001.txt（字符数约", joined_volume)

    def test_payload_context_summary_uses_actual_payload_content_lengths(self) -> None:
        payload = {
            "stable_injected_global_docs": {
                "world_model": {
                    "label": "世界模型",
                    "file_name": "01_world_model.md",
                    "file_path": "F:/project/global_injection/01_world_model.md",
                    "content": "世界模型正文",
                    "char_count": 999,
                }
            },
            "current_group_outline": {
                "label": "组纲（0001-0005）",
                "file_name": "0001_0005_group_outline.md",
                "file_path": "F:/project/group/0001_0005_group_outline.md",
                "content": "组纲正文",
            },
            "rewritten_chapters": {
                "0001": {
                    "file_name": "0001.txt",
                    "file_path": "F:/project/rewritten/0001.txt",
                    "text": "正文一二三",
                }
            },
            "update_target_files": [
                {
                    "file_key": "group_outline",
                    "label": "组纲",
                    "file_name": "0001_0005_group_outline.md",
                    "file_path": "F:/project/group/0001_0005_group_outline.md",
                    "current_content": "已有组纲一",
                    "current_char_count": 1000,
                    "preferred_mode": "edit_or_patch",
                }
            ],
        }

        input_lines = rewrite_workflow.payload_actual_input_summary_lines(payload)
        target_lines = rewrite_workflow.payload_target_file_summary_lines(payload)

        self.assertIn("稳定全局注入文档：世界模型（01_world_model.md）", "\n".join(input_lines))
        self.assertIn("字符数约 6", "\n".join(input_lines))
        self.assertNotIn("字符数约 999", "\n".join(input_lines))
        self.assertIn("当前组纲：组纲（0001-0005）（0001_0005_group_outline.md）", "\n".join(input_lines))
        self.assertIn("已生成章节正文：0001.txt", "\n".join(input_lines))
        self.assertIn("字符数约 5", "\n".join(target_lines))
        self.assertNotIn("字符数约 1000", "\n".join(target_lines))
        self.assertIn("建议工具=edit_or_patch", "\n".join(target_lines))

    def test_volume_index_defers_chapter_text_during_rewrite_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            volume_dir = Path(temp_dir) / "001"
            volume_dir.mkdir()
            for index in range(1, 51):
                chapter_number = f"{index:04d}"
                _write_text(volume_dir / f"{chapter_number}.txt", f"{chapter_number} 标题\n参考源正文 {chapter_number}。")
            _write_text(volume_dir / "volume_note.md", "补充资料。")

            real_read_text = novel_source_module.read_text
            read_names: list[str] = []

            def tracking_read_text(path: Path) -> str:
                read_names.append(Path(path).name)
                return real_read_text(path)

            with patch.object(novel_source_module, "read_text", side_effect=tracking_read_text):
                volume_index = rewrite_workflow.load_volume_index(volume_dir)

            self.assertEqual(len(volume_index["chapters"]), 50)
            self.assertTrue(all(chapter["text"] == "" for chapter in volume_index["chapters"]))
            self.assertIn("volume_note.md", read_names)
            self.assertNotIn("0001.txt", read_names)

            read_names.clear()
            self.assertEqual(read_names, [])

    def test_group_generation_payload_uses_reviewed_group_outline_as_input(self) -> None:
        chapter_numbers = ["0001", "0002", "0003", "0004", "0005"]
        volume_material = _volume_material(chapter_numbers)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            _seed_group_plan(project_root, [chapter_numbers])
            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0001")
            payload, _, _ = rewrite_workflow.build_group_generation_payload(
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_numbers=chapter_numbers,
                catalog=catalog,
            )

        target_keys = [item["file_key"] for item in payload["update_target_files"]]
        self.assertNotIn("group_outline", target_keys)
        self.assertNotIn("0001_chapter_outline", target_keys)
        self.assertEqual(payload["document_request"]["required_group_outline_file"], "0001_0005_group_outline.md")
        self.assertEqual(payload["current_group_outline"]["chapter_numbers"], chapter_numbers)
        self.assertNotIn("reference_chapter_metrics", payload)
        self.assertIn("不再读取参考源章节正文", "\n".join(payload["requirements"]))

    def test_dynamic_groups_come_from_reviewed_group_plan(self) -> None:
        chapter_numbers = [f"{index:04d}" for index in range(1, 15)]
        first_group = [f"{index:04d}" for index in range(1, 7)]
        second_group = [f"{index:04d}" for index in range(7, 15)]

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            _seed_group_plan(project_root, [first_group, second_group])
            volume_material = {**_volume_material(chapter_numbers), "project_root": str(project_root)}
            groups = rewrite_workflow.build_five_chapter_groups(volume_material)

            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0007")
            payload, _, _ = rewrite_workflow.build_group_generation_payload(
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_numbers=groups[1],
                catalog=catalog,
            )

        self.assertEqual(groups, [first_group, second_group])

        target_keys = [item["file_key"] for item in payload["update_target_files"]]
        self.assertEqual(payload["document_request"]["chapter_numbers"], second_group)
        self.assertEqual(payload["document_request"]["chapter_count"], 8)
        self.assertIn("0007_rewritten_chapter", target_keys)
        self.assertIn("0014_rewritten_chapter", target_keys)
        self.assertNotIn("0006_rewritten_chapter", target_keys)
        self.assertIn("章节组来自组纲计划", "\n".join(payload["requirements"]))

    def test_process_volume_runs_short_final_group_as_one_group(self) -> None:
        chapter_numbers = ["0001", "0002", "0003"]
        volume_material = _volume_material(chapter_numbers)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            _seed_group_plan(project_root, [chapter_numbers])
            with (
                patch.object(volume_runner_module, "run_group_generation_workflow") as generation,
                patch.object(volume_runner_module, "run_five_chapter_review") as review,
                patch.object(volume_runner_module, "print_progress"),
            ):
                completed_scope, next_target = volume_runner_module.process_volume_workflow(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                    run_mode=rewrite_workflow.RUN_MODE_GROUP,
            )

        self.assertEqual((completed_scope, next_target), ("group", None))
        generation.assert_called_once()
        review.assert_called_once()
        self.assertEqual(generation.call_args.kwargs["chapter_numbers"], ["0001", "0002", "0003"])
        self.assertEqual(review.call_args.kwargs["chapter_numbers"], ["0001", "0002", "0003"])

    def test_process_volume_runs_group_generation_then_group_review(self) -> None:
        chapter_numbers = ["0001", "0002", "0003", "0004", "0005"]
        passed_review = rewrite_workflow.WorkflowSubmissionPayload(
            passed=True,
            review_md="组审查通过。",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            volume_material = _volume_material(chapter_numbers)
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            _write_text(paths["book_outline"], "# 全书大纲\n")
            _write_text(paths["style_guide"], "# 文笔写作风格\n")
            _write_text(paths["world_model"], "# 世界模型\n")
            _write_text(paths["volume_outline"], "# 卷级大纲\n")
            _write_text(paths["volume_plot_progress"], "# 卷级剧情进程\n")
            _seed_group_plan(project_root, [chapter_numbers])

            def fake_generation_agent(*args, **kwargs):
                allowed_files = kwargs["allowed_files"]
                self.assertNotIn("group_outline", allowed_files)
                for chapter_number in chapter_numbers:
                    _write_text(
                        allowed_files[f"{chapter_number}_rewritten_chapter"],
                        f"{chapter_number} 仿写正文。",
                    )
                return _rewrite_agent_stage_result(
                    rewrite_workflow.WorkflowSubmissionPayload(
                        summary="动态组正文生成完成。",
                        generated_files=[f"{item}_rewritten_chapter" for item in chapter_numbers],
                    ),
                    "resp_group_generation_submit",
                    ["resp_group_generation_write", "resp_group_generation_submit"],
                    applications=[
                        _agent_application(
                            "submit_document_writes",
                            ["0001_rewritten_chapter"],
                        )
                    ],
                )

            with (
                patch.object(group_runner_module, "run_agent_stage", side_effect=fake_generation_agent) as generation_agent,
                patch.object(
                    chapter_review_module,
                    "run_agent_stage",
                    return_value=_rewrite_agent_stage_result(
                        passed_review,
                        "resp_group_review",
                        applications=[_agent_application("submit_document_edits", ["0001_rewritten_chapter"])],
                    ),
                ) as review_agent,
                patch.object(rewrite_workflow, "print_request_context_summary") as context_summary,
                patch.object(rewrite_workflow, "print_progress"),
                patch.object(chapter_shared_module, "print_progress") as agent_progress,
            ):
                completed_scope, next_target = rewrite_workflow.process_volume_workflow(
                    client=Mock(),
                    model="test-model",
                    rewrite_manifest=manifest,
                    volume_material=volume_material,
                    run_mode=rewrite_workflow.RUN_MODE_GROUP,
                    requested_chapter="0001",
                )

            outline_path = rewrite_workflow.group_outline_path(project_root, "001", chapter_numbers)
            outline_content = outline_path.read_text(encoding="utf-8")
            self.assertEqual((completed_scope, next_target), ("group", None))
            self.assertIn("# 0001-0005 组纲", outline_content)
            for chapter_number in chapter_numbers:
                self.assertIn(f"## {chapter_number}", outline_content)
                paths = rewrite_workflow.rewrite_paths(project_root, "001", chapter_number)
                self.assertTrue(paths["rewritten_chapter"].exists())
                self.assertFalse(paths["chapter_outline"].exists())
                self.assertEqual(
                    rewrite_workflow.get_chapter_state(manifest, "001", chapter_number)["last_stage"],
                    "group_generation",
                )

            batch_id = rewrite_workflow.five_chapter_batch_id(chapter_numbers)
            generation_state = rewrite_workflow.get_group_generation_state(manifest, "001", batch_id, chapter_numbers)
            review_state = rewrite_workflow.get_five_chapter_review_state(manifest, "001", batch_id, chapter_numbers)
            self.assertEqual(generation_state["status"], "passed")
            self.assertEqual(generation_state["group_outline_path"], str(outline_path))
            self.assertEqual(review_state["status"], "passed")
            self.assertEqual(generation_agent.call_count, 1)
            self.assertEqual(review_agent.call_count, 1)
            group_summary_call = next(
                call for call in context_summary.call_args_list
                if call.kwargs["request_label"].startswith("组生成")
            )
            included_docs = "\n".join(group_summary_call.kwargs["included_docs"])
            self.assertIn("[global] 全书大纲", included_docs)
            self.assertIn("[global] 世界模型", included_docs)
            self.assertIn("[volume] 卷级大纲", included_docs)
            self.assertIn("[volume] 卷级剧情进程", included_docs)
            self.assertIn("不注入参考源章节正文", "\n".join(group_summary_call.kwargs["source_summary_lines"]))
            progress_lines = "\n".join(str(call.args[0]) for call in agent_progress.call_args_list if call.args)
            self.assertIn("组生成 agent 本轮执行文档工具 1 次，累计变更=0001_rewritten_chapter。", progress_lines)
            self.assertIn("组生成 agent 提交阶段结果：generated_files=0001_rewritten_chapter", progress_lines)
            self.assertIn("组审查 agent 本轮执行文档工具 1 次，累计变更=0001_rewritten_chapter。", progress_lines)
            self.assertIn("组审查 agent 提交审核结论：通过；返修章节=无；返修目标=无；阻塞问题=0 项。", progress_lines)


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
                rewrite_workflow.llm_runtime,
                "call_function_tools",
                return_value=_multi_tool_from_operation(repaired_operation),
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
            self.assertEqual(
                [spec.name for spec in call_tools.call_args.kwargs["tool_specs"]],
                [spec.name for spec in rewrite_workflow.chapter_rewrite_stage_tool_specs()],
            )


class ReviewFixLoopTests(unittest.TestCase):
    def test_review_and_fix_current_goals_are_trailing_dynamic_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manifest = _manifest(project_root)
            chapter_numbers = ["0001", "0002", "0003", "0004", "0005"]
            volume_material = _volume_material(chapter_numbers)
            _seed_rewrite_files(project_root, chapter_numbers)
            catalog = rewrite_workflow.read_doc_catalog(project_root, "001", "0001")

            chapter_review_payload, _, _ = rewrite_workflow.build_phase_request_payload(
                phase_key=rewrite_workflow.PHASE3_REVIEW,
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                chapter_number="0001",
                catalog=catalog,
                chapter_text="当前章节正文。",
            )
            group_payload, _, _ = rewrite_workflow.build_five_chapter_review_payload(
                project_root=project_root,
                volume_material=volume_material,
                chapter_numbers=chapter_numbers,
                catalog=catalog,
                rewritten_chapters=rewrite_workflow.build_rewritten_chapters_payload(project_root, "001", chapter_numbers),
            )
            volume_payload, _, _ = rewrite_workflow.build_volume_review_payload(
                project_root=project_root,
                volume_material=volume_material,
                volume_number="001",
                catalog=catalog,
                rewritten_chapters=rewrite_workflow.build_rewritten_chapters_payload(project_root, "001", chapter_numbers),
            )
            failed_review = rewrite_workflow.WorkflowSubmissionPayload(
                passed=False,
                review_md="不通过。",
                blocking_issues=["正文问题"],
                rewrite_targets=["chapter_text"],
            )
            fix_payload = rewrite_workflow.build_review_fix_payload(
                review_kind="chapter",
                review=failed_review,
                allowed_files={"rewritten_chapter": rewrite_workflow.rewrite_paths(project_root, "001", "0001")["rewritten_chapter"]},
            )
            repair_payload = rewrite_workflow.build_document_operation_repair_payload(
                phase_key=rewrite_workflow.PHASE2_CHAPTER_TEXT,
                role="章节仿写修订作者",
                task="修正定位文本。",
                apply_error=ValueError("未找到 old_text"),
                failed_operation=rewrite_workflow.document_ops.DocumentOperationCallResult(
                    mode="edit",
                    response_id="resp_failed",
                    status="completed",
                    output_types=["function_call"],
                    preview="failed edit",
                    raw_body_text="",
                    raw_json={},
                ),
                allowed_files={"rewritten_chapter": rewrite_workflow.rewrite_paths(project_root, "001", "0001")["rewritten_chapter"]},
            )

        for payload in [chapter_review_payload, group_payload, volume_payload]:
            self.assertEqual(list(payload.keys())[-1], "latest_work_target")
            self.assertEqual(payload["latest_work_target"]["required_tool"], rewrite_workflow.WORKFLOW_SUBMISSION_TOOL_NAME)
            self.assertIn("必须调用 submit_workflow_result", payload["latest_work_target"]["instruction"])

        self.assertEqual(list(fix_payload.keys())[-1], "latest_work_target")
        self.assertEqual(fix_payload["latest_work_target"]["forbidden_tool"], rewrite_workflow.WORKFLOW_SUBMISSION_TOOL_NAME)
        self.assertIn("必须调用 write/edit/patch", fix_payload["latest_work_target"]["instruction"])

        self.assertEqual(list(repair_payload.keys())[-1], "latest_work_target")
        self.assertEqual(repair_payload["latest_work_target"]["forbidden_tool"], rewrite_workflow.WORKFLOW_SUBMISSION_TOOL_NAME)
        self.assertIn("最新工作目标", repair_payload["latest_work_target"]["instruction"])

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
                patch.object(rewrite_workflow.llm_runtime, "call_function_tools") as call_tools,
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
                patch.object(
                    rewrite_workflow.llm_runtime,
                    "call_function_tools",
                    return_value=_multi_tool_from_operation(fix_operation),
                ) as fix_call,
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
            fix_call.assert_called_once()
            self.assertEqual(fix_call.call_args.kwargs["instructions"], rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)
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

            def fake_group_agent(*args, **kwargs):
                if fake_group_agent.call_count == 0:
                    _write_text(
                        rewrite_workflow.rewrite_paths(project_root, "001", "0003")["rewritten_chapter"],
                        "0003 组审修复后的正文。",
                    )
                    fake_group_agent.call_count += 1
                    return _rewrite_agent_stage_result(
                        failed_review,
                        "resp_group_review_1",
                        ["resp_group_fix", "resp_group_review_1"],
                    )
                fake_group_agent.call_count += 1
                return _rewrite_agent_stage_result(passed_review, "resp_group_review_2")

            fake_group_agent.call_count = 0

            with (
                patch.object(
                    chapter_review_module,
                    "run_agent_stage",
                    side_effect=fake_group_agent,
                ) as agent_call,
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
            self.assertEqual(agent_call.call_count, 2)
            self.assertEqual(agent_call.call_args_list[0].kwargs["instructions"], rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)
            self.assertEqual(agent_call.call_args_list[1].kwargs["instructions"], rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)
            self.assertIsNone(agent_call.call_args_list[0].kwargs["previous_response_id"])
            self.assertEqual(agent_call.call_args_list[1].kwargs["previous_response_id"], "resp_group_review_1")
            self.assertNotIn("group_outline", agent_call.call_args_list[0].kwargs["allowed_files"])
            self.assertEqual(group_state["status"], "passed")
            self.assertEqual(group_state["last_response_id"], "resp_group_review_2")
            self.assertEqual(
                group_state["response_ids"],
                ["resp_group_fix", "resp_group_review_1", "resp_group_review_2"],
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
                    chapter_review_module,
                    "run_agent_stage",
                    return_value=_rewrite_agent_stage_result(passed_review, "resp_group_review_2"),
                ) as agent_call,
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
            agent_call.assert_called_once()
            self.assertEqual(agent_call.call_args.kwargs["previous_response_id"], "resp_group_fix")
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
            with (
                self.assertRaisesRegex(ValueError, "连续 10 次审核"),
                patch.object(
                    chapter_review_module,
                    "run_agent_stage",
                    side_effect=[
                        _rewrite_agent_stage_result(failed_review, f"resp_group_review_{index}")
                        for index in range(1, 11)
                    ],
                ) as agent_call,
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
            self.assertEqual(agent_call.call_count, 10)
            self.assertEqual(agent_call.call_args_list[9].kwargs["previous_response_id"], "resp_group_review_9")
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

            def fake_volume_agent(*args, **kwargs):
                if fake_volume_agent.call_count == 0:
                    _write_text(
                        rewrite_workflow.rewrite_paths(project_root, "001", "0002")["rewritten_chapter"],
                        "0002 卷审修复后的正文。",
                    )
                    fake_volume_agent.call_count += 1
                    return _rewrite_agent_stage_result(
                        failed_review,
                        "resp_volume_review_1",
                        ["resp_volume_fix", "resp_volume_review_1"],
                        applications=[
                            _agent_application(
                                "submit_document_edits",
                                ["0002_rewritten_chapter"],
                            )
                        ],
                    )
                fake_volume_agent.call_count += 1
                return _rewrite_agent_stage_result(passed_review, "resp_volume_review_2")

            fake_volume_agent.call_count = 0

            with (
                patch.object(
                    chapter_review_module,
                    "run_agent_stage",
                    side_effect=fake_volume_agent,
                ) as agent_call,
                patch.object(rewrite_workflow, "print_request_context_summary"),
                patch.object(rewrite_workflow, "print_progress"),
                patch.object(chapter_shared_module, "print_progress") as agent_progress,
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
            self.assertEqual(agent_call.call_count, 2)
            self.assertEqual(agent_call.call_args_list[0].kwargs["instructions"], rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)
            self.assertEqual(agent_call.call_args_list[1].kwargs["instructions"], rewrite_workflow.COMMON_CHAPTER_WORKFLOW_INSTRUCTIONS)
            self.assertIsNone(agent_call.call_args_list[0].kwargs["previous_response_id"])
            self.assertEqual(agent_call.call_args_list[1].kwargs["previous_response_id"], "resp_volume_review_1")
            self.assertIn("volume_review", agent_call.call_args_list[0].kwargs["allowed_files"])
            self.assertEqual(volume_state["status"], "passed")
            self.assertEqual(volume_state["last_response_id"], "resp_volume_review_2")
            self.assertEqual(
                volume_state["response_ids"],
                ["resp_volume_fix", "resp_volume_review_1", "resp_volume_review_2"],
            )
            self.assertIn("001", manifest["processed_volumes"])
            self.assertNotEqual(chapter_state.get("status"), "needs_revision")
            progress_lines = "\n".join(str(call.args[0]) for call in agent_progress.call_args_list if call.args)
            self.assertIn(
                "卷级审核 agent 本轮执行文档工具 1 次，累计变更=0002_rewritten_chapter。",
                progress_lines,
            )
            self.assertIn(
                "卷级审核 agent 提交审核结论：未通过；返修章节=0002；返修目标=0002:chapter_text；阻塞问题=1 项。",
                progress_lines,
            )
            self.assertIn(
                "卷级审核 agent 本轮未调用文档修复工具，直接提交审核结论。",
                progress_lines,
            )
            self.assertIn(
                "卷级审核 agent 提交审核结论：通过；返修章节=无；返修目标=无；阻塞问题=0 项。",
                progress_lines,
            )

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
                    chapter_review_module,
                    "run_agent_stage",
                    return_value=_rewrite_agent_stage_result(passed_review, "resp_volume_review_2"),
                ) as agent_call,
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
            agent_call.assert_called_once()
            self.assertEqual(agent_call.call_args.kwargs["previous_response_id"], "resp_volume_fix")
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
            with (
                self.assertRaisesRegex(ValueError, "连续 10 次审核"),
                patch.object(
                    chapter_review_module,
                    "run_agent_stage",
                    side_effect=[
                        _rewrite_agent_stage_result(failed_review, f"resp_volume_review_{index}")
                        for index in range(1, 11)
                    ],
                ) as agent_call,
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
            self.assertEqual(agent_call.call_count, 10)
            self.assertEqual(agent_call.call_args_list[9].kwargs["previous_response_id"], "resp_volume_review_9")
            self.assertEqual(volume_state["status"], "failed")
            self.assertEqual(volume_state["attempts"], 10)


class SupportUpdateScopeTests(unittest.TestCase):
    def test_support_update_targets_do_not_include_adaptation_owned_globals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            paths = rewrite_workflow.rewrite_paths(project_root, "001", "0001")
            target_paths = rewrite_workflow.support_update_target_paths(paths)
        self.assertNotIn("world_model", target_paths)
        self.assertNotIn("book_outline", target_paths)
        self.assertNotIn("style_guide", target_paths)

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

    def test_core_adaptation_docs_are_promoted_to_stable_global_docs(self) -> None:
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
                paths["foreshadowing"],
                paths["world_state"],
            ):
                file_path.parent.mkdir(parents=True, exist_ok=True)
            paths["book_outline"].write_text("# 全书大纲\n", encoding="utf-8")
            paths["style_guide"].write_text("# 文笔写作风格\n", encoding="utf-8")
            paths["world_model"].write_text("# 世界模型\n", encoding="utf-8")
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
            ],
        )
        self.assertNotIn("world_model", rolling_keys)
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
