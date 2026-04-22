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
            new_text = self.text[:cursor + completion.start_position] + completion.text + self.text[cursor:]
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
            new_text = self.text[:cursor + completion.start_position] + completion.text + self.text[cursor:]
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
            new_text = self.text[:cursor + completion.start_position] + completion.text + self.text[cursor:]
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


def test_local_no_issue_summary_triggered_only_for_zero_scan_without_patch() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    cli.agent = SimpleNamespace(focus_paths=["src/main/java/demo/LegacyConfig.java"])
    cli.artifacts = None

    zero_scan_messages = [
        {
            "role": "tool",
            "name": "scan_project",
            "content": "扫描完成 [ID: scan-1]，共发现 0 个问题。\n\n✔ 恭喜，未发现任何安全或正确性问题。",
        }
    ]
    assert cli._should_render_local_no_issue_summary(zero_scan_messages) is True
    assert cli._describe_current_scope_paths() == ["src/main/java/demo/LegacyConfig.java"]
    assert cli._build_static_scan_summary() == "当前范围未发现安全或正确性问题。"
    assert cli._build_local_no_issue_summary() == "模型复核未发现需要修复的问题。"

    patched_messages = zero_scan_messages + [
        {"role": "tool", "name": "propose_patch", "content": "补丁提案已成功生成并加入队列。"}
    ]
    assert cli._should_render_local_no_issue_summary(patched_messages) is False


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
        )
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


def test_build_patch_feedback_prompt_replans_followups_before_current() -> None:
    cli = AutoPatchCLI.__new__(AutoPatchCLI)
    discarded = [
        SimpleNamespace(file_path="src/main/java/demo/User.java"),
        SimpleNamespace(file_path="src/main/java/demo/AppConfig.java"),
    ]

    prompt = cli._build_patch_feedback_prompt(
        pending_file="src/main/java/demo/UserService.java",
        user_feedback="加一句注释",
        discarded_followups=discarded,
    )

    assert "加一句注释" in prompt
    assert "- src/main/java/demo/User.java" in prompt
    assert "- src/main/java/demo/AppConfig.java" in prompt
    assert "请先为上述后续文件重新调用 propose_patch" in prompt
    assert "当前文件的补丁必须最后提交" in prompt
    assert "src/main/java/demo/UserService.java" in prompt
