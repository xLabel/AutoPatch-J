from __future__ import annotations

from dataclasses import dataclass

from autopatch_j.core.domain.intent import ConversationRoute, IntentType


@dataclass(frozen=True, slots=True)
class IntentClassificationResult:
    """用户意图识别结果及其程序侧 fallback 诊断。"""

    intent: IntentType
    source: str
    fallback_reason: str = ""
    error: str = ""

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallback_reason)


@dataclass(frozen=True, slots=True)
class RouteClassificationResult:
    """pending review 连续性路由结果及其程序侧 fallback 诊断。"""

    route: ConversationRoute
    source: str
    fallback_reason: str = ""
    error: str = ""

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallback_reason)
