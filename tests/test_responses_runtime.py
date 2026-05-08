from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
from pydantic import BaseModel

from novelist.core import agent_runtime
from novelist.core import document_ops
from novelist.core import responses_runtime as llm_runtime
from novelist.core import workflow_tools


class _CompatToolPayload(BaseModel):
    value: str


class _FakeChatCompletionChunk:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self) -> dict:
        return self._payload


class _FakeChatCompletionResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self) -> dict:
        return self._payload


class _FakeStream:
    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks

    def __iter__(self):
        for chunk in self._chunks:
            yield _FakeChatCompletionChunk(chunk)

    def close(self) -> None:
        return None


class _FakeChatCompletions:
    def __init__(self, stream_chunks: list[dict]) -> None:
        self._stream_chunks = stream_chunks
        self.last_request: dict | None = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return _FakeStream(self._stream_chunks)


class _FailingChatCompletions:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        raise self._error


class _FallbackChatCompletions:
    def __init__(self, success_chunks: list[dict]) -> None:
        self._success_chunks = success_chunks
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        tool_choice = kwargs.get("tool_choice")
        if isinstance(tool_choice, dict) and isinstance(tool_choice.get("function"), dict):
            request = httpx.Request("POST", "https://example.com/v1/chat/completions")
            response = httpx.Response(400, request=request)
            raise openai.BadRequestError(
                "Error code: 400 - {'error': {'message': \"Unknown parameter: 'tool_choice.function'.\"}}",
                response=response,
                body={
                    "error": {
                        "message": "Unknown parameter: 'tool_choice.function'.",
                        "type": "invalid_request_error",
                    }
                },
            )
        return _FakeStream(self._success_chunks)


class _StreamToNonstreamFallbackChatCompletions:
    def __init__(self, response_payload: dict) -> None:
        self._response_payload = response_payload
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream") is False:
            return _FakeChatCompletionResponse(self._response_payload)
        raise openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.com/v1/chat/completions")
        )


class _NonstreamToStreamFallbackChatCompletions:
    def __init__(self, stream_chunks: list[dict]) -> None:
        self._stream_chunks = stream_chunks
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream") is True:
            return _FakeStream(self._stream_chunks)
        raise openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.com/v1/chat/completions")
        )


class _FakeClient:
    def __init__(self, stream_chunks: list[dict], compatible_options: dict | None = None) -> None:
        self._codex_protocol = llm_runtime.PROTOCOL_OPENAI_COMPATIBLE
        self._codex_openai_compatible_options = {"transport": "stream", **(compatible_options or {})}
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(stream_chunks))


class _FakeDirectHttpClient:
    def __init__(self, post_response: httpx.Response) -> None:
        self._post_response = post_response
        self.post_calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def post(self, url: str, *, headers: dict[str, str] | None = None, json: dict | None = None):
        self.post_calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self._post_response


class _FakeStreamContext:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._response.close()
        return False


class _FakeDirectStreamHttpClient:
    def __init__(self, stream_response: httpx.Response) -> None:
        self._stream_response = stream_response
        self.stream_calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def stream(self, method: str, url: str, *, headers: dict[str, str] | None = None, json: dict | None = None):
        self.stream_calls.append({"method": method, "url": url, "headers": headers or {}, "json": json or {}})
        return _FakeStreamContext(self._stream_response)


class _ErroringDirectHttpClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def post(self, url: str, *, headers: dict[str, str] | None = None, json: dict | None = None):
        raise self._error


class _FakeNonStreamChatCompletions:
    def __init__(self, response_payload: dict) -> None:
        self._response_payload = response_payload
        self.last_request: dict | None = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return _FakeChatCompletionResponse(self._response_payload)


class _FakeResponsesStream:
    def __init__(self, events: list[SimpleNamespace], final_response: dict | Exception | None = None) -> None:
        self._events = events
        self._final_response = final_response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def __iter__(self):
        yield from self._events

    def get_final_response(self):
        if isinstance(self._final_response, Exception):
            raise self._final_response
        return self._final_response


class _FakeResponses:
    def __init__(
        self,
        events: list[SimpleNamespace],
        final_response: dict | Exception | None = None,
        retrieve_responses: list[dict] | None = None,
        retrieve_error: Exception | None = None,
        continue_events: list[SimpleNamespace] | None = None,
        continue_final_response: dict | Exception | None = None,
    ) -> None:
        self._events = events
        self._final_response = final_response
        self._retrieve_responses = list(retrieve_responses or [])
        self._retrieve_error = retrieve_error
        self._continue_events = continue_events
        self._continue_final_response = continue_final_response
        self.last_request: dict | None = None
        self.retrieve_calls: list[str] = []
        self.continue_stream_calls: list[str] = []

    def stream(self, **kwargs):
        self.last_request = kwargs
        response_id = kwargs.get("response_id")
        if isinstance(response_id, str):
            self.continue_stream_calls.append(response_id)
            return _FakeResponsesStream(
                self._continue_events or [],
                self._continue_final_response,
            )
        return _FakeResponsesStream(self._events, self._final_response)

    def retrieve(self, response_id: str):
        self.retrieve_calls.append(response_id)
        if self._retrieve_error is not None:
            raise self._retrieve_error
        if self._retrieve_responses:
            return self._retrieve_responses.pop(0)
        return self._final_response


class _FakeResponsesClient:
    def __init__(
        self,
        events: list[SimpleNamespace],
        final_response: dict | Exception | None = None,
        retrieve_responses: list[dict] | None = None,
        retrieve_error: Exception | None = None,
        continue_events: list[SimpleNamespace] | None = None,
        continue_final_response: dict | Exception | None = None,
    ) -> None:
        self._codex_protocol = llm_runtime.PROTOCOL_RESPONSES
        self.responses = _FakeResponses(
            events,
            final_response,
            retrieve_responses,
            retrieve_error,
            continue_events,
            continue_final_response,
        )


class ResponsesRuntimeCompatibleTests(unittest.TestCase):
    def test_build_openai_client_sets_explicit_timeouts_and_disables_sdk_retries(self) -> None:
        captured: dict[str, object] = {}

        class _SentinelClient:
            pass

        def fake_openai(**kwargs):
            captured.update(kwargs)
            return _SentinelClient()

        with patch("novelist.core.responses_runtime.OpenAI", side_effect=fake_openai):
            client = llm_runtime.build_openai_client(
                api_key="test-key",
                base_url="https://api.openai.com/v1",
            )

        self.assertIsInstance(client, _SentinelClient)
        self.assertEqual(captured["api_key"], "test-key")
        self.assertEqual(captured["base_url"], "https://api.openai.com/v1")
        self.assertEqual(captured["max_retries"], 0)
        self.assertIsInstance(captured["http_client"], httpx.Client)
        http_client = captured["http_client"]
        assert isinstance(http_client, httpx.Client)
        timeout = http_client.timeout
        assert isinstance(timeout, httpx.Timeout)
        self.assertEqual(timeout.connect, llm_runtime.DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(timeout.read, llm_runtime.DEFAULT_OPENAI_READ_TIMEOUT_SECONDS)
        self.assertEqual(timeout.write, llm_runtime.DEFAULT_OPENAI_WRITE_TIMEOUT_SECONDS)
        self.assertEqual(timeout.pool, llm_runtime.DEFAULT_OPENAI_POOL_TIMEOUT_SECONDS)
        self.assertTrue(getattr(http_client, "_trust_env"))
        http_client.close()

    def test_build_openai_client_bypasses_environment_proxy_for_local_base_url(self) -> None:
        captured: dict[str, object] = {}

        class _SentinelClient:
            pass

        def fake_openai(**kwargs):
            captured.update(kwargs)
            return _SentinelClient()

        with patch("novelist.core.responses_runtime.OpenAI", side_effect=fake_openai):
            client = llm_runtime.build_openai_client(
                api_key="test-key",
                base_url="http://localhost:8317/v1",
            )

        self.assertIsInstance(client, _SentinelClient)
        self.assertIsInstance(captured["http_client"], httpx.Client)
        http_client = captured["http_client"]
        assert isinstance(http_client, httpx.Client)
        self.assertFalse(getattr(http_client, "_trust_env"))
        http_client.close()

    def test_responses_stream_merges_final_and_reconstructed_function_call_items(self) -> None:
        events = [
            SimpleNamespace(
                type="response.created",
                response={"id": "resp_merge", "status": "in_progress", "output": []},
            ),
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item={"type": "function_call", "name": "submit_tool", "arguments": ""},
            ),
            SimpleNamespace(
                type="response.function_call_arguments.done",
                output_index=0,
                arguments="{\"value\": \"ok\"}",
            ),
            SimpleNamespace(
                type="response.completed",
                response={
                    "id": "resp_merge",
                    "status": "completed",
                    "output": [{"type": "function_call", "arguments": ""}],
                },
            ),
        ]
        client = _FakeResponsesClient(
            events,
            final_response={
                "id": "resp_merge",
                "status": "completed",
                "output": [{"type": "function_call", "arguments": ""}],
            },
        )

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            tool_choice={"type": "function", "name": "submit_tool"},
            retries=1,
        )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(result.response_id, "resp_merge")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.raw_json["output"][0]["name"], "submit_tool")
        self.assertEqual(json.loads(result.raw_json["output"][0]["arguments"]), {"value": "ok"})

    def test_extract_token_usage_matches_opencode_cache_split(self) -> None:
        usage = llm_runtime.extract_token_usage(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 30,
                    "output_tokens_details": {"reasoning_tokens": 7},
                    "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 5},
                    "total_tokens": 130,
                }
            }
        )

        self.assertEqual(usage.input_total, 100)
        self.assertEqual(usage.input, 55)
        self.assertEqual(usage.output, 23)
        self.assertEqual(usage.reasoning, 7)
        self.assertEqual(usage.cache_read, 40)
        self.assertEqual(usage.cache_write, 5)
        self.assertEqual(usage.cache_hit, 40)
        self.assertIn("发送=100", llm_runtime.token_usage_summary(usage))
        self.assertIn("缓存命中=40", llm_runtime.token_usage_summary(usage))

    def test_responses_function_tools_use_flat_responses_schema(self) -> None:
        tools = llm_runtime.build_responses_function_tools(
            [
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ]
        )

        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["name"], "submit_tool")
        self.assertEqual(tools[0]["description"], "test tool")
        self.assertTrue(tools[0]["strict"])
        self.assertIn("parameters", tools[0])
        self.assertNotIn("function", tools[0])

    def test_function_tool_result_carries_response_token_usage(self) -> None:
        events = [
            SimpleNamespace(
                type="response.created",
                response={"id": "resp_usage", "status": "in_progress", "output": []},
            ),
            SimpleNamespace(
                type="response.function_call_arguments.done",
                output_index=0,
                arguments="{\"value\": \"ok\"}",
            ),
            SimpleNamespace(
                type="response.completed",
                response={
                    "id": "resp_usage",
                    "status": "completed",
                    "output": [{"type": "function_call", "name": "submit_tool", "arguments": ""}],
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 20},
                    },
                },
            ),
        ]
        client = _FakeResponsesClient(
            events,
            final_response={
                "id": "resp_usage",
                "status": "completed",
                "output": [{"type": "function_call", "name": "submit_tool", "arguments": ""}],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 20},
                },
            },
        )

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            tool_choice={"type": "function", "name": "submit_tool"},
            retries=1,
        )

        self.assertEqual(result.token_usage.input_total, 50)
        self.assertEqual(result.token_usage.input, 30)
        self.assertEqual(result.token_usage.output, 12)
        self.assertEqual(result.token_usage.cache_hit, 20)

    def test_responses_stream_polls_in_progress_response_before_parsing_tool_call(self) -> None:
        events = [
            SimpleNamespace(
                type="response.created",
                response={"id": "resp_pending", "status": "in_progress", "output": []},
            ),
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item={
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                },
            ),
        ]
        final_pending = {
            "id": "resp_pending",
            "status": "in_progress",
            "output": [
                {
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                }
            ],
        }
        retrieved_completed = {
            "id": "resp_pending",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"ok\"}",
                    "status": "completed",
                }
            ],
        }
        client = _FakeResponsesClient(
            events,
            final_response=final_pending,
            retrieve_responses=[retrieved_completed],
        )

        with patch.object(llm_runtime, "DEFAULT_RESPONSE_POLL_INTERVAL_SECONDS", 0):
            result = llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="test-model",
                instructions="system instruction",
                user_input="user input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                tool_choice={"type": "function", "name": "submit_tool"},
                retries=1,
            )

        self.assertEqual(client.responses.retrieve_calls, ["resp_pending"])
        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(result.status, "completed")

    def test_responses_stream_continues_in_progress_response_by_id_before_retrieve(self) -> None:
        events = [
            SimpleNamespace(
                type="response.created",
                response={"id": "resp_continue", "status": "in_progress", "output": []},
            ),
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item={
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                },
            ),
        ]
        final_pending = {
            "id": "resp_continue",
            "status": "in_progress",
            "output": [
                {
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                }
            ],
        }
        continue_events = [
            SimpleNamespace(
                type="response.output_item.done",
                output_index=0,
                item={
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"ok\"}",
                    "status": "completed",
                },
            ),
            SimpleNamespace(
                type="response.completed",
                response={
                    "id": "resp_continue",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "submit_tool",
                            "arguments": "{\"value\": \"ok\"}",
                            "status": "completed",
                        }
                    ],
                },
            ),
        ]
        client = _FakeResponsesClient(
            events,
            final_response=final_pending,
            continue_events=continue_events,
            continue_final_response={
                "id": "resp_continue",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "submit_tool",
                        "arguments": "{\"value\": \"ok\"}",
                        "status": "completed",
                    }
                ],
            },
        )

        with patch.object(llm_runtime, "DEFAULT_RESPONSE_POLL_INTERVAL_SECONDS", 0):
            result = llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="test-model",
                instructions="system instruction",
                user_input="user input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                tool_choice={"type": "function", "name": "submit_tool"},
                retries=1,
            )

        self.assertEqual(client.responses.continue_stream_calls, ["resp_continue"])
        self.assertEqual(client.responses.retrieve_calls, [])
        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")

    def test_responses_stream_does_not_start_non_streaming_duplicate_request_when_retrieve_is_unavailable(self) -> None:
        events = [
            SimpleNamespace(
                type="response.created",
                response={"id": "resp_pending", "status": "in_progress", "output": []},
            ),
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item={
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                },
            ),
        ]
        final_pending = {
            "id": "resp_pending",
            "status": "in_progress",
            "output": [
                {
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                }
            ],
        }
        request = httpx.Request("GET", "https://example.com/v1/responses/resp_pending")
        response = httpx.Response(404, request=request)
        retrieve_error = openai.NotFoundError("404 page not found", response=response, body=None)
        client = _FakeResponsesClient(
            events,
            final_response=final_pending,
            retrieve_error=retrieve_error,
        )

        with (
            patch.object(llm_runtime, "DEFAULT_RESPONSE_POLL_INTERVAL_SECONDS", 0),
            self.assertRaises(llm_runtime.ModelOutputError) as context,
        ):
            llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="test-model",
                instructions="system instruction",
                user_input="user input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                tool_choice={"type": "function", "name": "submit_tool"},
                retries=1,
            )

        self.assertEqual(client.responses.retrieve_calls, ["resp_pending"])
        self.assertIn("模型响应仍为 in_progress", str(context.exception))
        self.assertIn("retrieve 补取失败", str(context.exception))
        self.assertNotIn("非流式兜底", str(context.exception))

    def test_in_progress_function_call_error_does_not_claim_model_completed(self) -> None:
        events = [
            SimpleNamespace(
                type="response.created",
                response={"id": "resp_pending", "status": "in_progress", "output": []},
            ),
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item={
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                },
            ),
        ]
        final_pending = {
            "id": "resp_pending",
            "status": "in_progress",
            "output": [
                {
                    "type": "function_call",
                    "name": "submit_tool",
                    "arguments": "{\"value\": \"half",
                    "status": "incomplete",
                }
            ],
        }
        retrieve_request = httpx.Request("GET", "https://example.com/v1/responses/resp_pending")
        retrieve_response = httpx.Response(404, request=retrieve_request)
        retrieve_error = openai.NotFoundError("404 page not found", response=retrieve_response, body=None)
        client = _FakeResponsesClient(
            events,
            final_response=final_pending,
            retrieve_error=retrieve_error,
        )

        with self.assertRaises(llm_runtime.ModelOutputError) as context:
            llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="test-model",
                instructions="system instruction",
                user_input="user input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                tool_choice={"type": "function", "name": "submit_tool"},
                retries=1,
            )

        message = str(context.exception)
        self.assertNotIn("模型回复已完成", message)
        self.assertIn("模型响应仍为 in_progress", message)
        self.assertIn("不能按已完成回复解析", message)
        self.assertIn("retrieve 补取失败", message)
        self.assertNotIn("非流式兜底", message)
        self.assertEqual(
            llm_runtime.extraction_retry_stage(status="in_progress", default_stage="函数工具参数提取"),
            "接口响应未完成",
        )

    def test_call_function_tool_uses_named_function_tool_choice_for_compatible(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_auto",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_auto",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        client = _FakeClient(stream_chunks)

        result = llm_runtime.call_function_tool(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_model=_CompatToolPayload,
            tool_name="submit_tool",
            tool_description="test tool",
            retries=1,
        )

        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(
            client.chat.completions.last_request["tool_choice"],
            {"type": "function", "function": {"name": "submit_tool"}},
        )

    def test_call_function_tools_supports_openai_compatible_tool_calls(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\":",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": " \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 90,
                    "completion_tokens": 18,
                    "prompt_tokens_details": {"cached_tokens": 30},
                },
            },
        ]
        client = _FakeClient(stream_chunks)

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            tool_choice={"type": "function", "name": "submit_tool"},
            retries=1,
        )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(result.response_id, "chatcmpl_test")
        self.assertEqual(result.status, "completed")
        self.assertIn("chat.completion", result.output_types)
        self.assertEqual(result.token_usage.input_total, 90)
        self.assertEqual(result.token_usage.input, 60)
        self.assertEqual(result.token_usage.output, 18)
        self.assertEqual(result.token_usage.cache_hit, 30)
        self.assertEqual(client.chat.completions.last_request["messages"][0]["role"], "system")
        self.assertTrue(client.chat.completions.last_request["stream"])
        self.assertEqual(client.chat.completions.last_request["stream_options"], {"include_usage": True})
        self.assertEqual(
            client.chat.completions.last_request["tool_choice"],
            {"type": "function", "function": {"name": "submit_tool"}},
        )

    def test_call_function_tools_openai_compatible_applies_provider_specific_request_extras(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        client = _FakeClient(
            stream_chunks,
            compatible_options={
                "extra_body": {
                    "prompt_cache_key": "{{prompt_cache_key}}",
                    "cache_control": {"type": "ephemeral"},
                },
                "extra_headers": {
                    "x-cache-key": "{{prompt_cache_key}}",
                },
            },
        )

        llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            prompt_cache_key="chapter-rewrite-cache-key",
            retries=1,
        )

        self.assertEqual(
            client.chat.completions.last_request["extra_body"],
            {
                "prompt_cache_key": "chapter-rewrite-cache-key",
                "cache_control": {"type": "ephemeral"},
            },
        )
        self.assertEqual(
            client.chat.completions.last_request["extra_headers"],
            {"x-cache-key": "chapter-rewrite-cache-key"},
        )

    def test_call_function_tools_opencode_go_defaults_prompt_cache_key_passthrough(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        client = _FakeClient(stream_chunks)
        client._codex_provider = "opencode_go"

        llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="kimi-k2.6",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            prompt_cache_key="chapter-rewrite-cache-key",
            retries=1,
        )
        self.assertEqual(
            client.chat.completions.last_request["extra_body"]["prompt_cache_key"],
            "chapter-rewrite-cache-key",
        )

    def test_call_function_tools_openai_compatible_adds_deepseek_reasoning_defaults(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_reasoning",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_reasoning",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        client = _FakeClient(stream_chunks)
        client._codex_base_url = "https://api.deepseek.com"

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="deepseek-v4-pro",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            retries=1,
        )

        self.assertEqual(client.chat.completions.last_request["reasoning_effort"], "high")
        self.assertEqual(result.assistant_reasoning_content, "")
        self.assertEqual(
            client.chat.completions.last_request["extra_body"],
            {"thinking": {"type": "enabled"}},
        )

    def test_call_function_tools_extracts_deepseek_reasoning_content_from_stream(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_reasoning",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "reasoning_content": "alpha ",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_reasoning",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_content": "beta",
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_reasoning",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        client = _FakeClient(stream_chunks)
        client._codex_provider = "opencode_go"

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="deepseek-v4-pro",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            retries=1,
        )

        self.assertEqual(result.assistant_reasoning_content, "alpha beta")

    def test_call_function_tools_openai_compatible_parses_provider_specific_cache_usage_paths(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {
                    "prompt_tokens": 90,
                    "completion_tokens": 18,
                    "nim_cache": {"hit_tokens": 30, "write_tokens": 5},
                },
            },
        ]
        client = _FakeClient(
            stream_chunks,
            compatible_options={
                "cache_read_paths": [["nim_cache", "hit_tokens"]],
                "cache_write_paths": ["nim_cache.write_tokens"],
            },
        )

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            retries=1,
        )

        self.assertEqual(result.token_usage.input_total, 90)
        self.assertEqual(result.token_usage.cache_hit, 30)
        self.assertEqual(result.token_usage.cache_write, 5)
        self.assertEqual(result.token_usage.input, 55)

    def test_call_function_tools_openai_compatible_accepts_alias_tool_name_and_single_file_write_shape(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_alias_write",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "write",
                                        "arguments": "{\"filePath\": \"F:/novelist/out.txt\", \"content\": \"hello\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_alias_write",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        client = _FakeClient(stream_chunks)

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=document_ops.document_tool_specs(),
            retries=1,
        )

        self.assertEqual(result.tool_name, document_ops.DOCUMENT_WRITE_TOOL_NAME)
        self.assertEqual(result.parsed.files[0].file_path, "F:/novelist/out.txt")
        self.assertEqual(result.parsed.files[0].content, "hello")
        request_tool_names = [tool["function"]["name"] for tool in client.chat.completions.last_request["tools"]]
        self.assertIn(document_ops.DOCUMENT_WRITE_TOOL_NAME, request_tool_names)
        self.assertIn("write", request_tool_names)

    def test_call_function_tools_openai_compatible_uses_canonical_workflow_tool_name(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_submit_result",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": workflow_tools.WORKFLOW_SUBMISSION_TOOL_NAME,
                                        "arguments": "{\"content\": \"阶段摘要\", \"files\": [\"rewritten_chapter\"], \"summary\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_submit_result",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        client = _FakeClient(stream_chunks)

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[workflow_tools.workflow_submission_tool_spec()],
            retries=1,
        )

        self.assertEqual(result.tool_name, workflow_tools.WORKFLOW_SUBMISSION_TOOL_NAME)
        self.assertEqual(result.parsed.content_md, "阶段摘要")
        self.assertEqual(result.parsed.generated_files, ["rewritten_chapter"])
        request_tool_names = [tool["function"]["name"] for tool in client.chat.completions.last_request["tools"]]
        self.assertEqual(request_tool_names, [workflow_tools.WORKFLOW_SUBMISSION_TOOL_NAME])

    def test_call_function_tools_supports_legacy_function_call_stream_shape(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_legacy",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "function_call": {
                                "name": "submit_tool",
                                "arguments": "{\"value\":",
                            },
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_legacy",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "function_call": {
                                "arguments": " \"ok\"}",
                            },
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_legacy",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "function_call"}],
            },
        ]
        client = _FakeClient(stream_chunks)

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            tool_choice="auto",
            retries=1,
        )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertIn("tool_calls", result.output_types)

    def test_extraction_error_message_reports_observed_tool_calls(self) -> None:
        message = llm_runtime.build_extraction_error_message(
            target_label="函数工具调用",
            response_id="resp_test",
            status="completed",
            output_items=1,
            output_types=["chat.completion", "tool_calls"],
            raw_json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "write",
                                        "arguments": "{\"filePath\":\"F:/novelist/out.txt\",\"content\":\"hello\"}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

        self.assertIn("观测到的工具调用", message)
        self.assertIn("write", message)

    def test_call_function_tools_falls_back_to_legacy_compatible_tool_choice_shape(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_fallback",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_fallback",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        fallback = _FallbackChatCompletions(stream_chunks)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            _codex_openai_compatible_options={"transport": "stream"},
            chat=SimpleNamespace(completions=fallback),
        )

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            tool_choice={"type": "function", "name": "submit_tool"},
            retries=1,
        )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(len(fallback.requests), 2)
        self.assertEqual(
            fallback.requests[0]["tool_choice"],
            {"type": "function", "function": {"name": "submit_tool"}},
        )
        self.assertEqual(
            fallback.requests[1]["tool_choice"],
            {"type": "function", "name": "submit_tool"},
        )

    def test_large_compatible_connection_error_aborts_retries_immediately(self) -> None:
        error = openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.com/v1/chat/completions")
        )
        failing = _FailingChatCompletions(error)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            chat=SimpleNamespace(completions=failing),
        )

        with self.assertRaises(llm_runtime.ApiRequestError) as context:
            llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="test-model",
                instructions="system instruction",
                user_input="x" * 130000,
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                retries=10,
            )

        self.assertEqual(failing.call_count, 2)
        self.assertIn("openai_compatible", str(context.exception))
        self.assertIn("大载荷", str(context.exception))
        self.assertIn("已尝试传输=stream -> nonstream", str(context.exception))

    def test_compatible_defaults_to_stream_chat_completion(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_default_stream",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_default_stream",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        ]
        completions = _FakeChatCompletions(stream_chunks)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            chat=SimpleNamespace(completions=completions),
        )

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="user input",
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            retries=1,
        )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertTrue(completions.last_request["stream"])
        self.assertEqual(completions.last_request["stream_options"], {"include_usage": True})

    def test_large_compatible_connection_error_falls_back_to_nonstream_chat_completion(self) -> None:
        response_payload = {
            "id": "chatcmpl_nonstream",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "submit_tool",
                                    "arguments": "{\"value\": \"ok\"}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }
        fallback = _StreamToNonstreamFallbackChatCompletions(response_payload)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            _codex_openai_compatible_options={"transport": "stream"},
            chat=SimpleNamespace(completions=fallback),
        )

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="x" * 130000,
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            retries=1,
        )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(len(fallback.requests), 2)
        self.assertTrue(fallback.requests[0]["stream"])
        self.assertFalse(fallback.requests[1]["stream"])
        self.assertNotIn("stream_options", fallback.requests[1])

    def test_large_compatible_connection_error_falls_back_from_nonstream_to_stream_chat_completion(self) -> None:
        stream_chunks = [
            {
                "id": "chatcmpl_stream",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool",
                                        "arguments": "{\"value\": \"ok\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl_stream",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        fallback = _NonstreamToStreamFallbackChatCompletions(stream_chunks)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            _codex_openai_compatible_options={"transport": "nonstream"},
            chat=SimpleNamespace(completions=fallback),
        )

        result = llm_runtime.call_function_tools(
            client,  # type: ignore[arg-type]
            model="test-model",
            instructions="system instruction",
            user_input="x" * 130000,
            tool_specs=[
                llm_runtime.FunctionToolSpec(
                    model=_CompatToolPayload,
                    name="submit_tool",
                    description="test tool",
                )
            ],
            retries=1,
        )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(len(fallback.requests), 2)
        self.assertFalse(fallback.requests[0]["stream"])
        self.assertTrue(fallback.requests[1]["stream"])
        self.assertEqual(fallback.requests[1]["stream_options"], {"include_usage": True})

    def test_openai_compatible_direct_http_merges_extra_body_into_top_level_body(self) -> None:
        response_payload = {
            "id": "chatcmpl_http",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "submit_tool",
                                    "arguments": "{\"value\": \"ok\"}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
        response = httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            json=response_payload,
        )
        fake_http = _FakeDirectHttpClient(response)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            _codex_base_url="https://integrate.api.nvidia.com/v1",
            _codex_api_key="test-key",
            _codex_openai_compatible_options={
                "transport": "nonstream",
                "extra_body": {"prompt_cache_key": "{{prompt_cache_key}}"},
            },
        )

        with patch("novelist.core.responses_runtime._build_openai_compatible_http_client", return_value=fake_http):
            result = llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="deepseek-ai/deepseek-v4-pro",
                instructions="system instruction",
                user_input="user input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                prompt_cache_key="chapter-rewrite-cache-key",
                retries=1,
            )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(len(fake_http.post_calls), 1)
        sent = fake_http.post_calls[0]["json"]
        self.assertEqual(sent["prompt_cache_key"], "chapter-rewrite-cache-key")
        self.assertEqual(sent["reasoning_effort"], "high")
        self.assertFalse(sent["stream"])
        self.assertNotIn("extra_body", sent)
        self.assertNotIn("thinking", sent)
        self.assertEqual(
            fake_http.post_calls[0]["headers"]["Authorization"],
            "Bearer test-key",
        )

    def test_openai_compatible_direct_http_stream_sets_stream_body_param(self) -> None:
        request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
        response = httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            content=(
                b'data: {"id":"chatcmpl_http_stream","object":"chat.completion.chunk",'
                b'"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,'
                b'"id":"call_1","type":"function","function":{"name":"submit_tool",'
                b'"arguments":"{\\"value\\": \\"ok\\"}"}}]},"finish_reason":null}]}\n\n'
                b'data: {"id":"chatcmpl_http_stream","object":"chat.completion.chunk",'
                b'"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )
        fake_http = _FakeDirectStreamHttpClient(response)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            _codex_base_url="https://integrate.api.nvidia.com/v1",
            _codex_api_key="test-key",
            _codex_openai_compatible_options={"transport": "stream"},
        )

        with patch("novelist.core.responses_runtime._build_openai_compatible_http_client", return_value=fake_http):
            result = llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="deepseek-ai/deepseek-v4-pro",
                instructions="system instruction",
                user_input="user input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                retries=1,
            )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(len(fake_http.stream_calls), 1)
        sent = fake_http.stream_calls[0]["json"]
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["stream_options"], {"include_usage": True})
        self.assertEqual(fake_http.stream_calls[0]["headers"]["Accept"], "text/event-stream")

    def test_openai_compatible_direct_http_retries_without_env_proxy_on_tls_corruption(self) -> None:
        response_payload = {
            "id": "chatcmpl_http_retry",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "submit_tool",
                                    "arguments": "{\"value\": \"ok\"}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
        tls_error = httpx.ReadError(
            "[SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC] decryption failed or bad record mac (_ssl.c:2648)",
            request=request,
        )
        success_response = httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            json=response_payload,
        )
        success_client = _FakeDirectHttpClient(success_response)
        build_calls: list[bool | None] = []

        def fake_builder(base_url: str, *, trust_env_override: bool | None = None):
            build_calls.append(trust_env_override)
            if trust_env_override is None:
                return _ErroringDirectHttpClient(tls_error)
            return success_client

        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            _codex_base_url="https://integrate.api.nvidia.com/v1",
            _codex_api_key="test-key",
            _codex_openai_compatible_options={"transport": "nonstream"},
        )

        with patch("novelist.core.responses_runtime._build_openai_compatible_http_client", side_effect=fake_builder):
            result = llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="deepseek-ai/deepseek-v4-pro",
                instructions="system instruction",
                user_input="user input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                retries=1,
            )

        self.assertEqual(result.tool_name, "submit_tool")
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(build_calls, [None, False])
        self.assertEqual(len(success_client.post_calls), 1)

    def test_small_compatible_connection_error_retries_once_before_stopping(self) -> None:
        error = openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.com/v1/chat/completions")
        )
        failing = _FailingChatCompletions(error)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            chat=SimpleNamespace(completions=failing),
        )

        with (
            patch("novelist.core.responses_runtime.print_progress"),
            patch("novelist.core.responses_runtime.time.sleep"),
            self.assertRaises(llm_runtime.ApiRequestError) as context,
        ):
            llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="test-model",
                instructions="system instruction",
                user_input="short input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                retries=10,
            )

        self.assertEqual(failing.call_count, 2)
        self.assertIn("已停止继续重试", str(context.exception))

    def test_context_window_error_aborts_retries_immediately(self) -> None:
        error = RuntimeError("Your input exceeds the context window of this model.")

        self.assertTrue(
            llm_runtime.should_abort_transport_retries(
                error,
                protocol=llm_runtime.PROTOCOL_RESPONSES,
                request_chars=200000,
                attempt=1,
            )
        )
        self.assertIn(
            "上下文窗口超限",
            llm_runtime.format_transport_error_message(
                error,
                protocol=llm_runtime.PROTOCOL_RESPONSES,
                request_chars=200000,
                abort_retries=True,
            ),
        )

    def test_request_char_estimate_counts_responses_input_items(self) -> None:
        estimate = llm_runtime.estimate_request_text_chars(
            "instructions",
            [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "完整阶段上下文"}],
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_doc",
                    "output": "工具结果",
                },
            ],
        )

        self.assertGreaterEqual(estimate, len("instructions完整阶段上下文工具结果"))

    def test_agent_stage_continues_responses_with_local_transcript_tool_call_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.md"
            tool_arguments = json.dumps(
                {
                    "files": [
                        {
                            "file_key": "target",
                            "content": "工具写入正文。",
                        }
                    ]
                },
                ensure_ascii=False,
            )
            tool_result = llm_runtime.MultiFunctionToolResult(
                tool_name=document_ops.DOCUMENT_WRITE_TOOL_NAME,
                parsed=document_ops.DocumentWritePayload(
                    files=[
                        document_ops.DocumentWriteFile(
                            file_key="target",
                            content="工具写入正文。",
                        )
                    ]
                ),
                response_id="resp_tool",
                status="completed",
                output_types=["function_call"],
                preview="tool",
                raw_body_text="",
                raw_json={
                    "id": "resp_tool",
                    "status": "completed",
                    "output": [
                        {
                            "type": "reasoning",
                            "id": "rs_tool",
                            "summary": [],
                            "status": "completed",
                        },
                        {
                            "type": "function_call",
                            "id": "fc_tool",
                            "call_id": "call_doc",
                            "name": document_ops.DOCUMENT_WRITE_TOOL_NAME,
                            "arguments": tool_arguments,
                            "parsed_arguments": {
                                "files": [
                                    {
                                        "file_key": "target",
                                        "content": "工具写入正文。",
                                    }
                                ]
                            },
                            "status": "completed",
                        }
                    ],
                },
                call_id="call_doc",
                raw_arguments=tool_arguments,
            )
            submit_result = llm_runtime.MultiFunctionToolResult(
                tool_name=workflow_tools.WORKFLOW_SUBMISSION_TOOL_NAME,
                parsed=workflow_tools.WorkflowSubmissionPayload(
                    summary="完成。",
                    generated_files=["target"],
                ),
                response_id="resp_submit",
                status="completed",
                output_types=["function_call"],
                preview="submit",
                raw_body_text="",
                raw_json={},
                call_id="call_submit",
                raw_arguments="{}",
            )
            calls: list[dict[str, object]] = []

            def fake_call_function_tools(*args, **kwargs):
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    return tool_result
                return submit_result

            client = SimpleNamespace(_codex_protocol=llm_runtime.PROTOCOL_RESPONSES)
            with patch.object(agent_runtime.llm_runtime, "call_function_tools", side_effect=fake_call_function_tools):
                result = agent_runtime.run_agent_stage(
                    client,  # type: ignore[arg-type]
                    model="test-model",
                    instructions="instructions",
                    user_input="initial request",
                    allowed_files={"target": target},
                    retries=1,
                )

            self.assertEqual(result.response_id, "resp_submit")
            self.assertEqual(target.read_text(encoding="utf-8").strip(), "工具写入正文。")
            self.assertIsNotNone(result.transcript_state)
            assert result.transcript_state is not None
            self.assertIsNotNone(result.transcript_state.responses_transcript)
            final_transcript = result.transcript_state.responses_transcript
            assert final_transcript is not None
            self.assertTrue(
                any(
                    item.get("type") == "function_call"
                    and item.get("call_id") == "call_submit"
                    and item.get("name") == workflow_tools.WORKFLOW_SUBMISSION_TOOL_NAME
                    for item in final_transcript
                    if isinstance(item, dict)
                )
            )
            self.assertTrue(
                any(
                    item.get("type") == "function_call_output"
                    and item.get("call_id") == "call_submit"
                    and '"tool": "result"' in str(item.get("output") or "")
                    for item in final_transcript
                    if isinstance(item, dict)
                )
            )
            self.assertEqual(len(calls), 2)
            self.assertIsInstance(calls[0]["user_input"], list)
            self.assertIsNone(calls[0]["previous_response_id"])
            self.assertFalse(calls[0]["store"])
            self.assertIsInstance(calls[1]["user_input"], list)
            self.assertIsNone(calls[1]["previous_response_id"])
            self.assertFalse(calls[1]["store"])
            followup_input = calls[1]["user_input"]
            assert isinstance(followup_input, list)
            self.assertIn("initial request", json.dumps(followup_input, ensure_ascii=False))
            self.assertFalse(
                any("parsed_arguments" in item for item in followup_input if isinstance(item, dict))
            )
            self.assertFalse(any(item.get("type") == "reasoning" for item in followup_input if isinstance(item, dict)))
            self.assertTrue(
                any(
                    item.get("type") == "function_call"
                    and item.get("call_id") == "call_doc"
                    and item.get("name") == document_ops.DOCUMENT_WRITE_TOOL_NAME
                    and item.get("arguments") == tool_arguments
                    for item in followup_input
                    if isinstance(item, dict)
                )
            )
            self.assertTrue(
                any(
                    item.get("type") == "function_call_output"
                    and item.get("call_id") == "call_doc"
                    and '"ok": true' in str(item.get("output") or "")
                    and '"file_key": "target"' in str(item.get("output") or "")
                    for item in followup_input
                    if isinstance(item, dict)
                )
            )
            followup_submit = llm_runtime.MultiFunctionToolResult(
                tool_name=workflow_tools.WORKFLOW_SUBMISSION_TOOL_NAME,
                parsed=workflow_tools.WorkflowSubmissionPayload(summary="后续完成。"),
                response_id="resp_followup",
                status="completed",
                output_types=["function_call"],
                preview="followup",
                raw_body_text="",
                raw_json={},
                call_id="call_followup",
                raw_arguments="{}",
            )
            calls.clear()

            def fake_followup_call_function_tools(*args, **kwargs):
                calls.append(dict(kwargs))
                return followup_submit

            with patch.object(agent_runtime.llm_runtime, "call_function_tools", side_effect=fake_followup_call_function_tools):
                followup = agent_runtime.run_agent_stage(
                    client,  # type: ignore[arg-type]
                    model="test-model",
                    instructions="instructions",
                    user_input="next request",
                    allowed_files={"target": target},
                    retries=1,
                    transcript_state=result.transcript_state,
                )

            self.assertEqual(followup.response_id, "resp_followup")
            self.assertEqual(len(calls), 1)
            self.assertIsNone(calls[0]["previous_response_id"])
            followup_input = calls[0]["user_input"]
            assert isinstance(followup_input, list)
            serialized_followup = json.dumps(followup_input, ensure_ascii=False)
            self.assertIn("initial request", serialized_followup)
            self.assertIn("next request", serialized_followup)
            self.assertIn("call_submit", serialized_followup)

    def test_agent_stage_replays_reasoning_content_for_openai_compatible_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.md"
            tool_arguments = json.dumps(
                {
                    "files": [
                        {
                            "file_key": "target",
                            "content": "工具写入正文。",
                        }
                    ]
                },
                ensure_ascii=False,
            )
            tool_result = llm_runtime.MultiFunctionToolResult(
                tool_name=document_ops.DOCUMENT_WRITE_TOOL_NAME,
                parsed=document_ops.DocumentWritePayload(
                    files=[
                        document_ops.DocumentWriteFile(
                            file_key="target",
                            content="工具写入正文。",
                        )
                    ]
                ),
                response_id="resp_tool",
                status="completed",
                output_types=["chat.completion", "tool_calls"],
                preview="tool",
                raw_body_text="",
                raw_json={},
                call_id="call_doc",
                raw_arguments=tool_arguments,
                assistant_reasoning_content="alpha beta",
            )
            submit_result = llm_runtime.MultiFunctionToolResult(
                tool_name=workflow_tools.WORKFLOW_SUBMISSION_TOOL_NAME,
                parsed=workflow_tools.WorkflowSubmissionPayload(
                    summary="完成。",
                    generated_files=["target"],
                ),
                response_id="resp_submit",
                status="completed",
                output_types=["chat.completion", "tool_calls"],
                preview="submit",
                raw_body_text="",
                raw_json={},
                call_id="call_submit",
                raw_arguments="{}",
            )
            calls: list[dict[str, object]] = []

            def fake_call_function_tools(*args, **kwargs):
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    return tool_result
                return submit_result

            client = SimpleNamespace(_codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE)
            with patch.object(agent_runtime.llm_runtime, "call_function_tools", side_effect=fake_call_function_tools):
                result = agent_runtime.run_agent_stage(
                    client,  # type: ignore[arg-type]
                    model="deepseek-v4-pro",
                    instructions="instructions",
                    user_input="initial request",
                    allowed_files={"target": target},
                    retries=1,
                )

            self.assertEqual(result.response_id, "resp_submit")
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["previous_response_id"], None)
            self.assertEqual(calls[1]["previous_response_id"], "resp_tool")
            self.assertTrue(calls[0]["store"])
            self.assertTrue(calls[1]["store"])
            second_chat_messages = calls[1]["chat_messages"]
            assert isinstance(second_chat_messages, list)
            assistant_tool_messages = [
                message
                for message in second_chat_messages
                if isinstance(message, dict)
                and message.get("role") == "assistant"
                and isinstance(message.get("tool_calls"), list)
            ]
            self.assertTrue(assistant_tool_messages)
            self.assertEqual(assistant_tool_messages[0].get("reasoning_content"), "alpha beta")

    def test_compatible_database_internal_server_error_aborts_immediately(self) -> None:
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        response = httpx.Response(500, request=request)
        error = openai.InternalServerError(
            "Error code: 500 - {'error': {'message': 'Database error, please contact the administrator'}}",
            response=response,
            body={
                "error": {
                    "message": "Database error, please contact the administrator",
                    "type": "new_api_error",
                }
            },
        )
        failing = _FailingChatCompletions(error)
        client = SimpleNamespace(
            _codex_protocol=llm_runtime.PROTOCOL_OPENAI_COMPATIBLE,
            chat=SimpleNamespace(completions=failing),
        )

        with self.assertRaises(llm_runtime.ApiRequestError) as context:
            llm_runtime.call_function_tools(
                client,  # type: ignore[arg-type]
                model="test-model",
                instructions="system instruction",
                user_input="some input",
                tool_specs=[
                    llm_runtime.FunctionToolSpec(
                        model=_CompatToolPayload,
                        name="submit_tool",
                        description="test tool",
                    )
                ],
                retries=10,
            )

        self.assertEqual(failing.call_count, 1)
        self.assertIn("数据库/内部错误", str(context.exception))


if __name__ == "__main__":
    unittest.main()
