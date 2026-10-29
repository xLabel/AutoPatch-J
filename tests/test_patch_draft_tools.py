"""真实草案构造配合临时 Java 文件，验证暂存不等于入队或应用。"""

from __future__ import annotations

import pathlib

from autopatch_j.agent import session as agent_session
from autopatch_j.core import finding
from autopatch_j.core.patching import search_replace
from autopatch_j.core.patching import types
from autopatch_j.core.patching import verification
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.review import artifacts as review_artifacts
from autopatch_j.core.review import workspace
from autopatch_j.scanners import models
from autopatch_j.tools.function_calls import propose_patch
from autopatch_j.tools.function_calls import revise_patch


def _make_session(repo_root: pathlib.Path) -> agent_session.AgentSession:
    artifacts = review_artifacts.ProjectArtifactStore(repo_root)
    return agent_session.AgentSession(
        repo_root=repo_root,
        artifact_manager=artifacts,
        workspace_manager=workspace.ReviewWorkspaceManager(artifacts),
        patch_engine=search_replace.SearchReplacePatchEngine(repo_root),
        code_fetcher=source_reader.SourceReader(repo_root),
        symbol_indexer=symbol_index.SymbolIndex(repo_root),
        patch_verifier=verification.PatchQualityVerifier(repo_root, None),
        focus_paths=["src/main/java/demo/UserService.java"],
    )


def _draft(
    file_path: str = "src/main/java/demo/UserService.java",
) -> types.SearchReplacePatchDraft:
    return types.SearchReplacePatchDraft(
        file_path=file_path,
        old_string="old",
        new_string="new",
        diff="diff",
        match_region=finding.SourceRegion(1, 1, 1, 4, 0, 3),
        validation=types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="fix",
    )


def _prepare_associated_revision(
    tmp_path: pathlib.Path,
) -> tuple[agent_session.AgentSession, types.SearchReplacePatchDraft]:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    source = (
        "class UserService {\n    void run() " "{ unsafe(); unrelated(); }\n}\n"
    )
    (java_dir / "UserService.java").write_text(source, encoding="utf-8")
    evidence = "unsafe()"
    start_offset = source.index(evidence)
    session = _make_session(tmp_path)
    session.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                models.Finding(
                    fingerprint=f"apj-v1:{"c" * 64}:1",
                    check_id="demo.unsafe-call",
                    path="src/main/java/demo/UserService.java",
                    region=finding.SourceRegion(
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
    result = propose_patch.ProposePatchTool(session).execute(
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


def test_propose_patch_returns_structured_old_string_error(
    tmp_path: pathlib.Path,
) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        "package demo;\n\npublic class UserSer"
        "vice {\n    public boolean isAdmin(U"
        "ser user) {\n        return user.get"
        'Name().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )

    session = _make_session(tmp_path)
    session.set_proposed_patch_draft(_draft())
    tool = propose_patch.ProposePatchTool(session)
    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return "admin".equals(user.getName());',
        new_string='return user != null && "admin".equa' "ls(user.getName());",
        rationale="fix npe",
        associated_finding_id=None,
    )

    assert result.status == "error"
    assert isinstance(result.payload, dict)
    assert result.payload["file_path"] == "src/main/java/demo/UserService.java"
    assert result.payload["associated_finding_id"] is None
    assert result.payload["error_code"] == "OLD_STRING_NOT_FOUND"
    assert session.proposed_patch_draft is None


def test_propose_patch_normalizes_associated_finding_id_in_error_payload(
    tmp_path: pathlib.Path,
) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    source = (
        "package demo;\n\npublic class UserSer"
        "vice {\n    public boolean isAdmin(U"
        "ser user) {\n        return user.get"
        'Name().equals("admin");\n    }\n}\n'
    )
    (java_dir / "UserService.java").write_text(source, encoding="utf-8")
    evidence = 'return user.getName().equals("admin");'
    start_offset = source.index(evidence)

    session = _make_session(tmp_path)
    session.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                models.Finding(
                    fingerprint=f"apj-v1:{"a" * 64}:1",
                    check_id="demo.rule",
                    path="src/main/java/demo/UserService.java",
                    region=finding.SourceRegion(
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

    result = propose_patch.ProposePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return "admin".equals(user.getName());',
        new_string='return user != null && "admin".equa' "ls(user.getName());",
        rationale="fix npe",
        associated_finding_id="f1",
    )

    assert result.status == "error"
    assert isinstance(result.payload, dict)
    assert result.payload["associated_finding_id"] == "F1"
    assert result.payload["error_code"] == "OLD_STRING_NOT_FOUND"


def test_propose_patch_stashes_draft_without_appending_workspace(
    tmp_path: pathlib.Path,
) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        "package demo;\n\npublic class UserSer"
        "vice {\n    public boolean isAdmin(U"
        "ser user) {\n        return user.get"
        'Name().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )
    session = _make_session(tmp_path)
    tool = propose_patch.ProposePatchTool(session)

    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return user.getName().equals("admin");',
        new_string="return user != null && user.getName" '().equals("admin");',
        rationale="fix npe",
        associated_finding_id=None,
    )

    assert result.status in {"ok", "unavailable"}
    assert session.proposed_patch_draft is not None
    assert (
        session.proposed_patch_draft.new_string
        == "return user != null && user.getName"
        '().equals("admin");'
    )
    assert not session.workspace_manager.load().patch_items


def test_revise_patch_stashes_draft_without_appending_workspace(
    tmp_path: pathlib.Path,
) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "UserService.java").write_text(
        "package demo;\n\npublic class UserSer"
        "vice {\n    public boolean isAdmin(U"
        "ser user) {\n        return user.get"
        'Name().equals("admin");\n    }\n}\n',
        encoding="utf-8",
    )
    session = _make_session(tmp_path)
    old_string = 'return user.getName().equals("admin");'
    initial_new_string = (
        "return user != null && user.getName" '().equals("admin");'
    )
    build_result = session.patch_engine.create_draft(
        "src/main/java/demo/UserService.java",
        old_string,
        initial_new_string,
    )
    session.workspace_manager.add_patch(
        types.SearchReplacePatchDraft(
            file_path="src/main/java/demo/UserService.java",
            old_string=old_string,
            new_string=initial_new_string,
            diff=build_result.diff,
            match_region=build_result.match_region,
            validation=types.SyntaxCheckResult(status="ok", message="ok"),
            status="ok",
            message="ok",
            rationale="initial fix",
        )
    )
    tool = revise_patch.RevisePatchTool(session)

    result = tool.execute(
        file_path="src/main/java/demo/UserService.java",
        old_string=old_string,
        new_string='return user != null && "admin".equa' "ls(user.getName());",
        rationale="improve npe fix",
        associated_finding_id=None,
    )

    assert result.status in {"ok", "unavailable"}
    assert session.revised_patch_draft is not None
    assert (
        session.revised_patch_draft.new_string
        == 'return user != null && "admin".equa'
        "ls(user.getName());"
    )
    assert len(session.workspace_manager.load().patch_items) == 1


def test_associated_patch_is_rejected_when_old_string_misses_finding_region(
    tmp_path: pathlib.Path,
) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    source = (
        "class UserService {\n    void run() " "{ unsafe(); unrelated(); }\n}\n"
    )
    (java_dir / "UserService.java").write_text(source, encoding="utf-8")
    evidence = "unsafe()"
    start_offset = source.index(evidence)
    session = _make_session(tmp_path)
    session.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                models.Finding(
                    fingerprint=f"apj-v1:{"b" * 64}:1",
                    check_id="demo.unsafe-call",
                    path="src/main/java/demo/UserService.java",
                    region=finding.SourceRegion(
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

    result = propose_patch.ProposePatchTool(session).execute(
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
    tmp_path: pathlib.Path,
) -> None:
    session, current_draft = _prepare_associated_revision(tmp_path)

    result = revise_patch.RevisePatchTool(session).execute(
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
    assert current_draft.target_finding is not None
    assert revised.match_region.intersects(current_draft.target_finding.region)
    assert result.payload["associated_finding_id"] == "F1"


def test_revise_patch_rejects_out_of_region_change_when_handle_is_omitted(
    tmp_path: pathlib.Path,
) -> None:
    session, _ = _prepare_associated_revision(tmp_path)

    result = revise_patch.RevisePatchTool(session).execute(
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


def test_revise_patch_rejects_explicit_finding_switch(
    tmp_path: pathlib.Path,
) -> None:
    session, _ = _prepare_associated_revision(tmp_path)

    result = revise_patch.RevisePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string="unsafe()",
        new_string="safe()",
        rationale="switch finding",
        associated_finding_id="F2",
    )

    assert result.status == "error"
    assert result.payload["error_code"] == "REVISION_ASSOCIATION_MISMATCH"
    assert session.revised_patch_draft is None


def test_revise_patch_rejects_without_pending_patch(
    tmp_path: pathlib.Path,
) -> None:
    session = _make_session(tmp_path)

    result = revise_patch.RevisePatchTool(session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string="unsafe()",
        new_string="safe()",
        rationale="no current patch",
        associated_finding_id=None,
    )

    assert result.status == "error"
    assert result.payload["error_code"] == "NO_PENDING_PATCH"
    assert session.revised_patch_draft is None
