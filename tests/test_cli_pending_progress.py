"""通过短输入会话驱动真实 CLI 循环，模型和 renderer 以明确 mock 隔离。"""

from __future__ import annotations

import pathlib
from unittest import mock

import pytest

from autopatch_j.cli import app
from autopatch_j.core import finding
from autopatch_j.core.domain import scope
from autopatch_j.core.domain import workspace


@pytest.fixture(autouse=True, name="_use_fake_cli_llm")
def make__use_fake_cli_llm(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_factory,
) -> None:
    monkeypatch.setattr(
        "autopatch_j.llm.factory.build_model_runtime",
        fake_llm_factory,
    )


def _make_cli(tmp_path: pathlib.Path) -> app.AutoPatchCli:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    return app.AutoPatchCli(tmp_path)


def _scope() -> scope.CodeScope:
    return scope.CodeScope(
        kind=scope.CodeScopeKind.MULTI_FILE,
        source_roots=["src/main/java/demo"],
        focus_files=[
            "src/main/java/demo/User.java",
            "src/main/java/demo/UserService.java",
            "src/main/java/demo/AppConfig.java",
        ],
        is_locked=True,
    )


def _item(
    item_id: str,
    file_path: str,
    status: workspace.PatchReviewStatus,
    rationale: str,
    source_hint: str | None = None,
) -> workspace.ReviewPatchItem:
    return workspace.ReviewPatchItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=["F1"],
        status=status,
        draft=workspace.PatchDraftSnapshot(
            file_path=file_path,
            old_string="old",
            new_string="new",
            diff=f"diff-{item_id}",
            match_region=finding.SourceRegion(1, 1, 1, 4, 0, 3),
            message="ok",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale=rationale,
            source_hint=source_hint,
        ),
    )


def test_run_renders_pending_patch_with_absolute_progress(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _make_cli(tmp_path)
    assert cli.runtime is not None

    cli.runtime.workspace_manager.save(
        workspace.ReviewWorkspace(
            mode=workspace.WorkspaceStatus.REVIEWING,
            scope=_scope(),
            latest_scan_id="scan-1",
            patch_items=[
                _item(
                    item_id="item-1",
                    file_path="src/main/java/demo/User.java",
                    status=workspace.PatchReviewStatus.APPLIED,
                    rationale="rationale-1",
                ),
                _item(
                    item_id="item-2",
                    file_path="src/main/java/demo/UserService.java",
                    status=workspace.PatchReviewStatus.PENDING,
                    rationale="rationale-2",
                ),
                _item(
                    item_id="item-3",
                    file_path="src/main/java/demo/AppConfig.java",
                    status=workspace.PatchReviewStatus.PENDING,
                    rationale="rationale-3",
                ),
            ],
            current_patch_index=1,
        )
    )

    mock_prompt_session = mock.MagicMock()
    monkeypatch.setattr(cli, "prompt_session", mock_prompt_session)
    mock_prompt_session.prompt.side_effect = EOFError
    mock_print_panel = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_panel", mock_print_panel)
    mock_print = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_plain", mock_print)
    mock_print_diff = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_diff", mock_print_diff)
    mock_print_action_panel = mock.MagicMock()
    monkeypatch.setattr(
        cli.renderer, "print_action_panel", mock_print_action_panel
    )

    cli.run()

    mock_print_action_panel.assert_called_once()
    kwargs = mock_print_action_panel.call_args.kwargs
    assert kwargs["current_idx"] == 2
    assert kwargs["total_count"] == 3


def test_run_passes_source_hint_to_action_panel(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _make_cli(tmp_path)
    assert cli.runtime is not None

    cli.runtime.workspace_manager.save(
        workspace.ReviewWorkspace(
            mode=workspace.WorkspaceStatus.REVIEWING,
            scope=_scope(),
            latest_scan_id="scan-1",
            patch_items=[
                _item(
                    item_id="item-1",
                    file_path="src/main/java/demo/UserService.java",
                    status=workspace.PatchReviewStatus.PENDING,
                    rationale="rationale-1",
                    source_hint="LLM 二次复核（静态扫描未报出问题）",
                ),
            ],
            current_patch_index=0,
        )
    )

    mock_prompt_session = mock.MagicMock()
    monkeypatch.setattr(cli, "prompt_session", mock_prompt_session)
    mock_prompt_session.prompt.side_effect = EOFError
    mock_print_panel = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_panel", mock_print_panel)
    mock_print = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_plain", mock_print)
    mock_print_diff = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_diff", mock_print_diff)
    mock_print_action_panel = mock.MagicMock()
    monkeypatch.setattr(
        cli.renderer, "print_action_panel", mock_print_action_panel
    )

    cli.run()

    mock_print_action_panel.assert_called_once()
    kwargs = mock_print_action_panel.call_args.kwargs
    assert kwargs["source_hint"] == "LLM 二次复核（静态扫描未报出问题）"


def test_run_passes_stale_blocking_reason_to_action_panel(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _make_cli(tmp_path)
    assert cli.runtime is not None
    stale_item = _item(
        item_id="item-1",
        file_path="src/main/java/demo/UserService.java",
        status=workspace.PatchReviewStatus.PENDING,
        rationale="rationale-1",
    )
    stale_item.draft.error_code = "STALE_DRAFT"
    stale_item.draft.message = "待审补丁已失效：old_string 已偏离预期位置。"
    cli.runtime.workspace_manager.save(
        workspace.ReviewWorkspace(
            mode=workspace.WorkspaceStatus.REVIEWING,
            scope=_scope(),
            latest_scan_id="scan-1",
            patch_items=[stale_item],
            current_patch_index=0,
        )
    )
    mock_prompt_session = mock.MagicMock()
    monkeypatch.setattr(cli, "prompt_session", mock_prompt_session)
    mock_prompt_session.prompt.side_effect = EOFError
    mock_print_panel = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_panel", mock_print_panel)
    mock_print = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_plain", mock_print)
    mock_print_diff = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_diff", mock_print_diff)
    mock_print_action_panel = mock.MagicMock()
    monkeypatch.setattr(
        cli.renderer, "print_action_panel", mock_print_action_panel
    )

    cli.run()

    kwargs = mock_print_action_panel.call_args.kwargs
    assert kwargs["blocking_error"] == stale_item.draft.message
    assert "STALE" in str(mock_prompt_session.prompt.call_args.args[0])


def test_run_retains_pending_patch_without_shared_agent_history(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _make_cli(tmp_path)
    assert cli.runtime is not None

    cli.runtime.workspace_manager.save(
        workspace.ReviewWorkspace(
            mode=workspace.WorkspaceStatus.REVIEWING,
            scope=_scope(),
            latest_scan_id="scan-1",
            patch_items=[
                _item(
                    item_id="item-1",
                    file_path="src/main/java/demo/UserService.java",
                    status=workspace.PatchReviewStatus.PENDING,
                    rationale="rationale-1",
                ),
            ],
            current_patch_index=0,
        )
    )

    mock_prompt_session = mock.MagicMock()
    monkeypatch.setattr(cli, "prompt_session", mock_prompt_session)
    mock_prompt_session.prompt.side_effect = EOFError
    mock_print_panel = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_panel", mock_print_panel)
    mock_print = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_plain", mock_print)
    mock_print_diff = mock.MagicMock()
    monkeypatch.setattr(cli.renderer, "print_diff", mock_print_diff)
    mock_print_action_panel = mock.MagicMock()
    monkeypatch.setattr(
        cli.renderer, "print_action_panel", mock_print_action_panel
    )
    cli.run()

    assert cli.runtime.workspace_manager.load_current_patch_draft() is not None
    assert not hasattr(cli.runtime.agent, "_messages")
