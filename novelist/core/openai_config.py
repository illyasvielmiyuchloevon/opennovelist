from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from .files import load_json_file, merge_dict_updates, normalize_base_url, save_json_file
from .responses_runtime import build_openai_client
from .ui import fail, print_progress, prompt_choice, prompt_text


DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
PROVIDER_OPENAI = "openai"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROTOCOL_RESPONSES = "responses"
PROTOCOL_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_LABELS = {
    PROVIDER_OPENAI: "OpenAI 官方",
    PROVIDER_OPENAI_COMPATIBLE: "OpenAI Compatible（兼容提供商 / 自定义服务）",
}
PROTOCOL_LABELS = {
    PROTOCOL_RESPONSES: "OpenAI Responses API",
    PROTOCOL_OPENAI_COMPATIBLE: "OpenAI Compatible（兼容 OpenAI 接口）",
}


def provider_default_protocol(provider: str) -> str:
    if provider == PROVIDER_OPENAI_COMPATIBLE:
        return PROTOCOL_OPENAI_COMPATIBLE
    return PROTOCOL_RESPONSES


def infer_provider_from_base_url(base_url: str | None) -> str:
    normalized = normalize_base_url(base_url or DEFAULT_BASE_URL)
    if "api.openai.com" in normalized:
        return PROVIDER_OPENAI
    return PROVIDER_OPENAI_COMPATIBLE


def ordered_choice_options(
    options: list[tuple[str, str]],
    preferred_key: str | None,
) -> list[tuple[str, str]]:
    if not preferred_key:
        return options
    prioritized = [item for item in options if item[0] == preferred_key]
    rest = [item for item in options if item[0] != preferred_key]
    return [*prioritized, *rest]


def resolve_provider_protocol_metadata(
    *,
    cli_provider: str | None,
    cli_protocol: str | None,
    global_config: dict[str, Any],
    legacy_settings: dict[str, Any] | None = None,
    force_prompt: bool = False,
) -> tuple[str, str]:
    legacy_settings = legacy_settings or {}
    remembered_provider = str(global_config.get("last_provider") or "").strip()
    remembered_protocol = str(global_config.get("last_protocol") or "").strip()
    legacy_provider = str(legacy_settings.get("provider") or "").strip()
    legacy_protocol = str(legacy_settings.get("protocol") or "").strip()
    inferred_provider = infer_provider_from_base_url(
        str(global_config.get("last_base_url") or legacy_settings.get("base_url") or DEFAULT_BASE_URL)
    )

    provider = (cli_provider or "").strip() or remembered_provider or legacy_provider or inferred_provider
    if provider not in PROVIDER_LABELS:
        provider = inferred_provider

    protocol = (cli_protocol or "").strip() or remembered_protocol or legacy_protocol or provider_default_protocol(provider)
    if protocol not in PROTOCOL_LABELS:
        protocol = provider_default_protocol(provider)

    if force_prompt and sys.stdin and sys.stdin.isatty():
        provider = prompt_choice(
            "请选择 API 提供商",
            ordered_choice_options(
                [
                    (PROVIDER_OPENAI, f"{PROVIDER_LABELS[PROVIDER_OPENAI]}（官方服务）"),
                    (
                        PROVIDER_OPENAI_COMPATIBLE,
                        f"{PROVIDER_LABELS[PROVIDER_OPENAI_COMPATIBLE]}（支持自定义 base_url）",
                    ),
                ],
                provider,
            ),
        )
        protocol = prompt_choice(
            "请选择协议",
            ordered_choice_options(
                [
                    (PROTOCOL_RESPONSES, PROTOCOL_LABELS[PROTOCOL_RESPONSES]),
                    (PROTOCOL_OPENAI_COMPATIBLE, PROTOCOL_LABELS[PROTOCOL_OPENAI_COMPATIBLE]),
                ],
                protocol,
            ),
        )

    return provider, protocol


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


def force_reconfigure_openai(
    *,
    cli_provider: str | None,
    cli_protocol: str | None,
    cli_base_url: str | None,
    cli_api_key: str | None,
    cli_model: str | None,
    global_config: dict[str, Any],
    config_path: Path,
    env_var: str = "OPENAI_API_KEY",
    api_key_prompt: str = "重新输入 OpenAI API Key（明文显示）",
    base_url_prompt: str = "重新输入 OpenAI base_url",
    model_prompt: str = "重新输入模型名称",
) -> tuple[str, dict[str, str], dict[str, Any]]:
    provider, protocol = resolve_provider_protocol_metadata(
        cli_provider=cli_provider,
        cli_protocol=cli_protocol,
        global_config=global_config,
        force_prompt=True,
    )
    remembered_api_key = (
        (cli_api_key or "").strip()
        or os.getenv(env_var, "").strip()
        or str(global_config.get("last_api_key") or "").strip()
    )
    remembered_base_url = normalize_base_url(
        (cli_base_url or "").strip()
        or str(global_config.get("last_base_url") or "").strip()
        or DEFAULT_BASE_URL
    )
    remembered_model = (
        (cli_model or "").strip()
        or str(global_config.get("last_model") or "").strip()
        or DEFAULT_MODEL
    )

    api_key = prompt_text(api_key_prompt, remembered_api_key or None).strip()
    if not api_key:
        fail("未提供 OpenAI API Key。")

    base_url = normalize_base_url(prompt_text(base_url_prompt, remembered_base_url))
    model = prompt_text(model_prompt, remembered_model).strip()
    if not model:
        fail("未提供模型名称。")

    updated_config = update_global_config(
        config_path,
        global_config,
        {
            "last_api_key": api_key,
            "last_base_url": base_url,
            "last_model": model,
            "last_provider": provider,
            "last_protocol": protocol,
        },
    )
    return api_key, {"base_url": base_url, "model": model, "provider": provider, "protocol": protocol}, updated_config


def resolve_openai_settings(
    *,
    cli_provider: str | None = None,
    cli_protocol: str | None = None,
    cli_base_url: str | None,
    cli_model: str | None,
    global_config: dict[str, Any],
    config_path: Path,
    legacy_settings: dict[str, Any] | None = None,
    base_url_prompt: str = "输入 OpenAI base_url",
    model_prompt: str = "输入模型名称",
) -> tuple[dict[str, str], dict[str, Any]]:
    legacy_settings = legacy_settings or {}
    provider, protocol = resolve_provider_protocol_metadata(
        cli_provider=cli_provider,
        cli_protocol=cli_protocol,
        global_config=global_config,
        legacy_settings=legacy_settings,
        force_prompt=not (
            (cli_provider or "").strip()
            or str(global_config.get("last_provider") or "").strip()
            or str(legacy_settings.get("provider") or "").strip()
        ),
    )

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
        {
            "last_base_url": base_url,
            "last_model": model,
            "last_provider": provider,
            "last_protocol": protocol,
        },
    )
    return {
        "base_url": base_url,
        "model": model,
        "provider": provider,
        "protocol": protocol,
    }, updated_config


def create_openai_client(
    *,
    api_key: str,
    base_url: str,
    protocol: str = PROTOCOL_RESPONSES,
    provider: str = PROVIDER_OPENAI,
) -> OpenAI:
    client = build_openai_client(api_key=api_key, base_url=base_url)
    setattr(client, "_codex_protocol", protocol)
    setattr(client, "_codex_provider", provider)
    return client
