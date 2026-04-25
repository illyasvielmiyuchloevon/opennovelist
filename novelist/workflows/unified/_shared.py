from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from novelist.workflows import novel_adaptation as adaptation_workflow
from novelist.workflows import novel_chapter_rewrite as rewrite_workflow
from novelist.workflows import split_novel
import novelist.core.openai_config as openai_config
from novelist.core.files import normalize_path
from novelist.core.ui import fail, pause_before_exit, print_progress, prompt_choice, prompt_text


INPUT_RAW_TEXT = "raw_text"
INPUT_SPLIT_ROOT = "split_root"
INPUT_PROJECT_ROOT = "project_root"
STARTUP_MODE_WORKFLOW = "workflow"
STARTUP_MODE_CONFIG_AND_WORKFLOW = "configure_and_workflow"
STARTUP_MODE_CONFIG_ONLY = "configure_only"
STARTUP_MODE_LABELS = {
    STARTUP_MODE_WORKFLOW: "直接进入统一工作流",
    STARTUP_MODE_CONFIG_AND_WORKFLOW: "先重新配置 OpenAI 设置，再进入统一工作流",
    STARTUP_MODE_CONFIG_ONLY: "只重新配置 OpenAI 设置",
}
WORKFLOW_SCOPE_FULL = "full"
WORKFLOW_SCOPE_CONTINUE_INTERRUPTED = "continue_interrupted"
WORKFLOW_SCOPE_CONTINUE_ADAPTATION = "continue_adaptation"
WORKFLOW_SCOPE_ADAPTATION_ONLY = "adaptation_only"
WORKFLOW_SCOPE_REWRITE_ONLY = "rewrite_only"
WORKDIR = Path(__file__).resolve().parents[3]
GLOBAL_CONFIG_PATH = adaptation_workflow.GLOBAL_CONFIG_PATH
LEGACY_GLOBAL_CONFIG_PATH = adaptation_workflow.LEGACY_GLOBAL_CONFIG_PATH


# Export imported helpers and workflow constants for the split modules.
__all__ = [name for name in globals() if not name.startswith("_")]
