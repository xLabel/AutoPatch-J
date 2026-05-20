from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autopatch_j.cli.app import AutoPatchCli
from autopatch_j.cli.command_router import CommandRouter
from autopatch_j.core.patching import SearchReplacePatchDraft
from autopatch_j.core.patching import SyntaxCheckResult
from autopatch_j.core.domain import (
    ReviewWorkspace,
    FindingTask,
    CodeScope,
    CodeScopeKind,
    ConversationRoute,
    IntentType,
    PatchDraftSnapshot,
    ReviewPatchItem,
    PatchReviewStatus,
    WorkspaceStatus,
)


@pytest.fixture
def cli(tmp_path: Path) -> AutoPatchCli:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.renderer = MagicMock()
    cli_obj.command_router = CommandRouter(cli_obj.command_handlers, cli_obj.renderer)
    cli_obj.initialize_runtime(cli_obj.repo_root)
    return cli_obj


def _item(
    item_id: str,
    file_path: str = "src/main/java/demo/User.java",
    finding_id: str = "F1",
) -> ReviewPatchItem:
    return ReviewPatchItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=[finding_id],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftSnapshot(
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


def _patch_draft(new_string: str, finding_id: str = "F1") -> SearchReplacePatchDraft:
    return SearchReplacePatchDraft(
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


def _finding(finding_id: str = "F1") -> FindingTask:
    return FindingTask(
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


def test_handle_status_does_not_crash_with_pending_patch(cli: AutoPatchCli) -> None:
    # Setup workspace with a pending patch
    workspace = ReviewWorkspace(
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
    cli.runtime.workspace_manager.save(workspace)

    # This should not raise AttributeError
    cli.command_handlers.handle_status()

    cli.renderer.print_panel.assert_called_once()


def test_handle_status_includes_output_mode(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_status()

    table = cli.renderer.print_panel.call_args.args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]

    assert "调试模式" in cells
    assert "关闭" in cells
    assert "日志模式" not in cells
    assert "输出模式" not in cells
    assert "静态扫描器" not in cells


def test_handle_doctor_renders_runtime_diagnostics(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_doctor()

    table = cli.renderer.print_panel.call_args.args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]

    assert "LLM API Key" in cells
    assert "Semgrep" in cells
    assert "Tree-sitter" in cells


def test_handle_patch_explain_does_not_crash(cli: AutoPatchCli) -> None:
    # Setup workspace with a pending patch
    workspace = ReviewWorkspace(
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
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.agent.perform_patch_explain = MagicMock(return_value="")

    # This should not raise AttributeError
    cli.input_router.handle_patch_explain("explain this")

    cli.runtime.agent.perform_patch_explain.assert_called_once()


def test_handle_patch_revise_replaces_only_current_patch(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
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
    cli.runtime.workspace_manager.save(workspace)
    replacement = SearchReplacePatchDraft(
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
        cli.runtime.agent.session.set_revised_patch_draft(replacement)
        return "done"

    cli.runtime.agent.perform_patch_revise = MagicMock(side_effect=revise_current)

    cli.input_router.handle_patch_revise("rewrite current")

    updated = cli.runtime.workspace_manager.load()
    assert [item.item_id for item in updated.patch_items] == ["item-1", "item-2"]
    assert updated.current_patch_index == 0
    assert updated.patch_items[0].draft.new_string == "better"
    assert updated.patch_items[1].file_path == "src/main/java/demo/UserService.java"
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli.renderer.print_agent_text.assert_any_call("已更新当前补丁，后续补丁保持不变。")


def test_handle_patch_revise_keeps_queue_when_no_revision_created(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
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
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.agent.perform_patch_revise = MagicMock(return_value="no revision")

    cli.input_router.handle_patch_revise("explain only")

    updated = cli.runtime.workspace_manager.load()
    assert [item.item_id for item in updated.patch_items] == ["item-1", "item-2"]
    assert updated.patch_items[0].draft.rationale == "fix F1"
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli.renderer.print_agent_text.assert_any_call("未生成修订补丁，当前补丁保持不变。")


def test_process_single_finding_commits_staged_patch_after_success(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]

    def run_agent(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("better", "F1"))
        return [_tool_message("ok", "F1")]

    cli.agent_runner.run = MagicMock(side_effect=run_agent)

    cli.input_router.code_audit_workflow._process_single_finding(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "better"
    assert finding.status.value == "patch_ready"
    assert cli.runtime.agent.session.proposed_patch_draft is None
    assert cli.agent_runner.run.call_args.kwargs["suppress_answer_output"] is True


def test_process_single_finding_does_not_commit_without_staged_patch(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]
    cli.agent_runner.run = MagicMock(return_value=[_tool_message("ok", "F1")])

    cli.input_router.code_audit_workflow._process_single_finding(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert workspace.patch_items == []
    assert finding.status.value == "failed"
    assert finding.last_error_code == "NO_PROPOSED_PATCH_DRAFT"
    assert cli.agent_runner.run.call_args.kwargs["suppress_answer_output"] is True


def test_finding_retry_commits_only_retry_patch(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]
    old_draft = _patch_draft("stale", "F1")
    cli.runtime.agent.session.set_proposed_patch_draft(old_draft)

    def run_retry(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("retry-fix", "F1"))
        return [_tool_message("ok", "F1")]

    cli.agent_runner.run = MagicMock(side_effect=run_retry)

    cli.input_router.code_audit_workflow._handle_finding_retry(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "retry-fix"
    assert finding.status.value == "patch_ready"


def test_finding_retry_failure_does_not_commit_patch(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]

    def run_retry(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("stale", "F1"))
        cli.runtime.agent.session.clear_proposed_patch_draft()
        return [_tool_message("error", "F1")]

    cli.agent_runner.run = MagicMock(side_effect=run_retry)

    cli.input_router.code_audit_workflow._handle_finding_retry(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert workspace.patch_items == []
    assert finding.status.value == "failed"


def test_zero_finding_review_commits_staged_patch(cli: AutoPatchCli) -> None:
    scope = CodeScope(
        kind=CodeScopeKind.SINGLE_FILE,
        source_roots=[],
        focus_files=["src/main/java/demo/User.java"],
        is_locked=True,
    )

    def run_agent(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("zero-fix", "F1"))
        return [_tool_message("ok", "F1")]

    cli.agent_runner.run = MagicMock(side_effect=run_agent)

    cli.input_router.code_audit_workflow._handle_zero_finding_review("audit", scope)

    workspace = cli.runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "zero-fix"
    cli.renderer.print_no_issue_panel.assert_not_called()


def test_cli_wires_llm_intent_classifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        content = "code_audit"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)

    cli_obj = AutoPatchCli(tmp_path)

    assert cli_obj.runtime.intent_detector is not None
    assert cli_obj.runtime.intent_detector.classify_with_llm is not None
    assert cli_obj.runtime.intent_detector.classify("@Foo.java check code", False) is IntentType.CODE_AUDIT


def test_handle_chat_routes_llm_code_audit_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        content = "code_audit"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.input_router.handle_code_audit = MagicMock()
    cli_obj.input_router.handle_general_chat = MagicMock()

    cli_obj.input_router.handle_chat("@Foo.java check code")

    cli_obj.input_router.handle_code_audit.assert_called_once_with("@Foo.java check code")
    cli_obj.input_router.handle_general_chat.assert_not_called()


def test_handle_chat_filters_patch_intent_without_pending_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        content = "patch_revise"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.input_router.handle_general_chat = MagicMock()
    cli_obj.input_router.handle_patch_revise = MagicMock()
    cli_obj.input_router.handle_patch_explain = MagicMock()

    cli_obj.input_router.handle_chat("revise this patch")

    cli_obj.input_router.handle_general_chat.assert_called_once_with("revise this patch")
    cli_obj.input_router.handle_patch_revise.assert_not_called()
    cli_obj.input_router.handle_patch_explain.assert_not_called()


def test_handle_chat_switches_new_task_with_pending_review(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
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
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.conversation_router = MagicMock()
    cli.runtime.conversation_router.classify_route.return_value = ConversationRoute.NEW_TASK
    cli.runtime.intent_detector = MagicMock()
    cli.runtime.intent_detector.classify.return_value = IntentType.GENERAL_CHAT
    cli.input_router.handle_general_chat = MagicMock()

    cli.input_router.handle_chat("@Foo.java explain code")

    assert cli.runtime.workspace_manager.load().has_pending_patch() is False
    cli.renderer.print_agent_text.assert_any_call("已切换到新任务")
    cli.input_router.handle_general_chat.assert_called_once_with("@Foo.java explain code")


def test_handle_code_explain_without_scope_uses_project_context(tmp_path: Path) -> None:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "App.java").write_text("class App {}", encoding="utf-8")
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.renderer = MagicMock()
    cli_obj.command_router = CommandRouter(cli_obj.command_handlers, cli_obj.renderer)
    cli_obj.initialize_runtime(cli_obj.repo_root)
    cli_obj.agent_runner.run = MagicMock(return_value=[])
    cli_obj.runtime.agent.perform_code_explain = MagicMock(return_value="项目说明")

    cli_obj.input_router.handle_code_explain("这个项目是干什么的")

    kwargs = cli_obj.agent_runner.run.call_args.kwargs
    assert kwargs["answer_intent"] is IntentType.CODE_EXPLAIN
    assert kwargs["plain_answer"] is True
    assert "show_chat_anchors" not in kwargs
    cli_obj.renderer.print_user_anchor.assert_not_called()
    kwargs["agent_call"]("prompt")
    call_kwargs = cli_obj.runtime.agent.perform_code_explain.call_args.kwargs
    assert call_kwargs["scope"].kind is CodeScopeKind.PROJECT
    assert "项目轻量上下文" in call_kwargs["project_context"]


def test_general_chat_does_not_print_chat_anchors(cli: AutoPatchCli) -> None:
    cli.agent_runner.run = MagicMock(return_value=[])

    cli.input_router.handle_general_chat("leetcode 第一题题解")

    kwargs = cli.agent_runner.run.call_args.kwargs
    assert kwargs["answer_intent"] is IntentType.GENERAL_CHAT
    assert kwargs["plain_answer"] is True
    assert "show_chat_anchors" not in kwargs
    cli.renderer.print_user_anchor.assert_not_called()


def test_reset_clears_project_state_and_requires_reinit(cli: AutoPatchCli) -> None:
    state_dir = cli.repo_root / ".autopatch-j"
    findings_dir = state_dir / "findings"
    runtime_dir = state_dir / "runtime" / "semgrep"
    findings_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "workspace.json").write_text("{}", encoding="utf-8")
    (state_dir / "memory.json").write_text("{}", encoding="utf-8")
    (state_dir / "history.txt").write_text("/status\n", encoding="utf-8")
    (findings_dir / "scan-demo.json").write_text("{}", encoding="utf-8")
    (runtime_dir / "cache.txt").write_text("cache", encoding="utf-8")
    cli.runtime.agent.session.append_memory_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Java Optional 怎么用",
        answer="Optional answer",
    )

    cli.command_handlers.handle_reset()

    assert state_dir.exists()
    assert list(state_dir.iterdir()) == []
    assert cli.runtime is None
    assert cli.input_router is None
    assert cli.agent_runner is None
    cli.renderer.print_success.assert_called_once_with("项目状态已重置，请执行 /init 重新初始化。")


def test_handle_chat_maps_pending_code_explain_without_scope_to_patch_explain(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
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
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.conversation_router = MagicMock()
    cli.runtime.conversation_router.classify_route.return_value = ConversationRoute.REVIEW_CONTINUE
    cli.runtime.intent_detector = MagicMock()
    cli.runtime.intent_detector.classify.return_value = IntentType.CODE_EXPLAIN
    cli.input_router.handle_patch_explain = MagicMock()
    cli.input_router.handle_code_explain = MagicMock()

    cli.input_router.handle_chat("解释一下")

    cli.input_router.handle_patch_explain.assert_called_once_with("解释一下")
    cli.input_router.handle_code_explain.assert_not_called()


def test_handle_chat_keeps_pending_patch_revise_route(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
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
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.conversation_router = MagicMock()
    cli.runtime.conversation_router.classify_route.return_value = ConversationRoute.REVIEW_CONTINUE
    cli.runtime.intent_detector = MagicMock()
    cli.runtime.intent_detector.classify.return_value = IntentType.PATCH_REVISE
    cli.input_router.handle_patch_revise = MagicMock()

    cli.input_router.handle_chat("重新写这个补丁")

    cli.input_router.handle_patch_revise.assert_called_once_with("重新写这个补丁")
