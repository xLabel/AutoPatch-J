from __future__ import annotations

from autopatch_j.core.conversation_router import ConversationRouter
from autopatch_j.core.models import CodeScope, CodeScopeKind, ConversationRoute


def _scope() -> CodeScope:
    return CodeScope(
        kind=CodeScopeKind.SINGLE_FILE,
        source_roots=["src/main/java/demo/UserService.java"],
        focus_files=["src/main/java/demo/UserService.java"],
        is_locked=True,
    )


def test_continuity_judge_treats_scope_change_as_new_task() -> None:
    service = ConversationRouter()

    route = service.fetch_route(
        user_text="@demo 检查代码",
        has_pending_review=True,
        requested_scope=_scope(),
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.NEW_TASK


def test_continuity_judge_defaults_ambiguous_review_input_to_review_continue() -> None:
    service = ConversationRouter()

    route = service.fetch_route(
        user_text="这个再看看",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.REVIEW_CONTINUE


def test_continuity_judge_uses_llm_classifier_for_ambiguous_review_input() -> None:
    class FakeLLM:
        def chat(self, messages, tools=None, extra_body=None, on_token=None, on_reasoning_token=None):
            class Response:
                content = "NEW_TASK"
                tool_calls = None
                reasoning_content = None

            return Response()

    service = ConversationRouter(llm=FakeLLM())  # type: ignore[arg-type]

    route = service.fetch_route(
        user_text="重新检查一下",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.NEW_TASK
