from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopatch_j.config import get_project_state_dir


@dataclass(slots=True)
class IndexEntry:
    """单条索引记录。"""
    path: str
    name: str
    kind: str
    line: int = 0
    container: str = ""
    mtime: float = 0.0  # 修改时间，用于增量索引


class IndexService:
    """
    符号索引服务。
    职责：扫描项目并建立 SQLite 索引，必要时补充 Tree-sitter 符号信息。
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
        """初始化 SQLite 数据库结构。"""
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

    @contextmanager
    def _connect(self):
        """返回数据库连接上下文。"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def rebuild_index(self) -> dict[str, int]:
        """
        重建索引。
        当前实现使用全量扫描并覆盖旧记录。
        """
        all_entries: list[IndexEntry] = []
        repo_root_abs = os.path.abspath(str(self.repo_root))
        self._reset_symbol_extract_status()

        for root, dirs, files in os.walk(repo_root_abs):
            # 过滤黑名单目录。
            dirs[:] = [d for d in dirs if d not in self.ignored_dirs]
            
            rel_root = os.path.relpath(root, repo_root_abs).replace(os.sep, '/')
            if rel_root == ".": rel_root = ""

            for d in dirs:
                if d.startswith('.') and d != '.autopatch-j': continue
                path = os.path.join(rel_root, d).replace(os.sep, '/')
                all_entries.append(IndexEntry(path=path, name=d, kind="dir"))

            for f in files:
                if f.startswith('.') and f != '.autopatch-j': continue
                
                abs_f = os.path.join(root, f)
                path = os.path.join(rel_root, f).replace(os.sep, '/')
                current_mtime = os.path.getmtime(abs_f)
                
                all_entries.append(IndexEntry(path=path, name=f, kind="file", mtime=current_mtime))
                
                if f.endswith(".java"):
                    all_entries.extend(self._extract_java_symbols(path, Path(abs_f), current_mtime))

        with self._connect() as conn:
            conn.execute("DELETE FROM entries")
            conn.executemany(
                "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)", 
                [(e.path, e.name, e.kind, e.line, e.container, e.mtime) for e in all_entries]
            )
            conn.commit()
        
        return self.get_stats()

    def search(self, query: str, limit: int = 20) -> list[IndexEntry]:
        """按名称模糊搜索索引项。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, name, kind, line, container, mtime FROM entries WHERE name LIKE ? LIMIT ?",
                (f"%{query}%", limit)
            )
            return [IndexEntry(*row) for row in cursor.fetchall()]

    def get_stats(self) -> dict[str, int]:
        """获取索引统计信息。"""
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

    def _extract_java_symbols(self, rel_path: str, full_path: Path, mtime: float) -> list[IndexEntry]:
        """使用 Tree-sitter 提取 Java 类和方法。"""
        symbols: list[IndexEntry] = []
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava
            
            content = full_path.read_text(encoding="utf-8", errors="replace")
            language = Language(tsjava.language())
            parser = Parser(language)
            tree = parser.parse(content.encode("utf-8"))
            
            query = language.query("(class_declaration name: (identifier) @class.name) (method_declaration name: (identifier) @method.name)")
            captures = query.captures(tree.root_node)
            
            for node, tag in captures:
                symbols.append(IndexEntry(
                    path=rel_path, 
                    name=node.text.decode("utf-8"), 
                    kind="class" if tag == "class.name" else "method", 
                    line=node.start_point[0] + 1, 
                    container=rel_path,
                    mtime=mtime
                ))
        except ImportError as exc:
            self._mark_symbol_extract_degraded(str(exc), enabled=False)
        except Exception as exc:
            self._mark_symbol_extract_degraded(str(exc), enabled=True)
        return symbols

    def _reset_symbol_extract_status(self) -> None:
        self.symbol_extract_enabled = True
        self.symbol_extract_mode = "full"
        self.symbol_extract_last_error = None

    def _mark_symbol_extract_degraded(self, error_message: str, enabled: bool) -> None:
        self.symbol_extract_enabled = enabled
        self.symbol_extract_mode = "degraded"
        if self.symbol_extract_last_error is None:
            self.symbol_extract_last_error = error_message
