"""从 Java 文件提取类型、构造器和方法的索引条目。"""

from __future__ import annotations

import pathlib

tree_sitter_import_error: ImportError | None = None
try:
    import tree_sitter
    import tree_sitter_java
except ImportError as exc:
    tree_sitter_import_error = exc


from autopatch_j.core.project import source_text
from autopatch_j.core.project import symbols


class JavaSymbolExtractor:
    """Java 类/方法符号提取器。

    只负责从单个 Java 文件提取索引条目，不关心 SQLite 存储和项目遍历。
    """

    def extract(
        self, rel_path: str, full_path: pathlib.Path, mtime: float
    ) -> list[symbols.SymbolIndexEntry]:
        """从单个 Java 文件提取类型、构造器和方法；错误交索引层记录降级。"""
        entries: list[symbols.SymbolIndexEntry] = []
        if tree_sitter_import_error is not None:
            raise tree_sitter_import_error

        content = source_text.read(full_path).content
        language = tree_sitter.Language(tree_sitter_java.language())
        parser = tree_sitter.Parser(language)
        tree = parser.parse(content.encode("utf-8"))

        query = tree_sitter.Query(
            language,
            """
            (class_declaration name: (identifier) @class.name)
            (method_declaration name: (identifier) @method.name)
            (interface_declaration name: (identifier) @interface.name)
            (enum_declaration name: (identifier) @enum.name)
            (record_declaration name: (identifier) @record.name)
            (constructor_declaration name: (identifier) @constructor.name)
            """,
        )
        captures = tree_sitter.QueryCursor(query).captures(tree.root_node)
        kind_by_tag = {
            "class.name": "class",
            "method.name": "method",
            "interface.name": "interface",
            "enum.name": "enum",
            "record.name": "record",
            "constructor.name": "constructor",
        }

        for tag, nodes in captures.items():
            for node in nodes:
                name = node.text
                if name is None:
                    raise ValueError("Tree-sitter 未提供符号文本")
                entries.append(
                    symbols.SymbolIndexEntry(
                        path=rel_path,
                        name=name.decode("utf-8"),
                        kind=kind_by_tag.get(tag, "symbol"),
                        line=node.start_point[0] + 1,
                        container=rel_path,
                        mtime=mtime,
                    )
                )
        return entries
