from __future__ import annotations

from typing import Any

import openai

from autopatch_j.llm.options import LLMReasoningMode, LLMRequestOptions


class OpenAIChatTransport:
    """
    OpenAI 兼容 Chat Completions 传输层。

    只负责 SDK 调用和协议兼容 retry，不解析响应，也不理解上层业务意图。
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def create_completion(self, kwargs: dict[str, Any], options: LLMRequestOptions) -> Any:
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not self.should_retry_without_disabled_reasoning(exc, kwargs, options):
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs["extra_body"] = None
            retry_kwargs.pop("stream_options", None)
            return self.client.chat.completions.create(**retry_kwargs)

    def should_retry_without_disabled_reasoning(
        self,
        exc: Exception,
        kwargs: dict[str, Any],
        options: LLMRequestOptions,
    ) -> bool:
        if options.reasoning is not LLMReasoningMode.DISABLED:
            return False
        if not kwargs.get("extra_body"):
            return False
        message = str(exc).lower()
        retry_markers = (
            "thinking",
            "enable_thinking",
            "extra_body",
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized",
            "invalid parameter",
        )
        return any(marker in message for marker in retry_markers)
