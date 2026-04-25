from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def process_volume_workflow(
    *,
    client: OpenAI,
    model: str,
    rewrite_manifest: dict[str, Any],
    volume_material: dict[str, Any],
    run_mode: str,
    requested_chapter: str | None = None,
) -> tuple[str, Any]:
    manual_requested_chapter = requested_chapter
    target_group = None
    if run_mode == RUN_MODE_GROUP:
        target_group = (
            find_group_for_chapter(volume_material, requested_chapter)
            if requested_chapter
            else next_pending_group(volume_material, rewrite_manifest)
        )

    while True:
        next_chapter = select_next_chapter(
            rewrite_manifest,
            volume_material,
            requested_chapter=manual_requested_chapter,
            allowed_chapters=target_group if run_mode == RUN_MODE_GROUP else None,
        )
        manual_requested_chapter = None

        if next_chapter is None:
            if run_mode == RUN_MODE_GROUP:
                if target_group is None:
                    return ("group", None)
                if not all_group_chapters_passed(rewrite_manifest, volume_material, target_group):
                    fail(f"当前组 {target_group[0]}-{target_group[-1]} 仍有章节未完成，但未识别到可处理章节。")
                if not run_due_five_chapter_reviews(
                    client=client,
                    model=model,
                    rewrite_manifest=rewrite_manifest,
                    volume_material=volume_material,
                    target_group=target_group,
                ):
                    continue
                return ("group", next_group_after(volume_material, rewrite_manifest, target_group))

            if not all_chapters_passed(rewrite_manifest, volume_material):
                fail(f"第 {volume_material['volume_number']} 卷仍有章节未完成，但未识别到可处理章节。")
            if not run_due_five_chapter_reviews(
                client=client,
                model=model,
                rewrite_manifest=rewrite_manifest,
                volume_material=volume_material,
            ):
                continue

            if run_mode == RUN_MODE_CHAPTER:
                return ("chapter", None)

            review_passed = run_volume_review(
                client=client,
                model=model,
                rewrite_manifest=rewrite_manifest,
                volume_material=volume_material,
            )
            if review_passed:
                return ("volume", None)
            continue

        print_progress(f"准备处理第 {volume_material['volume_number']} 卷第 {next_chapter} 章。")
        run_chapter_workflow(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_number=next_chapter,
        )

        if run_mode == RUN_MODE_CHAPTER:
            return ("chapter", select_next_chapter(rewrite_manifest, volume_material))

        if not run_due_five_chapter_reviews(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            target_group=target_group if run_mode == RUN_MODE_GROUP else None,
        ):
            continue

        if run_mode == RUN_MODE_GROUP and target_group is not None and group_review_passed(
            rewrite_manifest,
            volume_material["volume_number"],
            target_group,
        ):
            return ("group", next_group_after(volume_material, rewrite_manifest, target_group))

__all__ = [
    'process_volume_workflow',
]
