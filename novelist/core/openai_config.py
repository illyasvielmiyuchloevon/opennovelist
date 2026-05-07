from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from openai import OpenAI

from .files import load_json_file, merge_dict_updates, normalize_base_url, save_json_file
from .responses_runtime import build_openai_client
from .ui import fail, print_progress, prompt_choice, prompt_text


DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
DEFAULT_OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_OPENCODE_GO_MODEL = "kimi-k2.6"
PROVIDER_OPENAI = "openai"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_OPENCODE_GO = "opencode_go"
PROTOCOL_RESPONSES = "responses"
PROTOCOL_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_LABELS = {
    PROVIDER_OPENAI: "OpenAI 官方",
    PROVIDER_OPENAI_COMPATIBLE: "OpenAI Compatible（兼容提供商 / 自定义服务）",
    PROVIDER_OPENCODE_GO: "OpenCode Go（官方订阅 API）",
}
PROTOCOL_LABELS = {
    PROTOCOL_RESPONSES: "OpenAI Responses API",
    PROTOCOL_OPENAI_COMPATIBLE: "OpenAI Compatible（兼容 OpenAI 接口）",
}
OPENAI_COMPATIBLE_EXTRA_BODY_CONFIG_KEY = "openai_compatible_extra_body"
OPENAI_COMPATIBLE_EXTRA_HEADERS_CONFIG_KEY = "openai_compatible_extra_headers"
OPENAI_COMPATIBLE_CACHE_READ_PATHS_CONFIG_KEY = "openai_compatible_cache_read_paths"
OPENAI_COMPATIBLE_CACHE_WRITE_PATHS_CONFIG_KEY = "openai_compatible_cache_write_paths"
OPENAI_COMPATIBLE_TRANSPORT_CONFIG_KEY = "openai_compatible_transport"
OPENAI_COMPATIBLE_REASONING_EFFORT_CONFIG_KEY = "openai_compatible_reasoning_effort"
OPENAI_COMPATIBLE_EXTRA_BODY_ENV = "NOVELIST_OPENAI_COMPATIBLE_EXTRA_BODY_JSON"
OPENAI_COMPATIBLE_EXTRA_HEADERS_ENV = "NOVELIST_OPENAI_COMPATIBLE_EXTRA_HEADERS_JSON"
OPENAI_COMPATIBLE_CACHE_READ_PATHS_ENV = "NOVELIST_OPENAI_COMPATIBLE_CACHE_READ_PATHS_JSON"
OPENAI_COMPATIBLE_CACHE_WRITE_PATHS_ENV = "NOVELIST_OPENAI_COMPATIBLE_CACHE_WRITE_PATHS_JSON"
OPENAI_COMPATIBLE_TRANSPORT_ENV = "NOVELIST_OPENAI_COMPATIBLE_TRANSPORT"
OPENAI_COMPATIBLE_REASONING_EFFORT_ENV = "NOVELIST_OPENAI_COMPATIBLE_REASONING_EFFORT"
OPENCODE_GO_MODELS_PATH = "/models"
OPENCODE_GO_MODELS_TIMEOUT_SECONDS = 20.0
OPENCODE_GO_DEFAULT_EXTRA_BODY = {
    "prompt_cache_key": "{{prompt_cache_key}}",
}
OPENCODE_GO_UNSUPPORTED_MESSAGES_MODEL_IDS = {
    # Official Go docs list these models on /messages (Anthropic SDK route).
    # Current local runtime for opencode_go uses chat/completions.
    "minimax-m2.7",
    "minimax-m2.5",
}
OPENCODE_GO_FALLBACK_MODEL_IDS = [
    "glm-5.1",
    "glm-5",
    "kimi-k2.5",
    "kimi-k2.6",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "mimo-v2.5",
    "mimo-v2.5-pro",
    "qwen3.6-plus",
    "qwen3.5-plus",
]


def provider_default_protocol(provider: str) -> str:
    if provider in {PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OPENCODE_GO}:
        return PROTOCOL_OPENAI_COMPATIBLE
    return PROTOCOL_RESPONSES


def provider_default_base_url(provider: str) -> str:
    if provider == PROVIDER_OPENCODE_GO:
        return DEFAULT_OPENCODE_GO_BASE_URL
    return DEFAULT_BASE_URL


def provider_default_model(provider: str) -> str:
    if provider == PROVIDER_OPENCODE_GO:
        return DEFAULT_OPENCODE_GO_MODEL
    return DEFAULT_MODEL


def _provider_changed_from_saved(
    *,
    provider: str,
    cli_provider: str | None,
    global_config: dict[str, Any],
    legacy_settings: dict[str, Any] | None = None,
) -> bool:
    explicit_provider = (cli_provider or "").strip()
    if not explicit_provider:
        return False
    legacy_settings = legacy_settings or {}
    remembered_provider = (
        str(global_config.get("last_provider") or "").strip()
        or str(legacy_settings.get("provider") or "").strip()
    )
    return bool(remembered_provider) and remembered_provider != provider


def infer_provider_from_base_url(base_url: str | None) -> str:
    normalized = normalize_base_url(base_url or DEFAULT_BASE_URL)
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").strip().lower()
    path = (parsed.path or "").strip().lower()
    if host == "opencode.ai" and "/zen/go/v1" in path:
        return PROVIDER_OPENCODE_GO
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


def _normalize_model_options(
    model_ids: list[str],
    preferred_model: str | None,
) -> list[tuple[str, str]]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in model_ids:
        model_id = str(item or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        cleaned.append(model_id)
    if not cleaned:
        cleaned = list(OPENCODE_GO_FALLBACK_MODEL_IDS)
    cleaned.sort()
    preferred = str(preferred_model or "").strip()
    if preferred and preferred in cleaned:
        cleaned = [preferred, *[item for item in cleaned if item != preferred]]
    return [(model_id, model_id) for model_id in cleaned]


def _filter_opencode_go_models_for_runtime(model_ids: list[str]) -> tuple[list[str], list[str]]:
    supported: list[str] = []
    unsupported: list[str] = []
    for model_id in model_ids:
        text = str(model_id or "").strip()
        if not text:
            continue
        if text in OPENCODE_GO_UNSUPPORTED_MESSAGES_MODEL_IDS:
            unsupported.append(text)
            continue
        supported.append(text)
    return supported, unsupported


def _extract_model_ids_from_models_payload(payload: Any) -> list[str]:
    candidates: list[str] = []

    def _append_from_item(item: Any) -> None:
        if isinstance(item, str):
            text = item.strip()
            if text:
                candidates.append(text)
            return
        if isinstance(item, dict):
            for key in ("id", "model", "name"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
                    return

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                _append_from_item(item)
        models = payload.get("models")
        if isinstance(models, list):
            for item in models:
                _append_from_item(item)
        if isinstance(models, dict):
            for key, value in models.items():
                if isinstance(value, dict):
                    nested_id = value.get("id")
                    if isinstance(nested_id, str) and nested_id.strip():
                        candidates.append(nested_id.strip())
                        continue
                candidates.append(str(key).strip())
    elif isinstance(payload, list):
        for item in payload:
            _append_from_item(item)
    return [item for item in candidates if item]


def fetch_opencode_go_model_ids(
    *,
    api_key: str,
    base_url: str,
) -> list[str]:
    key = str(api_key or "").strip()
    if not key:
        return []
    url = normalize_base_url(base_url).rstrip("/") + OPENCODE_GO_MODELS_PATH
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=OPENCODE_GO_MODELS_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as error:
        print_progress(f"OpenCode Go 模型列表拉取失败，将使用内置模型候选：{error}")
        return []
    return _extract_model_ids_from_models_payload(payload)


def select_opencode_go_model(
    *,
    api_key: str,
    base_url: str,
    preferred_model: str | None = None,
    prompt_label: str = "请选择 OpenCode Go 模型",
) -> str:
    model_ids = fetch_opencode_go_model_ids(api_key=api_key, base_url=base_url)
    model_ids, unsupported = _filter_opencode_go_models_for_runtime(model_ids)
    options = _normalize_model_options(model_ids, preferred_model)
    if unsupported:
        print_progress(
            "OpenCode Go 模型列表中存在当前运行时暂不支持的协议模型（/messages）："
            + ", ".join(sorted(set(unsupported)))
            + "；已在本项目中自动隐藏。"
        )
    if model_ids:
        print_progress(f"已从 OpenCode Go API 拉取模型列表：共 {len(options)} 个可选模型。")
    else:
        print_progress("未能从 OpenCode Go API 拉取模型列表，已回退到内置模型候选。")
    return prompt_choice(prompt_label, options)


def _parse_json_config_value(raw_value: Any, *, expected_type: type, label: str) -> Any:
    if raw_value is None:
        return None
    if isinstance(raw_value, expected_type):
        return raw_value
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except Exception:
            print_progress(f"已忽略无效的 {label}：不是合法 JSON。")
            return None
        if isinstance(parsed, expected_type):
            return parsed
    print_progress(f"已忽略无效的 {label}：期望 {expected_type.__name__}。")
    return None


def _normalize_usage_path_list(raw_value: Any, *, label: str) -> list[list[str]]:
    if raw_value is None:
        return []
    parsed = _parse_json_config_value(raw_value, expected_type=list, label=label)
    if parsed is None:
        return []
    normalized: list[list[str]] = []
    for item in parsed:
        parts: list[str] = []
        if isinstance(item, str):
            parts = [segment.strip() for segment in item.split(".") if segment.strip()]
        elif isinstance(item, list):
            parts = [str(segment).strip() for segment in item if str(segment).strip()]
        if parts:
            normalized.append(parts)
    return normalized


def _normalize_openai_compatible_transport(raw_value: Any) -> str:
    text = str(raw_value or "").strip().lower()
    if text in {"stream", "nonstream"}:
        return text
    return "stream"


def _normalize_openai_compatible_reasoning_effort(raw_value: Any) -> str | None:
    text = str(raw_value or "").strip().lower()
    if not text:
        return None
    if text in {"none", "minimal", "low", "medium", "high", "max", "xhigh"}:
        return text
    return None


def load_openai_compatible_options(
    global_config: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    extra_body_raw = os.getenv(OPENAI_COMPATIBLE_EXTRA_BODY_ENV, "").strip() or global_config.get(
        OPENAI_COMPATIBLE_EXTRA_BODY_CONFIG_KEY
    )
    extra_headers_raw = os.getenv(OPENAI_COMPATIBLE_EXTRA_HEADERS_ENV, "").strip() or global_config.get(
        OPENAI_COMPATIBLE_EXTRA_HEADERS_CONFIG_KEY
    )
    cache_read_paths_raw = os.getenv(OPENAI_COMPATIBLE_CACHE_READ_PATHS_ENV, "").strip() or global_config.get(
        OPENAI_COMPATIBLE_CACHE_READ_PATHS_CONFIG_KEY
    )
    cache_write_paths_raw = os.getenv(OPENAI_COMPATIBLE_CACHE_WRITE_PATHS_ENV, "").strip() or global_config.get(
        OPENAI_COMPATIBLE_CACHE_WRITE_PATHS_CONFIG_KEY
    )
    transport_raw = os.getenv(OPENAI_COMPATIBLE_TRANSPORT_ENV, "").strip() or global_config.get(
        OPENAI_COMPATIBLE_TRANSPORT_CONFIG_KEY
    )
    reasoning_effort_raw = os.getenv(OPENAI_COMPATIBLE_REASONING_EFFORT_ENV, "").strip() or global_config.get(
        OPENAI_COMPATIBLE_REASONING_EFFORT_CONFIG_KEY
    )

    extra_body = _parse_json_config_value(
        extra_body_raw,
        expected_type=dict,
        label=f"{OPENAI_COMPATIBLE_EXTRA_BODY_CONFIG_KEY}/{OPENAI_COMPATIBLE_EXTRA_BODY_ENV}",
    )
    extra_headers = _parse_json_config_value(
        extra_headers_raw,
        expected_type=dict,
        label=f"{OPENAI_COMPATIBLE_EXTRA_HEADERS_CONFIG_KEY}/{OPENAI_COMPATIBLE_EXTRA_HEADERS_ENV}",
    )
    cache_read_paths = _normalize_usage_path_list(
        cache_read_paths_raw,
        label=f"{OPENAI_COMPATIBLE_CACHE_READ_PATHS_CONFIG_KEY}/{OPENAI_COMPATIBLE_CACHE_READ_PATHS_ENV}",
    )
    cache_write_paths = _normalize_usage_path_list(
        cache_write_paths_raw,
        label=f"{OPENAI_COMPATIBLE_CACHE_WRITE_PATHS_CONFIG_KEY}/{OPENAI_COMPATIBLE_CACHE_WRITE_PATHS_ENV}",
    )
    transport = _normalize_openai_compatible_transport(transport_raw)
    reasoning_effort = _normalize_openai_compatible_reasoning_effort(reasoning_effort_raw)

    options: dict[str, Any] = {}
    normalized_extra_body = dict(extra_body) if isinstance(extra_body, dict) else {}
    if provider == PROVIDER_OPENCODE_GO:
        has_prompt_cache_key = bool(str(normalized_extra_body.get("prompt_cache_key") or "").strip())
        if not has_prompt_cache_key:
            normalized_extra_body = {
                **OPENCODE_GO_DEFAULT_EXTRA_BODY,
                **normalized_extra_body,
            }
    if normalized_extra_body:
        options["extra_body"] = normalized_extra_body
    if extra_headers:
        options["extra_headers"] = {str(key): str(value) for key, value in extra_headers.items()}
    if cache_read_paths:
        options["cache_read_paths"] = cache_read_paths
    if cache_write_paths:
        options["cache_write_paths"] = cache_write_paths
    options["transport"] = transport
    if reasoning_effort:
        options["reasoning_effort"] = reasoning_effort
    return options


def _contains_prompt_cache_key_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return "{{prompt_cache_key}}" in value
    if isinstance(value, list):
        return any(_contains_prompt_cache_key_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_prompt_cache_key_placeholder(item) for item in value.values())
    return False


def openai_compatible_cache_summary_lines(settings: dict[str, Any]) -> list[str]:
    if str(settings.get("protocol") or "").strip() != PROTOCOL_OPENAI_COMPATIBLE:
        return []

    options = settings.get("openai_compatible_options")
    compatible_options = dict(options) if isinstance(options, dict) else {}
    provider = str(settings.get("provider") or "").strip()
    extra_body = compatible_options.get("extra_body")
    extra_headers = compatible_options.get("extra_headers")
    cache_read_paths = compatible_options.get("cache_read_paths")
    cache_write_paths = compatible_options.get("cache_write_paths")
    transport = str(compatible_options.get("transport") or "stream").strip().lower() or "stream"
    has_provider_extras = isinstance(extra_body, dict) and bool(extra_body) or isinstance(extra_headers, dict) and bool(extra_headers)
    passes_prompt_cache_key = _contains_prompt_cache_key_placeholder(extra_body) or _contains_prompt_cache_key_placeholder(
        extra_headers
    )

    lines: list[str] = []
    if passes_prompt_cache_key:
        lines.append("OpenAI Compatible 缓存：已通过 provider-specific extra_body/extra_headers 透传 {{prompt_cache_key}}。")
    elif has_provider_extras:
        lines.append(
            "OpenAI Compatible 缓存：已配置 provider-specific 额外参数，但未引用 {{prompt_cache_key}}；是否命中取决于上游兼容服务。"
        )
    else:
        lines.append(
            "OpenAI Compatible 缓存：当前未配置 provider-specific 缓存参数；兼容协议本身没有统一的 prompt cache 请求字段。"
        )

    custom_usage_paths: list[str] = []
    if isinstance(cache_read_paths, list) and cache_read_paths:
        custom_usage_paths.append(f"read={len(cache_read_paths)}")
    if isinstance(cache_write_paths, list) and cache_write_paths:
        custom_usage_paths.append(f"write={len(cache_write_paths)}")
    if custom_usage_paths:
        lines.append(
            "OpenAI Compatible 缓存统计：已启用 provider-specific usage 路径解析（" + "，".join(custom_usage_paths) + "）。"
        )
    else:
        lines.append("OpenAI Compatible 缓存统计：未配置自定义 usage 路径，将只识别标准缓存字段。")
    if provider == PROVIDER_OPENCODE_GO:
        lines.append("OpenCode Go：默认开启 prompt_cache_key 透传，以贴近官方 agent 的缓存键策略。")
    if transport == "stream":
        lines.append("OpenAI Compatible 传输：默认使用流式 chat.completions，与 opencode 主会话链路一致。")
    else:
        lines.append("OpenAI Compatible 传输：已显式切到非流式 chat.completions，作为兼容网关的手动兜底模式。")
    return lines


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
    if provider == PROVIDER_OPENCODE_GO:
        # OpenCode Go is an OpenAI-compatible official endpoint in this project.
        # Keep protocol fixed to avoid misleading dual-protocol prompts.
        protocol = PROTOCOL_OPENAI_COMPATIBLE

    if force_prompt and sys.stdin and sys.stdin.isatty():
        provider = prompt_choice(
            "请选择 API 提供商",
            ordered_choice_options(
                [
                    (PROVIDER_OPENAI, f"{PROVIDER_LABELS[PROVIDER_OPENAI]}（官方服务）"),
                    (
                        PROVIDER_OPENCODE_GO,
                        f"{PROVIDER_LABELS[PROVIDER_OPENCODE_GO]}（默认 {DEFAULT_OPENCODE_GO_BASE_URL}）",
                    ),
                    (
                        PROVIDER_OPENAI_COMPATIBLE,
                        f"{PROVIDER_LABELS[PROVIDER_OPENAI_COMPATIBLE]}（支持自定义 base_url）",
                    ),
                ],
                provider,
            ),
        )
        if provider == PROVIDER_OPENCODE_GO:
            protocol = PROTOCOL_OPENAI_COMPATIBLE
        else:
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


def load_global_config(config_path: Path, *, legacy_path: Path | None = None) -> dict[str, Any]:
    if config_path.exists():
        return load_json_file(config_path)
    if legacy_path is not None and legacy_path.exists():
        loaded = load_json_file(legacy_path)
        if loaded:
            save_json_file(config_path, loaded)
        return loaded
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

    api_key = input("请输入 Provider API Key（明文显示，仅首次需要，后续会记住）：").strip()
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
    api_key_prompt: str = "重新输入 Provider API Key（明文显示）",
    base_url_prompt: str = "重新输入 Provider base_url",
    model_prompt: str = "重新输入模型名称",
) -> tuple[str, dict[str, Any], dict[str, Any]]:
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
    provider_switched = _provider_changed_from_saved(
        provider=provider,
        cli_provider=cli_provider,
        global_config=global_config,
    )
    remembered_base_url = normalize_base_url(
        (cli_base_url or "").strip()
        or (
            str(global_config.get("last_base_url") or "").strip()
            if not provider_switched
            else ""
        )
        or provider_default_base_url(provider)
    )
    remembered_model = (
        (cli_model or "").strip()
        or (
            str(global_config.get("last_model") or "").strip()
            if not provider_switched
            else ""
        )
        or provider_default_model(provider)
    )

    api_key = prompt_text(api_key_prompt, remembered_api_key or None).strip()
    if not api_key:
        fail("未提供 OpenAI API Key。")

    if provider == PROVIDER_OPENCODE_GO and not (cli_base_url or "").strip():
        base_url = provider_default_base_url(provider)
        print_progress(f"OpenCode Go 已使用官方固定 base_url：{base_url}")
    else:
        base_url = normalize_base_url(prompt_text(base_url_prompt, remembered_base_url))
    if (cli_model or "").strip():
        model = str(cli_model or "").strip()
    elif provider == PROVIDER_OPENCODE_GO:
        model = select_opencode_go_model(
            api_key=api_key,
            base_url=base_url,
            preferred_model=remembered_model or provider_default_model(provider),
            prompt_label="请选择 OpenCode Go 模型",
        )
    else:
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
    settings: dict[str, Any] = {
        "base_url": base_url,
        "model": model,
        "provider": provider,
        "protocol": protocol,
    }
    if protocol == PROTOCOL_OPENAI_COMPATIBLE:
        compatible_options = load_openai_compatible_options(updated_config, provider=provider)
        if compatible_options:
            settings["openai_compatible_options"] = compatible_options
    return api_key, settings, updated_config


def resolve_openai_settings(
    *,
    cli_provider: str | None = None,
    cli_protocol: str | None = None,
    cli_base_url: str | None,
    cli_model: str | None,
    api_key: str | None = None,
    global_config: dict[str, Any],
    config_path: Path,
    legacy_settings: dict[str, Any] | None = None,
    base_url_prompt: str = "输入 Provider base_url",
    model_prompt: str = "输入模型名称",
) -> tuple[dict[str, Any], dict[str, Any]]:
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
    provider_switched = _provider_changed_from_saved(
        provider=provider,
        cli_provider=cli_provider,
        global_config=global_config,
        legacy_settings=legacy_settings,
    )

    raw_base_url = (cli_base_url or "").strip()
    if raw_base_url:
        base_url = normalize_base_url(raw_base_url)
        print_progress("已使用命令行传入的 base_url。")
    elif provider == PROVIDER_OPENCODE_GO:
        base_url = provider_default_base_url(provider)
        print_progress(f"OpenCode Go 已使用官方固定 base_url：{base_url}")
    else:
        remembered_base_url = "" if provider_switched else str(global_config.get("last_base_url") or "").strip()
        if remembered_base_url:
            base_url = normalize_base_url(remembered_base_url)
            print_progress("已加载全局保存的 base_url。")
        else:
            legacy_base_url = str(legacy_settings.get("base_url") or "").strip()
            if legacy_base_url:
                base_url = normalize_base_url(legacy_base_url)
                print_progress("已从旧项目配置迁移 base_url 到全局设置。")
            else:
                base_url = normalize_base_url(prompt_text(base_url_prompt, provider_default_base_url(provider)))

    raw_model = (cli_model or "").strip()
    if raw_model:
        model = raw_model
        print_progress("已使用命令行传入的模型名称。")
    else:
        remembered_model = "" if provider_switched else str(global_config.get("last_model") or "").strip()
        if remembered_model:
            model = remembered_model
            print_progress("已加载全局保存的模型名称。")
        else:
            legacy_model = str(legacy_settings.get("model") or "").strip()
            if legacy_model:
                model = legacy_model
                print_progress("已从旧项目配置迁移模型名称到全局设置。")
            else:
                if provider == PROVIDER_OPENCODE_GO:
                    model = select_opencode_go_model(
                        api_key=str(api_key or "").strip(),
                        base_url=base_url,
                        preferred_model=provider_default_model(provider),
                        prompt_label="请选择 OpenCode Go 模型",
                    )
                else:
                    model = prompt_text(model_prompt, provider_default_model(provider))

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
    settings: dict[str, Any] = {
        "base_url": base_url,
        "model": model,
        "provider": provider,
        "protocol": protocol,
    }
    if protocol == PROTOCOL_OPENAI_COMPATIBLE:
        compatible_options = load_openai_compatible_options(updated_config, provider=provider)
        if compatible_options:
            settings["openai_compatible_options"] = compatible_options
    return settings, updated_config


def create_openai_client(
    *,
    api_key: str,
    base_url: str,
    protocol: str = PROTOCOL_RESPONSES,
    provider: str = PROVIDER_OPENAI,
    openai_compatible_options: dict[str, Any] | None = None,
) -> OpenAI:
    client = build_openai_client(api_key=api_key, base_url=base_url)
    setattr(client, "_codex_protocol", protocol)
    setattr(client, "_codex_provider", provider)
    setattr(client, "_codex_base_url", base_url)
    setattr(client, "_codex_api_key", api_key)
    if openai_compatible_options:
        setattr(client, "_codex_openai_compatible_options", dict(openai_compatible_options))
    return client
