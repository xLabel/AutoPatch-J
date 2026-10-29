"""审核会话的显式信号与语义分类结果，不依赖模型 SDK。"""

from collections.abc import Callable
import dataclasses

from autopatch_j.core.domain import intent
from autopatch_j.core.domain import scope
from autopatch_j.core.user_input import diagnostics


@dataclasses.dataclass(slots=True)
class ReviewRouteClassifier:
    """pending review 场景下的会话连续性路由器。

    职责边界：
    1. 判断用户输入是继续当前补丁审核、发起新任务，还是系统命令。
    2. 优先使用显式信号（命令、@scope）切换路径，模糊输入再交给短 LLM。
    3. 不分类具体业务意图；route 确定后才由 UserIntentClassifier 决定工作流类型。
    """

    classify_with_llm: (
        Callable[
            [str, str | None, scope.CodeScope | None],
            tuple[intent.ConversationRoute | None, str],
        ]
        | None
    ) = None

    def classify_route(
        self,
        user_text: str,
        has_pending_review: bool,
        requested_scope: scope.CodeScope | None,
        current_patch_file: str | None,
        current_scope: scope.CodeScope | None,
    ) -> intent.ConversationRoute:
        """判断是否继续当前审核，显式新范围优先于模型分类。"""
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
        requested_scope: scope.CodeScope | None,
        current_patch_file: str | None,
        current_scope: scope.CodeScope | None,
    ) -> diagnostics.RouteClassificationResult:
        """返回审核路由及分类失败原因，失败默认继续审核。"""
        stripped = user_text.strip()
        if stripped.startswith("/"):
            return diagnostics.RouteClassificationResult(
                route=intent.ConversationRoute.COMMAND,
                source="explicit_command",
            )
        if not has_pending_review:
            return diagnostics.RouteClassificationResult(
                route=intent.ConversationRoute.NEW_TASK,
                source="no_pending_review",
            )
        if "@" in user_text or requested_scope is not None:
            return diagnostics.RouteClassificationResult(
                route=intent.ConversationRoute.NEW_TASK, source="explicit_scope"
            )

        llm_route, error = (
            self.classify_with_llm(user_text, current_patch_file, current_scope)
            if self.classify_with_llm is not None
            else (None, "未配置路由分类器")
        )
        if llm_route is not None:
            return diagnostics.RouteClassificationResult(
                route=llm_route, source="llm", fallback_reason=error
            )
        return diagnostics.RouteClassificationResult(
            route=intent.ConversationRoute.REVIEW_CONTINUE,
            source="fallback",
            fallback_reason=error or "route classifier returned no valid route",
            error=error,
        )
