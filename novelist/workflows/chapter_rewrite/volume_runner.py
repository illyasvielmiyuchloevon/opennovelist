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
    volume_material = {**volume_material, "project_root": rewrite_manifest["project_root"]}
    while True:
        if requested_chapter:
            current_group = find_group_for_chapter(volume_material, requested_chapter)
            requested_chapter = None
        else:
            current_group = next_pending_group(volume_material, rewrite_manifest)

        if current_group is None:
            if run_mode == RUN_MODE_VOLUME:
                review_passed = run_volume_review(
                    client=client,
                    model=model,
                    rewrite_manifest=rewrite_manifest,
                    volume_material=volume_material,
                )
                if review_passed:
                    return ("volume", None)
                continue
            return (run_mode, None)

        print_progress(
            f"准备处理第 {volume_material['volume_number']} 卷 "
            f"{current_group[0]}-{current_group[-1]} 组（{len(current_group)} 章）。"
        )
        run_group_generation_workflow(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_numbers=current_group,
        )
        run_five_chapter_review(
            client=client,
            model=model,
            rewrite_manifest=rewrite_manifest,
            volume_material=volume_material,
            chapter_numbers=current_group,
        )

        next_group = next_group_after(volume_material, rewrite_manifest, current_group)
        if run_mode == RUN_MODE_CHAPTER:
            return ("chapter", next_group[0] if next_group else None)

        if run_mode == RUN_MODE_GROUP:
            return ("group", next_group)

__all__ = [
    'process_volume_workflow',
]
