from __future__ import annotations

from pathlib import Path
from typing import Any

from autopatch_j.core.symbol_indexer import IndexEntry


class CodeFetcher:
    """
    源码读取和片段回源服务。

    职责边界：
    1. 根据索引项、物理行号或 finding 坐标从磁盘读取代码。
    2. 对大文件、目录和非 Java 文件做上下文防爆保护。
    3. 可用 Tree-sitter 时提取类/方法块；不负责扫描、补丁生成或语法校验。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.last_extract_mode: str = "full"
        self.last_extract_error: str | None = None

    def fetch_entry_source(self, entry: IndexEntry) -> str:
        """
        根据索引项抓取代码。
        如果是类或方法，会尝试定位完整语法块。
        """
        full_path = self.repo_root / entry.path
        if not full_path.exists():
            return f"错误：找不到文件或目录：{entry.path}"

        if entry.kind == "dir" or full_path.is_dir():
            return (
                f"[系统防线] 这是一个目录：{entry.path}。为了防止上下文爆炸，已拦截代码全量注入。"
                "请直接对该目录发起检查，或先缩小到文件级范围。"
            )

        if not entry.path.endswith(".java"):
            content = self._read_file(full_path)
            lines = content.splitlines()
            if len(lines) > 200:
                return "\n".join(lines[:200]) + f"\n\n... [系统防线] 非 Java 文件，截断显示 200 行 (共 {len(lines)} 行) ..."
            return content

        try:
            size_kb = full_path.stat().st_size / 1024
            if size_kb > 100:
                return (
                    f"[系统防线] 警告：该文件体积过大 ({size_kb:.1f} KB)，为防止上下文爆炸，已拒绝全量代码注入。"
                    "请优先对该文件发起检查，或使用 search_symbols 查找特定特征。"
                    "严禁使用 read_source_code 读取全量内容。"
                )
        except OSError:
            pass

        content = self._read_file(full_path)
        lines = content.splitlines()
        if len(lines) > 3000:
            return (
                f"[系统防线] 警告：该文件内容过多 (约 {len(lines)} 行)，为防止上下文爆炸，已拒绝全量代码注入。"
                "请优先对该文件发起检查，或使用 search_symbols 查找特定特征。"
                "严禁使用 read_source_code 读取全量内容。"
            )

        if entry.kind == "file":
            return content

        if entry.kind in ("class", "method"):
            return self._extract_symbol_block(content, entry.line)

        return ""

    def fetch_lines(self, file_path: str, start_line: int, end_line: int) -> str:
        """根据物理行号区间提取代码，1-based 且包含结束行。"""
        full_path = self.repo_root / file_path
        if not full_path.exists():
            return ""

        content = self._read_file(full_path)
        lines = content.splitlines()
        start_index = max(0, start_line - 1)
        end_index = min(len(lines), end_line)
        return "\n".join(lines[start_index:end_index])

    def fetch_resolved_snippet(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        fallback_snippet: str | None = None,
    ) -> str:
        """
        根据 finding 坐标回源提取稳定证据片段；源文件不可用时回退到扫描器快照。
        """
        normalized_path = file_path.replace("\\", "/").strip()
        safe_start_line = max(1, start_line)
        safe_end_line = max(safe_start_line, end_line)
        snippet = self.fetch_lines(normalized_path, safe_start_line, safe_end_line).strip()
        if snippet:
            return snippet
        return (fallback_snippet or "").strip()

    def _extract_symbol_block(self, content: str, start_line: int) -> str:
        """尝试用 Tree-sitter 提取完整语法块，失败时退化到固定行窗。"""
        self.last_extract_mode = "full"
        self.last_extract_error = None
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava

            language = Language(tsjava.language())
            parser = Parser(language)

            normalized_content = content.replace("\r\n", "\n")
            content_bytes = normalized_content.encode("utf-8")
            tree = parser.parse(content_bytes)

            target_node = self._find_node_at_line(tree.root_node, start_line)
            if target_node:
                return content_bytes[target_node.start_byte : target_node.end_byte].decode("utf-8")
        except (ImportError, Exception) as exc:
            self.last_extract_mode = "fallback"
            self.last_extract_error = str(exc)

        self.last_extract_mode = "fallback"
        lines = content.splitlines()
        start_index = max(0, start_line - 1)
        return "\n".join(lines[start_index : start_index + 30])

    def _find_node_at_line(self, node: Any, line: int) -> Any:
        """递归查找位于指定行号的最小完整语法节点。"""
        start_row = node.start_point[0] + 1

        if start_row == line and node.type in (
            "method_declaration",
            "class_declaration",
            "interface_declaration",
            "record_declaration",
        ):
            return node

        for child in node.children:
            found = self._find_node_at_line(child, line)
            if found:
                return found
        return None

    def _read_file(self, path: Path) -> str:
        """
        读取文件并强制归一化为 LF。
        这是解决 Windows/Linux 补丁匹配问题的核心门禁。
        """
        raw_bytes = path.read_bytes()
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw_bytes.decode("gbk")
            except UnicodeDecodeError:
                content = raw_bytes.decode("utf-8", errors="replace")
        return content.replace("\r\n", "\n")
