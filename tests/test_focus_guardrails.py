from __future__ import annotations

from pathlib import Path

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.session import AgentSession
from autopatch_j.core.patching import PatchQualityVerifier, SearchReplacePatchEngine
from autopatch_j.core.project import SourceReader, SymbolIndex
from autopatch_j.core.review import ProjectArtifactStore, ReviewWorkspaceManager
from autopatch_j.tools.function_calls.propose_patch import ProposePatchTool
from autopatch_j.tools.function_calls.read_source_context import ReadSourceContextTool
from autopatch_j.tools.function_calls.read_source_file import ReadSourceFileTool
from autopatch_j.tools.function_calls.search_symbols import SearchSymbolsTool


def _build_agent(repo_root: Path) -> Agent:
    artifact_manager = ProjectArtifactStore(repo_root)
    workspace_manager = ReviewWorkspaceManager(artifact_manager)
    symbol_indexer = SymbolIndex(repo_root)
    patch_engine = SearchReplacePatchEngine(repo_root)
    code_fetcher = SourceReader(repo_root)
    symbol_indexer.rebuild_index()
    session = AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=PatchQualityVerifier(repo_root, None),
    )
    return Agent(session=session, llm=None)


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
    read_result = ReadSourceFileTool(agent.session).execute("src/main/java/demo/UserService.java")
    assert read_result.status == "error"
    assert "焦点约束阻止越界读取" in read_result.message

    patch_result = ProposePatchTool(agent.session).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='user.getName().equals("admin")',
        new_string='"admin".equals(user.getName())',
        rationale="should stay in focus scope",
        associated_finding_id=None,
    )
    assert patch_result.status == "error"
    assert "焦点约束阻止越界修复" in patch_result.message


def test_tools_reject_paths_outside_repo_even_without_focus_lock(tmp_path: Path) -> None:
    outside_file = tmp_path.parent / "Outside.java"
    outside_file.write_text("public class Outside {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    read_result = ReadSourceFileTool(agent.session).execute("../Outside.java")
    assert read_result.status == "error"
    assert "读取失败" in read_result.message

    patch_result = ProposePatchTool(agent.session).execute(
        file_path="../Outside.java",
        old_string="Outside",
        new_string="Inside",
        rationale="must stay inside repo",
        associated_finding_id=None,
    )
    assert patch_result.status == "error"
    assert patch_result.payload["error_code"] == "OUT_OF_FOCUS"


def test_focus_lock_filters_symbol_search_results(tmp_path: Path) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    app_config = tmp_path / "src" / "main" / "java" / "demo" / "AppConfig.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("public class LegacyConfig {}", encoding="utf-8")
    app_config.write_text("public class AppConfig {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    agent.session.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])
    blocked = SearchSymbolsTool(agent.session).execute("AppConfig")
    assert blocked.status == "ok"
    assert "未找到" in blocked.message

    allowed = SearchSymbolsTool(agent.session).execute("LegacyConfig")
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

    agent.session.code_fetcher.fetch_entry_source = fake_fetch_entry_source  # type: ignore[method-assign]
    tool = ReadSourceFileTool(agent.session)

    first = tool.execute("src/main/java/demo/LegacyConfig.java")
    second = tool.execute("src/main/java/demo/LegacyConfig.java")

    assert first.status == "ok"
    assert second.status == "ok"
    assert calls == ["fetch"]
    assert first.message == second.message


def test_source_reader_cache_is_split_by_tool_semantics(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"line {index}" for index in range(1, 40)), encoding="utf-8")

    agent = _build_agent(tmp_path)
    agent.session.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])

    file_result = ReadSourceFileTool(agent.session).execute("src/main/java/demo/LegacyConfig.java")
    context_result = ReadSourceContextTool(agent.session).execute("src/main/java/demo/LegacyConfig.java", 25)

    assert file_result.status == "ok"
    assert context_result.status == "ok"
    assert file_result.message != context_result.message
