from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from autopatch_j.config import GlobalConfig
from autopatch_j.agent.llm_client import LLMCallPurpose, LLMClient


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
            "现在需要获取 UserService.java 的完整代码。使用 read_source_code。\n\n"
            "<｜DSML｜function_calls>\n"
            "<｜DSML｜invoke name=\"read_source_code\">\n"
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
    assert response.tool_calls[0].name == "read_source_code"
    assert response.tool_calls[0].arguments == {"path": "src/main/java/demo/UserService.java"}
    assert response.content == "现在需要获取 UserService.java 的完整代码。使用 read_source_code。"
    assert "".join(streamed_tokens) == "现在需要获取 UserService.java 的完整代码。使用 read_source_code。\n\n"


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
