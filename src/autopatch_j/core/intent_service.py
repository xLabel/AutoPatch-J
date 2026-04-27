from __future__ import annotations

import re
from collections.abc import Callable

from autopatch_j.core.models import IntentType


class IntentService:
    """
    意图判定服务 (Core Service)
    职责：优先使用本地规则识别用户意图，必要时调用轻量 LLM 分类兜底。
    """

    def __init__(
        self,
        classify_with_llm: Callable[[str, bool], IntentType | None] | None = None,
    ) -> None:
        self.classify_with_llm = classify_with_llm

    def detect_intent(self, user_text: str, has_pending_review: bool) -> IntentType:
        normalized = self._normalize_text(user_text)
        if has_pending_review:
            local = self._fetch_review_intent_by_rule(normalized)
            if local is not None:
                return local
            llm_intent = self._fetch_llm_intent(normalized, has_pending_review=True)
            return llm_intent or IntentType.PATCH_REVISE

        local = self._fetch_entry_intent_by_rule(normalized)
        if local is not None:
            return local
        llm_intent = self._fetch_llm_intent(normalized, has_pending_review=False)
        return llm_intent or IntentType.GENERAL_CHAT

    def _fetch_entry_intent_by_rule(self, normalized_text: str) -> IntentType | None:
        if self._contains_any(normalized_text, ("检查", "扫描", "修复", "漏洞", "风险", "空指针", "问题")):
            return IntentType.CODE_AUDIT
        if self._contains_any(normalized_text, ("解释", "说明", "功能", "做什么", "干嘛", "什么意思")):
            return IntentType.CODE_EXPLAIN
        return None

    def _fetch_review_intent_by_rule(self, normalized_text: str) -> IntentType | None:
        if self._contains_any(
            normalized_text,
            (
                "加一句",
                "加一行",
                "加个",
                "加注释",
                "注释",
                "改成",
                "改一下",
                "换成",
                "不要",
                "只改",
                "换个写法",
                "补一句",
                "补一行",
                "这个方案不对",
            ),
        ):
            return IntentType.PATCH_REVISE
        if self._contains_any(normalized_text, ("为什么", "影响性能", "什么意思", "说明", "解释")):
            return IntentType.PATCH_EXPLAIN
        return None

    def _fetch_llm_intent(self, normalized_text: str, has_pending_review: bool) -> IntentType | None:
        if self.classify_with_llm is None:
            return None
        return self.classify_with_llm(normalized_text, has_pending_review)

    def _contains_any(self, normalized_text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in normalized_text for keyword in keywords)

    def _normalize_text(self, user_text: str) -> str:
        compact = re.sub(r"\s+", "", user_text)
        return compact.lower()
