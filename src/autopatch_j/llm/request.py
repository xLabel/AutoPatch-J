from __future__ import annotations

from typing import Any

from .options import LLMRequestOptions
from .providers import ProviderProfile


class LLMRequestBuilder:
    """Build one provider request from typed provider and purpose profiles."""

    def __init__(
        self,
        *,
        model: str,
        provider: ProviderProfile,
        max_output_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.max_output_tokens = max_output_tokens

    def build_request_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: LLMRequestOptions,
    ) -> dict[str, Any]:
        requested_max = options.max_tokens
        if requested_max is None:
            if self.max_output_tokens is None:
                raise ValueError("LLM purpose 缺少 operational output cap")
            requested_max = self.max_output_tokens
        effective_max = (
            min(requested_max, self.max_output_tokens)
            if self.max_output_tokens is not None
            else requested_max
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.provider.prepare_messages(messages, options),
            "stream": options.stream,
        }
        if tools is not None:
            kwargs["tools"] = tools
        kwargs.update(
            self.provider.request_fields(
                options=options,
                max_output_tokens=effective_max,
            )
        )
        if options.temperature is not None:
            kwargs["temperature"] = options.temperature
        if options.timeout_seconds is not None:
            kwargs["timeout"] = options.timeout_seconds
        if options.response_format is not None:
            kwargs["response_format"] = dict(options.response_format)
        return kwargs
