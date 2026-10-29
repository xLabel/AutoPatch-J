"""使用临时目录核对扫描与审核工件；不依赖真实扫描进程。"""

from __future__ import annotations

import pathlib

from autopatch_j.core import finding
from autopatch_j.core.patching import types
from autopatch_j.core.review import artifacts as review_artifacts
from autopatch_j.core.review import workspace as review_workspace
from autopatch_j.scanners import models


def _draft(file_path: str, rationale: str) -> types.SearchReplacePatchDraft:
    return types.SearchReplacePatchDraft(
        file_path=file_path,
        old_string="old",
        new_string="new",
        diff="diff",
        match_region=finding.SourceRegion(1, 1, 1, 4, 0, 3),
        validation=types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale=rationale,
    )


def test_add_pending_patch_uses_workspace_storage_via_manager(
    tmp_path: pathlib.Path,
) -> None:
    artifacts = review_artifacts.ProjectArtifactStore(tmp_path)
    workspace_manager = review_workspace.ReviewWorkspaceManager(artifacts)

    workspace_manager.add_patch(_draft("first.java", "first"))
    workspace_manager.add_patch(_draft("second.java", "second"))

    workspace = artifacts.load_review_workspace()

    assert workspace is not None
    assert workspace.mode.value == "reviewing"
    assert workspace.current_patch_index == 0
    # 注意：现在 add_patch 是 append 模式（符合队列直觉），之前兼容层可能是 prepend 或其他。
    # 我们现在的 add_patch 实现是 workspace.patch_items.append(...)
    assert [item.file_path for item in workspace.patch_items] == [
        "first.java",
        "second.java",
    ]

    current = workspace_manager.load_current_patch_draft()
    assert current is not None
    assert current.file_path == "first.java"


def test_scan_artifact_ids_do_not_collide_for_fast_saves(
    tmp_path: pathlib.Path,
) -> None:
    artifacts = review_artifacts.ProjectArtifactStore(tmp_path)
    result = models.ScanResult(
        engine="semgrep",
        scope=["."],
        targets=["Demo.java"],
        status="ok",
        message="ok",
        findings=[],
    )

    first_id = artifacts.save_scan_result(result)
    second_id = artifacts.save_scan_result(result)

    assert first_id != second_id
    assert artifacts.load_scan_result(first_id) is not None
    assert artifacts.load_scan_result(second_id) is not None
