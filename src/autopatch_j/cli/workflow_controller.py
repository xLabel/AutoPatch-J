from __future__ import annotations

import re

from autopatch_j.cli.audit_workflow import CliAuditWorkflow
from autopatch_j.cli.patch_review_workflow import CliPatchReviewWorkflow
from autopatch_j.cli.workflow_types import ChatInputDecision, WorkflowControllerContext
from autopatch_j.config import GlobalConfig
from autopatch_j.core.models import CodeScopeKind, ConversationRoute, IntentType, PatchReviewItem


class CliWorkflowController:
    """
    用户输入到业务工作流的入口路由器。

    职责边界：
    1. 负责 chat 输入的会话路由、意图识别分发和普通问答/代码讲解入口。
    2. 把代码审计交给 CliAuditWorkflow，把待确认补丁交给 CliPatchReviewWorkflow。
    3. 不直接执行工具、不直接推进 finding backlog，也不直接修改用户源文件。
    """

    def __init__(self, context: WorkflowControllerContext) -> None:
        self.context = context
        self.audit_workflow = CliAuditWorkflow(context)
        self.patch_review_workflow = CliPatchReviewWorkflow(context, route_chat=self.handle_chat)

    def handle_review_input(self, user_input: str, current_item: PatchReviewItem) -> None:
        self.patch_review_workflow.handle_review_input(user_input, current_item)

    def handle_chat(self, text: str) -> None:
        if not all(
            [
                self.context.agent,
                self.context.intent_detector,
                self.context.conversation_router,
                self.context.scope_service,
                self.context.scanner_runner,
                self.context.workspace_manager,
            ]
        ):
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return

        stripped_instruction = re.sub(r"@([^\s@]+)", "", text).strip()
        if "@" in text and not stripped_instruction:
            self.context.renderer.print_agent_text("请继续输入代码指令")
            return

        decision = self.classify_chat_input(text)
        if decision.route is ConversationRoute.COMMAND:
            self.context.command_controller.handle_command(text)
            return
        if decision.intent is None:
            self.handle_general_chat(text)
            return
        self.dispatch_chat_intent(text, decision.intent)

    def classify_chat_input(self, text: str) -> ChatInputDecision:
        workspace = self.context.workspace_manager.load_workspace()
        has_pending_review = workspace.has_pending_patch()
        requested_scope = self.context.scope_service.fetch_scope(text, default_to_project=False)
        current_item = workspace.get_current_patch() if has_pending_review else None
        route = self.context.conversation_router.determine_route(
            user_text=text,
            has_pending_review=has_pending_review,
            requested_scope=requested_scope,
            current_patch_file=current_item.file_path if current_item else None,
            current_scope=workspace.scope if has_pending_review else None,
        )

        if route is ConversationRoute.COMMAND:
            return ChatInputDecision(route=route, intent=None)

        if route is ConversationRoute.NEW_TASK:
            self.switch_to_new_task_if_needed(has_pending_review)
            intent = self.context.intent_detector.detect_intent(text, has_pending_review=False)
        else:
            intent = self.context.intent_detector.detect_intent(text, has_pending_review=True)
            if intent is IntentType.CODE_EXPLAIN and has_pending_review and "@" not in text:
                intent = IntentType.PATCH_EXPLAIN
        return ChatInputDecision(route=route, intent=intent)

    def switch_to_new_task_if_needed(self, has_pending_review: bool) -> None:
        self.context.agent.reset_history()
        if has_pending_review:
            self.context.workspace_manager.clear_workspace()
            self.context.renderer.print_agent_text("已切换到新任务")

    def dispatch_chat_intent(self, text: str, intent: IntentType) -> None:
        if intent is IntentType.CODE_AUDIT:
            self.handle_code_audit(text)
            return
        if intent is IntentType.CODE_EXPLAIN:
            self.handle_code_explain(text)
            return
        if intent is IntentType.PATCH_EXPLAIN:
            self.handle_patch_explain(text)
            return
        if intent is IntentType.PATCH_REVISE:
            self.handle_patch_revise(text)
            return
        self.handle_general_chat(text)

    def handle_code_audit(self, text: str) -> None:
        self.audit_workflow.handle_code_audit(text)

    def handle_code_explain(self, text: str) -> None:
        if self.context.scope_service is None or self.context.agent is None or self.context.chat_filter is None:
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return

        scope = self.context.scope_service.fetch_scope(text, default_to_project=True)
        compact_observation = not GlobalConfig.debug_mode

        if scope is not None:
            if not scope.focus_files:
                self.context.renderer.print_agent_text("当前项目缺少可解释的 Java 源码范围。")
                return
            focus_paths = scope.focus_files if scope.is_locked else []
            self.context.agent.session.set_focus_paths(focus_paths)
            allow_symbol_search = scope.kind is not CodeScopeKind.SINGLE_FILE
            self.context.agent.session.code_explain_allow_symbol_search = allow_symbol_search
            project_context = (
                self.context._build_project_explain_context(scope)
                if scope.kind is CodeScopeKind.PROJECT
                else None
            )
            self.context._run_agent_request(
                prompt=text,
                agent_call=lambda p, **kwargs: self.context.agent.perform_code_explain(
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

        self.context.agent.session.set_focus_paths([])
        self.context.agent.session.code_explain_allow_symbol_search = True
        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_general_chat(
                raw_user_text=text,
                **kwargs,
            ),
            compact_observation=compact_observation,
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            plain_answer=True,
        )

    def handle_general_chat(self, text: str) -> None:
        if self.context.agent is None or self.context.chat_filter is None:
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return

        self.context.agent.session.set_focus_paths([])
        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_general_chat(
                raw_user_text=text,
                **kwargs,
            ),
            compact_observation=not GlobalConfig.debug_mode,
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            plain_answer=True,
        )

    def handle_patch_explain(self, text: str) -> None:
        self.patch_review_workflow.handle_patch_explain(text)

    def handle_patch_revise(self, text: str) -> None:
        self.patch_review_workflow.handle_patch_revise(text)
