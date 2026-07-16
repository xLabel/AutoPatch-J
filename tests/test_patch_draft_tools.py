from __future__ import annotations

from pathlib import Path

from autopatch_j.core.project import SourceReader
from autopatch_j.core.review import ProjectArtifactStore
from autopatch_j.core.patching import SearchReplacePatchDraft, SearchReplacePatchEngine
from autopatch_j.core.patching import PatchQualityVerifier, SyntaxCheckResult
from autopatch_j.core.review import ReviewWorkspaceManager
from autopatch_j.scanners.models import Finding, ScanResult, SourceRegion
from autopatch_j.tools.function_calls.propose_patch import ProposePatchTool
from autopatch_j.tools.function_calls.revise_patch import RevisePatchTool


class _FakeAgentSession:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.patch_engine = SearchReplacePatchEngine(repo_root)
        self.artifact_manager = ProjectArtifactStore(repo_root)
        self.workspace_manager = ReviewWorkspaceManager(self.artifact_manager)
        self.code_fetcher = SourceReader(repo_root)
        self.focus_paths = ["src/main/java/demo/UserService.java"]
        self.patch_source_hint = None
        self.patch_verifier = PatchQualityVerifier(repo_root, None)
        self.proposed_patch_draft = None
        self.revised_patch_draft = None

    def is_path_in_focus(self, path: str) -> bool:
        return path in self.focus_paths

    def normalize_repo_path(self, path: str) -> str:
        return str(path).replace("\\", "/").strip()

    def set_proposed_patch_draft(self, draft) -> None:
        self.proposed_patch_draft = draft

    def clear_proposed_patch_draft(self) -> None:
        self.proposed_patch_draft = None

    def set_revised_patch_draft(self, draft) -> None:
        self.revised_patch_draft = draft


def _draft(file_path: str = "src/main/java/demo/UserService.java") -> SearchReplacePatchDraft:
    return SearchReplacePatchDraft(
        file_path=file_path,
        old_string="old",
        new_string="new",
        diff="diff",
        match_region=SourceRegion(1, 1, 1, 4, 0, 3),
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="fix",
    )


def _prepare_associated_revision(
    tmp_path: Path,
) -> tuple[_FakeAgentSession, SearchReplacePatchDraft]:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    source = "class UserService {\n    void run() { unsafe(); unrelated(); }\n}\n"
    (java_dir / "UserService.java").write_text(source, encoding="utf-8")
    evidence = "unsafe()"
    start_offset = source.index(evidence)
    session = _FakeAgentSession(tmp_path)
    session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    fingerprint=f"apj-v1:{'c' * 64}:1",
                    check_id="demo.unsafe-call",
                    path="src/main/java/demo/UserService.java",
                    region=SourceRegion(
                        2,
                        18,
                        2,
                        18 + len(evidence),
                        start_offset,
                        start_offset + len(evidence),
                    ),
                    severity="error",
                    message="unsafe call",
                    snippet=evidence,
                )
            ],
        )
    )
    result = ProposePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string=evidence,
        new_string="guarded()",
        rationale="initial fix",
        associated_finding_id="F1",
    )
    assert result.status in {"ok", "unavailable"}
    assert session.proposed_patch_draft is not None
    current_draft = session.proposed_patch_draft
    session.workspace_manager.add_patch(current_draft)
    return session, current_draft


def test_propose_patch_returns_structured_old_string_error(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )

    session = _FakeAgentSession(tmp_path)
    session.set_proposed_patch_draft(_draft())
    tool = ProposePatchTool(session)
    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return "admin".equals(user.getName());',
        new_string='return user != null && "admin".equals(user.getName());',
        rationale="fix npe",
        associated_finding_id=None,
    )

    assert result.status == "error"
    assert isinstance(result.payload, dict)
    assert result.payload["file_path"] == "src/main/java/demo/UserService.java"
    assert result.payload["associated_finding_id"] is None
    assert result.payload["error_code"] == "OLD_STRING_NOT_FOUND"
    assert session.proposed_patch_draft is None


def test_propose_patch_normalizes_associated_finding_id_in_error_payload(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    source = 'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n'
    (java_dir / "UserService.java").write_text(source, encoding="utf-8")
    evidence = 'return user.getName().equals("admin");'
    start_offset = source.index(evidence)

    session = _FakeAgentSession(tmp_path)
    session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    fingerprint=f"apj-v1:{'a' * 64}:1",
                    check_id="demo.rule",
                    path="src/main/java/demo/UserService.java",
                    region=SourceRegion(
                        5,
                        9,
                        5,
                        9 + len(evidence),
                        start_offset,
                        start_offset + len(evidence),
                    ),
                    severity="warning",
                    message="unsafe equals order",
                    snippet='return user.getName().equals("admin");',
                )
            ],
        )
    )

    result = ProposePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return "admin".equals(user.getName());',
        new_string='return user != null && "admin".equals(user.getName());',
        rationale="fix npe",
        associated_finding_id="f1",
    )

    assert result.status == "error"
    assert isinstance(result.payload, dict)
    assert result.payload["associated_finding_id"] == "F1"
    assert result.payload["error_code"] == "OLD_STRING_NOT_FOUND"


def test_propose_patch_stashes_draft_without_appending_workspace(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )
    session = _FakeAgentSession(tmp_path)
    tool = ProposePatchTool(session)

    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return user.getName().equals("admin");',
        new_string='return user != null && user.getName().equals("admin");',
        rationale="fix npe",
        associated_finding_id=None,
    )

    assert result.status in {"ok", "unavailable"}
    assert session.proposed_patch_draft is not None
    assert session.proposed_patch_draft.new_string == 'return user != null && user.getName().equals("admin");'
    assert session.workspace_manager.load().patch_items == []


def test_revise_patch_stashes_draft_without_appending_workspace(tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        'package demo;\n\npublic class UserService {\n    public boolean isAdmin(User user) {\n        return user.getName().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )
    session = _FakeAgentSession(tmp_path)
    old_string = 'return user.getName().equals("admin");'
    initial_new_string = 'return user != null && user.getName().equals("admin");'
    build_result = session.patch_engine.create_draft(
        "src/main/java/demo/UserService.java",
        old_string,
        initial_new_string,
    )
    session.workspace_manager.add_patch(
        SearchReplacePatchDraft(
            file_path="src/main/java/demo/UserService.java",
            old_string=old_string,
            new_string=initial_new_string,
            diff=build_result.diff,
            match_region=build_result.match_region,
            validation=SyntaxCheckResult(status="ok", message="ok"),
            status="ok",
            message="ok",
            rationale="initial fix",
        )
    )
    tool = RevisePatchTool(session)

    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string=old_string,
        new_string='return user != null && "admin".equals(user.getName());',
        rationale="improve npe fix",
        associated_finding_id=None,
    )

    assert result.status in {"ok", "unavailable"}
    assert session.revised_patch_draft is not None
    assert session.revised_patch_draft.new_string == 'return user != null && "admin".equals(user.getName());'
    assert len(session.workspace_manager.load().patch_items) == 1


def test_associated_patch_is_rejected_when_old_string_misses_finding_region(
    tmp_path: Path,
) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    source = "class UserService {\n    void run() { unsafe(); unrelated(); }\n}\n"
    (java_dir / "UserService.java").write_text(source, encoding="utf-8")
    evidence = "unsafe()"
    start_offset = source.index(evidence)
    session = _FakeAgentSession(tmp_path)
    session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    fingerprint=f"apj-v1:{'b' * 64}:1",
                    check_id="demo.unsafe-call",
                    path="src/main/java/demo/UserService.java",
                    region=SourceRegion(
                        2,
                        18,
                        2,
                        18 + len(evidence),
                        start_offset,
                        start_offset + len(evidence),
                    ),
                    severity="error",
                    message="unsafe call",
                    snippet=evidence,
                )
            ],
        )
    )

    result = ProposePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string="unrelated()",
        new_string="safe()",
        rationale="changes another call",
        associated_finding_id="F1",
    )

    assert result.status == "error"
    assert result.payload["error_code"] == "PATCH_OUTSIDE_FINDING_REGION"
    assert session.proposed_patch_draft is None


def test_revise_patch_inherits_current_finding_binding_when_handle_is_omitted(
    tmp_path: Path,
) -> None:
    session, current_draft = _prepare_associated_revision(tmp_path)

    result = RevisePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string="unsafe()",
        new_string="safe()",
        rationale="revised fix",
        associated_finding_id=None,
    )

    assert result.status in {"ok", "unavailable"}
    assert session.revised_patch_draft is not None
    revised = session.revised_patch_draft
    assert revised.associated_finding_id == "F1"
    assert revised.source_scan_id == current_draft.source_scan_id
    assert revised.target_finding == current_draft.target_finding
    assert revised.match_region.intersects(current_draft.target_finding.region)
    assert result.payload["associated_finding_id"] == "F1"


def test_revise_patch_rejects_out_of_region_change_when_handle_is_omitted(
    tmp_path: Path,
) -> None:
    session, _ = _prepare_associated_revision(tmp_path)

    result = RevisePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string="unrelated()",
        new_string="other()",
        rationale="changes unrelated code",
        associated_finding_id=None,
    )

    assert result.status == "error"
    assert result.payload["associated_finding_id"] == "F1"
    assert result.payload["error_code"] == "PATCH_OUTSIDE_FINDING_REGION"
    assert session.revised_patch_draft is None


def test_revise_patch_rejects_explicit_finding_switch(tmp_path: Path) -> None:
    session, _ = _prepare_associated_revision(tmp_path)

    result = RevisePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string="unsafe()",
        new_string="safe()",
        rationale="switch finding",
        associated_finding_id="F2",
    )

    assert result.status == "error"
    assert result.payload["error_code"] == "REVISION_ASSOCIATION_MISMATCH"
    assert session.revised_patch_draft is None


def test_revise_patch_rejects_without_pending_patch(tmp_path: Path) -> None:
    session = _FakeAgentSession(tmp_path)

    result = RevisePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string="unsafe()",
        new_string="safe()",
        rationale="no current patch",
        associated_finding_id=None,
    )

    assert result.status == "error"
    assert result.payload["error_code"] == "NO_PENDING_PATCH"
    assert session.revised_patch_draft is None
