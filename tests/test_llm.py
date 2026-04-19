from __future__ import annotations

import io
import json
import os
import unittest
from urllib import error
from unittest.mock import patch

from autopatch_j.llm import (
    OpenAICompatibleChatClient,
    build_default_llm_client,
    parse_chat_completion_response,
    parse_chat_completion_stream,
)


class FakeHTTPResponse:
    def __init__(self, body: str = "", lines: list[str] | None = None) -> None:
        self.body = body
        self.lines = lines or []

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode("utf-8")

    def __iter__(self) -> object:
        return iter([line.encode("utf-8") for line in self.lines])


class OpenAICompatibleChatClientTests(unittest.TestCase):
    def test_build_payload_adds_stream_usage_when_streaming(self) -> None:
        client = OpenAICompatibleChatClient(
            api_key="key",
            model="deepseek-chat",
            base_url="https://llm.example/v1",
        )

        payload = client.build_payload(
            messages=[{"role": "user", "content": "scan"}],
            tools=[{"type": "function", "function": {"name": "scan"}}],
            stream=True,
        )

        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertEqual(payload["tool_choice"], "auto")

    def test_parse_non_stream_response_extracts_content_tool_calls_and_usage(self) -> None:
        result = parse_chat_completion_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": "hello",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "scan",
                                        "arguments": '{"scope":["src"]}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"total_tokens": 42},
            }
        )

        self.assertEqual(result.content, "hello")
        self.assertEqual(result.tool_calls[0].name, "scan")
        self.assertEqual(result.tool_calls[0].arguments["scope"], ["src"])
        self.assertEqual(result.usage, {"total_tokens": 42})

    def test_parse_stream_response_aggregates_content_tool_calls_and_usage(self) -> None:
        deltas: list[str] = []
        response = FakeHTTPResponse(
            lines=[
                'data: {"choices":[{"delta":{"content":"he"}}]}\n',
                'data: {"choices":[{"delta":{"content":"llo"}}]}\n',
                (
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
                    '"function":{"name":"scan","arguments":"{\\"scope\\":"}}]}}]}\n'
                ),
                (
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                    '"function":{"arguments":"[\\"src\\"]}"}}]}}]}\n'
                ),
                'data: {"choices":[],"usage":{"total_tokens":7}}\n',
                "data: [DONE]\n",
            ]
        )

        result = parse_chat_completion_stream(response, on_delta=deltas.append)

        self.assertEqual(result.content, "hello")
        self.assertEqual(deltas, ["he", "llo"])
        self.assertEqual(result.tool_calls[0].name, "scan")
        self.assertEqual(result.tool_calls[0].arguments, {"scope": ["src"]})
        self.assertEqual(result.usage, {"total_tokens": 7})

    def test_client_retries_without_stream_options_when_provider_rejects_it(self) -> None:
        client = OpenAICompatibleChatClient(
            api_key="key",
            model="deepseek-chat",
            base_url="https://llm.example/v1",
            max_retries=0,
        )
        http_error = error.HTTPError(
            url="https://llm.example/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b"unknown field: stream_options"),
        )
        response = FakeHTTPResponse(
            lines=[
                'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                "data: [DONE]\n",
            ]
        )

        with patch("autopatch_j.llm.request.urlopen", side_effect=[http_error, response]) as urlopen:
            result = client.complete(messages=[{"role": "user", "content": "hi"}], stream=True)

        self.assertEqual(result.content, "ok")
        first_payload = json.loads(urlopen.call_args_list[0].args[0].data.decode("utf-8"))
        second_payload = json.loads(urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("stream_options", first_payload)
        self.assertNotIn("stream_options", second_payload)

    def test_build_default_llm_client_accepts_generic_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTOPATCH_LLM_API_KEY": "key",
                "AUTOPATCH_LLM_MODEL": "deepseek-chat",
                "AUTOPATCH_LLM_BASE_URL": "https://llm.example/v1",
            },
            clear=True,
        ):
            client = build_default_llm_client()

        self.assertIsNotNone(client)
        assert client is not None
        self.assertEqual(client.label, "openai-compatible:deepseek-chat")
        self.assertEqual(client.base_url, "https://llm.example/v1")


if __name__ == "__main__":
    unittest.main()
