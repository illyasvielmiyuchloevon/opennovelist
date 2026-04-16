from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Generic, TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel, Field

from .ui import print_progress


DEFAULT_API_RETRIES = 10
DEFAULT_RETRY_DELAY_SECONDS = 5
DEFAULT_REASONING_EFFORT = "medium"


class ApiRequestError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    def __init__(self, message: str, preview: str = "", raw_body_text: str = "") -> None:
        super().__init__(message)
        self.preview = preview
        self.raw_body_text = raw_body_text


class MarkdownDocumentPayload(BaseModel):
    content_md: str = Field(..., description="Markdown document body to be written to the target file.")


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


def build_openai_client(*, api_key: str, base_url: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


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
    stripped = text.strip()
    if not stripped:
        return None
    try:
        loaded = json.loads(stripped)
    except Exception:
        return None
    if isinstance(loaded, (dict, list)):
        return loaded
    return None


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


def collect_stream_response(
    client: OpenAI,
    *,
    request_params: dict[str, Any],
) -> tuple[Any, str, Any]:
    response_payload: dict[str, Any] = {}
    output_items_by_index: dict[int, dict[str, Any]] = {}

    with client.responses.stream(**request_params) as stream:
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

    output_items = [output_items_by_index[index] for index in sorted(output_items_by_index)]
    response_payload = dict(response_payload)
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


@dataclass
class FunctionToolResult(Generic[T]):
    parsed: T
    response_id: str | None
    status: str
    output_types: list[str]
    preview: str
    raw_body_text: str
    raw_json: Any


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
        if isinstance(arguments, str):
            loaded = safe_json_loads(arguments)
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
                if isinstance(arguments, str):
                    loaded = safe_json_loads(arguments)
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
                    if isinstance(arguments, str):
                        loaded = safe_json_loads(arguments)
                        if isinstance(loaded, dict):
                            try:
                                return tool_model.model_validate(loaded), "raw_json.choices.message.tool_calls.arguments"
                            except Exception:
                                pass

    return None, ""


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
            last_error = ApiRequestError(
                f"接口请求失败（{type(error).__name__}，发生在 SDK 预处理或发送阶段）：{error}"
            )
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
        preview = build_response_preview(response, raw_body_text=raw_body_text, raw_json=raw_json)
        parsed_payload, extraction_source = _coerce_parsed_payload(
            response,
            text_format,
            raw_body_text=raw_body_text,
            raw_json=raw_json,
        )

        activity.stop(
            f"已接收回复，来源：{extraction_source or 'unknown'}，response_id={response_id}，"
            f"output={','.join(output_types) or 'none'}。"
        )

        if parsed_payload is not None:
            return StructuredResponseResult(
                parsed=parsed_payload,
                response_id=response_id,
                status=status,
                output_types=output_types,
                preview=preview,
                raw_body_text=raw_body_text,
                raw_json=raw_json,
            )

        last_error = ModelOutputError(
            "模型回复已完成，但未能从结构化响应中提取所需字段。"
            f" response_id={response_id},"
            f" status={status},"
            f" output_items={output_items},"
            f" output_types={output_types or ['none']}",
            preview=preview,
        )
        if attempt < retries:
            _print_retry_notice(
                attempt=attempt,
                retries=retries,
                retry_delay_seconds=retry_delay_seconds,
                stage="结构化结果提取",
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
    last_error: Exception | None = None

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
                "tools": [
                    openai.pydantic_function_tool(
                        tool_model,
                        name=tool_name,
                        description=tool_description,
                    )
                ],
                "tool_choice": {"type": "function", "name": tool_name},
                "parallel_tool_calls": False,
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
            last_error = ApiRequestError(
                f"接口请求失败（{type(error).__name__}，发生在 SDK 预处理或发送阶段）：{error}"
            )
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
        preview = build_response_preview(response, raw_body_text=raw_body_text, raw_json=raw_json)
        parsed_payload, extraction_source = _coerce_function_tool_arguments(
            response,
            tool_model,
            tool_name=tool_name,
            raw_body_text=raw_body_text,
            raw_json=raw_json,
        )

        activity.stop(
            f"已接收回复，来源：{extraction_source or 'unknown'}，response_id={response_id}，"
            f"output={','.join(output_types) or 'none'}。"
        )

        if parsed_payload is not None:
            return FunctionToolResult(
                parsed=parsed_payload,
                response_id=response_id,
                status=status,
                output_types=output_types,
                preview=preview,
                raw_body_text=raw_body_text,
                raw_json=raw_json,
            )

        last_error = ModelOutputError(
            "模型回复已完成，但未能从函数工具调用中提取所需字段。"
            f" response_id={response_id},"
            f" status={status},"
            f" output_items={output_items},"
            f" output_types={output_types or ['none']}",
            preview=preview,
            raw_body_text=raw_body_text,
        )
        if attempt < retries:
            _print_retry_notice(
                attempt=attempt,
                retries=retries,
                retry_delay_seconds=retry_delay_seconds,
                stage="函数工具参数提取",
                error=last_error,
            )
            time.sleep(retry_delay_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("调用 Responses API 失败：未知错误。")
