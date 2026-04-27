from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from autopatch_j.core.symbol_indexer import SymbolIndexer


def test_rebuild_index_and_stats(tmp_path: Path):
    """测试全量重建索引"""
    # 准备测试文件
    java_file = tmp_path / "com" / "demo" / "AuthService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text(
        "package com.demo;\n"
        "public class AuthService {\n"
        "    public void login() {}\n"
        "}\n",
        encoding="utf-8"
    )
    
    symbol_indexer = SymbolIndexer(tmp_path)
    stats = symbol_indexer.rebuild_index()
    
    # 核心测试：验证物理文件和目录是否被发现
    assert stats.get("file", 0) >= 1
    assert stats.get("dir", 0) >= 2
    assert stats.get("total", 0) >= 3


def test_search_symbols(tmp_path: Path):
    """测试模糊搜索功能"""
    java_file = tmp_path / "AuthService.java"
    java_file.write_text("public class AuthService { public void login() {} }", encoding="utf-8")
    
    symbol_indexer = SymbolIndexer(tmp_path)
    symbol_indexer.rebuild_index()
    
    # 基础测试：搜索文件名
    results = symbol_indexer.search("AuthService")
    assert any("AuthService" in r.name for r in results)


def test_ignored_dirs(tmp_path: Path):
    """测试忽略目录功能"""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("...")
    
    symbol_indexer = SymbolIndexer(tmp_path, ignored_dirs={".git"})
    symbol_indexer.rebuild_index()
    
    results = symbol_indexer.search(".git")
    assert len(results) == 0


def test_symbol_indexer_marks_symbol_extract_degraded_when_dependency_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Demo.java").write_text("public class Demo { void run() {} }", encoding="utf-8")
    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"tree_sitter", "tree_sitter_java"}:
            raise ImportError(f"missing dependency: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    symbol_indexer = SymbolIndexer(tmp_path)
    stats = symbol_indexer.rebuild_index()
    status = symbol_indexer.fetch_symbol_extract_status()

    assert stats.get("file", 0) >= 1
    assert stats.get("class", 0) == 0
    assert stats.get("method", 0) == 0
    assert status["mode"] == "degraded"
    assert status["enabled"] is False
    assert "missing dependency" in str(status["last_error"])


def test_symbol_indexer_extracts_class_and_method_when_tree_sitter_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Demo.java").write_text("public class Demo { void run() {} }", encoding="utf-8")

    class FakeNode:
        def __init__(self, text: str, line: int) -> None:
            self.text = text.encode("utf-8")
            self.start_point = (line, 0)

    class FakeQuery:
        def __init__(self, _language, _query_str) -> None:
            pass
        def captures(self, _root_node):
            return [
                (FakeNode("Demo", 0), "class.name"),
                (FakeNode("run", 0), "method.name"),
            ]

    class FakeLanguage:
        def __init__(self, _language) -> None:
            pass

    class FakeParser:
        def __init__(self, _language: FakeLanguage) -> None:
            pass

        def parse(self, _content: bytes):
            return types.SimpleNamespace(root_node=object())

    monkeypatch.setitem(sys.modules, "tree_sitter", types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser, Query=FakeQuery))
    monkeypatch.setitem(sys.modules, "tree_sitter_java", types.SimpleNamespace(language=lambda: object()))

    symbol_indexer = SymbolIndexer(tmp_path)
    stats = symbol_indexer.rebuild_index()
    results = symbol_indexer.search("run")
    status = symbol_indexer.fetch_symbol_extract_status()

    assert stats.get("class", 0) == 1
    assert stats.get("method", 0) == 1
    assert any(entry.kind == "method" and entry.name == "run" for entry in results)
    assert status["mode"] == "full"
    assert status["enabled"] is True
    assert status["last_error"] is None


def test_symbol_indexer_marks_symbol_extract_degraded_when_runtime_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Demo.java").write_text("public class Demo { void run() {} }", encoding="utf-8")

    class FakeQuery:
        def __init__(self, _language, _query_str) -> None:
            raise RuntimeError("query failed")

    class FakeLanguage:
        def __init__(self, _language) -> None:
            pass

    class FakeParser:
        def __init__(self, _language: FakeLanguage) -> None:
            pass

        def parse(self, _content: bytes):
            return types.SimpleNamespace(root_node=object())

    monkeypatch.setitem(sys.modules, "tree_sitter", types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser, Query=FakeQuery))
    monkeypatch.setitem(sys.modules, "tree_sitter_java", types.SimpleNamespace(language=lambda: object()))

    symbol_indexer = SymbolIndexer(tmp_path)
    stats = symbol_indexer.rebuild_index()
    status = symbol_indexer.fetch_symbol_extract_status()

    assert stats.get("file", 0) >= 1
    assert stats.get("class", 0) == 0
    assert stats.get("method", 0) == 0
    assert status["mode"] == "degraded"
    assert status["enabled"] is True
    assert "query failed" in str(status["last_error"])
