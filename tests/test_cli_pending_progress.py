from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autopatch_j.cli.app import AutoPatchCLI
from autopatch_j.core.models import (
    ActiveWorkspace,
    CodeScope,
    CodeScopeKind,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
    WorkspaceStatus,
)


def _make_cli(tmp_path: Path) -> AutoPatchCLI:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    return AutoPatchCLI(tmp_path)


def _scope() -> CodeScope:
    return CodeScope(
        kind=CodeScopeKind.MULTI_FILE,
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
    status: PatchReviewStatus,
    rationale: str,
) -> PatchReviewItem:
    return PatchReviewItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=["F1"],
        status=status,
        draft=PatchDraftData(
            file_path=file_path,
            old_string="old",
            new_string="new",
            diff=f"diff-{item_id}",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale=rationale,
        ),
    )


def test_run_renders_pending_patch_with_absolute_progress(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.artifacts is not None

    cli.artifacts.persist_workspace(
        ActiveWorkspace(
            mode=WorkspaceStatus.REVIEWING,
            scope=_scope(),
            latest_scan_id="scan-1",
            patch_items=[
                _item(
                    item_id="item-1",
                    file_path="src/main/java/demo/User.java",
                    status=PatchReviewStatus.APPLIED,
                    rationale="rationale-1",
                ),
                _item(
                    item_id="item-2",
                    file_path="src/main/java/demo/UserService.java",
                    status=PatchReviewStatus.PENDING,
                    rationale="rationale-2",
                ),
                _item(
                    item_id="item-3",
                    file_path="src/main/java/demo/AppConfig.java",
                    status=PatchReviewStatus.PENDING,
                    rationale="rationale-3",
                ),
            ],
            current_patch_index=1,
        )
    )

    cli.prompt_session = MagicMock()
    cli.prompt_session.prompt.side_effect = EOFError
    cli.renderer.print_panel = MagicMock()
    cli.renderer.print = MagicMock()
    cli.renderer.print_diff = MagicMock()
    cli.renderer.print_action_panel = MagicMock()

    cli.run()

    cli.renderer.print_action_panel.assert_called_once()
    kwargs = cli.renderer.print_action_panel.call_args.kwargs
    assert kwargs["current_idx"] == 2
    assert kwargs["total_count"] == 3
