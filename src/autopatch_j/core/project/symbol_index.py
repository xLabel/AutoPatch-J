from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopatch_j.config import get_project_state_dir
from autopatch_j.core.project.repo_path import (
    UnsafeRepoPathError,
    is_project_state_path,
    resolve_repo_path,
    to_repo_relative_path,
)


@dataclass(slots=True)
class SymbolIndexEntry:
    """
    本地代码索引的一条记录。

    kind 表示目录、文件、类或方法；line 用于 SourceReader 定位符号块。
    """

    path: str
    name: str
    kind: str
    line: int = 0
    container: str = ""
    mtime: float = 0.0


class SymbolIndex:
    """
    本地 Java 符号索引服务。

    职责边界：
    1. 扫描项目文件并维护 SQLite 索引，支撑 @补全、范围解析和 search_symbols。
    2. 可用 Tree-sitter 时提取类/方法符号；不可用时降级为文件/目录级索引。
    3. 不读取完整代码内容，也不执行扫描器规则；源码读取和静态扫描由其他组件负责。
    """

    def __init__(self, repo_root: Path, ignored_dirs: set[str] | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.ignored_dirs = ignored_dirs or set()
        self.db_path = get_project_state_dir(self.repo_root) / "index.db"
        self.symbol_extract_enabled: bool = True
        self.symbol_extract_mode: str = "full"
        self.symbol_extract_last_error: str | None = None
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    path TEXT,
                    name TEXT,
                    kind TEXT,
                    line INTEGER,
                    container TEXT,
                    mtime REAL,
                    PRIMARY KEY (path, name, kind, line)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON entries(name)")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def rebuild_index(self) -> dict[str, int]:
        all_entries: list[SymbolIndexEntry] = []
        repo_root_abs = os.path.abspath(str(self.repo_root))
        self._reset_symbol_extract_status()

        for root, dirs, files in os.walk(repo_root_abs):
            rel_root = os.path.relpath(root, repo_root_abs).replace(os.sep, "/")
            if rel_root == ".":
                rel_root = ""

            safe_dirs: list[str] = []
            for directory in dirs:
                if directory in self.ignored_dirs:
                    continue
                path = os.path.join(rel_root, directory).replace(os.sep, "/")
                if not self._is_indexable_path(path):
                    continue
                safe_dirs.append(directory)
                if directory.startswith("."):
                    continue
                all_entries.append(SymbolIndexEntry(path=path, name=directory, kind="dir"))
            dirs[:] = safe_dirs

            for file_name in files:
                if file_name.startswith(".") and file_name != ".autopatch-j":
                    continue

                abs_file = os.path.join(root, file_name)
                path = os.path.join(rel_root, file_name).replace(os.sep, "/")
                if not self._is_indexable_path(path):
                    continue
                current_mtime = os.path.getmtime(abs_file)

                all_entries.append(SymbolIndexEntry(path=path, name=file_name, kind="file", mtime=current_mtime))

                if file_name.endswith(".java"):
                    all_entries.extend(self._extract_java_symbols(path, Path(abs_file), current_mtime))

        with self._connect() as conn:
            conn.execute("DELETE FROM entries")
            conn.executemany(
                "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)",
                [(e.path, e.name, e.kind, e.line, e.container, e.mtime) for e in all_entries],
            )
            conn.commit()

        return self.get_stats()

    def _is_indexable_path(self, path: str) -> bool:
        try:
            resolved = resolve_repo_path(self.repo_root, path)
            repo_path = to_repo_relative_path(self.repo_root, resolved)
        except UnsafeRepoPathError:
            return False
        return not is_project_state_path(repo_path)

    def search(self, query: str, limit: int = 20) -> list[SymbolIndexEntry]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, name, kind, line, container, mtime FROM entries WHERE name LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            )
            return [SymbolIndexEntry(*row) for row in cursor.fetchall()]

    def get_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            cursor = conn.execute("SELECT kind, COUNT(*) FROM entries GROUP BY kind")
            stats = {kind: count for kind, count in cursor.fetchall()}
            stats["total"] = sum(stats.values())
            return stats

    def fetch_symbol_extract_status(self) -> dict[str, Any]:
        return {
            "enabled": self.symbol_extract_enabled,
            "mode": self.symbol_extract_mode,
            "last_error": self.symbol_extract_last_error,
        }

    def _extract_java_symbols(self, rel_path: str, full_path: Path, mtime: float) -> list[SymbolIndexEntry]:
        try:
            from autopatch_j.core.project.java_symbols import JavaSymbolExtractor

            return JavaSymbolExtractor().extract(rel_path, full_path, mtime)
        except ImportError as exc:
            self._mark_symbol_extract_degraded(str(exc), enabled=False)
        except Exception as exc:
            self._mark_symbol_extract_degraded(str(exc), enabled=True)
        return []

    def _reset_symbol_extract_status(self) -> None:
        self.symbol_extract_enabled = True
        self.symbol_extract_mode = "full"
        self.symbol_extract_last_error = None

    def _mark_symbol_extract_degraded(self, error_message: str, enabled: bool) -> None:
        self.symbol_extract_enabled = enabled
        self.symbol_extract_mode = "degraded"
        if self.symbol_extract_last_error is None:
            self.symbol_extract_last_error = error_message
