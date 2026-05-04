from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from novelist.core.files import extract_json_payload, write_markdown_data
from novelist.workflows import novel_adaptation as adaptation_workflow
from novelist.workflows import novel_chapter_rewrite as rewrite_workflow
from novelist.workflows import split_novel


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _chapter(number: int, size: int) -> split_novel.PartitionChapter:
    chapter_number = f"{number:04d}"
    return split_novel.PartitionChapter(
        chapter_number=chapter_number,
        file_name=f"{chapter_number}.txt",
        text=f"第{number}章\n" + ("x" * size),
        file_path=f"source/{chapter_number}.txt",
    )


class SplitNovelPartitionTests(unittest.TestCase):
    def test_partition_keeps_fifty_small_chapters_in_one_volume(self) -> None:
        plan = split_novel.partition_chapters_by_budget(
            [_chapter(index, 20) for index in range(1, 51)]
        )

        self.assertEqual(len(plan.volumes), 1)
        self.assertEqual(plan.volumes[0].volume_number, "001")
        self.assertEqual(len(plan.volumes[0].chapters), 50)
        self.assertFalse(plan.volumes[0].over_budget)

    def test_partition_pushes_tail_chapters_to_next_volume_when_over_budget(self) -> None:
        plan = split_novel.partition_chapters_by_budget(
            [_chapter(index, 4000) for index in range(1, 51)]
        )

        self.assertGreater(len(plan.volumes), 1)
        self.assertEqual(plan.volumes[0].volume_number, "001")
        self.assertLess(len(plan.volumes[0].chapters), 50)
        self.assertLessEqual(plan.volumes[0].source_char_count, split_novel.TARGET_VOLUME_SOURCE_CHARS)
        self.assertEqual(plan.volumes[1].chapters[0].chapter_number, f"{len(plan.volumes[0].chapters) + 1:04d}")

    def test_partition_allows_single_over_budget_chapter_with_warning(self) -> None:
        plan = split_novel.partition_chapters_by_budget([_chapter(1, 160_000)])

        self.assertEqual(len(plan.volumes), 1)
        self.assertTrue(plan.volumes[0].over_budget)
        self.assertTrue(plan.warnings)


class SourceRebalanceTests(unittest.TestCase):
    def test_rebalance_keeps_locked_volume_and_rewrites_current_forward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source"
            _write_text(source_root / "001" / "0001.txt", "第1章\nlocked\n")
            for index in range(2, 52):
                _write_text(source_root / "002" / f"{index:04d}.txt", f"第{index}章\n" + ("x" * 4000))

            report = split_novel.rebalance_source_volumes(
                source_root,
                start_volume="002",
                locked_volumes={"001"},
            )

            self.assertTrue(report.needed)
            self.assertTrue(report.changed)
            self.assertIsNotNone(report.backup_dir)
            self.assertTrue((source_root / "001" / "0001.txt").exists())
            self.assertTrue((source_root / "002").exists())
            self.assertTrue((source_root / "003").exists())
            self.assertLess(len(list((source_root / "002").glob("*.txt"))), 50)
            self.assertTrue((source_root / split_novel.SOURCE_REBALANCE_BACKUP_DIRNAME).exists())

    def test_adaptation_rebalance_backs_up_unfinished_outputs_and_records_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "source"
            project_root = root / "project"
            _write_text(source_root / "001" / "0001.txt", "第1章\nlocked\n")
            for index in range(2, 52):
                _write_text(source_root / "002" / f"{index:04d}.txt", f"第{index}章\n" + ("x" * 4000))
            stale_volume_dir = project_root / "volume_injection" / "002_volume_injection"
            _write_text(stale_volume_dir / "002_volume_outline.md", "# stale\n")
            rewrite_manifest_path = project_root / "00_chapter_rewrite_manifest.md"
            write_markdown_data(
                rewrite_manifest_path,
                title="Chapter Rewrite Manifest",
                payload={
                    "project_root": str(project_root),
                    "source_root": str(source_root),
                    "new_book_title": "测试书",
                    "rewrite_output_root": str(project_root / "rewritten_novel"),
                    "total_volumes": 2,
                    "processed_volumes": ["001"],
                    "last_processed_volume": "001",
                    "last_processed_chapter": "0001",
                    "chapter_states": {"002": {"0002": {"status": "passed"}}},
                    "group_generation_states": {"002": {"0002_0006": {"status": "passed"}}},
                    "five_chapter_review_states": {"002": {"0002_0006": {"status": "passed"}}},
                    "volume_review_states": {"002": {"status": "passed"}},
                },
                summary_lines=["test"],
            )
            manifest = {
                "project_root": str(project_root),
                "source_root": str(source_root),
                "new_book_title": "测试书",
                "target_worldview": "玄幻",
                "total_volumes": 2,
                "processed_volumes": ["001"],
                "last_processed_volume": "001",
            }

            with patch.object(adaptation_workflow, "print_progress"):
                volume_dirs = adaptation_workflow.prepare_source_volumes_for_adaptation(
                    source_root=source_root,
                    manifest=manifest,
                    target_volume=source_root / "002",
                    dry_run=False,
                )

            self.assertEqual([item.name for item in volume_dirs][:2], ["001", "002"])
            self.assertTrue((source_root / "003").exists())
            self.assertFalse(stale_volume_dir.exists())
            self.assertTrue((project_root / "source_rebalance_backups").exists())
            self.assertIn("source_rebalance_history", manifest)
            self.assertGreater(manifest["total_volumes"], 2)
            rewrite_manifest = extract_json_payload(rewrite_manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("002", rewrite_manifest["chapter_states"])
            self.assertNotIn("002", rewrite_manifest["group_generation_states"])

    def test_rewrite_guard_does_not_rebalance_or_block_chapter_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source"
            _write_text(source_root / "001" / "0001.txt", "第1章\nlocked\n")
            for index in range(2, 52):
                _write_text(source_root / "002" / f"{index:04d}.txt", f"第{index}章\n" + ("x" * 4000))

            with patch.object(rewrite_workflow, "print_progress") as progress_call:
                rewrite_workflow.ensure_source_volumes_stable_for_rewrite(
                    source_root=source_root,
                    project_manifest={"processed_volumes": ["001"]},
                    target_volume=source_root / "002",
                    dry_run=False,
                )

            self.assertFalse((source_root / "003").exists())
            progress_text = "\n".join(str(call.args[0]) for call in progress_call.call_args_list if call.args)
            self.assertIn("章节工作流不执行参考源自适应分卷检查", progress_text)


if __name__ == "__main__":
    unittest.main()
