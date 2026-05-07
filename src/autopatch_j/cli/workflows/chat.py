from __future__ import annotations

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
                self.services.renderer.print_agent_text("当前项目缺少可解释的 Java 源码范围。")
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
            self.services.agent_runner.run(
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
            )
            return

        runtime.agent.session.set_focus_paths([])
        runtime.agent.session.code_explain_allow_symbol_search = True
        self.services.agent_runner.run(
            prompt=text,
            agent_call=lambda p, **kwargs: runtime.agent.perform_general_chat(
                raw_user_text=text,
                **kwargs,
            ),
            compact_observation=compact_observation,
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            plain_answer=True,
        )

    def handle_general_chat(self, text: str) -> None:
        runtime = self.services.runtime
        runtime.agent.session.set_focus_paths([])
        self.services.agent_runner.run(
            prompt=text,
            agent_call=lambda p, **kwargs: runtime.agent.perform_general_chat(
                raw_user_text=text,
                **kwargs,
            ),
            compact_observation=not self.services.debug_mode(),
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            plain_answer=True,
        )
