from __future__ import annotations

from unittest.mock import MagicMock

from autopatch_j.cli.render import BODY_STYLE, MUTED_STYLE, CliRenderer


def test_tool_start_uses_same_muted_style_for_agent_and_llm() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_tool_start("scan_project", caller="AGENT")
    renderer.print_tool_start("read_source_file", caller="LLM")

    calls = [call.args[0] for call in renderer.console.print.call_args_list]
    assert calls[0].startswith(f"[{MUTED_STYLE}]")
    assert "[AGENT]" in calls[0]
    assert "scan_project" in calls[0]
    assert calls[1].startswith(f"[{MUTED_STYLE}]")
    assert "[LLM]" in calls[1]
    assert "read_source_file" in calls[1]


def test_reasoning_text_is_plain_italic_muted_text() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_reasoning_text("step 1")

    renderer.console.print.assert_called_once_with(
        "step 1",
        end="",
        highlight=False,
        markup=False,
        style=f"italic {MUTED_STYLE}",
    )


def test_reasoning_status_prints_single_compact_status() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_reasoning_status(0)

    args, kwargs = renderer.console.print.call_args
    assert args[0].startswith(f"[italic {MUTED_STYLE}]")
    assert kwargs == {"end": "", "soft_wrap": True}


def test_agent_text_is_plain_muted_text() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_agent_text('```java\nMessageDigest.getInstance("MD5");\n```')

    renderer.console.print.assert_called_once_with(
        '```java\nMessageDigest.getInstance("MD5");\n```',
        end="\n",
        highlight=False,
        markup=False,
        style=MUTED_STYLE,
    )


def test_agent_text_supports_custom_end() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_agent_text("source read summary", end="")

    renderer.console.print.assert_called_once_with(
        "source read summary",
        end="",
        highlight=False,
        markup=False,
        style=MUTED_STYLE,
    )


def test_assistant_anchor_uses_body_style_without_bold_blue() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_assistant_anchor()

    renderer.console.print.assert_called_once_with(f"[{BODY_STYLE}]AutoPatch-J:[/]")
