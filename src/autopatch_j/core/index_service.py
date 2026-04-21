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
    path: str          # 仓库相对路径
    name: str          # 符号或文件名
    kind: str          # "file", "dir", "class", "method"
    line: int = 0      # 定义所在行号 (1-based)
    container: str = "" # 所属容器（如类名或文件名）


class IndexService:
    """
    符号索引服务 (Core Service)
    职责：使用 Tree-sitter 扫描项目并建立 SQLite 符号索引。
    支持：文件名、类名、方法名的快速模糊搜索。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
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
        """全量重建索引，使用高效的 os.scandir 算法"""
        all_entries: list[IndexEntry] = []
        
        def _scan_dir(current_path: Path):
            try:
                with os.scandir(current_path) as it:
                    for entry in it:
                        # 过滤隐藏文件和忽略目录
                        if entry.name.startswith('.') or entry.name in GlobalConfig.ignored_dirs:
                            continue
                        
                        rel_path = Path(entry.path).relative_to(self.repo_root).as_posix()
                        
                        if entry.is_dir():
                            all_entries.append(IndexEntry(path=rel_path, name=entry.name, kind="dir"))
                            _scan_dir(Path(entry.path)) # 递归
                        elif entry.is_file():
                            all_entries.append(IndexEntry(path=rel_path, name=entry.name, kind="file"))
                            if entry.name.endswith(".java"):
                                all_entries.extend(self._extract_java_symbols(rel_path, Path(entry.path)))
            except PermissionError:
                pass # 忽略无权限目录

        # 启动递归扫描
        _scan_dir(self.repo_root)

        # 持久化到数据库
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
            
            # 定义查询：查找类定义和方法定义
            query_scm = """
                (class_declaration name: (identifier) @class.name)
                (method_declaration name: (identifier) @method.name)
            """
            query = language.query(query_scm)
            captures = query.captures(tree.root_node)
            
            for node, tag in captures:
                kind = "class" if tag == "class.name" else "method"
                name = node.text.decode("utf-8")
                line = node.start_point[0] + 1
                symbols.append(IndexEntry(path=rel_path, name=name, kind=kind, line=line, container=rel_path))
                
        except (ImportError, Exception):
            # 如果 Tree-sitter 不可用或解析失败，优雅降级（只索引文件本身）
            pass
        return symbols
