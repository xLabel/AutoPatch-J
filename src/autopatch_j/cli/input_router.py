"""审核连续性与意图分类后的 workflow 路由。"""

from __future__ import annotations

import re

from autopatch_j.cli import workflow_dependencies
from autopatch_j.cli.workflows import chat
from autopatch_j.cli.workflows import code_audit
from autopatch_j.cli.workflows import patch_review
from autopatch_j.core.domain import intent as domain_intent
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.core.user_input import diagnostics


class UserInputRouter:
    """用户自然语言输入路由器。

    只负责 command/new task/review continue 的路由判断和 IntentType 分发；
    具体业务执行交给 chat/audit/patch review workflow。
    """

    def __init__(
        self, services: workflow_dependencies.WorkflowDependencies
    ) -> None:
        self.services = services
        self.code_audit_workflow = code_audit.CodeAuditWorkflow(services)
        self.chat_workflow = chat.ChatWorkflow(services)
        self.patch_review_workflow = patch_review.PatchReviewWorkflow(services)

    def handle_review_input(
        self, user_input: str, current_item: domain_workspace.ReviewPatchItem
    ) -> None:
        """优先消费人工决定，再对剩余文本分类。"""
        if self.patch_review_workflow.handle_review_action(
            user_input, current_item
        ):
            return
        self.handle_chat(user_input)

    def handle_chat(self, text: str) -> None:
        """处理普通自然语言输入，必要时切换或延续当前审核。"""
        stripped_instruction = re.sub(r"@([^\s@]+)", "", text).strip()
        if "@" in text and not stripped_instruction:
            self.services.renderer.print_agent_text("请继续输入代码指令")
            return

        decision = self.classify_chat_input(text)
        if decision.route is domain_intent.ConversationRoute.COMMAND:
            self.services.command_router.handle_command(text)
            return
        if decision.intent is None:
            self.handle_general_chat(text)
            return
        self.dispatch_chat_intent(text, decision.intent)

    def classify_chat_input(
        self, text: str
    ) -> workflow_dependencies.InputRoutingDecision:
        """结合审核状态做短分类，并拒绝不合法的 patch-only 意图。"""
        runtime = self.services.runtime
        workspace = runtime.workspace_manager.load()
        has_pending_review = workspace.has_pending_patch()
        requested_scope = runtime.scope_service.resolve(
            text, default_to_project=False
        )
        current_item = workspace.current_patch() if has_pending_review else None
        route_result = (
            runtime.conversation_router.classify_route_with_diagnostics(
                user_text=text,
                has_pending_review=has_pending_review,
                requested_scope=requested_scope,
                current_patch_file=(
                    current_item.file_path if current_item else None
                ),
                current_scope=workspace.scope if has_pending_review else None,
            )
        )
        self._render_route_diagnostic(route_result)
        route = route_result.route

        if route is domain_intent.ConversationRoute.COMMAND:
            return workflow_dependencies.InputRoutingDecision(
                route=route, intent=None
            )

        if route is domain_intent.ConversationRoute.NEW_TASK:
            self.switch_to_new_task_if_needed(has_pending_review)
            intent_result = runtime.intent_detector.classify_with_diagnostics(
                text, has_pending_review=False
            )
        else:
            intent_result = runtime.intent_detector.classify_with_diagnostics(
                text, has_pending_review=True
            )
        intent = intent_result.intent
        self._render_intent_diagnostic(intent_result)
        if (
            route is not domain_intent.ConversationRoute.NEW_TASK
            and intent is domain_intent.IntentType.CODE_EXPLAIN
            and has_pending_review
            and "@" not in text
        ):
            intent = domain_intent.IntentType.PATCH_EXPLAIN
        return workflow_dependencies.InputRoutingDecision(
            route=route, intent=intent
        )

    def _render_route_diagnostic(
        self, result: diagnostics.RouteClassificationResult
    ) -> None:
        if not self.services.debug_mode() or not result.used_fallback:
            return
        self.services.renderer.print_agent_text(
            f"路由诊断：route 使用 fallback {result.route.value}"
            f"，原因：{result.fallback_reason}"
        )

    def _render_intent_diagnostic(
        self, result: diagnostics.IntentClassificationResult
    ) -> None:
        if not self.services.debug_mode() or not result.used_fallback:
            return
        self.services.renderer.print_agent_text(
            f"路由诊断：intent 使用 fallback "
            f"{result.intent.value}"
            f"，原因：{result.fallback_reason}"
        )

    def switch_to_new_task_if_needed(self, has_pending_review: bool) -> None:
        """转入新任务前清理旧审核工作台与局部约束，不清空普通会话。"""
        runtime = self.services.runtime
        runtime.agent.reset_history()
        if has_pending_review:
            runtime.workspace_manager.clear()
            self.services.renderer.print_agent_text("已切换到新任务")

    def dispatch_chat_intent(
        self, text: str, intent: domain_intent.IntentType
    ) -> None:
        """将合法意图分派给对应业务 workflow。"""
        if intent is domain_intent.IntentType.CODE_AUDIT:
            self.handle_code_audit(text)
            return
        if intent is domain_intent.IntentType.CODE_EXPLAIN:
            self.handle_code_explain(text)
            return
        if intent is domain_intent.IntentType.PATCH_EXPLAIN:
            self.handle_patch_explain(text)
            return
        if intent is domain_intent.IntentType.PATCH_REVISE:
            self.handle_patch_revise(text)
            return
        self.handle_general_chat(text)

    def handle_code_audit(self, text: str) -> None:
        """解析范围并执行扫描与草案流程，扫描失败不能冒充零 finding。"""
        self.code_audit_workflow.handle_code_audit(text)

    def handle_code_explain(self, text: str) -> None:
        """解析讲解范围并选择只读工具，项目范围仅注入轻量上下文。"""
        self.chat_workflow.handle_code_explain(text)

    def handle_general_chat(self, text: str) -> None:
        """清除源码焦点并延续当前普通会话。"""
        self.chat_workflow.handle_general_chat(text)

    def handle_patch_explain(self, text: str) -> None:
        """围绕当前审核项准备范围与只读说明。"""
        self.patch_review_workflow.handle_patch_explain(text)

    def handle_patch_revise(self, text: str) -> None:
        """仅替换当前绑定通过的修订，其他队列项保持不变。"""
        self.patch_review_workflow.handle_patch_revise(text)
