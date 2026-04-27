from __future__ import annotations

from pathlib import Path

from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.patch_verifier import PatchVerifier
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.source_reader_tool import SourceReaderTool
from autopatch_j.tools.symbol_search_tool import SymbolSearchTool


def _build_agent(repo_root: Path) -> AutoPatchAgent:
    artifacts = ArtifactManager(repo_root)
    symbol_indexer = SymbolIndexer(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)
    symbol_indexer.rebuild_index()
    session = AgentSession(repo_root, artifacts, symbol_indexer, patch_engine, fetcher)
    return AutoPatchAgent(session=session, llm=None)


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
    agent.session.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])
    read_result = SourceReaderTool(agent.session).execute("src/main/java/demo/UserService.java")
    assert read_result.status == "error"
    assert "焦点约束阻止越界读取" in read_result.message

    patch_result = PatchProposalTool(agent.session).execute(
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
    agent.session.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])
    blocked = SymbolSearchTool(agent.session).execute("AppConfig")
    assert blocked.status == "ok"
    assert "未找到与 'AppConfig' 相关的符号。" in blocked.message

    allowed = SymbolSearchTool(agent.session).execute("LegacyConfig")
    assert allowed.status == "ok"
    assert "LegacyConfig.java" in allowed.message
    assert "AppConfig.java" not in allowed.message


def test_source_reader_uses_same_turn_cache(tmp_path: Path) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("public class LegacyConfig {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    agent.session.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])
    calls: list[str] = []

    def fake_fetch_entry_source(entry: object) -> str:
        calls.append("fetch")
        return "public class LegacyConfig {}"

    agent.session.fetcher.fetch_entry_source = fake_fetch_entry_source  # type: ignore[method-assign]
    tool = SourceReaderTool(agent.session)

    first = tool.execute("src/main/java/demo/LegacyConfig.java")
    second = tool.execute("src/main/java/demo/LegacyConfig.java")

    assert first.status == "ok"
    assert second.status == "ok"
    assert calls == ["fetch"]
    assert first.message == second.message
