from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document

from autopatch_j.cli import app as cli_app_module
from autopatch_j.cli.app import AutoPatchCLI


def test_select_first_completion_highlights_without_modifying_text(monkeypatch) -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    invalidated = {"called": False}

    monkeypatch.setattr(
        cli_app_module,
        "get_app",
        lambda: SimpleNamespace(invalidate=lambda: invalidated.__setitem__("called", True)),
    )

    state = CompletionState(
        original_document=Document(text="/i", cursor_position=2),
        completions=[Completion("init", start_position=-1)],
        complete_index=None,
    )
    buffer = SimpleNamespace(
        complete_state=state,
        text="/i",
        document=Document(text="/i", cursor_position=2),
    )

    changed = cli._select_first_completion(buffer)

    assert changed is True
    assert state.current_completion is not None
    assert state.current_completion.text == "init"
    assert buffer.text == "/i"
    assert buffer.document.text == "/i"
    assert invalidated["called"] is True


def test_pick_active_completion_tolerates_stale_index() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    state = CompletionState(
        original_document=Document(text="/i", cursor_position=2),
        completions=[Completion("init", start_position=-1)],
        complete_index=3,
    )
    buffer = SimpleNamespace(complete_state=state)

    completion = cli._pick_active_completion(buffer)

    assert completion is not None
    assert completion.text == "init"


def test_accept_completion_appends_space_for_mentions() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    state = CompletionState(
        original_document=Document(text="@Leg", cursor_position=4),
        completions=[Completion("LegacyConfig.java", start_position=-3)],
        complete_index=0,
    )

    class DummyBuffer:
        def __init__(self) -> None:
            self.complete_state = state
            self.text = "@Leg"
            self.document = Document(text=self.text, cursor_position=len(self.text))

        def start_completion(self, select_first: bool = False) -> None:
            return None

        def apply_completion(self, completion: Completion) -> None:
            cursor = self.document.cursor_position
            new_text = self.text[: cursor + completion.start_position] + completion.text + self.text[cursor:]
            self.text = new_text
            self.document = Document(text=new_text, cursor_position=len(new_text))

        def insert_text(self, value: str) -> None:
            cursor = self.document.cursor_position
            new_text = self.text[:cursor] + value + self.text[cursor:]
            self.text = new_text
            self.document = Document(text=new_text, cursor_position=cursor + len(value))

    buffer = DummyBuffer()

    accepted = cli._accept_completion(buffer)

    assert accepted is True
    assert buffer.text == "@LegacyConfig.java "


def test_accept_completion_does_not_append_space_for_commands() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    state = CompletionState(
        original_document=Document(text="/i", cursor_position=2),
        completions=[Completion("init", start_position=-1)],
        complete_index=0,
    )

    class DummyBuffer:
        def __init__(self) -> None:
            self.complete_state = state
            self.text = "/i"
            self.document = Document(text=self.text, cursor_position=len(self.text))

        def start_completion(self, select_first: bool = False) -> None:
            return None

        def apply_completion(self, completion: Completion) -> None:
            cursor = self.document.cursor_position
            new_text = self.text[: cursor + completion.start_position] + completion.text + self.text[cursor:]
            self.text = new_text
            self.document = Document(text=new_text, cursor_position=len(new_text))

        def insert_text(self, value: str) -> None:
            cursor = self.document.cursor_position
            new_text = self.text[:cursor] + value + self.text[cursor:]
            self.text = new_text
            self.document = Document(text=new_text, cursor_position=cursor + len(value))

    buffer = DummyBuffer()

    accepted = cli._accept_completion(buffer)

    assert accepted is True
    assert buffer.text == "/init"


def test_accept_completion_returns_false_for_already_complete_command() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    state = CompletionState(
        original_document=Document(text="/help", cursor_position=5),
        completions=[Completion("help", start_position=-4)],
        complete_index=0,
    )

    class DummyBuffer:
        def __init__(self) -> None:
            self.complete_state = state
            self.text = "/help"
            self.document = Document(text=self.text, cursor_position=len(self.text))

        def start_completion(self, select_first: bool = False) -> None:
            return None

        def apply_completion(self, completion: Completion) -> None:
            cursor = self.document.cursor_position
            new_text = self.text[: cursor + completion.start_position] + completion.text + self.text[cursor:]
            self.text = new_text
            self.document = Document(text=new_text, cursor_position=len(new_text))

        def insert_text(self, value: str) -> None:
            cursor = self.document.cursor_position
            new_text = self.text[:cursor] + value + self.text[cursor:]
            self.text = new_text
            self.document = Document(text=new_text, cursor_position=cursor + len(value))

    buffer = DummyBuffer()

    accepted = cli._accept_completion(buffer)

    assert accepted is False
    assert buffer.text == "/help"


def test_collect_latest_scan_paths_expands_directory_targets() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    cli.repo_root = Path("examples/demo-repo").resolve()
    cli.agent = SimpleNamespace(focus_paths=[])

    from autopatch_j.scanners.base import ScanResult

    fake_findings_dir = SimpleNamespace(glob=lambda pattern: [SimpleNamespace(stem="scan-1")])
    cli.artifacts = SimpleNamespace(
        findings_dir=fake_findings_dir,
        fetch_scan_result=lambda artifact_id: ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo"],
            targets=["src/main/java/demo"],
            status="ok",
            message="",
            findings=[],
        ),
    )

    assert cli._describe_current_scope_paths() == [
        "src/main/java/demo/AppConfig.java",
        "src/main/java/demo/LegacyConfig.java",
        "src/main/java/demo/User.java",
        "src/main/java/demo/UserService.java",
    ]


def test_sanitize_assistant_output_truncates_dsml_payload() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)

    text = (
        "现在需要获取 UserService.java 的完整代码。使用 read_source_code。\n\n"
        "<｜DSML｜function_calls>\n"
        "<｜DSML｜invoke name=\"read_source_code\">...</｜DSML｜invoke>\n"
        "</｜DSML｜function_calls>"
    )

    assert cli._sanitize_assistant_output(text) == "现在需要获取 UserService.java 的完整代码。使用 read_source_code。"
