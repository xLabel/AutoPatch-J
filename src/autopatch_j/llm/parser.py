from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .errors import LLMProtocolError
from .models import LLMResponse, ToolCall


TokenCallback = Callable[[str], None]


class LLMResponseParser:
    """Strict parser for standard OpenAI Chat Completions responses."""

    def parse_stream_response(
        self,
        response: Any,
        on_content_delta: TokenCallback | None = None,
        on_reasoning_delta: TokenCallback | None = None,
    ) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_present = False
        tool_calls_map: dict[int, dict[str, str]] = {}
        response_id: str | None = None
        finish_reason: str | None = None
        saw_choice = False

        for chunk in response:
            chunk_id = _bounded_protocol_text(_read_field(chunk, "id"))
            if chunk_id is not None:
                response_id = chunk_id
            choices = _read_field(chunk, "choices") or []
            if not choices:
                continue
            saw_choice = True
            choice = choices[0]
            choice_finish = _bounded_protocol_text(_read_field(choice, "finish_reason"))
            if choice_finish is not None:
                finish_reason = choice_finish
            delta = _read_field(choice, "delta")
            if delta is None:
                raise LLMProtocolError("Stream choice is missing delta.")

            if _field_present(delta, "reasoning_content"):
                reasoning_present = True
                reasoning = _read_field(delta, "reasoning_content")
                if reasoning is not None:
                    if not isinstance(reasoning, str):
                        raise LLMProtocolError("reasoning_content must be a string.")
                    reasoning_parts.append(reasoning)
                    if on_reasoning_delta and reasoning:
                        on_reasoning_delta(reasoning)

            if _field_present(delta, "content"):
                content = _read_field(delta, "content")
                if content is not None:
                    if not isinstance(content, str):
                        raise LLMProtocolError("content must be a string.")
                    content_parts.append(content)
                    if on_content_delta and content:
                        on_content_delta(content)

            for tool_call in _read_field(delta, "tool_calls") or []:
                index = _read_field(tool_call, "index")
                if not isinstance(index, int) or index < 0:
                    raise LLMProtocolError("Stream tool call has an invalid index.")
                current = tool_calls_map.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                call_id = _read_field(tool_call, "id")
                if call_id is not None:
                    current["id"] = str(call_id)
                function = _read_field(tool_call, "function")
                if function is None:
                    continue
                name = _read_field(function, "name")
                if name is not None:
                    current["name"] += str(name)
                arguments = _read_field(function, "arguments")
                if arguments is not None:
                    current["arguments"] += str(arguments)

        if not saw_choice:
            raise LLMProtocolError("Stream response contains no choices.")
        tool_calls = self._parse_tool_calls(tool_calls_map)
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls or None,
            reasoning_content=("".join(reasoning_parts) if reasoning_present else None),
            reasoning_content_present=reasoning_present,
            response_id=response_id,
            finish_reason=finish_reason,
        )

    def parse_non_stream_response(
        self,
        response: Any,
        on_content_delta: TokenCallback | None = None,
    ) -> LLMResponse:
        response_id = _bounded_protocol_text(_read_field(response, "id"))
        choices = _read_field(response, "choices") or []
        if not choices:
            raise LLMProtocolError("Chat completion response contains no choices.")
        choice = choices[0]
        message = _read_field(choice, "message")
        if message is None:
            raise LLMProtocolError("Chat completion choice is missing message.")
        raw_content = _read_field(message, "content")
        if raw_content is None:
            content = ""
        elif isinstance(raw_content, str):
            content = raw_content
        else:
            raise LLMProtocolError("content must be a string or null.")
        if on_content_delta and content:
            on_content_delta(content)

        reasoning_present = _field_present(message, "reasoning_content")
        reasoning = _read_field(message, "reasoning_content") if reasoning_present else None
        if reasoning is not None and not isinstance(reasoning, str):
            raise LLMProtocolError("reasoning_content must be a string or null.")

        tool_calls_map: dict[int, dict[str, str]] = {}
        for index, tool_call in enumerate(_read_field(message, "tool_calls") or []):
            function = _read_field(tool_call, "function")
            tool_calls_map[index] = {
                "id": str(_read_field(tool_call, "id") or ""),
                "name": str(_read_field(function, "name") or "") if function else "",
                "arguments": (
                    str(_read_field(function, "arguments") or "")
                    if function
                    else ""
                ),
            }
        tool_calls = self._parse_tool_calls(tool_calls_map)
        return LLMResponse(
            content=content,
            tool_calls=tool_calls or None,
            reasoning_content=reasoning,
            reasoning_content_present=reasoning_present,
            response_id=response_id,
            finish_reason=_bounded_protocol_text(_read_field(choice, "finish_reason")),
        )

    def _parse_tool_calls(
        self,
        tool_calls_map: dict[int, dict[str, str]],
    ) -> list[ToolCall]:
        parsed: list[ToolCall] = []
        for index in sorted(tool_calls_map):
            item = tool_calls_map[index]
            if not item["id"] or not item["name"] or not item["arguments"]:
                raise LLMProtocolError("Tool call is incomplete.")
            try:
                arguments = json.loads(item["arguments"])
            except json.JSONDecodeError as exc:
                raise LLMProtocolError("Tool call arguments are not valid JSON.") from exc
            if not isinstance(arguments, dict):
                raise LLMProtocolError("Tool call arguments must be a JSON object.")
            parsed.append(
                ToolCall(
                    name=item["name"],
                    arguments=arguments,
                    call_id=item["id"],
                    raw_arguments=item["arguments"],
                )
            )
        return parsed


def _read_field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _field_present(value: Any, name: str) -> bool:
    if isinstance(value, dict):
        return name in value
    fields_set = getattr(value, "model_fields_set", None)
    if fields_set is not None:
        return name in fields_set
    model_extra = getattr(value, "model_extra", None)
    if isinstance(model_extra, dict) and name in model_extra:
        return True
    return hasattr(value, name)


def _bounded_protocol_text(value: Any, max_chars: int = 128) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized[:max_chars] or None
