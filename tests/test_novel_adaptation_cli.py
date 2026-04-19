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


if __name__ == "__main__":
    unittest.main()
