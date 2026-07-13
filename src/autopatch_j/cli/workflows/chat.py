from __future__ import annotations

from collections.abc import Callable

from autopatch_j.cli.agent_stream_presenter import PresentedAgentResult
from autopatch_j.cli.workflow_dependencies import WorkflowDependencies
from autopatch_j.core.domain import CodeScopeKind, IntentType


class ChatWorkflow:
    """
    普通问答与代码讲解工作流。

    负责 code_explain/general_chat 的 scope 解析、Agent session 焦点设置、
    项目轻量上下文注入和最终回答展示参数。
    """

    def __init__(self, services: WorkflowDependencies) -> None:
        self.services = services

    def handle_code_explain(self, text: str) -> None:
        runtime = self.services.runtime
        scope = runtime.scope_service.resolve(text, default_to_project=True)
        compact_observation = not self.services.debug_mode()

        if scope is not None:
            if not scope.focus_files:
                answer = "当前项目缺少可解释的 Java 源码范围。"
                self._run_ordinary_turn(
                    text=text,
                    intent=IntentType.CODE_EXPLAIN,
                    scope_paths=[],
                    run=lambda: self._present_local_answer(answer),
                )
                return
            focus_paths = scope.focus_files if scope.is_locked else []
            runtime.agent.session.set_focus_paths(focus_paths)
            allow_symbol_search = scope.kind is not CodeScopeKind.SINGLE_FILE
            runtime.agent.session.code_explain_allow_symbol_search = allow_symbol_search
            project_context = (
                self.services.summary_provider.build_project_explain_context(scope)
                if scope.kind is CodeScopeKind.PROJECT
                else None
            )
            self._run_ordinary_turn(
                text=text,
                intent=IntentType.CODE_EXPLAIN,
                scope_paths=scope.focus_files,
                run=lambda: self.services.agent_runner.run(
                    prompt=text,
                    agent_call=lambda p, **kwargs: runtime.agent.perform_code_explain(
                        raw_user_text=text,
                        scope=scope,
                        project_context=project_context,
                        allow_symbol_search=allow_symbol_search,
                        **kwargs,
                    ),
                    compact_observation=compact_observation,
                    answer_intent=IntentType.CODE_EXPLAIN,
                    raw_user_text=text,
                    plain_answer=True,
                ),
            )
            return

        runtime.agent.session.set_focus_paths([])
        runtime.agent.session.code_explain_allow_symbol_search = True
        self._run_ordinary_turn(
            text=text,
            intent=IntentType.GENERAL_CHAT,
            scope_paths=[],
            run=lambda: self.services.agent_runner.run(
                prompt=text,
                agent_call=lambda p, **kwargs: runtime.agent.perform_general_chat(
                    raw_user_text=text,
                    **kwargs,
                ),
                compact_observation=compact_observation,
                answer_intent=IntentType.GENERAL_CHAT,
                raw_user_text=text,
                plain_answer=True,
            ),
        )

    def handle_general_chat(self, text: str) -> None:
        runtime = self.services.runtime
        runtime.agent.session.set_focus_paths([])
        self._run_ordinary_turn(
            text=text,
            intent=IntentType.GENERAL_CHAT,
            scope_paths=[],
            run=lambda: self.services.agent_runner.run(
                prompt=text,
                agent_call=lambda p, **kwargs: runtime.agent.perform_general_chat(
                    raw_user_text=text,
                    **kwargs,
                ),
                compact_observation=not self.services.debug_mode(),
                answer_intent=IntentType.GENERAL_CHAT,
                raw_user_text=text,
                plain_answer=True,
            ),
        )

    def _run_ordinary_turn(
        self,
        *,
        text: str,
        intent: IntentType,
        scope_paths: list[str],
        run: Callable[[], PresentedAgentResult],
    ) -> PresentedAgentResult:
        manager = self.services.runtime.memory_manager
        turn = manager.begin_turn(
            intent=intent,
            user_text=text,
            scope_paths=scope_paths,
        )
        session = self.services.runtime.agent.session
        session.bind_memory_thread(turn.thread_id)
        try:
            try:
                result = run()
            except BaseException as exc:
                error = f"{type(exc).__name__}: {exc}".strip()
                try:
                    manager.fail_turn(turn.id, error=error)
                except Exception as memory_exc:
                    raise RuntimeError(
                        f"普通对话失败，且 Memory turn 状态更新失败：{memory_exc}"
                    ) from exc
                raise
            manager.complete_turn(turn.id, assistant_text=result.display_answer)
            return result
        finally:
            session.clear_memory_thread()

    def _present_local_answer(self, answer: str) -> PresentedAgentResult:
        self.services.renderer.print_agent_text(answer)
        return PresentedAgentResult(
            raw_answer=answer,
            display_answer=answer,
            trace_messages=[],
        )
