from __future__ import annotations

import unittest

from novelist.cli import novel_adaptation_cli as adaptation_cli


class AdaptationDocumentPlanTests(unittest.TestCase):
    def test_first_volume_plan_includes_world_model(self) -> None:
        plan = adaptation_cli.build_document_plan("001")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
                "style_guide",
                "world_design",
                "world_model",
                "book_outline",
                "foreshadowing",
                "volume_outline",
            ],
        )

    def test_later_volume_plan_includes_world_model(self) -> None:
        plan = adaptation_cli.build_document_plan("002")
        keys = [item["key"] for item in plan]
        self.assertEqual(
            keys,
            [
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


if __name__ == "__main__":
    unittest.main()
