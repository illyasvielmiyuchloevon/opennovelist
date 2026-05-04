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


class _FakeClient:
    def __init__(self, stream_chunks: list[dict]) -> None:
        self._codex_protocol = llm_runtime.PROTOCOL_OPENAI_COMPATIBLE
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(stream_chunks))


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

    def test_call_function_tool_uses_auto_tool_choice_for_compatible(self) -> None:
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
        self.assertEqual(client.chat.completions.last_request["tool_choice"], "auto")

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

        self.assertEqual(failing.call_count, 1)
        self.assertIn("openai_compatible", str(context.exception))
        self.assertIn("大载荷", str(context.exception))

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
                    and '"tool": "submit_workflow_result"' in str(item.get("output") or "")
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
