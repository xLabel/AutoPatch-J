"""从 Java 文件提取类型、构造器和方法的索引条目。"""

from __future__ import annotations

from autopatch_j.core.project import java_ast
from autopatch_j.core.project import symbols


class JavaSymbolExtractor:
    """Java 类/方法符号提取器。

    只负责从单个 Java 文件提取索引条目，不关心 SQLite 存储和项目遍历。
    """

    def extract(
        self, rel_path: str, source: java_ast.JavaSource, mtime: float
    ) -> list[symbols.SymbolIndexEntry]:
        """提取原生解析识别的声明，不宣称语法或语义完备。"""
        # 官方 tags 覆盖 class/interface/method，补充本项目已有类型和构造器。
        pattern = """
        (enum_declaration) @definition.enum
        (record_declaration) @definition.record
        (constructor_declaration) @definition.constructor
        (compact_constructor_declaration) @definition.constructor
        (annotation_type_declaration) @definition.annotation
        """
        entries = []
        for _, captures in source.tag_matches(pattern):
            for tag, nodes in captures.items():
                if not tag.startswith("definition."):
                    continue
                for node in nodes:
                    name = node.child_by_field_name("name")
                    if name is None:
                        continue
                    line, column = source.point(name)
                    end_line, end_column = source.point(node, end=True)
                    entries.append(
                        symbols.SymbolIndexEntry(
                            path=rel_path,
                            name=java_ast.text(name),
                            kind=tag.removeprefix("definition."),
                            line=line,
                            column=column,
                            end_line=end_line,
                            end_column=end_column,
                            owner=java_ast.owner(node, source),
                            signature=source.signature(node),
                            mtime=mtime,
                            source_sha256=source.source_sha256,
                            source_has_errors=source.source_has_errors,
                        )
                    )
        entries.sort(key=lambda item: (item.line, item.column, item.kind))
        return entries
