from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.validators.java_syntax import SyntaxValidationResult


def _draft(file_path: str, rationale: str) -> PatchDraft:
    return PatchDraft(
        file_path=file_path,
        old_string="old",
        new_string="new",
        diff="diff",
        validation=SyntaxValidationResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale=rationale,
        target_check_id=None,
        target_snippet=None,
    )


def test_discard_followup_patches_keeps_only_current(tmp_path: Path) -> None:
    artifacts = ArtifactManager(tmp_path)
    artifacts.persist_pending_patch(_draft("third.java", "third"))
    artifacts.persist_pending_patch(_draft("second.java", "second"))
    artifacts.persist_pending_patch(_draft("first.java", "first"))

    discarded = artifacts.discard_followup_patches()

    queue = artifacts.fetch_pending_patches()
    assert [draft.file_path for draft in discarded] == ["second.java", "third.java"]
    assert len(queue) == 1
    assert queue[0].file_path == "first.java"

    workspace = artifacts.fetch_workspace()
    assert workspace is not None
    assert workspace.fetch_current_patch_item() is not None
    assert workspace.fetch_current_patch_item().file_path == "first.java"


def test_pending_patch_compatibility_layer_uses_workspace_storage(tmp_path: Path) -> None:
    artifacts = ArtifactManager(tmp_path)
    artifacts.persist_pending_patch(_draft("first.java", "first"))
    artifacts.persist_pending_patch(_draft("second.java", "second"))

    pending = artifacts.fetch_pending_patches()
    workspace = artifacts.fetch_workspace()

    assert workspace is not None
    assert [draft.file_path for draft in pending] == ["second.java", "first.java"]
    assert workspace.mode.value == "reviewing"
    assert workspace.current_patch_index == 0
    assert [item.file_path for item in workspace.patch_items] == ["second.java", "first.java"]
