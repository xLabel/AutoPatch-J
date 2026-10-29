"""使用固定分类回调核对审核连续性，分类不调用真实模型。"""

from __future__ import annotations

from autopatch_j.core.domain import intent
from autopatch_j.core.domain import scope
from autopatch_j.core.user_input import router


def _scope() -> scope.CodeScope:
    return scope.CodeScope(
        kind=scope.CodeScopeKind.SINGLE_FILE,
        source_roots=["src/main/java/demo/UserService.java"],
        focus_files=["src/main/java/demo/UserService.java"],
        is_locked=True,
    )


def test_review_route_treats_scope_change_as_new_task() -> None:
    service = router.ReviewRouteClassifier()

    route = service.classify_route(
        user_text="@demo 检查代码",
        has_pending_review=True,
        requested_scope=_scope(),
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is intent.ConversationRoute.NEW_TASK


def test_review_route_defaults_ambiguous_review_input_to_review_continue() -> (
    None
):
    service = router.ReviewRouteClassifier()

    route = service.classify_route(
        user_text="这个再看看",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is intent.ConversationRoute.REVIEW_CONTINUE


def test_classifier_callback_can_choose_new_task():
    service = router.ReviewRouteClassifier(
        classify_with_llm=lambda text, file, scope: (
            intent.ConversationRoute.NEW_TASK,
            "",
        )
    )
    result = service.classify_route_with_diagnostics(
        user_text="重新检查",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="A.java",
        current_scope=_scope(),
    )
    assert result.route is intent.ConversationRoute.NEW_TASK


def test_classifier_failure_preserves_review():
    service = router.ReviewRouteClassifier(
        classify_with_llm=lambda text, file, scope: (None, "ModelFailure")
    )
    result = service.classify_route_with_diagnostics(
        user_text="重新检查",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="A.java",
        current_scope=_scope(),
    )
    assert result.route is intent.ConversationRoute.REVIEW_CONTINUE
    assert result.source == "fallback"
