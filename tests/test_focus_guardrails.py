"""临时源码、索引及状态目录覆盖模型工具的焦点与真实路径边界。"""

from __future__ import annotations

import pathlib

from autopatch_j.agent import session as agent_session
from autopatch_j.core.patching import search_replace
from autopatch_j.core.patching import verification
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.project import symbols
from autopatch_j.core.review import artifacts
from autopatch_j.core.review import workspace
from autopatch_j.tools.function_calls import propose_patch
from autopatch_j.tools.function_calls import read_source_context
from autopatch_j.tools.function_calls import read_source_file
from autopatch_j.tools.function_calls import search_symbols


def _build_session(repo_root: pathlib.Path) -> agent_session.AgentSession:
    artifact_manager = artifacts.ProjectArtifactStore(repo_root)
    workspace_manager = workspace.ReviewWorkspaceManager(artifact_manager)
    symbol_indexer = symbol_index.SymbolIndex(repo_root)
    patch_engine = search_replace.SearchReplacePatchEngine(repo_root)
    code_fetcher = source_reader.SourceReader(repo_root)
    symbol_indexer.rebuild_index()
    session = agent_session.AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=verification.PatchQualityVerifier(repo_root, None),
    )
    return session


def test_focus_lock_blocks_unrelated_read_and_patch(
    tmp_path: pathlib.Path,
) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    user_service = (
        tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    )
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "public class LegacyConfig { boolean"
        " isDebug(AppConfig config) { return"
        ' config.getMode().equals("debug"); '
        "} }",
        encoding="utf-8",
    )
    user_service.write_text(
        "public class UserService { boolean "
        "isAdmin(User user) { return user.ge"
        'tName().equals("admin"); } }',
        encoding="utf-8",
    )

    context = _build_session(tmp_path)
    context.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])
    read_result = read_source_file.ReadSourceFileTool(context).execute(
        "src/main/java/demo/UserService.java"
    )
    assert read_result.status == "error"
    assert "焦点约束阻止越界读取" in read_result.message

    patch_result = propose_patch.ProposePatchTool(context).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='user.getName().equals("admin")',
        new_string='"admin".equals(user.getName())',
        rationale="should stay in focus scope",
        associated_finding_id=None,
    )
    assert patch_result.status == "error"
    assert "焦点约束阻止越界修复" in patch_result.message


def test_tools_reject_paths_outside_repo_even_without_focus_lock(
    tmp_path: pathlib.Path,
) -> None:
    outside_file = tmp_path.parent / "Outside.java"
    outside_file.write_text("public class Outside {}", encoding="utf-8")

    context = _build_session(tmp_path)
    read_result = read_source_file.ReadSourceFileTool(context).execute(
        "../Outside.java"
    )
    assert read_result.status == "error"
    assert "读取失败" in read_result.message

    patch_result = propose_patch.ProposePatchTool(context).execute(
        file_path="../Outside.java",
        old_string="Outside",
        new_string="Inside",
        rationale="must stay inside repo",
        associated_finding_id=None,
    )
    assert patch_result.status == "error"
    assert patch_result.payload["error_code"] == "OUT_OF_FOCUS"


def test_focus_lock_filters_symbol_search_results(
    tmp_path: pathlib.Path,
) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    app_config = tmp_path / "src" / "main" / "java" / "demo" / "AppConfig.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("public class LegacyConfig {}", encoding="utf-8")
    app_config.write_text("public class AppConfig {}", encoding="utf-8")

    context = _build_session(tmp_path)
    context.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])
    blocked = search_symbols.SearchSymbolsTool(context).execute("AppConfig")
    assert blocked.status == "ok"
    assert "未找到" in blocked.message

    allowed = search_symbols.SearchSymbolsTool(context).execute("LegacyConfig")
    assert allowed.status == "ok"
    assert "LegacyConfig.java" in allowed.message
    assert "AppConfig.java" not in allowed.message


def test_symbol_search_filters_stale_state_entries_without_focus_lock(
    tmp_path: pathlib.Path,
) -> None:
    state_file = tmp_path / ".autopatch-j" / "StateOnly.java"
    safe_file = tmp_path / "src" / "SafeSource.java"
    state_file.parent.mkdir(parents=True)
    safe_file.parent.mkdir(parents=True)
    state_file.write_text("class SharedSymbol {}", encoding="utf-8")
    safe_file.write_text("class SharedSymbol {}", encoding="utf-8")
    (tmp_path / "StateAlias.java").symlink_to(state_file)
    (tmp_path / "SafeAlias.java").symlink_to(safe_file)
    context = _build_session(tmp_path)
    context.symbol_indexer.search = lambda query, limit=20: [
        symbols.SymbolIndexEntry(
            path="StateAlias.java",
            name="SharedSymbol",
            kind="class",
        ),
        symbols.SymbolIndexEntry(
            path="SafeAlias.java",
            name="SharedSymbol",
            kind="class",
        ),
    ]

    assert context.is_focus_locked() is False
    result = search_symbols.SearchSymbolsTool(context).execute("SharedSymbol")

    assert result.status == "ok"
    assert result.payload["paths"] == ["SafeAlias.java"]
    assert "StateAlias.java" not in result.message


def test_source_reader_uses_same_turn_cache(tmp_path: pathlib.Path) -> None:
    legacy = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("public class LegacyConfig {}", encoding="utf-8")

    context = _build_session(tmp_path)
    context.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])
    calls: list[str] = []

    def fake_fetch_entry_source(entry: object) -> str:
        del entry
        calls.append("fetch")
        return "public class LegacyConfig {}"

    context.code_fetcher.fetch_entry_source = fake_fetch_entry_source
    tool = read_source_file.ReadSourceFileTool(context)

    first = tool.execute("src/main/java/demo/LegacyConfig.java")
    second = tool.execute("src/main/java/demo/LegacyConfig.java")

    assert first.status == "ok"
    assert second.status == "ok"
    assert calls == ["fetch"]
    assert first.message == second.message


def test_source_reader_cache_is_split_by_tool_semantics(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "src" / "main" / "java" / "demo" / "LegacyConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(f"line {index}" for index in range(1, 40)), encoding="utf-8"
    )

    context = _build_session(tmp_path)
    context.set_focus_paths(["src/main/java/demo/LegacyConfig.java"])

    file_result = read_source_file.ReadSourceFileTool(context).execute(
        "src/main/java/demo/LegacyConfig.java"
    )
    context_result = read_source_context.ReadSourceContextTool(context).execute(
        "src/main/java/demo/LegacyConfig.java", 25
    )

    assert file_result.status == "ok"
    assert context_result.status == "ok"
    assert file_result.message != context_result.message
