from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from novelist.cli import novel_adaptation_cli as adaptation_cli


class AdaptationDocumentPlanTests(unittest.TestCase):
    def test_first_volume_plan_uses_new_generation_order(self) -> None:
        plan = adaptation_cli.build_document_plan("001")
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
        plan = adaptation_cli.build_document_plan("002")
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
        self.assertEqual(len(adaptation_cli.WORLD_MODEL_DEFAULT_SECTIONS), 16)
        self.assertEqual(adaptation_cli.WORLD_MODEL_DEFAULT_SECTIONS[-1], "可扩展世界专题")

    def test_world_model_scope_text_mentions_expansion_section(self) -> None:
        scope = adaptation_cli.world_model_scope_text()
        self.assertIn("可扩展世界专题", scope)
        self.assertIn("16 个二级标题", scope)
        self.assertIn("多个三级标题", scope)


class GlobalPlotProgressDefinitionTests(unittest.TestCase):
    def test_global_plot_progress_scope_mentions_storyline_structure(self) -> None:
        scope = adaptation_cli.global_plot_progress_scope_text()
        self.assertIn("全书级故事线规划文档", scope)
        self.assertIn("二级标题", scope)
        self.assertIn("三级标题", scope)
        self.assertIn("待推进", scope)

    def test_global_plot_progress_file_number_is_six(self) -> None:
        self.assertEqual(adaptation_cli.GLOBAL_FILE_NAMES["global_plot_progress"], "06_global_plot_progress.md")

    def test_global_plot_progress_request_definition_is_storyline_planning(self) -> None:
        request = adaptation_cli.build_document_request("global_plot_progress")
        self.assertIn("全书故事线规划文档", request["task"])
        self.assertIn("三级标题", request["scope"])


class AdaptationInjectionOrderTests(unittest.TestCase):
    def test_build_injected_global_docs_uses_requested_generation_order(self) -> None:
        injected = adaptation_cli.build_injected_global_docs(
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
        injected = adaptation_cli.build_injected_global_docs(
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

        with patch.object(adaptation_cli, "call_document_operation_response", side_effect=fake_call):
            adaptation_cli.generate_document_operation(
                client=None,  # type: ignore[arg-type]
                model="gpt-test",
                manifest={
                    "new_book_title": "测试书",
                    "target_worldview": "测试世界",
                    "total_volumes": 1,
                    "processed_volumes": [],
                    "style": {"mode": adaptation_cli.STYLE_MODE_SOURCE, "style_file": None},
                    "protagonist": {"mode": adaptation_cli.PROTAGONIST_MODE_ADAPTIVE, "description": None},
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
        payload = adaptation_cli.json.loads(str(user_input))
        self.assertIn("target_file", payload)
        self.assertNotIn("existing_style_guide", payload)
        self.assertNotIn("style_guide", payload["injected_global_docs"])


if __name__ == "__main__":
    unittest.main()
