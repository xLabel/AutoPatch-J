from __future__ import annotations

from typing import Any


class JavaBlockExtractor:
    """
    Java 类/方法代码块提取器。

    Tree-sitter 可用时返回完整语法块；不可用或异常时退化到固定行窗。
    """

    def __init__(self) -> None:
        self.last_mode: str = "full"
        self.last_error: str | None = None

    def extract(self, content: str, start_line: int) -> str:
        self.last_mode = "full"
        self.last_error = None
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
            self.last_mode = "fallback"
            self.last_error = str(exc)

        self.last_mode = "fallback"
        lines = content.splitlines()
        start_index = max(0, start_line - 1)
        return "\n".join(lines[start_index : start_index + 30])

    def _find_node_at_line(self, node: Any, line: int) -> Any:
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
