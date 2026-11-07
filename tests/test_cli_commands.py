"""从实际 CLI 入口验证人工动作、任务保留和资源收尾。"""

import asyncio
from collections.abc import Callable
from collections.abc import Iterator
import dataclasses
import pathlib
import sqlite3
from unittest import mock

from prompt_toolkit import history as prompt_history
from prompt_toolkit import input as prompt_input
from prompt_toolkit import output as prompt_output
from prompt_toolkit.application import current as prompt_current
from pydantic_ai import exceptions as sdk_errors
from pydantic_ai import messages
import pytest

from autopatch_j import config as autopatch_j_config
from autopatch_j.cli import app
from autopatch_j.cli import commands
from autopatch_j.cli import render
from autopatch_j.core import conversations
from autopatch_j.core.project import java_ast
from autopatch_j.core.project import java_symbols
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service
from autopatch_j.llm import factory
from autopatch_j.llm import runtime as model_runtime
from autopatch_j.scanners.semgrep import runtime as scanner_runtime
from tests import cli_helpers
from tests import sdk_fakes
from tests import test_patch_verification
from tests import test_repair


def test_saved_apply_failure_is_visible_after_cli_reload(cli, tmp_path):
    cli.renderer = render.CliRenderer()
    cli.welcome_presenter.renderer = cli.renderer
    repair, pending_state = test_repair.pending(tmp_path)
    (tmp_path / "A.java").write_text("class A { int external; }")
    refused = repair.apply(pending_state)

    def before_input(command, state, output):
        assert command is None
        assert state.last_apply_error == refused.message
        assert refused.message in output

    cli_helpers.drive_terminal(cli, [], before_input)


def test_applied_display_marks_historical_evidence_after_source_change(
    cli, tmp_path
):
    cli.renderer = render.CliRenderer()
    cli.welcome_presenter.renderer = cli.renderer
    repair, pending_state = test_repair.pending(tmp_path)
    repair.apply(pending_state)
    (tmp_path / "A.java").write_text("class A { int external; }")

    def before_input(command, state, output):
        assert command is None and state.status == "applied"
        assert "当时保存的验证结果" in output
        assert "旧结论不能作为当前通过" in output

    cli_helpers.drive_terminal(cli, [], before_input)


@pytest.fixture(name="cli")
def make_cli(
    tmp_path: pathlib.Path,
    fake_llm_factory: Callable[
        [autopatch_j_config.AppConfig], model_runtime.ModelRuntime
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[app.AutoPatchCli]:
    (tmp_path / "pom.xml").write_text("<project/>")
    monkeypatch.setattr(factory, "build_model_runtime", fake_llm_factory)
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    result = app.AutoPatchCli(tmp_path, autopatch_j_config.AppConfig())
    result.renderer = mock.MagicMock()
    result.command_router.renderer = result.renderer
    assert result.runtime is not None
    assert result.input_router is not None
    result.input_router.renderer = result.renderer
    yield result
    result.clear_runtime()


def pending(cli: app.AutoPatchCli, root: pathlib.Path) -> models.RepairState:
    (root / "A.java").write_text("class A { int n = 1; }")
    assert cli.runtime is not None
    repair = cli.runtime.repair
    initial = repair.store.load()
    current = repair.commit(initial, repair.start(initial, "fix", ("A.java",)))
    current = repair.approve_scope(current)
    edit = models.FileEdit(
        path="A.java",
        replacements=(models.Replacement(old_text="n = 1", new_text="n = 2"),),
    )
    return repair.commit(current, repair.prepare(current, (edit,), "fix"))


def test_init_keeps_pending_task(cli, tmp_path, monkeypatch):
    current = pending(cli, tmp_path)
    ready = scanner_runtime.RuntimeStatus("ready", "1.160.0", "1.160.0")
    monkeypatch.setattr(
        scanner_runtime,
        "install_managed_semgrep_runtime",
        lambda *args, **kwargs: ready,
    )
    cli.command_handlers.handle_init()
    assert cli.runtime.repair.store.load() == current


def test_init_is_the_only_index_command_and_refreshes_existing_index(
    cli, tmp_path, monkeypatch
):
    ready = scanner_runtime.RuntimeStatus("ready", "1.160.0", "1.160.0")
    monkeypatch.setattr(
        scanner_runtime,
        "install_managed_semgrep_runtime",
        lambda *args, **kwargs: ready,
    )
    cli.command_handlers.handle_init()
    (tmp_path / "Added.java").write_text("interface Added {}")
    cli.command_handlers.handle_init()
    assert cli.runtime.symbol_index.search("Added").entries
    names = {command.name for command in commands.CLI_COMMANDS}
    assert "/init" in names and "/reindex" not in names
    cli.renderer.reset_mock()
    cli.command_router.handle_command("/reindex")
    cli.renderer.print_error.assert_called_once()


def test_init_parser_failure_stops_without_partial_success(
    cli, tmp_path, monkeypatch
):
    current = pending(cli, tmp_path)
    ready = scanner_runtime.RuntimeStatus("ready", "1.160.0", "1.160.0")
    monkeypatch.setattr(
        scanner_runtime,
        "install_managed_semgrep_runtime",
        lambda *args, **kwargs: ready,
    )
    with mock.patch.object(
        java_symbols.JavaSymbolExtractor,
        "extract",
        side_effect=RuntimeError("parser unavailable"),
    ):
        with pytest.raises(RuntimeError, match="parser unavailable"):
            cli.command_handlers.handle_init()
    cli.renderer.print_success.assert_not_called()
    assert cli.runtime.repair.store.load() == current


def test_apply_uses_displayed_version_not_newly_loaded_selection(cli, tmp_path):
    displayed = pending(cli, tmp_path)
    repair = cli.runtime.repair
    new_edit = models.FileEdit(
        path="A.java",
        replacements=(models.Replacement(old_text="n = 1", new_text="n = 3"),),
    )
    current = repair.commit(
        displayed, repair.prepare(displayed, (new_edit,), "new proposal")
    )
    with pytest.raises(models.StateConflict):
        cli.input_router.handle_input("apply", displayed)
    assert repair.store.load() == current
    assert b"n = 1" in (tmp_path / "A.java").read_bytes()


def test_display_previews_every_file_and_does_not_hide_unverified(
    cli, tmp_path
):
    current = pending(cli, tmp_path)
    assert current.proposal is not None
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = EOFError()
    cli.welcome_presenter = mock.MagicMock()
    cli.run()
    assert (
        cli.renderer.print_diff.call_args.args[0]
        == current.proposal.files[0].diff
    )
    assert any(
        "apply" in str(call) for call in cli.renderer.print_plain.call_args_list
    )


def test_new_and_reset_keep_notes_history_and_lock(cli, tmp_path):
    current = pending(cli, tmp_path)
    state_dir = tmp_path / ".autopatch-j"
    (state_dir / "memory.db").write_bytes(b"legacy user data")
    (state_dir / "history.txt").write_text("commands")
    lock = state_dir / "repair.lock"
    assert lock.exists()
    cli.runtime.model_runtime.run(
        cli.runtime.memory.store.write(
            "project/main/MEMORY.md",
            "confirmed preference",
            expected_version=None,
        )
    )
    cli.command_handlers.displayed_state = current
    cli.command_handlers.handle_new()
    assert cli.runtime.repair.store.load().version > current.version
    assert (
        cli.runtime.model_runtime.run(cli.runtime.memory.show()).content
        == "confirmed preference"
    )
    cli.command_handlers.displayed_state = cli.runtime.repair.store.load()
    cli.command_handlers.handle_reset()
    assert cli.runtime is None
    assert lock.exists()
    assert (state_dir / "memory.db").read_bytes() == b"legacy user data"
    assert (state_dir / "history.txt").read_text() == "commands"


def test_command_router_rejects_unknown_args_and_bad_quoting(cli):
    for text in ("/status extra", "/unknown", '/memory "unterminated'):
        cli.command_router.handle_command(text)
    assert cli.command_handlers.host.renderer.print_error.call_count == 3


def test_cli_loop_preserves_pending_on_read_question_and_closes(
    tmp_path, sdk_scenario, monkeypatch
):
    (tmp_path / "pom.xml").write_text("<project/>")
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario([sdk_fakes.Reply(text="只是说明，提案仍待审。")])
    monkeypatch.setattr(
        factory, "build_model_runtime", lambda settings: scenario.runtime()
    )
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    cli = app.AutoPatchCli(tmp_path, autopatch_j_config.AppConfig())
    current = pending(cli, tmp_path)
    cli.renderer = mock.MagicMock()
    assert cli.input_router is not None
    cli.input_router.renderer = cli.renderer
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = ["解释 @A.java 的改动", EOFError()]
    assert cli.run() == 0
    assert cli.runtime is not None
    assert cli.runtime.model_runtime.closed
    assert cli.runtime.repair.store.load() == current


@pytest.mark.parametrize("debug", [False, True])
def test_provider_error_details_respect_debug_boundary(cli, debug):
    cli.settings = dataclasses.replace(cli.settings, debug_mode=debug)
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = [
        "调查代码",
        "/help",
        EOFError(),
    ]
    cli.welcome_presenter = mock.MagicMock()
    cli.input_router.handle_input = mock.Mock(
        side_effect=RuntimeError("provider body private-marker")
    )
    assert cli.run() == 0
    shown = str(cli.renderer.print_error.call_args_list) + str(
        cli.renderer.print_plain.call_args_list
    )
    assert ("private-marker" in shown) is debug


@pytest.mark.parametrize("failure", ["corrupt", "unreadable"])
def test_state_failure_after_input_keeps_help_without_reset(
    cli, tmp_path, monkeypatch, failure
):
    pending(cli, tmp_path)
    runtime = cli.runtime
    store = runtime.repair.store
    original_load = store.load
    original_bytes = store.path.read_bytes()
    broken_bytes = (
        b"invalid repair json" if failure == "corrupt" else original_bytes
    )
    loads = 0

    def load():
        nonlocal loads
        loads += 1
        if loads >= 2 and failure == "unreadable":
            raise PermissionError("repair state is not readable")
        return original_load()

    inputs = iter(["调查代码", "/help"])

    def prompt(*args, **unused_options):
        del args
        command = next(inputs, None)
        if command == "调查代码":
            store.path.write_bytes(broken_bytes)
        if command == "/help":
            assert cli.command_handlers.displayed_state is None
            assert not runtime.model_runtime.closed
        if command is None:
            raise EOFError
        return command

    monkeypatch.setattr(store, "load", load)
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = prompt
    cli.welcome_presenter = mock.MagicMock()

    assert cli.run() == 0
    assert loads == 2
    assert cli.prompt_session.prompt.call_count == 3
    cli.renderer.print_error.assert_called_once()
    assert runtime.model_runtime.closed
    assert store.path.read_bytes() == broken_bytes
    assert (tmp_path / "A.java").read_text() == "class A { int n = 1; }"


@pytest.mark.parametrize(
    "fault",
    [
        "memory",
        "history_read",
        "history_save",
        "parser",
        "scan",
        "source",
        "index",
        "state",
    ],
)
def test_operational_fault_stops_agent_and_accepts_help(
    tmp_path, monkeypatch, sdk_scenario, fault
):
    (tmp_path / "A.java").write_text("class A {}")
    tools: dict[str, tuple[str, dict[str, object]]] = {
        "parser": ("read_source_block", {"path": "A.java", "line": 1}),
        "scan": ("scan_java", {"paths": ["A.java"]}),
        "source": ("read_source_file", {"path": "A.java"}),
        "index": ("search_symbols", {"query": "A"}),
    }
    tool = tools.get(fault)
    replies = (
        [sdk_fakes.Reply(tool=tool[0], arguments=tool[1], finish="tool_calls")]
        if tool
        else [sdk_fakes.Reply(text="已生成的回答")]
    )
    scenario = sdk_scenario(replies)
    monkeypatch.setattr(
        factory, "build_model_runtime", lambda _cfg: scenario.runtime()
    )
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    terminal = app.AutoPatchCli(tmp_path, autopatch_j_config.AppConfig())
    terminal.renderer = mock.MagicMock()
    terminal.welcome_presenter = mock.MagicMock()
    assert terminal.input_router is not None
    terminal.input_router.renderer = terminal.renderer
    terminal.prompt_session = mock.MagicMock()
    terminal.prompt_session.prompt.side_effect = [
        "调查代码",
        "/help",
        EOFError(),
    ]
    runtime = terminal.runtime
    assert runtime is not None

    async def async_failed(*unused_args, **unused_kwargs):
        raise sqlite3.OperationalError("injected storage failure")

    def failed(*unused_args, **unused_kwargs):
        raise OSError("injected operation failure")

    if fault == "memory":
        monkeypatch.setattr(runtime.memory.store, "read", async_failed)
    elif fault == "history_read":
        monkeypatch.setattr(runtime.conversations, "load", async_failed)
    elif fault == "history_save":
        monkeypatch.setattr(runtime.conversations, "save", async_failed)
    elif fault == "parser":
        monkeypatch.setattr(java_ast, "parse", failed)
    elif fault == "scan":
        monkeypatch.setattr(runtime.evidence, "scan", failed)
    elif fault == "source":
        monkeypatch.setattr(runtime.source_reader, "read", failed)
    elif fault == "index":
        monkeypatch.setattr(runtime.symbol_index, "search", failed)
    else:
        runtime.repair.store.path.write_text('{"broken":true}')
    assert terminal.run() == 0
    assert runtime.model_runtime.closed
    assert terminal.prompt_session.prompt.call_count == 3
    assert len(scenario.requests) == (
        0 if fault in {"state", "memory", "history_read"} else 1
    )
    assert terminal.renderer.print_error.called
    if fault == "history_save":
        assert any(
            "已生成的回答" in str(call)
            for call in terminal.renderer.print_plain.call_args_list
        )


def test_close_failure_makes_normal_cli_exit_nonzero(cli, monkeypatch):
    original = cli.runtime.model_runtime.close

    def failed():
        original()
        raise OSError("close failed")

    monkeypatch.setattr(cli.runtime.model_runtime, "close", failed)
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = EOFError
    assert cli.run() == 1
    assert cli.runtime.model_runtime.closed
    # 恢复正常的幂等 close，交 fixture 完成收尾。
    monkeypatch.setattr(cli.runtime.model_runtime, "close", original)


def test_applied_snapshot_read_fault_keeps_prompt_and_facts(cli, tmp_path):
    repair, state = test_repair.pending(tmp_path)
    repair.apply(state)
    saved = repair.store.load()
    (tmp_path / "A.java").unlink()
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = ["/help", EOFError()]
    assert cli.run() == 0
    assert cli.prompt_session.prompt.call_count == 2
    assert cli.runtime.model_runtime.closed
    assert repair.store.load() == saved
    reported = str(cli.renderer.print_plain.call_args_list)
    assert "applied" in reported and "已写入" in reported
    assert "源码" in str(cli.renderer.print_error.call_args_list)


def test_memory_command_backend_value_error_accepts_next_command(
    cli, monkeypatch
):
    async def failed():
        raise ValueError("invalid stored metadata")

    monkeypatch.setattr(cli.runtime.memory, "paths", failed)
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = [
        "/memory list",
        "/help",
        EOFError(),
    ]
    assert cli.run() == 0
    assert cli.runtime.model_runtime.closed
    assert cli.prompt_session.prompt.call_count == 3
    cli.renderer.print_error.assert_called_once()


@pytest.mark.parametrize(
    "fault", ["preflight", "second_write", "progress_save", "rescan"]
)
def test_apply_fault_keeps_cli_with_real_file_facts(
    cli, tmp_path, monkeypatch, fault
):
    source = "class A { int n = 1; }"
    (tmp_path / "A.java").write_text(source)
    (tmp_path / "B.java").write_text("class B { int n = 1; }")
    runtime = cli.runtime
    repair = runtime.repair
    scanner = mock.MagicMock()
    scanner.scan.return_value = test_patch_verification.scanner_result(
        [test_patch_verification.warning(source, "n = 1")]
    )
    repair.scanner = scanner
    runtime.evidence.scanner = scanner
    baseline = repair.store.load()
    current = repair.commit(
        baseline, repair.start(baseline, "fix", ("A.java", "B.java"))
    )
    current = repair.approve_scope(current)
    record = runtime.evidence.scan(("A.java",))
    target, _ = runtime.evidence.finding(f"{record.id}:F1")
    state = repair.commit(
        current,
        repair.prepare(
            current,
            tuple(
                test_repair.edit(name, ("n = 1", "n = 2"))
                for name in current.write_paths
            ),
            "完整修复",
            target,
        ),
    )
    scanner.scan.return_value = test_patch_verification.scanner_result(
        [], "error"
    )
    read_bytes = pathlib.Path.read_bytes
    atomic_write = files.atomic_write
    save = repair.store.save

    def read(path):
        if fault == "preflight" and path.name == "B.java":
            raise OSError("source unavailable")
        return read_bytes(path)

    def write(path, content, **kwargs):
        if fault == "second_write" and path.name == "B.java":
            raise OSError("write unavailable")
        atomic_write(path, content, **kwargs)

    def save_state(value):
        if fault == "progress_save" and any(
            row.status == "written" for row in value.progress
        ):
            raise OSError("progress unavailable")
        save(value)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    monkeypatch.setattr(files, "atomic_write", write)
    monkeypatch.setattr(repair.store, "save", save_state)
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = ["apply", "/help", EOFError()]
    assert cli.run() == 0
    assert cli.prompt_session.prompt.call_count == 3
    assert runtime.model_runtime.closed
    saved = repair.store.load()
    assert saved.proposal.id == state.proposal.id
    assert (
        saved.status
        == {
            "preflight": "pending",
            "second_write": "interrupted",
            "progress_save": "applying",
            "rescan": "applied",
        }[fault]
    )
    assert (
        b"n = 2" in read_bytes(tmp_path / "A.java")
        if fault != "preflight"
        else b"n = 1" in read_bytes(tmp_path / "A.java")
    )
    assert (
        b"n = 2" in read_bytes(tmp_path / "B.java")
        if fault == "rescan"
        else b"n = 1" in read_bytes(tmp_path / "B.java")
    )
    assert scanner.scan.call_count == (2 if fault == "rescan" else 1)
    assert not saved.checks
    assert cli.renderer.print_error.called


@pytest.mark.parametrize("terminal_type", ["xterm-256color", "dumb"])
def test_native_completion_failure_preserves_input_and_tab_retry(
    cli, monkeypatch, terminal_type
):
    monkeypatch.setenv("TERM", terminal_type)
    index = cli.runtime.symbol_index
    index.rebuild_index()
    original_index = index.db_path.read_bytes()
    queries = []
    observed = []
    search = index.search
    help_handler = mock.Mock(wraps=cli.command_handlers.handle_help)
    monkeypatch.setattr(cli.command_handlers, "handle_help", help_handler)

    def record_search(query, **kwargs):
        queries.append(query)
        return search(query, **kwargs)

    monkeypatch.setattr(index, "search", record_search)
    with prompt_input.create_pipe_input() as terminal_input:
        with prompt_current.create_app_session(
            input=terminal_input, output=prompt_output.DummyOutput()
        ):
            cli.prompt_session = cli.input_controller.create_prompt_session()
            buffer = cli.prompt_session.default_buffer

            async def timeout(application):
                await asyncio.sleep(4)
                application.exit(exception=TimeoutError("completion hung"))

            async def retry():
                observed.append((buffer.text, buffer.cursor_position))
                terminal_input.send_text("abc")
                await asyncio.sleep(0.05)
                observed.append((buffer.text, len(queries)))
                index.db_path.write_bytes(original_index)
                terminal_input.send_text("\t")
                await asyncio.sleep(0.05)
                terminal_input.send_text("\x15/help\r\x04")

            def report(unused_message):
                prompt_current.get_app().create_background_task(retry())

            cli.renderer.print_error.side_effect = report

            def corrupt_on_typing(current_buffer):
                if current_buffer.text == "@":
                    index.db_path.write_bytes(b"corrupt sqlite")
                    application = prompt_current.get_app()
                    application.create_background_task(timeout(application))

            buffer.on_text_changed += corrupt_on_typing
            terminal_input.send_text("@")
            assert cli.run() == 0
    assert observed == [("@", 1), ("@abc", 1)]
    assert queries == ["", "abc"]
    help_handler.assert_called_once()
    cli.renderer.print_error.assert_called_once()
    assert cli.runtime.model_runtime.closed


def test_conversation_cas_rejection_preserves_answer_and_allows_next_input(
    tmp_path, sdk_scenario, monkeypatch
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(text="已生成但未保存的回答"),
            sdk_fakes.Reply(text="下一轮正常回答"),
        ]
    )
    monkeypatch.setattr(
        factory, "build_model_runtime", lambda _cfg: scenario.runtime()
    )
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    terminal = app.AutoPatchCli(tmp_path, autopatch_j_config.AppConfig())
    runtime = terminal.runtime
    assert runtime is not None
    other = conversations.ProjectConversations(tmp_path)

    async def compete():
        scenario.before_response = None
        saved = await other.load()
        await other.save(
            saved.summary,
            [
                messages.ModelRequest(
                    parts=[messages.UserPromptPart("COMPETING_CONVERSATION")]
                )
            ],
        )

    scenario.before_response = compete

    def before_input(command, unused_state, output):
        if command == "继续":
            assert (
                "已生成但未保存的回答" in output and "本轮历史未保存" in output
            )
            assert not runtime.model_runtime.closed

    try:
        output = cli_helpers.drive_terminal(
            terminal, ["首次问答", "继续"], before_input
        )
        assert "下一轮正常回答" in output and len(scenario.requests) == 2
        assert "COMPETING_CONVERSATION" in str(scenario.requests[1])
        assert runtime.model_runtime.closed
    finally:
        terminal.clear_runtime()


def test_rescan_cas_rejection_keeps_applied_facts_and_allows_next_command(
    cli, tmp_path
):
    source = "class A { int n = 1; }"
    scanner = mock.MagicMock()
    scanner.scan.return_value = test_patch_verification.scanner_result(
        [test_patch_verification.warning(source, "n = 1")]
    )
    repair, state = test_patch_verification.prepared_task(
        tmp_path, scanner, source
    )
    cli.runtime.repair.scanner = scanner
    record = cli.runtime.evidence
    record.scanner = scanner
    scanned = record.scan(("A.java",))
    target, _ = record.finding(f"{scanned.id}:F1")
    repair.commit(
        state,
        repair.prepare(
            state,
            (test_repair.edit("A.java", ("n = 1", "n = 2")),),
            "修复",
            target,
        ),
    )

    def compete(*unused_args):
        other = service.RepairService(tmp_path)
        applied = other.store.load()
        other.commit(applied, other.start(applied, "后续任务", ("A.java",)))
        return test_patch_verification.scanner_result([])

    scanner.scan.side_effect = compete
    cli.renderer = render.CliRenderer()
    cli.welcome_presenter.renderer = cli.renderer
    cli.input_router.renderer = cli.renderer
    cli.command_router.renderer = cli.renderer

    def before_input(command, state, output):
        if command == "/help":
            assert state.goal == "后续任务" and state.proposal is None
            assert "源码已应用" in output and "验证结果未写入当前任务" in output
            assert not cli.runtime.model_runtime.closed

    cli_helpers.drive_terminal(cli, ["apply", "/help"], before_input)
    assert b"n = 2" in (tmp_path / "A.java").read_bytes()
    assert repair.store.load().goal == "后续任务"


def test_restart_without_init_keeps_initialization_guidance(cli, tmp_path):
    for terminal in (
        cli,
        app.AutoPatchCli(tmp_path, autopatch_j_config.AppConfig()),
    ):
        renderer = mock.MagicMock()
        terminal.welcome_presenter.renderer = renderer
        terminal.welcome_presenter.render(
            terminal.repo_root, terminal.is_first_run, terminal.runtime
        )
        assert terminal.is_first_run
        assert "/init" in str(renderer.print_panel.call_args_list)
        assert "[就绪]" not in str(renderer.print_panel.call_args_list)
        terminal.clear_runtime()


@pytest.mark.parametrize("rescan_fails", [False, True])
def test_corrupted_state_after_rescan_preserves_applied_facts(
    cli, tmp_path, rescan_fails
):
    source = "class A { int n = 1; }"
    scanner = mock.MagicMock()
    scanner.scan.return_value = test_patch_verification.scanner_result(
        [test_patch_verification.warning(source, "n = 1")]
    )
    repair, state = test_patch_verification.prepared_task(
        tmp_path, scanner, source
    )
    cli.runtime.repair.scanner = scanner
    cli.runtime.evidence.scanner = scanner
    scanned = cli.runtime.evidence.scan(("A.java",))
    target, _ = cli.runtime.evidence.finding(f"{scanned.id}:F1")
    repair.commit(
        state,
        repair.prepare(
            state,
            (test_repair.edit("A.java", ("n = 1", "n = 2")),),
            "修复",
            target,
        ),
    )

    def corrupt_state(*unused_args):
        assert repair.store.load().status == "applied"
        assert b"n = 2" in (tmp_path / "A.java").read_bytes()
        repair.store.path.write_bytes(b"corrupted state")
        if rescan_fails:
            raise OSError("rescan unavailable")
        return test_patch_verification.scanner_result([])

    scanner.scan.side_effect = corrupt_state
    help_handler = mock.Mock(wraps=cli.command_handlers.handle_help)
    cli.command_handlers.handle_help = help_handler
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = ["apply", "/help", EOFError()]
    assert cli.run() == 0
    help_handler.assert_called_once()
    shown = str(cli.renderer.print_error.call_args_list)
    assert "源码已应用" in shown
    if rescan_fails:
        assert "复扫失败" in shown and "失败原因未能保存" in shown
    else:
        assert "验证结果保存失败" in shown and "任务状态损坏" in shown
    assert cli.command_handlers.displayed_state is None
    assert repair.store.path.read_bytes() == b"corrupted state"
    assert b"n = 2" in (tmp_path / "A.java").read_bytes()


@pytest.mark.parametrize(
    "status", ["idle", "investigating", "applied", "applying", "interrupted"]
)
def test_discard_rejection_reports_actual_task_state(cli, tmp_path, status):
    if status != "idle":
        pending(cli, tmp_path)
        repair = cli.runtime.repair
        if status == "investigating":
            repair.end(repair.store.load())
            initial = repair.store.load()
            repair.commit(initial, repair.start(initial, "继续调查", ()))
        elif status == "applied":
            repair.apply(repair.store.load())
        else:
            current = repair.store.load()
            repair.store.save(
                models.RepairState.model_validate(
                    {
                        **current.model_dump(),
                        "status": status,
                        "progress": [
                            {"path": "A.java", "status": "not_written"}
                        ],
                    }
                )
            )
    before = cli.runtime.repair.store.load()
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = ["discard", "/help", EOFError()]
    assert cli.run() == 0
    shown = str(cli.renderer.print_error.call_args_list)
    assert ("当前任务已中断" in shown) == (
        status in {"applying", "interrupted"}
    )
    assert ("当前没有待审提案" in shown) == (
        status not in {"applying", "interrupted"}
    )
    assert cli.runtime.repair.store.load() == before


@pytest.mark.parametrize(
    "settings",
    [
        autopatch_j_config.AppConfig(),
        autopatch_j_config.AppConfig(llm_api_key="key", llm_base_url="invalid"),
        autopatch_j_config.AppConfig(llm_api_key="key", llm_model=""),
        autopatch_j_config.AppConfig(llm_api_key="key", llm_request_limit=0),
    ],
)
def test_invalid_startup_keeps_help_status_and_quit(
    tmp_path, settings, monkeypatch
):
    build_client = mock.Mock(
        side_effect=AssertionError("must not create a model client")
    )
    monkeypatch.setattr(factory.openai, "AsyncOpenAI", build_client)
    terminal = app.AutoPatchCli(tmp_path, settings)
    assert terminal.runtime is None
    terminal.renderer = mock.MagicMock()
    terminal.command_router.renderer = terminal.renderer
    terminal.welcome_presenter = mock.MagicMock()
    terminal.prompt_session = mock.MagicMock()
    terminal.prompt_session.prompt.side_effect = [
        "请检查项目",
        "/help",
        "/status",
        "/quit",
    ]
    with pytest.raises(SystemExit) as caught:
        terminal.run()
    assert caught.value.code == 0
    assert terminal.prompt_session.prompt.call_count == 4
    assert terminal.renderer.print_table.called
    assert terminal.runtime is None
    assert terminal.runtime_unavailable_reason in str(
        terminal.renderer.print_error.call_args_list
    )
    build_client.assert_not_called()


def test_project_directory_failure_is_not_misreported_as_missing_key(
    tmp_path, monkeypatch
):
    def unavailable_root(unused_path):
        raise PermissionError("project unavailable")

    monkeypatch.setattr(
        autopatch_j_config, "discover_repo_root", unavailable_root
    )
    terminal = app.AutoPatchCli(tmp_path, autopatch_j_config.AppConfig())
    terminal.renderer = mock.MagicMock()
    terminal.command_router.renderer = terminal.renderer
    terminal.welcome_presenter = mock.MagicMock()
    terminal.prompt_session = mock.MagicMock()
    terminal.prompt_session.prompt.side_effect = ["/help", EOFError()]
    assert terminal.run() == 0
    shown = str(terminal.renderer.print_error.call_args_list)
    assert "项目服务未启动" in shown and "没有访问权限" in shown
    assert "AUTOPATCH_LLM_API_KEY" not in shown


def test_failed_preview_requires_fresh_human_decision(
    cli, tmp_path, monkeypatch
):
    state = pending(cli, tmp_path)
    # 故障注入针对真实预览边界，公开人工审核操作仍通过 CLI 执行。
    preview = cli._render_repair  # pylint: disable=protected-access
    attempts = 0

    def render_preview(value):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("preview unavailable")
        preview(value)

    monkeypatch.setattr(cli, "_render_repair", render_preview)
    commands_to_enter = iter(["apply", "apply", "/help"])
    entered = []

    def prompt(*unused_args, **unused_options):
        command = next(commands_to_enter, None)
        if len(entered) == 1:
            assert cli.runtime.repair.store.load() == state
            assert b"n = 1" in (tmp_path / "A.java").read_bytes()
            assert cli.command_handlers.displayed_state == state
        entered.append(command)
        if command is None:
            raise EOFError
        return command

    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = prompt
    assert cli.run() == 0
    assert cli.runtime.repair.store.load().status == "applied"
    assert b"n = 2" in (tmp_path / "A.java").read_bytes()


@pytest.mark.parametrize("terminal_type", ["xterm-256color", "dumb"])
@pytest.mark.parametrize("phase", ["read", "write"])
def test_native_history_fault_keeps_help_input(
    cli, monkeypatch, terminal_type, phase
):
    monkeypatch.setenv("TERM", terminal_type)

    def fail(*unused_args):
        raise PermissionError("history unavailable")

    monkeypatch.setattr(
        prompt_history.FileHistory,
        "load_history_strings" if phase == "read" else "store_string",
        fail,
    )
    help_handler = mock.Mock(wraps=cli.command_handlers.handle_help)
    monkeypatch.setattr(cli.command_handlers, "handle_help", help_handler)
    with prompt_input.create_pipe_input() as terminal_input:
        with prompt_current.create_app_session(
            input=terminal_input, output=prompt_output.DummyOutput()
        ):
            cli.prompt_session = cli.input_controller.create_prompt_session()
            if phase == "read":
                cli.renderer.print_error.side_effect = (
                    lambda unused_message: terminal_input.send_text(
                        "/help\r\x04"
                    )
                )
            else:
                terminal_input.send_text("/help\r\x04")
            assert cli.run() == 0
    help_handler.assert_called_once()
    cli.renderer.print_error.assert_called_once()
    assert "输入历史" in str(cli.renderer.print_error.call_args)


def test_init_failure_keeps_shell_and_explicit_retry(cli, monkeypatch):
    calls = 0

    def install(*unused_args, **unused_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return scanner_runtime.RuntimeStatus(
                "broken", "1.160.0", message="installation blocked"
            )
        return scanner_runtime.RuntimeStatus("ready", "1.160.0", "1.160.0")

    monkeypatch.setattr(
        scanner_runtime, "install_managed_semgrep_runtime", install
    )
    cli.welcome_presenter = mock.MagicMock()
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = [
        "/init",
        "/help",
        "/init",
        EOFError(),
    ]
    assert cli.run() == 0
    assert calls == 2
    assert cli.runtime.symbol_index.get_stats()["total"] >= 0
    assert cli.prompt_session.prompt.call_count == 4


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "模型认证失败"),
        (403, "模型访问被拒绝"),
        (404, "接口或模型未找到"),
        (429, "模型请求受限"),
        (503, "模型服务异常"),
    ],
)
def test_model_error_uses_actual_status_and_hides_body(cli, status, expected):
    error = sdk_errors.ModelHTTPError(
        status, "test-model", body="private-marker contains 401"
    )
    # 测试在展示边界注入真实 SDK 异常类型，核对公开状态字段。
    # pylint: disable=protected-access
    cli._render_runtime_error(
        error, "Agent"
    )  # pylint: disable=protected-access
    shown = str(cli.renderer.print_error.call_args)
    assert expected in shown
    assert "private-marker" not in shown


@pytest.mark.parametrize("terminal_type", ["xterm-256color", "dumb"])
def test_unhandled_native_background_error_is_friendly_and_keeps_input(
    cli, monkeypatch, terminal_type
):
    monkeypatch.setenv("TERM", terminal_type)
    help_handler = mock.Mock(wraps=cli.command_handlers.handle_help)
    monkeypatch.setattr(cli.command_handlers, "handle_help", help_handler)
    with prompt_input.create_pipe_input() as terminal_input:
        with prompt_current.create_app_session(
            input=terminal_input, output=prompt_output.DummyOutput()
        ):
            cli.prompt_session = cli.input_controller.create_prompt_session()

            async def failed_task():
                raise RuntimeError("private background detail")

            def trigger(buffer):
                if buffer.text == "z":
                    prompt_current.get_app().create_background_task(
                        failed_task()
                    )

            cli.prompt_session.default_buffer.on_text_changed += trigger
            cli.renderer.print_error.side_effect = (
                lambda unused_message: terminal_input.send_text(
                    "\x15/help\r\x04"
                )
            )
            terminal_input.send_text("z")
            assert cli.run() == 0
    help_handler.assert_called_once()
    cli.renderer.print_error.assert_called_once()
    assert "输入辅助功能" in str(cli.renderer.print_error.call_args)
    assert "private background detail" not in str(
        cli.renderer.print_error.call_args
    )


def test_history_save_failure_does_not_claim_a_nonexistent_proposal(
    tmp_path, monkeypatch, sdk_scenario
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="start_repair",
                arguments={"goal": "调查修复", "write_paths": []},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="任务已建立，等待下一步"),
        ]
    )
    monkeypatch.setattr(
        factory,
        "build_model_runtime",
        lambda unused_settings: scenario.runtime(),
    )
    terminal = app.AutoPatchCli(tmp_path, autopatch_j_config.AppConfig())
    runtime = terminal.runtime

    assert runtime is not None and terminal.input_router is not None

    async def failed(*unused_args):
        raise PermissionError("history storage unavailable")

    monkeypatch.setattr(runtime.conversations, "save", failed)
    terminal.renderer = mock.MagicMock()
    terminal.input_router.renderer = terminal.renderer
    terminal.welcome_presenter = mock.MagicMock()
    terminal.prompt_session = mock.MagicMock()
    terminal.prompt_session.prompt.side_effect = [
        "修复但先调查",
        "/help",
        EOFError(),
    ]
    assert terminal.run() == 0
    saved = runtime.repair.store.load()
    assert saved.task_id is not None and saved.proposal is None
    shown = str(terminal.renderer.print_plain.call_args_list)
    assert "任务变更已保存" in shown
    assert "提案已提交" not in shown
