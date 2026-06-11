from __future__ import annotations

from pathlib import Path

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.session import AgentSession
from autopatch_j.core.patching import SearchReplacePatchEngine
from autopatch_j.core.project import SourceReader, SymbolIndex
from autopatch_j.core.review import ProjectArtifactStore, ReviewWorkspaceManager
from autopatch_j.tools.function_calls.read_source_block import ReadSourceBlockTool
from autopatch_j.tools.function_calls.read_source_context import ReadSourceContextTool
from autopatch_j.tools.function_calls.read_source_file import ReadSourceFileTool


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
    )
    return Agent(session=session, llm=None)


def test_read_source_context_uses_fixed_window_without_line_numbers(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"line {index}" for index in range(1, 151)), encoding="utf-8")

    agent = _build_agent(tmp_path)
    result = ReadSourceContextTool(agent.session).execute("src/Demo.java", 50)

    assert result.status == "ok"
    assert result.summary == "已读取源代码上下文: src/Demo.java:30-130"
    assert "line 30" in result.message
    assert "line 130" in result.message
    assert "\nline 29\n" not in result.message
    assert "\nline 131\n" not in result.message


def test_read_source_context_clips_at_file_edges(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"line {index}" for index in range(1, 20)), encoding="utf-8")

    agent = _build_agent(tmp_path)
    result = ReadSourceContextTool(agent.session).execute("src/Demo.java", 3)

    assert result.status == "ok"
    assert result.payload["start_line"] == 1
    assert result.payload["end_line"] == 19


def test_read_source_file_keeps_full_file_guard(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Huge.java"
    source.parent.mkdir(parents=True)
    source.write_text("public class Huge {\n" + ("    void x() {}\n" * 9000) + "}\n", encoding="utf-8")

    agent = _build_agent(tmp_path)
    result = ReadSourceFileTool(agent.session).execute("src/Huge.java")

    assert result.status == "ok"
    assert "已拒绝全量代码注入" in result.message
    assert "read_source_block/read_source_context" in result.message


def test_read_source_file_corrects_missing_path_only_when_candidate_is_unique(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main" / "java" / "demo" / "User.java"
    source.parent.mkdir(parents=True)
    source.write_text("public class User {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    result = ReadSourceFileTool(agent.session).execute("wrong/User.java")

    assert result.status == "ok"
    assert "src/main/java/demo/User.java" in result.message


def test_read_source_file_rejects_ambiguous_missing_path_candidates(tmp_path: Path) -> None:
    first = tmp_path / "module-a" / "src" / "main" / "java" / "demo" / "User.java"
    second = tmp_path / "module-b" / "src" / "main" / "java" / "demo" / "User.java"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("public class User {}", encoding="utf-8")
    second.write_text("public class User {}", encoding="utf-8")

    agent = _build_agent(tmp_path)
    result = ReadSourceFileTool(agent.session).execute("wrong/User.java")

    assert result.status == "error"
    assert "同名候选不唯一" in result.message
    assert "module-a/src/main/java/demo/User.java" in result.message
    assert "module-b/src/main/java/demo/User.java" in result.message
    assert result.payload["candidates"] == [
        "module-a/src/main/java/demo/User.java",
        "module-b/src/main/java/demo/User.java",
    ]


def test_read_source_file_filters_missing_path_candidates_by_focus(tmp_path: Path) -> None:
    focused = tmp_path / "module-a" / "src" / "main" / "java" / "demo" / "User.java"
    outside = tmp_path / "module-b" / "src" / "main" / "java" / "demo" / "User.java"
    focused.parent.mkdir(parents=True)
    outside.parent.mkdir(parents=True)
    focused.write_text("public class User { String scope() { return \"focused\"; } }", encoding="utf-8")
    outside.write_text("public class User { String scope() { return \"outside\"; } }", encoding="utf-8")

    agent = _build_agent(tmp_path)
    agent.session.set_focus_paths(["module-a/src/main/java/demo/User.java"])
    result = ReadSourceFileTool(agent.session).execute("wrong/User.java")

    assert result.status == "ok"
    assert "focused" in result.message
    assert "outside" not in result.message
    assert "module-a/src/main/java/demo/User.java" in result.message


def test_read_source_block_finds_method_when_line_is_inside_body(tmp_path: Path) -> None:
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

    agent = _build_agent(tmp_path)
    result = ReadSourceBlockTool(agent.session).execute("src/Demo.java", 7)

    assert result.status == "ok"
    assert "public void run()" in result.message
    assert "call();" in result.message
    assert "private int value" not in result.message


def test_read_source_block_returns_class_for_field_line(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Demo.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "public class Demo {\n    private int value;\n    public void run() {}\n}\n",
        encoding="utf-8",
    )

    agent = _build_agent(tmp_path)
    result = ReadSourceBlockTool(agent.session).execute("src/Demo.java", 2)

    assert result.status == "ok"
    assert "public class Demo" in result.message
    assert "private int value" in result.message
