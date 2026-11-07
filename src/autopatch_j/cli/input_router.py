"""确定性人工动作与统一 Agent 的终端适配，不做 LLM 意图或审核路由分类。"""

from autopatch_j import error_messages
from autopatch_j.agent import callbacks
from autopatch_j.agent import result
from autopatch_j.application import runtime as project_runtime
from autopatch_j.cli import render
from autopatch_j.core.repair import models


class UserInputRouter:
    """展示绑定的人工决定，其他自然语言交给统一项目 Agent。"""

    def __init__(
        self,
        runtime: project_runtime.ProjectRuntime,
        renderer: render.CliRenderer,
    ) -> None:
        self.runtime = runtime
        self.renderer = renderer

    def handle_input(self, text: str, displayed: models.RepairState) -> None:
        """人工动作绑定本轮已展示版本；自然语言不清空任务或扩大权限。"""
        action = text.strip().lower()
        if action == "apply":
            outcome = self.runtime.repair.apply(displayed)
            self.renderer.print_plain(outcome.message)
            for progress in outcome.progress:
                self.renderer.print_plain(
                    f"{progress.path}: {progress.status} {progress.message}"
                )
            for check in outcome.checks:
                self.renderer.print_plain(
                    f"{check.kind}: {check.status} — {check.message}"
                )
            return
        if action == "allow":
            state = self.runtime.repair.approve_scope(displayed)
            self.renderer.print_plain(
                "已批准写范围："
                + "、".join(state.write_paths)
                + "；继续准备当前修复提案，尚未修改源码。"
            )
        if action in {"discard", "abort"}:
            if action == "discard" and displayed.status != "pending":
                raise models.RepairError(
                    error_messages.INTERRUPTED_DISCARD
                    if displayed.status in {"applying", "interrupted"}
                    else error_messages.NO_REVIEW_PROPOSAL
                )
            self.runtime.repair.end(displayed)
            self.renderer.print_plain(
                "已结束当前任务；已经写入的源码不会回滚。"
            )
            return
        self.renderer.print_assistant_anchor()
        streamed = False

        def token(value: str) -> None:
            nonlocal streamed
            streamed = True
            self.renderer.print_plain(value, end="")

        def observation(message: str, summary: str | None) -> None:
            # 回调协议同时提供摘要，终端此处只展示完整观察。
            del summary
            self.renderer.print_plain(message)

        outcome = self.runtime.agent.run(
            text,
            output=callbacks.AgentCallbacks(
                on_token=token,
                on_tool_start=lambda name: self.renderer.print_step(
                    f"工具: {name}"
                ),
                on_observation=observation,
                on_reasoning=(
                    (lambda value: self.renderer.print_plain(value, end=""))
                    if self.runtime.settings.debug_mode
                    else None
                ),
            ),
        )
        self.renderer.print_blank()
        if not streamed or outcome.status != result.RunStatus.COMPLETED:
            self.renderer.print_plain(outcome.final_answer)
