from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.scanners.base import Finding, ScanResult
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.project_scanner_tool import ProjectScannerTool
from autopatch_j.tools.source_reader_tool import SourceReaderTool


def _build_agent(repo_root: Path) -> AutoPatchAgent:
    artifacts = ArtifactManager(repo_root)
    indexer = IndexService(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)
    indexer.perform_rebuild()
    return AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=None)


def test_focus_lock_forces_scan_scope(tmp_path: Path) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    user_service = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("class LegacyConfig {}", encoding="utf-8")
    user_service.write_text("class UserService {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    agent.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])

    captured_scope: list[str] = []

    class FakeScanner:
        def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
            captured_scope[:] = scope
            return ScanResult(
                engine="fake",
                scope=scope,
                targets=scope,
                status="ok",
                message="",
                findings=[
                    Finding(
                        check_id="demo.rule",
                        path="src/main/java/demo/LegacyConfig.java",
                        start_line=1,
                        end_line=1,
                        severity="warning",
                        message="focused finding",
                        snippet="class LegacyConfig {}",
                    )
                ],
            )

    with patch("autopatch_j.tools.project_scanner_tool.get_scanner", return_value=FakeScanner()):
        result = ProjectScannerTool(agent).execute(["."])

    assert captured_scope == ["src/main/java/demo/LegacyConfig.java"]
    assert "焦点约束已生效" in result.message
    assert "LegacyConfig.java" in result.message
    assert "UserService.java" not in result.message


def test_focus_lock_blocks_unrelated_read_and_patch(tmp_path: Path) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    user_service = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "public class LegacyConfig { boolean isDebug(AppConfig config) { return config.getMode().equals(\"debug\"); } }",
        encoding="utf-8",
    )
    user_service.write_text(
        "public class UserService { boolean isAdmin(User user) { return user.getName().equals(\"admin\"); } }",
        encoding="utf-8",
    )

    agent = _build_agent(tmp_path)
    agent.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])

    read_result = SourceReaderTool(agent).execute("src/main/java/demo/UserService.java")
    assert read_result.status == "error"
    assert "焦点约束阻止越界读取" in read_result.message

    patch_result = PatchProposalTool(agent).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='user.getName().equals("admin")',
        new_string='"admin".equals(user.getName())',
        rationale="不应允许越界修复",
        associated_finding_id=None,
    )
    assert patch_result.status == "error"
    assert "焦点约束阻止越界修复" in patch_result.message
