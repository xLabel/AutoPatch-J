from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool


class _FakeAgentContext:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.patch_engine = PatchEngine(repo_root)
        self.artifacts = ArtifactManager(repo_root)
        self.focus_paths = ["src/main/java/demo/UserService.java"]

    def is_path_in_focus(self, path: str) -> bool:
        return path in self.focus_paths


def test_patch_proposal_tool_returns_structured_old_string_error(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )

    tool = PatchProposalTool(_FakeAgentContext(tmp_path))
    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return "admin".equals(user.getName());',
        new_string='return user != null && "admin".equals(user.getName());',
        rationale="fix npe",
        associated_finding_id="F1",
    )

    assert result.status == "error"
    assert isinstance(result.payload, dict)
    assert result.payload["file_path"] == "src/main/java/demo/UserService.java"
    assert result.payload["associated_finding_id"] == "F1"
    assert result.payload["error_code"] == "OLD_STRING_NOT_FOUND"
