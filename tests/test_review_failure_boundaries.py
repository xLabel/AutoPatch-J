"""取消、路径变更和持久化故障下的实际 CLI 与审核边界。"""

import asyncio
from collections.abc import Iterator
from unittest import mock

import pytest

from autopatch_j.cli import app
from autopatch_j.cli import command_handlers
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.core import memory
from autopatch_j.core.domain import scope
from autopatch_j.core.patching import types
from autopatch_j.llm import context_window
from autopatch_j.llm import factory
from autopatch_j.llm import runtime as llm_runtime
from autopatch_j.scanners import models
from autopatch_j.tools.function_calls import propose_patch
from tests import sdk_fakes


@pytest.fixture(name="runtime")
def make_runtime(tmp_path) -> Iterator[cli_runtime.CliRuntime]:
    resources = llm_runtime.ModelRuntime(
        None, context_window.resolve_context_profile(model="deepseek-flash")
    )
    instance = cli_runtime.build_cli_runtime(tmp_path, lambda: resources)
    try:
        yield instance
    finally:
        instance.close()


def _draft(runtime: cli_runtime.CliRuntime) -> types.SearchReplacePatchDraft:
    source = runtime.patch_engine.repo_root / "Demo.java"
    source.write_text("class Demo {}", encoding="utf-8")
    built = runtime.patch_engine.create_draft(
        "Demo.java", "class Demo", "class Renamed"
    )
    return types.SearchReplacePatchDraft(
        file_path="Demo.java",
        old_string="class Demo",
        new_string="class Renamed",
        diff=built.diff,
        match_region=built.match_region,
        validation=types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
    )


def test_apply_and_rebase_reject_source_retargeted_to_project_state(runtime):
    draft = _draft(runtime)
    source = runtime.patch_engine.repo_root / "Demo.java"
    protected = source.parent / ".autopatch-j" / "private.java"
    protected.write_text("class Demo {}", encoding="utf-8")
    source.unlink()
    source.symlink_to(protected)
    outcome = runtime.patch_engine.apply_patch(draft)
    assert not outcome.applied and outcome.error_code == "UNSAFE_PATH"
    rebased = runtime.patch_engine.rebase_draft(
        draft, draft.match_region, draft.match_region
    )
    assert rebased.error_code == "STALE_DRAFT"
    with pytest.raises(PermissionError):
        runtime.patch_engine.create_draft(
            "Demo.java", "class Demo", "class Renamed"
        )
    assert protected.read_text(encoding="utf-8") == "class Demo {}"


def test_apply_accepts_internal_source_symlink(runtime):
    draft = _draft(runtime)
    source = runtime.patch_engine.repo_root / "Demo.java"
    target = source.parent / "Actual.java"
    source.rename(target)
    source.symlink_to(target)
    assert runtime.patch_engine.apply_patch(draft).applied
    assert target.read_text(encoding="utf-8") == "class Renamed {}"


@pytest.mark.parametrize("destination", ["outside", "state"])
def test_project_metadata_does_not_inject_forbidden_symlink(
    runtime, tmp_path, destination
):
    target = (
        tmp_path.parent / f"{tmp_path.name}-private.md"
        if destination == "outside"
        else tmp_path / ".autopatch-j" / "private.md"
    )
    target.write_text("FORBIDDEN_METADATA_MARKER", encoding="utf-8")
    (tmp_path / "README.md").symlink_to(target)
    allowed = tmp_path / "notes.md"
    allowed.write_text("ALLOWED_PROJECT_METADATA", encoding="utf-8")
    (tmp_path / "pom.xml").symlink_to(allowed)
    context = runtime.summary_provider.build_project_explain_context(
        scope.CodeScope(scope.CodeScopeKind.PROJECT, [], [], False)
    )
    assert "FORBIDDEN_METADATA_MARKER" not in context
    assert "ALLOWED_PROJECT_METADATA" in context


@pytest.mark.parametrize("failure_at", [1, 2])
def test_workspace_save_failure_preserves_write_fact_and_blocks_repeat(
    runtime, monkeypatch, failure_at
):
    draft = _draft(runtime)
    runtime.workspace_manager.add_patch(draft)
    manager = runtime.workspace_manager
    original_save = manager.save
    saves = []

    def save(unused_self, workspace):
        saves.append(True)
        if len(saves) == failure_at:
            raise OSError("injected save failure")
        original_save(workspace)

    monkeypatch.setattr(type(manager), "save", save)
    verifier = mock.MagicMock()
    monkeypatch.setattr(runtime, "patch_verifier", verifier)
    outcome = runtime.review_service.apply(draft)
    source = runtime.patch_engine.repo_root / "Demo.java"
    if failure_at == 1:
        assert not outcome.application.applied
        assert outcome.application.error_code == "WORKSPACE_SAVE_FAILED"
        assert "源码未写入" in outcome.application.message
        assert source.read_text(encoding="utf-8") == "class Demo {}"
    else:
        assert outcome.application.applied
        assert (
            outcome.workspace_error and "源码已写入" in outcome.workspace_error
        )
        assert source.read_text(encoding="utf-8") == "class Renamed {}"
        current = manager.load_current_patch_draft()
        assert current is not None and current.error_code == "STALE_DRAFT"
        assert not runtime.review_service.apply(current).application.applied
        restarted = cli_runtime.build_cli_runtime(
            source.parent,
            lambda: llm_runtime.ModelRuntime(
                None,
                context_window.resolve_context_profile(model="deepseek-flash"),
            ),
        )
        try:
            pending = restarted.workspace_manager.load_current_patch_draft()
            assert pending is not None and pending.error_code == "STALE_DRAFT"
            assert not restarted.review_service.apply(
                pending
            ).application.applied
        finally:
            restarted.close()
    verifier.verify_finding_resolved.assert_not_called()


def test_cli_cancelled_classifier_exits_without_followup_and_closes_client(
    tmp_path, sdk_scenario, monkeypatch
):
    scenario = sdk_scenario([sdk_fakes.Reply(text="general_chat")])
    resources = scenario.runtime()
    scenario.before_reply = resources.cancel
    monkeypatch.setattr(factory, "build_model_runtime", lambda: resources)
    renderer = mock.MagicMock()
    monkeypatch.setattr("autopatch_j.cli.render.CliRenderer", lambda: renderer)
    cli = app.AutoPatchCli(tmp_path)
    prompt = mock.MagicMock()
    prompt.prompt.return_value = "你好"
    monkeypatch.setattr(
        cli.input_controller, "create_prompt_session", lambda: prompt
    )
    assert cli.run() == 0
    assert len(scenario.requests) == 1
    prompt.prompt.assert_called_once()
    assert (
        resources.closed
        and resources.client is not None
        and resources.client.is_closed()
    )


@pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
def test_cli_closes_resources_when_startup_or_memory_operation_fails(
    runtime, tmp_path, monkeypatch, failure
):
    monkeypatch.setattr(
        factory, "build_model_runtime", lambda: runtime.model_runtime
    )
    monkeypatch.setattr("autopatch_j.cli.render.CliRenderer", mock.MagicMock)
    cli = app.AutoPatchCli(tmp_path)
    prompt = mock.MagicMock()
    prompt.prompt.return_value = "/memory status"
    monkeypatch.setattr(
        cli.input_controller, "create_prompt_session", lambda: prompt
    )
    if failure is asyncio.CancelledError:
        assert cli.runtime is not None

        async def cancelled():
            raise asyncio.CancelledError

        monkeypatch.setattr(cli.runtime.memory, "paths", cancelled)
        assert cli.run() == 0
    else:
        monkeypatch.setattr(
            cli.welcome_presenter,
            "render",
            mock.MagicMock(side_effect=RuntimeError("render failed")),
        )
        with pytest.raises(RuntimeError, match="render failed"):
            cli.run()
    assert runtime.model_runtime.closed


def test_limited_zero_finding_workflow_keeps_draft_and_discloses_status(
    tmp_path, sdk_scenario, monkeypatch
):
    (tmp_path / "Demo.java").write_text("class Demo {}", encoding="utf-8")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="propose_patch",
                arguments={
                    "file_path": "Demo.java",
                    "old_string": "class Demo",
                    "new_string": "class Renamed",
                    "rationale": "用户要求改名",
                },
                finish="tool_calls",
            ),
            *[
                sdk_fakes.Reply(
                    tool="read_memory",
                    arguments={"file": f"note-{i}.md"},
                    finish="tool_calls",
                )
                for i in range(10)
            ],
        ]
    )
    resources = scenario.runtime()
    monkeypatch.setattr(factory, "build_model_runtime", lambda: resources)
    renderer = mock.MagicMock()
    monkeypatch.setattr("autopatch_j.cli.render.CliRenderer", lambda: renderer)
    cli = app.AutoPatchCli(tmp_path)
    assert cli.runtime is not None and cli.input_router is not None
    for index in range(10):
        resources.run(
            cli.runtime.memory.store.write(
                memory.PREFIX + f"note-{index}.md",
                f"不同的笔记内容 {index}",
                expected_version=None,
            )
        )
    monkeypatch.setattr(
        cli.runtime.scanner_runner,
        "run_scan_and_save",
        mock.MagicMock(
            return_value=(
                "scan-zero",
                models.ScanResult(
                    engine="semgrep",
                    scope=["Demo.java"],
                    targets=["Demo.java"],
                    status="ok",
                    message="零 finding",
                ),
            )
        ),
    )
    try:
        cli.input_router.handle_code_audit("@Demo.java 检查并修复")
        assert cli.runtime.workspace_manager.load().has_pending_patch()
        errors = [call.args[0] for call in renderer.print_error.call_args_list]
        assert any("未完整完成" in text for text in errors)
        renderer.print_no_issue_panel.assert_not_called()
        summaries = [
            call.args[0] for call in renderer.print_agent_text.call_args_list
        ]
        assert any("本轮代码审计结束" in text for text in summaries)
        assert not any("代码审计完成" in text for text in summaries)
        assert (tmp_path / "Demo.java").read_text(
            encoding="utf-8"
        ) == "class Demo {}"
    finally:
        cli.clear_runtime()


def test_cli_reports_written_source_when_final_workspace_save_fails(
    runtime, monkeypatch
):
    draft = _draft(runtime)
    runtime.workspace_manager.add_patch(draft)
    original_save = runtime.workspace_manager.save
    saves = []

    def save(unused_self, workspace):
        saves.append(True)
        if len(saves) == 2:
            raise OSError("disk failure")
        original_save(workspace)

    monkeypatch.setattr(type(runtime.workspace_manager), "save", save)
    renderer = mock.MagicMock()
    host = mock.MagicMock(runtime=runtime, renderer=renderer)
    applied = command_handlers.CommandHandlers(host).handle_apply(draft)
    assert applied.applied
    renderer.print_success.assert_called_once_with("补丁已应用")
    assert any(
        "源码已写入，但审核状态未保存" in call.args[0]
        for call in renderer.print_error.call_args_list
    )
    assert not any(
        "补丁队列已清空" in call.args[0]
        for call in renderer.print_agent_text.call_args_list
    )


def test_failed_source_apply_restores_original_review_snapshot(runtime):
    draft = _draft(runtime)
    runtime.workspace_manager.add_patch(draft)
    source = runtime.patch_engine.repo_root / "Demo.java"
    source.write_text("class Changed {}", encoding="utf-8")
    outcome = runtime.review_service.apply(draft)
    assert not outcome.application.applied
    assert outcome.application.error_code == "SOURCE_CHANGED"
    assert outcome.workspace_error is None
    assert runtime.workspace_manager.load_current_patch_draft() == draft
    assert source.read_text(encoding="utf-8") == "class Changed {}"


def test_unavailable_syntax_does_not_claim_validation_passed(runtime):
    _draft(runtime)
    runtime.agent.session.patch_verifier = None
    response = propose_patch.ProposePatchTool(runtime.agent.session).execute(
        file_path="Demo.java",
        old_string="class Demo",
        new_string="class Renamed",
        rationale="用户要求改名",
    )
    assert response.status == "unavailable"
    draft = runtime.agent.session.proposed_patch_draft
    assert draft is not None and draft.validation.status == "unavailable"
    assert "已通过语法校验" not in draft.message


def test_old_scan_scope_does_not_follow_directory_retargeted_outside(
    runtime, tmp_path
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "Private.java").write_text("class Private {}", encoding="utf-8")
    (tmp_path / "source-alias").symlink_to(outside, target_is_directory=True)
    runtime.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["source-alias"],
            targets=["source-alias"],
            status="ok",
            message="旧扫描快照",
        )
    )
    assert runtime.summary_provider.describe_current_scope_paths() == [
        "当前范围"
    ]
