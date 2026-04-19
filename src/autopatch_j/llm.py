from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import error, request


@dataclass(slots=True)
class LLMToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    call_id: str | None = None


@dataclass(slots=True)
class LLMResponse:
    content: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


class ChatCompletionClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    @property
    def label(self) -> str:
        return f"chat-completions:{self.model}"

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        stream: bool = True,
        include_usage: bool = True,
        temperature: float | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        payload = self.build_payload(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stream=stream,
            include_usage=include_usage,
            temperature=temperature,
        )
        return self._complete_with_retries(payload, stream=stream, on_delta=on_delta)

    def build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        stream: bool = True,
        include_usage: bool = True,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format is not None:
            payload["response_format"] = response_format
        if temperature is not None:
            payload["temperature"] = temperature
        if stream and include_usage:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _complete_with_retries(
        self,
        payload: dict[str, Any],
        stream: bool,
        on_delta: Callable[[str], None] | None,
    ) -> LLMResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._complete_once(payload, stream=stream, on_delta=on_delta)
            except error.HTTPError as exc:
                if should_retry_without_stream_options(exc, payload):
                    retry_payload = dict(payload)
                    retry_payload.pop("stream_options", None)
                    return self._complete_with_retries(
                        retry_payload,
                        stream=stream,
                        on_delta=on_delta,
                    )
                last_error = exc
            except (TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc

            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_seconds * (attempt + 1))

        assert last_error is not None
        raise last_error

    def _complete_once(
        self,
        payload: dict[str, Any],
        stream: bool,
        on_delta: Callable[[str], None] | None,
    ) -> LLMResponse:
        http_request = self.build_request(payload)
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            if stream:
                return parse_chat_completion_stream(response, on_delta=on_delta)
            raw = response.read().decode("utf-8")
        return parse_chat_completion_response(json.loads(raw))

    def build_request(self, payload: dict[str, Any]) -> request.Request:
        body = json.dumps(payload).encode("utf-8")
        return request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )


def build_default_llm_client() -> ChatCompletionClient | None:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    model = (
        os.getenv("LLM_MODEL")
        or "gpt-5.4-mini"
    )
    base_url = (
        os.getenv("LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    return ChatCompletionClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


def parse_chat_completion_response(payload: dict[str, Any]) -> LLMResponse:
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return LLMResponse(raw=payload, usage=extract_usage(payload))

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return LLMResponse(raw=payload, usage=extract_usage(payload))

    message = first_choice.get("message", {})
    if not isinstance(message, dict):
        return LLMResponse(raw=payload, usage=extract_usage(payload))

    return LLMResponse(
        content=str(message.get("content") or ""),
        tool_calls=parse_message_tool_calls(message.get("tool_calls", [])),
        usage=extract_usage(payload),
        raw=payload,
    )


def parse_chat_completion_stream(
    response: Any,
    on_delta: Callable[[str], None] | None = None,
) -> LLMResponse:
    content_parts: list[str] = []
    tool_accumulator: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None
    last_event: dict[str, Any] | None = None

    for raw_line in response:
        line = decode_sse_line(raw_line)
        if not line or not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break

        event = json.loads(data)
        last_event = event
        event_usage = extract_usage(event)
        if event_usage is not None:
            usage = event_usage

        choices = event.get("choices", [])
        if not isinstance(choices, list) or not choices:
            continue
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            continue
        delta = first_choice.get("delta", {})
        if not isinstance(delta, dict):
            continue

        content_delta = delta.get("content")
        if content_delta:
            text = str(content_delta)
            content_parts.append(text)
            if on_delta is not None:
                on_delta(text)

        merge_tool_call_deltas(tool_accumulator, delta.get("tool_calls", []))

    return LLMResponse(
        content="".join(content_parts),
        tool_calls=build_stream_tool_calls(tool_accumulator),
        usage=usage,
        raw=last_event,
    )


def parse_message_tool_calls(raw_tool_calls: Any) -> list[LLMToolCall]:
    if not isinstance(raw_tool_calls, list):
        return []
    parsed: list[LLMToolCall] = []
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function", {})
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "")
        raw_arguments = str(function.get("arguments") or "")
        parsed.append(
            LLMToolCall(
                call_id=str(item.get("id")) if item.get("id") else None,
                name=name,
                raw_arguments=raw_arguments,
                arguments=parse_json_object(raw_arguments),
            )
        )
    return parsed


def merge_tool_call_deltas(accumulator: dict[int, dict[str, Any]], raw_tool_calls: Any) -> None:
    if not isinstance(raw_tool_calls, list):
        return
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", 0))
        except (TypeError, ValueError):
            index = 0
        current = accumulator.setdefault(
            index,
            {
                "id": None,
                "name": "",
                "arguments": [],
            },
        )
        if item.get("id"):
            current["id"] = str(item["id"])
        function = item.get("function", {})
        if not isinstance(function, dict):
            continue
        if function.get("name"):
            current["name"] += str(function["name"])
        if function.get("arguments"):
            current["arguments"].append(str(function["arguments"]))


def build_stream_tool_calls(accumulator: dict[int, dict[str, Any]]) -> list[LLMToolCall]:
    calls: list[LLMToolCall] = []
    for index in sorted(accumulator):
        item = accumulator[index]
        raw_arguments = "".join(item["arguments"])
        calls.append(
            LLMToolCall(
                call_id=item["id"],
                name=str(item["name"]),
                raw_arguments=raw_arguments,
                arguments=parse_json_object(raw_arguments),
            )
        )
    return calls


def parse_json_object(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_usage(payload: dict[str, Any]) -> dict[str, Any] | None:
    usage = payload.get("usage")
    return usage if isinstance(usage, dict) else None


def decode_sse_line(raw_line: bytes | str) -> str:
    if isinstance(raw_line, bytes):
        return raw_line.decode("utf-8").strip()
    return raw_line.strip()


def should_retry_without_stream_options(
    exc: error.HTTPError,
    payload: dict[str, Any],
) -> bool:
    if "stream_options" not in payload:
        return False
    if exc.code not in {400, 422}:
        return False
    try:
        body = exc.read().decode("utf-8", errors="replace").lower()
    except OSError:
        return True
    finally:
        exc.close()
    return "stream_options" in body or "include_usage" in body or "unknown" in body
