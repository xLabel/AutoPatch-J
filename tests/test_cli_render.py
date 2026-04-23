from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autopatch_j.cli.app import AutoPatchCLI
from autopatch_j.cli.assistant_stream import AssistantStream
from autopatch_j.cli.render import MUTED_STYLE, SYSTEM_STYLE, CliRenderer
from autopatch_j.core.models import IntentType


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

    cli.renderer.print_panel.assert_called_once_with("命令帮助", style=SYSTEM_STYLE)
    first_table = cli.renderer.console.print.call_args_list[0].args[0]
    second_table = cli.renderer.console.print.call_args_list[1].args[0]
    assert first_table.columns[1]._cells[3] == "重建代码索引"
    assert second_table.columns[1]._cells[0] == "补全文件或目录"


def test_handle_status_uses_system_panel_style(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    cli.indexer = SimpleNamespace(
        get_stats=lambda: {"file": 1, "class": 2, "method": 3, "total": 6},
        fetch_symbol_extract_status=lambda: {"enabled": True, "mode": "full", "last_error": None},
    )
    cli.workflow_service = SimpleNamespace(fetch_current_patch_item=lambda: None)
    cli.renderer.print_panel = MagicMock()

    fake_scanner = SimpleNamespace(
        get_meta=lambda repo_root: SimpleNamespace(is_implemented=True, status="就绪", version="1.0.0")
    )

    with patch("autopatch_j.cli.command_controller.get_scanner", return_value=fake_scanner):
        cli.handle_status()

    assert cli.renderer.print_panel.call_args.kwargs["style"] == SYSTEM_STYLE


def test_handle_status_shows_symbol_extract_degraded_state(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    cli.indexer = SimpleNamespace(
        get_stats=lambda: {"file": 1, "class": 0, "method": 0, "total": 1},
        fetch_symbol_extract_status=lambda: {
            "enabled": False,
            "mode": "degraded",
            "last_error": "missing dependency: tree_sitter",
        },
    )
    cli.workflow_service = SimpleNamespace(fetch_current_patch_item=lambda: None)
    cli.renderer.print_panel = MagicMock()

    fake_scanner = SimpleNamespace(
        get_meta=lambda repo_root: SimpleNamespace(is_implemented=True, status="就绪", version="1.0.0")
    )

    with patch("autopatch_j.cli.command_controller.get_scanner", return_value=fake_scanner):
        cli.handle_status()

    table = cli.renderer.print_panel.call_args.args[0]
    assert table.columns[0]._cells[-1] == "[bold]符号提取[/]"
    assert "已降级" in table.columns[1]._cells[-1]


def test_renderer_feedback_messages_drop_prefix_labels() -> None:
    renderer = CliRenderer()
    renderer.console.print = MagicMock()

    renderer.print_success("初始化完成，索引 9 项")
    renderer.print_error("系统未初始化，请先执行 /init")
    renderer.print_info("补丁队列已清空")

    calls = renderer.console.print.call_args_list
    assert "成功:" not in calls[0].args[0]
    assert "错误:" not in calls[1].args[0]
    assert "提示:" not in calls[2].args[0]


def test_renderer_uses_updated_patch_and_check_titles() -> None:
    renderer = CliRenderer()
    renderer.print_panel = MagicMock()

    renderer.print_action_panel(
        file_path="src/main/java/demo/UserService.java",
        diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n",
        validation="ok",
        rationale="修复空指针",
        current_idx=2,
        total_count=3,
    )
    assert renderer.print_panel.call_args.kwargs["title"] == "待确认补丁 (PENDING) [2/3]"

    renderer.print_panel.reset_mock()
    renderer.print_no_issue_panel(
        scope_paths=["src/main/java/demo/LegacyConfig.java"],
        scanner_summary="当前范围未发现安全或正确性问题。",
        llm_summary="模型复核未发现需要修复的问题。",
    )
    assert renderer.print_panel.call_args.kwargs["title"] == "检查结果"


def test_ensure_prompt_session_reports_clean_init_error(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    cli.renderer.print_error = MagicMock()
    cli._create_prompt_session = MagicMock(side_effect=RuntimeError("boom"))

    ready = cli._ensure_prompt_session()

    assert ready is False
    cli.renderer.print_error.assert_called_once_with("CLI 输入环境初始化失败: boom")


def test_summarize_observation_extracts_symbol_name_from_search_result(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)

    summary = cli._summarize_observation("search_symbols", "为您找到以下与 'AppConfig' 相关的匹配项：")

    assert summary == "已定位符号: AppConfig"


def test_assistant_stream_masks_reasoning_content_with_status(tmp_path: Path) -> None:
    renderer = CliRenderer()
    renderer.print = MagicMock()
    renderer.print_reasoning_status = MagicMock()
    renderer.finish_reasoning_status = MagicMock()
    renderer.print_observation = MagicMock()
    renderer.print_tool_start = MagicMock()
    renderer.print_plain = MagicMock()
    renderer.print_assistant_anchor = MagicMock()

    agent = SimpleNamespace(messages=[])
    workflow_service = SimpleNamespace(verify_has_pending_patch=lambda: False)
    chat_service = SimpleNamespace()
    stream = AssistantStream(
        renderer=renderer,
        workflow_service=workflow_service,
        chat_service=chat_service,
        agent=agent,
        sanitize_output=lambda text: text,
        prepare_display_answer=lambda answer, answer_intent, raw_user_text: answer,
        summarize_observation=lambda tool_name, message: message,
        describe_current_scope_paths=lambda: [],
        build_static_scan_summary=lambda: "",
        build_local_no_issue_summary=lambda: "",
    )

    def fake_agent_call(prompt: str, on_token, on_reasoning, on_observation, on_tool_start) -> str:
        on_reasoning("Let me think")
        on_reasoning("more reasoning")
        on_token("final answer")
        return "final answer"

    stream.run(
        prompt="prompt",
        agent_call=fake_agent_call,
        answer_intent=IntentType.GENERAL_CHAT,
        raw_user_text="question",
        plain_answer=True,
    )

    renderer.print_reasoning_status.assert_called_once_with(0)
    renderer.finish_reasoning_status.assert_called_once()
    printed = "".join(str(call.args[0]) for call in renderer.print_plain.call_args_list)
    assert "Let me think" not in printed
    assert "more reasoning" not in printed
