from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopatch_j.config import GlobalConfig
from autopatch_j.paths import get_project_state_dir


@dataclass(slots=True)
class IndexEntry:
    """索引项数据模型"""
    path: str
    name: str
    kind: str
    line: int = 0
    container: str = ""


class IndexService:
    """
    符号索引服务 (Core Service)
    职责：使用 Tree-sitter 扫描项目并建立 SQLite 符号索引。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.db_path = get_project_state_dir(self.repo_root) / "index.db"
        self._init_db()

    def _init_db(self) -> None:
        """初始化 SQLite 数据库结构"""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    path TEXT,
                    name TEXT,
                    kind TEXT,
                    line INTEGER,
                    container TEXT,
                    PRIMARY KEY (path, name, kind, line)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON entries(name)")

    @contextmanager
    def _connect(self):
        """数据库连接上下文管理器"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def rebuild_index(self) -> dict[str, int]:
        """全量重建索引，采用稳健的 os.walk 算法"""
        all_entries: list[IndexEntry] = []
        repo_root_abs = os.path.abspath(str(self.repo_root))

        for root, dirs, files in os.walk(repo_root_abs):
            # 1. 过滤黑名单
            dirs[:] = [d for d in dirs if d not in GlobalConfig.ignored_dirs]
            
            # 2. 计算当前相对路径
            rel_root = os.path.relpath(root, repo_root_abs).replace(os.sep, '/')
            if rel_root == ".": rel_root = ""

            # 3. 索引目录
            for d in dirs:
                if d.startswith('.') and d != '.autopatch-j': continue
                path = f"{rel_root}/{d}".lstrip('/')
                all_entries.append(IndexEntry(path=path, name=d, kind="dir"))

            # 4. 索引文件
            for f in files:
                if f.startswith('.') and f != '.autopatch-j': continue
                path = f"{rel_root}/{f}".lstrip('/')
                all_entries.append(IndexEntry(path=path, name=f, kind="file"))
                
                if f.endswith(".java"):
                    abs_f = os.path.join(root, f)
                    all_entries.extend(self._extract_java_symbols(path, Path(abs_f)))

        # 5. 持久化到数据库
        with self._connect() as conn:
            conn.execute("DELETE FROM entries")
            conn.executemany(
                "INSERT INTO entries (path, name, kind, line, container) VALUES (?, ?, ?, ?, ?)",
                [(e.path, e.name, e.kind, e.line, e.container) for e in all_entries]
            )
        
        return self.get_stats()

    def search(self, query: str, limit: int = 20) -> list[IndexEntry]:
        """模糊搜索符号或路径"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, name, kind, line, container FROM entries WHERE name LIKE ? LIMIT ?",
                (f"%{query}%", limit)
            )
            return [IndexEntry(*row) for row in cursor.fetchall()]

    def get_stats(self) -> dict[str, int]:
        """获取索引统计信息"""
        with self._connect() as conn:
            cursor = conn.execute("SELECT kind, COUNT(*) FROM entries GROUP BY kind")
            stats = {kind: count for kind, count in cursor.fetchall()}
            stats["total"] = sum(stats.values())
            return stats

    def _extract_java_symbols(self, rel_path: str, full_path: Path) -> list[IndexEntry]:
        """使用 Tree-sitter 提取 Java 类和方法"""
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
                    container=rel_path
                ))
        except:
            pass
        return symbols
