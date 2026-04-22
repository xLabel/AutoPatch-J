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
from autopatch_j.tools.symbol_search_tool import SymbolSearchTool


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
        'public class LegacyConfig { boolean isDebug(AppConfig config) { return config.getMode().equals("debug"); } }',
        encoding="utf-8",
    )
    user_service.write_text(
        'public class UserService { boolean isAdmin(User user) { return user.getName().equals("admin"); } }',
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
        rationale="should stay in focus scope",
        associated_finding_id=None,
    )
    assert patch_result.status == "error"
    assert "焦点约束阻止越界修复" in patch_result.message


def test_focus_lock_filters_symbol_search_results(tmp_path: Path) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    app_config = tmp_path / "src" / "main" / "java" / "demo" / "AppConfig.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("public class LegacyConfig {}", encoding="utf-8")
    app_config.write_text("public class AppConfig {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    agent.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])

    blocked = SymbolSearchTool(agent).execute("AppConfig")
    assert blocked.status == "ok"
    assert "未找到与 'AppConfig' 相关的符号。" in blocked.message

    allowed = SymbolSearchTool(agent).execute("LegacyConfig")
    assert allowed.status == "ok"
    assert "LegacyConfig.java" in allowed.message
    assert "AppConfig.java" not in allowed.message


def test_source_reader_uses_same_turn_cache(tmp_path: Path) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("public class LegacyConfig {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    agent.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])

    calls: list[str] = []

    def fake_fetch_entry(entry: object) -> str:
        calls.append("fetch")
        return "public class LegacyConfig {}"

    agent.fetcher.fetch_entry = fake_fetch_entry  # type: ignore[method-assign]
    tool = SourceReaderTool(agent)

    first = tool.execute("src/main/java/demo/LegacyConfig.java")
    second = tool.execute("src/main/java/demo/LegacyConfig.java")

    assert first.status == "ok"
    assert second.status == "ok"
    assert calls == ["fetch"]
    assert first.message == second.message
