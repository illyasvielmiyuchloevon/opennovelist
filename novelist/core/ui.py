from __future__ import annotations

import sys
from typing import NoReturn


def print_progress(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(message, file=stream, flush=True)


def pause_before_exit() -> None:
    if not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("按回车键退出...")
    except EOFError:
        pass


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def prompt_text(label: str, default: str | None = None) -> str:
    prompt = f"{label}"
    if default:
        prompt += f" [{default}]"
    prompt += "："
    value = input(prompt).strip()
    if value:
        return value
    if default is not None:
        return default
    fail(f"{label}不能为空。")


def prompt_choice(label: str, options: list[tuple[str, str]]) -> str:
    print(label)
    for index, (_, description) in enumerate(options, start=1):
        print(f"  {index}. {description}")

    while True:
        raw = input("请输入选项编号：").strip()
        if raw.isdigit():
            selected = int(raw)
            if 1 <= selected <= len(options):
                return options[selected - 1][0]
        print("输入无效，请重新输入选项编号。")
