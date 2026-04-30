from __future__ import annotations

from autopatch_j.agent.llm_client import LLMCallPurpose
from autopatch_j.core.input_classifier import ConversationRouter
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

    route = service.determine_route(
        user_text="@demo 检查代码",
        has_pending_review=True,
        requested_scope=_scope(),
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.NEW_TASK


def test_continuity_judge_defaults_ambiguous_review_input_to_review_continue() -> None:
    service = ConversationRouter()

    route = service.determine_route(
        user_text="这个再看看",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.REVIEW_CONTINUE


def test_continuity_judge_uses_llm_classifier_for_ambiguous_review_input() -> None:
    class FakeLLM:
        def __init__(self) -> None:
            self.kwargs = None

        def chat(self, messages, **kwargs):
            self.kwargs = kwargs

            class Response:
                content = "NEW_TASK"
                tool_calls = None
                reasoning_content = None

            return Response()

    llm = FakeLLM()
    service = ConversationRouter(llm=llm)  # type: ignore[arg-type]

    route = service.determine_route(
        user_text="重新检查一下",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.NEW_TASK
    assert llm.kwargs == {
        "tools": None,
        "purpose": LLMCallPurpose.CLASSIFIER,
    }


def test_continuity_judge_falls_back_to_react_when_fast_path_is_empty() -> None:
    class FakeLLM:
        def __init__(self) -> None:
            self.purposes: list[LLMCallPurpose] = []

        def chat(self, messages, **kwargs):
            purpose = kwargs["purpose"]
            self.purposes.append(purpose)

            class Response:
                tool_calls = None
                reasoning_content = None

                def __init__(self, content: str) -> None:
                    self.content = content

            if purpose is LLMCallPurpose.CLASSIFIER:
                return Response("")
            return Response("NEW_TASK")

    llm = FakeLLM()
    service = ConversationRouter(llm=llm)  # type: ignore[arg-type]

    route = service.determine_route(
        user_text="重新检查一下",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.NEW_TASK
    assert llm.purposes == [LLMCallPurpose.CLASSIFIER, LLMCallPurpose.REACT]


def test_continuity_judge_falls_back_when_llm_classifier_fails() -> None:
    class FailingLLM:
        def chat(self, messages, **kwargs):
            raise RuntimeError("router unavailable")

    service = ConversationRouter(llm=FailingLLM())  # type: ignore[arg-type]

    route = service.determine_route(
        user_text="解释一下",
        has_pending_review=True,
        requested_scope=None,
        current_patch_file="src/main/java/demo/UserService.java",
        current_scope=_scope(),
    )

    assert route is ConversationRoute.REVIEW_CONTINUE
