"""意图和路由的结果来源及分类失败原因。"""

from __future__ import annotations

import dataclasses

from autopatch_j.core.domain import intent as domain_intent


@dataclasses.dataclass(frozen=True, slots=True)
class IntentClassificationResult:
    """用户意图识别结果及其程序侧 fallback 诊断。"""

    intent: domain_intent.IntentType
    source: str
    fallback_reason: str = ""
    error: str = ""

    @property
    def used_fallback(self) -> bool:
        """本次意图或路由结果是否来自确定性默认值。"""
        return bool(self.fallback_reason)


@dataclasses.dataclass(frozen=True, slots=True)
class RouteClassificationResult:
    """pending review 连续性路由结果及其程序侧 fallback 诊断。"""

    route: domain_intent.ConversationRoute
    source: str
    fallback_reason: str = ""
    error: str = ""

    @property
    def used_fallback(self) -> bool:
        """本次意图或路由结果是否来自确定性默认值。"""
        return bool(self.fallback_reason)
