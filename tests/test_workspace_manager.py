from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.models import (
    ActiveWorkspace,
    CodeScope,
    CodeScopeKind,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
    WorkspaceStatus,
)
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.core.patch_verifier import SyntaxCheckResult
from autopatch_j.core.workspace_manager import WorkspaceManager


def _scope() -> CodeScope:
    return CodeScope(
        kind=CodeScopeKind.MULTI_FILE,
        source_roots=["src/main/java/demo"],
        focus_files=[
            "src/main/java/demo/User.java",
            "src/main/java/demo/UserService.java",
        ],
        is_locked=True,
    )


def _item(item_id: str, file_path: str, finding_id: str) -> PatchReviewItem:
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
            target_snippet="snippet",
        ),
    )


def test_artifact_manager_persists_workspace_round_trip(tmp_path: Path) -> None:
    artifacts = ArtifactManager(tmp_path)
    workspace = ActiveWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=_scope(),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1", "src/main/java/demo/User.java", "F1")],
        current_patch_index=0,
    )

    artifacts.save_workspace(workspace)
    restored = artifacts.load_workspace()

    assert restored is not None
    assert restored.mode is WorkspaceStatus.REVIEWING
    assert restored.scope is not None
    assert restored.scope.focus_files == workspace.scope.focus_files
    assert restored.get_current_patch() is not None
    assert restored.get_current_patch().item_id == "item-1"


def test_workspace_manager_persist_review_workspace_starts_review_mode(tmp_path: Path) -> None:
    service = WorkspaceManager(ArtifactManager(tmp_path))

    workspace = service.initialize_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-2",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
    )

    assert workspace.mode is WorkspaceStatus.REVIEWING
    assert workspace.current_patch_index == 0
    assert service.load_workspace().has_pending_patch() is True
    assert workspace.get_current_patch() is not None
    assert workspace.get_current_patch().item_id == "item-1"


def test_workspace_manager_persist_applied_current_patch_advances_until_idle(tmp_path: Path) -> None:
    service = WorkspaceManager(ArtifactManager(tmp_path))
    service.initialize_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-3",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
    )

    with service.edit() as workspace:
        workspace.mark_applied()
    
    first_pass = service.load_workspace()
    
    with service.edit() as workspace:
        workspace.mark_applied()
        
    second_pass = service.load_workspace()

    assert first_pass.patch_items[0].status is PatchReviewStatus.APPLIED
    assert first_pass.current_patch_index == 1
    assert first_pass.mode is WorkspaceStatus.REVIEWING
    assert second_pass.patch_items[1].status is PatchReviewStatus.APPLIED
    assert second_pass.current_patch_index == 2
    assert second_pass.mode is WorkspaceStatus.IDLE


def test_workspace_manager_replace_remaining_patch_items_keeps_applied_head(tmp_path: Path) -> None:
    service = WorkspaceManager(ArtifactManager(tmp_path))
    service.initialize_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-4",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
    )
    with service.edit() as workspace:
        workspace.mark_applied()

    with service.edit() as workspace:
        workspace.replace_tail(
            [
                _item("item-3", "src/main/java/demo/UserService.java", "F2"),
                _item("item-4", "src/main/java/demo/UserHelper.java", "F3"),
            ]
        )
        
    replaced = service.load_workspace()

    assert replaced.current_patch_index == 1
    assert replaced.mode is WorkspaceStatus.REVIEWING
    assert [item.item_id for item in replaced.patch_items] == ["item-1", "item-3", "item-4"]
    assert replaced.patch_items[0].status is PatchReviewStatus.APPLIED
    assert replaced.get_current_patch() is not None
    assert replaced.get_current_patch().item_id == "item-3"


def test_workspace_manager_replace_current_patch_keeps_queue_order(tmp_path: Path) -> None:
    service = WorkspaceManager(ArtifactManager(tmp_path))
    service.initialize_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-5",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
    )
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

    replaced = service.replace_current_patch(replacement)
    workspace = service.load_workspace()

    assert replaced is True
    assert workspace.current_patch_index == 0
    assert [item.item_id for item in workspace.patch_items] == ["item-1", "item-2"]
    assert workspace.patch_items[0].draft.new_string == "better"
    assert workspace.patch_items[0].draft.rationale == "better fix"
    assert workspace.patch_items[1].draft.rationale == "fix F2"
