from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from novelist.cli import novel_adaptation_cli as adaptation_cli
from novelist.cli import novel_chapter_rewrite_cli as rewrite_cli
from novelist.cli import novel_workflow_cli as workflow_cli
import novelist.core.openai_config as openai_config
from novelist.core.files import write_markdown_data


class WorkflowCliDetectionTests(unittest.TestCase):
    def test_detect_input_kind_identifies_raw_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_file = Path(temp_dir) / "book.txt"
            source_file.write_text("第1章\n内容\n", encoding="utf-8")
            self.assertEqual(workflow_cli.detect_input_kind(source_file), workflow_cli.INPUT_RAW_TEXT)

    def test_detect_input_kind_identifies_split_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            split_root = Path(temp_dir) / "book"
            volume_dir = split_root / "001"
            volume_dir.mkdir(parents=True)
            (volume_dir / "0001.txt").write_text("第1章\n内容\n", encoding="utf-8")
            self.assertEqual(workflow_cli.detect_input_kind(split_root), workflow_cli.INPUT_SPLIT_ROOT)

    def test_detect_input_kind_identifies_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source"
            project_root = Path(temp_dir) / "project"
            source_root.mkdir()
            (source_root / "001").mkdir()
            project_root.mkdir()
            write_markdown_data(
                project_root / adaptation_cli.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_cli.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_cli.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "last_processed_volume": None,
                },
                summary_lines=["new_book_title: 测试书"],
            )
            self.assertEqual(workflow_cli.detect_input_kind(project_root), workflow_cli.INPUT_PROJECT_ROOT)

    def test_resolve_workflow_entry_auto_picks_nested_raw_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir) / "workspace"
            parent.mkdir()
            source_file = parent / "book.txt"
            source_file.write_text("第1章\n内容\n", encoding="utf-8")
            resolved_path, kind = workflow_cli.resolve_workflow_entry(parent)
            self.assertEqual(resolved_path, source_file)
            self.assertEqual(kind, workflow_cli.INPUT_RAW_TEXT)

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
                project_root / adaptation_cli.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(split_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_cli.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_cli.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "last_processed_volume": None,
                },
                summary_lines=["new_book_title: 测试书"],
            )

            resolved_path, kind = workflow_cli.resolve_workflow_entry(parent)
            self.assertEqual(resolved_path, project_root)
            self.assertEqual(kind, workflow_cli.INPUT_PROJECT_ROOT)

    def test_pending_rewrite_volumes_reports_adapted_but_unfinished_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "source"
            project_root = root / "project"
            source_root.mkdir()
            project_root.mkdir()
            write_markdown_data(
                project_root / adaptation_cli.PROJECT_MANIFEST_NAME,
                title="Project Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界观",
                    "style": {"mode": adaptation_cli.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_cli.PROTAGONIST_MODE_ADAPTIVE, "description": None},
                    "total_volumes": 3,
                    "processed_volumes": ["001", "002"],
                    "last_processed_volume": "002",
                },
                summary_lines=["new_book_title: 测试书"],
            )
            write_markdown_data(
                project_root / rewrite_cli.REWRITE_MANIFEST_NAME,
                title="Chapter Rewrite Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "rewrite_output_root": str(project_root / rewrite_cli.REWRITTEN_ROOT_DIRNAME),
                    "processed_volumes": ["001"],
                    "last_processed_volume": "001",
                    "last_processed_chapter": "0005",
                    "chapter_states": {},
                    "volume_review_states": {},
                    "five_chapter_review_states": {},
                },
                summary_lines=["new_book_title: 测试书"],
            )

            self.assertEqual(workflow_cli.pending_rewrite_volumes(project_root), ["002"])


class WorkflowCliArgumentTests(unittest.TestCase):
    def build_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            new_title="玄幻忍者",
            target_worldview="玄幻修仙",
            style_mode=adaptation_cli.STYLE_MODE_SOURCE,
            style_file=None,
            protagonist_mode=adaptation_cli.PROTAGONIST_MODE_ADAPTIVE,
            protagonist_text=None,
            project_root="F:\\project",
            adaptation_volume="001",
            rewrite_volume="002",
            rewrite_chapter="0003",
            startup_mode=None,
            reconfigure_openai=False,
            dry_run=True,
        )

    def test_build_adaptation_cli_args(self) -> None:
        args = self.build_args()
        cli_args = workflow_cli.build_adaptation_cli_args(
            args,
            input_root=Path("F:/source"),
            run_mode=adaptation_cli.RUN_MODE_BOOK,
        )
        self.assertEqual(cli_args[0], "F:\\source")
        self.assertIn("--run-mode", cli_args)
        self.assertIn(adaptation_cli.RUN_MODE_BOOK, cli_args)
        self.assertIn("--new-title", cli_args)
        self.assertIn("玄幻忍者", cli_args)
        self.assertIn("--volume", cli_args)
        self.assertIn("001", cli_args)
        self.assertIn("--dry-run", cli_args)

    def test_build_adaptation_cli_args_supports_workflow_controlled_override(self) -> None:
        args = self.build_args()
        cli_args = workflow_cli.build_adaptation_cli_args(
            args,
            input_root=Path("F:/source"),
            run_mode=adaptation_cli.RUN_MODE_STAGE,
            workflow_controlled=True,
            volume_override="003",
        )
        self.assertIn("--workflow-controlled", cli_args)
        volume_index = cli_args.index("--volume")
        self.assertEqual(cli_args[volume_index + 1], "003")

    def test_build_rewrite_cli_args(self) -> None:
        args = self.build_args()
        cli_args = workflow_cli.build_rewrite_cli_args(
            args,
            project_root=Path("F:/project"),
            run_mode=rewrite_cli.RUN_MODE_GROUP,
        )
        self.assertEqual(cli_args[0], "F:\\project")
        self.assertIn("--run-mode", cli_args)
        self.assertIn(rewrite_cli.RUN_MODE_GROUP, cli_args)
        self.assertIn("--volume", cli_args)
        self.assertIn("002", cli_args)
        self.assertIn("--chapter", cli_args)
        self.assertIn("0003", cli_args)
        self.assertIn("--dry-run", cli_args)

    def test_build_rewrite_cli_args_supports_workflow_controlled_override(self) -> None:
        args = self.build_args()
        cli_args = workflow_cli.build_rewrite_cli_args(
            args,
            project_root=Path("F:/project"),
            run_mode=rewrite_cli.RUN_MODE_GROUP,
            workflow_controlled=True,
            volume_override="005",
        )
        self.assertIn("--workflow-controlled", cli_args)
        volume_index = cli_args.index("--volume")
        self.assertEqual(cli_args[volume_index + 1], "005")

    def test_resolve_startup_mode_uses_explicit_mode(self) -> None:
        args = self.build_args()
        args.startup_mode = workflow_cli.STARTUP_MODE_CONFIG_ONLY
        self.assertEqual(
            workflow_cli.resolve_startup_mode(args),
            workflow_cli.STARTUP_MODE_CONFIG_ONLY,
        )

    def test_resolve_startup_mode_uses_reconfigure_flag(self) -> None:
        args = self.build_args()
        args.reconfigure_openai = True
        self.assertEqual(
            workflow_cli.resolve_startup_mode(args),
            workflow_cli.STARTUP_MODE_CONFIG_AND_WORKFLOW,
        )

    def test_startup_mode_labels_cover_config_only(self) -> None:
        self.assertIn(
            workflow_cli.STARTUP_MODE_CONFIG_ONLY,
            workflow_cli.STARTUP_MODE_LABELS,
        )

    def test_openai_provider_labels_cover_compatible(self) -> None:
        self.assertIn(
            openai_config.PROVIDER_OPENAI_COMPATIBLE,
            openai_config.PROVIDER_LABELS,
        )

    def test_provider_default_protocol_for_compatible(self) -> None:
        self.assertEqual(
            openai_config.provider_default_protocol(openai_config.PROVIDER_OPENAI_COMPATIBLE),
            openai_config.PROTOCOL_OPENAI_COMPATIBLE,
        )


if __name__ == "__main__":
    unittest.main()
