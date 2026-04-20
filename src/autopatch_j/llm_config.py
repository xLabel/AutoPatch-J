from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_LLM_MODEL = "gpt-5.4-mini"
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"


@dataclass(slots=True)
class LLMConfig:
    api_key: str
    model: str
    base_url: str


def load_llm_config() -> LLMConfig | None:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    return LLMConfig(
        api_key=api_key,
        model=os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL,
        base_url=(
            os.getenv("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or DEFAULT_LLM_BASE_URL
        ),
    )


def has_llm_config() -> bool:
    return load_llm_config() is not None


def missing_llm_config_message(capability: str) -> str:
    return (
        f"LLM 配置缺失，无法启用{capability}。"
        "请设置 LLM_API_KEY；如果使用 OpenAI-compatible 服务，"
        "可同时设置 LLM_BASE_URL 和 LLM_MODEL。"
    )
