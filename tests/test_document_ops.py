from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novelist.core import document_ops
from novelist.core import responses_runtime as llm_runtime
from novelist.core.files import (
    migrate_numbered_injection_dirs,
    migrate_renamed_files,
    read_text_if_exists,
    replace_text_with_fallbacks,
)


class FilesPatchTests(unittest.TestCase):
    def test_replace_text_with_fallbacks_matches_indentation_flexibly(self) -> None:
        content = "root:\n    child:\n        value: 1\n"
        old_text = "child:\n    value: 1"
        new_text = "child:\n    value: 2"

        updated = replace_text_with_fallbacks(content, old_text, new_text)

        self.assertIn("value: 2", updated)
        self.assertNotIn("value: 1", updated)

    def test_replace_text_with_fallbacks_matches_block_anchors_aggressively(self) -> None:
        content = "第一段。\n\n锚点起。\n真实中间内容。\n锚点止。\n\n最后段。"
        old_text = "锚点起。\n模型记错的中间内容。\n锚点止。"
        new_text = "锚点起。\n修订后的中间内容。\n锚点止。"

        updated = replace_text_with_fallbacks(content, old_text, new_text)

        self.assertIn("修订后的中间内容", updated)
        self.assertNotIn("真实中间内容", updated)

    def test_replace_text_with_fallbacks_matches_escaped_newlines(self) -> None:
        content = "第一句。\n第二句。\n第三句。"
        old_text = "第一句。\\n第二句。"
        new_text = "第一句。\n第二句已经修订。"

        updated = replace_text_with_fallbacks(content, old_text, new_text)

        self.assertIn("第二句已经修订。", updated)
        self.assertNotIn("第二句。\n第三句。", updated)

    def test_replace_text_with_fallbacks_ignores_missing_replace_all(self) -> None:
        content = "林玄已经离开天海道院。"

        updated = replace_text_with_fallbacks(content, "林奇", "林玄", replace_all=True)

        self.assertEqual(updated, content)

    def test_migrate_numbered_injection_dirs_moves_legacy_dirs_into_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            legacy_dir = root / "001_volume_injection"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            legacy_file = legacy_dir / "001_volume_outline.md"
            legacy_file.write_text("# 卷纲\n\n- 旧位置内容。\n", encoding="utf-8")

            container = migrate_numbered_injection_dirs(
                root,
                container_dirname="volume_injection",
                suffix="_volume_injection",
            )

            migrated_file = container / "001_volume_injection" / "001_volume_outline.md"
            self.assertTrue(container.exists())
            self.assertTrue(migrated_file.exists())
            self.assertFalse(legacy_dir.exists())
            self.assertIn("旧位置内容", migrated_file.read_text(encoding="utf-8"))

    def test_migrate_renamed_files_moves_legacy_global_file_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_world_model = root / "08_world_model.md"
            old_world_model.write_text("# 世界模型\n\n- 旧编号内容。\n", encoding="utf-8")

            warnings = migrate_renamed_files(
                root,
                {
                    "08_world_model.md": "02_world_model.md",
                    "04_foreshadowing.md": "05_foreshadowing.md",
                },
            )

            self.assertEqual(warnings, [])
            self.assertFalse(old_world_model.exists())
            self.assertTrue((root / "02_world_model.md").exists())
            self.assertIn("旧编号内容", (root / "02_world_model.md").read_text(encoding="utf-8"))

    def test_migrate_renamed_files_warns_when_new_and_old_differ(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_file = root / "08_global_plot_progress.md"
            new_file = root / "06_global_plot_progress.md"
            old_file.write_text("# 全局剧情进程\n\n- 旧内容。\n", encoding="utf-8")
            new_file.write_text("# 全局剧情进程\n\n- 新内容。\n", encoding="utf-8")

            warnings = migrate_renamed_files(
                root,
                {
                    "08_global_plot_progress.md": "06_global_plot_progress.md",
                },
            )

            self.assertEqual(len(warnings), 1)
            self.assertTrue(old_file.exists())
            self.assertTrue(new_file.exists())

    def test_apply_patch_edits_to_text_supports_multiple_edit_blocks(self) -> None:
        content = (
            "# 世界状态\n\n"
            "## 地点状态\n"
            "- 青岚城：表面平静。\n\n"
            "## 势力状态\n"
            "- 外门长老一系保持观望。\n"
        )
        edits = [
            document_ops.DocumentPatchEdit(
                action="replace",
                match_text="- 青岚城：表面平静。",
                new_text="- 青岚城：开始戒严，城内气氛转为紧张。",
            ),
            document_ops.DocumentPatchEdit(
                action="insert_after",
                match_text="## 势力状态\n- 外门长老一系保持观望。",
                new_text="\n- 黑市势力开始关注宗门内的异常动向。",
            ),
            document_ops.DocumentPatchEdit(
                action="append",
                match_text="",
                new_text="## 事件状态\n- 入门试炼结束后，异常玉符线索浮出水面。",
            ),
        ]

        updated = document_ops.apply_patch_edits_to_text(content, edits)

        self.assertIn("青岚城：开始戒严", updated)
        self.assertIn("黑市势力开始关注宗门内的异常动向", updated)
        self.assertIn("## 事件状态", updated)
        self.assertIn("异常玉符线索浮出水面", updated)

    def test_apply_patch_edits_to_text_skips_noop_replace(self) -> None:
        content = "# 世界状态\n\n## 地点状态\n- 青岚城：表面平静。\n"
        edits = [
            document_ops.DocumentPatchEdit(
                action="replace",
                match_text="- 青岚城：表面平静。",
                new_text="- 青岚城：表面平静。",
            ),
            document_ops.DocumentPatchEdit(
                action="append",
                match_text="",
                new_text="## 事件状态\n- 暂无新增事件。",
            ),
        ]

        updated = document_ops.apply_patch_edits_to_text(content, edits)

        self.assertIn("- 青岚城：表面平静。", updated)
        self.assertIn("## 事件状态", updated)


class DocumentOperationTests(unittest.TestCase):
    def test_apply_document_operation_edits_existing_file_precisely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            volume_progress = root / "001_volume_plot_progress.md"
            volume_progress.write_text(
                "# 卷级剧情进程\n\n"
                "## 试炼线\n"
                "### 起始\n"
                "- 主角进入试炼。\n\n"
                "### 已发生发展\n"
                "- 主角通过第一轮筛选。\n\n"
                "### 当前状态\n"
                "- 仍在试炼中。\n",
                encoding="utf-8",
            )

            operation = document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_edit",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                edit_payload=document_ops.DocumentEditPayload(
                    files=[
                        document_ops.DocumentEditFile(
                            file_key="volume_plot_progress",
                            edits=[
                                document_ops.DocumentEditEdit(
                                    old_text="- 仍在试炼中。",
                                    new_text="- 仍在试炼中，并开始接触核心对手。",
                                )
                            ],
                        )
                    ]
                ),
            )

            applied = document_ops.apply_document_operation(
                operation,
                allowed_files={"volume_plot_progress": volume_progress},
            )

            self.assertEqual(applied.mode, "edit")
            self.assertEqual(applied.changed_keys, ["volume_plot_progress"])
            updated = read_text_if_exists(volume_progress)
            self.assertIn("并开始接触核心对手", updated)
            self.assertIn("### 起始", updated)
            self.assertIn("### 已发生发展", updated)

    def test_apply_document_operation_can_target_file_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chapter_path = root / "0005.txt"
            chapter_path.write_text("第一段。\n\n第二段。\n", encoding="utf-8")

            operation = document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_path",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                edit_payload=document_ops.DocumentEditPayload(
                    files=[
                        document_ops.DocumentEditFile(
                            file_path=str(chapter_path),
                            edits=[
                                document_ops.DocumentEditEdit(
                                    old_text="第二段。",
                                    new_text="第二段已经修订。",
                                )
                            ],
                        )
                    ]
                ),
            )

            applied = document_ops.apply_document_operation(
                operation,
                allowed_files={"rewritten_chapter": chapter_path},
            )

            self.assertEqual(applied.changed_keys, ["rewritten_chapter"])
            self.assertIn("第二段已经修订。", chapter_path.read_text(encoding="utf-8"))

    def test_apply_document_operation_edit_replace_all_skips_missing_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            world_design = root / "01_world_design.md"
            world_design.write_text("林奇进入天海道院。\n", encoding="utf-8")

            operation = document_ops.DocumentOperationCallResult(
                mode="edit",
                response_id="resp_replace_all",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                edit_payload=document_ops.DocumentEditPayload(
                    files=[
                        document_ops.DocumentEditFile(
                            file_key="world_design",
                            edits=[
                                document_ops.DocumentEditEdit(
                                    old_text="林奇",
                                    new_text="林玄",
                                    replace_all=True,
                                ),
                                document_ops.DocumentEditEdit(
                                    old_text="不存在的人名",
                                    new_text="新名字",
                                    replace_all=True,
                                ),
                            ],
                        )
                    ]
                ),
            )

            applied = document_ops.apply_document_operation(
                operation,
                allowed_files={"world_design": world_design},
            )

            self.assertEqual(applied.changed_keys, ["world_design"])
            updated = world_design.read_text(encoding="utf-8")
            self.assertIn("林玄进入天海道院。", updated)
            self.assertNotIn("不存在的人名", updated)

    def test_document_edit_payload_accepts_external_field_names(self) -> None:
        payload = document_ops.DocumentEditPayload.model_validate(
            {
                "files": [
                    {
                        "filePath": "F:/books/0005.txt",
                        "edits": [
                            {
                                "oldString": "旧句子。",
                                "newString": "新句子。",
                                "replaceAll": True,
                            }
                        ],
                    }
                ]
            }
        )

        self.assertEqual(payload.files[0].file_path, "F:/books/0005.txt")
        self.assertEqual(payload.files[0].edits[0].old_text, "旧句子。")
        self.assertEqual(payload.files[0].edits[0].new_text, "新句子。")
        self.assertTrue(payload.files[0].edits[0].replace_all)

    def test_apply_document_operation_patches_multiple_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            global_plot = root / "06_global_plot_progress.md"
            world_state = root / "09_world_state.md"
            global_plot.write_text(
                "# 全局剧情进程\n\n## 主线进度\n- 主角刚刚进入宗门外门。\n",
                encoding="utf-8",
            )
            world_state.write_text(
                "# 世界状态\n\n## 地点状态\n- 青岚城：表面平静。\n",
                encoding="utf-8",
            )

            operation = document_ops.DocumentOperationCallResult(
                mode="patch",
                response_id="resp_test",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                patch_payload=document_ops.DocumentPatchPayload(
                    files=[
                        document_ops.DocumentPatchFile(
                            file_key="global_plot_progress",
                            edits=[
                                document_ops.DocumentPatchEdit(
                                    action="replace",
                                    match_text="- 主角刚刚进入宗门外门。",
                                    new_text="- 主角已通过试炼进入宗门外门，并发现异常玉符线索。",
                                )
                            ],
                        ),
                        document_ops.DocumentPatchFile(
                            file_key="world_state",
                            edits=[
                                document_ops.DocumentPatchEdit(
                                    action="replace",
                                    match_text="- 青岚城：表面平静。",
                                    new_text="- 青岚城：试炼结束后开始戒严。",
                                ),
                                document_ops.DocumentPatchEdit(
                                    action="append",
                                    match_text="",
                                    new_text="## 事件状态\n- 入门试炼刚刚结束。",
                                ),
                            ],
                        ),
                    ]
                ),
            )

            applied = document_ops.apply_document_operation(
                operation,
                allowed_files={
                    "global_plot_progress": global_plot,
                    "world_state": world_state,
                },
            )

            self.assertEqual(applied.mode, "patch")
            self.assertCountEqual(applied.emitted_keys, ["global_plot_progress", "world_state"])
            self.assertCountEqual(applied.changed_keys, ["global_plot_progress", "world_state"])
            self.assertIn("异常玉符线索", read_text_if_exists(global_plot))
            self.assertIn("试炼结束后开始戒严", read_text_if_exists(world_state))
            self.assertIn("## 事件状态", read_text_if_exists(world_state))

    def test_apply_document_operation_write_creates_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            outline_path = root / "001_volume_outline.md"

            operation = document_ops.DocumentOperationCallResult(
                mode="write",
                response_id="resp_write",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                write_payload=document_ops.DocumentWritePayload(
                    files=[
                        document_ops.DocumentWriteFile(
                            file_key="volume_outline",
                            content="# 卷级大纲\n\n- 本卷从入门试炼开始。\n",
                        )
                    ]
                ),
            )

            applied = document_ops.apply_document_operation(
                operation,
                allowed_files={"volume_outline": outline_path},
            )

            self.assertEqual(applied.mode, "write")
            self.assertEqual(applied.changed_keys, ["volume_outline"])
            self.assertTrue(outline_path.exists())
            self.assertIn("本卷从入门试炼开始", read_text_if_exists(outline_path))

    def test_apply_document_operation_patch_ignores_noop_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            world_state = root / "09_world_state.md"
            original = "# 世界状态\n\n## 地点状态\n- 青岚城：表面平静。\n"
            world_state.write_text(original, encoding="utf-8")

            operation = document_ops.DocumentOperationCallResult(
                mode="patch",
                response_id="resp_noop",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                patch_payload=document_ops.DocumentPatchPayload(
                    files=[
                        document_ops.DocumentPatchFile(
                            file_key="world_state",
                            edits=[
                                document_ops.DocumentPatchEdit(
                                    action="replace",
                                    match_text="- 青岚城：表面平静。",
                                    new_text="- 青岚城：表面平静。",
                                )
                            ],
                        )
                    ]
                ),
            )

            applied = document_ops.apply_document_operation(
                operation,
                allowed_files={"world_state": world_state},
            )

            self.assertEqual(applied.mode, "patch")
            self.assertEqual(applied.emitted_keys, ["world_state"])
            self.assertEqual(applied.changed_keys, [])
            self.assertEqual(read_text_if_exists(world_state), original)

    def test_apply_document_operation_patch_replace_all_skips_missing_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            world_state = root / "09_world_state.md"
            world_state.write_text("赤穹修行馆。\n", encoding="utf-8")

            operation = document_ops.DocumentOperationCallResult(
                mode="patch",
                response_id="resp_patch_replace_all",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                patch_payload=document_ops.DocumentPatchPayload(
                    files=[
                        document_ops.DocumentPatchFile(
                            file_key="world_state",
                            edits=[
                                document_ops.DocumentPatchEdit(
                                    action="replace",
                                    match_text="赤穹",
                                    new_text="玄穹",
                                    replace_all=True,
                                ),
                                document_ops.DocumentPatchEdit(
                                    action="replace",
                                    match_text="不存在术语",
                                    new_text="新术语",
                                    replace_all=True,
                                ),
                            ],
                        )
                    ]
                ),
            )

            applied = document_ops.apply_document_operation(
                operation,
                allowed_files={"world_state": world_state},
            )

            self.assertEqual(applied.changed_keys, ["world_state"])
            self.assertEqual(read_text_if_exists(world_state), "玄穹修行馆。\n")

    def test_apply_document_operation_write_rejects_existing_file_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            outline_path = root / "001_volume_outline.md"
            outline_path.write_text("# 卷级大纲\n\n- 旧内容。\n", encoding="utf-8")

            operation = document_ops.DocumentOperationCallResult(
                mode="write",
                response_id="resp_write_existing",
                status="completed",
                output_types=["function_call"],
                preview="",
                raw_body_text="",
                raw_json={},
                write_payload=document_ops.DocumentWritePayload(
                    files=[
                        document_ops.DocumentWriteFile(
                            file_key="volume_outline",
                            content="# 卷级大纲\n\n- 新内容。\n",
                        )
                    ]
                ),
            )

            with self.assertRaises(ValueError):
                document_ops.apply_document_operation(
                    operation,
                    allowed_files={"volume_outline": outline_path},
                )

    def test_apply_document_operation_rejects_unauthorized_file_key(self) -> None:
        operation = document_ops.DocumentOperationCallResult(
            mode="patch",
            response_id="resp_bad",
            status="completed",
            output_types=["function_call"],
            preview="",
            raw_body_text="",
            raw_json={},
            patch_payload=document_ops.DocumentPatchPayload(
                files=[
                    document_ops.DocumentPatchFile(
                        file_key="not_allowed",
                        edits=[
                            document_ops.DocumentPatchEdit(
                                action="append",
                                match_text="",
                                new_text="x",
                            )
                        ],
                    )
                ]
            ),
        )

        with self.assertRaises(ValueError):
            document_ops.apply_document_operation(
                operation,
                allowed_files={"world_state": Path("ignored.md")},
            )


class DocumentToolCallMappingTests(unittest.TestCase):
    def test_call_document_operation_tools_maps_edit_tool_result(self) -> None:
        fake_result = llm_runtime.MultiFunctionToolResult(
            tool_name=document_ops.DOCUMENT_EDIT_TOOL_NAME,
            parsed=document_ops.DocumentEditPayload(
                files=[
                    document_ops.DocumentEditFile(
                        file_key="volume_plot_progress",
                        edits=[
                            document_ops.DocumentEditEdit(
                                old_text="- 旧内容。",
                                new_text="- 新内容。",
                            )
                        ],
                    )
                ]
            ),
            response_id="resp_edit",
            status="completed",
            output_types=["function_call"],
            preview="preview",
            raw_body_text="raw",
            raw_json={},
        )

        with mock.patch.object(document_ops.llm_runtime, "call_function_tools", return_value=fake_result):
            result = document_ops.call_document_operation_tools(
                client=mock.Mock(),
                model="test-model",
                instructions="instructions",
                user_input="input",
            )

        self.assertEqual(result.mode, "edit")
        self.assertIsNotNone(result.edit_payload)
        self.assertEqual(result.edit_payload.files[0].file_key, "volume_plot_progress")

    def test_call_document_operation_tools_maps_patch_tool_result(self) -> None:
        fake_result = llm_runtime.MultiFunctionToolResult(
            tool_name=document_ops.DOCUMENT_PATCH_TOOL_NAME,
            parsed=document_ops.DocumentPatchPayload(
                files=[
                    document_ops.DocumentPatchFile(
                        file_key="world_state",
                        edits=[
                            document_ops.DocumentPatchEdit(
                                action="append",
                                match_text="",
                                new_text="## 事件状态\n- 测试事件。",
                            )
                        ],
                    )
                ]
            ),
            response_id="resp_patch",
            status="completed",
            output_types=["function_call"],
            preview="preview",
            raw_body_text="raw",
            raw_json={},
        )

        with mock.patch.object(document_ops.llm_runtime, "call_function_tools", return_value=fake_result):
            result = document_ops.call_document_operation_tools(
                client=mock.Mock(),
                model="test-model",
                instructions="instructions",
                user_input="input",
            )

        self.assertEqual(result.mode, "patch")
        self.assertIsNotNone(result.patch_payload)
        self.assertEqual(result.patch_payload.files[0].file_key, "world_state")

    def test_call_document_operation_tools_maps_write_tool_result(self) -> None:
        fake_result = llm_runtime.MultiFunctionToolResult(
            tool_name=document_ops.DOCUMENT_WRITE_TOOL_NAME,
            parsed=document_ops.DocumentWritePayload(
                files=[
                    document_ops.DocumentWriteFile(
                        file_key="book_outline",
                        content="# 全书大纲\n\n- 测试大纲。\n",
                    )
                ]
            ),
            response_id="resp_write",
            status="completed",
            output_types=["function_call"],
            preview="preview",
            raw_body_text="raw",
            raw_json={},
        )

        with mock.patch.object(document_ops.llm_runtime, "call_function_tools", return_value=fake_result):
            result = document_ops.call_document_operation_tools(
                client=mock.Mock(),
                model="test-model",
                instructions="instructions",
                user_input="input",
            )

        self.assertEqual(result.mode, "write")
        self.assertIsNotNone(result.write_payload)
        self.assertEqual(result.write_payload.files[0].file_key, "book_outline")


if __name__ == "__main__":
    unittest.main()
