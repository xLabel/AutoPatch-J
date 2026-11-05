"""Java 语法块定位，返回实际行范围及每次调用独立的降级信息。"""

import dataclasses

tree_sitter_import_error: ImportError | None = None
try:
    import tree_sitter
    import tree_sitter_java
except ImportError as exc:
    tree_sitter_import_error = exc


@dataclasses.dataclass(frozen=True, slots=True)
class BlockRange:
    """一次定位的行范围及降级信息，不共享可变 last_* 状态。"""

    start_line: int
    end_line: int
    mode: str
    error: str | None = None


class JavaBlockExtractor:
    """优先最内层方法、构造器或类型，无法定位时退化为 30 行窗口。"""

    def extract(self, content: str, line: int) -> BlockRange:
        """返回包含目标行的最内层 Java 结构，无法定位时给出行窗。"""
        try:
            if tree_sitter_import_error is not None:
                raise tree_sitter_import_error
            parser = tree_sitter.Parser(
                tree_sitter.Language(tree_sitter_java.language())
            )
            tree = parser.parse(content.encode("utf-8"))
            node = self._find_node_at_line(tree.root_node, line)
            if node is not None:
                return BlockRange(
                    node.start_point.row + 1,
                    node.end_point.row + 1,
                    "java_block",
                )
            error = "目标行未命中 Java 结构，使用行窗。"
        except (ImportError, RuntimeError, ValueError) as exc:
            error = str(exc)
        return BlockRange(
            line, min(len(content.splitlines()), line + 29), "fallback", error
        )

    def _find_node_at_line(
        self, node: tree_sitter.Node, line: int
    ) -> tree_sitter.Node | None:
        if not node.start_point.row + 1 <= line <= node.end_point.row + 1:
            return None
        for child in node.children:
            found = self._find_node_at_line(child, line)
            if found is not None:
                return found
        if node.type in {
            "method_declaration",
            "constructor_declaration",
            "class_declaration",
            "interface_declaration",
            "record_declaration",
            "enum_declaration",
        }:
            return node
        return None
