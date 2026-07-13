from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import autopatch_j.config as config_module
from autopatch_j.config import AppConfig, GlobalConfig
from autopatch_j.llm.client import LLMClient
from autopatch_j.llm.diagnostics import (
    MAX_RAW_LLM_ERROR_CHARS,
    format_raw_llm_exception,
)
from autopatch_j.llm.options import LLMCallPurpose, LLMReasoningMode, resolve_request_options


def _chunk(
    content: str | None = None,
    reasoning_content: str | None = None,
    tool_calls: list[object] | None = None,
) -> SimpleNamespace:
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        reasoning=None,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


def _non_stream_response(
    content: str,
    reasoning_content: str | None = None,
    tool_calls: list[object] | None = None,
) -> SimpleNamespace:
    message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        reasoning=None,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_chat_parses_dsml_tool_call_and_hides_markup() -> None:
    client = LLMClient(api_key="test-key", base_url="https://example.invalid/v1", model="test-model", stream_dialect="bailian-dsml")
    client.client = MagicMock()

    client.client.chat.completions.create.return_value = [
        _chunk("先检查 F3。\n<｜DS"),
        _chunk("ML｜function_calls>\n<｜DSML｜invoke name=\"get_finding_detail\">"),
        _chunk("<｜DSML｜parameter name=\"finding_id\" string=\"true\">F3</｜DSML｜parameter>"),
        _chunk("</｜DSML｜invoke>\n</｜DSML｜function_calls>"),
    ]

    streamed_tokens: list[str] = []
    response = client.chat(
        messages=[{"role": "user", "content": "检查代码"}],
        tools=[],
        on_content_delta=streamed_tokens.append,
    )

    assert response.tool_calls is not None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_finding_detail"
    assert response.tool_calls[0].arguments == {"finding_id": "F3"}
    assert response.content == "先检查 F3。"
    assert "".join(streamed_tokens) == "先检查 F3。\n"


def test_chat_parses_dsml_tool_call_with_preamble_and_spacing() -> None:
    client = LLMClient(api_key="test-key", base_url="https://example.invalid/v1", model="test-model", stream_dialect="bailian-dsml")
    client.client = MagicMock()

    client.client.chat.completions.create.return_value = [
        _chunk(
            "现在需要获取 UserService.java 的完整代码。使用 read_source_file。\n\n"
            "<｜DSML｜function_calls>\n"
            "<｜DSML｜invoke name=\"read_source_file\">\n"
            "<｜DSML｜parameter name=\"path\" string=\"true\">src/main/java/demo/UserService.java</｜DSML｜parameter>\n"
            "</｜DSML｜invoke>\n"
            "</｜DSML｜function_calls>"
        )
    ]

    streamed_tokens: list[str] = []
    response = client.chat(
        messages=[{"role": "user", "content": "检查代码"}],
        tools=[],
        on_content_delta=streamed_tokens.append,
    )

    assert response.tool_calls is not None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "read_source_file"
    assert response.tool_calls[0].arguments == {"path": "src/main/java/demo/UserService.java"}
    assert response.content == "现在需要获取 UserService.java 的完整代码。使用 read_source_file。"
    assert "".join(streamed_tokens) == "现在需要获取 UserService.java 的完整代码。使用 read_source_file。\n\n"


def test_react_purpose_inherits_reasoning_and_streams_deltas(monkeypatch) -> None:
    monkeypatch.setattr(GlobalConfig, "llm_extra_body", '{"thinking": {"type": "enabled"}}')
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        reasoning_effort="high",
    )
    client.client = MagicMock()
    client.client.chat.completions.create.return_value = [
        _chunk(reasoning_content="先判断"),
        _chunk(content="可见回答"),
    ]

    content_deltas: list[str] = []
    reasoning_deltas: list[str] = []
    response = client.chat(
        messages=[{"role": "user", "content": "检查代码"}],
        tools=[],
        purpose=LLMCallPurpose.REACT,
        on_content_delta=content_deltas.append,
        on_reasoning_delta=reasoning_deltas.append,
    )

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["stream"] is True
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["stream_options"] == {"include_usage": True}
    assert response.content == "可见回答"
    assert response.reasoning_content == "先判断"
    assert content_deltas == ["可见回答"]
    assert reasoning_deltas == ["先判断"]


def test_llm_client_records_call_diagnostics_for_classifier() -> None:
    client = LLMClient(api_key="test-key", base_url="https://example.invalid/v1", model="test-model")
    client.client = MagicMock()
    client.client.chat.completions.create.return_value = _non_stream_response("code_audit")

    response = client.chat(
        messages=[{"role": "user", "content": "检查代码"}],
        tools=None,
        purpose=LLMCallPurpose.CLASSIFIER,
    )

    assert response.content == "code_audit"
    diagnostic = client.diagnostics[-1]
    assert diagnostic.purpose is LLMCallPurpose.CLASSIFIER
    assert diagnostic.stream is False
    assert diagnostic.reasoning is LLMReasoningMode.DISABLED
    assert diagnostic.max_tokens == 128
    assert diagnostic.status == "ok"


def test_llm_diagnostic_preserves_raw_provider_failure() -> None:
    class ProviderError(RuntimeError):
        status_code = 429
        code = "rate_limit"
        body = {"detail": "RAW provider body"}
        response = SimpleNamespace(text="RAW response body", content=b"ignored")

    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    client.client = MagicMock()
    client.client.chat.completions.create.side_effect = ProviderError(
        "provider echoed RAW failure"
    )

    with pytest.raises(ProviderError):
        client.chat(
            messages=[{"role": "user", "content": "RAW turn"}],
            purpose=LLMCallPurpose.MEMORY_EXTRACTION,
        )

    diagnostic = client.diagnostics[-1]
    assert diagnostic.status == "error"
    assert "ProviderError: provider echoed RAW failure" in diagnostic.error
    assert "status_code: 429" in diagnostic.error
    assert "code: rate_limit" in diagnostic.error
    assert 'body: {"detail": "RAW provider body"}' in diagnostic.error
    assert "response.text: RAW response body" in diagnostic.error


def test_llm_diagnostic_records_non_stream_parser_failure() -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    client.client = MagicMock()
    client.client.chat.completions.create.return_value = _non_stream_response("ignored")
    client.response_parser.parse_non_stream_response = MagicMock(
        side_effect=RuntimeError("RAW parser failure")
    )

    with pytest.raises(RuntimeError, match="RAW parser failure"):
        client.chat(
            messages=[{"role": "user", "content": "classify"}],
            purpose=LLMCallPurpose.CLASSIFIER,
        )

    diagnostic = client.diagnostics[-1]
    assert diagnostic.purpose is LLMCallPurpose.CLASSIFIER
    assert diagnostic.status == "error"
    assert diagnostic.error == "RuntimeError: RAW parser failure"


def test_llm_diagnostic_records_stream_iteration_failure() -> None:
    class FailingStream:
        def __iter__(self):
            raise RuntimeError("RAW stream failure")

    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    client.client = MagicMock()
    client.client.chat.completions.create.return_value = FailingStream()

    with pytest.raises(RuntimeError, match="RAW stream failure"):
        client.chat(messages=[{"role": "user", "content": "answer"}])

    diagnostic = client.diagnostics[-1]
    assert diagnostic.purpose is LLMCallPurpose.REACT
    assert diagnostic.status == "error"
    assert diagnostic.error == "RuntimeError: RAW stream failure"


def test_raw_llm_exception_formatter_is_bounded_and_marks_truncation() -> None:
    formatted = format_raw_llm_exception(RuntimeError("x" * 30_000))

    assert len(formatted) == MAX_RAW_LLM_ERROR_CHARS
    assert formatted.endswith(
        f"...[truncated to {MAX_RAW_LLM_ERROR_CHARS} characters]"
    )


def test_raw_llm_exception_formatter_handles_provider_attribute_failures() -> None:
    class BrokenProviderError(RuntimeError):
        @property
        def status_code(self):
            raise RuntimeError("status getter failed")

        @property
        def code(self):
            raise RuntimeError("code getter failed")

        @property
        def body(self):
            raise RuntimeError("body getter failed")

        @property
        def response(self):
            raise RuntimeError("response getter failed")

        @property
        def request(self):
            raise AssertionError("request must not be inspected")

        @property
        def headers(self):
            raise AssertionError("headers must not be inspected")

        @property
        def messages(self):
            raise AssertionError("messages must not be inspected")

        @property
        def prompt(self):
            raise AssertionError("prompt must not be inspected")

        @property
        def api_key(self):
            raise AssertionError("api_key must not be inspected")

    formatted = format_raw_llm_exception(BrokenProviderError("original failure"))

    assert formatted == "BrokenProviderError: original failure"

    class Unprintable:
        def __str__(self) -> str:
            raise RuntimeError("serialization failed")

    class UnprintableBodyError(RuntimeError):
        body = Unprintable()

    formatted = format_raw_llm_exception(UnprintableBodyError("original failure"))
    assert formatted.endswith("body: <unprintable Unprintable>")


def test_raw_llm_exception_formatter_uses_content_fallback_and_deduplicates_body() -> None:
    class DuplicateBodyError(RuntimeError):
        body = {"detail": "same body"}
        response = SimpleNamespace(
            text='{"detail": "same body"}',
            content=b"ignored",
        )

    duplicate = format_raw_llm_exception(DuplicateBodyError("failed"))
    assert duplicate.count("same body") == 1

    class ContentOnlyError(RuntimeError):
        response = SimpleNamespace(text="", content="响应正文".encode())

    content_fallback = format_raw_llm_exception(ContentOnlyError("failed"))
    assert "response.content: 响应正文" in content_fallback

    class FailingEquality:
        def __eq__(self, other):
            raise RuntimeError("equality failed")

    class NonScalarResponseError(RuntimeError):
        response = SimpleNamespace(text=FailingEquality(), content=b"ignored")

    non_scalar = format_raw_llm_exception(NonScalarResponseError("failed"))
    assert "response.text:" in non_scalar


def test_classifier_purpose_uses_non_stream_response_without_reasoning_effort() -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        reasoning_effort="high",
    )
    client.client = MagicMock()
    client.client.chat.completions.create.return_value = _non_stream_response(
        content="code_audit",
        reasoning_content="hidden reasoning",
    )

    response = client.chat(
        messages=[{"role": "user", "content": "检查代码"}],
        tools=None,
        purpose=LLMCallPurpose.CLASSIFIER,
    )

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["stream"] is False
    assert kwargs["max_tokens"] == 128
    assert kwargs["temperature"] == 0
    assert "reasoning_effort" not in kwargs
    assert "stream_options" not in kwargs
    assert kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    assert response.content == "code_audit"
    assert response.reasoning_content == "hidden reasoning"


@pytest.mark.parametrize(
    ("purpose", "max_tokens"),
    [
        (LLMCallPurpose.MEMORY_EXTRACTION, 1800),
        (LLMCallPurpose.MEMORY_CONSOLIDATION, 2200),
    ],
)
def test_memory_purposes_use_bounded_non_stream_response(
    purpose: LLMCallPurpose,
    max_tokens: int,
) -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        reasoning_effort="high",
    )
    client.client = MagicMock()
    client.client.chat.completions.create.return_value = _non_stream_response(
        content='{"episode_summaries": []}',
    )

    response = client.chat(
        messages=[{"role": "user", "content": "summarize memory"}],
        tools=None,
        purpose=purpose,
    )

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["stream"] is False
    assert kwargs["max_tokens"] == max_tokens
    assert kwargs["temperature"] == 0
    assert kwargs["timeout"] == 60
    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    assert response.content == '{"episode_summaries": []}'


def test_classifier_purpose_retries_without_disabled_thinking_when_unsupported() -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        reasoning_effort="high",
    )
    client.client = MagicMock()
    client.client.chat.completions.create.side_effect = [
        RuntimeError("unsupported parameter: thinking"),
        _non_stream_response(content="code_audit"),
    ]

    response = client.chat(
        messages=[{"role": "user", "content": "检查代码"}],
        tools=None,
        purpose=LLMCallPurpose.CLASSIFIER,
    )

    first_kwargs = client.client.chat.completions.create.call_args_list[0].kwargs
    second_kwargs = client.client.chat.completions.create.call_args_list[1].kwargs
    assert first_kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    assert second_kwargs["extra_body"] is None
    assert second_kwargs["stream"] is False
    assert "reasoning_effort" not in second_kwargs
    assert response.content == "code_audit"


def test_react_request_rejects_invalid_global_extra_body(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_EXTRA_BODY", "{bad json")
    monkeypatch.setattr(config_module, "GlobalConfig", AppConfig.from_env())
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
    )

    with pytest.raises(ValueError, match="AUTOPATCH_LLM_EXTRA_BODY"):
        client.request_builder.build_request_kwargs(
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            options=resolve_request_options(LLMCallPurpose.REACT),
        )
