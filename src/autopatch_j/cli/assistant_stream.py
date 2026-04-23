from __future__ import annotations

from typing import Any, Callable

from autopatch_j.cli.render import CliRenderer
from autopatch_j.core.chat_service import ChatService
from autopatch_j.core.models import IntentType
from autopatch_j.core.workflow_service import WorkflowService


class AssistantStream:
    """Run one agent request and adapt its streamed events to CLI rendering."""

    def __init__(
        self,
        *,
        renderer: CliRenderer,
        workflow_service: WorkflowService | None,
        chat_service: ChatService | None,
        agent: Any,
        sanitize_output: Callable[[str], str],
        prepare_display_answer: Callable[[str, IntentType | None, str | None], str],
        summarize_observation: Callable[[str | None, str], str],
        describe_current_scope_paths: Callable[[], list[str]],
        build_static_scan_summary: Callable[[], str],
        build_local_no_issue_summary: Callable[[], str],
    ) -> None:
        self.renderer = renderer
        self.workflow_service = workflow_service
        self.chat_service = chat_service
        self.agent = agent
        self._sanitize_output = sanitize_output
        self._prepare_display_answer = prepare_display_answer
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
    ) -> list[dict[str, Any]]:
        assert self.agent is not None
        assert self.workflow_service is not None
        assert self.chat_service is not None

        self.renderer.print()
        stream_state = {"in_reasoning": False, "answer_after_reasoning": False}
        buffered_answer_parts: list[str] = []
        start_index = len(self.agent.messages)
        current_tool_name: str | None = None

        def on_token(token: str) -> None:
            if stream_state["in_reasoning"]:
                stream_state["answer_after_reasoning"] = True
                stream_state["in_reasoning"] = False
            buffered_answer_parts.append(token)

        def on_reasoning(token: str) -> None:
            stream_state["in_reasoning"] = True
            self.renderer.print_reasoning(token, end="")

        def on_tool_start(tool_name: str) -> None:
            nonlocal current_tool_name
            current_tool_name = tool_name
            self.renderer.print_tool_start(tool_name, caller="LLM")

        def on_observation(message: str) -> None:
            if compact_observation:
                self.renderer.print_info(self._summarize_observation(current_tool_name, message))
                return
            self.renderer.print_observation(message)

        final_answer = agent_call(
            prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )
        new_messages = list(self.agent.messages[start_index:])

        has_pending_patches = self.workflow_service.verify_has_pending_patch()
        if has_pending_patches:
            self.renderer.print()
            return new_messages

        if render_no_issue_panel:
            if buffered_answer_parts or final_answer:
                self.renderer.print("\n")
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths or self._describe_current_scope_paths(),
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
            self.renderer.print()
            return new_messages

        buffered_answer = self._sanitize_output("".join(buffered_answer_parts))
        if buffered_answer:
            rendered_answer = self._prepare_display_answer(
                answer=buffered_answer,
                answer_intent=answer_intent,
                raw_user_text=raw_user_text,
            )
            if stream_state["answer_after_reasoning"]:
                self.renderer.print("\n\n")
            if show_chat_anchors:
                self.renderer.print_assistant_anchor()
            if plain_answer:
                self.renderer.print_plain(rendered_answer, end="")
            else:
                self.renderer.print(rendered_answer, end="")
        else:
            sanitized_final_answer = self._sanitize_output(final_answer or "")
            if sanitized_final_answer:
                rendered_answer = self._prepare_display_answer(
                    answer=sanitized_final_answer,
                    answer_intent=answer_intent,
                    raw_user_text=raw_user_text,
                )
                if show_chat_anchors:
                    self.renderer.print_assistant_anchor()
                if plain_answer:
                    self.renderer.print_plain(f"\n{rendered_answer}")
                else:
                    self.renderer.print(f"\n{rendered_answer}")

        self.renderer.print()
        return new_messages
