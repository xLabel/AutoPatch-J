from __future__ import annotations

import pytest

from autopatch_j.core.domain import (
    ReviewWorkspace,
    CodeScope,
    CodeScopeKind,
    PatchDraftSnapshot,
    ReviewPatchItem,
    PatchReviewStatus,
    WorkspaceStatus,
)
from autopatch_j.scanners import FindingIdentity, SourceRegion


def _target_identity() -> FindingIdentity:
    return FindingIdentity(
        fingerprint=f"apj-v1:{'a' * 64}:1",
        check_id="demo.rule",
        path="src/main/java/demo/User.java",
        region=SourceRegion(1, 1, 1, 8, 0, 7),
    )


def _match_region() -> SourceRegion:
    return SourceRegion(1, 1, 1, 4, 0, 3)


def test_active_workspace_round_trip_preserves_nested_models() -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.MULTI_FILE,
            source_roots=["src/main/java/demo"],
            focus_files=[
                "src/main/java/demo/User.java",
                "src/main/java/demo/UserService.java",
            ],
            is_locked=True,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            ReviewPatchItem(
                item_id="item-1",
                file_path="src/main/java/demo/User.java",
                finding_ids=["F1"],
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftSnapshot(
                    file_path="src/main/java/demo/User.java",
                    old_string="old",
                    new_string="new",
                    diff="diff",
                    match_region=_match_region(),
                    message="ok",
                    validation_status="ok",
                    validation_message="ok",
                    validation_errors=[],
                    rationale="rationale",
                    source_hint="LLM 二次复核（静态扫描未报出问题）",
                    associated_finding_id="F1",
                    source_scan_id="scan-1",
                    target_finding=_target_identity(),
                ),
            )
        ],
        current_patch_index=0,
    )

    restored = ReviewWorkspace.from_dict(workspace.to_dict())

    assert restored.mode is WorkspaceStatus.REVIEWING
    assert restored.scope is not None
    assert restored.scope.kind is CodeScopeKind.MULTI_FILE
    assert restored.scope.focus_files == [
        "src/main/java/demo/User.java",
        "src/main/java/demo/UserService.java",
    ]
    current = restored.current_patch()
    assert current is not None
    assert current.status is PatchReviewStatus.PENDING
    assert current.draft.source_hint == "LLM 二次复核（静态扫描未报出问题）"
    assert current.draft.associated_finding_id == "F1"
    assert current.draft.source_scan_id == "scan-1"
    assert current.draft.target_finding == _target_identity()
    assert current.draft.to_patch_draft().validation.message == "ok"


def test_old_snapshot_schema_is_rejected() -> None:
    with pytest.raises(ValueError, match="旧版"):
        PatchDraftSnapshot.from_dict(
            {
                "file_path": "src/main/java/demo/User.java",
                "old_string": "old",
                "new_string": "new",
                "diff": "diff",
                "validation_status": "ok",
                "validation_message": "ok",
                "target_check_id": "F1",
            }
        )


def test_fetch_current_patch_item_returns_none_for_out_of_bounds_cursor() -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=None,
        latest_scan_id=None,
        patch_items=[],
        current_patch_index=2,
    )

    assert workspace.current_patch() is None


def test_fetch_review_progress_uses_absolute_patch_index() -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=None,
        latest_scan_id="scan-1",
        patch_items=[
            ReviewPatchItem(
                item_id="item-1",
                file_path="src/main/java/demo/User.java",
                finding_ids=["F1"],
                status=PatchReviewStatus.APPLIED,
                draft=PatchDraftSnapshot(
                    file_path="src/main/java/demo/User.java",
                    old_string="old-1",
                    new_string="new-1",
                    diff="diff-1",
                    match_region=_match_region(),
                    message="ok",
                    validation_status="ok",
                    validation_message="ok",
                    validation_errors=[],
                ),
            ),
            ReviewPatchItem(
                item_id="item-2",
                file_path="src/main/java/demo/UserService.java",
                finding_ids=["F2"],
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftSnapshot(
                    file_path="src/main/java/demo/UserService.java",
                    old_string="old-2",
                    new_string="new-2",
                    diff="diff-2",
                    match_region=_match_region(),
                    message="ok",
                    validation_status="ok",
                    validation_message="ok",
                    validation_errors=[],
                ),
            ),
            ReviewPatchItem(
                item_id="item-3",
                file_path="src/main/java/demo/AppConfig.java",
                finding_ids=["F3"],
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftSnapshot(
                    file_path="src/main/java/demo/AppConfig.java",
                    old_string="old-3",
                    new_string="new-3",
                    diff="diff-3",
                    match_region=_match_region(),
                    message="ok",
                    validation_status="ok",
                    validation_message="ok",
                    validation_errors=[],
                ),
            ),
        ],
        current_patch_index=1,
    )

    assert workspace.review_progress() == (2, 3)
