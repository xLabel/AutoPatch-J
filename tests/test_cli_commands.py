from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autopatch_j.cli.app import CLI
from autopatch_j.core.models import (
    ActiveWorkspace,
    CodeScope,
    CodeScopeKind,
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


def _item(item_id: str) -> PatchReviewItem:
    return PatchReviewItem(
        item_id=item_id,
        file_path="src/main/java/demo/User.java",
        finding_ids=["F1"],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftData(
            file_path="src/main/java/demo/User.java",
            old_string="old",
            new_string="new",
            diff="diff",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale="fix it",
        ),
    )


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
