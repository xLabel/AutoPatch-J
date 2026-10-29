"""将 SDK 流式回调转换为终端展示，保留业务观察及结束状态。"""

from __future__ import annotations

import dataclasses
from typing import Callable

from autopatch_j.agent import result
from autopatch_j.cli import render
from autopatch_j.core import chat_filter as core_chat_filter
from autopatch_j.core.domain import intent
from autopatch_j.core.review import workspace
from autopatch_j.tools import contract


@dataclasses.dataclass(slots=True)
class ReActDisplayPolicy:
    """ReAct 过程在 CLI 中的展示策略。

    debug 模式下展示完整思考链和工具输出；普通模式下折叠为状态提示，避免 CLI 噪音过大。
    """

    debug_mode: bool
    force_compact_observation: bool = False

    @property
    def compact_reasoning(self) -> bool:
        """是否将推理事件折叠为状态提示。"""
        return not self.debug_mode

    @property
    def compact_observation(self) -> bool:
        """是否将工具观察折叠为摘要。"""
        return self.force_compact_observation or not self.debug_mode


@dataclasses.dataclass(frozen=True, slots=True)
class PresentedAgentResult:
    """展示过滤后的回答、业务观察和实际结束状态。"""

    raw_answer: str
    display_answer: str
    observations: tuple[contract.ToolExecutionResult, ...]
    status: result.RunStatus = result.RunStatus.COMPLETED


class _ReasoningRenderState:
    """单次 ReAct 调用中的 reasoning 展示状态。"""

    def __init__(
        self, renderer: render.CliRenderer, policy: ReActDisplayPolicy
    ) -> None:
        self.renderer = renderer
        self.policy = policy
        self.in_reasoning = False
        self.visible = False

    def on_reasoning(self, token: str) -> None:
        """按展示策略输出推理或一次折叠提示，不改变运行状态。"""
        if self.policy.compact_reasoning:
            if not self.in_reasoning:
                self.in_reasoning = True
                self.visible = True
                self.renderer.print_reasoning_status(0)
            return

        if not self.in_reasoning:
            self.renderer.print_reasoning_text("-- 深度思考中 --\n")
        self.in_reasoning = True
        self.visible = True
        self.renderer.print_reasoning_text(token)

    def finish_if_visible(self) -> None:
        """仅在推理提示已经显示时补充换行并复位。"""
        if not self.visible:
            return
        self.renderer.finish_reasoning_status()
        self.visible = False
        self.in_reasoning = False


class _AgentStreamExecution:
    """单次 Agent 调用期间的流式渲染状态。

    该类只在 AgentStreamPresenter 内部使用，负责把 token、reasoning、
    tool start 和 observation 事件转成 renderer 调用。
    """

    def __init__(
        self, presenter: AgentStreamPresenter, policy: ReActDisplayPolicy
    ) -> None:
        self.presenter = presenter
        self.renderer = presenter.renderer
        self.policy = policy
        self.reasoning = _ReasoningRenderState(self.renderer, policy)

        self.current_tool_name: str | None = None
        self.buffered_answer_parts: list[str] = []

    def on_token(self, token: str) -> None:
        """缓存答案事件，待完整结束后再决定是否展示。"""
        self.finish_reasoning_status_if_visible()
        self.buffered_answer_parts.append(token)

    def on_reasoning(self, token: str) -> None:
        """按展示策略输出推理或一次折叠提示，不改变运行状态。"""
        self.reasoning.on_reasoning(token)

    def on_tool_start(self, tool_name: str) -> None:
        """收束推理提示后展示工具开始，不代表工具成功。"""
        self.finish_reasoning_status_if_visible()
        self.current_tool_name = tool_name
        self.buffered_answer_parts.clear()
        self.renderer.print_tool_start(tool_name, caller="LLM")

    def on_observation(self, message: str, summary: str | None = None) -> None:
        """按折叠策略显示业务观察或其摘要。"""
        self.finish_reasoning_status_if_visible()
        if self.policy.compact_observation:
            fallback = (
                summary if summary else f"已执行工具: {self.current_tool_name}"
            )
            self.renderer.print_agent_text(fallback)
            return
        self.renderer.print_agent_text(message)

    def finish_reasoning_status_if_visible(self) -> None:
        """收束本轮已经显示的推理提示。"""
        self.reasoning.finish_if_visible()


class AgentStreamPresenter:
    """Agent 流式事件到 CLI 输出的展示器。

    职责边界：
    1. 把 LLM token、reasoning、Tool Call 和 observation 转换为终端展示。
    2. 根据 debug 模式决定展示完整过程还是折叠状态。
    3. 不参与 ReAct 决策、不执行工具，也不改变补丁队列；它只负责展示和最终回答过滤。
    """

    def __init__(
        self,
        renderer: render.CliRenderer,
        workspace_manager: workspace.ReviewWorkspaceManager | None,
        chat_filter: core_chat_filter.ChatFilter | None,
        describe_current_scope_paths: Callable[[], list[str]],
        build_static_scan_summary: Callable[[], str],
        build_local_no_issue_summary: Callable[[], str],
        debug_mode: Callable[[], bool],
    ) -> None:
        self.renderer = renderer
        self.workspace_manager = workspace_manager
        self.chat_filter = chat_filter
        self._describe_current_scope_paths = describe_current_scope_paths
        self._build_static_scan_summary = build_static_scan_summary
        self._build_local_no_issue_summary = build_local_no_issue_summary
        self._debug_mode = debug_mode

    def run(
        self,
        prompt: str,
        agent_call: Callable[..., result.AgentRunResult],
        scope_paths: list[str] | None = None,
        render_no_issue_panel: bool = False,
        compact_observation: bool = False,
        answer_intent: intent.IntentType | None = None,
        raw_user_text: str | None = None,
        show_chat_anchors: bool = False,
        plain_answer: bool = False,
        suppress_answer_output: bool = False,
    ) -> PresentedAgentResult:
        """展示过程与结束状态，答案隐藏不隐藏失败、取消或受限提示。

        Args:
            prompt: 本轮用户输入。
            agent_call: 接受输入和流式回调的任务入口。
            scope_paths: 结果面板使用的范围，省略时从工作台获取。
            render_no_issue_panel: 仅正常完成时显示未发现问题面板。
            compact_observation: 强制折叠工具观察。
            answer_intent: 决定答案过滤和待审期间的展示许可。
            raw_user_text: 传给答案过滤器的用户原文。
            show_chat_anchors: 显示问答标记。
            plain_answer: 使用纯文本渲染答案。
            suppress_answer_output: 隐藏最终答案，保留过程和非完成状态。

        Returns:
            实际结束状态、原始答案、展示答案及业务观察，不提交草案。
        """
        if self.workspace_manager is None or self.chat_filter is None:
            self.renderer.print_error("系统未初始化，请先执行 /init")
            return PresentedAgentResult(
                raw_answer="", display_answer="", observations=()
            )
        workspace_manager = self.workspace_manager
        chat_filter = self.chat_filter

        policy = ReActDisplayPolicy(
            debug_mode=self._debug_mode(),
            force_compact_observation=compact_observation,
        )

        execution = _AgentStreamExecution(self, policy)
        try:
            run_result = agent_call(
                prompt,
                on_token=execution.on_token,
                on_reasoning=execution.on_reasoning,
                on_observation=execution.on_observation,
                on_tool_start=execution.on_tool_start,
            )
        except Exception:
            execution.finish_reasoning_status_if_visible()
            self.renderer.print_error(
                "本轮未完成；已显示的过程不代表工具结果或补丁已获确认。"
            )
            raise
        execution.finish_reasoning_status_if_visible()

        if run_result.status is not result.RunStatus.COMPLETED:
            self.renderer.print_error(run_result.final_answer)

        if (
            render_no_issue_panel
            and run_result.status is result.RunStatus.COMPLETED
        ):
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths or self._describe_current_scope_paths(),
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
            return PresentedAgentResult(
                raw_answer=run_result.final_answer,
                display_answer="",
                observations=run_result.observations,
                status=run_result.status,
            )

        if suppress_answer_output:
            return PresentedAgentResult(
                raw_answer=run_result.final_answer,
                display_answer="",
                observations=run_result.observations,
                status=run_result.status,
            )

        has_pending_patches = workspace_manager.load().has_pending_patch()
        answer_allowed_with_pending_patch = {
            intent.IntentType.PATCH_EXPLAIN,
            intent.IntentType.CODE_EXPLAIN,
            intent.IntentType.GENERAL_CHAT,
        }
        if (
            has_pending_patches
            and answer_intent not in answer_allowed_with_pending_patch
        ):
            return PresentedAgentResult(
                raw_answer=run_result.final_answer,
                display_answer="",
                observations=run_result.observations,
                status=run_result.status,
            )

        answer = run_result.final_answer
        if run_result.status is result.RunStatus.COMPLETED:
            answer = "".join(execution.buffered_answer_parts) or answer
        else:
            answer = ""
        rendered_answer = (
            chat_filter.build_display_answer(
                user_text=raw_user_text or "",
                answer=answer,
                intent=answer_intent,
            )
            if answer_intent
            else answer
        )
        if rendered_answer:
            if show_chat_anchors:
                self.renderer.print_assistant_anchor()
            if plain_answer:
                self.renderer.print_plain(rendered_answer)
            else:
                self.renderer.print_agent_text(rendered_answer)

        return PresentedAgentResult(
            raw_answer=run_result.final_answer,
            display_answer=rendered_answer,
            observations=run_result.observations,
            status=run_result.status,
        )
