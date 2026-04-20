from __future__ import annotations

import unittest

from novelist.cli import novel_adaptation_cli as adaptation_cli


class AdaptationDocumentPlanTests(unittest.TestCase):
    def test_first_volume_plan_includes_global_plot_progress_first(self) -> None:
        plan = adaptation_cli.build_document_plan("001")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "global_plot_progress",
                "style_guide",
                "world_design",
                "world_model",
                "book_outline",
                "foreshadowing",
                "volume_outline",
            ],
        )

    def test_later_volume_plan_includes_global_plot_progress_first(self) -> None:
        plan = adaptation_cli.build_document_plan("002")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "global_plot_progress",
                "world_design",
                "world_model",
                "book_outline",
                "foreshadowing",
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

    def test_global_plot_progress_file_number_is_five(self) -> None:
        self.assertEqual(adaptation_cli.GLOBAL_FILE_NAMES["global_plot_progress"], "05_global_plot_progress.md")

    def test_global_plot_progress_request_definition_is_storyline_planning(self) -> None:
        request = adaptation_cli.build_document_request("global_plot_progress")
        self.assertIn("全书故事线规划文档", request["task"])
        self.assertIn("三级标题", request["scope"])


class AdaptationInjectionOrderTests(unittest.TestCase):
    def test_build_injected_global_docs_uses_cache_friendly_generation_order(self) -> None:
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
                "global_plot_progress",
                "style_guide",
                "world_design",
                "world_model",
                "book_outline",
                "foreshadowing",
            ],
        )


if __name__ == "__main__":
    unittest.main()
