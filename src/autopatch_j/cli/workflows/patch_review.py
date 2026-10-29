"""人工决定、当前补丁解释与修订的交互协调。"""

from __future__ import annotations

import functools

from autopatch_j.agent import result
from autopatch_j.cli import workflow_dependencies
from autopatch_j.core.domain import intent
from autopatch_j.core.domain import workspace


class PatchReviewWorkflow:
    """待确认补丁工作流。

    负责 pending patch 期间的 apply、discard、abort、patch_explain 和
    patch_revise。它不反向调用主路由；无法消费的普通文本由 UserInputRouter 再分类。
    """

    def __init__(
        self, services: workflow_dependencies.WorkflowDependencies
    ) -> None:
        self.services = services

    def handle_review_action(
        self, user_input: str, current_item: workspace.ReviewPatchItem
    ) -> bool:
        """消费 apply/discard/abort；返回是否已经处理该输入。"""
        runtime = self.services.runtime
        current_draft = current_item.draft.to_patch_draft()
        normalized = user_input.lower()

        if normalized == "apply":
            apply_result = self.services.command_handlers.handle_apply(
                current_draft
            )
            if not apply_result.applied:
                return True
            runtime.agent.session.clear_runtime_patch_constraints(
                current_item.item_id
            )
            return True

        if normalized == "discard":
            self.services.command_handlers.handle_discard()
            if runtime.review_service.discard():
                self.services.renderer.print_agent_text("补丁队列已清空")
            runtime.agent.session.clear_runtime_patch_constraints(
                current_item.item_id
            )
            return True

        if normalized == "abort":
            runtime.review_service.abort()
            runtime.agent.reset_history()
            self.services.renderer.print_agent_text(
                "已中止审核流程，丢弃所有剩余补丁草案。"
            )
            return True

        return False

    def handle_patch_explain(self, text: str) -> None:
        """围绕当前审核项准备范围与只读说明。"""
        runtime = self.services.runtime
        current_item = runtime.workspace_manager.load().current_patch()
        if current_item is None:
            self.services.renderer.print_error("当前没有待确认补丁")
            return

        focus_paths = self.services.summary_provider.fetch_review_scope_paths(
            current_item
        )
        runtime.agent.session.set_focus_paths(focus_paths)
        self.services.agent_runner.run(
            prompt=text,
            agent_call=functools.partial(
                runtime.agent.perform_patch_explain, current_item=current_item
            ),
            answer_intent=intent.IntentType.PATCH_EXPLAIN,
            raw_user_text=text,
        )

    def handle_patch_revise(self, text: str) -> None:
        """仅替换当前绑定通过的修订，其他队列项保持不变。"""
        runtime = self.services.runtime
        current_item = runtime.workspace_manager.load().current_patch()
        if current_item is None:
            self.services.renderer.print_error("当前没有待确认补丁")
            return
        if current_item.draft.error_code == "STALE_DRAFT":
            self.services.renderer.print_error(
                "当前补丁绑定已失效，不能继续修订；请 discard，或 abort 后重新扫描。"
            )
            return

        runtime.agent.session.set_focus_paths([current_item.file_path])
        runtime.agent.session.revised_patch_draft = None

        def run_revision(
            current_item: workspace.ReviewPatchItem = current_item,
        ) -> str:
            """依据实际结束状态消费暂存修订，绑定通过才替换持久草案。

            失败或没有有效修订时保留原草案；返回已经向用户展示的摘要。
            """
            response = self.services.agent_runner.run(
                prompt=text,
                agent_call=functools.partial(
                    runtime.agent.perform_patch_revise,
                    current_item=current_item,
                ),
                suppress_answer_output=True,
            )
            if response.status not in {
                result.RunStatus.COMPLETED,
                result.RunStatus.LIMITED,
            }:
                runtime.agent.session.revised_patch_draft = None
                summary = "修订未完成，当前补丁保持不变。"
                self.services.renderer.print_error(summary)
                return summary
            revised_patch = runtime.agent.session.pop_revised_patch_draft()
            if revised_patch is None:
                summary = "未生成修订补丁，当前补丁保持不变。"
                self.services.renderer.print_agent_text(summary)
                return summary
            if not runtime.workspace_manager.replace_current_patch(
                revised_patch
            ):
                summary = "修订补丁未应用：当前 finding binding 校验失败，原补丁保持不变。"
                self.services.renderer.print_error(summary)
                return summary
            summary = "已更新当前补丁，后续补丁保持不变。"
            self.services.renderer.print_agent_text(summary)
            return summary

        run_revision()
