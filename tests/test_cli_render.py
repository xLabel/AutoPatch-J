from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autopatch_j.cli.app import AutoPatchCLI
from autopatch_j.cli.render import MUTED_STYLE, SYSTEM_STYLE, CliRenderer


def _make_cli(tmp_path: Path) -> AutoPatchCLI:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    return AutoPatchCLI(tmp_path)


def test_print_tool_start_uses_compact_llm_and_agent_labels() -> None:
    renderer = CliRenderer()
    renderer.console.print = MagicMock()

    renderer.print_tool_start("scan_project", caller="AGENT")
    renderer.print_tool_start("read_source_code", caller="LLM")

    calls = renderer.console.print.call_args_list
    assert calls[0].args[0].startswith(f"\n[bold {SYSTEM_STYLE}]")
    assert " [AGENT]:" not in calls[0].args[0]
    assert "[AGENT]:" in calls[0].args[0]
    assert calls[0].args[0].endswith("scan_project...[/]")

    assert calls[1].args[0].startswith(f"\n[bold {MUTED_STYLE}]")
    assert " [LLM]:" not in calls[1].args[0]
    assert "[LLM]:" in calls[1].args[0]
    assert calls[1].args[0].endswith("read_source_code...[/]")


def test_handle_help_uses_system_panel_style(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    cli.renderer.print_panel = MagicMock()
    cli.renderer.console.print = MagicMock()
    cli.renderer.print = MagicMock()

    cli.handle_help()

    cli.renderer.print_panel.assert_called_once_with("AutoPatch-J 指令中心", style=SYSTEM_STYLE)


def test_handle_status_uses_system_panel_style(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    cli.indexer = SimpleNamespace(get_stats=lambda: {"file": 1, "class": 2, "method": 3, "total": 6})
    cli.workflow_service = SimpleNamespace(fetch_current_patch_item=lambda: None)
    cli.renderer.print_panel = MagicMock()

    fake_scanner = SimpleNamespace(
        get_meta=lambda repo_root: SimpleNamespace(is_implemented=True, status="就绪", version="1.0.0")
    )

    with patch("autopatch_j.cli.app.get_scanner", return_value=fake_scanner):
        cli.handle_status()

    assert cli.renderer.print_panel.call_args.kwargs["style"] == SYSTEM_STYLE
