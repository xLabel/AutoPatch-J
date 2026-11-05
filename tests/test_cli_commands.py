"""从实际 CLI 入口验证人工动作、任务保留和资源收尾。"""

from unittest import mock

import pytest

from autopatch_j.cli import app
from autopatch_j import config
from autopatch_j.core.repair import models
from autopatch_j.llm import factory
from tests import sdk_fakes


@pytest.fixture(name="cli")
def make_cli(tmp_path, fake_llm_factory, monkeypatch):
    (tmp_path / "pom.xml").write_text("<project/>")
    monkeypatch.setattr(factory, "build_model_runtime", fake_llm_factory)
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    result = app.AutoPatchCli(tmp_path)
    result.renderer = mock.MagicMock()
    result.command_router.renderer = result.renderer
    assert result.runtime is not None
    assert result.input_router is not None
    result.input_router.renderer = result.renderer
    yield result
    result.clear_runtime()


def pending(cli, root):
    (root / "A.java").write_text("class A { int n = 1; }")
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
    monkeypatch.setattr(
        app.command_handlers.semgrep_runtime,
        "install_managed_semgrep_runtime",
        lambda: ("ready", None),
    )
    cli.command_handlers.handle_init()
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
    cli.command_handlers.handle_new()
    assert cli.runtime.repair.store.load().version > current.version
    assert (
        cli.runtime.model_runtime.run(cli.runtime.memory.show()).content
        == "confirmed preference"
    )
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
    monkeypatch.setattr(factory, "build_model_runtime", scenario.runtime)
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    cli = app.AutoPatchCli(tmp_path)
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
def test_provider_error_details_respect_debug_boundary(cli, monkeypatch, debug):
    monkeypatch.setattr(config.GLOBAL_CONFIG, "debug_mode", debug)
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = [
        RuntimeError("provider body private-marker"),
        EOFError(),
    ]
    cli.welcome_presenter = mock.MagicMock()
    cli.run()
    shown = str(cli.renderer.print_error.call_args)
    assert ("private-marker" in shown) is debug


@pytest.mark.parametrize("failure", ["corrupt", "unreadable"])
def test_state_failure_after_input_exits_once_without_reset(
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
        # 旧实现也能有界结束，不能让回归用例陷入刷屏循环。
        if loads > 2:
            raise KeyboardInterrupt
        if loads == 2 and failure == "unreadable":
            raise PermissionError("repair state is not readable")
        return original_load()

    def prompt(*args):
        del args
        store.path.write_bytes(broken_bytes)
        return "/help"

    monkeypatch.setattr(store, "load", load)
    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = prompt
    cli.welcome_presenter = mock.MagicMock()

    assert cli.run() == 1
    assert loads == 2
    cli.prompt_session.prompt.assert_called_once()
    cli.renderer.print_error.assert_called_once()
    assert runtime.model_runtime.closed
    assert store.path.read_bytes() == broken_bytes
    assert (tmp_path / "A.java").read_text() == "class A { int n = 1; }"
