from __future__ import annotations

from autopatch_j.cli.workflow_context import WorkflowServices
from autopatch_j.core.models import IntentType, PatchReviewItem


class PatchReviewWorkflow:
    """
    待确认补丁工作流。

    负责 pending patch 期间的 apply、discard、abort、patch_explain 和
    patch_revise。它不反向调用主路由；无法消费的普通文本由 UserInputRouter 再分类。
    """

    def __init__(self, services: WorkflowServices) -> None:
        self.services = services

    def handle_review_action(self, user_input: str, current_item: PatchReviewItem) -> bool:
        runtime = self.services.runtime
        current_draft = current_item.draft.fetch_patch_draft()
        normalized = user_input.lower()

        if normalized == "apply":
            self.services.command_handlers.handle_apply(current_draft)
            with runtime.workspace_manager.edit() as workspace:
                workspace.mark_applied()
                if not workspace.has_pending_patch():
                    self.services.renderer.print_agent_text("补丁队列已清空")
            return True

        if normalized == "discard":
            self.services.command_handlers.handle_discard()
            with runtime.workspace_manager.edit() as workspace:
                workspace.mark_discarded()
                if not workspace.has_pending_patch():
                    self.services.renderer.print_agent_text("补丁队列已清空")
            return True

        if normalized == "abort":
            runtime.workspace_manager.clear_workspace()
            runtime.agent.reset_history()
            self.services.renderer.print_agent_text("已中止审核流程，丢弃所有剩余补丁草案。")
            return True

        return False

    def handle_patch_explain(self, text: str) -> None:
        runtime = self.services.runtime
        current_item = runtime.workspace_manager.load_workspace().get_current_patch()
        if current_item is None:
            self.services.renderer.print_error("当前没有待确认补丁")
            return

        focus_paths = self.services.summary_provider.fetch_review_scope_paths(current_item)
        runtime.agent.session.set_focus_paths(focus_paths)
        self.services.agent_runner.run(
            prompt=text,
            agent_call=lambda p, **kwargs: runtime.agent.perform_patch_explain(
                raw_user_text=text,
                current_item=current_item,
                **kwargs,
            ),
            answer_intent=IntentType.PATCH_EXPLAIN,
            raw_user_text=text,
        )

    def handle_patch_revise(self, text: str) -> None:
        runtime = self.services.runtime
        current_item = runtime.workspace_manager.load_workspace().get_current_patch()
        if current_item is None:
            self.services.renderer.print_error("当前没有待确认补丁")
            return

        runtime.agent.session.set_focus_paths(self.services.summary_provider.fetch_review_scope_paths(current_item))
        runtime.agent.session.revised_patch_draft = None

        self.services.agent_runner.run(
            prompt=text,
            agent_call=lambda p, **kwargs: runtime.agent.perform_patch_revise(
                raw_user_text=text,
                current_item=current_item,
                **kwargs,
            ),
        )
        revised_patch = runtime.agent.session.pop_revised_patch_draft()
        if revised_patch is None:
            self.services.renderer.print_agent_text("未生成修订补丁，当前补丁保持不变。")
            return
        runtime.workspace_manager.replace_current_patch(revised_patch)
        self.services.renderer.print_agent_text("已更新当前补丁，后续补丁保持不变。")

