from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from .files import load_json_file, merge_dict_updates, normalize_base_url, save_json_file
from .responses_runtime import build_openai_client
from .ui import fail, print_progress, prompt_text


DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")


def load_global_config(config_path: Path) -> dict[str, Any]:
    return load_json_file(config_path)


def save_global_config(config_path: Path, config: dict[str, Any]) -> None:
    save_json_file(config_path, config)


def update_global_config(
    config_path: Path,
    config: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    merged = merge_dict_updates(config, updates)
    save_global_config(config_path, merged)
    return merged


def resolve_api_key(
    *,
    cli_api_key: str | None,
    global_config: dict[str, Any],
    config_path: Path,
    env_var: str = "OPENAI_API_KEY",
) -> tuple[str, dict[str, Any]]:
    api_key = (cli_api_key or "").strip()
    if api_key:
        print_progress("已使用命令行传入的 API Key。")
        updated_config = update_global_config(config_path, global_config, {"last_api_key": api_key})
        return api_key, updated_config

    api_key = os.getenv(env_var, "").strip()
    if api_key:
        print_progress(f"已读取环境变量 {env_var}。")
        updated_config = update_global_config(config_path, global_config, {"last_api_key": api_key})
        return api_key, updated_config

    remembered_api_key = str(global_config.get("last_api_key") or "").strip()
    if remembered_api_key:
        print_progress("已加载已保存的 API Key。")
        return remembered_api_key, global_config

    api_key = input("请输入 OpenAI API Key（明文显示，仅首次需要，后续会记住）：").strip()
    if not api_key:
        fail("未提供 OpenAI API Key。")
    updated_config = update_global_config(config_path, global_config, {"last_api_key": api_key})
    return api_key, updated_config


def resolve_openai_settings(
    *,
    cli_base_url: str | None,
    cli_model: str | None,
    global_config: dict[str, Any],
    config_path: Path,
    legacy_settings: dict[str, Any] | None = None,
    base_url_prompt: str = "输入 OpenAI base_url",
    model_prompt: str = "输入模型名称",
) -> tuple[dict[str, str], dict[str, Any]]:
    legacy_settings = legacy_settings or {}

    raw_base_url = (cli_base_url or "").strip()
    if raw_base_url:
        base_url = normalize_base_url(raw_base_url)
        print_progress("已使用命令行传入的 base_url。")
    else:
        remembered_base_url = str(global_config.get("last_base_url") or "").strip()
        if remembered_base_url:
            base_url = normalize_base_url(remembered_base_url)
            print_progress("已加载全局保存的 base_url。")
        else:
            legacy_base_url = str(legacy_settings.get("base_url") or "").strip()
            if legacy_base_url:
                base_url = normalize_base_url(legacy_base_url)
                print_progress("已从旧项目配置迁移 base_url 到全局设置。")
            else:
                base_url = normalize_base_url(prompt_text(base_url_prompt, DEFAULT_BASE_URL))

    raw_model = (cli_model or "").strip()
    if raw_model:
        model = raw_model
        print_progress("已使用命令行传入的模型名称。")
    else:
        remembered_model = str(global_config.get("last_model") or "").strip()
        if remembered_model:
            model = remembered_model
            print_progress("已加载全局保存的模型名称。")
        else:
            legacy_model = str(legacy_settings.get("model") or "").strip()
            if legacy_model:
                model = legacy_model
                print_progress("已从旧项目配置迁移模型名称到全局设置。")
            else:
                model = prompt_text(model_prompt, DEFAULT_MODEL)

    updated_config = update_global_config(
        config_path,
        global_config,
        {"last_base_url": base_url, "last_model": model},
    )
    return {"base_url": base_url, "model": model}, updated_config


def create_openai_client(*, api_key: str, base_url: str) -> OpenAI:
    return build_openai_client(api_key=api_key, base_url=base_url)
