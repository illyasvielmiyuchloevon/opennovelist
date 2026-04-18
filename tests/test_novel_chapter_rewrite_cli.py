from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from novelist.cli import novel_chapter_rewrite_cli as rewrite_cli


class ReviewPayloadNormalizationTests(unittest.TestCase):
    def test_finalize_review_payload_infers_passed_from_review_text(self) -> None:
        payload = rewrite_cli.WorkflowSubmissionPayload(
            review_md="## 一、总体结论\n本章**通过**。\n",
        )

        finalized = rewrite_cli.finalize_review_payload(payload, review_kind="chapter")

        self.assertTrue(finalized.passed)
        self.assertIn("# 章级审核", finalized.review_md)
        self.assertIn("## 总体结论", finalized.review_md)
        self.assertIn("**通过**", finalized.review_md)

    def test_finalize_group_review_payload_extracts_chapters_to_revise(self) -> None:
        payload = rewrite_cli.WorkflowSubmissionPayload(
            review_md="## 一、总体结论\n当前组审查不通过。\n需要返工章节：0003、0005。\n",
            blocking_issues=["当前组剧情推进与卷纲发生偏移。"],
        )

        finalized = rewrite_cli.finalize_review_payload(
            payload,
            review_kind="group",
            allowed_chapters=["0001", "0002", "0003", "0004", "0005"],
        )

        self.assertFalse(finalized.passed)
        self.assertEqual(finalized.chapters_to_revise, ["0003", "0005"])
        self.assertIn("# 组审查", finalized.review_md)
        self.assertIn("## 需要返工的章节", finalized.review_md)

    def test_finalize_review_payload_uses_content_md_as_fallback(self) -> None:
        payload = rewrite_cli.WorkflowSubmissionPayload(
            content_md="本章通过，可进入下一阶段。",
        )

        finalized = rewrite_cli.finalize_review_payload(payload, review_kind="chapter")

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
            catalog = rewrite_cli.read_doc_catalog(project_root, "001", "0001")
            with patch.object(
                rewrite_cli,
                "load_chapter_writing_skill_reference",
                return_value={"label": "写作规范 Skill", "content": "写作规范内容"},
            ):
                payload, _, _ = rewrite_cli.build_phase_request_payload(
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
            catalog = rewrite_cli.read_doc_catalog(project_root, "001", "0001")
            payload, _, _ = rewrite_cli.build_phase_request_payload(
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


if __name__ == "__main__":
    unittest.main()
