from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class LLMCallPurpose(Enum):
    """LLM 调用意图，业务层只声明用途，不传供应商参数。"""

    REACT = auto()
    CLASSIFIER = auto()
    MEMORY_EXTRACTION = auto()
    MEMORY_CONSOLIDATION = auto()
    CONTEXT_COMPACTION = auto()


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
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class LLMCallDiagnostic:
    """单次 LLM 调用的轻量诊断；不主动附加 request context。

    provider RAW exception/body 可能包含供应商自行回显的 prompt 或认证文本。
    """

    purpose: LLMCallPurpose
    stream: bool
    reasoning: LLMReasoningMode
    max_tokens: int | None
    temperature: float | None
    status: str
    timeout_seconds: float | None = None
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
    LLMCallPurpose.MEMORY_EXTRACTION: LLMRequestOptions(
        stream=False,
        reasoning=LLMReasoningMode.DISABLED,
        max_tokens=1800,
        temperature=0,
        timeout_seconds=60,
    ),
    LLMCallPurpose.MEMORY_CONSOLIDATION: LLMRequestOptions(
        stream=False,
        reasoning=LLMReasoningMode.DISABLED,
        max_tokens=2200,
        temperature=0,
        timeout_seconds=60,
    ),
    LLMCallPurpose.CONTEXT_COMPACTION: LLMRequestOptions(
        stream=False,
        reasoning=LLMReasoningMode.DISABLED,
        max_tokens=16_384,
        temperature=0,
        timeout_seconds=120,
    ),
}


def resolve_request_options(purpose: LLMCallPurpose) -> LLMRequestOptions:
    return _PURPOSE_OPTIONS[purpose]
