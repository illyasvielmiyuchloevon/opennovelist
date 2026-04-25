from __future__ import annotations

import argparse
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from novelist.workflows import novel_adaptation as adaptation_workflow
from novelist.workflows import novel_chapter_rewrite as rewrite_workflow
from novelist.workflows import novel_workflow as workflow_entry
import novelist.core.openai_config as openai_config
from novelist.core.files import write_markdown_data


class WorkflowCliDetectionTests(unittest.TestCase):
    def test_detect_input_kind_identifies_raw_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_file = Path(temp_dir) / "book.txt"
            source_file.write_text("第1章\n内容\n", encoding="utf-8")
            self.assertEqual(workflow_entry.detect_input_kind(source_file), workflow_entry.INPUT_RAW_TEXT)

    def test_detect_input_kind_identifies_split_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            split_root = Path(temp_dir) / "book"
            volume_dir = split_root / "001"
            volume_dir.mkdir(parents=True)
            (volume_dir / "0001.txt").write_text("第1章\n内容\n", encoding="utf-8")
            self.assertEqual(workflow_entry.detect_input_kind(split_root), workflow_entry.INPUT_SPLIT_ROOT)

    def test_detect_input_kind_identifies_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source"
            project_root = Path(temp_dir) / "project"
            source_root.mkdir()
            (source_root / "001").mkdir()
            project_root.mkdir()
            write_markdown_data(
                project_root / adaptation_workflow.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "last_processed_volume": None,
                },
                summary_lines=["new_book_title: 测试书"],
            )
            self.assertEqual(workflow_entry.detect_input_kind(project_root), workflow_entry.INPUT_PROJECT_ROOT)

    def test_resolve_workflow_entry_auto_picks_nested_raw_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir) / "workspace"
            parent.mkdir()
            source_file = parent / "book.txt"
            source_file.write_text("第1章\n内容\n", encoding="utf-8")
            resolved_path, kind = workflow_entry.resolve_workflow_entry(parent)
            self.assertEqual(resolved_path, source_file)
            self.assertEqual(kind, workflow_entry.INPUT_RAW_TEXT)

    def test_resolve_workflow_entry_prefers_project_over_split_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir) / "workspace"
            parent.mkdir()

            split_root = parent / "source_split"
            (split_root / "001").mkdir(parents=True)
            (split_root / "001" / "0001.txt").write_text("第1章\n内容\n", encoding="utf-8")

            project_root = parent / "project"
            project_root.mkdir()
            write_markdown_data(
                project_root / adaptation_workflow.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(split_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "last_processed_volume": None,
                },
                summary_lines=["new_book_title: 测试书"],
            )

            resolved_path, kind = workflow_entry.resolve_workflow_entry(parent)
            self.assertEqual(resolved_path, project_root)
            self.assertEqual(kind, workflow_entry.INPUT_PROJECT_ROOT)

    def test_try_resolve_existing_project_from_raw_text_prefers_bound_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source_file = workspace / "book.txt"
            source_file.write_text("第1章\n内容\n", encoding="utf-8")

            split_root = workspace / "book"
            (split_root / "001").mkdir(parents=True)
            (split_root / "001" / "0001.txt").write_text("第1章\n内容\n", encoding="utf-8")

            project_root = workspace / "project"
            project_root.mkdir()
            write_markdown_data(
                project_root / adaptation_workflow.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(split_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 1,
                    "processed_volumes": ["001"],
                    "last_processed_volume": "001",
                    "updated_at": "2026-04-24T12:00:00+08:00",
                },
                summary_lines=["new_book_title: 测试书"],
            )

            resolved_source_root, resolved_project_root = workflow_entry.try_resolve_existing_project_from_raw_text(
                source_file,
                None,
            )
            self.assertEqual(resolved_source_root, split_root)
            self.assertEqual(resolved_project_root, project_root)

    def test_pending_rewrite_volumes_reports_adapted_but_unfinished_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "source"
            project_root = root / "project"
            source_root.mkdir()
            project_root.mkdir()
            write_markdown_data(
                project_root / adaptation_workflow.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 3,
                    "processed_volumes": ["001", "002"],
                    "last_processed_volume": "002",
                },
                summary_lines=["new_book_title: 测试书"],
            )
            write_markdown_data(
                project_root / rewrite_workflow.REWRITE_MANIFEST_NAME,
                title="Chapter Rewrite Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "rewrite_output_root": str(project_root / rewrite_workflow.REWRITTEN_ROOT_DIRNAME),
                    "processed_volumes": ["001"],
                    "last_processed_volume": "001",
                    "last_processed_chapter": "0005",
                    "chapter_states": {},
                    "volume_review_states": {},
                    "five_chapter_review_states": {},
                },
                summary_lines=["new_book_title: 测试书"],
            )

            self.assertEqual(workflow_entry.pending_rewrite_volumes(project_root), ["002"])

    def test_pending_adaptation_volumes_reports_unprocessed_source_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "source"
            project_root = root / "project"
            (source_root / "001").mkdir(parents=True)
            (source_root / "002").mkdir(parents=True)
            (source_root / "003").mkdir(parents=True)
            project_root.mkdir()
            write_markdown_data(
                project_root / adaptation_workflow.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_workflow.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 3,
                    "processed_volumes": ["001"],
                    "last_processed_volume": "001",
                },
                summary_lines=["new_book_title: 测试书"],
            )

            self.assertEqual(workflow_entry.pending_adaptation_volumes(project_root), ["002", "003"])


class WorkflowCliArgumentTests(unittest.TestCase):
    class TtyInput:
        def isatty(self) -> bool:
            return True

    def build_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            input_path=None,
            new_title="玄幻忍者",
            target_worldview="玄幻修仙",
            style_mode=adaptation_workflow.STYLE_MODE_SOURCE,
            style_file=None,
            protagonist_mode=adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE,
            protagonist_text=None,
            project_root="F:\\project",
            adaptation_volume="001",
            rewrite_volume="002",
            rewrite_chapter="0003",
            startup_mode=None,
            reconfigure_openai=False,
            skip_adaptation=False,
            skip_rewrite=False,
            dry_run=True,
        )

    def test_build_adaptation_workflow_args(self) -> None:
        args = self.build_args()
        workflow_args = workflow_entry.build_adaptation_workflow_args(
            args,
            input_root=Path("F:/source"),
            run_mode=adaptation_workflow.RUN_MODE_BOOK,
        )
        self.assertEqual(workflow_args[0], "F:\\source")
        self.assertIn("--run-mode", workflow_args)
        self.assertIn(adaptation_workflow.RUN_MODE_BOOK, workflow_args)
        self.assertIn("--new-title", workflow_args)
        self.assertIn("玄幻忍者", workflow_args)
        self.assertIn("--volume", workflow_args)
        self.assertIn("001", workflow_args)
        self.assertIn("--dry-run", workflow_args)

    def test_build_adaptation_workflow_args_supports_workflow_controlled_override(self) -> None:
        args = self.build_args()
        workflow_args = workflow_entry.build_adaptation_workflow_args(
            args,
            input_root=Path("F:/source"),
            run_mode=adaptation_workflow.RUN_MODE_STAGE,
            workflow_controlled=True,
            volume_override="003",
        )
        self.assertIn("--workflow-controlled", workflow_args)
        volume_index = workflow_args.index("--volume")
        self.assertEqual(workflow_args[volume_index + 1], "003")

    def test_build_rewrite_workflow_args(self) -> None:
        args = self.build_args()
        workflow_args = workflow_entry.build_rewrite_workflow_args(
            args,
            project_root=Path("F:/project"),
            run_mode=rewrite_workflow.RUN_MODE_GROUP,
        )
        self.assertEqual(workflow_args[0], "F:\\project")
        self.assertIn("--run-mode", workflow_args)
        self.assertIn(rewrite_workflow.RUN_MODE_GROUP, workflow_args)
        self.assertIn("--volume", workflow_args)
        self.assertIn("002", workflow_args)
        self.assertIn("--chapter", workflow_args)
        self.assertIn("0003", workflow_args)
        self.assertIn("--dry-run", workflow_args)

    def test_build_rewrite_workflow_args_supports_workflow_controlled_override(self) -> None:
        args = self.build_args()
        workflow_args = workflow_entry.build_rewrite_workflow_args(
            args,
            project_root=Path("F:/project"),
            run_mode=rewrite_workflow.RUN_MODE_GROUP,
            workflow_controlled=True,
            volume_override="005",
        )
        self.assertIn("--workflow-controlled", workflow_args)
        volume_index = workflow_args.index("--volume")
        self.assertEqual(workflow_args[volume_index + 1], "005")

    def test_resolve_startup_mode_uses_explicit_mode(self) -> None:
        args = self.build_args()
        args.startup_mode = workflow_entry.STARTUP_MODE_CONFIG_ONLY
        self.assertEqual(
            workflow_entry.resolve_startup_mode(args),
            workflow_entry.STARTUP_MODE_CONFIG_ONLY,
        )

    def test_resolve_startup_mode_uses_reconfigure_flag(self) -> None:
        args = self.build_args()
        args.reconfigure_openai = True
        self.assertEqual(
            workflow_entry.resolve_startup_mode(args),
            workflow_entry.STARTUP_MODE_CONFIG_AND_WORKFLOW,
        )

    def test_resolve_startup_mode_with_input_path_skips_interactive_menu(self) -> None:
        args = self.build_args()
        args.input_path = "F:/books/source"
        self.assertEqual(
            workflow_entry.resolve_startup_mode(args),
            workflow_entry.STARTUP_MODE_WORKFLOW,
        )

    def test_run_modes_with_input_path_skip_interactive_menus(self) -> None:
        args = self.build_args()
        args.input_path = "F:/books/source"
        self.assertEqual(
            workflow_entry.resolve_adaptation_run_mode(args),
            adaptation_workflow.RUN_MODE_BOOK,
        )
        self.assertEqual(
            workflow_entry.resolve_rewrite_run_mode(args),
            rewrite_workflow.RUN_MODE_VOLUME,
        )

    def test_startup_mode_labels_cover_config_only(self) -> None:
        self.assertIn(
            workflow_entry.STARTUP_MODE_CONFIG_ONLY,
            workflow_entry.STARTUP_MODE_LABELS,
        )

    def test_interrupted_workflow_continue_scope_skips_adaptation(self) -> None:
        args = self.build_args()
        args.rewrite_volume = None

        with (
            mock.patch.object(workflow_entry.sys, "stdin", self.TtyInput()),
            mock.patch.object(
                workflow_entry,
                "prompt_choice",
                return_value=workflow_entry.WORKFLOW_SCOPE_CONTINUE_INTERRUPTED,
            ) as prompt_choice,
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, [], ["002"])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_CONTINUE_INTERRUPTED)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (True, False))
        self.assertEqual(
            workflow_entry.resolve_rewrite_volume_override(
                args,
                adapted_volume_number=None,
                rewrite_backlog_volumes=["002", "003"],
            ),
            "002",
        )
        prompt_choice.assert_called_once()

    def test_interrupted_workflow_reselect_full_keeps_both_stages(self) -> None:
        args = self.build_args()
        args.rewrite_volume = None

        with (
            mock.patch.object(workflow_entry.sys, "stdin", self.TtyInput()),
            mock.patch.object(
                workflow_entry,
                "prompt_choice",
                side_effect=["reselect", workflow_entry.WORKFLOW_SCOPE_FULL],
            ) as prompt_choice,
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, [], ["001"])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_FULL)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (False, False))
        self.assertEqual(
            workflow_entry.resolve_rewrite_volume_override(
                args,
                adapted_volume_number="004",
                rewrite_backlog_volumes=["001"],
            ),
            "004",
        )
        self.assertEqual(prompt_choice.call_count, 2)

    def test_interrupted_workflow_reselect_adaptation_only_skips_rewrite(self) -> None:
        args = self.build_args()

        with (
            mock.patch.object(workflow_entry.sys, "stdin", self.TtyInput()),
            mock.patch.object(
                workflow_entry,
                "prompt_choice",
                side_effect=["reselect", workflow_entry.WORKFLOW_SCOPE_ADAPTATION_ONLY],
            ),
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, [], ["001"])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_ADAPTATION_ONLY)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (False, True))

    def test_interrupted_workflow_reselect_rewrite_only_skips_adaptation(self) -> None:
        args = self.build_args()
        args.rewrite_volume = None

        with (
            mock.patch.object(workflow_entry.sys, "stdin", self.TtyInput()),
            mock.patch.object(
                workflow_entry,
                "prompt_choice",
                side_effect=["reselect", workflow_entry.WORKFLOW_SCOPE_REWRITE_ONLY],
            ),
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, [], ["003"])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_REWRITE_ONLY)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (True, False))
        self.assertEqual(
            workflow_entry.resolve_rewrite_volume_override(
                args,
                adapted_volume_number=None,
                rewrite_backlog_volumes=["003"],
            ),
            "003",
        )

    def test_interrupted_workflow_with_input_path_keeps_automatic_resume(self) -> None:
        args = self.build_args()
        args.input_path = "F:/books/source"

        with (
            mock.patch.object(workflow_entry.sys, "stdin", self.TtyInput()),
            mock.patch.object(workflow_entry, "prompt_choice") as prompt_choice,
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, ["003"], ["002"])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_CONTINUE_INTERRUPTED)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (True, False))
        prompt_choice.assert_not_called()

    def test_interrupted_workflow_explicit_skip_flags_are_not_overridden(self) -> None:
        args = self.build_args()
        args.skip_rewrite = True

        with (
            mock.patch.object(workflow_entry.sys, "stdin", self.TtyInput()),
            mock.patch.object(workflow_entry, "prompt_choice") as prompt_choice,
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, ["003"], ["002"])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_FULL)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (False, True))
        prompt_choice.assert_not_called()

    def test_interrupted_workflow_can_continue_adaptation_backlog(self) -> None:
        args = self.build_args()

        with (
            mock.patch.object(workflow_entry.sys, "stdin", self.TtyInput()),
            mock.patch.object(
                workflow_entry,
                "prompt_choice",
                return_value=workflow_entry.WORKFLOW_SCOPE_CONTINUE_ADAPTATION,
            ) as prompt_choice,
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, ["002"], ["001"])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_CONTINUE_ADAPTATION)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (False, True))
        prompt_choice.assert_called_once()

    def test_interrupted_workflow_noninteractive_adaptation_only_backlog_keeps_full_flow(self) -> None:
        args = self.build_args()

        with (
            mock.patch.object(workflow_entry.sys, "stdin", None),
            mock.patch.object(workflow_entry, "prompt_choice") as prompt_choice,
        ):
            workflow_scope = workflow_entry.resolve_workflow_scope(args, ["002"], [])

        self.assertEqual(workflow_scope, workflow_entry.WORKFLOW_SCOPE_FULL)
        self.assertEqual(workflow_entry.effective_stage_skips(args, workflow_scope), (False, False))
        prompt_choice.assert_not_called()

    def test_openai_provider_labels_cover_compatible(self) -> None:
        self.assertIn(
            openai_config.PROVIDER_OPENAI_COMPATIBLE,
            openai_config.PROVIDER_LABELS,
        )

    def test_global_config_loads_and_migrates_legacy_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_path = root / ".novel_adaptation_workflow" / "config.json"
            new_path = root / ".novel_adaptation" / "config.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text('{"last_model": "legacy-model"}\n', encoding="utf-8")

            loaded = openai_config.load_global_config(new_path, legacy_path=legacy_path)

            self.assertEqual(loaded["last_model"], "legacy-model")
            self.assertTrue(new_path.exists())

    def test_provider_default_protocol_for_compatible(self) -> None:
        self.assertEqual(
            openai_config.provider_default_protocol(openai_config.PROVIDER_OPENAI_COMPATIBLE),
            openai_config.PROTOCOL_OPENAI_COMPATIBLE,
        )


if __name__ == "__main__":
    unittest.main()
