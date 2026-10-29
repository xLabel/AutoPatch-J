"""以固定输入游标和索引候选核对补全替换范围。"""

from prompt_toolkit import completion as prompt_toolkit_completion
from prompt_toolkit import document

from autopatch_j.cli import completer as cli_completer
from autopatch_j.core.project import symbols


def _apply_completion(
    text: str, completion_text: str, start_position: int
) -> str:
    cursor = len(text)
    return text[: cursor + start_position] + completion_text + text[cursor:]


def test_command_completion_from_root_slash() -> None:
    completer = cli_completer.AutoPatchCompleter(lambda unused_: [])
    completions = list(
        completer.get_completions(
            document.Document(text="/", cursor_position=1),
            prompt_toolkit_completion.CompleteEvent(),
        )
    )

    init_completion = next(c for c in completions if c.display_text == "/init")
    assert (
        _apply_completion(
            "/", init_completion.text, init_completion.start_position
        )
        == "/init"
    )


def test_command_completion_replaces_only_command_body() -> None:
    completer = cli_completer.AutoPatchCompleter(lambda unused_: [])
    completions = list(
        completer.get_completions(
            document.Document(text="/st", cursor_position=3),
            prompt_toolkit_completion.CompleteEvent(),
        )
    )

    status_completion = next(
        c for c in completions if c.display_text == "/status"
    )
    assert (
        _apply_completion(
            "/st", status_completion.text, status_completion.start_position
        )
        == "/status"
    )


def test_memory_subcommand_completion_from_trailing_space() -> None:
    completer = cli_completer.AutoPatchCompleter(lambda unused_: [])
    completions = list(
        completer.get_completions(
            document.Document(text="/memory ", cursor_position=8),
            prompt_toolkit_completion.CompleteEvent(),
        )
    )

    assert {completion.text for completion in completions} == {
        "status",
        "list",
        "show",
        "forget",
        "clear",
        "export",
    }


def test_memory_subcommand_completion_replaces_partial_argument() -> None:
    completer = cli_completer.AutoPatchCompleter(lambda unused_: [])
    completions = list(
        completer.get_completions(
            document.Document(text="/memory fo", cursor_position=10),
            prompt_toolkit_completion.CompleteEvent(),
        )
    )

    forget = next(
        completion for completion in completions if completion.text == "forget"
    )
    assert (
        _apply_completion("/memory fo", forget.text, forget.start_position)
        == "/memory forget"
    )


def test_memory_completion_stops_after_subcommand() -> None:
    completer = cli_completer.AutoPatchCompleter(lambda unused_: [])

    completions = list(
        completer.get_completions(
            document.Document(text="/memory show item-1", cursor_position=19),
            prompt_toolkit_completion.CompleteEvent(),
        )
    )

    assert not completions


def test_command_completion_excludes_removed_doctor() -> None:
    completer = cli_completer.AutoPatchCompleter(lambda unused_: [])
    completions = list(
        completer.get_completions(
            document.Document(text="/do", cursor_position=3),
            prompt_toolkit_completion.CompleteEvent(),
        )
    )

    assert all(
        completion.display_text != "/doctor" for completion in completions
    )


def test_mention_completion_only_exposes_files_and_directories() -> None:
    completer = cli_completer.AutoPatchCompleter(
        lambda unused_: [
            symbols.SymbolIndexEntry(
                path="src/main/java/demo", name="demo", kind="dir"
            ),
            symbols.SymbolIndexEntry(
                path="src/main/java/demo/UserService.java",
                name="UserService.java",
                kind="file",
            ),
            symbols.SymbolIndexEntry(
                path="src/main/java/demo/UserService.java",
                name="UserService",
                kind="class",
                line=3,
            ),
            symbols.SymbolIndexEntry(
                path="src/main/java/demo/UserService.java",
                name="isAdmin",
                kind="method",
                line=4,
            ),
        ]
    )

    completions = list(
        completer.get_completions(
            document.Document(text="@User", cursor_position=5),
            prompt_toolkit_completion.CompleteEvent(),
        )
    )

    assert [completion.display_text for completion in completions] == [
        "demo",
        "UserService.java",
    ]
