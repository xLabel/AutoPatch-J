from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class LLMCallPurpose(Enum):
    """LLM 调用意图，业务层只声明用途，不传供应商参数。"""

    REACT = auto()
    CLASSIFIER = auto()
    MEMORY_SUMMARY = auto()


class LLMReasoningMode(Enum):
    """LLMClient 内部使用的 reasoning 策略。"""

    INHERIT = auto()
    DISABLED = auto()


@dataclass(frozen=True, slots=True)
class LLMRequestOptions:
    """由调用意图解析出的底层请求选项。"""

    stream: bool
    reasoning: LLMReasoningMode
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class LLMCallDiagnostic:
    """单次 LLM 调用的轻量诊断信息，不包含 prompt、token 或密钥。"""

    purpose: LLMCallPurpose
    stream: bool
    reasoning: LLMReasoningMode
    max_tokens: int | None
    temperature: float | None
    status: str
    error: str = ""


_PURPOSE_OPTIONS: dict[LLMCallPurpose, LLMRequestOptions] = {
    LLMCallPurpose.REACT: LLMRequestOptions(
        stream=True,
        reasoning=LLMReasoningMode.INHERIT,
    ),
    LLMCallPurpose.CLASSIFIER: LLMRequestOptions(
        stream=False,
        reasoning=LLMReasoningMode.DISABLED,
        max_tokens=128,
        temperature=0,
    ),
    LLMCallPurpose.MEMORY_SUMMARY: LLMRequestOptions(
        stream=False,
        reasoning=LLMReasoningMode.DISABLED,
        max_tokens=1200,
        temperature=0,
    ),
}


def resolve_request_options(purpose: LLMCallPurpose) -> LLMRequestOptions:
    return _PURPOSE_OPTIONS[purpose]
