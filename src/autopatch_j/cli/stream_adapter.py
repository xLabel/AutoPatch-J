from __future__ import annotations

from typing import Any, Callable

from autopatch_j.cli.render import CliRenderer
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.models import IntentType
from autopatch_j.core.workspace_manager import WorkspaceManager


class _StreamExecution:
    def __init__(self, stream: StreamAdapter, compact_observation: bool) -> None:
        self.stream = stream
        self.renderer = stream.renderer
        self.compact_observation = compact_observation
        
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
        if not self.in_reasoning:
            self.renderer.print("\n[dim italic]-- 深度思考中 --[/]")
        self.in_reasoning = True
        self.reasoning_visible = True
        self.renderer.print_reasoning(token)

    def on_tool_start(self, tool_name: str) -> None:
        self.finish_reasoning_status_if_visible()
        self.current_tool_name = tool_name
        self.renderer.print_tool_start(tool_name, caller="LLM")

    def on_observation(self, message: str) -> None:
        self.finish_reasoning_status_if_visible()
        if self.compact_observation:
            self.renderer.print_info(
                self.stream._summarize_observation(self.current_tool_name, message)
            )
            return
        self.renderer.print_observation(message)

    def finish_reasoning_status_if_visible(self) -> None:
        if self.reasoning_visible:
            self.renderer.finish_reasoning_status()
            self.reasoning_visible = False


class StreamAdapter:
    """
    流式事件渲染适配器 (Stream-to-CLI Adapter)。
    核心职责：桥接大模型的底层吐字流与 Rich 终端 UI。
    将 LLM 的流式文本 (Tokens)、长思考链 (Reasoning) 以及工具调用流 (Tool Calls)，
    平滑且美观地转化为终端上的高语义化反馈（如：暗色滚动的思考过程、精简折叠的工具输出总结），有效缓解用户等待焦虑。
    """

    def __init__(
        self,
        renderer: CliRenderer,
        workspace_manager: WorkspaceManager | None,
        chat_filter: ChatFilter | None,
        agent: Agent | None,
        summarize_observation: Callable[[str | None, str], str],
        describe_current_scope_paths: Callable[[], list[str]],
        build_static_scan_summary: Callable[[], str],
        build_local_no_issue_summary: Callable[[], str],
    ) -> None:
        self.renderer = renderer
        self.workspace_manager = workspace_manager
        self.chat_filter = chat_filter
        self.agent = agent
        self._summarize_observation = summarize_observation
        self._describe_current_scope_paths = describe_current_scope_paths
        self._build_static_scan_summary = build_static_scan_summary
        self._build_local_no_issue_summary = build_local_no_issue_summary

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

        self.renderer.print()
        
        execution = _StreamExecution(self, compact_observation)
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

        has_pending_patches = self.workspace_manager.load_workspace().has_pending_patch()
        if has_pending_patches:
            self.renderer.print()
            return new_messages

        if render_no_issue_panel:
            if execution.buffered_answer_parts or final_answer:
                self.renderer.print("\n")
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths or self._describe_current_scope_paths(),
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
            self.renderer.print()
            return new_messages

        if suppress_answer_output:
            self.renderer.print()
            return new_messages

        buffered_answer = "".join(execution.buffered_answer_parts)
        if buffered_answer:
            rendered_answer = self.chat_filter.build_display_answer(
                user_text=raw_user_text or "",
                answer=buffered_answer,
                intent=answer_intent,
            ) if answer_intent else buffered_answer
            if execution.answer_after_reasoning:
                self.renderer.print("\n\n")
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
                    self.renderer.print_plain(f"\n{rendered_answer}")
                else:
                    self.renderer.print(f"\n{rendered_answer}")

        self.renderer.print()
        return new_messages