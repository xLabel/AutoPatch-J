"""Java 符号 SQLite 索引、源码遍历及提取降级状态。"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sqlite3
from typing import TypedDict

from autopatch_j import config
from autopatch_j.core.project import java_symbols
from autopatch_j.core.project import repo_path as project_repo_path
from autopatch_j.core.project import symbols


class SymbolExtractStatus(TypedDict):
    """符号提取的可用性和最近一次降级原因。"""

    enabled: bool
    mode: str
    last_error: str | None


class SymbolIndex:
    """本地 Java 符号索引服务。

    职责边界：
    1. 扫描项目文件并维护 SQLite 索引，支撑 @补全、范围解析和 search_symbols。
    2. 可用 Tree-sitter 时提取类/方法符号；不可用时降级为文件/目录级索引。
    3. 不读取完整代码内容，也不执行扫描器规则；源码读取和静态扫描由其他组件负责。
    """

    def __init__(
        self, repo_root: pathlib.Path, ignored_dirs: set[str] | None = None
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.ignored_dirs = ignored_dirs or set()
        self.db_path = config.get_project_state_dir(self.repo_root) / "index.db"
        self.symbol_extract_enabled: bool = True
        self.symbol_extract_mode: str = "full"
        self.symbol_extract_last_error: str | None = None
        self._init_db()

    def _init_db(self) -> None:
        """建立本项目索引表和名称索引，不填充源码记录。"""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    path TEXT,
                    name TEXT,
                    kind TEXT,
                    line INTEGER,
                    container TEXT,
                    mtime REAL,
                    PRIMARY KEY (path, name, kind, line)
                )
                """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON entries(name)")

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def rebuild_index(self) -> dict[str, int]:
        """重建源码与 Java 符号索引，并记录解析依赖或运行降级状态。"""
        all_entries: list[symbols.SymbolIndexEntry] = []
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
                all_entries.append(
                    symbols.SymbolIndexEntry(
                        path=path, name=directory, kind="dir"
                    )
                )
            dirs[:] = safe_dirs

            for file_name in files:
                if file_name.startswith(".") and file_name != ".autopatch-j":
                    continue

                abs_file = os.path.join(root, file_name)
                path = os.path.join(rel_root, file_name).replace(os.sep, "/")
                if not self._is_indexable_path(path):
                    continue
                current_mtime = os.path.getmtime(abs_file)

                all_entries.append(
                    symbols.SymbolIndexEntry(
                        path=path,
                        name=file_name,
                        kind="file",
                        mtime=current_mtime,
                    )
                )

                if file_name.endswith(".java"):
                    all_entries.extend(
                        self._extract_java_symbols(
                            path, pathlib.Path(abs_file), current_mtime
                        )
                    )

        with self._connect() as conn:
            conn.execute("DELETE FROM entries")
            conn.executemany(
                "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (e.path, e.name, e.kind, e.line, e.container, e.mtime)
                    for e in all_entries
                ],
            )
            conn.commit()

        return self.get_stats()

    def _is_indexable_path(self, path: str) -> bool:
        try:
            resolved = project_repo_path.resolve_repo_path(self.repo_root, path)
            repo_path = project_repo_path.to_repo_relative_path(
                self.repo_root, resolved
            )
        except project_repo_path.UnsafeRepoPathError:
            return False
        return not project_repo_path.is_project_state_path(repo_path)

    def search(
        self, query: str, limit: int = 20
    ) -> list[symbols.SymbolIndexEntry]:
        """从持久索引返回有限符号候选，候选不等于实时源码事实。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, name, kind, line, container, mtime "
                "FROM entries WHERE name LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            )
            return [symbols.SymbolIndexEntry(*row) for row in cursor.fetchall()]

    def get_stats(self) -> dict[str, int]:
        """返回当前索引各符号种类及总数。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT kind, COUNT(*) FROM entries GROUP BY kind"
            )
            stats = {kind: count for kind, count in cursor.fetchall()}
            stats["total"] = sum(stats.values())
            return stats

    def fetch_symbol_extract_status(self) -> SymbolExtractStatus:
        """返回 Java 符号提取的可用性、降级原因和模式。"""
        return {
            "enabled": self.symbol_extract_enabled,
            "mode": self.symbol_extract_mode,
            "last_error": self.symbol_extract_last_error,
        }

    def _extract_java_symbols(
        self, rel_path: str, full_path: pathlib.Path, mtime: float
    ) -> list[symbols.SymbolIndexEntry]:
        try:
            return java_symbols.JavaSymbolExtractor().extract(
                rel_path, full_path, mtime
            )
        except ImportError as exc:
            self._mark_symbol_extract_degraded(str(exc), enabled=False)
        except (OSError, RuntimeError, ValueError) as exc:
            self._mark_symbol_extract_degraded(str(exc), enabled=True)
        return []

    def _reset_symbol_extract_status(self) -> None:
        self.symbol_extract_enabled = True
        self.symbol_extract_mode = "full"
        self.symbol_extract_last_error = None

    def _mark_symbol_extract_degraded(
        self, error_message: str, enabled: bool
    ) -> None:
        self.symbol_extract_enabled = enabled
        self.symbol_extract_mode = "degraded"
        if self.symbol_extract_last_error is None:
            self.symbol_extract_last_error = error_message
