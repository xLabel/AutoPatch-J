from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autopatch_j.cli.app import AutoPatchCLI
from autopatch_j.core.models import CodeScope, CodeScopeKind


def _make_cli(tmp_path: Path) -> AutoPatchCLI:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    return AutoPatchCLI(tmp_path)


def _single_file_scope() -> CodeScope:
    return CodeScope(
        kind=CodeScopeKind.SINGLE_FILE,
        source_roots=["src/main/java/demo/LegacyConfig.java"],
        focus_files=["src/main/java/demo/LegacyConfig.java"],
        is_locked=True,
    )


def _multi_file_scope() -> CodeScope:
    return CodeScope(
        kind=CodeScopeKind.MULTI_FILE,
        source_roots=["src/main/java/demo"],
        focus_files=[
            "src/main/java/demo/LegacyConfig.java",
            "src/main/java/demo/AppConfig.java",
        ],
        is_locked=True,
    )


def test_build_code_explain_prompt_constrains_single_file_scope(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)

    prompt = cli._build_code_explain_prompt("@LegacyConfig.java explain", _single_file_scope())

    assert "当前解释范围仅限文件" in prompt
    assert "不要主动搜索、读取或推断焦点范围外的类型实现" in prompt
    assert "回答默认控制在 2 到 4 句" in prompt


def test_build_code_explain_prompt_allows_navigation_in_multi_file_scope(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)

    prompt = cli._build_code_explain_prompt("@demo explain", _multi_file_scope())

    assert "search_symbols" in prompt
    assert "read_source_code" in prompt
    assert "src/main/java/demo/AppConfig.java" in prompt


def test_handle_code_explain_keeps_bound_method_and_disables_symbol_search_for_single_file(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.scope_service is not None
    assert cli.agent is not None

    cli.scope_service.fetch_scope = MagicMock(return_value=_single_file_scope())
    cli.agent.perform_code_explain = MagicMock(return_value="done")
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False: captured.update(
        {"prompt": prompt, "agent_call": agent_call}
    )

    cli._handle_code_explain("@LegacyConfig.java explain")

    assert captured["agent_call"] == cli.agent.perform_code_explain
    assert cli.agent.code_explain_allow_symbol_search is False
    assert "当前解释范围仅限文件" in str(captured["prompt"])
