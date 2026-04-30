from __future__ import annotations

from pathlib import Path

from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.agent import Agent
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.patch_verifier import PatchVerifier
from autopatch_j.core.workspace_manager import WorkspaceManager
from autopatch_j.scanners.base import Finding, ScanResult
from autopatch_j.scanners.semgrep import normalize_semgrep_payload
from autopatch_j.tools.finding_retriever_tool import FindingRetrieverTool
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool


def _build_agent(repo_root: Path) -> Agent:
    artifact_manager = ArtifactManager(repo_root)
    workspace_manager = WorkspaceManager(artifact_manager)
    symbol_indexer = SymbolIndexer(repo_root)
    patch_engine = PatchEngine(repo_root)
    code_fetcher = CodeFetcher(repo_root)
    symbol_indexer.rebuild_index()
    patch_verifier = PatchVerifier(repo_root, None)
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


def test_normalize_semgrep_payload_prefers_source_lines_over_dirty_extra_lines(tmp_path: Path) -> None:
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

    result = normalize_semgrep_payload(
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

    result = FindingRetrieverTool(agent.session).execute("F1")

    assert result.status == "ok"
    assert 'return user.getName().equals("admin");' in result.message
    assert "requires login" not in result.message


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

    result = PatchProposalTool(agent.session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return user.getName().equals("admin");',
        new_string='return "admin".equals(user.getName());',
        rationale="fix unsafe equals order",
        associated_finding_id="F1",
    )

    pending = agent.session.proposed_patch_draft
    assert result.status in {"ok", "invalid"}
    assert pending is not None
    assert pending.target_snippet == 'return user.getName().equals("admin");'
