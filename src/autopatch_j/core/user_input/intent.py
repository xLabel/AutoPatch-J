from __future__ import annotations

from collections.abc import Callable
from typing import Any

from autopatch_j.core.domain.intent import IntentType
from autopatch_j.core.user_input.intent_parser import parse_intent_label
from autopatch_j.core.user_input.prompts import INTENT_CLASSIFIER_PROMPT, build_intent_classifier_user_prompt
from autopatch_j.llm.client import LLMCallPurpose


PATCH_ONLY_INTENTS = {IntentType.PATCH_EXPLAIN, IntentType.PATCH_REVISE}


def build_llm_user_intent_classifier(llm: Any | None) -> Callable[[str, bool], IntentType | None] | None:
    if llm is None:
        return None

    def classify(user_text: str, has_pending_review: bool) -> IntentType | None:
        messages = [
            {"role": "system", "content": INTENT_CLASSIFIER_PROMPT},
            {"role": "user", "content": build_intent_classifier_user_prompt(user_text, has_pending_review)},
        ]
        intent = _classify_intent_with_purpose(llm, messages, LLMCallPurpose.CLASSIFIER)
        if intent is not None:
            return intent
        return _classify_intent_with_purpose(llm, messages, LLMCallPurpose.REACT)

    return classify


def _classify_intent_with_purpose(
    llm: Any,
    messages: list[dict[str, str]],
    purpose: LLMCallPurpose,
) -> IntentType | None:
    try:
        response = llm.chat(
            messages=messages,
            tools=None,
            purpose=purpose,
        )
    except Exception:
        return None
    return parse_intent_label(str(response.content))


class UserIntentClassifier:
    """
    用户输入的语义意图分类器。

    职责边界：
    1. 将自然语言输入归类为 code_audit、code_explain、general_chat、patch_explain 或 patch_revise。
    2. 使用短 LLM 做语义判断，并用程序硬约束过滤无 pending review 时的 patch-only 意图。
    3. 不决定 pending review 是否应继续；会话连续性由 ReviewRouteClassifier 判断。
    """

    def __init__(
        self,
        classify_with_llm: Callable[[str, bool], IntentType | None] | None = None,
    ) -> None:
        self.classify_with_llm = classify_with_llm

    def classify(self, user_text: str, has_pending_review: bool) -> IntentType:
        llm_intent = self._fetch_llm_intent(user_text, has_pending_review)

        # 也可以在 has_pending_review=False 时动态裁掉 patch_explain/patch_revise，
        # 只把 code_audit/code_explain/general_chat 暴露给 LLM；但那会让分类协议
        # 随状态分叉，降低 prompt cache 命中和排查一致性。这里保持固定提示词，
        # 让 LLM 做语义分类，状态合法性由程序硬约束。
        if not has_pending_review and llm_intent in PATCH_ONLY_INTENTS:
            llm_intent = None

        if has_pending_review:
            return llm_intent or IntentType.PATCH_EXPLAIN
        return llm_intent or IntentType.GENERAL_CHAT

    def _fetch_llm_intent(self, user_text: str, has_pending_review: bool) -> IntentType | None:
        if self.classify_with_llm is None:
            return None
        try:
            return self.classify_with_llm(user_text, has_pending_review)
        except Exception:
            return None
