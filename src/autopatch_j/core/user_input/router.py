from __future__ import annotations

import re
from dataclasses import dataclass

from autopatch_j.core.domain.intent import ConversationRoute
from autopatch_j.core.domain.scope import CodeScope
from autopatch_j.core.user_input.prompts import REVIEW_ROUTE_CLASSIFIER_PROMPT, build_review_route_user_prompt
from autopatch_j.llm.client import LLMCallPurpose, LLMClient


@dataclass(slots=True)
class ReviewRouteClassifier:
    """
    pending review 场景下的会话连续性路由器。

    职责边界：
    1. 判断用户输入是继续当前补丁审核、发起新任务，还是系统命令。
    2. 优先使用显式信号（命令、@scope）切换路径，模糊输入再交给短 LLM。
    3. 不分类具体业务意图；route 确定后才由 UserIntentClassifier 决定工作流类型。
    """

    llm: LLMClient | None = None

    def classify_route(
        self,
        user_text: str,
        has_pending_review: bool,
        requested_scope: CodeScope | None,
        current_patch_file: str | None,
        current_scope: CodeScope | None,
    ) -> ConversationRoute:
        stripped = user_text.strip()
        if stripped.startswith("/"):
            return ConversationRoute.COMMAND
        if not has_pending_review:
            return ConversationRoute.NEW_TASK
        if "@" in user_text or requested_scope is not None:
            return ConversationRoute.NEW_TASK

        llm_route = self._fetch_route_by_llm(
            user_text=user_text,
            current_patch_file=current_patch_file,
            current_scope=current_scope,
        )
        return llm_route or ConversationRoute.REVIEW_CONTINUE

    def _fetch_route_by_llm(
        self,
        user_text: str,
        current_patch_file: str | None,
        current_scope: CodeScope | None,
    ) -> ConversationRoute | None:
        if self.llm is None:
            return None

        scope_summary = (
            ", ".join(current_scope.focus_files)
            if current_scope and current_scope.focus_files
            else "无"
        )
        messages = [
            {"role": "system", "content": REVIEW_ROUTE_CLASSIFIER_PROMPT},
            {"role": "user", "content": build_review_route_user_prompt(user_text, current_patch_file, scope_summary)},
        ]
        route = self._fetch_route_with_purpose(messages, LLMCallPurpose.CLASSIFIER)
        if route is not None:
            return route
        return self._fetch_route_with_purpose(messages, LLMCallPurpose.REACT)

    def _fetch_route_with_purpose(
        self,
        messages: list[dict[str, str]],
        purpose: LLMCallPurpose,
    ) -> ConversationRoute | None:
        if self.llm is None:
            return None
        try:
            response = self.llm.chat(
                messages=messages,
                tools=None,
                purpose=purpose,
            )
        except Exception:
            return None
        return self._parse_route(str(response.content))

    def _parse_route(self, raw_text: str) -> ConversationRoute | None:
        normalized = re.sub(r"[^A-Z_]", "", raw_text.upper())
        compact = normalized.replace("_", "")
        if "NEWTASK" in compact:
            return ConversationRoute.NEW_TASK
        if "REVIEWCONTINUE" in compact:
            return ConversationRoute.REVIEW_CONTINUE
        if "COMMAND" in compact:
            return ConversationRoute.COMMAND
        return None
