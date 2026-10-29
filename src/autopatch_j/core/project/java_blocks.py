"""Tree-sitter 语法块提取与明确的行窗降级。"""

from __future__ import annotations

tree_sitter_import_error: ImportError | None = None
try:
    import tree_sitter
    import tree_sitter_java
except ImportError as exc:
    tree_sitter_import_error = exc


class JavaBlockExtractor:
    """Java 类/方法代码块提取器。

    Tree-sitter 可用时返回完整语法块；不可用或异常时退化到固定行窗。
    """

    def __init__(self) -> None:
        self.last_mode: str = "full"
        self.last_error: str | None = None

    def extract(self, content: str, start_line: int) -> str:
        """提取目标行最内层 Java 代码块；依赖或解析失败时返回明确行窗降级。"""
        self.last_mode = "full"
        self.last_error = None
        try:
            if tree_sitter_import_error is not None:
                raise tree_sitter_import_error

            language = tree_sitter.Language(tree_sitter_java.language())
            parser = tree_sitter.Parser(language)

            normalized_content = content.replace("\r\n", "\n")
            content_bytes = normalized_content.encode("utf-8")
            tree = parser.parse(content_bytes)

            target_node = self._find_node_at_line(tree.root_node, start_line)
            if target_node:
                return content_bytes[
                    target_node.start_byte : target_node.end_byte
                ].decode("utf-8")
        except (ImportError, RuntimeError, ValueError) as exc:
            self.last_mode = "fallback"
            self.last_error = str(exc)

        self.last_mode = "fallback"
        lines = content.splitlines()
        start_index = max(0, start_line - 1)
        return "\n".join(lines[start_index : start_index + 30])

    def _find_node_at_line(
        self, node: tree_sitter.Node, line: int
    ) -> tree_sitter.Node | None:
        """递归查找包含目标行的最内层方法、构造器或类型节点。"""
        start_row = node.start_point[0] + 1
        end_row = node.end_point[0] + 1
        if line < start_row or line > end_row:
            return None

        for child in node.children:
            found = self._find_node_at_line(child, line)
            if found:
                return found

        if node.type in (
            "method_declaration",
            "constructor_declaration",
            "class_declaration",
            "interface_declaration",
            "record_declaration",
            "enum_declaration",
        ):
            return node
        return None
