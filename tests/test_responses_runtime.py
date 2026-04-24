from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
from pydantic import BaseModel

from novelist.core import responses_runtime as llm_runtime


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
    def __init__(self, events: list[SimpleNamespace], final_response: dict | Exception | None = None) -> None:
        self._events = events
        self._final_response = final_response
        self.last_request: dict | None = None

    def stream(self, **kwargs):
        self.last_request = kwargs
        return _FakeResponsesStream(self._events, self._final_response)


class _FakeResponsesClient:
    def __init__(self, events: list[SimpleNamespace], final_response: dict | Exception | None = None) -> None:
        self._codex_protocol = llm_runtime.PROTOCOL_RESPONSES
        self.responses = _FakeResponses(events, final_response)


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
        self.assertIsInstance(captured["timeout"], httpx.Timeout)
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        self.assertEqual(timeout.connect, llm_runtime.DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(timeout.read, llm_runtime.DEFAULT_OPENAI_READ_TIMEOUT_SECONDS)
        self.assertEqual(timeout.write, llm_runtime.DEFAULT_OPENAI_WRITE_TIMEOUT_SECONDS)
        self.assertEqual(timeout.pool, llm_runtime.DEFAULT_OPENAI_POOL_TIMEOUT_SECONDS)

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
        self.assertEqual(client.chat.completions.last_request["messages"][0]["role"], "system")
        self.assertTrue(client.chat.completions.last_request["stream"])
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

        with self.assertRaises(llm_runtime.ApiRequestError) as context:
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
