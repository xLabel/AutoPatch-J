from __future__ import annotations

import re
from dataclasses import dataclass

from autopatch_j.core.domain.intent import ConversationRoute
from autopatch_j.core.domain.scope import CodeScope
from autopatch_j.core.user_input.diagnostics import RouteClassificationResult
from autopatch_j.core.user_input.prompts import REVIEW_ROUTE_CLASSIFIER_PROMPT, build_review_route_user_prompt
from autopatch_j.llm.client import LLMClient
from autopatch_j.llm.diagnostics import format_raw_llm_exception
from autopatch_j.llm.options import LLMCallPurpose


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
        return self.classify_route_with_diagnostics(
            user_text=user_text,
            has_pending_review=has_pending_review,
            requested_scope=requested_scope,
            current_patch_file=current_patch_file,
            current_scope=current_scope,
        ).route

    def classify_route_with_diagnostics(
        self,
        user_text: str,
        has_pending_review: bool,
        requested_scope: CodeScope | None,
        current_patch_file: str | None,
        current_scope: CodeScope | None,
    ) -> RouteClassificationResult:
        stripped = user_text.strip()
        if stripped.startswith("/"):
            return RouteClassificationResult(route=ConversationRoute.COMMAND, source="explicit_command")
        if not has_pending_review:
            return RouteClassificationResult(route=ConversationRoute.NEW_TASK, source="no_pending_review")
        if "@" in user_text or requested_scope is not None:
            return RouteClassificationResult(route=ConversationRoute.NEW_TASK, source="explicit_scope")

        llm_route, error = self._fetch_route_by_llm(
            user_text=user_text,
            current_patch_file=current_patch_file,
            current_scope=current_scope,
        )
        if llm_route is not None:
            return RouteClassificationResult(route=llm_route, source="llm", fallback_reason=error)
        return RouteClassificationResult(
            route=ConversationRoute.REVIEW_CONTINUE,
            source="fallback",
            fallback_reason=error or "route classifier returned no valid route",
            error=error,
        )

    def _fetch_route_by_llm(
        self,
        user_text: str,
        current_patch_file: str | None,
        current_scope: CodeScope | None,
    ) -> tuple[ConversationRoute | None, str]:
        if self.llm is None:
            return None, "route classifier llm is not configured"

        scope_summary = (
            ", ".join(current_scope.focus_files)
            if current_scope and current_scope.focus_files
            else "无"
        )
        messages = [
            {"role": "system", "content": REVIEW_ROUTE_CLASSIFIER_PROMPT},
            {"role": "user", "content": build_review_route_user_prompt(user_text, current_patch_file, scope_summary)},
        ]
        route, error = self._fetch_route_with_purpose(messages, LLMCallPurpose.CLASSIFIER)
        if route is not None:
            return route, ""
        fallback_route, fallback_error = self._fetch_route_with_purpose(messages, LLMCallPurpose.REACT)
        if fallback_route is not None:
            return fallback_route, f"classifier empty or invalid; react fallback used ({error or 'no route'})"
        return None, fallback_error or error

    def _fetch_route_with_purpose(
        self,
        messages: list[dict[str, str]],
        purpose: LLMCallPurpose,
    ) -> tuple[ConversationRoute | None, str]:
        if self.llm is None:
            return None, "route classifier llm is not configured"
        try:
            response = self.llm.chat(
                messages=messages,
                tools=None,
                purpose=purpose,
            )
        except Exception as exc:
            return None, (
                f"{purpose.name.lower()} exception: {format_raw_llm_exception(exc)}"
            )
        route = self._parse_route(str(response.content))
        if route is None:
            return None, f"{purpose.name.lower()} returned invalid route"
        return route, ""

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
