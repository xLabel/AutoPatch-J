"""Java 语法块定位，保留 Tree-sitter 原生容错能力。"""

from __future__ import annotations

import dataclasses

from autopatch_j.core.project import java_ast


@dataclasses.dataclass(frozen=True, slots=True)
class BlockRange:
    """一次定位的实际行范围及源码语法诊断。"""

    start_line: int
    end_line: int
    source_has_errors: bool
    syntax_issues: tuple[str, ...]


class JavaBlockExtractor:
    """定位最内层方法、构造器或类型；不隐式切换读取工具。"""

    def extract(self, content: str, line: int) -> BlockRange | None:
        """定位最内层语法块；未命中不替换成另一种读取方式。"""
        source = java_ast.parse(content)
        node = self._find_node_at_line(source.tree.root_node, line)
        if node is None:
            return None
        return BlockRange(
            node.start_point.row + 1,
            node.end_point.row + 1,
            source.source_has_errors,
            source.syntax_issues(),
        )

    def _find_node_at_line(
        self, node: java_ast.JavaNode, line: int
    ) -> java_ast.JavaNode | None:
        if not node.start_point.row + 1 <= line <= node.end_point.row + 1:
            return None
        for child in node.children:
            found = self._find_node_at_line(child, line)
            if found is not None:
                return found
        if node.type in java_ast.TYPE_NODES | java_ast.METHOD_NODES:
            return node
        return None
