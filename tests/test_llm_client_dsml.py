from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from autopatch_j.agent.llm_client import LLMClient


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
        on_token=streamed_tokens.append,
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
        on_token=streamed_tokens.append,
    )

    assert response.tool_calls is not None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "read_source_code"
    assert response.tool_calls[0].arguments == {"path": "src/main/java/demo/UserService.java"}
    assert response.content == "现在需要获取 UserService.java 的完整代码。使用 read_source_code。"
    assert "".join(streamed_tokens) == "现在需要获取 UserService.java 的完整代码。使用 read_source_code。\n\n"


def test_chat_supports_non_stream_response_without_reasoning_effort() -> None:
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
        stream=False,
        reasoning_effort=None,
        max_tokens=32,
        temperature=0,
    )

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["stream"] is False
    assert kwargs["max_tokens"] == 32
    assert kwargs["temperature"] == 0
    assert "reasoning_effort" not in kwargs
    assert "stream_options" not in kwargs
    assert response.content == "code_audit"
    assert response.reasoning_content == "hidden reasoning"
