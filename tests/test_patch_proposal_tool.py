from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.patch_engine import PatchDraft, PatchEngine
from autopatch_j.core.patch_verifier import PatchVerifier, SyntaxCheckResult
from autopatch_j.core.workspace_manager import WorkspaceManager
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.patch_revision_tool import PatchRevisionTool


class _FakeAgentSession:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.patch_engine = PatchEngine(repo_root)
        self.artifact_manager = ArtifactManager(repo_root)
        self.workspace_manager = WorkspaceManager(self.artifact_manager)
        self.focus_paths = ["src/main/java/demo/UserService.java"]
        self.patch_source_hint = None
        self.patch_verifier = PatchVerifier(repo_root, None)
        self.proposed_patch_draft = None
        self.revised_patch_draft = None

    def is_path_in_focus(self, path: str) -> bool:
        return path in self.focus_paths

    def set_proposed_patch_draft(self, draft) -> None:
        self.proposed_patch_draft = draft

    def clear_proposed_patch_draft(self) -> None:
        self.proposed_patch_draft = None

    def set_revised_patch_draft(self, draft) -> None:
        self.revised_patch_draft = draft


def _draft(file_path: str = "src/main/java/demo/UserService.java") -> PatchDraft:
    return PatchDraft(
        file_path=file_path,
        old_string="old",
        new_string="new",
        diff="diff",
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="fix",
    )


def test_patch_proposal_tool_returns_structured_old_string_error(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )

    session = _FakeAgentSession(tmp_path)
    session.set_proposed_patch_draft(_draft())
    tool = PatchProposalTool(session)
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
    assert session.proposed_patch_draft is None


def test_patch_proposal_tool_stashes_draft_without_appending_workspace(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )
    session = _FakeAgentSession(tmp_path)
    tool = PatchProposalTool(session)

    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return user.getName().equals("admin");',
        new_string='return user != null && user.getName().equals("admin");',
        rationale="fix npe",
        associated_finding_id="F1",
    )

    assert result.status in {"ok", "unavailable"}
    assert session.proposed_patch_draft is not None
    assert session.proposed_patch_draft.new_string == 'return user != null && user.getName().equals("admin");'
    assert session.workspace_manager.load_workspace().patch_items == []


def test_patch_revision_tool_stashes_draft_without_appending_workspace(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )
    session = _FakeAgentSession(tmp_path)
    tool = PatchRevisionTool(session)

    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return user.getName().equals("admin");',
        new_string='return user != null && user.getName().equals("admin");',
        rationale="fix npe",
        associated_finding_id="F1",
    )

    assert result.status in {"ok", "unavailable"}
    assert session.revised_patch_draft is not None
    assert session.revised_patch_draft.new_string == 'return user != null && user.getName().equals("admin");'
    assert session.workspace_manager.load_workspace().patch_items == []
