"""临时 SQLite 索引配合替代解析器，分别验证符号提取成功与降级。"""

from __future__ import annotations

import pathlib
import types

import pytest

from autopatch_j.core.project import java_symbols
from autopatch_j.core.project import symbol_index


def test_rebuild_index_and_stats(tmp_path: pathlib.Path):
    """测试全量重建索引"""
    # 准备测试文件
    java_file = tmp_path / "com" / "demo" / "AuthService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text(
        "package com.demo;\n"
        "public class AuthService {\n"
        "    public void login() {}\n"
        "}\n",
        encoding="utf-8",
    )

    symbol_indexer = symbol_index.SymbolIndex(tmp_path)
    stats = symbol_indexer.rebuild_index()

    # 核心测试：验证物理文件和目录是否被发现
    assert stats.get("file", 0) >= 1
    assert stats.get("dir", 0) >= 2
    assert stats.get("total", 0) >= 3


def test_search_symbols(tmp_path: pathlib.Path):
    """测试模糊搜索功能"""
    java_file = tmp_path / "AuthService.java"
    java_file.write_text(
        "public class AuthService { public void login() {} }", encoding="utf-8"
    )

    symbol_indexer = symbol_index.SymbolIndex(tmp_path)
    symbol_indexer.rebuild_index()

    # 基础测试：搜索文件名
    results = symbol_indexer.search("AuthService")
    assert any("AuthService" in r.name for r in results)


def test_ignored_dirs(tmp_path: pathlib.Path):
    """测试忽略目录功能"""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("...")

    symbol_indexer = symbol_index.SymbolIndex(tmp_path, ignored_dirs={".git"})
    symbol_indexer.rebuild_index()

    results = symbol_indexer.search(".git")
    assert len(results) == 0


def test_rebuild_index_excludes_state_symlinks_but_keeps_safe_source_symlinks(
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

    symbol_indexer = symbol_index.SymbolIndex(tmp_path)
    symbol_indexer.rebuild_index()

    indexed_paths = {
        entry.path
        for query in ("StateOnly", "StateAlias", "SafeSource", "SafeAlias")
        for entry in symbol_indexer.search(query)
    }
    assert ".autopatch-j/StateOnly.java" not in indexed_paths
    assert "StateAlias.java" not in indexed_paths
    assert "src/SafeSource.java" in indexed_paths
    assert "SafeAlias.java" in indexed_paths


def test_symbol_indexer_marks_symbol_extract_degraded_when_dependency_missing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Demo.java").write_text(
        "public class Demo { void run() {} }", encoding="utf-8"
    )
    monkeypatch.setattr(
        java_symbols,
        "tree_sitter_import_error",
        ImportError("missing dependency: tree_sitter"),
    )

    symbol_indexer = symbol_index.SymbolIndex(tmp_path)
    stats = symbol_indexer.rebuild_index()
    status = symbol_indexer.fetch_symbol_extract_status()

    assert stats.get("file", 0) >= 1
    assert stats.get("class", 0) == 0
    assert stats.get("method", 0) == 0
    assert status["mode"] == "degraded"
    assert status["enabled"] is False
    assert "missing dependency" in str(status["last_error"])


def test_symbol_indexer_extracts_class_and_method_when_tree_sitter_available(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Demo.java").write_text(
        "public class Demo { void run() {} }", encoding="utf-8"
    )

    class FakeNode:
        def __init__(self, text: str, line: int) -> None:
            self.text = text.encode("utf-8")
            self.start_point = (line, 0)

    class FakeQueryCursor:
        def __init__(self, unused_query) -> None:
            pass

        def captures(self, unused_root_node):
            # 0.25.2+ 返回字典
            return {
                "class.name": [FakeNode("Demo", 0)],
                "method.name": [FakeNode("run", 0)],
            }

    class FakeLanguage:
        def __init__(self, unused_capsule) -> None:
            pass

    class FakeParser:
        def __init__(self, unused_language: FakeLanguage) -> None:
            pass

        def parse(self, unused_content: bytes):
            return types.SimpleNamespace(root_node=object())

    class FakeQuery:
        def __init__(self, unused_language, unused_query_str) -> None:
            pass

    # 模拟 tree_sitter 模块，必须对齐 0.25.2 的类结构
    monkeypatch.setattr(
        java_symbols,
        "tree_sitter",
        types.SimpleNamespace(
            Language=FakeLanguage,
            Parser=FakeParser,
            Query=FakeQuery,
            QueryCursor=FakeQueryCursor,
        ),
    )
    monkeypatch.setattr(
        java_symbols,
        "tree_sitter_java",
        types.SimpleNamespace(language=object),
    )

    symbol_indexer = symbol_index.SymbolIndex(tmp_path)
    stats = symbol_indexer.rebuild_index()
    results = symbol_indexer.search("run")
    status = symbol_indexer.fetch_symbol_extract_status()

    assert stats.get("class", 0) == 1
    assert stats.get("method", 0) == 1
    assert any(
        entry.kind == "method" and entry.name == "run" for entry in results
    )
    assert status["mode"] == "full"
    assert status["enabled"] is True
    assert status["last_error"] is None


def test_symbol_indexer_extracts_extended_java_symbol_kinds(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Demo.java").write_text("interface Api {}", encoding="utf-8")

    class FakeNode:
        def __init__(self, text: str, line: int) -> None:
            self.text = text.encode("utf-8")
            self.start_point = (line, 0)

    class FakeQueryCursor:
        def __init__(self, unused_query) -> None:
            pass

        def captures(self, unused_root_node):
            return {
                "interface.name": [FakeNode("Api", 0)],
                "enum.name": [FakeNode("Mode", 1)],
                "record.name": [FakeNode("UserRecord", 2)],
                "constructor.name": [FakeNode("Demo", 3)],
            }

    class FakeLanguage:
        def __init__(self, unused_capsule) -> None:
            pass

    class FakeParser:
        def __init__(self, unused_language: FakeLanguage) -> None:
            pass

        def parse(self, unused_content: bytes):
            return types.SimpleNamespace(root_node=object())

    class FakeQuery:
        def __init__(self, unused_language, unused_query_str) -> None:
            pass

    monkeypatch.setattr(
        java_symbols,
        "tree_sitter",
        types.SimpleNamespace(
            Language=FakeLanguage,
            Parser=FakeParser,
            Query=FakeQuery,
            QueryCursor=FakeQueryCursor,
        ),
    )
    monkeypatch.setattr(
        java_symbols,
        "tree_sitter_java",
        types.SimpleNamespace(language=object),
    )

    symbol_indexer = symbol_index.SymbolIndex(tmp_path)
    stats = symbol_indexer.rebuild_index()

    assert stats.get("interface") == 1
    assert stats.get("enum") == 1
    assert stats.get("record") == 1
    assert stats.get("constructor") == 1
    assert any(
        entry.kind == "record" for entry in symbol_indexer.search("UserRecord")
    )


def test_symbol_indexer_marks_symbol_extract_degraded_when_runtime_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Demo.java").write_text(
        "public class Demo { void run() {} }", encoding="utf-8"
    )

    class FakeLanguage:
        def __init__(self, unused_capsule) -> None:
            pass

    class FakeQuery:
        def __init__(self, unused_language, unused_query_str) -> None:
            pass

    class FakeQueryCursor:
        def __init__(self, unused_query) -> None:
            # 模拟执行时报错
            raise RuntimeError("query failed")

    class FakeParser:
        def __init__(self, unused_language: FakeLanguage) -> None:
            pass

        def parse(self, unused_content: bytes):
            return types.SimpleNamespace(root_node=object())

    monkeypatch.setattr(
        java_symbols,
        "tree_sitter",
        types.SimpleNamespace(
            Language=FakeLanguage,
            Parser=FakeParser,
            Query=FakeQuery,
            QueryCursor=FakeQueryCursor,
        ),
    )
    monkeypatch.setattr(
        java_symbols,
        "tree_sitter_java",
        types.SimpleNamespace(language=object),
    )

    symbol_indexer = symbol_index.SymbolIndex(tmp_path)
    stats = symbol_indexer.rebuild_index()
    status = symbol_indexer.fetch_symbol_extract_status()

    assert stats.get("file", 0) >= 1
    assert stats.get("class", 0) == 0
    assert stats.get("method", 0) == 0
    assert status["mode"] == "degraded"
    assert status["enabled"] is True
    assert "query failed" in str(status["last_error"])
