from __future__ import annotations

from autopatch_j.core.models import (
    ActiveWorkspace,
    CodeScope,
    CodeScopeKind,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
    WorkspaceStatus,
)


def test_active_workspace_round_trip_preserves_nested_models() -> None:
    workspace = ActiveWorkspace(
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
            PatchReviewItem(
                item_id="item-1",
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
                    rationale="rationale",
                    source_hint="LLM 二次复核（静态扫描未报出问题）",
                    target_check_id="F1",
                    target_snippet="snippet",
                ),
            )
        ],
        current_patch_index=0,
    )

    restored = ActiveWorkspace.from_dict(workspace.to_dict())

    assert restored.mode is WorkspaceStatus.REVIEWING
    assert restored.scope is not None
    assert restored.scope.kind is CodeScopeKind.MULTI_FILE
    assert restored.scope.focus_files == [
        "src/main/java/demo/User.java",
        "src/main/java/demo/UserService.java",
    ]
    current = restored.fetch_current_patch_item()
    assert current is not None
    assert current.status is PatchReviewStatus.PENDING
    assert current.draft.source_hint == "LLM 二次复核（静态扫描未报出问题）"
    assert current.draft.target_check_id == "F1"


def test_fetch_current_patch_item_returns_none_for_out_of_bounds_cursor() -> None:
    workspace = ActiveWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=None,
        latest_scan_id=None,
        patch_items=[],
        current_patch_index=2,
    )

    assert workspace.fetch_current_patch_item() is None


def test_fetch_review_progress_uses_absolute_patch_index() -> None:
    workspace = ActiveWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=None,
        latest_scan_id="scan-1",
        patch_items=[
            PatchReviewItem(
                item_id="item-1",
                file_path="src/main/java/demo/User.java",
                finding_ids=["F1"],
                status=PatchReviewStatus.APPLIED,
                draft=PatchDraftData(
                    file_path="src/main/java/demo/User.java",
                    old_string="old-1",
                    new_string="new-1",
                    diff="diff-1",
                    validation_status="ok",
                    validation_message="ok",
                    validation_errors=[],
                ),
            ),
            PatchReviewItem(
                item_id="item-2",
                file_path="src/main/java/demo/UserService.java",
                finding_ids=["F2"],
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftData(
                    file_path="src/main/java/demo/UserService.java",
                    old_string="old-2",
                    new_string="new-2",
                    diff="diff-2",
                    validation_status="ok",
                    validation_message="ok",
                    validation_errors=[],
                ),
            ),
            PatchReviewItem(
                item_id="item-3",
                file_path="src/main/java/demo/AppConfig.java",
                finding_ids=["F3"],
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftData(
                    file_path="src/main/java/demo/AppConfig.java",
                    old_string="old-3",
                    new_string="new-3",
                    diff="diff-3",
                    validation_status="ok",
                    validation_message="ok",
                    validation_errors=[],
                ),
            ),
        ],
        current_patch_index=1,
    )

    assert workspace.fetch_review_progress() == (2, 3)
