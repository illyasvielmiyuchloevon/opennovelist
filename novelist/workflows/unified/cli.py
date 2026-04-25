from __future__ import annotations

from ._shared import *  # noqa: F401,F403


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "统一调度 split_novel、novel_adaptation、novel_chapter_rewrite，"
            "支持从原始小说文本、拆分后的书名目录或已有工程目录启动全流程。"
        )
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help="原始小说 txt、split_novel 的书名目录、或已有工程目录路径；不传时启动后提示输入。",
    )
    parser.add_argument("--new-title", help="新书名。")
    parser.add_argument("--target-worldview", help="目标世界观。")
    parser.add_argument(
        "--style-mode",
        choices=(adaptation_workflow.STYLE_MODE_CUSTOM, adaptation_workflow.STYLE_MODE_SOURCE),
        help="写作风格来源模式。",
    )
    parser.add_argument("--style-file", help="自定义写作风格文件路径。")
    parser.add_argument(
        "--protagonist-mode",
        choices=(adaptation_workflow.PROTAGONIST_MODE_CUSTOM, adaptation_workflow.PROTAGONIST_MODE_ADAPTIVE),
        help="主角设定来源模式。",
    )
    parser.add_argument("--protagonist-text", help="自定义主角设定和性格描述。")
    parser.add_argument("--project-root", help="工程目录路径。")
    parser.add_argument(
        "--adaptation-run-mode",
        choices=(adaptation_workflow.RUN_MODE_STAGE, adaptation_workflow.RUN_MODE_BOOK),
        help="novel_adaptation 的运行方式。",
    )
    parser.add_argument(
        "--rewrite-run-mode",
        choices=(rewrite_workflow.RUN_MODE_CHAPTER, rewrite_workflow.RUN_MODE_GROUP, rewrite_workflow.RUN_MODE_VOLUME),
        help="novel_chapter_rewrite 的运行方式。",
    )
    parser.add_argument("--adaptation-volume", help="只让 novel_adaptation 处理指定卷，例如 001。")
    parser.add_argument("--rewrite-volume", help="只让 novel_chapter_rewrite 处理指定卷，例如 001。")
    parser.add_argument("--rewrite-chapter", help="只让 novel_chapter_rewrite 处理指定章，例如 0001。")
    parser.add_argument("--base-url", help="OpenAI Responses API 的 base_url。")
    parser.add_argument("--api-key", help="OpenAI API Key。")
    parser.add_argument("--model", help="调用的模型名称。")
    parser.add_argument(
        "--provider",
        choices=(openai_config.PROVIDER_OPENAI, openai_config.PROVIDER_OPENAI_COMPATIBLE),
        help="API 提供商。",
    )
    parser.add_argument(
        "--protocol",
        choices=(openai_config.PROTOCOL_RESPONSES, openai_config.PROTOCOL_OPENAI_COMPATIBLE),
        help="API 协议。",
    )
    parser.add_argument(
        "--startup-mode",
        choices=(STARTUP_MODE_WORKFLOW, STARTUP_MODE_CONFIG_AND_WORKFLOW, STARTUP_MODE_CONFIG_ONLY),
        help="启动方式：直接进入工作流、先重新配置 OpenAI 再进入工作流、或只重新配置 OpenAI。",
    )
    parser.add_argument(
        "--reconfigure-openai",
        "--reset-openai-settings",
        dest="reconfigure_openai",
        action="store_true",
        help="重新设置并记住 base_url、api_key、model。",
    )
    parser.add_argument("--skip-split", action="store_true", help="跳过 split_novel 阶段。")
    parser.add_argument("--skip-adaptation", action="store_true", help="跳过 novel_adaptation 阶段。")
    parser.add_argument("--skip-rewrite", action="store_true", help="跳过 novel_chapter_rewrite 阶段。")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="对子流程传递 dry-run；split 阶段仍会真实执行本地拆分。",
    )
    return parser.parse_args()

__all__ = [
    'parse_args',
]
