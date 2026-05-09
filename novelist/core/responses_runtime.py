from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import warnings
from dataclasses import dataclass, field
from ipaddress import ip_address
from types import SimpleNamespace
from typing import Any, Generic, TypeVar
from urllib.parse import urlparse

import httpx
import openai
from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, Field

from .ui import print_progress


DEFAULT_API_RETRIES = 10
DEFAULT_RETRY_DELAY_SECONDS = 5
DEFAULT_REASONING_EFFORT = "medium"
PROTOCOL_RESPONSES = "responses"
PROTOCOL_OPENAI_COMPATIBLE = "openai_compatible"
COMPATIBLE_LARGE_REQUEST_CHAR_THRESHOLD = 120000
COMPATIBLE_CHAT_TRANSPORT_STREAM = "stream"
COMPATIBLE_CHAT_TRANSPORT_NONSTREAM = "nonstream"
PROVIDER_OPENCODE_GO = "opencode_go"
DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS = float(os.getenv("OPENAI_CONNECT_TIMEOUT_SECONDS", "30"))
DEFAULT_OPENAI_READ_TIMEOUT_SECONDS = float(os.getenv("OPENAI_READ_TIMEOUT_SECONDS", "600"))
DEFAULT_OPENAI_WRITE_TIMEOUT_SECONDS = float(os.getenv("OPENAI_WRITE_TIMEOUT_SECONDS", "30"))
DEFAULT_OPENAI_POOL_TIMEOUT_SECONDS = float(os.getenv("OPENAI_POOL_TIMEOUT_SECONDS", "30"))
DEFAULT_RESPONSE_POLL_TIMEOUT_SECONDS = float(os.getenv("OPENAI_RESPONSE_POLL_TIMEOUT_SECONDS", "600"))
DEFAULT_RESPONSE_POLL_INTERVAL_SECONDS = float(os.getenv("OPENAI_RESPONSE_POLL_INTERVAL_SECONDS", "2"))
IN_PROGRESS_RESPONSE_STATUSES = {"queued", "in_progress"}
LOCAL_BASE_URL_HOSTS = {"localhost"}
COMPAT_TEMPLATE_OMIT = object()


class ApiRequestError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    def __init__(self, message: str, preview: str = "", raw_body_text: str = "") -> None:
        super().__init__(message)
        self.preview = preview
        self.raw_body_text = raw_body_text


class MarkdownDocumentPayload(BaseModel):
    content_md: str = Field(..., description="Markdown document body to be written to the target file.")


@dataclass(frozen=True)
class TokenUsage:
    total: int
    input: int
    input_total: int
    output: int
    reasoning: int
    cache_read: int
    cache_write: int

    @property
    def cache_hit(self) -> int:
        return self.cache_read


def empty_token_usage() -> TokenUsage:
    return TokenUsage(total=0, input=0, input_total=0, output=0, reasoning=0, cache_read=0, cache_write=0)


def _print_retry_notice(
    *,
    attempt: int,
    retries: int,
    retry_delay_seconds: int,
    stage: str,
    error: Exception,
) -> None:
    print_progress(
        f"第 {attempt}/{retries} 次尝试失败，阶段：{stage}。"
        f"{retry_delay_seconds} 秒后重试第 {attempt + 1}/{retries} 次：{error}",
        error=True,
    )


def _estimate_text_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_estimate_text_chars(item) for item in value)
    if isinstance(value, dict):
        return sum(_estimate_text_chars(item) for item in value.values())
    return 0


def estimate_request_text_chars(*parts: Any) -> int:
    return sum(_estimate_text_chars(part) for part in parts)


def should_abort_transport_retries(
    error: Exception,
    *,
    protocol: str,
    request_chars: int,
    attempt: int,
) -> bool:
    error_text = str(error).lower()
    if "context window" in error_text or "input exceeds" in error_text:
        return True

    if isinstance(
        error,
        (
            openai.BadRequestError,
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.NotFoundError,
            openai.UnprocessableEntityError,
        ),
    ):
        return True

    if isinstance(error, openai.InternalServerError):
        original_error_text = str(error)
        if "Database error" in original_error_text or "please contact the administrator" in original_error_text:
            return True
        if protocol == PROTOCOL_OPENAI_COMPATIBLE:
            return attempt >= 2
        return False

    if not isinstance(error, openai.APIConnectionError):
        return False
    if protocol != PROTOCOL_OPENAI_COMPATIBLE:
        return False
    if request_chars >= COMPATIBLE_LARGE_REQUEST_CHAR_THRESHOLD:
        return True
    return attempt >= 2


def format_transport_error_message(
    error: Exception,
    *,
    protocol: str,
    request_chars: int,
    abort_retries: bool,
    compatible_transport_attempts: list[str] | None = None,
) -> str:
    message = f"接口请求失败（{type(error).__name__}，发生在请求预处理或发送阶段）：{error}"
    context_limit_error = "context window" in str(error).lower() or "input exceeds" in str(error).lower()
    if context_limit_error:
        message += " 这是上下文窗口超限的确定性错误，已停止继续重试。"
    if protocol == PROTOCOL_OPENAI_COMPATIBLE:
        message += f" 当前协议=openai_compatible，请求文本约 {request_chars} 字符。"
        attempts = [mode for mode in compatible_transport_attempts or [] if mode]
        if attempts:
            message += f" 已尝试传输={' -> '.join(attempts)}。"
        if isinstance(error, openai.BadRequestError):
            message += " 这是请求参数层面的确定性错误，已停止继续重试。"
        if isinstance(error, openai.InternalServerError):
            if "Database error" in str(error) or "please contact the administrator" in str(error):
                message += " 这是兼容服务端返回的数据库/内部错误，不是本地解析问题，继续重试通常没有意义。"
            elif abort_retries:
                message += " 同类服务端内部错误已连续出现，已停止继续重试。"
        if isinstance(error, openai.APIConnectionError):
            if request_chars >= COMPATIBLE_LARGE_REQUEST_CHAR_THRESHOLD:
                message += " 这更像是兼容网关或上游在大载荷下直接断开连接，不是模型回复解析失败。"
            elif abort_retries:
                message += " 同类连接错误已连续出现，已停止继续重试。"
    return message

class StatusSpinner:
    FRAMES = ("|", "/", "-", "\\")

    def __init__(self, initial_status: str, *, error: bool = False) -> None:
        self.stream = sys.stderr if error else sys.stdout
        self.enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.status = initial_status
        self.error = error
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_length = 0

    def start(self) -> None:
        if not self.enabled:
            print_progress(self.status, error=self.error)
            return

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status
        if not self.enabled:
            print_progress(status, error=self.error)

    def stop(self, final_message: str | None = None) -> None:
        if self.enabled:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=1)
            self._clear_line()
            if final_message:
                print(final_message, file=self.stream, flush=True)
        elif final_message:
            print_progress(final_message, error=self.error)

    def _run(self) -> None:
        frame_index = 0
        while not self._stop_event.is_set():
            with self._lock:
                status = self.status
            frame = self.FRAMES[frame_index % len(self.FRAMES)]
            message = f"\r{status} {frame}"
            self._last_length = max(self._last_length, len(message))
            padded = message.ljust(self._last_length)
            print(padded, end="", file=self.stream, flush=True)
            frame_index += 1
            time.sleep(0.15)

    def _clear_line(self) -> None:
        if not self.enabled:
            return
        print("\r" + (" " * self._last_length) + "\r", end="", file=self.stream, flush=True)


def _parse_env_bool(value: str | None) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _should_trust_environment_http_settings(base_url: str) -> bool:
    override = _parse_env_bool(os.getenv("OPENAI_HTTP_TRUST_ENV"))
    if override is not None:
        return override

    host = (urlparse(base_url).hostname or "").strip().lower()
    if not host:
        return True
    if host in LOCAL_BASE_URL_HOSTS:
        return False
    try:
        return not ip_address(host).is_loopback
    except ValueError:
        return True


def build_openai_client(*, api_key: str, base_url: str) -> OpenAI:
    timeout = httpx.Timeout(
        connect=DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS,
        read=DEFAULT_OPENAI_READ_TIMEOUT_SECONDS,
        write=DEFAULT_OPENAI_WRITE_TIMEOUT_SECONDS,
        pool=DEFAULT_OPENAI_POOL_TIMEOUT_SECONDS,
    )
    http_client = httpx.Client(
        timeout=timeout,
        trust_env=_should_trust_environment_http_settings(base_url),
    )
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )


def runtime_protocol(client: OpenAI) -> str:
    protocol = str(getattr(client, "_codex_protocol", "") or "").strip()
    if protocol:
        return protocol
    return PROTOCOL_RESPONSES


def openai_compatible_options(client: OpenAI) -> dict[str, Any]:
    options = getattr(client, "_codex_openai_compatible_options", None)
    return dict(options) if isinstance(options, dict) else {}


def openai_compatible_base_url(client: OpenAI) -> str:
    return str(getattr(client, "_codex_base_url", "") or "").strip()


def openai_compatible_api_key(client: OpenAI) -> str:
    return str(getattr(client, "_codex_api_key", "") or "").strip()


def _build_openai_compatible_request_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def _build_openai_compatible_http_client(
    base_url: str,
    *,
    trust_env_override: bool | None = None,
) -> httpx.Client:
    timeout = httpx.Timeout(
        connect=DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS,
        read=DEFAULT_OPENAI_READ_TIMEOUT_SECONDS,
        write=DEFAULT_OPENAI_WRITE_TIMEOUT_SECONDS,
        pool=DEFAULT_OPENAI_POOL_TIMEOUT_SECONDS,
    )
    return httpx.Client(
        timeout=timeout,
        trust_env=_should_trust_environment_http_settings(base_url) if trust_env_override is None else trust_env_override,
    )


def _extract_compatible_error_message(payload: Any, body_text: str, status_code: int) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if body_text.strip():
        return body_text.strip()
    return f"HTTP {status_code}"


def _raise_openai_compatible_http_error(response: httpx.Response) -> None:
    response.read()
    body_text = response.text
    payload = safe_json_loads(body_text)
    message = _extract_compatible_error_message(payload, body_text, response.status_code)
    status_code = response.status_code
    if status_code == 400:
        raise openai.BadRequestError(message, response=response, body=payload)
    if status_code == 401:
        raise openai.AuthenticationError(message, response=response, body=payload)
    if status_code == 403:
        raise openai.PermissionDeniedError(message, response=response, body=payload)
    if status_code == 404:
        raise openai.NotFoundError(message, response=response, body=payload)
    if status_code == 422:
        raise openai.UnprocessableEntityError(message, response=response, body=payload)
    if status_code == 429:
        raise openai.RateLimitError(message, response=response, body=payload)
    if status_code >= 500:
        raise openai.InternalServerError(message, response=response, body=payload)
    raise openai.APIStatusError(message, response=response, body=payload)


def _looks_like_deepseek_v4_model(model: str) -> bool:
    return "deepseek-v4" in str(model or "").strip().lower()


def _is_deepseek_api_base_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower()
    return host == "api.deepseek.com"


def compatible_reasoning_effort(client: OpenAI, *, model: str) -> str | None:
    options = openai_compatible_options(client)
    configured = str(options.get("reasoning_effort") or "").strip().lower()
    if configured:
        return configured
    if _looks_like_deepseek_v4_model(model):
        return "high"
    return None


def _compatible_default_extra_body(
    client: OpenAI,
    *,
    model: str,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    base_url = openai_compatible_base_url(client)
    if _is_deepseek_api_base_url(base_url) and _looks_like_deepseek_v4_model(model) and reasoning_effort != "none":
        return {"thinking": {"type": "enabled"}}
    return {}


def _normalize_reasoning_content_value(value: Any) -> str | None:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    return None


def extract_chat_completion_reasoning_content(payload: Any) -> str | None:
    plain = to_plain_data(payload)
    if not isinstance(plain, dict):
        return None
    choices = plain.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        if "reasoning_content" not in message:
            continue
        normalized = _normalize_reasoning_content_value(message.get("reasoning_content"))
        if normalized is not None:
            return normalized
        return str(message.get("reasoning_content"))
    return None


def _merge_openai_compatible_extra_body(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in overrides.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[str(key)] = {**existing, **value}
        else:
            merged[str(key)] = value
    return merged


def _render_openai_compatible_template(value: Any, context: dict[str, str | None]) -> Any:
    if isinstance(value, str):
        rendered = value
        used_placeholder = False
        for key, replacement in context.items():
            token = f"{{{{{key}}}}}"
            if token not in rendered:
                continue
            used_placeholder = True
            if replacement is None:
                if rendered.strip() == token:
                    return COMPAT_TEMPLATE_OMIT
                rendered = rendered.replace(token, "")
            else:
                rendered = rendered.replace(token, replacement)
        if used_placeholder and not rendered.strip():
            return COMPAT_TEMPLATE_OMIT
        return rendered
    if isinstance(value, list):
        rendered_list: list[Any] = []
        for item in value:
            rendered_item = _render_openai_compatible_template(item, context)
            if rendered_item is COMPAT_TEMPLATE_OMIT:
                continue
            rendered_list.append(rendered_item)
        return rendered_list
    if isinstance(value, dict):
        rendered_dict: dict[str, Any] = {}
        for key, item in value.items():
            rendered_item = _render_openai_compatible_template(item, context)
            if rendered_item is COMPAT_TEMPLATE_OMIT:
                continue
            rendered_dict[str(key)] = rendered_item
        return rendered_dict
    return value


def resolve_openai_compatible_request_extras(
    client: OpenAI,
    *,
    prompt_cache_key: str | None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    options = openai_compatible_options(client)
    context = {
        "prompt_cache_key": str(prompt_cache_key).strip() or None,
    }
    extra_body = _render_openai_compatible_template(options.get("extra_body"), context)
    extra_headers = _render_openai_compatible_template(options.get("extra_headers"), context)
    normalized_body = extra_body if isinstance(extra_body, dict) else {}
    normalized_headers = (
        {str(key): str(value) for key, value in extra_headers.items()}
        if isinstance(extra_headers, dict)
        else {}
    )
    default_body = _compatible_default_extra_body(
        client,
        model=str(model or ""),
        reasoning_effort=reasoning_effort,
    )
    provider_id = str(getattr(client, "_codex_provider", "") or "").strip().lower()
    if provider_id == PROVIDER_OPENCODE_GO and context["prompt_cache_key"]:
        default_body = {
            **default_body,
            "prompt_cache_key": context["prompt_cache_key"],
        }
    normalized_body = _merge_openai_compatible_extra_body(default_body, normalized_body)
    return normalized_body, normalized_headers


def openai_compatible_uses_direct_http(client: OpenAI) -> bool:
    return bool(openai_compatible_base_url(client) and openai_compatible_api_key(client))


def _split_openai_compatible_http_request(request_params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    payload = dict(request_params)
    extra_body = payload.pop("extra_body", None)
    extra_headers = payload.pop("extra_headers", None)
    if isinstance(extra_body, dict) and extra_body:
        payload = _merge_openai_compatible_extra_body(payload, extra_body)
    headers = (
        {str(key): str(value) for key, value in extra_headers.items()}
        if isinstance(extra_headers, dict)
        else {}
    )
    return payload, headers


def _build_openai_compatible_request_headers(
    api_key: str,
    *,
    stream: bool,
    extra_headers: dict[str, str],
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }
    headers.update(extra_headers)
    return headers


def _build_openai_compatible_status_url(base_url: str, request_id: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        normalized = normalized[: -len("/chat/completions")]
    return f"{normalized}/status/{request_id}"


def _extract_openai_compatible_request_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("requestId", "request_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _raise_openai_compatible_pending_error(response: httpx.Response, payload: Any) -> None:
    message = _extract_compatible_error_message(payload, response.text, response.status_code)
    if not isinstance(payload, dict):
        payload = {"message": message}
    raise openai.APIStatusError(message, response=response, body=payload)


def should_retry_openai_compatible_without_env_proxy(
    error: Exception,
    *,
    base_url: str,
    trust_env: bool,
) -> bool:
    if not trust_env:
        return False
    if not _should_trust_environment_http_settings(base_url):
        return False
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "decryption failed or bad record mac",
            "wrong version number",
            "tlsv1 alert",
            "unexpected eof while reading",
        )
    )


def _poll_openai_compatible_pending_response(
    client: OpenAI,
    *,
    pending_response: httpx.Response,
    extra_headers: dict[str, str],
    trust_env_override: bool | None = None,
) -> httpx.Response:
    pending_response.read()
    payload = safe_json_loads(pending_response.text)
    request_id = _extract_openai_compatible_request_id(payload)
    if not request_id:
        _raise_openai_compatible_pending_error(pending_response, payload)

    base_url = openai_compatible_base_url(client)
    api_key = openai_compatible_api_key(client)
    status_url = _build_openai_compatible_status_url(base_url, request_id)
    deadline = time.time() + DEFAULT_RESPONSE_POLL_TIMEOUT_SECONDS

    with _build_openai_compatible_http_client(base_url, trust_env_override=trust_env_override) as http_client:
        headers = _build_openai_compatible_request_headers(
            api_key,
            stream=False,
            extra_headers=extra_headers,
        )
        while True:
            try:
                response = http_client.get(status_url, headers=headers)
            except httpx.HTTPError as error:
                request = getattr(error, "request", None) or httpx.Request("GET", status_url)
                raise openai.APIConnectionError(request=request) from error
            if response.status_code == 202:
                if time.time() >= deadline:
                    raise openai.APITimeoutError(request=response.request) from TimeoutError(
                        f"兼容接口轮询超时，request_id={request_id}"
                    )
                time.sleep(DEFAULT_RESPONSE_POLL_INTERVAL_SECONDS)
                continue
            if response.status_code >= 400:
                _raise_openai_compatible_http_error(response)
            return response


def compatible_usage_extra_paths(
    client: OpenAI,
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    options = openai_compatible_options(client)

    def normalize(raw_value: Any) -> list[tuple[str, ...]]:
        if not isinstance(raw_value, list):
            return []
        normalized: list[tuple[str, ...]] = []
        for item in raw_value:
            parts: list[str] = []
            if isinstance(item, str):
                parts = [segment.strip() for segment in item.split(".") if segment.strip()]
            elif isinstance(item, list):
                parts = [str(segment).strip() for segment in item if str(segment).strip()]
            if parts:
                normalized.append(tuple(parts))
        return normalized

    return normalize(options.get("cache_read_paths")), normalize(options.get("cache_write_paths"))


def compatible_chat_transport_mode(client: OpenAI) -> str:
    options = openai_compatible_options(client)
    mode = str(options.get("transport") or "").strip().lower()
    if mode in {COMPATIBLE_CHAT_TRANSPORT_STREAM, COMPATIBLE_CHAT_TRANSPORT_NONSTREAM}:
        return mode
    return COMPATIBLE_CHAT_TRANSPORT_STREAM


def alternate_compatible_chat_transport(mode: str) -> str:
    if mode == COMPATIBLE_CHAT_TRANSPORT_STREAM:
        return COMPATIBLE_CHAT_TRANSPORT_NONSTREAM
    return COMPATIBLE_CHAT_TRANSPORT_STREAM


def compatible_chat_transport_candidates(
    client: OpenAI,
    *,
    request_chars: int,
) -> list[str]:
    preferred = compatible_chat_transport_mode(client)
    if request_chars < COMPATIBLE_LARGE_REQUEST_CHAR_THRESHOLD:
        return [preferred]
    alternate = alternate_compatible_chat_transport(preferred)
    if alternate == preferred:
        return [preferred]
    return [preferred, alternate]


def to_plain_data(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain_data(item) for key, item in value.items()}

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return to_plain_data(to_dict())
        except Exception:
            pass

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return to_plain_data(model_dump())
        except Exception:
            pass

    return value


def safe_json_loads(text: str) -> dict[str, Any] | list[Any] | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None

    candidates = [stripped]
    fenced_match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, re.IGNORECASE)
    if fenced_match:
        fenced_payload = fenced_match.group(1).strip()
        if fenced_payload:
            candidates.append(fenced_payload)

    for candidate in candidates:
        current = candidate
        for _ in range(3):
            try:
                loaded = json.loads(current)
            except Exception:
                break
            if isinstance(loaded, (dict, list)):
                return loaded
            if isinstance(loaded, str):
                next_candidate = loaded.strip()
                if not next_candidate or next_candidate == current:
                    break
                current = next_candidate
                continue
            break
    return None


def safe_token_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def nested_get(mapping: Any, *keys: str) -> Any:
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_token_value(mapping: Any, paths: list[tuple[str, ...]]) -> int:
    for path in paths:
        value = nested_get(mapping, *path)
        if value is not None:
            return safe_token_int(value)
    return 0


def extract_token_usage(
    raw_json: Any,
    *,
    extra_cache_read_paths: list[tuple[str, ...]] | None = None,
    extra_cache_write_paths: list[tuple[str, ...]] | None = None,
) -> TokenUsage:
    plain = to_plain_data(raw_json)
    if not isinstance(plain, dict):
        return TokenUsage(total=0, input=0, input_total=0, output=0, reasoning=0, cache_read=0, cache_write=0)

    usage = plain.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage(total=0, input=0, input_total=0, output=0, reasoning=0, cache_read=0, cache_write=0)

    input_total = first_token_value(
        usage,
        [
            ("input_tokens",),
            ("inputTokens",),
            ("prompt_tokens",),
            ("promptTokens",),
        ],
    )
    output_total = first_token_value(
        usage,
        [
            ("output_tokens",),
            ("outputTokens",),
            ("completion_tokens",),
            ("completionTokens",),
        ],
    )
    reasoning = first_token_value(
        usage,
        [
            ("output_tokens_details", "reasoning_tokens"),
            ("outputTokenDetails", "reasoningTokens"),
            ("completion_tokens_details", "reasoning_tokens"),
            ("completionTokenDetails", "reasoningTokens"),
            ("reasoning_tokens",),
            ("reasoningTokens",),
        ],
    )
    cache_read = first_token_value(
        usage,
        [
            ("input_tokens_details", "cached_tokens"),
            ("input_tokens_details", "cache_read_tokens"),
            ("inputTokenDetails", "cacheReadTokens"),
            ("prompt_tokens_details", "cached_tokens"),
            ("promptTokenDetails", "cachedTokens"),
            ("cached_input_tokens",),
            ("cachedInputTokens",),
            ("cache_read_input_tokens",),
            ("cacheReadInputTokens",),
            *((extra_cache_read_paths or [])),
        ],
    )
    cache_write = first_token_value(
        usage,
        [
            ("input_tokens_details", "cache_write_tokens"),
            ("input_tokens_details", "cache_creation_tokens"),
            ("inputTokenDetails", "cacheWriteTokens"),
            ("prompt_tokens_details", "cache_write_tokens"),
            ("promptTokenDetails", "cacheWriteTokens"),
            ("cache_write_input_tokens",),
            ("cacheWriteInputTokens",),
            ("cache_creation_input_tokens",),
            ("cacheCreationInputTokens",),
            *((extra_cache_write_paths or [])),
        ],
    )
    total = first_token_value(
        usage,
        [
            ("total_tokens",),
            ("totalTokens",),
        ],
    )
    adjusted_input = max(input_total - cache_read - cache_write, 0)
    output = max(output_total - reasoning, 0)
    if total <= 0:
        total = adjusted_input + output + reasoning + cache_read + cache_write
    return TokenUsage(
        total=total,
        input=adjusted_input,
        input_total=input_total,
        output=output,
        reasoning=reasoning,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def token_usage_summary(usage: TokenUsage) -> str:
    if usage.total <= 0 and usage.input_total <= 0 and usage.output <= 0 and usage.reasoning <= 0:
        return "token=unavailable"
    parts = [
        f"发送={usage.input_total}",
        f"接收={usage.output}",
        f"缓存命中={usage.cache_hit}",
    ]
    if usage.cache_write:
        parts.append(f"缓存写入={usage.cache_write}")
    if usage.reasoning:
        parts.append(f"推理={usage.reasoning}")
    if usage.input_total != usage.input:
        parts.append(f"非缓存输入={usage.input}")
    if usage.total:
        parts.append(f"总计={usage.total}")
    return "token " + "，".join(parts)


def _has_response_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _function_arguments_quality(value: Any) -> int:
    if not isinstance(value, str) or not value.strip():
        return 0
    loaded = safe_json_loads(value)
    if isinstance(loaded, dict):
        return 3
    if isinstance(loaded, list):
        return 2
    return 1


def _response_output_item_score(item: Any) -> int:
    if not isinstance(item, dict):
        return 0
    score = 0
    if isinstance(item.get("type"), str) and item["type"]:
        score += 1
    if isinstance(item.get("name"), str) and item["name"]:
        score += 4
    score += _function_arguments_quality(item.get("arguments")) * 3
    if _has_response_value(item.get("parsed_arguments")):
        score += 6
    if _has_response_value(item.get("content")):
        score += 4
    if _has_response_value(item.get("id")):
        score += 1
    return score


def _merge_response_output_item(primary: Any, fallback: Any) -> Any:
    if not isinstance(primary, dict) or not isinstance(fallback, dict):
        return primary

    merged = dict(primary)
    for key, value in fallback.items():
        if not _has_response_value(merged.get(key)) and _has_response_value(value):
            merged[key] = value

    if _function_arguments_quality(fallback.get("arguments")) > _function_arguments_quality(merged.get("arguments")):
        merged["arguments"] = fallback["arguments"]
    return merged


def _merge_response_outputs(final_output: Any, reconstructed_output: list[dict[str, Any]]) -> list[Any]:
    if isinstance(final_output, list) and final_output:
        merged_output: list[Any] = list(final_output)
    else:
        merged_output = []

    for index, reconstructed_item in enumerate(reconstructed_output):
        if index >= len(merged_output):
            merged_output.append(reconstructed_item)
            continue
        current_item = merged_output[index]
        if not isinstance(current_item, dict) or not isinstance(reconstructed_item, dict):
            continue
        if _response_output_item_score(reconstructed_item) > _response_output_item_score(current_item):
            merged_output[index] = _merge_response_output_item(reconstructed_item, current_item)
        else:
            merged_output[index] = _merge_response_output_item(current_item, reconstructed_item)

    return merged_output or reconstructed_output


def response_payload_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("status") or "").strip()


def response_payload_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("id") or "").strip()


def clip_error_detail(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def response_followup_error_details(raw_json: Any) -> str:
    plain = to_plain_data(raw_json)
    if not isinstance(plain, dict):
        return ""
    details: list[str] = []
    followup_errors = (
        ("response_id 续接失败", plain.get("_response_id_stream_unavailable_error")),
        ("retrieve 补取失败", plain.get("_retrieve_unavailable_error")),
    )
    for label, error in followup_errors:
        if error:
            details.append(f"{label}={clip_error_detail(error)}")
    return "；".join(details)


def observed_tool_call_details(raw_json: Any) -> str:
    plain = to_plain_data(raw_json)
    if not isinstance(plain, dict):
        return ""
    details: list[str] = []
    choices = plain.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                name = str(function.get("name") or "").strip() or "<unknown>"
                arguments = clip_error_detail(function.get("arguments") or "", limit=160)
                details.append(f"{name} args={arguments}")
        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or "").strip() or "<unknown>"
            arguments = clip_error_detail(function_call.get("arguments") or "", limit=160)
            details.append(f"{name} args={arguments}")
    return "；".join(details[:3])


def build_extraction_error_message(
    *,
    target_label: str,
    response_id: str,
    status: str,
    output_items: int,
    output_types: list[str],
    raw_json: Any,
) -> str:
    normalized_status = str(status or "unknown")
    if normalized_status in IN_PROGRESS_RESPONSE_STATUSES:
        message = f"模型响应仍为 {normalized_status}，未取得完整{target_label}，不能按已完成回复解析。"
        followup_details = response_followup_error_details(raw_json)
        if followup_details:
            message += f" 后续补取情况：{followup_details}。"
    elif normalized_status == "unknown":
        message = f"模型响应状态未知，未能从{target_label}中提取所需字段。"
    else:
        message = f"模型响应状态为 {normalized_status}，但未能从{target_label}中提取所需字段。"
    tool_details = observed_tool_call_details(raw_json)
    if tool_details:
        message += f" 观测到的工具调用：{tool_details}。"
    return (
        message
        + f" response_id={response_id},"
        + f" status={normalized_status},"
        + f" output_items={output_items},"
        + f" output_types={output_types or ['none']}"
    )


def extraction_retry_stage(*, status: str, default_stage: str) -> str:
    if str(status or "") in IN_PROGRESS_RESPONSE_STATUSES:
        return "接口响应未完成"
    return default_stage


def retrieve_response_until_terminal(
    client: OpenAI,
    response_payload: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_RESPONSE_POLL_TIMEOUT_SECONDS,
    interval_seconds: float = DEFAULT_RESPONSE_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    response_id = response_payload_id(response_payload)
    if not response_id or response_payload_status(response_payload) not in IN_PROGRESS_RESPONSE_STATUSES:
        return response_payload

    deadline = time.monotonic() + max(timeout_seconds, 0)
    latest_payload = dict(response_payload)
    while True:
        try:
            retrieved = client.responses.retrieve(response_id)
        except openai.OpenAIError as error:
            latest_payload["_retrieve_unavailable_error"] = str(error)
            return latest_payload
        retrieved_payload = to_plain_data(retrieved)
        if isinstance(retrieved_payload, dict):
            latest_payload = retrieved_payload
            if response_payload_status(latest_payload) not in IN_PROGRESS_RESPONSE_STATUSES:
                return latest_payload

        if time.monotonic() >= deadline:
            return latest_payload
        time.sleep(max(interval_seconds, 0))


def normalize_content_text(value: Any) -> list[str]:
    plain = to_plain_data(value)
    if isinstance(plain, str):
        stripped = plain.strip()
        return [stripped] if stripped else []

    if isinstance(plain, list):
        result: list[str] = []
        for item in plain:
            result.extend(normalize_content_text(item))
        return result

    if not isinstance(plain, dict):
        return []

    result: list[str] = []
    text_value = plain.get("text")
    if isinstance(text_value, str) and text_value.strip():
        result.append(text_value.strip())
    elif text_value is not None:
        result.extend(normalize_content_text(text_value))

    for key in (
        "content",
        "message",
        "delta",
        "result",
        "output",
        "response",
        "generated_text",
        "completion",
        "answer",
    ):
        if key in plain:
            result.extend(normalize_content_text(plain[key]))

    value_field = plain.get("value")
    if isinstance(value_field, str) and value_field.strip():
        result.append(value_field.strip())

    return result


def append_candidate_text(candidates: list[tuple[str, str]], source: str, value: Any) -> None:
    texts = normalize_content_text(value)
    if not texts:
        return
    combined = "\n\n".join(part for part in texts if part.strip()).strip()
    if combined:
        candidates.append((source, combined))


def synthesize_output_text_from_output_items(output_items: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for item in output_items:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
    return "\n\n".join(texts).strip()


def consume_response_stream_events(stream: Any) -> tuple[dict[str, Any], dict[int, dict[str, Any]], dict[str, Any] | None]:
    response_payload: dict[str, Any] = {}
    output_items_by_index: dict[int, dict[str, Any]] = {}
    final_response_payload: dict[str, Any] | None = None

    for event in stream:
        event_type = getattr(event, "type", "")

        if event_type in {"response.created", "response.in_progress", "response.completed"}:
            payload = to_plain_data(getattr(event, "response", None))
            if isinstance(payload, dict):
                response_payload = payload
            continue

        if event_type == "response.output_item.added":
            item = to_plain_data(getattr(event, "item", None))
            output_index = getattr(event, "output_index", None)
            if isinstance(item, dict) and isinstance(output_index, int):
                output_items_by_index[output_index] = item
            continue

        if event_type == "response.output_item.done":
            item = to_plain_data(getattr(event, "item", None))
            output_index = getattr(event, "output_index", None)
            if isinstance(item, dict) and isinstance(output_index, int):
                output_items_by_index[output_index] = item
            continue

        if event_type == "response.function_call_arguments.delta":
            output_index = getattr(event, "output_index", None)
            if not isinstance(output_index, int):
                continue
            item = output_items_by_index.setdefault(
                output_index,
                {"type": "function_call", "arguments": "", "name": ""},
            )
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                arguments = ""
            delta = getattr(event, "delta", "")
            if isinstance(delta, str):
                item["arguments"] = arguments + delta
            continue

        if event_type == "response.function_call_arguments.done":
            output_index = getattr(event, "output_index", None)
            if not isinstance(output_index, int):
                continue
            item = output_items_by_index.setdefault(
                output_index,
                {"type": "function_call", "arguments": "", "name": ""},
            )
            arguments = getattr(event, "arguments", "")
            if isinstance(arguments, str):
                item["arguments"] = arguments
            continue

        if event_type == "response.output_text.done":
            output_index = getattr(event, "output_index", None)
            content_index = getattr(event, "content_index", None)
            if not isinstance(output_index, int) or not isinstance(content_index, int):
                continue
            item = output_items_by_index.setdefault(output_index, {"type": "message", "content": []})
            content = item.setdefault("content", [])
            if not isinstance(content, list):
                item["content"] = []
                content = item["content"]
            while len(content) <= content_index:
                content.append({"type": "output_text", "text": ""})
            text = getattr(event, "text", "")
            if isinstance(text, str):
                content[content_index] = {"type": "output_text", "text": text}

    try:
        final_response = stream.get_final_response()
        final_payload = to_plain_data(final_response)
        if isinstance(final_payload, dict):
            final_response_payload = final_payload
    except RuntimeError:
        final_response_payload = None

    return response_payload, output_items_by_index, final_response_payload


def continue_response_stream_until_terminal(
    client: OpenAI,
    response_payload: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_RESPONSE_POLL_TIMEOUT_SECONDS,
    interval_seconds: float = DEFAULT_RESPONSE_POLL_INTERVAL_SECONDS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    response_id = response_payload_id(response_payload)
    if not response_id or response_payload_status(response_payload) not in IN_PROGRESS_RESPONSE_STATUSES:
        return response_payload, []

    deadline = time.monotonic() + max(timeout_seconds, 0)
    latest_payload = dict(response_payload)
    latest_items: list[dict[str, Any]] = []
    while True:
        try:
            with client.responses.stream(response_id=response_id) as stream:
                stream_payload, stream_items_by_index, final_payload = consume_response_stream_events(stream)
        except openai.OpenAIError as error:
            latest_payload["_response_id_stream_unavailable_error"] = str(error)
            return latest_payload, latest_items

        if not stream_payload and final_payload is None and not stream_items_by_index:
            latest_payload["_response_id_stream_unavailable_error"] = "empty response_id stream"
            return latest_payload, latest_items

        candidate_payload = dict(final_payload or stream_payload or latest_payload)
        candidate_items = [stream_items_by_index[index] for index in sorted(stream_items_by_index)]
        if candidate_items:
            latest_items = candidate_items
        latest_payload = candidate_payload
        if response_payload_status(latest_payload) not in IN_PROGRESS_RESPONSE_STATUSES:
            return latest_payload, latest_items

        if time.monotonic() >= deadline:
            return latest_payload, latest_items
        time.sleep(max(interval_seconds, 0))


def collect_stream_response(
    client: OpenAI,
    *,
    request_params: dict[str, Any],
) -> tuple[Any, str, Any]:
    response_payload: dict[str, Any] = {}
    output_items_by_index: dict[int, dict[str, Any]] = {}
    final_response_payload: dict[str, Any] | None = None

    with warnings.catch_warnings():
        # openai-python 1.109.x can emit Pydantic serializer warnings when
        # serializing newer Responses tool variants (for example image_generation
        # model values newer than the local SDK type hints). They are SDK type
        # noise and otherwise break the spinner line.
        warnings.filterwarnings(
            "ignore",
            message=r"Pydantic serializer warnings:.*",
            category=UserWarning,
            module=r"pydantic\.main",
        )
        with client.responses.stream(**request_params) as stream:
            response_payload, output_items_by_index, final_response_payload = consume_response_stream_events(stream)

    response_payload = dict(final_response_payload or response_payload)
    continued_payload, continued_items = continue_response_stream_until_terminal(client, response_payload)
    if continued_items:
        for index, item in enumerate(continued_items):
            output_items_by_index[index] = item
    response_payload = continued_payload
    response_payload = retrieve_response_until_terminal(client, response_payload)
    reconstructed_output = [output_items_by_index[index] for index in sorted(output_items_by_index)]
    output_items = _merge_response_outputs(response_payload.get("output"), reconstructed_output)
    response_payload["output"] = output_items
    output_text = synthesize_output_text_from_output_items(output_items)
    synthetic_response = SimpleNamespace(
        id=str(response_payload.get("id", "") or ""),
        status=str(response_payload.get("status", "") or ""),
        output=output_items,
        output_text=output_text,
    )
    raw_body_text = json.dumps(response_payload, ensure_ascii=False)
    return synthetic_response, raw_body_text, response_payload


def build_chat_completion_tools(tool_specs: list["FunctionToolSpec[Any]"]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for spec in tool_specs:
        for tool_name in spec.chat_completion_names():
            description = spec.description
            if tool_name != spec.name:
                description = f"{spec.description} 这是 {spec.name} 的兼容别名。"
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": description,
                        "parameters": to_strict_json_schema(spec.model),
                    },
                }
            )
    return tools


def build_responses_function_tools(tool_specs: list["FunctionToolSpec[Any]"]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for spec in tool_specs:
        tools.append(
            {
                "type": "function",
                "name": spec.name,
                "description": spec.description,
                "parameters": to_strict_json_schema(spec.model),
                "strict": True,
            }
        )
    return tools


def normalize_chat_tool_choice(tool_choice: Any) -> Any:
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function" and isinstance(tool_choice.get("name"), str):
            return {
                "type": "function",
                "function": {"name": tool_choice["name"]},
            }
    return tool_choice


def build_chat_tool_choice_candidates(tool_choice: Any) -> list[Any]:
    normalized = normalize_chat_tool_choice(tool_choice)
    candidates: list[Any] = [normalized]
    if isinstance(tool_choice, dict):
        legacy_name = tool_choice.get("name")
        if tool_choice.get("type") == "function" and isinstance(legacy_name, str):
            candidates.append({"type": "function", "name": legacy_name})

    deduped: list[Any] = []
    seen: set[str] = set()
    for candidate in candidates:
        marker = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(candidate)
    return deduped


def should_retry_legacy_chat_tool_choice(error: Exception) -> bool:
    if not isinstance(error, openai.BadRequestError):
        return False
    text = str(error)
    return "tool_choice.function" in text or "Unknown parameter: 'tool_choice.function'" in text


def should_retry_without_chat_stream_options(error: Exception) -> bool:
    if not isinstance(error, (openai.BadRequestError, openai.UnprocessableEntityError)):
        return False
    text = str(error)
    return "stream_options" in text or "include_usage" in text


def should_retry_with_alternate_chat_transport(
    error: Exception,
    *,
    request_chars: int,
) -> bool:
    if request_chars < COMPATIBLE_LARGE_REQUEST_CHAR_THRESHOLD:
        return False
    if isinstance(error, openai.APIConnectionError):
        return True
    if isinstance(error, openai.InternalServerError):
        text = str(error).lower()
        return "502" in text or "bad gateway" in text or "upstream" in text or "gateway" in text
    return False


def should_fallback_to_nonstream_chat_completion(
    error: Exception,
    *,
    request_chars: int,
) -> bool:
    return should_retry_with_alternate_chat_transport(error, request_chars=request_chars)


def build_nonstream_chat_completion_request(request_params: dict[str, Any]) -> dict[str, Any]:
    nonstream = dict(request_params)
    nonstream.pop("stream_options", None)
    nonstream["stream"] = False
    return nonstream


def request_compatible_chat_completion(
    client: OpenAI,
    *,
    request_params: dict[str, Any],
    transport_mode: str,
) -> tuple[Any, str, Any]:
    if openai_compatible_uses_direct_http(client):
        if transport_mode == COMPATIBLE_CHAT_TRANSPORT_STREAM:
            last_error: Exception | None = None
            for include_usage in (True, False):
                stream_request_params = dict(request_params)
                stream_request_params["stream"] = True
                if include_usage:
                    stream_request_params["stream_options"] = {"include_usage": True}
                try:
                    return collect_openai_compatible_stream_response_direct(
                        client,
                        request_params=stream_request_params,
                    )
                except Exception as error:
                    last_error = error
                    if include_usage and should_retry_without_chat_stream_options(error):
                        continue
                    raise
            if last_error is not None:
                raise last_error
        nonstream_request_params = build_nonstream_chat_completion_request(request_params)
        return collect_openai_compatible_response_direct(
            client,
            request_params=nonstream_request_params,
        )

    if transport_mode == COMPATIBLE_CHAT_TRANSPORT_STREAM:
        last_error: Exception | None = None
        for include_usage in (True, False):
            stream_request_params = dict(request_params)
            stream_request_params["stream"] = True
            if include_usage:
                stream_request_params["stream_options"] = {"include_usage": True}
            try:
                return collect_chat_completion_stream_response(
                    client,
                    request_params=stream_request_params,
                )
            except Exception as error:
                last_error = error
                if include_usage and should_retry_without_chat_stream_options(error):
                    continue
                raise
        if last_error is not None:
            raise last_error

    nonstream_request_params = build_nonstream_chat_completion_request(request_params)
    return collect_chat_completion_response(
        client,
        request_params=nonstream_request_params,
    )


def _build_chat_completion_stream_result(chunks: Any) -> tuple[Any, str, Any]:
    response_payload: dict[str, Any] = {}
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    legacy_function_call: dict[str, str] = {"name": "", "arguments": ""}
    content_parts: list[str] = []
    reasoning_content_parts: list[str] = []
    saw_reasoning_content = False
    finish_reason: str | None = None
    role = "assistant"

    for chunk in chunks:
        plain = to_plain_data(chunk)
        if not isinstance(plain, dict):
            continue
        for key in ("id", "object", "created", "model", "system_fingerprint"):
            if key in plain and key not in response_payload:
                response_payload[key] = plain[key]
        if isinstance(plain.get("usage"), dict):
            response_payload["usage"] = plain["usage"]

        choices = plain.get("choices")
        if not isinstance(choices, list):
            continue

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if isinstance(choice.get("finish_reason"), str) and choice["finish_reason"]:
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            if isinstance(delta.get("role"), str) and delta["role"]:
                role = delta["role"]
            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
            if "reasoning_content" in delta:
                saw_reasoning_content = True
                reasoning_content = delta.get("reasoning_content")
                if isinstance(reasoning_content, str):
                    reasoning_content_parts.append(reasoning_content)
            function_call = delta.get("function_call")
            if isinstance(function_call, dict):
                if isinstance(function_call.get("name"), str) and function_call["name"]:
                    legacy_function_call["name"] = function_call["name"]
                if isinstance(function_call.get("arguments"), str) and function_call["arguments"]:
                    legacy_function_call["arguments"] += function_call["arguments"]
            tool_calls = delta.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                index = tool_call.get("index")
                if not isinstance(index, int):
                    index = 0
                item = tool_calls_by_index.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if isinstance(tool_call.get("id"), str) and tool_call["id"]:
                    item["id"] = tool_call["id"]
                if isinstance(tool_call.get("type"), str) and tool_call["type"]:
                    item["type"] = tool_call["type"]
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                item_function = item.setdefault("function", {"name": "", "arguments": ""})
                if isinstance(function.get("name"), str) and function["name"]:
                    item_function["name"] = function["name"]
                if isinstance(function.get("arguments"), str) and function["arguments"]:
                    item_function["arguments"] = str(item_function.get("arguments") or "") + function["arguments"]

    message: dict[str, Any] = {
        "role": role,
        "content": "".join(content_parts) or None,
    }
    if saw_reasoning_content:
        message["reasoning_content"] = "".join(reasoning_content_parts)
    if tool_calls_by_index:
        message["tool_calls"] = [tool_calls_by_index[index] for index in sorted(tool_calls_by_index)]
    elif legacy_function_call["name"] or legacy_function_call["arguments"]:
        message["function_call"] = dict(legacy_function_call)
        message["tool_calls"] = [
            {
                "id": "legacy_function_call_0",
                "type": "function",
                "function": dict(legacy_function_call),
            }
        ]

    response_payload["choices"] = [
        {
            "index": 0,
            "message": message,
            "finish_reason": finish_reason or ("tool_calls" if tool_calls_by_index or legacy_function_call["name"] else "stop"),
        }
    ]
    synthetic_response = SimpleNamespace(
        id=str(response_payload.get("id", "") or ""),
        status="completed",
        output=[],
        output_text="".join(content_parts).strip(),
    )
    raw_body_text = json.dumps(response_payload, ensure_ascii=False)
    return synthetic_response, raw_body_text, response_payload


def collect_chat_completion_stream_response(
    client: OpenAI,
    *,
    request_params: dict[str, Any],
) -> tuple[Any, str, Any]:
    stream = client.chat.completions.create(**request_params)
    close = getattr(stream, "close", None)
    try:
        return _build_chat_completion_stream_result(stream)
    finally:
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _build_chat_completion_nonstream_result(response_payload: dict[str, Any]) -> tuple[Any, str, Any]:
    response_payload = dict(response_payload)
    response_payload.setdefault("status", "completed")
    response_payload.setdefault("_codex_transport", "chat.completion.nonstream")
    synthetic_response = SimpleNamespace(
        id=str(response_payload.get("id", "") or ""),
        status=str(response_payload.get("status", "") or "completed"),
        output=[],
        output_text="",
    )
    raw_body_text = json.dumps(response_payload, ensure_ascii=False)
    return synthetic_response, raw_body_text, response_payload


def collect_chat_completion_response(
    client: OpenAI,
    *,
    request_params: dict[str, Any],
) -> tuple[Any, str, Any]:
    completion = client.chat.completions.create(**request_params)
    payload = to_plain_data(completion)
    if isinstance(payload, dict):
        return _build_chat_completion_nonstream_result(dict(payload))
    return _build_chat_completion_nonstream_result({})


def _iter_openai_compatible_sse_events(response: httpx.Response) -> Any:
    data_lines: list[str] = []
    for raw_line in response.iter_lines():
        line = raw_line if isinstance(raw_line, str) else str(raw_line or "")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\n".join(data_lines)


def collect_openai_compatible_stream_response_direct(
    client: OpenAI,
    *,
    request_params: dict[str, Any],
) -> tuple[Any, str, Any]:
    base_url = openai_compatible_base_url(client)
    api_key = openai_compatible_api_key(client)
    request_url = _build_openai_compatible_request_url(base_url)
    request_payload, extra_headers = _split_openai_compatible_http_request(request_params)
    trust_env_modes: list[bool | None] = [None]
    if _should_trust_environment_http_settings(base_url):
        trust_env_modes.append(False)

    for trust_env_override in trust_env_modes:
        with _build_openai_compatible_http_client(base_url, trust_env_override=trust_env_override) as http_client:
            headers = _build_openai_compatible_request_headers(
                api_key,
                stream=True,
                extra_headers=extra_headers,
            )
            try:
                with http_client.stream("POST", request_url, headers=headers, json=request_payload) as response:
                    if response.status_code == 202:
                        polled = _poll_openai_compatible_pending_response(
                            client,
                            pending_response=response,
                            extra_headers=extra_headers,
                            trust_env_override=trust_env_override,
                        )
                        try:
                            payload = safe_json_loads(polled.text)
                        finally:
                            polled.close()
                        if isinstance(payload, dict):
                            payload.setdefault("_codex_transport", "chat.completion.polled")
                            return _build_chat_completion_nonstream_result(payload)
                        return _build_chat_completion_nonstream_result({})
                    if response.status_code >= 400:
                        _raise_openai_compatible_http_error(response)

                    content_type = str(response.headers.get("content-type") or "").lower()
                    if "application/json" in content_type:
                        response.read()
                        payload = safe_json_loads(response.text)
                        if isinstance(payload, dict):
                            payload.setdefault("_codex_transport", "chat.completion.stream.json")
                            return _build_chat_completion_nonstream_result(payload)
                        return _build_chat_completion_nonstream_result({})

                    def stream_chunks() -> Any:
                        for event_text in _iter_openai_compatible_sse_events(response):
                            if event_text == "[DONE]":
                                break
                            chunk_payload = safe_json_loads(event_text)
                            if isinstance(chunk_payload, dict):
                                yield chunk_payload

                    return _build_chat_completion_stream_result(stream_chunks())
            except httpx.HTTPError as error:
                if should_retry_openai_compatible_without_env_proxy(
                    error,
                    base_url=base_url,
                    trust_env=trust_env_override is not False,
                ):
                    continue
                request = getattr(error, "request", None) or httpx.Request("POST", request_url)
                raise openai.APIConnectionError(request=request) from error

    raise openai.APIConnectionError(request=httpx.Request("POST", request_url))


def collect_openai_compatible_response_direct(
    client: OpenAI,
    *,
    request_params: dict[str, Any],
) -> tuple[Any, str, Any]:
    base_url = openai_compatible_base_url(client)
    api_key = openai_compatible_api_key(client)
    request_url = _build_openai_compatible_request_url(base_url)
    request_payload, extra_headers = _split_openai_compatible_http_request(request_params)
    trust_env_modes: list[bool | None] = [None]
    if _should_trust_environment_http_settings(base_url):
        trust_env_modes.append(False)

    for trust_env_override in trust_env_modes:
        with _build_openai_compatible_http_client(base_url, trust_env_override=trust_env_override) as http_client:
            headers = _build_openai_compatible_request_headers(
                api_key,
                stream=False,
                extra_headers=extra_headers,
            )
            try:
                response = http_client.post(request_url, headers=headers, json=request_payload)
            except httpx.HTTPError as error:
                if should_retry_openai_compatible_without_env_proxy(
                    error,
                    base_url=base_url,
                    trust_env=trust_env_override is not False,
                ):
                    continue
                request = getattr(error, "request", None) or httpx.Request("POST", request_url)
                raise openai.APIConnectionError(request=request) from error

            if response.status_code == 202:
                response = _poll_openai_compatible_pending_response(
                    client,
                    pending_response=response,
                    extra_headers=extra_headers,
                    trust_env_override=trust_env_override,
                )
            if response.status_code >= 400:
                _raise_openai_compatible_http_error(response)

            content_type = str(response.headers.get("content-type") or "").lower()
            if "text/event-stream" in content_type:
                try:
                    return _build_chat_completion_stream_result(
                        chunk_payload
                        for event_text in _iter_openai_compatible_sse_events(response)
                        if event_text != "[DONE]"
                        for chunk_payload in [safe_json_loads(event_text)]
                        if isinstance(chunk_payload, dict)
                    )
                finally:
                    response.close()

            payload = safe_json_loads(response.text)
            try:
                if isinstance(payload, dict):
                    payload.setdefault("_codex_transport", "chat.completion.nonstream")
                    return _build_chat_completion_nonstream_result(payload)
                return _build_chat_completion_nonstream_result({})
            finally:
                response.close()

    raise openai.APIConnectionError(request=httpx.Request("POST", request_url))


def chat_completion_preview(raw_json: Any, *, limit: int = 600) -> str:
    plain = to_plain_data(raw_json)
    if not isinstance(plain, dict):
        return ""
    choices = plain.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:limit]
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            arguments = str(function.get("arguments") or "").strip()
            preview = f"{name}: {arguments}" if name else arguments
            if preview:
                return preview[:limit]
    return ""


def extract_text_candidates_from_response(response: Any) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        candidates.append(("response.output_text", output_text.strip()))

    output = to_plain_data(getattr(response, "output", None))
    if isinstance(output, list):
        for index, item in enumerate(output):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                append_candidate_text(candidates, f"response.output[{index}].content", item.get("content"))
            elif "text" in item:
                append_candidate_text(candidates, f"response.output[{index}].text", item.get("text"))
            elif "output" in item:
                append_candidate_text(candidates, f"response.output[{index}].output", item.get("output"))
    return candidates


def extract_text_candidates_from_raw_json(payload: Any) -> list[tuple[str, str]]:
    plain = to_plain_data(payload)
    candidates: list[tuple[str, str]] = []
    if not isinstance(plain, dict):
        return candidates

    if isinstance(plain.get("output_text"), str) and plain["output_text"].strip():
        candidates.append(("raw_json.output_text", plain["output_text"].strip()))

    if "output" in plain:
        append_candidate_text(candidates, "raw_json.output", plain["output"])

    choices = plain.get("choices")
    if isinstance(choices, list):
        for index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            for key in ("message", "delta", "content", "text"):
                if key in choice:
                    append_candidate_text(candidates, f"raw_json.choices[{index}].{key}", choice[key])

    for key in ("message", "content", "response", "result", "data", "completion", "answer", "generated_text"):
        if key in plain:
            append_candidate_text(candidates, f"raw_json.{key}", plain[key])

    return candidates


def dedupe_text_candidates(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for source, text in candidates:
        normalized = text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append((source, normalized))
    return deduped


def looks_like_response_envelope(payload: Any) -> bool:
    plain = to_plain_data(payload)
    if not isinstance(plain, dict):
        return False
    if plain.get("object") == "response":
        return True
    envelope_keys = {
        "id",
        "status",
        "model",
        "output",
        "choices",
        "usage",
        "created",
        "created_at",
        "incomplete_details",
    }
    return bool(envelope_keys.intersection(plain.keys()))


def extract_response_text(
    response: Any,
    *,
    raw_body_text: str = "",
    raw_json: Any = None,
) -> tuple[str, str]:
    candidates = dedupe_text_candidates(
        extract_text_candidates_from_response(response) + extract_text_candidates_from_raw_json(raw_json)
    )
    if candidates:
        source, text = candidates[0]
        return text, source

    stripped_body = raw_body_text.strip()
    if stripped_body and raw_json is not None and not looks_like_response_envelope(raw_json):
        return stripped_body, "raw_body_json"
    if stripped_body and not stripped_body.startswith("{") and not stripped_body.startswith("["):
        return stripped_body, "raw_body_text"

    return "", ""


def response_identity(response: Any, raw_json: Any = None) -> tuple[str, str, int]:
    response_id = str(getattr(response, "id", "") or "")
    status = str(getattr(response, "status", "") or "")
    output_items = len(getattr(response, "output", []) or [])

    plain = to_plain_data(raw_json)
    if isinstance(plain, dict):
        if not response_id and plain.get("id") is not None:
            response_id = str(plain.get("id"))
        if not status and plain.get("status") is not None:
            status = str(plain.get("status"))
        if not output_items:
            if isinstance(plain.get("output"), list):
                output_items = len(plain["output"])
            elif isinstance(plain.get("choices"), list):
                output_items = len(plain["choices"])

    return response_id or "unknown", status or "unknown", output_items


def response_output_types(response: Any, raw_json: Any = None) -> list[str]:
    types: list[str] = []
    output = to_plain_data(getattr(response, "output", None))
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                types.append(item["type"])

    plain = to_plain_data(raw_json)
    if not types and isinstance(plain, dict) and isinstance(plain.get("output"), list):
        for item in plain["output"]:
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                types.append(item["type"])

    deduped: list[str] = []
    seen: set[str] = set()
    for item_type in types:
        if item_type in seen:
            continue
        seen.add(item_type)
        deduped.append(item_type)
    return deduped


def build_response_preview(
    response: Any,
    *,
    raw_body_text: str = "",
    raw_json: Any = None,
    limit: int = 600,
) -> str:
    text, _ = extract_response_text(response, raw_body_text=raw_body_text, raw_json=raw_json)
    if text:
        return text[:limit]

    output = getattr(response, "output", None)
    if output:
        return str(output)[:limit]
    if raw_body_text.strip():
        return raw_body_text[:limit]
    return ""


T = TypeVar("T", bound=BaseModel)


@dataclass
class StructuredResponseResult(Generic[T]):
    parsed: T
    response_id: str | None
    status: str
    output_types: list[str]
    preview: str
    raw_body_text: str
    raw_json: Any
    token_usage: TokenUsage = field(default_factory=empty_token_usage)


@dataclass
class FunctionToolResult(Generic[T]):
    parsed: T
    response_id: str | None
    status: str
    output_types: list[str]
    preview: str
    raw_body_text: str
    raw_json: Any
    token_usage: TokenUsage = field(default_factory=empty_token_usage)


@dataclass(frozen=True)
class FunctionToolSpec(Generic[T]):
    model: type[T]
    name: str
    description: str
    compatible_aliases: tuple[str, ...] = ()

    def chat_completion_names(self) -> list[str]:
        names = [self.name]
        for alias in self.compatible_aliases:
            cleaned = str(alias or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
        return names


def build_tool_spec_lookup(
    tool_specs: list[FunctionToolSpec[Any]],
) -> tuple[dict[str, FunctionToolSpec[Any]], dict[str, str]]:
    tool_specs_by_lookup: dict[str, FunctionToolSpec[Any]] = {}
    canonical_name_by_lookup: dict[str, str] = {}
    for spec in tool_specs:
        for tool_name in spec.chat_completion_names():
            tool_specs_by_lookup[tool_name] = spec
            canonical_name_by_lookup[tool_name] = spec.name
    return tool_specs_by_lookup, canonical_name_by_lookup


@dataclass
class MultiFunctionToolResult:
    tool_name: str
    parsed: BaseModel
    response_id: str | None
    status: str
    output_types: list[str]
    preview: str
    raw_body_text: str
    raw_json: Any
    token_usage: TokenUsage = field(default_factory=empty_token_usage)
    call_id: str = ""
    raw_arguments: str = ""
    assistant_reasoning_content: str | None = None


def _coerce_tool_arguments_dict(arguments: Any) -> dict[str, Any] | None:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        loaded = safe_json_loads(arguments)
        if isinstance(loaded, dict):
            return loaded
    return None


def _coerce_function_tool_arguments(
    response: Any,
    tool_model: type[T],
    *,
    tool_name: str,
    raw_body_text: str = "",
    raw_json: Any = None,
) -> tuple[T | None, str]:
    output = getattr(response, "output", None) or []
    for item in output:
        item_type = getattr(item, "type", None)
        item_name = getattr(item, "name", None)
        if item_type != "function_call" or item_name != tool_name:
            continue
        parsed_arguments = getattr(item, "parsed_arguments", None)
        if isinstance(parsed_arguments, tool_model):
            return parsed_arguments, "function_call.parsed_arguments"
        if parsed_arguments is not None:
            try:
                return tool_model.model_validate(parsed_arguments), "function_call.parsed_arguments"
            except Exception:
                pass
        arguments = getattr(item, "arguments", None)
        loaded = _coerce_tool_arguments_dict(arguments)
        if isinstance(loaded, dict):
            try:
                return tool_model.model_validate(loaded), "function_call.arguments"
            except Exception:
                pass

    plain = to_plain_data(raw_json)
    if isinstance(plain, dict):
        raw_output = plain.get("output")
        if isinstance(raw_output, list):
            for item in raw_output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "function_call" or item.get("name") != tool_name:
                    continue
                arguments = item.get("arguments")
                loaded = _coerce_tool_arguments_dict(arguments)
                if isinstance(loaded, dict):
                    try:
                        return tool_model.model_validate(loaded), "raw_json.output.function_call.arguments"
                    except Exception:
                        pass

        choices = plain.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                function_call = message.get("function_call")
                if isinstance(function_call, dict):
                    item_name = function_call.get("name")
                    if item_name == tool_name:
                        arguments = function_call.get("arguments")
                        loaded = _coerce_tool_arguments_dict(arguments)
                        if isinstance(loaded, dict):
                            try:
                                return tool_model.model_validate(loaded), "raw_json.choices.message.function_call.arguments"
                            except Exception:
                                pass
                tool_calls = message.get("tool_calls")
                if not isinstance(tool_calls, list):
                    continue
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function")
                    if not isinstance(function, dict):
                        continue
                    if function.get("name") != tool_name:
                        continue
                    arguments = function.get("arguments")
                    loaded = _coerce_tool_arguments_dict(arguments)
                    if isinstance(loaded, dict):
                        try:
                            return tool_model.model_validate(loaded), "raw_json.choices.message.tool_calls.arguments"
                        except Exception:
                            pass

    return None, ""


def _coerce_any_function_tool_arguments(
    response: Any,
    tool_specs_by_name: dict[str, FunctionToolSpec[Any]],
    *,
    canonical_name_by_lookup: dict[str, str] | None = None,
    raw_body_text: str = "",
    raw_json: Any = None,
) -> tuple[BaseModel | None, str, str, str, str]:
    output = to_plain_data(getattr(response, "output", None)) or []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call":
                continue
            item_name = item.get("name")
            if not isinstance(item_name, str):
                continue
            spec = tool_specs_by_name.get(item_name)
            if spec is None:
                continue
            canonical_name = (canonical_name_by_lookup or {}).get(item_name, spec.name)
            call_id = str(item.get("call_id") or item.get("id") or "")
            parsed_arguments = item.get("parsed_arguments")
            if isinstance(parsed_arguments, spec.model):
                return parsed_arguments, canonical_name, "function_call.parsed_arguments", call_id, ""
            if parsed_arguments is not None:
                try:
                    return spec.model.model_validate(parsed_arguments), canonical_name, "function_call.parsed_arguments", call_id, json.dumps(parsed_arguments, ensure_ascii=False)
                except Exception:
                    pass
            arguments = item.get("arguments")
            loaded = _coerce_tool_arguments_dict(arguments)
            if isinstance(loaded, dict):
                try:
                    raw_arguments = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
                    return spec.model.model_validate(loaded), canonical_name, "function_call.arguments", call_id, raw_arguments
                except Exception:
                    pass

    plain = to_plain_data(raw_json)
    if isinstance(plain, dict):
        raw_output = plain.get("output")
        if isinstance(raw_output, list):
            for item in raw_output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "function_call":
                    continue
                item_name = item.get("name")
                if not isinstance(item_name, str):
                    continue
                spec = tool_specs_by_name.get(item_name)
                if spec is None:
                    continue
                canonical_name = (canonical_name_by_lookup or {}).get(item_name, spec.name)
                call_id = str(item.get("call_id") or item.get("id") or "")
                arguments = item.get("arguments")
                loaded = _coerce_tool_arguments_dict(arguments)
                if isinstance(loaded, dict):
                    try:
                        raw_arguments = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
                        return (
                            spec.model.model_validate(loaded),
                            canonical_name,
                            "raw_json.output.function_call.arguments",
                            call_id,
                            raw_arguments,
                        )
                    except Exception:
                        pass

        choices = plain.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                tool_calls = message.get("tool_calls")
                if not isinstance(tool_calls, list):
                    continue
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function")
                    if not isinstance(function, dict):
                        continue
                    item_name = function.get("name")
                    if not isinstance(item_name, str):
                        continue
                    spec = tool_specs_by_name.get(item_name)
                    if spec is None:
                        continue
                    canonical_name = (canonical_name_by_lookup or {}).get(item_name, spec.name)
                    call_id = str(tool_call.get("id") or "")
                    arguments = function.get("arguments")
                    loaded = _coerce_tool_arguments_dict(arguments)
                    if isinstance(loaded, dict):
                        try:
                            raw_arguments = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
                            return (
                                spec.model.model_validate(loaded),
                                canonical_name,
                                "raw_json.choices.message.tool_calls.arguments",
                                call_id,
                                raw_arguments,
                            )
                        except Exception:
                            pass

    return None, "", "", "", ""


def _coerce_parsed_payload(
    response: Any,
    text_format: type[T],
    *,
    raw_body_text: str = "",
    raw_json: Any = None,
) -> tuple[T | None, str]:
    parsed_payload = getattr(response, "output_parsed", None)
    if isinstance(parsed_payload, text_format):
        return parsed_payload, "structured_output"

    if parsed_payload is not None:
        try:
            return text_format.model_validate(parsed_payload), "structured_output"
        except Exception:
            pass

    fallback_text, fallback_source = extract_response_text(
        response,
        raw_body_text=raw_body_text,
        raw_json=raw_json,
    )
    fallback_json = safe_json_loads(fallback_text)
    if isinstance(fallback_json, dict):
        try:
            return text_format.model_validate(fallback_json), f"{fallback_source or 'response_text'} -> schema"
        except Exception:
            pass

    return None, ""


def call_structured_output(
    client: OpenAI,
    *,
    model: str,
    instructions: str,
    user_input: str,
    text_format: type[T],
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
    retries: int = DEFAULT_API_RETRIES,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
) -> StructuredResponseResult[T]:
    last_error: Exception | None = None
    request_chars = estimate_request_text_chars(instructions, user_input)

    for attempt in range(1, retries + 1):
        activity = StatusSpinner("思考中")
        raw_body_text = ""
        raw_json: Any = None
        response: Any = None
        try:
            activity.start()
            request_params: dict[str, Any] = {
                "model": model,
                "instructions": instructions,
                "input": user_input,
                "reasoning": {"effort": DEFAULT_REASONING_EFFORT},
                "text_format": text_format,
                "store": True,
            }
            if previous_response_id:
                request_params["previous_response_id"] = previous_response_id
            if prompt_cache_key:
                request_params["prompt_cache_key"] = prompt_cache_key

            activity.set_status("生成中")
            response, raw_body_text, raw_json = collect_stream_response(
                client,
                request_params=request_params,
            )
        except Exception as error:
            activity.stop()
            abort_retries = should_abort_transport_retries(
                error,
                protocol=PROTOCOL_RESPONSES,
                request_chars=request_chars,
                attempt=attempt,
            )
            last_error = ApiRequestError(
                format_transport_error_message(
                    error,
                    protocol=PROTOCOL_RESPONSES,
                    request_chars=request_chars,
                    abort_retries=abort_retries,
                )
            )
            if abort_retries or attempt >= retries:
                break
            if attempt < retries:
                _print_retry_notice(
                    attempt=attempt,
                    retries=retries,
                    retry_delay_seconds=retry_delay_seconds,
                    stage="接口请求",
                    error=last_error,
                )
                time.sleep(retry_delay_seconds)
            continue

        response_id, status, output_items = response_identity(response, raw_json)
        output_types = response_output_types(response, raw_json)
        token_usage = extract_token_usage(raw_json)
        preview = build_response_preview(response, raw_body_text=raw_body_text, raw_json=raw_json)
        parsed_payload, extraction_source = _coerce_parsed_payload(
            response,
            text_format,
            raw_body_text=raw_body_text,
            raw_json=raw_json,
        )

        activity.stop(
            f"已接收回复，来源：{extraction_source or 'unknown'}，response_id={response_id}，"
            f"status={status}，output={','.join(output_types) or 'none'}，"
            f"{token_usage_summary(token_usage)}。"
        )

        if parsed_payload is not None:
            return StructuredResponseResult(
                parsed=parsed_payload,
                response_id=response_id,
                status=status,
                output_types=output_types,
                token_usage=token_usage,
                preview=preview,
                raw_body_text=raw_body_text,
                raw_json=raw_json,
            )

        last_error = ModelOutputError(
            build_extraction_error_message(
                target_label="结构化响应",
                response_id=response_id,
                status=status,
                output_items=output_items,
                output_types=output_types,
                raw_json=raw_json,
            ),
            preview=preview,
        )
        if attempt < retries:
            _print_retry_notice(
                attempt=attempt,
                retries=retries,
                retry_delay_seconds=retry_delay_seconds,
                stage=extraction_retry_stage(status=status, default_stage="结构化结果提取"),
                error=last_error,
            )
            time.sleep(retry_delay_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("调用 Responses API 失败：未知错误。")


def call_function_tool(
    client: OpenAI,
    *,
    model: str,
    instructions: str,
    user_input: str,
    tool_model: type[T],
    tool_name: str,
    tool_description: str,
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
    retries: int = DEFAULT_API_RETRIES,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
) -> FunctionToolResult[T]:
    tool_choice: Any = {"type": "function", "name": tool_name}
    result = call_function_tools(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        tool_specs=[FunctionToolSpec(model=tool_model, name=tool_name, description=tool_description)],
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
        tool_choice=tool_choice,
    )
    if result.tool_name != tool_name:
        raise ModelOutputError(f"模型调用了意外工具：{result.tool_name}，期望工具：{tool_name}")
    return FunctionToolResult(
        parsed=tool_model.model_validate(result.parsed),
        response_id=result.response_id,
        status=result.status,
        output_types=result.output_types,
        token_usage=result.token_usage,
        preview=result.preview,
        raw_body_text=result.raw_body_text,
        raw_json=result.raw_json,
    )


def call_function_tools(
    client: OpenAI,
    *,
    model: str,
    instructions: str,
    user_input: Any,
    tool_specs: list[FunctionToolSpec[Any]],
    previous_response_id: str | None = None,
    prompt_cache_key: str | None = None,
    retries: int = DEFAULT_API_RETRIES,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
    tool_choice: Any = "auto",
    chat_messages: list[dict[str, Any]] | None = None,
    store: bool = True,
) -> MultiFunctionToolResult:
    last_error: Exception | None = None
    if not tool_specs:
        raise ValueError("tool_specs 不能为空。")
    tool_specs_by_name, canonical_name_by_lookup = build_tool_spec_lookup(tool_specs)
    protocol = runtime_protocol(client)
    request_chars = estimate_request_text_chars(instructions, user_input)

    for attempt in range(1, retries + 1):
        activity = StatusSpinner("思考中")
        raw_body_text = ""
        raw_json: Any = None
        response: Any = None
        compatible_transport_attempts: list[str] = []
        try:
            activity.start()
            activity.set_status("生成中")
            if protocol == PROTOCOL_OPENAI_COMPATIBLE:
                compatible_reasoning = compatible_reasoning_effort(client, model=model)
                extra_body, extra_headers = resolve_openai_compatible_request_extras(
                    client,
                    prompt_cache_key=prompt_cache_key,
                    model=model,
                    reasoning_effort=compatible_reasoning,
                )
                base_request_params = {
                    "model": model,
                    "messages": chat_messages
                    if chat_messages is not None
                    else [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": user_input},
                    ],
                    "tools": build_chat_completion_tools(tool_specs),
                }
                if compatible_reasoning:
                    base_request_params["reasoning_effort"] = compatible_reasoning
                if extra_body:
                    base_request_params["extra_body"] = extra_body
                if extra_headers:
                    base_request_params["extra_headers"] = extra_headers
                transport_candidates = compatible_chat_transport_candidates(
                    client,
                    request_chars=request_chars,
                )
                last_compatible_error: Exception | None = None
                for chat_tool_choice in build_chat_tool_choice_candidates(tool_choice):
                    request_params = dict(base_request_params)
                    request_params["tool_choice"] = chat_tool_choice
                    for transport_index, candidate_transport in enumerate(transport_candidates):
                        if candidate_transport not in compatible_transport_attempts:
                            compatible_transport_attempts.append(candidate_transport)
                        try:
                            response, raw_body_text, raw_json = request_compatible_chat_completion(
                                client,
                                request_params=request_params,
                                transport_mode=candidate_transport,
                            )
                            last_compatible_error = None
                            break
                        except Exception as compatible_error:
                            last_compatible_error = compatible_error
                            if should_retry_legacy_chat_tool_choice(compatible_error):
                                break
                            has_alternate_transport = transport_index + 1 < len(transport_candidates)
                            if has_alternate_transport and should_retry_with_alternate_chat_transport(
                                compatible_error,
                                request_chars=request_chars,
                            ):
                                continue
                            raise
                    if last_compatible_error is None:
                        break
                    if should_retry_legacy_chat_tool_choice(last_compatible_error):
                        continue
                if last_compatible_error is not None:
                    raise last_compatible_error
            else:
                request_params = {
                    "model": model,
                    "instructions": instructions,
                    "input": user_input,
                    "reasoning": {"effort": DEFAULT_REASONING_EFFORT},
                    "tools": build_responses_function_tools(tool_specs),
                    "tool_choice": tool_choice,
                    "parallel_tool_calls": False,
                    "store": store,
                }
                if previous_response_id:
                    request_params["previous_response_id"] = previous_response_id
                if prompt_cache_key:
                    request_params["prompt_cache_key"] = prompt_cache_key

                response, raw_body_text, raw_json = collect_stream_response(
                    client,
                    request_params=request_params,
                )
        except Exception as error:
            activity.stop()
            abort_retries = should_abort_transport_retries(
                error,
                protocol=protocol,
                request_chars=request_chars,
                attempt=attempt,
            )
            last_error = ApiRequestError(
                format_transport_error_message(
                    error,
                    protocol=protocol,
                    request_chars=request_chars,
                    abort_retries=abort_retries,
                    compatible_transport_attempts=compatible_transport_attempts,
                )
            )
            if abort_retries or attempt >= retries:
                break
            if attempt < retries:
                _print_retry_notice(
                    attempt=attempt,
                    retries=retries,
                    retry_delay_seconds=retry_delay_seconds,
                    stage="接口请求",
                    error=last_error,
                )
                time.sleep(retry_delay_seconds)
            continue

        response_id, status, output_items = response_identity(response, raw_json)
        if protocol == PROTOCOL_OPENAI_COMPATIBLE:
            status = "completed"
            output_types = ["chat.completion"]
            plain = to_plain_data(raw_json)
            if isinstance(plain, dict):
                choices = plain.get("choices")
                if isinstance(choices, list):
                    for choice in choices:
                        if not isinstance(choice, dict):
                            continue
                        message = choice.get("message")
                        if not isinstance(message, dict):
                            continue
                        if message.get("tool_calls") or message.get("function_call"):
                            output_types.append("tool_calls")
                            break
            preview = chat_completion_preview(raw_json) or build_response_preview(
                response,
                raw_body_text=raw_body_text,
                raw_json=raw_json,
            )
        else:
            output_types = response_output_types(response, raw_json)
            preview = build_response_preview(response, raw_body_text=raw_body_text, raw_json=raw_json)
        assistant_reasoning_content: str | None = None
        if protocol == PROTOCOL_OPENAI_COMPATIBLE and _looks_like_deepseek_v4_model(model):
            assistant_reasoning_content = extract_chat_completion_reasoning_content(raw_json)
            if assistant_reasoning_content is None:
                # Keep parity with DeepSeek/OpenCode interleaved-turn expectations:
                # assistant turns should still carry reasoning_content even when empty.
                assistant_reasoning_content = ""
        extra_cache_read_paths: list[tuple[str, ...]] = []
        extra_cache_write_paths: list[tuple[str, ...]] = []
        if protocol == PROTOCOL_OPENAI_COMPATIBLE:
            extra_cache_read_paths, extra_cache_write_paths = compatible_usage_extra_paths(client)
        token_usage = extract_token_usage(
            raw_json,
            extra_cache_read_paths=extra_cache_read_paths,
            extra_cache_write_paths=extra_cache_write_paths,
        )
        parsed_payload, parsed_tool_name, extraction_source, call_id, raw_arguments = _coerce_any_function_tool_arguments(
            response,
            tool_specs_by_name,
            canonical_name_by_lookup=canonical_name_by_lookup,
            raw_body_text=raw_body_text,
            raw_json=raw_json,
        )

        activity.stop(
            f"已接收回复，来源：{extraction_source or 'unknown'}，response_id={response_id}，"
            f"status={status}，output={','.join(output_types) or 'none'}，"
            f"{token_usage_summary(token_usage)}。"
        )

        if parsed_payload is not None and parsed_tool_name:
            return MultiFunctionToolResult(
                tool_name=parsed_tool_name,
                parsed=parsed_payload,
                response_id=response_id,
                status=status,
                output_types=output_types,
                token_usage=token_usage,
                preview=preview,
                raw_body_text=raw_body_text,
                raw_json=raw_json,
                call_id=call_id,
                raw_arguments=raw_arguments,
                assistant_reasoning_content=assistant_reasoning_content,
            )

        last_error = ModelOutputError(
            build_extraction_error_message(
                target_label="函数工具调用",
                response_id=response_id,
                status=status,
                output_items=output_items,
                output_types=output_types,
                raw_json=raw_json,
            ),
            preview=preview,
            raw_body_text=raw_body_text,
        )
        if attempt < retries:
            _print_retry_notice(
                attempt=attempt,
                retries=retries,
                retry_delay_seconds=retry_delay_seconds,
                stage=extraction_retry_stage(status=status, default_stage="函数工具参数提取"),
                error=last_error,
            )
            time.sleep(retry_delay_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("调用 Responses API 失败：未知错误。")
