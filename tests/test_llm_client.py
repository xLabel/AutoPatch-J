from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from openai import OpenAI as ActualOpenAI

from autopatch_j.llm import transport as llm_transport
from autopatch_j.llm.client import LLMClient
from autopatch_j.llm.context_window import ContextCapacityError, ModelContextProfile
from autopatch_j.llm.errors import (
    LLMContentFilterError,
    LLMProtocolError,
    LLMTransientError,
    LLMTruncationError,
)
from autopatch_j.llm.options import LLMCallPurpose, LLMReasoningMode
from autopatch_j.llm.parser import LLMResponseParser
from autopatch_j.llm.providers import ProviderKind, resolve_provider_profile
from autopatch_j.llm.transport import TransportCompletion


@pytest.fixture(autouse=True)
def _allow_actual_sdk_with_mock_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_transport.openai, "OpenAI", lambda **_kwargs: MagicMock())


def _non_stream_payload(
    content: str = "ok",
    *,
    finish_reason: str = "stop",
    message_extra: dict[str, object] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    message.update(message_extra or {})
    return {
        "id": "resp_test",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
    }


def _stream_response() -> httpx.Response:
    events = [
        {
            "id": "resp_stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"reasoning_content": "think"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "resp_stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "answer"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "resp_stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        },
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    body += "data: [DONE]\n\n"
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=body.encode(),
    )


def _sdk_client(
    model: str,
    handler,
    *,
    context_profile: ModelContextProfile | None = None,
) -> LLMClient:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model=model,
        context_profile=context_profile,
    )
    client.client = ActualOpenAI(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        ),
    )
    return client


@pytest.mark.parametrize(
    ("model", "kind"),
    [
        ("deepseek-v4-flash", ProviderKind.DEEPSEEK_V4),
        ("deepseek-v4-pro", ProviderKind.DEEPSEEK_V4),
        ("deepseek-v4", ProviderKind.OPENAI_CHAT),
        ("DEEPSEEK-V4-FLASH", ProviderKind.OPENAI_CHAT),
        ("my-deepseek-v4-flash-proxy", ProviderKind.OPENAI_CHAT),
        ("gpt-5", ProviderKind.OPENAI_CHAT),
    ],
)
def test_provider_selection_uses_exact_model_ids(model: str, kind: ProviderKind) -> None:
    assert resolve_provider_profile(model).kind is kind


@pytest.mark.parametrize(
    ("purpose", "stream", "thinking", "max_tokens", "temperature"),
    [
        (LLMCallPurpose.CLASSIFIER, False, "disabled", 128, 0),
        (LLMCallPurpose.REACT, True, "enabled", 128_000, None),
        (LLMCallPurpose.MEMORY_EXTRACTION, False, "enabled", 128_000, None),
        (LLMCallPurpose.CONTEXT_COMPACTION, False, "enabled", 128_000, None),
    ],
)
def test_deepseek_request_matrix_uses_actual_sdk_wire(
    purpose: LLMCallPurpose,
    stream: bool,
    thinking: str,
    max_tokens: int,
    temperature: int | None,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _stream_response() if stream else httpx.Response(200, json=_non_stream_payload())

    client = _sdk_client("deepseek-v4-flash", handler)
    response = client.chat(
        messages=[{"role": "user", "content": "hello"}],
        purpose=purpose,
    )

    body = requests[0]
    assert body["stream"] is stream
    assert body["thinking"] == {"type": thinking}
    assert body["max_tokens"] == max_tokens
    assert (body.get("temperature")) == temperature
    if thinking == "enabled":
        assert body["reasoning_effort"] == "max"
    else:
        assert "reasoning_effort" not in body
    if purpose is LLMCallPurpose.MEMORY_EXTRACTION:
        assert body["response_format"] == {"type": "json_object"}
    assert "enable_thinking" not in body
    assert response.finish_reason == "stop"
    diagnostic = client.diagnostics[-1]
    assert diagnostic.sdk_max_retries == 2
    assert diagnostic.sdk_retries_taken == 0


def test_openai_baseline_uses_standard_fields_and_strips_reasoning_content() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_non_stream_payload("general_chat"))

    client = _sdk_client("gpt-5", handler)
    client.chat(
        messages=[
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "private",
            },
            {"role": "user", "content": "hello"},
        ],
        purpose=LLMCallPurpose.CLASSIFIER,
    )

    body = requests[0]
    assert body["max_completion_tokens"] == 128
    assert "max_tokens" not in body
    assert "thinking" not in body
    assert "reasoning_effort" not in body
    assert "reasoning_content" not in body["messages"][0]


def test_sdk_retries_same_logical_operation_and_records_retries_taken() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"retry-after-ms": "0"}, json={"error": {"message": "busy", "type": "rate_limit_error"}})
        return httpx.Response(200, json=_non_stream_payload("general_chat"))

    client = _sdk_client("gpt-5", handler)
    response = client.chat(
        messages=[{"role": "user", "content": "hello"}],
        purpose=LLMCallPurpose.CLASSIFIER,
    )

    assert response.content == "general_chat"
    assert attempts == 3
    assert client.diagnostics[-1].sdk_retries_taken == 2


def test_sdk_does_not_retry_http_400() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": {"message": "bad request", "type": "invalid_request_error"}})

    client = _sdk_client("gpt-5", handler)
    with pytest.raises(openai.BadRequestError):
        client.chat(
            messages=[{"role": "user", "content": "hello"}],
            purpose=LLMCallPurpose.CLASSIFIER,
        )
    assert attempts == 1
    assert client.diagnostics[-1].sdk_retries_taken is None


class _BrokenSseStream(httpx.SyncByteStream):
    def __iter__(self):
        event = {
            "id": "resp_broken",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {"index": 0, "delta": {"content": "partial"}, "finish_reason": None}
            ],
        }
        yield f"data: {json.dumps(event)}\n\n".encode()
        raise httpx.ReadError("stream interrupted")


def test_stream_failure_after_sse_start_is_not_replayed() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_BrokenSseStream(),
        )

    client = _sdk_client("gpt-5", handler)
    with pytest.raises(Exception, match="stream interrupted"):
        client.chat(messages=[{"role": "user", "content": "hello"}])
    assert attempts == 1


def test_stream_parser_preserves_response_metadata_and_present_empty_reasoning() -> None:
    parser = LLMResponseParser()
    response = parser.parse_stream_response(
        [
            {
                "id": "resp_1",
                "choices": [
                    {
                        "delta": {"reasoning_content": "", "content": ""},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "resp_1",
                "choices": [{"delta": {}, "finish_reason": "stop"}],
            },
        ]
    )
    assert response.response_id == "resp_1"
    assert response.finish_reason == "stop"
    assert response.content == ""
    assert response.reasoning_content == ""
    assert response.reasoning_content_present is True


@pytest.mark.parametrize("raw_arguments", ["", "{bad", "[]"])
def test_parser_rejects_malformed_tool_arguments(raw_arguments: str) -> None:
    parser = LLMResponseParser()
    response = SimpleNamespace(
        id="resp_1",
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(
                                name="read_source_file",
                                arguments=raw_arguments,
                            ),
                        )
                    ],
                ),
            )
        ],
    )
    with pytest.raises(LLMProtocolError):
        parser.parse_non_stream_response(response)


def test_deepseek_thinking_tool_call_requires_reasoning_field() -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="deepseek-v4-pro",
    )
    client.transport.create_completion = MagicMock(
        return_value=TransportCompletion(
            response=[
                {
                    "id": "resp_tool",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "read_source_file",
                                            "arguments": '{"path":"A.java"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            ],
            sdk_retries_taken=0,
        )
    )
    with pytest.raises(LLMProtocolError, match="reasoning_content"):
        client.chat(messages=[{"role": "user", "content": "read"}])


def test_deepseek_tool_call_accepts_present_empty_reasoning_and_preserves_it() -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="deepseek-v4-pro",
    )
    client.transport.create_completion = MagicMock(
        return_value=TransportCompletion(
            response=[
                {
                    "id": "resp_tool",
                    "choices": [
                        {
                            "delta": {
                                "reasoning_content": "",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "read_source_file",
                                            "arguments": '{"path":"A.java"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            ],
            sdk_retries_taken=0,
        )
    )

    response = client.chat(messages=[{"role": "user", "content": "read"}])

    assert response.reasoning_content_present is True
    assert response.reasoning_content == ""
    assert response.tool_calls is not None


def test_deepseek_rejects_tool_call_history_without_reasoning_before_transport() -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="deepseek-v4-flash",
    )
    client.transport.create_completion = MagicMock()

    with pytest.raises(LLMProtocolError, match="history"):
        client.chat(
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_source_file",
                                "arguments": '{"path":"A.java"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "..."},
            ]
        )

    client.transport.create_completion.assert_not_called()


@pytest.mark.parametrize(
    ("finish_reason", "error_type"),
    [
        ("insufficient_system_resource", LLMTransientError),
        ("length", LLMTruncationError),
        ("content_filter", LLMContentFilterError),
    ],
)
def test_finish_reason_maps_to_typed_error(finish_reason: str, error_type: type[Exception]) -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="gpt-5",
    )
    client.transport.create_completion = MagicMock(
        return_value=TransportCompletion(
            response=SimpleNamespace(
                id="resp_failure",
                choices=[
                    SimpleNamespace(
                        finish_reason=finish_reason,
                        message=SimpleNamespace(content="partial", tool_calls=None),
                    )
                ],
            ),
            sdk_retries_taken=0,
        )
    )
    with pytest.raises(error_type):
        client.chat(
            messages=[{"role": "user", "content": "hello"}],
            purpose=LLMCallPurpose.CLASSIFIER,
        )
    diagnostic = client.diagnostics[-1]
    assert diagnostic.response_id == "resp_failure"
    assert diagnostic.finish_reason == finish_reason


@pytest.mark.parametrize("purpose", list(LLMCallPurpose))
def test_all_purposes_preflight_before_transport(purpose: LLMCallPurpose) -> None:
    profile = ModelContextProfile(
        model="test-model",
        context_window=200,
        provider_max_output_tokens=50,
        input_capacity=100,
        max_output_tokens=50,
        provider_safety_tokens=50,
    )
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        context_profile=profile,
    )
    client.transport.create_completion = MagicMock()
    with pytest.raises(ContextCapacityError):
        client.chat(
            messages=[{"role": "user", "content": "x" * 2_000}],
            purpose=purpose,
        )
    client.transport.create_completion.assert_not_called()


def test_classifier_diagnostic_uses_disabled_reasoning_and_sdk_retry_budget() -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="gpt-5",
    )
    client.transport.create_completion = MagicMock(
        return_value=TransportCompletion(
            response=SimpleNamespace(
                id="resp_classifier",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="general_chat", tool_calls=None),
                    )
                ],
            ),
            sdk_retries_taken=1,
        )
    )
    client.chat(
        messages=[{"role": "user", "content": "hello"}],
        purpose=LLMCallPurpose.CLASSIFIER,
    )
    diagnostic = client.diagnostics[-1]
    assert diagnostic.reasoning is LLMReasoningMode.DISABLED
    assert diagnostic.max_tokens == 128
    assert diagnostic.sdk_max_retries == 2
    assert diagnostic.sdk_retries_taken == 1
