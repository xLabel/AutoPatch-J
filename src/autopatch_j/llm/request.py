from __future__ import annotations

import json
from typing import Any

from autopatch_j.llm.options import LLMReasoningMode, LLMRequestOptions


class LLMRequestBuilder:
    """
    OpenAI 兼容请求参数构造器。

    根据调用用途解析出的 options，组装模型、工具、stream、reasoning 和供应商扩展参数。
    """

    def __init__(self, model: str, reasoning_effort: str | None = None) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort

    def build_request_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: LLMRequestOptions,
    ) -> dict[str, Any]:
        extra_body = self._build_extra_body(options)
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": options.stream,
            "extra_body": extra_body,
        }
        if options.stream and extra_body and (extra_body.get("enable_thinking") or "thinking" in extra_body):
            kwargs["stream_options"] = {"include_usage": True}
        if options.reasoning is LLMReasoningMode.INHERIT and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if options.max_tokens is not None:
            kwargs["max_tokens"] = options.max_tokens
        if options.temperature is not None:
            kwargs["temperature"] = options.temperature
        return kwargs

    def _build_extra_body(self, options: LLMRequestOptions) -> dict[str, Any] | None:
        if options.reasoning is LLMReasoningMode.DISABLED:
            return {
                "thinking": {"type": "disabled"},
                "enable_thinking": False,
            }

        extra_body = self._load_global_extra_body()
        return extra_body or None

    def _load_global_extra_body(self) -> dict[str, Any]:
        from autopatch_j.config import GlobalConfig

        if GlobalConfig.llm_extra_body_error:
            raise ValueError(GlobalConfig.llm_extra_body_error)
        try:
            parsed = json.loads(GlobalConfig.llm_extra_body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AUTOPATCH_LLM_EXTRA_BODY 不是有效 JSON：{exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("AUTOPATCH_LLM_EXTRA_BODY 必须是 JSON object")
        return parsed
