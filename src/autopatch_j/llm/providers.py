from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import LLMProtocolError
from .models import LLMResponse
from .options import LLMReasoningMode, LLMRequestOptions


DEEPSEEK_V4_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})


class ProviderKind(Enum):
    DEEPSEEK_V4 = "deepseek-v4"
    OPENAI_CHAT = "openai-chat"


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    kind: ProviderKind

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def is_deepseek_v4(self) -> bool:
        return self.kind is ProviderKind.DEEPSEEK_V4

    def prepare_messages(
        self,
        messages: list[dict[str, Any]],
        options: LLMRequestOptions,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for message in messages:
            item = dict(message)
            if self.is_deepseek_v4:
                if (
                    options.reasoning is LLMReasoningMode.ENABLED
                    and item.get("role") == "assistant"
                    and item.get("tool_calls")
                    and (
                        "reasoning_content" not in item
                        or not isinstance(item["reasoning_content"], str)
                    )
                ):
                    raise LLMProtocolError(
                        "DeepSeek thinking tool-call history is missing reasoning_content."
                    )
            else:
                item.pop("reasoning_content", None)
            prepared.append(item)
        return prepared

    def request_fields(
        self,
        *,
        options: LLMRequestOptions,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if self.is_deepseek_v4:
            thinking_type = (
                "enabled"
                if options.reasoning is LLMReasoningMode.ENABLED
                else "disabled"
            )
            fields["extra_body"] = {"thinking": {"type": thinking_type}}
            fields["max_tokens"] = max_output_tokens
            if options.reasoning is LLMReasoningMode.ENABLED:
                fields["reasoning_effort"] = "max"
        else:
            fields["max_completion_tokens"] = max_output_tokens
        return fields

    def validate_response(
        self,
        response: LLMResponse,
        options: LLMRequestOptions,
    ) -> None:
        if (
            self.is_deepseek_v4
            and options.reasoning is LLMReasoningMode.ENABLED
            and response.tool_calls
            and (
                not response.reasoning_content_present
                or response.reasoning_content is None
            )
        ):
            raise LLMProtocolError(
                "DeepSeek thinking tool-call response is missing reasoning_content."
            )


def resolve_provider_profile(model: str) -> ProviderProfile:
    normalized = model.strip()
    if normalized in DEEPSEEK_V4_MODELS:
        return ProviderProfile(ProviderKind.DEEPSEEK_V4)
    return ProviderProfile(ProviderKind.OPENAI_CHAT)
