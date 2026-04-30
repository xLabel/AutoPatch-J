from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from autopatch_j.cli.render import CliRenderer
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.models import IntentType
from autopatch_j.core.workspace_manager import WorkspaceManager


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


class _StreamExecution:
    """
    单次 Agent 调用期间的流式渲染状态。

    该类只在 StreamAdapter 内部使用，负责把 token、reasoning、tool start 和 observation 事件转成 renderer 调用。
    """

    def __init__(self, stream: StreamAdapter, policy: ReActDisplayPolicy) -> None:
        self.stream = stream
        self.renderer = stream.renderer
        self.policy = policy
        
        self.in_reasoning: bool = False
        self.answer_after_reasoning: bool = False
        self.reasoning_visible: bool = False
        self.current_tool_name: str | None = None
        self.buffered_answer_parts: list[str] = []

    def on_token(self, token: str) -> None:
        if self.in_reasoning:
            self.answer_after_reasoning = True
            self.in_reasoning = False
        self.finish_reasoning_status_if_visible()
        self.buffered_answer_parts.append(token)

    def on_reasoning(self, token: str) -> None:
        if self.policy.compact_reasoning:
            if not self.in_reasoning:
                self.in_reasoning = True
                self.reasoning_visible = True
                self.renderer.print_reasoning_status(0)
            return

        if not self.in_reasoning:
            self.renderer.print("[dim italic]-- 深度思考中 --[/]")
        self.in_reasoning = True
        self.reasoning_visible = True
        self.renderer.print_reasoning(token)

    def on_tool_start(self, tool_name: str) -> None:
        self.finish_reasoning_status_if_visible()
        self.current_tool_name = tool_name
        self.renderer.print_tool_start(tool_name, caller="LLM")

    def on_observation(self, message: str, summary: str | None = None) -> None:
        self.finish_reasoning_status_if_visible()
        if self.policy.compact_observation:
            fallback = summary if summary else f"已执行工具: {self.current_tool_name}"
            self.renderer.print_info(fallback)
            return
        self.renderer.print_observation(message)

    def finish_reasoning_status_if_visible(self) -> None:
        if self.reasoning_visible:
            self.renderer.finish_reasoning_status()
            self.reasoning_visible = False


class StreamAdapter:
    """
    Agent 流式事件到 CLI 输出的适配器。

    职责边界：
    1. 把 LLM token、reasoning、Tool Call 和 observation 转换为终端展示。
    2. 根据 debug 模式决定展示完整过程还是折叠状态。
    3. 不参与 ReAct 决策、不执行工具，也不改变补丁队列；它只负责展示和最终回答过滤。
    """

    def __init__(
        self,
        renderer: CliRenderer,
        workspace_manager: WorkspaceManager | None,
        chat_filter: ChatFilter | None,
        agent: Agent | None,
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
        agent_call: Callable[..., str],
        scope_paths: list[str] | None = None,
        render_no_issue_panel: bool = False,
        compact_observation: bool = False,
        answer_intent: IntentType | None = None,
        raw_user_text: str | None = None,
        show_chat_anchors: bool = False,
        plain_answer: bool = False,
        suppress_answer_output: bool = False,
    ) -> list[dict[str, Any]]:
        assert self.agent is not None
        assert self.workspace_manager is not None
        assert self.chat_filter is not None

        policy = ReActDisplayPolicy(
            debug_mode=self._debug_mode(),
            force_compact_observation=compact_observation,
        )
        
        execution = _StreamExecution(self, policy)
        start_index = len(self.agent.messages)

        final_answer = agent_call(
            prompt,
            on_token=execution.on_token,
            on_reasoning=execution.on_reasoning,
            on_observation=execution.on_observation,
            on_tool_start=execution.on_tool_start,
        )
        new_messages = list(self.agent.messages[start_index:])
        execution.finish_reasoning_status_if_visible()

        if render_no_issue_panel:
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths or self._describe_current_scope_paths(),
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
            return new_messages

        if suppress_answer_output:
            return new_messages

        has_pending_patches = self.workspace_manager.load_workspace().has_pending_patch()
        answer_allowed_with_pending_patch = {
            IntentType.PATCH_EXPLAIN,
            IntentType.CODE_EXPLAIN,
            IntentType.GENERAL_CHAT,
        }
        if has_pending_patches and answer_intent not in answer_allowed_with_pending_patch:
            return new_messages

        buffered_answer = "".join(execution.buffered_answer_parts)
        if buffered_answer:
            rendered_answer = self.chat_filter.build_display_answer(
                user_text=raw_user_text or "",
                answer=buffered_answer,
                intent=answer_intent,
            ) if answer_intent else buffered_answer
            if show_chat_anchors:
                self.renderer.print_assistant_anchor()
            if plain_answer:
                self.renderer.print_plain(rendered_answer, end="")
            else:
                self.renderer.print(rendered_answer, end="")
        else:
            sanitized_final_answer = final_answer or ""
            if sanitized_final_answer:
                rendered_answer = self.chat_filter.build_display_answer(
                    user_text=raw_user_text or "",
                    answer=sanitized_final_answer,
                    intent=answer_intent,
                ) if answer_intent else sanitized_final_answer
                if show_chat_anchors:
                    self.renderer.print_assistant_anchor()
                if plain_answer:
                    self.renderer.print_plain(rendered_answer)
                else:
                    self.renderer.print(rendered_answer)

        return new_messages
