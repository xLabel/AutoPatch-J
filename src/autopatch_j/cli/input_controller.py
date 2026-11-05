"""prompt-toolkit 输入会话、按键绑定及补全接受策略。"""

from __future__ import annotations

import pathlib
import re
from typing import Callable

from prompt_toolkit.application import current
from prompt_toolkit import buffer as prompt_buffer
from prompt_toolkit import completion as prompt_completion
from prompt_toolkit import history as prompt_toolkit_history
from prompt_toolkit import key_binding
from prompt_toolkit.key_binding import key_processor
import prompt_toolkit
from prompt_toolkit import styles

from autopatch_j.cli import completer
from autopatch_j import config
from autopatch_j.core.project import symbols


class CliInputController:
    """CLI 输入层适配器，负责 prompt_toolkit 会话、历史记录和补全交互。"""

    def __init__(
        self,
        index_search: Callable[[str], list[symbols.SymbolIndexEntry]],
        repo_root: pathlib.Path | None,
    ) -> None:
        self._index_search = index_search
        self._repo_root = repo_root

    def set_repo_root(self, repo_root: pathlib.Path | None) -> None:
        """更新下次创建输入会话时使用的项目历史目录。"""
        self._repo_root = repo_root

    def create_prompt_session(self) -> prompt_toolkit.PromptSession[str]:
        """创建带项目历史和补全按键的会话；环境不可用时由调用方处理异常。"""
        key_bindings = key_binding.KeyBindings()

        @key_bindings.add("enter")
        def _(event: key_processor.KeyPressEvent) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state:
                changed = self.accept_completion(buffer)
                if changed:
                    return
            buffer.validate_and_handle()

        @key_bindings.add("tab")
        def _(event: key_processor.KeyPressEvent) -> None:
            buffer = event.app.current_buffer
            self.accept_completion(buffer)

        custom_style = styles.Style.from_dict(
            {
                "completion-menu.completion": "bg:#333333 #ffffff",
                "completion-menu.completion.current": "bg:#007acc #ffffff bold",
                "completion-menu.meta.completion": "bg:#222222 #888888",
                "completion-menu.meta.completion.current": "bg:#007acc #ffffff",
            }
        )

        history = None
        if self._repo_root:
            history = prompt_toolkit_history.FileHistory(
                str(
                    config.get_project_state_dir(self._repo_root)
                    / "history.txt"
                )
            )

        session = prompt_toolkit.PromptSession(
            completer=completer.AutoPatchCompleter(self._index_search),
            key_bindings=key_bindings,
            style=custom_style,
            complete_while_typing=True,
            history=history,
        )

        def auto_select_first(buffer: prompt_buffer.Buffer) -> None:
            self.select_first_completion(buffer)

        session.default_buffer.on_completions_changed += auto_select_first
        return session

    def pick_active_completion(
        self, buffer: prompt_buffer.Buffer
    ) -> prompt_completion.Completion | None:
        """返回当前选中候选，尚未选择时返回首项，没有候选则为 None。"""
        state = getattr(buffer, "complete_state", None)
        if not state:
            return None
        completions = getattr(state, "completions", None) or []
        index = getattr(state, "complete_index", None)
        if isinstance(index, int) and 0 <= index < len(completions):
            return completions[index]
        return completions[0] if completions else None

    def accept_completion(self, buffer: prompt_buffer.Buffer) -> bool:
        """应用候选并按 @路径语义补空格，返回输入或游标是否改变。"""
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
            or getattr(buffer.document, "cursor_position", None)
            != before_cursor
        )
        current_char = getattr(buffer.document, "current_char", "")
        if append_space and (
            current_char is None or not str(current_char).isspace()
        ):
            buffer.insert_text(" ")
            changed = True
        return changed

    def select_first_completion(self, buffer: prompt_buffer.Buffer) -> bool:
        """在尚未选中候选时选择第一项并刷新界面。"""
        state = getattr(buffer, "complete_state", None)
        if not state:
            return False
        completions = getattr(state, "completions", None) or []
        index = getattr(state, "complete_index", None)
        if not completions or (
            isinstance(index, int) and 0 <= index < len(completions)
        ):
            return False
        state.go_to_index(0)
        current.get_app().invalidate()
        return True

    def should_append_space_after_completion(
        self, buffer: prompt_buffer.Buffer
    ) -> bool:
        """补全前输入是否为 @路径，用于判断接受候选后是否补空格。"""
        state = getattr(buffer, "complete_state", None)
        if not state:
            return False
        original_document = getattr(state, "original_document", None)
        if not original_document:
            return False
        return bool(
            re.search(r"(^|\s)@[^\s@]*$", original_document.text_before_cursor)
        )
