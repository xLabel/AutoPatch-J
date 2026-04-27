from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.core.patch_verifier import SyntaxCheckResult
from autopatch_j.core.workspace_manager import WorkspaceManager


def _draft(file_path: str, rationale: str) -> PatchDraft:
    return PatchDraft(
        file_path=file_path,
        old_string="old",
        new_string="new",
        diff="diff",
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale=rationale,
        target_check_id=None,
        target_snippet=None,
    )


def test_add_pending_patch_uses_workspace_storage_via_manager(tmp_path: Path) -> None:
    artifacts = ArtifactManager(tmp_path)
    workspace_manager = WorkspaceManager(artifacts)
    
    workspace_manager.add_pending_patch(_draft("first.java", "first"))
    workspace_manager.add_pending_patch(_draft("second.java", "second"))

    workspace = artifacts.load_workspace()

    assert workspace is not None
    assert workspace.mode.value == "reviewing"
    assert workspace.current_patch_index == 0
    # 注意：现在 add_pending_patch 是 append 模式（符合队列直觉），之前兼容层可能是 prepend 或其他。
    # 我们现在的 add_pending_patch 实现是 workspace.patch_items.append(...)
    assert [item.file_path for item in workspace.patch_items] == ["first.java", "second.java"]
    
    current = workspace_manager.load_pending_patch()
    assert current is not None
    assert current.file_path == "first.java"
