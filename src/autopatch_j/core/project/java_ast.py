"""真实 Java 语法树的查询与位置转换；不求解类型或跨文件引用。"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib

import tree_sitter
import tree_sitter_java

from autopatch_j.core import errors
from autopatch_j.core.project import source_text

type JavaNode = tree_sitter.Node

TYPE_NODES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
)
METHOD_NODES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "compact_constructor_declaration",
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class JavaSource:
    """解析输入及其原文件指纹，UTF-8 节点偏移只用于该输入。"""

    utf8: bytes
    tree: tree_sitter.Tree
    source_sha256: str

    @property
    def source_has_errors(self) -> bool:
        """原生树是否含语法错误；不表示编译结果。"""
        return self.tree.root_node.has_error

    def syntax_issues(self) -> tuple[str, ...]:
        """返回有界的原生 ERROR/missing 位置，未展示部分明确说明。"""
        pending = [self.tree.root_node]
        issues = []
        count = 0
        while pending:
            node = pending.pop()
            if node.is_error or node.is_missing:
                count += 1
                if len(issues) < 20:
                    line, column = self.point(node)
                    kind = (
                        "missing " + node.type if node.is_missing else node.type
                    )
                    issues.append(f"{line}:{column} {kind}")
            if node.has_error:
                pending.extend(reversed(node.children))
        if count > len(issues):
            issues.append(f"另有 {count - len(issues)} 处语法问题。")
        return tuple(issues)

    def matches(
        self, pattern: str
    ) -> list[tuple[int, dict[str, list[JavaNode]]]]:
        """保留每次匹配的捕获关联，避免把不同调用的名称混在一起。"""
        language = self.tree.language
        if language is None:
            raise RuntimeError("语法树缺少 Java language。")
        cursor = tree_sitter.QueryCursor(tree_sitter.Query(language, pattern))
        matched = cursor.matches(self.tree.root_node)
        if cursor.did_exceed_match_limit:
            raise errors.OperationError(
                "Java 查询未完整完成，不能当作没有匹配。"
            )
        return matched

    def tag_matches(
        self, extra_patterns: str
    ) -> list[tuple[int, dict[str, list[JavaNode]]]]:
        """统一读取已安装官方 tags，补充模式不能替换或丢弃原生查询。"""
        return self.matches(tree_sitter_java.TAGS_QUERY + extra_patterns)

    def point(self, node: JavaNode, *, end: bool = False) -> tuple[int, int]:
        """返回从 1 开始的行及 Unicode 字符列，不返回原文件 byte offset。"""
        row, byte_column = node.end_point if end else node.start_point
        offset = node.end_byte if end else node.start_byte
        prefix = self.utf8[offset - byte_column : offset]
        return row + 1, len(prefix.decode("utf-8")) + 1

    def signature(self, node: JavaNode) -> str:
        """声明头保留源码写法，不把简单类型名冒充已解析类型。"""
        body = node.child_by_field_name("body")
        end = body.start_byte if body is not None else node.end_byte
        return self.utf8[node.start_byte : end].decode("utf-8").strip()


def parse(content: str, raw: bytes | None = None) -> JavaSource:
    """使用原生容错树；语法错误是输入事实，解析器异常才是运行故障。"""
    utf8 = content.encode("utf-8")
    try:
        parser = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_java.language())
        )
        tree = parser.parse(utf8)
    except (RuntimeError, ValueError) as exc:
        raise errors.OperationError(f"Java 解析器执行失败：{exc}") from exc
    return JavaSource(
        utf8, tree, hashlib.sha256(raw if raw is not None else utf8).hexdigest()
    )


def read(path: pathlib.Path) -> JavaSource:
    """读取并解析当前源码，保留原始字节指纹及原生错误节点。"""
    decoded = source_text.read(path)
    return parse(decoded.content, decoded.raw_bytes)


def text(node: JavaNode) -> str:
    """只读取本次未编辑语法树的原文。"""
    value = node.text
    if value is None:
        raise ValueError("Tree-sitter 未提供节点原文。")
    return value.decode("utf-8")


def owner(node: JavaNode, source: JavaSource) -> str:
    """词法所属类型，不代表 Java 的完整限定名或接收者类型。"""
    names = []
    parent = node.parent
    while parent is not None:
        if (
            parent.type == "class_body"
            and parent.parent is not None
            and parent.parent.type
            in {"object_creation_expression", "enum_constant"}
        ):
            line, column = source.point(parent)
            names.append(f"<anonymous@{line}:{column}>")
        if parent.type in TYPE_NODES:
            name = parent.child_by_field_name("name")
            if name is not None:
                names.append(text(name))
        parent = parent.parent
    return ".".join(reversed(names))


def enclosing_method(node: JavaNode) -> str | None:
    """返回最近的词法方法/构造器名，lambda 沿用其所属方法。"""
    parent = node.parent
    while parent is not None:
        if parent.type in METHOD_NODES:
            name = parent.child_by_field_name("name")
            return text(name) if name is not None else None
        if parent.type in TYPE_NODES or (
            parent.type == "class_body"
            and parent.parent is not None
            and parent.parent.type
            in {"object_creation_expression", "enum_constant"}
        ):
            return None
        parent = parent.parent
    return None
