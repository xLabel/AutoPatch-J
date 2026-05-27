from __future__ import annotations

from pathlib import Path

from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.agent import Agent
from autopatch_j.core.domain import ReviewWorkspace, WorkspaceStatus
from autopatch_j.core.review import ProjectArtifactStore
from autopatch_j.core.project import SourceReader
from autopatch_j.core.project import SymbolIndex
from autopatch_j.core.patching import SearchReplacePatchEngine
from autopatch_j.core.patching import PatchQualityVerifier
from autopatch_j.core.review import ReviewWorkspaceManager
from autopatch_j.scanners.models import Finding, ScanResult
from autopatch_j.scanners.semgrep import build_semgrep_scan_result
from autopatch_j.tools.function_calls.get_finding_detail import GetFindingDetailTool
from autopatch_j.tools.function_calls.propose_patch import ProposePatchTool


def _build_agent(repo_root: Path) -> Agent:
    artifact_manager = ProjectArtifactStore(repo_root)
    workspace_manager = ReviewWorkspaceManager(artifact_manager)
    symbol_indexer = SymbolIndex(repo_root)
    patch_engine = SearchReplacePatchEngine(repo_root)
    code_fetcher = SourceReader(repo_root)
    symbol_indexer.rebuild_index()
    patch_verifier = PatchQualityVerifier(repo_root, None)
    session = AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=patch_verifier
    )
    return Agent(session=session, llm=None)


def test_build_semgrep_scan_result_prefers_source_lines_over_dirty_extra_lines(tmp_path: Path) -> None:
    java_file = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text(
        "package demo;\n\n"
        "public class UserService {\n"
        "    public boolean isAdmin(User user) {\n"
        "        return user.getName().equals(\"admin\");\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    payload = {
        "results": [
            {
                "check_id": "autopatch-j.java.correctness.unsafe-equals-order",
                "path": "src/main/java/demo/UserService.java",
                "start": {"line": 5},
                "end": {"line": 5},
                "extra": {
                    "severity": "WARNING",
                    "message": "unsafe equals order",
                    "lines": "requires login",
                },
            }
        ]
    }

    result = build_semgrep_scan_result(
        payload=payload,
        repo_root=tmp_path,
        scope=["src/main/java/demo/UserService.java"],
        targets=["src/main/java/demo/UserService.java"],
    )

    assert len(result.findings) == 1
    assert result.findings[0].snippet == 'return user.getName().equals("admin");'


def test_get_finding_detail_repairs_legacy_bad_snippet_from_snapshot(tmp_path: Path) -> None:
    java_file = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text(
        "package demo;\n\n"
        "public class UserService {\n"
        "    public boolean isAdmin(User user) {\n"
        "        return user.getName().equals(\"admin\");\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    agent = _build_agent(tmp_path)
    agent.session.set_focus_paths(["src/main/java/demo/UserService.java"])
    agent.session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    check_id="autopatch-j.java.correctness.unsafe-equals-order",
                    path="src\\main\\java\\demo\\UserService.java",
                    start_line=5,
                    end_line=5,
                    severity="warning",
                    message="unsafe equals order",
                    snippet="requires login",
                )
            ],
        )
    )

    result = GetFindingDetailTool(agent.session).execute("F1")

    assert result.status == "ok"
    assert 'return user.getName().equals("admin");' in result.message
    assert "requires login" not in result.message
    assert result.payload["finding_id"] == "F1"
    assert str(result.payload["scan_id"]).startswith("scan-")


def test_get_finding_detail_uses_workspace_scan_before_newer_scan(tmp_path: Path) -> None:
    first_file = tmp_path / "First.java"
    second_file = tmp_path / "Second.java"
    first_file.write_text("class First { void run() {} }", encoding="utf-8")
    second_file.write_text("class Second { void run() {} }", encoding="utf-8")

    agent = _build_agent(tmp_path)
    first_scan_id = agent.session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["First.java"],
            targets=["First.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    check_id="first.rule",
                    path="First.java",
                    start_line=1,
                    end_line=1,
                    severity="warning",
                    message="first finding",
                    snippet="class First { void run() {} }",
                )
            ],
        )
    )
    agent.session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["Second.java"],
            targets=["Second.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    check_id="second.rule",
                    path="Second.java",
                    start_line=1,
                    end_line=1,
                    severity="warning",
                    message="second finding",
                    snippet="class Second { void run() {} }",
                )
            ],
        )
    )
    agent.session.workspace_manager.save(
        ReviewWorkspace(
            mode=WorkspaceStatus.REVIEWING,
            scope=None,
            latest_scan_id=first_scan_id,
            patch_items=[],
        )
    )

    result = GetFindingDetailTool(agent.session).execute("F1")

    assert result.status == "ok"
    assert result.payload["scan_id"] == first_scan_id
    assert result.payload["check_id"] == "first.rule"


def test_get_finding_detail_does_not_fallback_when_workspace_scan_is_missing(tmp_path: Path) -> None:
    (tmp_path / "Demo.java").write_text("class Demo {}", encoding="utf-8")
    agent = _build_agent(tmp_path)
    agent.session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["Demo.java"],
            targets=["Demo.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    check_id="demo.rule",
                    path="Demo.java",
                    start_line=1,
                    end_line=1,
                    severity="warning",
                    message="demo finding",
                    snippet="class Demo {}",
                )
            ],
        )
    )
    agent.session.workspace_manager.save(
        ReviewWorkspace(
            mode=WorkspaceStatus.REVIEWING,
            scope=None,
            latest_scan_id="scan-missing",
            patch_items=[],
        )
    )

    result = GetFindingDetailTool(agent.session).execute("F1")

    assert result.status == "error"
    assert result.payload["error_code"] == "SCAN_ARTIFACT_NOT_FOUND"


def test_patch_proposal_uses_resolved_target_snippet_for_legacy_snapshot(tmp_path: Path) -> None:
    java_file = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text(
        "package demo;\n\n"
        "public class UserService {\n"
        "    public boolean isAdmin(User user) {\n"
        "        return user.getName().equals(\"admin\");\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    agent = _build_agent(tmp_path)
    agent.session.set_focus_paths(["src/main/java/demo/UserService.java"])
    agent.session.artifact_manager.save_scan_result(
        ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                Finding(
                    check_id="autopatch-j.java.correctness.unsafe-equals-order",
                    path="src\\main\\java\\demo\\UserService.java",
                    start_line=5,
                    end_line=5,
                    severity="warning",
                    message="unsafe equals order",
                    snippet="requires login",
                )
            ],
        )
    )

    result = ProposePatchTool(agent.session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return user.getName().equals("admin");',
        new_string='return "admin".equals(user.getName());',
        rationale="fix unsafe equals order",
        associated_finding_id="F1",
    )

    pending = agent.session.proposed_patch_draft
    assert result.status in {"ok", "invalid"}
    assert pending is not None
    assert pending.associated_finding_id == "F1"
    assert pending.source_scan_id is not None
    assert pending.target_check_id == "autopatch-j.java.correctness.unsafe-equals-order"
    assert pending.target_snippet == 'return user.getName().equals("admin");'
