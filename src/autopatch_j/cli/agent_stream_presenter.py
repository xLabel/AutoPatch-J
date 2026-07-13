from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from autopatch_j.agent.react_runner import AgentRunResult
from autopatch_j.cli.render import CliRenderer
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.domain import IntentType
from autopatch_j.core.review import ReviewWorkspaceManager


@dataclass(slots=True)
class ReActDisplayPolicy:
    """
    ReAct 过程在 CLI 中的展示策略。

    debug 模式下展示完整思考链和工具输出；普通模式下折叠为状态提示，避免 CLI 噪音过大。
    """

    debug_mode: bool
    force_compact_observation: bool = False

    @property
    def compact_reasoning(self) -> bool:
        return not self.debug_mode

    @property
    def compact_observation(self) -> bool:
        return self.force_compact_observation or not self.debug_mode


@dataclass(frozen=True, slots=True)
class PresentedAgentResult:
    """Request result after CLI display filtering, with the original trace preserved."""

    raw_answer: str
    display_answer: str
    trace_messages: list[dict[str, Any]]


class _ReasoningRenderState:
    """单次 ReAct 调用中的 reasoning 展示状态。"""

    def __init__(self, renderer: CliRenderer, policy: ReActDisplayPolicy) -> None:
        self.renderer = renderer
        self.policy = policy
        self.in_reasoning = False
        self.visible = False

    def on_reasoning(self, token: str) -> None:
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
        if not self.visible:
            return
        self.renderer.finish_reasoning_status()
        self.visible = False
        self.in_reasoning = False


class _AgentStreamExecution:
    """
    单次 Agent 调用期间的流式渲染状态。

    该类只在 AgentStreamPresenter 内部使用，负责把 token、reasoning、
    tool start 和 observation 事件转成 renderer 调用。
    """

    def __init__(self, presenter: AgentStreamPresenter, policy: ReActDisplayPolicy) -> None:
        self.presenter = presenter
        self.renderer = presenter.renderer
        self.policy = policy
        self.reasoning = _ReasoningRenderState(self.renderer, policy)

        self.current_tool_name: str | None = None
        self.buffered_answer_parts: list[str] = []

    def on_token(self, token: str) -> None:
        self.finish_reasoning_status_if_visible()
        self.buffered_answer_parts.append(token)

    def on_reasoning(self, token: str) -> None:
        self.reasoning.on_reasoning(token)

    def on_tool_start(self, tool_name: str) -> None:
        self.finish_reasoning_status_if_visible()
        self.current_tool_name = tool_name
        self.buffered_answer_parts.clear()
        self.renderer.print_tool_start(tool_name, caller="LLM")

    def on_observation(self, message: str, summary: str | None = None) -> None:
        self.finish_reasoning_status_if_visible()
        if self.policy.compact_observation:
            fallback = summary if summary else f"已执行工具: {self.current_tool_name}"
            self.renderer.print_agent_text(fallback)
            return
        self.renderer.print_agent_text(message)

    def finish_reasoning_status_if_visible(self) -> None:
        self.reasoning.finish_if_visible()


class AgentStreamPresenter:
    """
    Agent 流式事件到 CLI 输出的展示器。

    职责边界：
    1. 把 LLM token、reasoning、Tool Call 和 observation 转换为终端展示。
    2. 根据 debug 模式决定展示完整过程还是折叠状态。
    3. 不参与 ReAct 决策、不执行工具，也不改变补丁队列；它只负责展示和最终回答过滤。
    """

    def __init__(
        self,
        renderer: CliRenderer,
        workspace_manager: ReviewWorkspaceManager | None,
        chat_filter: ChatFilter | None,
        agent: Any | None,
        describe_current_scope_paths: Callable[[], list[str]],
        build_static_scan_summary: Callable[[], str],
        build_local_no_issue_summary: Callable[[], str],
        debug_mode: Callable[[], bool],
    ) -> None:
        self.renderer = renderer
        self.workspace_manager = workspace_manager
        self.chat_filter = chat_filter
        self.agent = agent
        self._describe_current_scope_paths = describe_current_scope_paths
        self._build_static_scan_summary = build_static_scan_summary
        self._build_local_no_issue_summary = build_local_no_issue_summary
        self._debug_mode = debug_mode

    def run(
        self,
        prompt: str,
        agent_call: Callable[..., AgentRunResult],
        scope_paths: list[str] | None = None,
        render_no_issue_panel: bool = False,
        compact_observation: bool = False,
        answer_intent: IntentType | None = None,
        raw_user_text: str | None = None,
        show_chat_anchors: bool = False,
        plain_answer: bool = False,
        suppress_answer_output: bool = False,
    ) -> PresentedAgentResult:
        if self.agent is None or self.workspace_manager is None or self.chat_filter is None:
            self.renderer.print_error("系统未初始化，请先执行 /init")
            return PresentedAgentResult(raw_answer="", display_answer="", trace_messages=[])
        agent = self.agent
        workspace_manager = self.workspace_manager
        chat_filter = self.chat_filter

        policy = ReActDisplayPolicy(
            debug_mode=self._debug_mode(),
            force_compact_observation=compact_observation,
        )

        execution = _AgentStreamExecution(self, policy)
        if policy.debug_mode:
            self._render_memory_debug_context(
                agent=agent,
                answer_intent=answer_intent,
                user_text=raw_user_text or prompt,
            )

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
            if policy.debug_mode:
                self._render_llm_debug_context(agent)
            raise
        execution.finish_reasoning_status_if_visible()
        if policy.debug_mode:
            self._render_llm_debug_context(agent)

        if render_no_issue_panel:
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths or self._describe_current_scope_paths(),
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
            return PresentedAgentResult(
                raw_answer=run_result.final_answer,
                display_answer="",
                trace_messages=run_result.trace_messages,
            )

        if suppress_answer_output:
            return PresentedAgentResult(
                raw_answer=run_result.final_answer,
                display_answer="",
                trace_messages=run_result.trace_messages,
            )

        has_pending_patches = workspace_manager.load().has_pending_patch()
        answer_allowed_with_pending_patch = {
            IntentType.PATCH_EXPLAIN,
            IntentType.CODE_EXPLAIN,
            IntentType.GENERAL_CHAT,
        }
        if has_pending_patches and answer_intent not in answer_allowed_with_pending_patch:
            return PresentedAgentResult(
                raw_answer=run_result.final_answer,
                display_answer="",
                trace_messages=run_result.trace_messages,
            )

        answer = "".join(execution.buffered_answer_parts) or run_result.final_answer
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
            trace_messages=run_result.trace_messages,
        )

    def _render_memory_debug_context(self, agent: Any, answer_intent: IntentType | None, user_text: str) -> None:
        if answer_intent is None:
            return
        session = getattr(agent, "session", None)
        if session is None or not hasattr(session, "build_memory_debug_summary"):
            return
        summary = session.build_memory_debug_summary(answer_intent, user_text)
        if summary:
            self.renderer.print_agent_text(summary)

    def _render_llm_debug_context(self, agent: Any) -> None:
        llm = getattr(agent, "llm", None)
        diagnostics = getattr(llm, "diagnostics", None)
        if not diagnostics:
            return

        diagnostic = diagnostics[-1]
        purpose = getattr(diagnostic.purpose, "name", str(diagnostic.purpose)).lower()
        reasoning = getattr(diagnostic.reasoning, "name", str(diagnostic.reasoning)).lower()
        stream = "on" if diagnostic.stream else "off"
        detail = (
            f"LLM 调用诊断：purpose={purpose}, stream={stream}, "
            f"reasoning={reasoning}, status={diagnostic.status}"
        )
        if purpose in {"memory_extraction", "memory_consolidation"}:
            detail += (
                f", max_tokens={getattr(diagnostic, 'max_tokens', None)}, "
                f"timeout={getattr(diagnostic, 'timeout_seconds', None)}s"
            )
        if diagnostic.error:
            detail += f", error={diagnostic.error}"
        self.renderer.print_agent_text(detail)
