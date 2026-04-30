from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from autopatch_j.cli.completer import AutoPatchCompleter
from autopatch_j.config import get_project_state_dir


class CliInputController:
    """CLI 输入层适配器，负责 prompt_toolkit 会话、历史记录和补全交互。"""

    def __init__(
        self,
        index_search: Callable[[str], list[Any]],
        repo_root: Path | None,
    ) -> None:
        self._index_search = index_search
        self._repo_root = repo_root

    def set_repo_root(self, repo_root: Path | None) -> None:
        self._repo_root = repo_root

    def create_prompt_session(self) -> PromptSession[str]:
        key_bindings = KeyBindings()

        @key_bindings.add("enter")
        def _(event: Any) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state:
                changed = self.accept_completion(buffer)
                if changed:
                    return
            buffer.validate_and_handle()

        @key_bindings.add("tab")
        def _(event: Any) -> None:
            buffer = event.app.current_buffer
            self.accept_completion(buffer)

        custom_style = Style.from_dict(
            {
                "completion-menu.completion": "bg:#333333 #ffffff",
                "completion-menu.completion.current": "bg:#007acc #ffffff bold",
                "completion-menu.meta.completion": "bg:#222222 #888888",
                "completion-menu.meta.completion.current": "bg:#007acc #ffffff",
            }
        )

        history = None
        if self._repo_root:
            history = FileHistory(str(get_project_state_dir(self._repo_root) / "history.txt"))

        session = PromptSession(
            completer=AutoPatchCompleter(self._index_search),
            key_bindings=key_bindings,
            style=custom_style,
            complete_while_typing=True,
            history=history,
        )

        def auto_select_first(buffer: Any) -> None:
            self.select_first_completion(buffer)

        session.default_buffer.on_completions_changed += auto_select_first
        return session

    def pick_active_completion(self, buffer: Any) -> Any:
        state = getattr(buffer, "complete_state", None)
        if not state:
            return None
        completions = getattr(state, "completions", None) or []
        index = getattr(state, "complete_index", None)
        if isinstance(index, int) and 0 <= index < len(completions):
            return completions[index]
        return completions[0] if completions else None

    def accept_completion(self, buffer: Any) -> bool:
        append_space = self.should_append_space_after_completion(buffer)
        completion = self.pick_active_completion(buffer)
        if completion is None:
            buffer.start_completion(select_first=False)
            append_space = self.should_append_space_after_completion(buffer)
            completion = self.pick_active_completion(buffer)
        if completion is None:
            return False

        before_text = getattr(buffer, "text", None)
        before_cursor = getattr(buffer.document, "cursor_position", None)
        buffer.apply_completion(completion)
        changed = (
            getattr(buffer, "text", None) != before_text
            or getattr(buffer.document, "cursor_position", None) != before_cursor
        )
        current_char = getattr(buffer.document, "current_char", "")
        if append_space and (current_char is None or not str(current_char).isspace()):
            buffer.insert_text(" ")
            changed = True
        return changed

    def select_first_completion(self, buffer: Any) -> bool:
        state = getattr(buffer, "complete_state", None)
        if not state:
            return False
        completions = getattr(state, "completions", None) or []
        index = getattr(state, "complete_index", None)
        if not completions or (isinstance(index, int) and 0 <= index < len(completions)):
            return False
        state.go_to_index(0)
        get_app().invalidate()
        return True

    def should_append_space_after_completion(self, buffer: Any) -> bool:
        state = getattr(buffer, "complete_state", None)
        if not state:
            return False
        original_document = getattr(state, "original_document", None)
        if not original_document:
            return False
        return bool(re.search(r"(^|\s)@[\w\.]*$", original_document.text_before_cursor))
