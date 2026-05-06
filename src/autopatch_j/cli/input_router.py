from __future__ import annotations

import re

from autopatch_j.cli.workflow_context import InputRouteDecision, WorkflowServices
from autopatch_j.cli.workflows.chat import ChatWorkflow
from autopatch_j.cli.workflows.code_audit import CodeAuditWorkflow
from autopatch_j.cli.workflows.patch_review import PatchReviewWorkflow
from autopatch_j.core.domain import ConversationRoute, IntentType, ReviewPatchItem


class UserInputRouter:
    """
    用户自然语言输入路由器。

    只负责 command/new task/review continue 的路由判断和 IntentType 分发；
    具体业务执行交给 chat/audit/patch review workflow。
    """

    def __init__(self, services: WorkflowServices) -> None:
        self.services = services
        self.code_audit_workflow = CodeAuditWorkflow(services)
        self.chat_workflow = ChatWorkflow(services)
        self.patch_review_workflow = PatchReviewWorkflow(services)

    def handle_review_input(self, user_input: str, current_item: ReviewPatchItem) -> None:
        if self.patch_review_workflow.handle_review_action(user_input, current_item):
            return
        self.handle_chat(user_input)

    def handle_chat(self, text: str) -> None:
        stripped_instruction = re.sub(r"@([^\s@]+)", "", text).strip()
        if "@" in text and not stripped_instruction:
            self.services.renderer.print_agent_text("请继续输入代码指令")
            return

        decision = self.classify_chat_input(text)
        if decision.route is ConversationRoute.COMMAND:
            self.services.command_router.handle_command(text)
            return
        if decision.intent is None:
            self.handle_general_chat(text)
            return
        self.dispatch_chat_intent(text, decision.intent)

    def classify_chat_input(self, text: str) -> InputRouteDecision:
        runtime = self.services.runtime
        workspace = runtime.workspace_manager.load()
        has_pending_review = workspace.has_pending_patch()
        requested_scope = runtime.scope_service.resolve(text, default_to_project=False)
        current_item = workspace.current_patch() if has_pending_review else None
        route = runtime.conversation_router.classify_route(
            user_text=text,
            has_pending_review=has_pending_review,
            requested_scope=requested_scope,
            current_patch_file=current_item.file_path if current_item else None,
            current_scope=workspace.scope if has_pending_review else None,
        )

        if route is ConversationRoute.COMMAND:
            return InputRouteDecision(route=route, intent=None)

        if route is ConversationRoute.NEW_TASK:
            self.switch_to_new_task_if_needed(has_pending_review)
            intent = runtime.intent_detector.classify(text, has_pending_review=False)
        else:
            intent = runtime.intent_detector.classify(text, has_pending_review=True)
            if intent is IntentType.CODE_EXPLAIN and has_pending_review and "@" not in text:
                intent = IntentType.PATCH_EXPLAIN
        return InputRouteDecision(route=route, intent=intent)

    def switch_to_new_task_if_needed(self, has_pending_review: bool) -> None:
        runtime = self.services.runtime
        runtime.agent.reset_history()
        if has_pending_review:
            runtime.workspace_manager.clear()
            self.services.renderer.print_agent_text("已切换到新任务")

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
        self.code_audit_workflow.handle_code_audit(text)

    def handle_code_explain(self, text: str) -> None:
        self.chat_workflow.handle_code_explain(text)

    def handle_general_chat(self, text: str) -> None:
        self.chat_workflow.handle_general_chat(text)

    def handle_patch_explain(self, text: str) -> None:
        self.patch_review_workflow.handle_patch_explain(text)

    def handle_patch_revise(self, text: str) -> None:
        self.patch_review_workflow.handle_patch_revise(text)
