from __future__ import annotations

from pathlib import Path

from autopatch_j.core.project.symbol_index import SymbolIndexEntry


class JavaSymbolExtractor:
    """
    Java 类/方法符号提取器。

    只负责从单个 Java 文件提取索引条目，不关心 SQLite 存储和项目遍历。
    """

    def extract(self, rel_path: str, full_path: Path, mtime: float) -> list[SymbolIndexEntry]:
        symbols: list[SymbolIndexEntry] = []
        from tree_sitter import Language, Parser, Query, QueryCursor
        import tree_sitter_java as tsjava

        content = full_path.read_text(encoding="utf-8", errors="replace")
        language = Language(tsjava.language())
        parser = Parser(language)
        tree = parser.parse(content.encode("utf-8"))

        query = Query(
            language,
            "(class_declaration name: (identifier) @class.name) "
            "(method_declaration name: (identifier) @method.name) "
            "(interface_declaration name: (identifier) @interface.name) "
            "(enum_declaration name: (identifier) @enum.name) "
            "(record_declaration name: (identifier) @record.name) "
            "(constructor_declaration name: (identifier) @constructor.name)",
        )
        captures = QueryCursor(query).captures(tree.root_node)
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
                symbols.append(
                    SymbolIndexEntry(
                        path=rel_path,
                        name=node.text.decode("utf-8"),
                        kind=kind_by_tag.get(tag, "symbol"),
                        line=node.start_point[0] + 1,
                        container=rel_path,
                        mtime=mtime,
                    )
                )
        return symbols
