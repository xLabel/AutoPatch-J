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
        context_profile=GlobalConfig.resolve_llm_context_profile(),
    )
