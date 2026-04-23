from __future__ import annotations

from pathlib import Path
from typing import Any
from autopatch_j.core.index_service import IndexEntry

class CodeFetcher:
    """
    代码提取服务 (Core Service)
    职责：根据索引项或物理坐标，从磁盘提取精准的代码片段。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.last_extract_mode: str = "full"
        self.last_extract_error: str | None = None

    def fetch_entry(self, entry: IndexEntry) -> str:
        """
        根据索引项智能抓取代码。
        如果是类或方法，它会尝试定位完整的语法块。
        """
        full_path = self.repo_root / entry.path
        if not full_path.exists():
            return f"错误：找不到文件或目录 {entry.path}"

        # 1. 目录防御
        if entry.kind == "dir" or full_path.is_dir():
            return f"[系统防线] 这是一个目录：{entry.path}。为了防止上下文爆炸，已拦截代码全量注入。请使用 scan_project 工具并将 scope 设置为该目录来进行大范围扫描。"

        # 2. 非代码文件防御
        if not entry.path.endswith(".java"):
            # 返回前 200 行作为摘要
            content = self._read_file(full_path)
            lines = content.splitlines()
            if len(lines) > 200:
                return "\n".join(lines[:200]) + f"\n\n... [系统防线] 非 Java 文件，截断显示 200 行 (共 {len(lines)} 行) ..."
            return content

        # 3. 巨型文件防御 (大于 100KB 或 3000 行)
        try:
            size_kb = full_path.stat().st_size / 1024
            if size_kb > 100:
                return f"[系统防线] 警告：该文件体积过大 ({size_kb:.1f} KB)，为防止上下文爆炸，已拒绝全量代码注入。请优先调用 scan_project 工具扫描该文件以获取漏洞摘要，或使用 search_symbols 查找特定特征。严禁使用 read_source_code 读取全量内容。"
        except OSError:
            pass

        content = self._read_file(full_path)

        # 精确检查行数
        lines = content.splitlines()
        if len(lines) > 3000:
            return f"[系统防线] 警告：该文件内容过多 (约 {len(lines)} 行)，为防止上下文爆炸，已拒绝全量代码注入。请优先调用 scan_project 工具扫描该文件以获取漏洞摘要，或使用 search_symbols 查找特定特征。严禁使用 read_source_code 读取全量内容。"

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
        
        # 使用归一化内容切割
        content = self._read_file(full_path)
        lines = content.splitlines()
        
        # 边界处理
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        return "\n".join(lines[s:e])

    def _extract_symbol_block(self, content: str, start_line: int) -> str:
        """
        尝试使用 Tree-sitter 准确提取语法块。
        """
        self.last_extract_mode = "full"
        self.last_extract_error = None
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava

            language = Language(tsjava.language())
            parser = Parser(language)
            
            # 🚀 深度修复：统一使用 LF 编码的字节流进行解析
            # 这确保了字节偏移量 (byte_offset) 与字符位置在逻辑上的一致性
            norm_content = content.replace("\r\n", "\n")
            content_bytes = norm_content.encode("utf-8")
            tree = parser.parse(content_bytes)
            
            # 找到包含目标起始行的最小节点
            target_node = self._find_node_at_line(tree.root_node, start_line)
            if target_node:
                return content_bytes[target_node.start_byte:target_node.end_byte].decode("utf-8")
        except (ImportError, Exception) as exc:
            self.last_extract_mode = "fallback"
            self.last_extract_error = str(exc)

        # 兜底：保守提取（从起始行开始取 30 行）
        self.last_extract_mode = "fallback"
        lines = content.splitlines()
        start_idx = max(0, start_line - 1)
        return "\n".join(lines[start_idx : start_idx + 30])

    def _find_node_at_line(self, node: Any, line: int) -> Any:
        """递归寻找位于指定行号的最小完整语法节点"""
        # line 是 1-based, tree-sitter 是 0-based
        start_row = node.start_point[0] + 1
        end_row = node.end_point[0] + 1

        if start_row == line and node.type in ("method_declaration", "class_declaration", "interface_declaration", "record_declaration"):
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
        
        # 🚀 强制归一化：Agent 看到的内容必须是纯净的 LF 风格
        return content.replace("\r\n", "\n")
