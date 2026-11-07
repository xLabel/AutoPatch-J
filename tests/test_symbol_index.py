"""真实 Java 语法树与临时 SQLite，验证声明定位、分页和故障边界。"""

from __future__ import annotations

import hashlib
import pathlib
import sqlite3

import pytest

from autopatch_j.core.project import java_ast
from autopatch_j.core.project import java_paths
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
    results = symbol_indexer.search("AuthService").entries
    assert any("AuthService" in r.name for r in results)


def test_ignored_dirs(tmp_path: pathlib.Path):
    """测试忽略目录功能"""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("...")

    symbol_indexer = symbol_index.SymbolIndex(tmp_path, ignored_dirs={".git"})
    symbol_indexer.rebuild_index()

    results = symbol_indexer.search(".git").entries
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
        for entry in symbol_indexer.search(query).entries
    }
    assert ".autopatch-j/StateOnly.java" not in indexed_paths
    assert "StateAlias.java" not in indexed_paths
    assert "src/SafeSource.java" in indexed_paths
    assert "SafeAlias.java" in indexed_paths


def test_real_parser_preserves_overloads_owners_and_compact_constructors(
    tmp_path,
):
    (tmp_path / "Demo.java").write_text(
        "package demo;\nimport java.util.List;\n"
        "class Demo { Demo() {} void save(String s) {} void save(int n) {} "
        "class Inner { void save(int n) {} } }\n"
        "interface Api { void run(); }\nenum Mode { READ }\n"
        "record Config(String key) { Config { "
        "java.util.Objects.requireNonNull(key); } }",
        encoding="utf-8",
    )
    index = symbol_index.SymbolIndex(tmp_path)
    stats = index.rebuild_index()
    assert stats["method"] == 4 and stats["constructor"] == 2
    assert stats["enum"] == stats["record"] == stats["interface"] == 1
    methods = index.search("save", kinds=["method"]).entries
    assert [entry.owner for entry in methods] == ["Demo", "Demo", "Demo.Inner"]
    assert [entry.signature for entry in methods] == [
        "void save(String s)",
        "void save(int n)",
        "void save(int n)",
    ]
    assert len({(entry.line, entry.column) for entry in methods}) == 3
    compact = index.search("Config", kinds=["constructor"]).entries
    assert compact[0].signature == "Config" and compact[0].owner == "Config"
    assert all(not entry.source_has_errors for entry in methods)


def test_enum_constant_methods_keep_distinct_anonymous_owners(tmp_path):
    (tmp_path / "Mode.java").write_text(
        "enum Mode {\n"
        " A { void run() {} },\n"
        " B { void run() {} };\n"
        " abstract void run();\n"
        "}",
        encoding="utf-8",
    )
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    methods = index.search("run", kinds=["method"]).entries
    assert [entry.owner for entry in methods] == [
        "Mode.<anonymous@2:4>",
        "Mode.<anonymous@3:4>",
        "Mode",
    ]
    assert [(entry.line, entry.column) for entry in methods] == [
        (2, 11),
        (3, 11),
        (4, 16),
    ]


def test_pages_are_stable_and_path_filter_precedes_limit(tmp_path):
    for i in range(25):
        (tmp_path / f"Config{i:02}.java").write_text(f"class Config{i} {{}}")
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    first = index.search("Config")
    assert first.next_offset is not None
    second = index.search("Config", first.next_offset)
    assert second.next_offset is not None
    third = index.search("Config", second.next_offset)
    assert first.total == second.total == third.total == 50
    assert len(first.entries) == len(second.entries) == 20
    assert len(third.entries) == 10 and third.next_offset is None
    all_entries = [*first.entries, *second.entries, *third.entries]
    assert len({(e.path, e.kind, e.line, e.column) for e in all_entries}) == 50
    paths = index.search("Config", kinds=["file"])
    assert paths.total == 25 and len(paths.entries) == 20
    assert all(entry.kind == "file" for entry in paths.entries)


def test_literal_substrings_unicode_and_stale_source(tmp_path):
    source = tmp_path / "Übung.java"
    source.write_text(
        "class Übung { void has_under_score() {} }", encoding="utf-8"
    )
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    assert index.search("üB", kinds=["class"]).total == 1
    assert index.search("%", kinds=["method"]).total == 0
    assert index.search("_", kinds=["method"]).total == 1
    source.write_text("class Übung { void changed() {} }", encoding="utf-8")
    located = index.search("has_under")
    assert located.entries and located.stale_paths == ("Übung.java",)
    assert "不证明" in located.notice


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gbk"])
def test_unicode_columns_and_raw_source_hash(tmp_path, encoding):
    source = "class 示例 { void 保存() {} void 保存(int 数值) {} }"
    path = tmp_path / "Example.java"
    path.write_bytes(source.encode(encoding))
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    methods = index.search("保存", kinds=["method"]).entries
    assert len(methods) == 2
    decoded = path.read_bytes().decode(
        "utf-8" if encoding.startswith("utf-8") else "gbk"
    )
    assert methods[0].column == decoded.index("保存") + 1
    assert (
        methods[0].source_sha256
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )


def test_incompatible_cache_requires_explicit_rebuild_and_keeps_other_tables(
    tmp_path,
):
    state = tmp_path / ".autopatch-j"
    state.mkdir()
    with sqlite3.connect(state / "index.db") as conn:
        conn.execute(
            "CREATE TABLE entries (path, name, kind, line, container, mtime)"
        )
        conn.execute("CREATE TABLE unrelated (value)")
        conn.execute("INSERT INTO unrelated VALUES ('keep')")
    index = symbol_index.SymbolIndex(tmp_path)
    with pytest.raises(symbol_index.IndexNotReadyError, match="/init"):
        index.search("Demo")
    with pytest.raises(symbol_index.IndexNotReadyError, match="/init"):
        index.get_stats()
    (tmp_path / "Demo.java").write_text("class Demo {}")
    assert index.rebuild_index()["class"] == 1
    with sqlite3.connect(state / "index.db") as conn:
        assert conn.execute("SELECT value FROM unrelated").fetchone() == (
            "keep",
        )


@pytest.mark.parametrize("offset", [-1, True, "1"])
def test_invalid_pagination_is_not_coerced(tmp_path, offset):
    with pytest.raises(ValueError, match="offset"):
        symbol_index.SymbolIndex(tmp_path).search("Demo", offset)


def test_native_tolerance_flag_survives_restart_and_empty_declarations(
    tmp_path,
):
    (tmp_path / "Bad.java").write_text(
        "class Bad { void broken() { int x = ; } void good() {} }"
    )
    (tmp_path / "Empty.java").write_text("???")
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    restarted = symbol_index.SymbolIndex(tmp_path)
    methods = restarted.search("good", kinds=["method"]).entries
    assert len(methods) == 1 and methods[0].source_has_errors is True
    empty = restarted.search("Empty", kinds=["file"]).entries
    assert len(empty) == 1 and empty[0].source_has_errors is True
    assert not restarted.search("absent", kinds=["method"]).entries


def test_parser_runtime_failure_is_reported(tmp_path, monkeypatch):
    (tmp_path / "Demo.java").write_text("class Demo {}")

    def failed(unused_path):
        raise RuntimeError("query failed")

    index = symbol_index.SymbolIndex(tmp_path)
    original = index.rebuild_index()
    monkeypatch.setattr(java_ast, "read", failed)
    with pytest.raises(RuntimeError, match="query failed"):
        index.rebuild_index()
    assert index.get_stats() == original


def test_directory_failure_keeps_previous_index_intact(tmp_path, monkeypatch):
    (tmp_path / "A.java").write_text("class A {}")
    index = symbol_index.SymbolIndex(tmp_path)
    original = index.rebuild_index()

    def inaccessible(unused_path, *, onerror):
        onerror(PermissionError("directory unavailable"))
        return iter(())

    monkeypatch.setattr(java_paths.os, "walk", inaccessible)
    with pytest.raises(PermissionError, match="directory unavailable"):
        index.rebuild_index()
    assert index.get_stats() == original


def test_corrupt_database_cannot_become_empty_or_previous_search(tmp_path):
    (tmp_path / "A.java").write_text("class A {}")
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    assert index.search("A", kinds=["class"]).total == 1
    corrupt = b"not a sqlite database"
    index.db_path.write_bytes(corrupt)
    with pytest.raises(symbol_index.IndexUnavailableError):
        index.search("A")
    with pytest.raises(sqlite3.DatabaseError):
        symbol_index.SymbolIndex(tmp_path)
    assert index.db_path.read_bytes() == corrupt


def test_failed_database_replace_rolls_back_without_partial_index(
    tmp_path, monkeypatch
):
    (tmp_path / "A.java").write_text("class A {}")
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    real_connect = sqlite3.connect

    class FailedInsert(sqlite3.Connection):
        def executemany(self, sql, parameters):
            raise sqlite3.OperationalError("transaction unavailable")

    def connect(path, **kwargs):
        return real_connect(path, factory=FailedInsert, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(
        sqlite3.OperationalError, match="transaction unavailable"
    ):
        index.rebuild_index()
    assert index.search("A", kinds=["class"]).total == 1


def test_unbuilt_index_is_distinct_from_successful_empty_build(tmp_path):
    index = symbol_index.SymbolIndex(tmp_path)
    for instance in (index, symbol_index.SymbolIndex(tmp_path)):
        with pytest.raises(symbol_index.IndexNotReadyError, match="/init"):
            instance.get_stats()
        with pytest.raises(symbol_index.IndexNotReadyError, match="/init"):
            instance.search("A")
        with pytest.raises(symbol_index.IndexNotReadyError, match="/init"):
            instance.search("A", kinds=[])
    assert not index.db_path.exists()
    assert index.rebuild_index() == {"total": 0}
    restarted = symbol_index.SymbolIndex(tmp_path)
    assert restarted.get_stats() == {"total": 0}
    assert restarted.search("A").total == 0
