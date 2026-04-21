from __future__ import annotations

from pathlib import Path
from autopatch_j.core.index_service import IndexEntry

class CodeFetcher:
    """
    代码提取服务 (Core Service)
    职责：根据索引项或物理坐标，从磁盘提取精准的代码片段。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    def fetch_entry(self, entry: IndexEntry) -> str:
        """
        根据索引项智能抓取代码。
        如果是类或方法，它会尝试定位完整的语法块。
        """
        full_path = self.repo_root / entry.path
        if not full_path.exists():
            return f"错误：找不到文件 {entry.path}"

        content = self._read_file(full_path)
        
        if entry.kind == "file":
            return content
        
        if entry.kind in ("class", "method"):
            return self._extract_symbol_block(content, entry.line)

        return ""

    def fetch_lines(self, file_path: str, start_line: int, end_line: int) -> str:
        """
        根据物理行号区间提取代码（1-based, inclusive）。
        """
        full_path = self.repo_root / file_path
        if not full_path.exists():
            return ""
        
        lines = self._read_file(full_path).splitlines()
        # 边界处理
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        return "\n".join(lines[s:e])

    def _extract_symbol_block(self, content: str, start_line: int) -> str:
        """
        尝试使用 Tree-sitter 准确提取语法块。
        如果解析失败或库不可用，则退回到基于行号的保守提取。
        """
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava

            language = Language(tsjava.language())
            parser = Parser(language)
            tree = parser.parse(content.encode("utf-8"))
            
            # 找到包含目标起始行的最小节点
            target_node = self._find_node_at_line(tree.root_node, start_line)
            if target_node:
                return content.encode("utf-8")[target_node.start_byte:target_node.end_byte].decode("utf-8")
        except (ImportError, Exception):
            pass

        # 兜底：保守提取（从起始行开始取 30 行，防止上下文过大）
        lines = content.splitlines()
        start_idx = max(0, start_line - 1)
        return "\n".join(lines[start_idx : start_idx + 30])

    def _find_node_at_line(self, node: Any, line: int) -> Any:
        """递归寻找位于指定行号的最小完整语法节点（如 MethodDeclaration）"""
        # line 是 1-based, tree-sitter 是 0-based
        start_row = node.start_point[0] + 1
        end_row = node.end_point[0] + 1

        if start_row == line and node.type in ("method_declaration", "class_declaration", "interface_declaration"):
            return node

        for child in node.children:
            found = self._find_node_at_line(child, line)
            if found:
                return found
        return None

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")
