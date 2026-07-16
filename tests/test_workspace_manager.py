from __future__ import annotations

from pathlib import Path

import pytest

from autopatch_j.core.review import ProjectArtifactStore
from autopatch_j.core.domain import (
    ReviewWorkspace,
    CodeScope,
    CodeScopeKind,
    PatchDraftSnapshot,
    ReviewPatchItem,
    PatchReviewStatus,
    WorkspaceStatus,
)
from autopatch_j.core.patching import SearchReplacePatchDraft
from autopatch_j.core.patching import SyntaxCheckResult
from autopatch_j.core.review import ReviewWorkspaceManager
from autopatch_j.scanners import FindingIdentity, SourceRegion


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


def _item(item_id: str, file_path: str, finding_id: str) -> ReviewPatchItem:
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
            match_region=SourceRegion(1, 1, 1, 4, 0, 3),
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


def _target_identity(file_path: str) -> FindingIdentity:
    return FindingIdentity(
        fingerprint=f"apj-v1:{'a' * 64}:1",
        check_id="demo.rule",
        path=file_path,
        region=SourceRegion(1, 1, 1, 4, 0, 3),
    )


def test_artifact_manager_persists_workspace_round_trip(tmp_path: Path) -> None:
    artifacts = ProjectArtifactStore(tmp_path)
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=_scope(),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1", "src/main/java/demo/User.java", "F1")],
        current_patch_index=0,
    )

    artifacts.save_review_workspace(workspace)
    restored = artifacts.load_review_workspace()

    assert restored is not None
    assert restored.mode is WorkspaceStatus.REVIEWING
    assert restored.scope is not None
    assert restored.scope.focus_files == workspace.scope.focus_files
    assert restored.current_patch() is not None
    assert restored.current_patch().item_id == "item-1"


def test_workspace_manager_persist_review_workspace_starts_review_mode(tmp_path: Path) -> None:
    service = ReviewWorkspaceManager(ProjectArtifactStore(tmp_path))

    workspace = service.initialize_review(
        scope=_scope(),
        latest_scan_id="scan-2",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
    )

    assert workspace.mode is WorkspaceStatus.REVIEWING
    assert workspace.current_patch_index == 0
    assert service.load().has_pending_patch() is True
    assert workspace.current_patch() is not None
    assert workspace.current_patch().item_id == "item-1"


def test_workspace_manager_persist_applied_current_patch_advances_until_idle(tmp_path: Path) -> None:
    service = ReviewWorkspaceManager(ProjectArtifactStore(tmp_path))
    service.initialize_review(
        scope=_scope(),
        latest_scan_id="scan-3",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
    )

    with service.edit() as workspace:
        workspace.mark_current_patch_applied()
    
    first_pass = service.load()
    
    with service.edit() as workspace:
        workspace.mark_current_patch_applied()
        
    second_pass = service.load()

    assert first_pass.patch_items[0].status is PatchReviewStatus.APPLIED
    assert first_pass.current_patch_index == 1
    assert first_pass.mode is WorkspaceStatus.REVIEWING
    assert second_pass.patch_items[1].status is PatchReviewStatus.APPLIED
    assert second_pass.current_patch_index == 2
    assert second_pass.mode is WorkspaceStatus.IDLE


def test_workspace_edit_does_not_save_when_block_raises(tmp_path: Path) -> None:
    service = ReviewWorkspaceManager(ProjectArtifactStore(tmp_path))
    service.initialize_review(
        scope=_scope(),
        latest_scan_id="scan-4",
        patch_items=[_item("item-1", "src/main/java/demo/User.java", "F1")],
    )

    with pytest.raises(RuntimeError):
        with service.edit() as workspace:
            workspace.mark_current_patch_applied()
            raise RuntimeError("abort save")

    restored = service.load()
    assert restored.patch_items[0].status is PatchReviewStatus.PENDING
    assert restored.current_patch_index == 0
    assert restored.mode is WorkspaceStatus.REVIEWING


def test_workspace_manager_replace_current_patch_keeps_queue_order(tmp_path: Path) -> None:
    service = ReviewWorkspaceManager(ProjectArtifactStore(tmp_path))
    service.initialize_review(
        scope=_scope(),
        latest_scan_id="scan-5",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
    )
    replacement = SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string="better",
        diff="better diff",
        match_region=SourceRegion(1, 1, 1, 4, 0, 3),
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="better fix",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_finding=_target_identity("src/main/java/demo/User.java"),
    )

    replaced = service.replace_current_patch(replacement)
    workspace = service.load()

    assert replaced is True
    assert workspace.current_patch_index == 0
    assert [item.item_id for item in workspace.patch_items] == ["item-1", "item-2"]
    assert workspace.patch_items[0].draft.new_string == "better"
    assert workspace.patch_items[0].draft.rationale == "better fix"
    assert workspace.patch_items[1].draft.rationale == "fix F2"


def test_workspace_manager_rejects_revision_that_switches_finding(tmp_path: Path) -> None:
    service = ReviewWorkspaceManager(ProjectArtifactStore(tmp_path))
    service.initialize_review(
        scope=_scope(),
        latest_scan_id="scan-6",
        patch_items=[_item("item-1", "src/main/java/demo/User.java", "F1")],
    )
    replacement = SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string="wrong finding",
        diff="wrong diff",
        match_region=SourceRegion(1, 1, 1, 4, 0, 3),
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="wrong finding",
        associated_finding_id="F2",
        source_scan_id="scan-1",
        target_finding=_target_identity("src/main/java/demo/User.java"),
    )

    replaced = service.replace_current_patch(replacement)
    workspace = service.load()

    assert replaced is False
    assert workspace.patch_items[0].draft.new_string == "new"


def test_workspace_manager_rejects_revision_that_drops_finding_binding(
    tmp_path: Path,
) -> None:
    service = ReviewWorkspaceManager(ProjectArtifactStore(tmp_path))
    service.initialize_review(
        scope=_scope(),
        latest_scan_id="scan-7",
        patch_items=[_item("item-1", "src/main/java/demo/User.java", "F1")],
    )
    replacement = SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string="unbound replacement",
        diff="replacement diff",
        match_region=SourceRegion(1, 1, 1, 4, 0, 3),
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="drops binding",
    )

    replaced = service.replace_current_patch(replacement)
    workspace = service.load()

    assert replaced is False
    assert workspace.patch_items[0].draft.new_string == "new"
    assert workspace.patch_items[0].draft.associated_finding_id == "F1"


def test_workspace_manager_rejects_replacement_for_stale_current_patch(
    tmp_path: Path,
) -> None:
    service = ReviewWorkspaceManager(ProjectArtifactStore(tmp_path))
    service.initialize_review(
        scope=_scope(),
        latest_scan_id="scan-8",
        patch_items=[_item("item-1", "src/main/java/demo/User.java", "F1")],
    )
    with service.edit() as workspace:
        current = workspace.current_patch()
        assert current is not None
        current.draft.error_code = "STALE_DRAFT"
        current.draft.message = "binding lost"
    replacement = SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string="replacement",
        diff="replacement diff",
        match_region=SourceRegion(1, 1, 1, 4, 0, 3),
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="attempt to replace stale patch",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_finding=_target_identity("src/main/java/demo/User.java"),
    )

    replaced = service.replace_current_patch(replacement)
    workspace = service.load()

    assert replaced is False
    assert workspace.patch_items[0].draft.error_code == "STALE_DRAFT"
    assert workspace.patch_items[0].draft.new_string == "new"
