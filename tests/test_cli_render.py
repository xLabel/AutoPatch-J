from __future__ import annotations

from unittest.mock import MagicMock

from autopatch_j.cli.render import BODY_STYLE, MUTED_STYLE, CliRenderer


def test_tool_start_uses_same_muted_style_for_agent_and_llm() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_tool_start("scan_project", caller="AGENT")
    renderer.print_tool_start("read_source_code", caller="LLM")

    expected_style = MUTED_STYLE
    renderer.console.print.assert_any_call(f"[{expected_style}]正在执行工具[AGENT]: scan_project...[/]")
    renderer.console.print.assert_any_call(f"[{expected_style}]正在执行工具[LLM]: read_source_code...[/]")


def test_reasoning_text_is_plain_italic_muted_text() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_reasoning_text("步骤 1: 获取 F1 (MD5 弱哈希算法)。")

    renderer.console.print.assert_called_once_with(
        "步骤 1: 获取 F1 (MD5 弱哈希算法)。",
        end="",
        highlight=False,
        markup=False,
        style=f"italic {MUTED_STYLE}",
    )


def test_reasoning_status_prints_single_compact_status() -> None:
    renderer = CliRenderer()
    renderer.console = MagicMock()

    renderer.print_reasoning_status(0)

    renderer.console.print.assert_called_once_with(
        f"[italic {MUTED_STYLE}]思考中...[/]",
        end="",
        soft_wrap=True,
    )


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

    renderer.print_agent_text("已读取源代码: src/main/java/demo/LegacyConfig.java", end="")

    renderer.console.print.assert_called_once_with(
        "已读取源代码: src/main/java/demo/LegacyConfig.java",
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
