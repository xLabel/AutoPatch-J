from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import openai

from .options import LLMRequestOptions


@dataclass(frozen=True, slots=True)
class TransportCompletion:
    response: Any
    sdk_retries_taken: int | None


class OpenAIChatTransport:
    """OpenAI Chat Completions transport for one logical operation."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def create_completion(
        self,
        kwargs: dict[str, Any],
        options: LLMRequestOptions,
    ) -> TransportCompletion:
        client = self.client.with_options(max_retries=options.sdk_max_retries)
        raw_response = client.chat.completions.with_raw_response.create(**kwargs)
        retries_taken = getattr(raw_response, "retries_taken", None)
        if not isinstance(retries_taken, int) or retries_taken < 0:
            retries_taken = None
        return TransportCompletion(
            response=raw_response.parse(),
            sdk_retries_taken=retries_taken,
        )
