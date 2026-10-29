"""用户意图的状态合法性和确定性默认值；不直接调用模型。"""

from collections.abc import Callable

from autopatch_j.core.domain import intent as domain_intent
from autopatch_j.core.user_input import diagnostics

PATCH_ONLY_INTENTS = {
    domain_intent.IntentType.PATCH_EXPLAIN,
    domain_intent.IntentType.PATCH_REVISE,
}


class UserIntentClassifier:
    """用户输入的语义意图分类器。

    职责边界：
    1. 将自然语言输入归类为 code_audit、code_explain、general_chat、patch_explain 或
    patch_revise。
    2. 使用短 LLM 做语义判断，并用程序硬约束过滤无 pending review 时的 patch-only 意图。
    3. 不决定 pending review 是否应继续；会话连续性由 ReviewRouteClassifier 判断。
    """

    def __init__(
        self,
        classify_with_llm: (
            Callable[
                [str, bool],
                domain_intent.IntentType
                | None
                | tuple[domain_intent.IntentType | None, str],
            ]
            | None
        ) = None,
    ) -> None:
        self.classify_with_llm = classify_with_llm

    def classify(
        self, user_text: str, has_pending_review: bool
    ) -> domain_intent.IntentType:
        """对输入分类；模型不可用时使用与审核状态相符的确定性默认值。"""
        return self.classify_with_diagnostics(
            user_text, has_pending_review
        ).intent

    def classify_with_diagnostics(
        self, user_text: str, has_pending_review: bool
    ) -> diagnostics.IntentClassificationResult:
        """返回合法意图、来源与失败原因，不进行第二轮生成。"""
        llm_intent, error = self._fetch_llm_intent_with_error(
            user_text, has_pending_review
        )
        source = "llm" if llm_intent is not None else "fallback"
        fallback_reason = error if llm_intent is not None else ""

        # 也可以在 has_pending_review=False 时动态裁掉 patch_explain/patch_revise，
        # 只把 code_audit/code_explain/general_chat 暴露给 LLM；但那会让分类协议
        # 随状态分叉，降低 prompt cache 命中和排查一致性。这里保持固定提示词，
        # 让 LLM 做语义分类，状态合法性由程序硬约束。
        if not has_pending_review and llm_intent in PATCH_ONLY_INTENTS:
            llm_intent = None
            source = "fallback"
            fallback_reason = (
                "patch-only intent rejected without pending review"
            )

        if has_pending_review:
            intent = llm_intent or domain_intent.IntentType.PATCH_EXPLAIN
            fallback_reason = fallback_reason or (
                error if llm_intent is None else ""
            )
        else:
            intent = llm_intent or domain_intent.IntentType.GENERAL_CHAT
            fallback_reason = fallback_reason or (
                error if llm_intent is None else ""
            )

        return diagnostics.IntentClassificationResult(
            intent=intent,
            source=source,
            fallback_reason=fallback_reason,
            error=error,
        )

    def _fetch_llm_intent(
        self, user_text: str, has_pending_review: bool
    ) -> domain_intent.IntentType | None:
        intent, _ = self._fetch_llm_intent_with_error(
            user_text, has_pending_review
        )
        return intent

    def _fetch_llm_intent_with_error(
        self,
        user_text: str,
        has_pending_review: bool,
    ) -> tuple[domain_intent.IntentType | None, str]:
        if self.classify_with_llm is None:
            return None, "llm classifier is not configured"
        result = self.classify_with_llm(user_text, has_pending_review)
        if isinstance(result, tuple):
            return result
        return result, ""
