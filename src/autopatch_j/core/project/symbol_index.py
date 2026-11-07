"""Java 符号 SQLite 索引、源码遍历。"""

from __future__ import annotations

from collections.abc import Collection
import contextlib
import dataclasses
import hashlib
import os
import pathlib
import sqlite3

from autopatch_j import config
from autopatch_j import error_messages
from autopatch_j.core import errors
from autopatch_j.core.project import java_ast
from autopatch_j.core.project import java_paths
from autopatch_j.core.project import java_symbols
from autopatch_j.core.project import repo_path as project_repo_path
from autopatch_j.core.project import symbols


class IndexUnavailableError(errors.OperationError):
    """索引存储故障，停止依赖本次查询的流程。"""


class IndexNotReadyError(ValueError):
    """缓存结构需要用户显式执行 /init 重建。"""


class SymbolIndex:
    """本地 Java 符号索引服务。

    职责边界：
    1. 扫描项目文件并维护 SQLite 索引，支撑 @补全、范围解析和 search_symbols。
    2. 使用 Tree-sitter 原生容错解析提取符号，运行故障立即失败。
    3. 不向调用方返回完整源码，不建立引用关系或执行规则；源码取证和扫描由其他组件负责。
    """

    def __init__(
        self, repo_root: pathlib.Path, ignored_dirs: Collection[str] = ()
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.ignored_dirs = frozenset(ignored_dirs)
        self.db_path = config.get_project_state_dir(self.repo_root) / "index.db"
        try:
            self.db_path.stat()
        except FileNotFoundError:
            pass
        else:
            with self._connect() as conn:
                conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()

    def clear(self) -> None:
        """删除自身可重建数据库；不跟随软链接删除其他文件。"""
        if self.db_path.is_symlink():
            raise ValueError("索引文件是软链接，未清理。")
        self.db_path.unlink(missing_ok=True)

    @staticmethod
    def _create_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE entries (
                path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                line INTEGER NOT NULL, column INTEGER NOT NULL,
                end_line INTEGER NOT NULL, end_column INTEGER NOT NULL,
                owner TEXT NOT NULL, signature TEXT NOT NULL,
                mtime REAL NOT NULL, source_sha256 TEXT NOT NULL,
                source_has_errors INTEGER NOT NULL,
                PRIMARY KEY (path, kind, line, column)
            )
        """)
        conn.execute("CREATE INDEX idx_name ON entries(name)")

    @staticmethod
    def _require_schema(conn: sqlite3.Connection) -> None:
        actual = [row[1] for row in conn.execute("PRAGMA table_info(entries)")]
        expected = [
            field.name for field in dataclasses.fields(symbols.SymbolIndexEntry)
        ]
        if not actual:
            raise IndexNotReadyError(error_messages.INDEX_NOT_READY)
        if actual != expected:
            raise IndexNotReadyError(error_messages.INDEX_SCHEMA_INVALID)

    @contextlib.contextmanager
    def _connect(self, *, create: bool = False):
        if create:
            conn = sqlite3.connect(self.db_path)
        else:
            try:
                self.db_path.stat()
            except FileNotFoundError as exc:
                raise IndexNotReadyError(
                    error_messages.INDEX_NOT_READY
                ) from exc
            conn = sqlite3.connect(self.db_path.as_uri() + "?mode=rw", uri=True)
        try:
            conn.create_function(
                "casefold", 1, str.casefold, deterministic=True
            )
            yield conn
        finally:
            conn.close()

    def rebuild_index(self) -> dict[str, int]:
        """完整提取后事务替换；失败不发布部分索引。"""
        all_entries = self._collect_entries()
        with self._connect(create=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TABLE IF EXISTS entries")
            self._create_table(conn)
            conn.executemany(
                "INSERT INTO entries VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [dataclasses.astuple(entry) for entry in all_entries],
            )
            conn.commit()
        return self.get_stats()

    def _collect_entries(self) -> list[symbols.SymbolIndexEntry]:
        """先完整枚举，失败不能破坏上一份已成功保存的缓存。"""
        all_entries: list[symbols.SymbolIndexEntry] = []
        repo_root_abs = os.path.abspath(str(self.repo_root))

        for root, dirs, files in os.walk(
            repo_root_abs, onerror=java_paths.raise_walk_error
        ):
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

                source = (
                    java_ast.read(pathlib.Path(abs_file))
                    if file_name.endswith(".java")
                    else None
                )
                all_entries.append(
                    symbols.SymbolIndexEntry(
                        path=path,
                        name=file_name,
                        kind="file",
                        mtime=current_mtime,
                        source_sha256=source.source_sha256 if source else "",
                        source_has_errors=(
                            source.source_has_errors if source else False
                        ),
                    )
                )

                if source is not None:
                    all_entries.extend(
                        java_symbols.JavaSymbolExtractor().extract(
                            path, source, current_mtime
                        )
                    )

        return all_entries

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
        self, query: str, offset: int = 0, kinds: list[str] | None = None
    ) -> symbols.SymbolSearchResult:
        """按 Unicode 忽略大小写的字面子串定位；分页不声明源码实时完整。"""
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise ValueError(error_messages.INVALID_OFFSET)
        where = "instr(casefold(name), ?) > 0"
        parameters: list[str | int] = [query.casefold()]
        if kinds is not None:
            where += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            parameters.extend(kinds)
        try:
            with self._connect() as conn:
                self._require_schema(conn)
                if kinds == []:
                    return symbols.SymbolSearchResult([], 0, None)
                conn.execute("BEGIN")
                total = conn.execute(
                    "SELECT COUNT(*) FROM entries WHERE " + where, parameters
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT * FROM entries WHERE "
                    + where
                    + " ORDER BY path, line, column, kind, name"
                    " LIMIT 20 OFFSET ?",
                    [*parameters, offset],
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise IndexUnavailableError(
                f"索引无法读取，请执行 /init：{exc}"
            ) from exc
        entries = []
        stale = set()
        source_hashes: dict[pathlib.Path, str] = {}
        for row in rows:
            entry = symbols.SymbolIndexEntry(
                *row[:-1], source_has_errors=bool(row[-1])
            )
            path = project_repo_path.resolve_source_path(
                self.repo_root, entry.path
            )
            if not path.exists():
                raise FileNotFoundError(entry.path)
            if entry.source_sha256:
                if path not in source_hashes:
                    source_hashes[path] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                if source_hashes[path] != entry.source_sha256:
                    stale.add(entry.path)
            entries.append(entry)
        return symbols.SymbolSearchResult(
            entries,
            total,
            offset + 20 if offset + 20 < total else None,
            tuple(sorted(stale)),
        )

    def get_stats(self) -> dict[str, int]:
        """返回当前索引各符号种类及总数。"""
        try:
            with self._connect() as conn:
                self._require_schema(conn)
                cursor = conn.execute(
                    "SELECT kind, COUNT(*) FROM entries GROUP BY kind"
                )
                stats = {kind: count for kind, count in cursor.fetchall()}
                stats["total"] = sum(stats.values())
                return stats
        except sqlite3.DatabaseError as exc:
            raise IndexUnavailableError(
                f"索引无法读取，请执行 /init：{exc}"
            ) from exc
