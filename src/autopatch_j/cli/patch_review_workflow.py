from __future__ import annotations

from autopatch_j.cli.workflow_types import WorkflowControllerContext
from autopatch_j.core.models import IntentType, PatchReviewItem


class CliPatchReviewWorkflow:
    """
    待确认补丁工作流编排器。

    职责边界：
    1. 处理 pending patch 期间的 apply、discard、abort 和普通反馈。
    2. 处理 patch_explain 与 patch_revise，并保证重写只替换当前补丁。
    3. 不负责新任务路由、扫描和 finding backlog 推进。
    """

    def __init__(self, context: WorkflowControllerContext, route_chat) -> None:
        self.context = context
        self._route_chat = route_chat

    def handle_review_input(self, user_input: str, current_item: PatchReviewItem) -> None:
        current_draft = current_item.draft.fetch_patch_draft()
        assert self.context.workspace_manager is not None

        if user_input.lower() == "apply":
            self.context.command_controller.handle_apply(current_draft)
            with self.context.workspace_manager.edit() as workspace:
                workspace.mark_applied()
                if not workspace.has_pending_patch():
                    self.context.renderer.print_agent_text("补丁队列已清空")
            return

        if user_input.lower() == "discard":
            self.context.command_controller.handle_discard()
            with self.context.workspace_manager.edit() as workspace:
                workspace.mark_discarded()
                if not workspace.has_pending_patch():
                    self.context.renderer.print_agent_text("补丁队列已清空")
            return

        if user_input.lower() == "abort":
            self.context.workspace_manager.clear_workspace()
            self.context.agent.reset_history()
            self.context.renderer.print_agent_text("已中止审核流程，丢弃所有剩余补丁草案。")
            return

        self._route_chat(user_input)

    def handle_patch_explain(self, text: str) -> None:
        assert self.context.workspace_manager is not None
        assert self.context.agent is not None

        current_item = self.context.workspace_manager.load_workspace().get_current_patch()
        if current_item is None:
            self.context.renderer.print_error("当前没有待确认补丁")
            return

        focus_paths = self.context._fetch_review_scope_paths(current_item)
        self.context.agent.session.set_focus_paths(focus_paths)
        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_patch_explain(
                raw_user_text=text,
                current_item=current_item,
                **kwargs,
            ),
            answer_intent=IntentType.PATCH_EXPLAIN,
            raw_user_text=text,
        )

    def handle_patch_revise(self, text: str) -> None:
        assert self.context.workspace_manager is not None
        assert self.context.agent is not None

        current_item = self.context.workspace_manager.load_workspace().get_current_patch()
        if current_item is None:
            self.context.renderer.print_error("当前没有待确认补丁")
            return

        self.context.agent.session.set_focus_paths(self.context._fetch_review_scope_paths(current_item))
        self.context.agent.session.revised_patch_draft = None

        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_patch_revise(
                raw_user_text=text,
                current_item=current_item,
                **kwargs,
            ),
        )
        revised_patch = self.context.agent.session.pop_revised_patch_draft()
        if revised_patch is None:
            self.context.renderer.print_agent_text("未生成修订补丁，当前补丁保持不变。")
            return
        self.context.workspace_manager.replace_current_patch(revised_patch)
        self.context.renderer.print_agent_text("已更新当前补丁，后续补丁保持不变。")
