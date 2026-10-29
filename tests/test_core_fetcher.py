"""通过临时源码和替代 Tree-sitter 对象验证语法提取及行窗降级。"""

from __future__ import annotations

import pathlib
import types

from autopatch_j.core.project import java_blocks
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbols


def test_fetch_logic(tmp_path: pathlib.Path):
    """验证代码提取逻辑：全量提取与智能块提取"""
    java_code = (
        "public class Demo {\n"
        "    public void run() {\n"
        "        // Line 3\n"
        "    }\n"
        "}\n"
    )
    file_path = "Demo.java"
    (tmp_path / file_path).write_text(java_code, encoding="utf-8")

    fetcher = source_reader.SourceReader(tmp_path)

    # 1. 测试全文提取
    full_entry = symbols.SymbolIndexEntry(
        path=file_path, name="Demo.java", kind="file", line=0
    )
    assert fetcher.fetch_entry_source(full_entry) == java_code

    # 2. 测试智能块提取 (行号 2 开始)
    method_entry = symbols.SymbolIndexEntry(
        path=file_path, name="run", kind="method", line=2
    )
    snippet = fetcher.fetch_entry_source(method_entry)
    assert "public void run()" in snippet
    assert "// Line 3" in snippet


def test_fetch_range(tmp_path: pathlib.Path):
    """验证物理行号区间提取"""
    (tmp_path / "L.txt").write_text("1\n2\n3\n4\n5", encoding="utf-8")
    fetcher = source_reader.SourceReader(tmp_path)
    assert fetcher.fetch_lines("L.txt", 2, 4) == "2\n3\n4"


def test_fetcher_marks_full_when_ast_extract_succeeds(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    java_code = (
        "public class Demo {\n"
        "    public void run() {\n"
        "        call();\n"
        "    }\n"
        "}\n"
    )
    file_path = "Demo.java"
    (tmp_path / file_path).write_text(java_code, encoding="utf-8")

    class FakeNode:
        start_byte = java_code.index("    public void run() {")
        end_byte = java_code.index("    }\n}") + len("    }\n")

    class FakeLanguage:
        def __init__(self, unused_language) -> None:
            pass

    class FakeParser:
        def __init__(self, unused_language: FakeLanguage) -> None:
            pass

        def parse(self, unused_content: bytes):
            return types.SimpleNamespace(root_node=object())

    monkeypatch.setattr(
        java_blocks,
        "tree_sitter",
        types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser),
    )
    monkeypatch.setattr(
        java_blocks,
        "tree_sitter_java",
        types.SimpleNamespace(language=object),
    )

    fetcher = source_reader.SourceReader(tmp_path)
    monkeypatch.setattr(
        java_blocks.JavaBlockExtractor,
        "_find_node_at_line",
        lambda self, node, line: FakeNode(),
    )
    snippet = fetcher.fetch_entry_source(
        symbols.SymbolIndexEntry(
            path=file_path, name="run", kind="method", line=2
        )
    )

    assert "public void run()" in snippet
    assert fetcher.last_extract_mode == "full"
    assert fetcher.last_extract_error is None


def test_fetcher_marks_fallback_when_ast_extract_fails(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    java_code = (
        "public class Demo {\n"
        "    public void run() {\n"
        "        call();\n"
        "    }\n"
        "}\n"
    )
    file_path = "Demo.java"
    (tmp_path / file_path).write_text(java_code, encoding="utf-8")
    monkeypatch.setattr(
        java_blocks,
        "tree_sitter_import_error",
        ImportError("tree-sitter missing"),
    )

    fetcher = source_reader.SourceReader(tmp_path)
    snippet = fetcher.fetch_entry_source(
        symbols.SymbolIndexEntry(
            path=file_path, name="run", kind="method", line=2
        )
    )

    assert "public void run()" in snippet
    assert fetcher.last_extract_mode == "fallback"
    assert "tree-sitter missing" in str(fetcher.last_extract_error)
