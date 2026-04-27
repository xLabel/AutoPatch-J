from __future__ import annotations

import sys
import types
from pathlib import Path

from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.symbol_indexer import IndexEntry


def test_fetch_logic(tmp_path: Path):
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
    
    fetcher = CodeFetcher(tmp_path)
    
    # 1. 测试全文提取
    full_entry = IndexEntry(path=file_path, name="Demo.java", kind="file", line=0)
    assert fetcher.fetch_entry_source(full_entry) == java_code
    
    # 2. 测试智能块提取 (行号 2 开始)
    method_entry = IndexEntry(path=file_path, name="run", kind="method", line=2)
    snippet = fetcher.fetch_entry_source(method_entry)
    assert "public void run()" in snippet
    assert "// Line 3" in snippet


def test_fetch_range(tmp_path: Path):
    """验证物理行号区间提取"""
    (tmp_path / "L.txt").write_text("1\n2\n3\n4\n5", encoding="utf-8")
    fetcher = CodeFetcher(tmp_path)
    assert fetcher.fetch_lines("L.txt", 2, 4) == "2\n3\n4"


def test_fetcher_marks_full_when_ast_extract_succeeds(tmp_path: Path, monkeypatch) -> None:
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
        def __init__(self, _language) -> None:
            pass

    class FakeParser:
        def __init__(self, _language: FakeLanguage) -> None:
            pass

        def parse(self, _content: bytes):
            return types.SimpleNamespace(root_node=object())

    monkeypatch.setitem(sys.modules, "tree_sitter", types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser))
    monkeypatch.setitem(sys.modules, "tree_sitter_java", types.SimpleNamespace(language=lambda: object()))

    fetcher = CodeFetcher(tmp_path)
    monkeypatch.setattr(fetcher, "_find_node_at_line", lambda node, line: FakeNode())
    snippet = fetcher.fetch_entry_source(IndexEntry(path=file_path, name="run", kind="method", line=2))

    assert "public void run()" in snippet
    assert fetcher.last_extract_mode == "full"
    assert fetcher.last_extract_error is None


def test_fetcher_marks_fallback_when_ast_extract_fails(tmp_path: Path, monkeypatch) -> None:
    java_code = (
        "public class Demo {\n"
        "    public void run() {\n"
        "        call();\n"
        "    }\n"
        "}\n"
    )
    file_path = "Demo.java"
    (tmp_path / file_path).write_text(java_code, encoding="utf-8")
    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"tree_sitter", "tree_sitter_java"}:
            raise ImportError("tree-sitter missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    fetcher = CodeFetcher(tmp_path)
    snippet = fetcher.fetch_entry_source(IndexEntry(path=file_path, name="run", kind="method", line=2))

    assert "public void run()" in snippet
    assert fetcher.last_extract_mode == "fallback"
    assert "tree-sitter missing" in str(fetcher.last_extract_error)
