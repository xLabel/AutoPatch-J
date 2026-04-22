from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autopatch_j.cli.app import AutoPatchCLI
from autopatch_j.core.models import (
    CodeScope,
    CodeScopeKind,
    IntentType,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
)
from autopatch_j.scanners.base import Finding, ScanResult


def _make_cli(tmp_path: Path) -> AutoPatchCLI:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    return AutoPatchCLI(tmp_path)


def _scope() -> CodeScope:
    return CodeScope(
        kind=CodeScopeKind.SINGLE_FILE,
        source_roots=["src/main/java/demo/User.java"],
        focus_files=["src/main/java/demo/User.java"],
        is_locked=True,
    )


def _review_item(item_id: str, file_path: str) -> PatchReviewItem:
    return PatchReviewItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=["F1"],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftData(
            file_path=file_path,
            old_string="old",
            new_string="new",
            diff="diff",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale="rationale",
            target_check_id="F1",
            target_snippet="snippet",
        ),
    )


def test_cli_code_audit_triggers_local_scan_then_agent(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_AUDIT)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.fetch_scan_snapshot = MagicMock(
        return_value=(
            "scan-1",
            ScanResult(
                engine="semgrep",
                scope=["src/main/java/demo/User.java"],
                targets=["src/main/java/demo/User.java"],
                status="ok",
                message="ok",
                findings=[],
            ),
        )
    )
    cli.renderer.print_tool_start = MagicMock()
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False: captured.update(
        {
            "prompt": prompt,
            "agent_call": agent_call,
            "scope_paths": scope_paths,
            "render_no_issue_panel": render_no_issue_panel,
        }
    )

    cli.handle_chat("@User.java 检查代码")

    cli.scope_service.fetch_scope.assert_called_once()
    cli.scan_service.fetch_scan_snapshot.assert_called_once()
    cli.renderer.print_tool_start.assert_called_once_with("scan_project", caller="AGENT")
    assert captured["agent_call"] == cli.agent.perform_code_audit
    assert captured["render_no_issue_panel"] is True


def test_cli_code_audit_injects_scan_digest_into_prompt(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_AUDIT)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.fetch_scan_snapshot = MagicMock(
        return_value=(
            "scan-1",
            ScanResult(
                engine="semgrep",
                scope=["src/main/java/demo/User.java"],
                targets=["src/main/java/demo/User.java"],
                status="ok",
                message="ok",
                findings=[
                    Finding(
                        check_id="autopatch-j.java.correctness.unsafe-equals-order",
                        path="src/main/java/demo/User.java",
                        start_line=5,
                        end_line=5,
                        severity="warning",
                        message="unsafe equals order",
                        snippet='return user.getName().equals("admin");',
                    )
                ],
            ),
        )
    )
    cli.renderer.print_tool_start = MagicMock()
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False: captured.update(
        {
            "prompt": prompt,
            "agent_call": agent_call,
            "scope_paths": scope_paths,
            "render_no_issue_panel": render_no_issue_panel,
        }
    )

    cli.handle_chat("@User.java 检查代码")

    prompt = str(captured["prompt"])
    assert "F1: src/main/java/demo/User.java:5" in prompt
    assert "优先根据 F 编号调用 get_finding_detail" in prompt
    assert captured["render_no_issue_panel"] is False


def test_cli_code_explain_skips_scan_and_uses_explain_entry(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_EXPLAIN)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.fetch_scan_snapshot = MagicMock()
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False: captured.update(
        {"agent_call": agent_call}
    )

    cli.handle_chat("@User.java 解释一下代码")

    cli.scan_service.fetch_scan_snapshot.assert_not_called()
    assert captured["agent_call"] == cli.agent.perform_code_explain


def test_cli_patch_revise_clears_remaining_tail_before_agent_call(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.PATCH_REVISE)
    cli.workflow_service.persist_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-1",
        patch_items=[
            _review_item("item-1", "src/main/java/demo/User.java"),
            _review_item("item-2", "src/main/java/demo/UserService.java"),
        ],
    )
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False: captured.update(
        {"agent_call": agent_call}
    )

    cli.handle_chat("加一句注释")

    workspace = cli.workflow_service.fetch_workspace()
    assert workspace.fetch_current_patch_item() is None
    assert workspace.patch_items == []
    assert captured["agent_call"] == cli.agent.perform_patch_revise


def test_cli_review_mixed_feedback_routes_to_patch_revise(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.workflow_service.persist_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-1",
        patch_items=[_review_item("item-1", "src/main/java/demo/User.java")],
    )
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False: captured.update(
        {"agent_call": agent_call, "prompt": prompt}
    )

    cli.handle_chat("加一行注释说明原因")

    assert captured["agent_call"] == cli.agent.perform_patch_revise
    assert "加一行注释说明原因" in str(captured["prompt"])


def test_cli_can_initialize_without_prompt_session(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    cli.handle_init()
    assert cli.artifacts is not None


def test_run_agent_request_labels_llm_tool_calls(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.workflow_service is not None

    cli.renderer.print_tool_start = MagicMock()
    cli.renderer.print = MagicMock()

    def fake_agent_call(
        prompt: str,
        on_token=None,
        on_reasoning=None,
        on_observation=None,
        on_tool_start=None,
    ) -> str:
        assert on_tool_start is not None
        on_tool_start("read_source_code")
        return ""

    cli._run_agent_request(prompt="check", agent_call=fake_agent_call)

    cli.renderer.print_tool_start.assert_called_once_with("read_source_code", caller="LLM")


def test_run_agent_request_uses_distinct_observation_and_reasoning_rendering(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.workflow_service is not None

    cli.renderer.print_reasoning = MagicMock()
    cli.renderer.print_observation = MagicMock()
    cli.renderer.print = MagicMock()

    def fake_agent_call(
        prompt: str,
        on_token=None,
        on_reasoning=None,
        on_observation=None,
        on_tool_start=None,
    ) -> str:
        assert on_reasoning is not None
        assert on_observation is not None
        on_reasoning("思考中")
        on_observation("工具观察")
        return ""

    cli._run_agent_request(prompt="check", agent_call=fake_agent_call)

    cli.renderer.print_reasoning.assert_called_once_with("思考中", end="")
    cli.renderer.print_observation.assert_called_once_with("工具观察")
