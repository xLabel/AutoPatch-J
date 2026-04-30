from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autopatch_j.cli.app import CLI
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.core.patch_verifier import SyntaxCheckResult
from autopatch_j.core.models import (
    ActiveWorkspace,
    AuditFindingItem,
    CodeScope,
    CodeScopeKind,
    IntentType,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
    WorkspaceStatus,
)


@pytest.fixture
def cli(tmp_path: Path) -> CLI:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = CLI(tmp_path)
    cli_obj.renderer = MagicMock()
    return cli_obj


def _item(
    item_id: str,
    file_path: str = "src/main/java/demo/User.java",
    finding_id: str = "F1",
) -> PatchReviewItem:
    return PatchReviewItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=[finding_id],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftData(
            file_path=file_path,
            old_string="old",
            new_string="new",
            diff="diff",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale=f"fix {finding_id}",
            target_check_id=finding_id,
        ),
    )


def _patch_draft(new_string: str, finding_id: str = "F1") -> PatchDraft:
    return PatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string=new_string,
        diff=f"diff {new_string}",
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale=f"fix {finding_id}",
        target_check_id=finding_id,
    )


def _finding(finding_id: str = "F1") -> AuditFindingItem:
    return AuditFindingItem(
        finding_id=finding_id,
        file_path="src/main/java/demo/User.java",
        check_id="demo.rule",
        start_line=1,
        end_line=1,
        message="demo finding",
        snippet="old",
    )


def _tool_message(status: str = "ok", finding_id: str = "F1") -> dict:
    return {
        "role": "tool",
        "name": "propose_patch",
        "tool_status": status,
        "tool_payload": {
            "associated_finding_id": finding_id,
            "file_path": "src/main/java/demo/User.java",
        },
    }


def test_handle_status_does_not_crash_with_pending_patch(cli: CLI) -> None:
    # Setup workspace with a pending patch
    workspace = ActiveWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.workspace_manager.save_workspace(workspace)

    # This should not raise AttributeError
    cli.command_controller.handle_status()

    cli.renderer.print_panel.assert_called_once()


def test_handle_status_includes_output_mode(cli: CLI) -> None:
    cli.command_controller.handle_status()

    table = cli.renderer.print_panel.call_args.args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]

    assert "[bold]调试模式[/]" in cells
    assert "关闭" in cells
    assert "[bold]日志模式[/]" not in cells
    assert "[bold]输出模式[/]" not in cells
    assert "[bold]静态扫描器[/]" not in cells


def test_handle_patch_explain_does_not_crash(cli: CLI) -> None:
    # Setup workspace with a pending patch
    workspace = ActiveWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.workspace_manager.save_workspace(workspace)
    cli.agent = MagicMock()

    # This should not raise AttributeError
    cli.workflow_controller.handle_patch_explain("explain this")

    cli.agent.perform_patch_explain.assert_called_once()


def test_handle_patch_revise_replaces_only_current_patch(cli: CLI) -> None:
    workspace = ActiveWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[
                "src/main/java/demo/User.java",
                "src/main/java/demo/UserService.java",
            ],
            is_locked=True,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
        current_patch_index=0,
    )
    cli.workspace_manager.save_workspace(workspace)
    replacement = PatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string="better",
        diff="better diff",
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="better fix",
        target_check_id="F1",
    )

    def revise_current(*args, **kwargs):
        cli.agent.session.set_revised_patch_draft(replacement)
        return "done"

    cli.agent.perform_patch_revise = MagicMock(side_effect=revise_current)

    cli.workflow_controller.handle_patch_revise("rewrite current")

    updated = cli.workspace_manager.load_workspace()
    assert [item.item_id for item in updated.patch_items] == ["item-1", "item-2"]
    assert updated.current_patch_index == 0
    assert updated.patch_items[0].draft.new_string == "better"
    assert updated.patch_items[1].file_path == "src/main/java/demo/UserService.java"
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli.renderer.print_info.assert_any_call("已更新当前补丁，后续补丁保持不变。")


def test_handle_patch_revise_keeps_queue_when_no_revision_created(cli: CLI) -> None:
    workspace = ActiveWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
        current_patch_index=0,
    )
    cli.workspace_manager.save_workspace(workspace)
    cli.agent.perform_patch_revise = MagicMock(return_value="no revision")

    cli.workflow_controller.handle_patch_revise("explain only")

    updated = cli.workspace_manager.load_workspace()
    assert [item.item_id for item in updated.patch_items] == ["item-1", "item-2"]
    assert updated.patch_items[0].draft.rationale == "fix F1"
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli.renderer.print_info.assert_any_call("未生成修订补丁，当前补丁保持不变。")


def test_process_single_finding_commits_staged_patch_after_success(cli: CLI) -> None:
    finding = _finding("F1")
    backlog = [finding]

    def run_agent(*args, **kwargs):
        cli.agent.session.set_proposed_patch_draft(_patch_draft("better", "F1"))
        return [_tool_message("ok", "F1")]

    cli._run_agent_request = MagicMock(side_effect=run_agent)

    cli.workflow_controller._process_single_finding(finding, "audit", backlog)

    workspace = cli.workspace_manager.load_workspace()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "better"
    assert finding.status.value == "patch_ready"
    assert cli.agent.session.proposed_patch_draft is None


def test_process_single_finding_does_not_commit_without_staged_patch(cli: CLI) -> None:
    finding = _finding("F1")
    backlog = [finding]
    cli._run_agent_request = MagicMock(return_value=[_tool_message("ok", "F1")])

    cli.workflow_controller._process_single_finding(finding, "audit", backlog)

    workspace = cli.workspace_manager.load_workspace()
    assert workspace.patch_items == []
    assert finding.status.value == "failed"
    assert finding.last_error_code == "NO_PROPOSED_PATCH_DRAFT"


def test_finding_retry_commits_only_retry_patch(cli: CLI) -> None:
    finding = _finding("F1")
    backlog = [finding]
    old_draft = _patch_draft("stale", "F1")
    cli.agent.session.set_proposed_patch_draft(old_draft)

    def run_retry(*args, **kwargs):
        cli.agent.session.set_proposed_patch_draft(_patch_draft("retry-fix", "F1"))
        return [_tool_message("ok", "F1")]

    cli._run_agent_request = MagicMock(side_effect=run_retry)

    cli.workflow_controller._handle_finding_retry(finding, "audit", backlog)

    workspace = cli.workspace_manager.load_workspace()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "retry-fix"
    assert finding.status.value == "patch_ready"


def test_finding_retry_failure_does_not_commit_patch(cli: CLI) -> None:
    finding = _finding("F1")
    backlog = [finding]

    def run_retry(*args, **kwargs):
        cli.agent.session.set_proposed_patch_draft(_patch_draft("stale", "F1"))
        cli.agent.session.clear_proposed_patch_draft()
        return [_tool_message("error", "F1")]

    cli._run_agent_request = MagicMock(side_effect=run_retry)

    cli.workflow_controller._handle_finding_retry(finding, "audit", backlog)

    workspace = cli.workspace_manager.load_workspace()
    assert workspace.patch_items == []
    assert finding.status.value == "failed"


def test_zero_finding_review_commits_staged_patch(cli: CLI) -> None:
    scope = CodeScope(
        kind=CodeScopeKind.SINGLE_FILE,
        source_roots=[],
        focus_files=["src/main/java/demo/User.java"],
        is_locked=True,
    )

    def run_agent(*args, **kwargs):
        cli.agent.session.set_proposed_patch_draft(_patch_draft("zero-fix", "F1"))
        return [_tool_message("ok", "F1")]

    cli._run_agent_request = MagicMock(side_effect=run_agent)

    cli.workflow_controller._handle_zero_finding_review("audit", scope)

    workspace = cli.workspace_manager.load_workspace()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "zero-fix"
    cli.renderer.print_no_issue_panel.assert_not_called()


def test_cli_wires_llm_intent_classifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        content = "code_audit"

    class FakeLLM:
        def chat(self, messages, tools=None, extra_body=None, on_token=None, on_reasoning_token=None):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)

    cli_obj = CLI(tmp_path)

    assert cli_obj.intent_detector is not None
    assert cli_obj.intent_detector.classify_with_llm is not None
    assert cli_obj.intent_detector.detect_intent("@Foo.java check code", False) is IntentType.CODE_AUDIT


def test_handle_chat_routes_llm_code_audit_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        content = "code_audit"

    class FakeLLM:
        def chat(self, messages, tools=None, extra_body=None, on_token=None, on_reasoning_token=None):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = CLI(tmp_path)
    cli_obj.workflow_controller.handle_code_audit = MagicMock()
    cli_obj.workflow_controller.handle_general_chat = MagicMock()

    cli_obj.workflow_controller.handle_chat("@Foo.java check code")

    cli_obj.workflow_controller.handle_code_audit.assert_called_once_with("@Foo.java check code")
    cli_obj.workflow_controller.handle_general_chat.assert_not_called()


def test_handle_chat_filters_patch_intent_without_pending_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        content = "patch_revise"

    class FakeLLM:
        def chat(self, messages, tools=None, extra_body=None, on_token=None, on_reasoning_token=None):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = CLI(tmp_path)
    cli_obj.workflow_controller.handle_general_chat = MagicMock()
    cli_obj.workflow_controller.handle_patch_revise = MagicMock()
    cli_obj.workflow_controller.handle_patch_explain = MagicMock()

    cli_obj.workflow_controller.handle_chat("revise this patch")

    cli_obj.workflow_controller.handle_general_chat.assert_called_once_with("revise this patch")
    cli_obj.workflow_controller.handle_patch_revise.assert_not_called()
    cli_obj.workflow_controller.handle_patch_explain.assert_not_called()
