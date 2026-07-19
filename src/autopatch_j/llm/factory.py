from __future__ import annotations

from autopatch_j.config import GlobalConfig
from autopatch_j.llm.client import LLMClient


def build_default_llm_client() -> LLMClient | None:
    if not GlobalConfig.llm_api_key:
        return None
    return LLMClient(
        api_key=GlobalConfig.llm_api_key,
        base_url=GlobalConfig.llm_base_url,
        model=GlobalConfig.llm_model,
        reasoning_effort=GlobalConfig.llm_reasoning_effort,
        stream_dialect=GlobalConfig.llm_stream_dialect,
        context_profile=GlobalConfig.resolve_llm_context_profile(),
    )
