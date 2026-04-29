from __future__ import annotations

import re
from collections.abc import Callable

from autopatch_j.core.models import IntentType


class IntentDetector:
    """
    意图判定服务 (Intent Gateway)。
    核心职责：作为拦截幻觉和约束 Agent 行为的“前哨站”。
    纯粹依赖大模型的零样本分类能力，摒弃脆弱的本地正则/规则匹配，
    精准区分 code_audit（代码审查）、code_explain（代码解释）、patch_revise（补丁修改）等工作流。
    """

    def __init__(
        self,
        classify_with_llm: Callable[[str, bool], IntentType | None] | None = None,
    ) -> None:
        self.classify_with_llm = classify_with_llm

    def detect_intent(self, user_text: str, has_pending_review: bool) -> IntentType:
        normalized = self._normalize_text(user_text)
        llm_intent = self._fetch_llm_intent(normalized, has_pending_review)
        
        if has_pending_review:
            return llm_intent or IntentType.PATCH_REVISE
        return llm_intent or IntentType.GENERAL_CHAT

    def _fetch_llm_intent(self, normalized_text: str, has_pending_review: bool) -> IntentType | None:
        if self.classify_with_llm is None:
            return None
        return self.classify_with_llm(normalized_text, has_pending_review)

    def _normalize_text(self, user_text: str) -> str:
        compact = re.sub(r"\s+", "", user_text)
        return compact.lower()
