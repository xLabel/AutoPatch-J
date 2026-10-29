"""临时源码覆盖读取窗口、唯一路径修正、状态目录和软链接隔离。"""

from __future__ import annotations

import pathlib

from autopatch_j.agent import session as agent_session
from autopatch_j.core.patching import search_replace
from autopatch_j.core.project import scope as project_scope
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index as project_symbol_index
from autopatch_j.core.project import symbols
from autopatch_j.core.review import artifacts
from autopatch_j.core.review import workspace
from autopatch_j.tools.function_calls import read_source_block
from autopatch_j.tools.function_calls import read_source_context
from autopatch_j.tools.function_calls import read_source_file


def _build_session(repo_root: pathlib.Path) -> agent_session.AgentSession:
    artifact_manager = artifacts.ProjectArtifactStore(repo_root)
    workspace_manager = workspace.ReviewWorkspaceManager(artifact_manager)
    symbol_indexer = project_symbol_index.SymbolIndex(repo_root)
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
    )
    return session


def test_read_source_context_uses_fixed_window_without_line_numbers(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "src" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(f"line {index}" for index in range(1, 151)), encoding="utf-8"
    )

    context = _build_session(tmp_path)
    result = read_source_context.ReadSourceContextTool(context).execute(
        "src/Demo.java", 50
    )

    assert result.status == "ok"
    assert result.summary == "已读取源代码上下文: src/Demo.java:30-130"
    assert "line 30" in result.message
    assert "line 130" in result.message
    assert "\nline 29\n" not in result.message
    assert "\nline 131\n" not in result.message


def test_read_source_context_clips_at_file_edges(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "src" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(f"line {index}" for index in range(1, 20)), encoding="utf-8"
    )

    context = _build_session(tmp_path)
    result = read_source_context.ReadSourceContextTool(context).execute(
        "src/Demo.java", 3
    )

    assert result.status == "ok"
    assert result.payload["start_line"] == 1
    assert result.payload["end_line"] == 19


def test_read_source_file_keeps_full_file_guard(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "src" / "Huge.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "public class Huge {\n" + ("    void x() {}\n" * 9000) + "}\n",
        encoding="utf-8",
    )

    context = _build_session(tmp_path)
    result = read_source_file.ReadSourceFileTool(context).execute(
        "src/Huge.java"
    )

    assert result.status == "ok"
    assert "已拒绝全量代码注入" in result.message
    assert "read_source_block/read_source_context" in result.message


def test_read_source_file_corrects_missing_path_only_when_candidate_is_unique(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "src" / "main" / "java" / "demo" / "User.java"
    source.parent.mkdir(parents=True)
    source.write_text("public class User {}", encoding="utf-8")

    context = _build_session(tmp_path)
    result = read_source_file.ReadSourceFileTool(context).execute(
        "wrong/User.java"
    )

    assert result.status == "ok"
    assert "src/main/java/demo/User.java" in result.message


def test_read_source_file_rejects_ambiguous_missing_path_candidates(
    tmp_path: pathlib.Path,
) -> None:
    first = (
        tmp_path / "module-a" / "src" / "main" / "java" / "demo" / "User.java"
    )
    second = (
        tmp_path / "module-b" / "src" / "main" / "java" / "demo" / "User.java"
    )
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("public class User {}", encoding="utf-8")
    second.write_text("public class User {}", encoding="utf-8")

    context = _build_session(tmp_path)
    result = read_source_file.ReadSourceFileTool(context).execute(
        "wrong/User.java"
    )

    assert result.status == "error"
    assert "同名候选不唯一" in result.message
    assert "module-a/src/main/java/demo/User.java" in result.message
    assert "module-b/src/main/java/demo/User.java" in result.message
    assert result.payload["candidates"] == [
        "module-a/src/main/java/demo/User.java",
        "module-b/src/main/java/demo/User.java",
    ]


def test_read_source_file_filters_missing_path_candidates_by_focus(
    tmp_path: pathlib.Path,
) -> None:
    focused = (
        tmp_path / "module-a" / "src" / "main" / "java" / "demo" / "User.java"
    )
    outside = (
        tmp_path / "module-b" / "src" / "main" / "java" / "demo" / "User.java"
    )
    focused.parent.mkdir(parents=True)
    outside.parent.mkdir(parents=True)
    focused.write_text(
        "public class User { String scope() " '{ return "focused"; } }',
        encoding="utf-8",
    )
    outside.write_text(
        "public class User { String scope() " '{ return "outside"; } }',
        encoding="utf-8",
    )

    context = _build_session(tmp_path)
    context.set_focus_paths(["module-a/src/main/java/demo/User.java"])
    result = read_source_file.ReadSourceFileTool(context).execute(
        "wrong/User.java"
    )

    assert result.status == "ok"
    assert "focused" in result.message
    assert "outside" not in result.message
    assert "module-a/src/main/java/demo/User.java" in result.message


def test_read_source_block_finds_method_when_line_is_inside_body(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "src" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "public class Demo {",
                "    private int value;",
                "    public Demo() {",
                "        this.value = 1;",
                "    }",
                "    public void run() {",
                "        call();",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    context = _build_session(tmp_path)
    result = read_source_block.ReadSourceBlockTool(context).execute(
        "src/Demo.java", 7
    )

    assert result.status == "ok"
    assert "public void run()" in result.message
    assert "call();" in result.message
    assert "private int value" not in result.message


def test_read_source_block_returns_class_for_field_line(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "src" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "public class Demo {\n    private int"
        " value;\n    public void run() {}\n}\n",
        encoding="utf-8",
    )

    context = _build_session(tmp_path)
    result = read_source_block.ReadSourceBlockTool(context).execute(
        "src/Demo.java", 2
    )

    assert result.status == "ok"
    assert "public class Demo" in result.message
    assert "private int value" in result.message


def test_project_state_file_is_blocked_from_scope_focus_and_source_reads(
    tmp_path: pathlib.Path,
) -> None:
    state_file = tmp_path / ".autopatch-j" / "memory_summary.md"
    state_file.parent.mkdir(parents=True)
    sentinel = "STATE_FILE_SECRET_MUST_NOT_REACH_LLM"
    state_file.write_text(sentinel, encoding="utf-8")
    context = _build_session(tmp_path)

    resolver = project_scope.ScopeResolver(tmp_path, context.symbol_indexer)
    assert resolver.resolve("@.autopatch-j/memory_summary.md 请解释") is None
    assert resolver.resolve("@.autopatch-j 请审查") is None

    context.set_focus_paths([".autopatch-j/memory_summary.md"])
    assert not context.focus_paths
    assert context.is_path_in_focus(".autopatch-j/memory_summary.md") is False

    tool_result = read_source_file.ReadSourceFileTool(context).execute(
        ".autopatch-j/memory_summary.md"
    )
    assert tool_result.status == "error"
    assert "项目状态目录不属于源码范围" in tool_result.message
    assert sentinel not in tool_result.message

    reader = source_reader.SourceReader(tmp_path)
    direct_result = reader.fetch_entry_source(
        symbols.SymbolIndexEntry(
            path=".autopatch-j/memory_summary.md",
            name="memory_summary.md",
            kind="file",
        )
    )
    assert direct_result.startswith("错误：")
    assert sentinel not in direct_result
    assert reader.fetch_lines(".autopatch-j/memory_summary.md", 1, 10) == ""
    assert (
        reader.fetch_resolved_snippet(
            ".autopatch-j/memory_summary.md",
            1,
            10,
            fallback_snippet="FALLBACK_STATE_SECRET",
        )
        == ""
    )


def test_source_path_symlink_resolving_into_project_state_is_blocked(
    tmp_path: pathlib.Path,
) -> None:
    state_file = tmp_path / ".autopatch-j" / "memory_summary.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("SYMLINK_STATE_SECRET", encoding="utf-8")
    alias = tmp_path / "memory-review.md"
    alias.symlink_to(state_file)
    context = _build_session(tmp_path)

    result = read_source_file.ReadSourceFileTool(context).execute(
        "memory-review.md"
    )

    assert result.status == "error"
    assert "项目状态目录不属于源码范围" in result.message
    assert "SYMLINK_STATE_SECRET" not in result.message
    assert (
        context.code_fetcher.fetch_resolved_snippet(
            "memory-review.md",
            1,
            10,
            fallback_snippet="SYMLINK_FALLBACK_SECRET",
        )
        == ""
    )


def test_project_scope_excludes_state_symlinks_but_keeps_safe_source_symlinks(
    tmp_path: pathlib.Path,
) -> None:
    state_file = tmp_path / ".autopatch-j" / "StateOnly.java"
    safe_file = tmp_path / "src" / "SafeSource.java"
    state_file.parent.mkdir(parents=True)
    safe_file.parent.mkdir(parents=True)
    state_file.write_text("class StateOnly {}", encoding="utf-8")
    safe_file.write_text("class SafeSource {}", encoding="utf-8")
    (tmp_path / "StateAlias.java").symlink_to(state_file)
    (tmp_path / "SafeAlias.java").symlink_to(safe_file)

    symbol_index = project_symbol_index.SymbolIndex(tmp_path)
    scope = project_scope.ScopeResolver(tmp_path, symbol_index).resolve(
        "",
        default_to_project=True,
    )

    assert scope is not None
    assert "src/SafeSource.java" in scope.focus_files
    assert "SafeAlias.java" in scope.focus_files
    assert ".autopatch-j/StateOnly.java" not in scope.focus_files
    assert "StateAlias.java" not in scope.focus_files
