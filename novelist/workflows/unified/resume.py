from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def sorted_volume_numbers(volume_numbers: list[str]) -> list[str]:
    normalized = [str(item).zfill(3) for item in volume_numbers if str(item).strip()]
    return sorted(dict.fromkeys(normalized), key=lambda item: int(item))

def pending_rewrite_volumes(project_root: Path) -> list[str]:
    adaptation_manifest = adaptation_workflow.load_manifest(project_root)
    if adaptation_manifest is None:
        return []

    rewrite_manifest = rewrite_workflow.load_rewrite_manifest(project_root)
    adapted_volumes = sorted_volume_numbers(list(adaptation_manifest.get("processed_volumes", [])))
    rewritten_volumes = set(
        sorted_volume_numbers(list((rewrite_manifest or {}).get("processed_volumes", [])))
    )
    return [volume for volume in adapted_volumes if volume not in rewritten_volumes]

def pending_adaptation_volumes(project_root: Path) -> list[str]:
    adaptation_manifest = adaptation_workflow.load_manifest(project_root)
    if adaptation_manifest is None:
        return []

    processed_volumes = set(sorted_volume_numbers(list(adaptation_manifest.get("processed_volumes", []))))
    source_root_value = str(adaptation_manifest.get("source_root") or "").strip()
    source_volumes: list[str] = []
    if source_root_value:
        source_root = normalize_path(source_root_value)
        if source_root.exists():
            source_volumes = [volume_dir.name for volume_dir in adaptation_workflow.discover_volume_dirs(source_root)]

    if not source_volumes:
        try:
            total_volumes = int(adaptation_manifest.get("total_volumes") or 0)
        except (TypeError, ValueError):
            total_volumes = 0
        source_volumes = [f"{index:03d}" for index in range(1, total_volumes + 1)]

    return [volume for volume in sorted_volume_numbers(source_volumes) if volume not in processed_volumes]

def should_prompt_interrupted_workflow(
    args: argparse.Namespace,
    adaptation_backlog_volumes: list[str],
    rewrite_backlog_volumes: list[str],
) -> bool:
    return (
        bool(adaptation_backlog_volumes or rewrite_backlog_volumes)
        and not getattr(args, "input_path", None)
        and not getattr(args, "skip_adaptation", False)
        and not getattr(args, "skip_rewrite", False)
        and bool(sys.stdin)
        and sys.stdin.isatty()
    )

def prompt_interrupted_workflow_scope(
    adaptation_backlog_volumes: list[str],
    rewrite_backlog_volumes: list[str],
) -> str:
    options: list[tuple[str, str]] = []
    if adaptation_backlog_volumes:
        adaptation_label = "、".join(adaptation_backlog_volumes)
        print_progress(f"检测到资料适配尚未完成的卷：{adaptation_label}")
        options.append(
            (
                WORKFLOW_SCOPE_CONTINUE_ADAPTATION,
                f"继续资料适配断点（第 {adaptation_label} 卷）",
            )
        )
    if rewrite_backlog_volumes:
        rewrite_label = "、".join(rewrite_backlog_volumes)
        print_progress(f"检测到已有已适配但未完成重写的卷：{rewrite_label}")
        options.append(
            (
                WORKFLOW_SCOPE_CONTINUE_INTERRUPTED,
                f"继续章节重写断点（第 {rewrite_label} 卷）",
            )
        )
    options.append(("reselect", "重新选择工作模式"))
    choice = prompt_choice(
        "请选择本次统一入口的处理方式",
        options,
    )
    if choice in (WORKFLOW_SCOPE_CONTINUE_ADAPTATION, WORKFLOW_SCOPE_CONTINUE_INTERRUPTED):
        return choice

    return prompt_choice(
        "请选择本次要运行的工作模式",
        [
            (WORKFLOW_SCOPE_FULL, "完整流程（资料适配完成后继续章节重写）"),
            (WORKFLOW_SCOPE_ADAPTATION_ONLY, "只跑资料适配"),
            (WORKFLOW_SCOPE_REWRITE_ONLY, "只跑章节重写"),
        ],
    )

def resolve_workflow_scope(
    args: argparse.Namespace,
    adaptation_backlog_volumes: list[str],
    rewrite_backlog_volumes: list[str],
) -> str:
    if not adaptation_backlog_volumes and not rewrite_backlog_volumes:
        return WORKFLOW_SCOPE_FULL
    if getattr(args, "skip_adaptation", False) or getattr(args, "skip_rewrite", False):
        return WORKFLOW_SCOPE_FULL
    if not should_prompt_interrupted_workflow(args, adaptation_backlog_volumes, rewrite_backlog_volumes):
        if rewrite_backlog_volumes:
            return WORKFLOW_SCOPE_CONTINUE_INTERRUPTED
        return WORKFLOW_SCOPE_FULL
    return prompt_interrupted_workflow_scope(adaptation_backlog_volumes, rewrite_backlog_volumes)

def effective_stage_skips(args: argparse.Namespace, workflow_scope: str) -> tuple[bool, bool]:
    effective_skip_adaptation = bool(getattr(args, "skip_adaptation", False))
    effective_skip_rewrite = bool(getattr(args, "skip_rewrite", False))

    if workflow_scope == WORKFLOW_SCOPE_CONTINUE_INTERRUPTED:
        effective_skip_adaptation = True
    elif workflow_scope == WORKFLOW_SCOPE_CONTINUE_ADAPTATION:
        effective_skip_rewrite = True
    elif workflow_scope == WORKFLOW_SCOPE_ADAPTATION_ONLY:
        effective_skip_rewrite = True
    elif workflow_scope == WORKFLOW_SCOPE_REWRITE_ONLY:
        effective_skip_adaptation = True

    return effective_skip_adaptation, effective_skip_rewrite

def resolve_rewrite_volume_override(
    args: argparse.Namespace,
    *,
    adapted_volume_number: str | None,
    rewrite_backlog_volumes: list[str],
) -> str | None:
    rewrite_volume_override = args.rewrite_volume or adapted_volume_number
    if rewrite_volume_override is None and rewrite_backlog_volumes:
        rewrite_volume_override = rewrite_backlog_volumes[0]
    return rewrite_volume_override

__all__ = [
    'sorted_volume_numbers',
    'pending_rewrite_volumes',
    'pending_adaptation_volumes',
    'should_prompt_interrupted_workflow',
    'prompt_interrupted_workflow_scope',
    'resolve_workflow_scope',
    'effective_stage_skips',
    'resolve_rewrite_volume_override',
]
