"""用固定持久快照核对嵌套模型及失效 schema 的拒绝边界。"""

from __future__ import annotations

import pytest

from autopatch_j.core import finding
from autopatch_j.core.domain import scope
from autopatch_j.core.domain import workspace as domain_workspace


def _target_identity() -> finding.FindingIdentity:
    return finding.FindingIdentity(
        fingerprint=f"apj-v1:{"a" * 64}:1",
        check_id="demo.rule",
        path="src/main/java/demo/User.java",
        region=finding.SourceRegion(1, 1, 1, 8, 0, 7),
    )


def _match_region() -> finding.SourceRegion:
    return finding.SourceRegion(1, 1, 1, 4, 0, 3)


def test_active_workspace_round_trip_preserves_nested_models() -> None:
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=scope.CodeScope(
            kind=scope.CodeScopeKind.MULTI_FILE,
            source_roots=["src/main/java/demo"],
            focus_files=[
                "src/main/java/demo/User.java",
                "src/main/java/demo/UserService.java",
            ],
            is_locked=True,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            domain_workspace.ReviewPatchItem(
                item_id="item-1",
                file_path="src/main/java/demo/User.java",
                finding_ids=["F1"],
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot(
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

    restored = domain_workspace.ReviewWorkspace.from_dict(workspace.to_dict())

    assert restored.mode is domain_workspace.WorkspaceStatus.REVIEWING
    assert restored.scope is not None
    assert restored.scope.kind is scope.CodeScopeKind.MULTI_FILE
    assert restored.scope.focus_files == [
        "src/main/java/demo/User.java",
        "src/main/java/demo/UserService.java",
    ]
    current = restored.current_patch()
    assert current is not None
    assert current.status is domain_workspace.PatchReviewStatus.PENDING
    assert current.draft.source_hint == "LLM 二次复核（静态扫描未报出问题）"
    assert current.draft.associated_finding_id == "F1"
    assert current.draft.source_scan_id == "scan-1"
    assert current.draft.target_finding == _target_identity()
    assert current.draft.to_patch_draft().validation.message == "ok"


def test_old_snapshot_schema_is_rejected() -> None:
    with pytest.raises(ValueError, match="旧版"):
        domain_workspace.PatchDraftSnapshot.from_dict(
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


def test_fetch_current_patch_item_returns_none_for_out_of_bounds_cursor() -> (
    None
):
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=None,
        latest_scan_id=None,
        patch_items=[],
        current_patch_index=2,
    )

    assert workspace.current_patch() is None


def test_fetch_review_progress_uses_absolute_patch_index() -> None:
    workspace = domain_workspace.ReviewWorkspace(
        mode=domain_workspace.WorkspaceStatus.REVIEWING,
        scope=None,
        latest_scan_id="scan-1",
        patch_items=[
            domain_workspace.ReviewPatchItem(
                item_id="item-1",
                file_path="src/main/java/demo/User.java",
                finding_ids=["F1"],
                status=domain_workspace.PatchReviewStatus.APPLIED,
                draft=domain_workspace.PatchDraftSnapshot(
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
            domain_workspace.ReviewPatchItem(
                item_id="item-2",
                file_path="src/main/java/demo/UserService.java",
                finding_ids=["F2"],
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot(
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
            domain_workspace.ReviewPatchItem(
                item_id="item-3",
                file_path="src/main/java/demo/AppConfig.java",
                finding_ids=["F3"],
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot(
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


@pytest.mark.parametrize("invalid", ["Demo.java", None, {"path": "Demo.java"}])
def test_scope_rejects_corrupt_persisted_file_list(invalid):
    with pytest.raises(TypeError, match="JSON array"):
        scope.CodeScope.from_dict(
            {
                "kind": "single_file",
                "source_roots": [],
                "focus_files": invalid,
                "is_locked": True,
            }
        )


@pytest.mark.parametrize("invalid", [True, "0", 0.5])
def test_workspace_rejects_non_integer_review_cursor(invalid):
    with pytest.raises(TypeError, match="current_patch_index"):
        domain_workspace.ReviewWorkspace.from_dict(
            {
                "current_patch_index": invalid,
                "patch_items": [],
            }
        )
