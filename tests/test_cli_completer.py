from __future__ import annotations

from prompt_toolkit.document import Document

from autopatch_j.cli.completer import AutoPatchCompleter


def _apply_completion(text: str, completion_text: str, start_position: int) -> str:
    cursor = len(text)
    return text[:cursor + start_position] + completion_text + text[cursor:]


def test_command_completion_from_root_slash() -> None:
    completer = AutoPatchCompleter(lambda _: [])
    completions = list(completer.get_completions(Document(text="/", cursor_position=1), None))

    init_completion = next(c for c in completions if c.display_text == "/init")
    assert _apply_completion("/", init_completion.text, init_completion.start_position) == "/init"


def test_command_completion_replaces_only_command_body() -> None:
    completer = AutoPatchCompleter(lambda _: [])
    completions = list(completer.get_completions(Document(text="/st", cursor_position=3), None))

    status_completion = next(c for c in completions if c.display_text == "/status")
    assert _apply_completion("/st", status_completion.text, status_completion.start_position) == "/status"
