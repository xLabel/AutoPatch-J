"""用临时工作台和显式 mock 隔离模型与扫描，保留真实审核和补丁判断。"""

from __future__ import annotations

from collections.abc import Callable
import io
import pathlib
from unittest import mock

import pytest
from rich import console

from autopatch_j import config
from autopatch_j.agent import result
from autopatch_j.cli import agent_stream_presenter
from autopatch_j.cli import app
from autopatch_j.cli import commands
from autopatch_j.core import finding as core_finding
from autopatch_j.core.domain import audit
from autopatch_j.core.domain import intent
from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.core.patching import types as patching_types
from autopatch_j.core.user_input import diagnostics
from autopatch_j.tools import contract


def _table_text(table) -> str:
    output = console.Console(file=io.StringIO(), record=True, width=200)
    output.print(table)
    return output.export_text()


@pytest.fixture(name="cli_factory")
def make_cli_factory(
    request: pytest.FixtureRequest,
) -> Callable[[pathlib.Path], app.AutoPatchCli]:
    instances: list[app.AutoPatchCli] = []

    def build(repo_root: pathlib.Path) -> app.AutoPatchCli:
        cli_obj = app.AutoPatchCli(repo_root)
        instances.append(cli_obj)
        return cli_obj

    def close_instances() -> None:
        for cli_obj in reversed(instances):
            cli_obj.clear_runtime()

    request.addfinalizer(close_instances)
    return build


@pytest.fixture(name="cli_renderer")
def make_cli_renderer() -> mock.MagicMock:
    return mock.MagicMock()


@pytest.fixture(autouse=True, name="_use_fake_cli_llm")
def make__use_fake_cli_llm(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_factory: Callable[[], object],
    cli_renderer: mock.MagicMock,
) -> None:
    monkeypatch.setattr(
        "autopatch_j.cli.render.CliRenderer", lambda: cli_renderer
    )
    monkeypatch.setattr(
        "autopatch_j.llm.factory.build_model_runtime",
        fake_llm_factory,
    )


@pytest.fixture(name="cli")
def make_cli(
    tmp_path: pathlib.Path,
    cli_factory: Callable[[pathlib.Path], app.AutoPatchCli],
) -> app.AutoPatchCli:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    return cli_factory(tmp_path)


def _item(
    item_id: str,
    file_path: str = "src/main/java/demo/User.java",
    finding_id: str = "F1",
) -> domain_workspace.ReviewPatchItem:
    return domain_workspace.ReviewPatchItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=[finding_id],
        status=domain_workspace.PatchReviewStatus.PENDING,
        draft=domain_workspace.PatchDraftSnapshot(
            file_path=file_path,
            old_string="old",
            new_string="new",
            diff="diff",
            match_region=core_finding.SourceRegion(1, 1, 1, 4, 0, 3),
            message="ok",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale=f"fix {finding_id}",
            associated_finding_id=finding_id,
            source_scan_id="scan-1",
            target_finding=_target_identity(file_path),
        ),
    )


def _patch_draft(
    new_string: str, finding_id: str | None = "F1"
) -> patching_types.SearchReplacePatchDraft:
    return patching_types.SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string=new_string,
        diff=f"diff {new_string}",
        match_region=core_finding.SourceRegion(1, 1, 1, 4, 0, 3),
        validation=patching_types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale=f"fix {finding_id}",
        associated_finding_id=finding_id,
        source_scan_id="scan-1" if finding_id else None,
        target_finding=(
            _target_identity("src/main/java/demo/User.java")
            if finding_id
            else None
        ),
    )


def _target_identity(file_path: str) -> core_finding.FindingIdentity:
    return core_finding.FindingIdentity(
        fingerprint=f"apj-v1:{"a" * 64}:1",
        check_id="demo.rule",
        path=file_path,
        region=core_finding.SourceRegion(1, 1, 1, 4, 0, 3),
    )


def _finding(finding_id: str = "F1") -> audit.FindingTask:
    return audit.FindingTask(
        finding_id=finding_id,
        file_path="src/main/java/demo/User.java",
        check_id="demo.rule",
        start_line=1,
        end_line=1,
        message="demo finding",
        snippet="old",
    )


def _tool_message(
    status: str = "ok", finding_id: str = "F1"
) -> contract.ToolExecutionResult:
    return contract.ToolExecutionResult(
        status=status,
        message="result",
        name="propose_patch",
        payload={
            "associated_finding_id": finding_id,
            "file_path": "src/main/java/demo/User.java",
        },
    )


def _presented(observations=None, display_answer=""):
    return agent_stream_presenter.PresentedAgentResult(
        raw_answer=display_answer,
        display_answer=display_answer,
        observations=tuple(observations or ()),
    )


def _agent_result(answer="", observations=None):
    return result.AgentRunResult(
        final_answer=answer, observations=tuple(observations or ())
    )


def test_handle_status_does_not_crash_with_pending_patch(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
) -> None:
    # Setup workspace with a pending patch
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)

    # This should not raise AttributeError
    cli.command_handlers.handle_status()

    cli_renderer.print_panel.assert_called_once()


def test_handle_status_includes_output_mode(
    cli_renderer: mock.MagicMock, cli: app.AutoPatchCli
) -> None:
    cli.command_handlers.handle_status()

    table = cli_renderer.print_panel.call_args.args[0]
    cells = _table_text(table)

    assert "调试模式" in cells
    assert "关闭" in cells
    assert "LLM API Key" in cells
    assert "Semgrep" in cells
    assert "Tree-sitter" in cells
    assert "日志模式" not in cells
    assert "输出模式" not in cells
    assert "静态扫描器" not in cells


def test_handle_status_renders_without_runtime(
    cli_renderer: mock.MagicMock, cli: app.AutoPatchCli
) -> None:
    cli.clear_runtime()

    cli.command_handlers.handle_status()

    table = cli_renderer.print_panel.call_args.args[0]
    cells = _table_text(table)

    assert "工作区" in cells
    assert "未初始化" in cells
    assert "LLM API Key" in cells
    assert "Semgrep" in cells
    assert "Tree-sitter" in cells


def test_clear_runtime_shuts_down_agent(cli: app.AutoPatchCli) -> None:
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    assert cli.runtime is not None
    shutdown = mock.MagicMock()
    cli_runtime.model_runtime.close = shutdown

    cli.clear_runtime()

    assert cli.runtime is None
    assert cli.input_router is None
    shutdown.assert_called_once_with()


def test_doctor_command_is_removed(
    cli_renderer: mock.MagicMock, cli: app.AutoPatchCli
) -> None:
    cli.command_router.handle_command("/doctor")

    cli_renderer.print_error.assert_called_once_with("未知命令：/doctor")


def test_command_router_parses_and_passes_shlex_arguments(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_handle_memory = mock.MagicMock()
    monkeypatch.setattr(
        cli.command_handlers, "handle_memory", mock_handle_memory
    )

    cli.command_router.handle_command('/memory show "memory item"')

    mock_handle_memory.assert_called_once_with(["show", "memory item"])


def test_command_router_rejects_arguments_for_argumentless_command(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handle_status = mock.MagicMock()
    monkeypatch.setattr(
        cli.command_handlers, "handle_status", mock_handle_status
    )

    cli.command_router.handle_command("/status extra")

    mock_handle_status.assert_not_called()
    cli_renderer.print_error.assert_called_once_with("命令 /status 不接受参数")


def test_command_router_reports_invalid_shell_quoting(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handle_memory = mock.MagicMock()
    monkeypatch.setattr(
        cli.command_handlers, "handle_memory", mock_handle_memory
    )

    cli.command_router.handle_command('/memory show "unterminated')

    mock_handle_memory.assert_not_called()
    assert "命令解析失败" in cli_renderer.print_error.call_args.args[0]


def test_help_uses_registered_command_descriptions(
    cli_renderer: mock.MagicMock, cli: app.AutoPatchCli
) -> None:
    cli.command_handlers.handle_help()

    table = cli_renderer.print_table.call_args_list[0].args[0]
    cells = _table_text(table)
    expected = [
        item
        for command in commands.CLI_COMMANDS
        if command.show_in_help
        for item in (command.name, command.help_description)
    ]

    for item in expected:
        assert item in cells


def test_handle_patch_explain_does_not_crash(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Setup workspace with a pending patch
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_perform_patch_explain = mock.MagicMock(return_value=_agent_result())
    monkeypatch.setattr(
        cli_runtime.agent, "perform_patch_explain", mock_perform_patch_explain
    )

    # This should not raise AttributeError
    cli_input_router.handle_patch_explain("explain this")

    mock_perform_patch_explain.assert_called_once()


def test_handle_patch_revise_replaces_only_current_patch(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
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
    cli_runtime.workspace_manager.save(workspace)
    replacement = patching_types.SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string="better",
        diff="better diff",
        match_region=core_finding.SourceRegion(1, 1, 1, 4, 0, 3),
        validation=patching_types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="better fix",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_finding=_target_identity("src/main/java/demo/User.java"),
    )

    def revise_current(*args, **kwargs):
        del args, kwargs
        cli_runtime.agent.session.set_revised_patch_draft(replacement)
        return _agent_result("done")

    mock_perform_patch_revise = mock.MagicMock(side_effect=revise_current)
    monkeypatch.setattr(
        cli_runtime.agent, "perform_patch_revise", mock_perform_patch_revise
    )
    cli_input_router.handle_patch_revise("rewrite current")

    updated = cli_runtime.workspace_manager.load()
    assert [item.item_id for item in updated.patch_items] == [
        "item-1",
        "item-2",
    ]
    assert updated.current_patch_index == 0
    assert updated.patch_items[0].draft.new_string == "better"
    assert (
        updated.patch_items[1].file_path
        == "src/main/java/demo/UserService.java"
    )
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli_renderer.print_agent_text.assert_any_call(
        "已更新当前补丁，后续补丁保持不变。"
    )


def test_handle_patch_revise_keeps_queue_when_no_revision_created(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
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
    cli_runtime.workspace_manager.save(workspace)
    mock_perform_patch_revise = mock.MagicMock(
        return_value=_agent_result("no revision")
    )
    monkeypatch.setattr(
        cli_runtime.agent, "perform_patch_revise", mock_perform_patch_revise
    )

    cli_input_router.handle_patch_revise("explain only")

    updated = cli_runtime.workspace_manager.load()
    assert [item.item_id for item in updated.patch_items] == [
        "item-1",
        "item-2",
    ]
    assert updated.patch_items[0].draft.rationale == "fix F1"
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli_renderer.print_agent_text.assert_any_call(
        "未生成修订补丁，当前补丁保持不变。"
    )


def test_handle_patch_revise_persists_binding_rejection_summary(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    invalid_replacement = _patch_draft("invalid", finding_id="F2")

    def revise_with_invalid_binding(*args, **kwargs):
        del args, kwargs
        cli_runtime.agent.session.set_revised_patch_draft(invalid_replacement)
        return _agent_result("done")

    mock_perform_patch_revise = mock.MagicMock(
        side_effect=revise_with_invalid_binding
    )
    monkeypatch.setattr(
        cli_runtime.agent, "perform_patch_revise", mock_perform_patch_revise
    )

    cli_input_router.handle_patch_revise("rewrite current")

    current = cli_runtime.workspace_manager.load().current_patch()
    assert current is not None
    assert current.draft.new_string == "new"
    expected = "修订补丁未应用：当前 finding binding 校验失败，原补丁保持不变。"
    cli_renderer.print_error.assert_any_call(expected)


def test_process_single_finding_commits_staged_patch_after_success(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    finding = _finding("F1")
    backlog = [finding]

    def run_agent(*args, **kwargs):
        del args, kwargs
        cli_runtime.agent.session.set_proposed_patch_draft(
            _patch_draft("better", "F1")
        )
        return _presented([_tool_message("ok", "F1")])

    mock_run = mock.MagicMock(side_effect=run_agent)
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)

    # 只检查 finding 入队资格，扫描和输入分类由独立用例覆盖。
    # pylint: disable-next=protected-access
    cli_input_router.code_audit_workflow._process_single_finding(
        finding, "audit", backlog
    )

    workspace = cli_runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "better"
    assert finding.status.value == "patch_ready"
    assert cli_runtime.agent.session.proposed_patch_draft is None
    assert mock_run.call_args.kwargs["suppress_answer_output"] is True


def test_process_single_finding_does_not_commit_without_staged_patch(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    finding = _finding("F1")
    backlog = [finding]
    mock_run = mock.MagicMock(
        return_value=_presented([_tool_message("ok", "F1")])
    )
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)

    # 只检查 finding 入队资格，扫描和输入分类由独立用例覆盖。
    # pylint: disable-next=protected-access
    cli_input_router.code_audit_workflow._process_single_finding(
        finding, "audit", backlog
    )

    workspace = cli_runtime.workspace_manager.load()
    assert not workspace.patch_items
    assert finding.status.value == "failed"
    assert finding.last_error_code == "NO_PROPOSED_PATCH_DRAFT"
    assert mock_run.call_args.kwargs["suppress_answer_output"] is True


def test_process_single_finding_rejects_patch_for_different_finding(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    finding = _finding("F1")
    backlog = [finding]

    def run_agent(*args, **kwargs):
        del args, kwargs
        cli_runtime.agent.session.set_proposed_patch_draft(
            _patch_draft("wrong-finding", "F2")
        )
        return _presented([_tool_message("ok", "F1")])

    mock_run = mock.MagicMock(side_effect=run_agent)
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)

    # 只检查 finding 入队资格，扫描和输入分类由独立用例覆盖。
    # pylint: disable-next=protected-access
    cli_input_router.code_audit_workflow._process_single_finding(
        finding, "audit", backlog
    )

    workspace = cli_runtime.workspace_manager.load()
    assert not workspace.patch_items
    assert finding.status.value == "failed"
    assert finding.last_error_code == "NO_PROPOSED_PATCH_DRAFT"


def test_code_audit_stops_at_batch_limit(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    backlog = [_finding("F1"), _finding("F2"), _finding("F3")]
    workflow = cli_input_router.code_audit_workflow
    mock__prepare_audit_workspace = mock.MagicMock(return_value=backlog)
    monkeypatch.setattr(
        workflow, "_prepare_audit_workspace", mock__prepare_audit_workspace
    )

    def mark_failed(
        finding: audit.FindingTask, text: str, backlog: list[audit.FindingTask]
    ) -> None:
        del text
        cli_runtime.backlog_manager.mark_failed(
            backlog, finding.finding_id, None, None
        )

    mock__process_single_finding = mock.MagicMock(side_effect=mark_failed)
    monkeypatch.setattr(
        workflow, "_process_single_finding", mock__process_single_finding
    )
    monkeypatch.setattr(config.GLOBAL_CONFIG, "audit_batch_limit", 2)

    workflow.handle_code_audit("audit")

    assert mock__process_single_finding.call_count == 2
    assert backlog[0].status.value == "failed"
    assert backlog[1].status.value == "failed"
    assert backlog[2].is_pending()
    cli_renderer.print_agent_text.assert_any_call(
        "本轮已处理 2 个 finding，仍有 1 个待处理。请确认当前补丁后再次发起检查继续处理。"
    )


def test_code_audit_scanner_failure_persists_failed_turn(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    scope = domain_scope.CodeScope(
        kind=domain_scope.CodeScopeKind.PROJECT,
        source_roots=[],
        focus_files=[],
        is_locked=False,
    )
    mock_resolve = mock.MagicMock(return_value=scope)
    monkeypatch.setattr(cli_runtime.scope_service, "resolve", mock_resolve)
    mock_run_scan_and_save = mock.MagicMock(
        side_effect=RuntimeError("scanner unavailable")
    )
    monkeypatch.setattr(
        cli_runtime.scanner_runner, "run_scan_and_save", mock_run_scan_and_save
    )

    cli_input_router.code_audit_workflow.handle_code_audit("audit project")

    cli_renderer.print_error.assert_called_once_with("scanner unavailable")


def test_code_audit_displays_and_persists_stable_success_summary(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    scope = domain_scope.CodeScope(
        kind=domain_scope.CodeScopeKind.PROJECT,
        source_roots=[],
        focus_files=[],
        is_locked=False,
    )
    workflow = cli_input_router.code_audit_workflow
    mock_resolve = mock.MagicMock(return_value=scope)
    monkeypatch.setattr(cli_runtime.scope_service, "resolve", mock_resolve)
    mock__prepare_audit_workspace = mock.MagicMock(return_value=None)
    monkeypatch.setattr(
        workflow, "_prepare_audit_workspace", mock__prepare_audit_workspace
    )

    workflow.handle_code_audit("audit project")

    expected = "本轮代码审计结束：静态扫描未发现 finding，LLM 二次复核生成 0 个待审补丁。"
    cli_renderer.print_agent_text.assert_any_call(expected)


def test_finding_retry_commits_only_retry_patch(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    finding = _finding("F1")
    backlog = [finding]
    old_draft = _patch_draft("stale", "F1")
    cli_runtime.agent.session.set_proposed_patch_draft(old_draft)

    def run_retry(*args, **kwargs):
        del args, kwargs
        cli_runtime.agent.session.set_proposed_patch_draft(
            _patch_draft("retry-fix", "F1")
        )
        return _presented([_tool_message("ok", "F1")])

    mock_run = mock.MagicMock(side_effect=run_retry)
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)

    # 只检查有限重读后的提交资格，不重复扫描和输入路由。
    # pylint: disable-next=protected-access
    cli_input_router.code_audit_workflow._handle_finding_retry(
        finding, "audit", backlog
    )

    workspace = cli_runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "retry-fix"
    assert finding.status.value == "patch_ready"


def test_finding_retry_failure_does_not_commit_patch(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    finding = _finding("F1")
    backlog = [finding]

    def run_retry(*args, **kwargs):
        del args, kwargs
        cli_runtime.agent.session.set_proposed_patch_draft(
            _patch_draft("stale", "F1")
        )
        cli_runtime.agent.session.clear_proposed_patch_draft()
        return _presented([_tool_message("error", "F1")])

    mock_run = mock.MagicMock(side_effect=run_retry)
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)

    # 只检查有限重读后的提交资格，不重复扫描和输入路由。
    # pylint: disable-next=protected-access
    cli_input_router.code_audit_workflow._handle_finding_retry(
        finding, "audit", backlog
    )

    workspace = cli_runtime.workspace_manager.load()
    assert not workspace.patch_items
    assert finding.status.value == "failed"


def test_zero_finding_review_commits_staged_patch(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    scope = domain_scope.CodeScope(
        kind=domain_scope.CodeScopeKind.SINGLE_FILE,
        source_roots=[],
        focus_files=["src/main/java/demo/User.java"],
        is_locked=True,
    )

    def run_agent(*args, **kwargs):
        del args, kwargs
        cli_runtime.agent.session.set_proposed_patch_draft(
            _patch_draft("zero-fix", None)
        )
        return _presented([_tool_message("ok", "F1")])

    mock_run = mock.MagicMock(side_effect=run_agent)
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)

    # 直接检查零 finding 复核的入队与展示，不重复扫描。
    # pylint: disable-next=protected-access
    cli_input_router.code_audit_workflow._handle_zero_finding_review(
        "audit", scope
    )

    workspace = cli_runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "zero-fix"
    cli_renderer.print_no_issue_panel.assert_not_called()


def test_review_apply_failure_keeps_current_patch_pending(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_handle_apply = mock.MagicMock(
        return_value=patching_types.PatchApplicationResult(
            applied=False,
            message="apply failed",
            error_code="SOURCE_CHANGED",
        )
    )
    monkeypatch.setattr(cli.command_handlers, "handle_apply", mock_handle_apply)

    cli_input_router.handle_review_input("apply", workspace.patch_items[0])

    updated = cli_runtime.workspace_manager.load()
    assert updated.current_patch_index == 0
    assert (
        updated.patch_items[0].status
        is domain_workspace.PatchReviewStatus.PENDING
    )


def test_successful_apply_rebases_later_same_file_pending_patch(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.repo_root is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    file_path = "src/main/java/demo/User.java"
    source_file = cli.repo_root / file_path
    source_file.parent.mkdir(parents=True, exist_ok=True)
    with source_file.open("w", encoding="utf-8", newline="") as stream:
        stream.write("first();\nsecond();\n")
    first_build = cli_runtime.patch_engine.create_draft(
        file_path,
        "first();",
        "first();\nextra();",
    )
    second_build = cli_runtime.patch_engine.create_draft(
        file_path,
        "second();",
        "fixedSecond();",
    )
    first_draft = patching_types.SearchReplacePatchDraft(
        file_path=file_path,
        old_string="first();",
        new_string="first();\nextra();",
        diff=first_build.diff,
        match_region=first_build.match_region,
        validation=patching_types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
    )
    second_target = core_finding.FindingIdentity(
        fingerprint=f"apj-v1:{"b" * 64}:1",
        check_id="demo.second",
        path=file_path,
        region=second_build.match_region,
    )
    second_draft = patching_types.SearchReplacePatchDraft(
        file_path=file_path,
        old_string="second();",
        new_string="fixedSecond();",
        diff=second_build.diff,
        match_region=second_build.match_region,
        validation=patching_types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        associated_finding_id="F2",
        source_scan_id="scan-1",
        target_finding=second_target,
    )
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.SINGLE_FILE,
            source_roots=[],
            focus_files=[file_path],
            is_locked=True,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            domain_workspace.ReviewPatchItem(
                item_id="item-1",
                file_path=file_path,
                finding_ids=[],
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot.from_patch_draft(
                    first_draft
                ),
            ),
            domain_workspace.ReviewPatchItem(
                item_id="item-2",
                file_path=file_path,
                finding_ids=["F2"],
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot.from_patch_draft(
                    second_draft
                ),
            ),
        ],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_patch_verifier = mock.MagicMock()
    monkeypatch.setattr(cli_runtime, "patch_verifier", mock_patch_verifier)
    mock_patch_verifier.verify_finding_resolved.return_value = (
        patching_types.VerificationResult(
            patching_types.VerificationOutcome.RESOLVED,
            "验证通过。",
        )
    )
    mock_patch_verifier.verify_syntax.return_value = (
        patching_types.SyntaxCheckResult(
            status="ok",
            message="ok",
        )
    )
    cli_runtime.agent.session.record_runtime_patch_constraint(
        "item-1",
        "当前补丁不要使用三元表达式",
    )
    cli_runtime.agent.session.record_runtime_patch_constraint(
        "item-2",
        "当前补丁必须保留日志",
    )

    cli_input_router.handle_review_input("apply", workspace.patch_items[0])

    rebased_workspace = cli_runtime.workspace_manager.load()
    rebased_item = rebased_workspace.current_patch()
    assert (
        rebased_workspace.patch_items[0].status
        is domain_workspace.PatchReviewStatus.APPLIED
    )
    assert rebased_item is not None
    assert rebased_item.item_id == "item-2"
    assert rebased_item.draft.error_code is None
    assert rebased_item.draft.match_region.start_line == 3
    assert rebased_item.draft.match_region.start_offset == (
        second_build.match_region.start_offset
        + len("\nextra();".encode("utf-8"))
    )
    assert rebased_item.draft.target_finding is not None
    assert (
        rebased_item.draft.target_finding.fingerprint
        == second_target.fingerprint
    )
    assert (
        rebased_item.draft.target_finding.region
        == rebased_item.draft.match_region
    )
    assert rebased_item.draft.source_scan_id == "scan-1"
    assert rebased_item.draft.associated_finding_id == "F2"
    assert "extra();" in rebased_item.draft.diff
    assert (
        cli_runtime.agent.session.build_runtime_patch_constraint_context(
            "item-1"
        )
        == ""
    )
    assert "必须保留日志" in (
        cli_runtime.agent.session.build_runtime_patch_constraint_context(
            "item-2"
        )
    )
    cli_renderer.print_agent_text.assert_any_call(
        "已重定位 1 个同文件待审补丁；0 个补丁需重新扫描。"
    )

    cli_input_router.handle_review_input("apply", rebased_item)

    final_workspace = cli_runtime.workspace_manager.load()
    assert (
        final_workspace.patch_items[1].status
        is domain_workspace.PatchReviewStatus.APPLIED
    )
    assert final_workspace.mode is domain_workspace.WorkspaceStatus.IDLE
    assert source_file.read_text(encoding="utf-8") == (
        "first();\nextra();\nfixedSecond();\n"
    )


def test_overlapping_pending_patch_becomes_stale_and_blocks_apply_and_revise(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.repo_root is not None
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    file_path = "src/main/java/demo/User.java"
    source_file = cli.repo_root / file_path
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("unsafe().trim();\n", encoding="utf-8")
    applied_build = cli_runtime.patch_engine.create_draft(
        file_path,
        "unsafe()",
        "safe()",
    )
    pending_build = cli_runtime.patch_engine.create_draft(
        file_path,
        "unsafe().trim()",
        "safeTrimmed()",
    )
    applied_draft = patching_types.SearchReplacePatchDraft(
        file_path=file_path,
        old_string="unsafe()",
        new_string="safe()",
        diff=applied_build.diff,
        match_region=applied_build.match_region,
        validation=patching_types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
    )
    pending_draft = patching_types.SearchReplacePatchDraft(
        file_path=file_path,
        old_string="unsafe().trim()",
        new_string="safeTrimmed()",
        diff=pending_build.diff,
        match_region=pending_build.match_region,
        validation=patching_types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
    )
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.SINGLE_FILE,
            source_roots=[],
            focus_files=[file_path],
            is_locked=True,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            domain_workspace.ReviewPatchItem(
                item_id="item-1",
                file_path=file_path,
                finding_ids=[],
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot.from_patch_draft(
                    applied_draft
                ),
            ),
            domain_workspace.ReviewPatchItem(
                item_id="item-2",
                file_path=file_path,
                finding_ids=[],
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot.from_patch_draft(
                    pending_draft
                ),
            ),
        ],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_patch_verifier = mock.MagicMock()
    monkeypatch.setattr(cli_runtime, "patch_verifier", mock_patch_verifier)
    mock_patch_verifier.verify_finding_resolved.return_value = (
        patching_types.VerificationResult(
            patching_types.VerificationOutcome.RESOLVED,
            "验证通过。",
        )
    )

    cli_input_router.handle_review_input("apply", workspace.patch_items[0])

    stale_workspace = cli_runtime.workspace_manager.load()
    stale_item = stale_workspace.current_patch()
    assert stale_item is not None
    assert stale_item.draft.error_code == "STALE_DRAFT"
    assert stale_item.draft.old_string == pending_draft.old_string
    assert stale_item.draft.diff == pending_draft.diff
    assert "相交" in stale_item.draft.message
    source_after_first_apply = source_file.read_bytes()

    cli_input_router.handle_review_input("apply", stale_item)

    assert source_file.read_bytes() == source_after_first_apply
    cli_renderer.print_error.assert_any_call(
        f"应用失败 [STALE_DRAFT]：{stale_item.draft.message}"
    )

    mock_run = mock.MagicMock()
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)
    cli_input_router.handle_patch_revise("try another replacement")

    mock_run.assert_not_called()
    cli_renderer.print_error.assert_any_call(
        "当前补丁绑定已失效，不能继续修订；请 discard，或 abort 后重新扫描。"
    )


def test_successful_apply_remains_applied_when_verification_fails(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.repo_root is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    file_path = "src/main/java/demo/User.java"
    source_file = cli.repo_root / file_path
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("old", encoding="utf-8")
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.SINGLE_FILE,
            source_roots=[],
            focus_files=[file_path],
            is_locked=True,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1", file_path, "F1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_patch_verifier = mock.MagicMock()
    monkeypatch.setattr(cli_runtime, "patch_verifier", mock_patch_verifier)
    mock_patch_verifier.verify_finding_resolved.return_value = (
        patching_types.VerificationResult(
            patching_types.VerificationOutcome.STILL_PRESENT,
            "验证未通过：目标仍存在。",
        )
    )

    cli_input_router.handle_review_input("apply", workspace.patch_items[0])

    updated = cli_runtime.workspace_manager.load()
    assert (
        updated.patch_items[0].status
        is domain_workspace.PatchReviewStatus.APPLIED
    )
    assert updated.mode is domain_workspace.WorkspaceStatus.IDLE
    assert source_file.read_text(encoding="utf-8") == "new"
    verification_args = (
        mock_patch_verifier.verify_finding_resolved.call_args.args
    )
    assert len(verification_args) == 2
    verified_draft, application_result = verification_args
    assert isinstance(application_result, patching_types.PatchApplicationResult)
    assert application_result.applied is True
    assert application_result.source_region == verified_draft.match_region
    assert application_result.changed_region is not None
    assert application_result.source_region is not None
    assert (
        application_result.changed_region.start_offset
        == application_result.source_region.start_offset
    )
    cli_renderer.print_error.assert_any_call("验证未通过：目标仍存在。")


def test_handle_chat_routes_llm_code_audit_intent(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_factory: Callable[[pathlib.Path], app.AutoPatchCli],
) -> None:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = cli_factory(tmp_path)
    cli_obj_input_router = cli_obj.input_router
    assert cli_obj_input_router is not None
    cli_obj_runtime = cli_obj.runtime
    assert cli_obj_runtime is not None
    cli_obj_runtime.intent_detector.classify_with_llm = (
        lambda text, pending: intent.IntentType.CODE_AUDIT
    )
    mock_handle_code_audit = mock.MagicMock()
    monkeypatch.setattr(
        cli_obj_input_router, "handle_code_audit", mock_handle_code_audit
    )
    mock_handle_general_chat = mock.MagicMock()
    monkeypatch.setattr(
        cli_obj_input_router, "handle_general_chat", mock_handle_general_chat
    )

    cli_obj_input_router.handle_chat("@Foo.java check code")

    mock_handle_code_audit.assert_called_once_with("@Foo.java check code")
    mock_handle_general_chat.assert_not_called()


def test_handle_chat_filters_patch_intent_without_pending_review(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_factory: Callable[[pathlib.Path], app.AutoPatchCli],
) -> None:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = cli_factory(tmp_path)
    cli_obj_input_router = cli_obj.input_router
    assert cli_obj_input_router is not None
    cli_obj_runtime = cli_obj.runtime
    assert cli_obj_runtime is not None
    cli_obj_runtime.intent_detector.classify_with_llm = (
        lambda text, pending: intent.IntentType.PATCH_REVISE
    )
    mock_handle_general_chat = mock.MagicMock()
    monkeypatch.setattr(
        cli_obj_input_router, "handle_general_chat", mock_handle_general_chat
    )
    mock_handle_patch_revise = mock.MagicMock()
    monkeypatch.setattr(
        cli_obj_input_router, "handle_patch_revise", mock_handle_patch_revise
    )
    mock_handle_patch_explain = mock.MagicMock()
    monkeypatch.setattr(
        cli_obj_input_router, "handle_patch_explain", mock_handle_patch_explain
    )

    cli_obj_input_router.handle_chat("revise this patch")

    mock_handle_general_chat.assert_called_once_with("revise this patch")
    mock_handle_patch_revise.assert_not_called()
    mock_handle_patch_explain.assert_not_called()


def test_handle_chat_switches_new_task_with_pending_review(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_conversation_router = mock.MagicMock()
    monkeypatch.setattr(
        cli_runtime, "conversation_router", mock_conversation_router
    )
    mock_conversation_router.classify_route_with_diagnostics.return_value = (
        diagnostics.RouteClassificationResult(
            route=intent.ConversationRoute.NEW_TASK,
            source="test",
        )
    )
    mock_intent_detector = mock.MagicMock()
    monkeypatch.setattr(cli_runtime, "intent_detector", mock_intent_detector)
    mock_intent_detector.classify_with_diagnostics.return_value = (
        diagnostics.IntentClassificationResult(
            intent=intent.IntentType.GENERAL_CHAT,
            source="test",
        )
    )
    mock_handle_general_chat = mock.MagicMock()
    monkeypatch.setattr(
        cli_input_router, "handle_general_chat", mock_handle_general_chat
    )

    cli_input_router.handle_chat("@Foo.java explain code")

    assert cli_runtime.workspace_manager.load().has_pending_patch() is False
    cli_renderer.print_agent_text.assert_any_call("已切换到新任务")
    mock_handle_general_chat.assert_called_once_with("@Foo.java explain code")


def test_handle_code_explain_without_scope_uses_project_context(
    cli_renderer: mock.MagicMock,
    tmp_path: pathlib.Path,
    cli_factory: Callable[[pathlib.Path], app.AutoPatchCli],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "App.java").write_text("class App {}", encoding="utf-8")
    cli_obj = cli_factory(tmp_path)
    cli_obj_agent_runner = cli_obj.agent_runner
    assert cli_obj_agent_runner is not None
    cli_obj_input_router = cli_obj.input_router
    assert cli_obj_input_router is not None
    cli_obj_runtime = cli_obj.runtime
    assert cli_obj_runtime is not None
    mock_run = mock.MagicMock(
        return_value=_presented(display_answer="项目说明")
    )
    monkeypatch.setattr(cli_obj_agent_runner, "run", mock_run)
    mock_perform_code_explain = mock.MagicMock(
        return_value=_agent_result("项目说明")
    )
    monkeypatch.setattr(
        cli_obj_runtime.agent, "perform_code_explain", mock_perform_code_explain
    )

    cli_obj_input_router.handle_code_explain("这个项目是干什么的")

    kwargs = mock_run.call_args.kwargs
    assert kwargs["answer_intent"] is intent.IntentType.CODE_EXPLAIN
    assert kwargs["plain_answer"] is True
    assert "show_chat_anchors" not in kwargs
    cli_renderer.print_user_anchor.assert_not_called()
    kwargs["agent_call"]("prompt")
    call_kwargs = mock_perform_code_explain.call_args.kwargs
    assert call_kwargs["scope"].kind is domain_scope.CodeScopeKind.PROJECT
    assert "项目轻量上下文" in call_kwargs["project_context"]


def test_general_chat_does_not_print_chat_anchors(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_agent_runner = cli.agent_runner
    assert cli_agent_runner is not None
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    mock_run = mock.MagicMock(return_value=_presented(display_answer="题解"))
    monkeypatch.setattr(cli_agent_runner, "run", mock_run)

    cli_input_router.handle_general_chat("leetcode 第一题题解")

    kwargs = mock_run.call_args.kwargs
    assert kwargs["answer_intent"] is intent.IntentType.GENERAL_CHAT
    assert kwargs["plain_answer"] is True
    assert "show_chat_anchors" not in kwargs
    cli_renderer.print_user_anchor.assert_not_called()


def test_reset_clears_project_state_and_requires_reinit(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
) -> None:
    assert cli.repo_root is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    state_dir = cli.repo_root / ".autopatch-j"
    findings_dir = state_dir / "findings"
    runtime_dir = state_dir / "runtime" / "semgrep"
    findings_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "workspace.json").write_text("{}", encoding="utf-8")
    (state_dir / "history.txt").write_text("/status\n", encoding="utf-8")
    (state_dir / "memory-export-demo.json").write_text("{}", encoding="utf-8")
    (state_dir / "memory_summary.md").write_text(
        "human review projection", encoding="utf-8"
    )
    (findings_dir / "scan-demo.json").write_text("{}", encoding="utf-8")
    (runtime_dir / "cache.txt").write_text("cache", encoding="utf-8")
    memory_db = state_dir / "memory.sqlite3"
    cli_runtime.model_runtime.run(cli_runtime.memory.paths())
    assert memory_db.exists()

    cli.command_handlers.handle_reset()

    assert state_dir.exists()
    assert memory_db.exists()
    assert (state_dir / "memory-export-demo.json").exists()
    assert (state_dir / "memory_summary.md").read_text(encoding="utf-8") == (
        "human review projection"
    )
    assert (state_dir / "history.txt").read_text(
        encoding="utf-8"
    ) == "/status\n"
    assert not findings_dir.exists()
    assert not runtime_dir.exists()
    assert not (state_dir / "workspace.json").exists()
    assert cli.runtime is None
    assert cli.input_router is None
    assert cli.agent_runner is None
    cli_renderer.print_success.assert_called_once_with(
        "项目工作台已重置；普通会话、项目笔记、导出和终端历史已保留。"
        "如需清空 Memory，请执行 /memory clear --"
        "confirm。"
    )


def test_runtime_error_hides_raw_provider_failure_outside_debug_mode(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.GLOBAL_CONFIG, "debug_mode", False)

    # 直接检查错误展示边界，不触发真实模型或终端输入。
    # pylint: disable-next=protected-access
    cli._render_runtime_error(RuntimeError("RAW_PROVIDER_SENTINEL"))

    message = cli_renderer.print_error.call_args.args[0]
    assert "RAW_PROVIDER_SENTINEL" not in message
    assert "AUTOPATCH_DEBUG=true" in message


def test_runtime_error_shows_raw_provider_failure_in_debug_mode(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProviderError(RuntimeError):
        status_code = 503
        body = {"detail": "RAW_PROVIDER_SENTINEL"}

    monkeypatch.setattr(config.GLOBAL_CONFIG, "debug_mode", True)

    # 直接检查错误展示边界，不触发真实模型或终端输入。
    # pylint: disable-next=protected-access
    cli._render_runtime_error(ProviderError("provider unavailable"))

    message = cli_renderer.print_error.call_args.args[0]
    assert "ProviderError: provider unavailable" in message
    assert "status_code: 503" in message
    assert 'body: {"detail": "RAW_PROVIDER_SENTINEL"}' in message


def test_handle_chat_maps_pending_code_explain_without_scope_to_patch_explain(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_conversation_router = mock.MagicMock()
    monkeypatch.setattr(
        cli_runtime, "conversation_router", mock_conversation_router
    )
    mock_conversation_router.classify_route_with_diagnostics.return_value = (
        diagnostics.RouteClassificationResult(
            route=intent.ConversationRoute.REVIEW_CONTINUE,
            source="test",
        )
    )
    mock_intent_detector = mock.MagicMock()
    monkeypatch.setattr(cli_runtime, "intent_detector", mock_intent_detector)
    mock_intent_detector.classify_with_diagnostics.return_value = (
        diagnostics.IntentClassificationResult(
            intent=intent.IntentType.CODE_EXPLAIN,
            source="test",
        )
    )
    mock_handle_patch_explain = mock.MagicMock()
    monkeypatch.setattr(
        cli_input_router, "handle_patch_explain", mock_handle_patch_explain
    )
    mock_handle_code_explain = mock.MagicMock()
    monkeypatch.setattr(
        cli_input_router, "handle_code_explain", mock_handle_code_explain
    )

    cli_input_router.handle_chat("解释一下")

    mock_handle_patch_explain.assert_called_once_with("解释一下")
    mock_handle_code_explain.assert_not_called()


def test_handle_chat_keeps_pending_patch_revise_route(
    cli: app.AutoPatchCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    cli_runtime = cli.runtime
    assert cli_runtime is not None
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=domain_scope.CodeScope(
            kind=domain_scope.CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli_runtime.workspace_manager.save(workspace)
    mock_conversation_router = mock.MagicMock()
    monkeypatch.setattr(
        cli_runtime, "conversation_router", mock_conversation_router
    )
    mock_conversation_router.classify_route_with_diagnostics.return_value = (
        diagnostics.RouteClassificationResult(
            route=intent.ConversationRoute.REVIEW_CONTINUE,
            source="test",
        )
    )
    mock_intent_detector = mock.MagicMock()
    monkeypatch.setattr(cli_runtime, "intent_detector", mock_intent_detector)
    mock_intent_detector.classify_with_diagnostics.return_value = (
        diagnostics.IntentClassificationResult(
            intent=intent.IntentType.PATCH_REVISE,
            source="test",
        )
    )
    mock_handle_patch_revise = mock.MagicMock()
    monkeypatch.setattr(
        cli_input_router, "handle_patch_revise", mock_handle_patch_revise
    )

    cli_input_router.handle_chat("重新写这个补丁")

    mock_handle_patch_revise.assert_called_once_with("重新写这个补丁")


def test_debug_mode_renders_classifier_fallback_diagnostics(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    monkeypatch.setattr(config.GLOBAL_CONFIG, "debug_mode", True)

    # 定向检查路由诊断，模型与主输入循环不属于本断言。
    # pylint: disable-next=protected-access
    cli_input_router._render_route_diagnostic(
        diagnostics.RouteClassificationResult(
            route=intent.ConversationRoute.REVIEW_CONTINUE,
            source="fallback",
            fallback_reason="classifier timeout",
        )
    )
    # 定向检查路由诊断，模型与主输入循环不属于本断言。
    # pylint: disable-next=protected-access
    cli_input_router._render_intent_diagnostic(
        diagnostics.IntentClassificationResult(
            intent=intent.IntentType.GENERAL_CHAT,
            source="fallback",
            fallback_reason="invalid label",
        )
    )

    cli_renderer.print_agent_text.assert_any_call(
        "路由诊断：route 使用 fallback review_conti"
        "nue，原因：classifier timeout"
    )
    cli_renderer.print_agent_text.assert_any_call(
        "路由诊断：intent 使用 fallback general_cha" "t，原因：invalid label"
    )


def test_normal_mode_hides_classifier_fallback_diagnostics(
    cli_renderer: mock.MagicMock,
    cli: app.AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_input_router = cli.input_router
    assert cli_input_router is not None
    monkeypatch.setattr(config.GLOBAL_CONFIG, "debug_mode", False)
    cli_renderer.print_agent_text.reset_mock()

    # 定向检查路由诊断，模型与主输入循环不属于本断言。
    # pylint: disable-next=protected-access
    cli_input_router._render_route_diagnostic(
        diagnostics.RouteClassificationResult(
            route=intent.ConversationRoute.REVIEW_CONTINUE,
            source="fallback",
            fallback_reason="RAW_ROUTE_SENTINEL",
        )
    )
    # 定向检查路由诊断，模型与主输入循环不属于本断言。
    # pylint: disable-next=protected-access
    cli_input_router._render_intent_diagnostic(
        diagnostics.IntentClassificationResult(
            intent=intent.IntentType.GENERAL_CHAT,
            source="fallback",
            fallback_reason="RAW_INTENT_SENTINEL",
        )
    )

    cli_renderer.print_agent_text.assert_not_called()
