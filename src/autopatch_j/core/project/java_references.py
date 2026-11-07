"""按当前 Java 语法定位引用候选，不能替代类型求解或完整调用图。"""

from __future__ import annotations

from collections.abc import Collection
import dataclasses
import hashlib
import pathlib

from autopatch_j import config
from autopatch_j import error_messages
from autopatch_j.core.project import java_ast
from autopatch_j.core.project import java_paths
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_reader

PAGE_SIZE = 20


class ReferenceQueryError(ValueError):
    """名称或范围可由模型调整；运行故障直接传播。"""


@dataclasses.dataclass(frozen=True, slots=True)
class JavaReference:
    """一个语法候选的出处、词法环境及受预算约束的原文片段。"""

    path: str
    kind: str
    name: str
    line: int
    column: int
    end_line: int
    end_column: int
    owner: str
    enclosing_method: str | None
    source_sha256: str
    excerpt: str
    receiver: str | None
    arguments: str | None
    excerpt_truncated: bool
    source_has_errors: bool


@dataclasses.dataclass(frozen=True, slots=True)
class QueryWarning:
    """未能提供可靠证据的文件，不能被遗漏为零匹配。"""

    path: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class ReferenceSearchResult:
    """实时查询页；跨页没有冻结快照或全局调用完备性保证。"""

    entries: tuple[JavaReference, ...]
    next_offset: int | None
    complete: bool
    searched_paths: tuple[str, ...]
    warnings: tuple[QueryWarning, ...]
    notice: str = (
        "仅为语法引用候选，须读取当前源码核实类型和调用约定；"
        "跨页实时查询，源码变化后重新核查。"
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _Candidate:
    node: java_ast.JavaNode
    kind: str
    name: str
    receiver: java_ast.JavaNode | None = None
    arguments: java_ast.JavaNode | None = None


def search(
    root: pathlib.Path,
    name: str,
    paths: tuple[str, ...],
    offset: int = 0,
    *,
    ignored_dirs: Collection[str] = config.IGNORED_DIRS,
) -> ReferenceSearchResult:
    """按区分大小写的简单名称精确查找；构造器用类型名，不用 new。"""
    if not name.strip() or not paths:
        raise ReferenceQueryError(error_messages.INVALID_REFERENCE_QUERY)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ReferenceQueryError(error_messages.INVALID_OFFSET)
    targets = java_paths.expand(root, paths, ignored_dirs)
    if not targets:
        return ReferenceSearchResult(
            (),
            None,
            False,
            (),
            (QueryWarning(".", "范围内没有 Java 文件，未执行查询。"),),
        )
    entries: list[JavaReference] = []
    warnings = []
    searched = []
    matched = 0
    budget = source_reader.MAX_SOURCE_CHARACTERS
    for relative in targets:
        searched.append(relative)
        path = repo_path.resolve_source_path(root, relative)
        source = java_ast.read(path)
        candidates = _candidates(source)
        current = repo_path.resolve_source_path(root, relative).read_bytes()
        if hashlib.sha256(current).hexdigest() != source.source_sha256:
            raise ReferenceQueryError(error_messages.SOURCE_CHANGED)
        if source.source_has_errors:
            warnings.append(
                QueryWarning(relative, "; ".join(source.syntax_issues()))
            )
        for candidate in candidates:
            if candidate.name != name:
                continue
            matched += 1
            if matched <= offset:
                continue
            if len(entries) == PAGE_SIZE:
                return ReferenceSearchResult(
                    tuple(entries),
                    offset + PAGE_SIZE,
                    False,
                    tuple(searched),
                    tuple(warnings),
                )
            entry = _describe(relative, source, candidate, budget)
            budget -= (
                len(entry.excerpt)
                + len(entry.receiver or "")
                + len(entry.arguments or "")
            )
            entries.append(entry)
    return ReferenceSearchResult(
        tuple(entries), None, not warnings, tuple(searched), tuple(warnings)
    )


def _candidates(source: java_ast.JavaSource) -> list[_Candidate]:
    pattern = """
    (method_reference) @extra.method_reference
    (object_creation_expression) @extra.constructor
    (superclass) @extra.extends
    (super_interfaces) @extra.implements
    (extends_interfaces) @extra.extends
    """
    found = {}
    for _, captures in source.tag_matches(pattern):
        for node in captures.get("reference.call", []):
            call = node.parent
            if call is None:
                continue
            called_name = call.child_by_field_name("name")
            if called_name is not None:
                item = _Candidate(
                    call,
                    "call",
                    java_ast.text(called_name),
                    call.child_by_field_name("object"),
                    node,
                )
                found[(call.start_byte, item.kind)] = item
        for tag, nodes in captures.items():
            if not tag.startswith("extra."):
                continue
            for node in nodes:
                for item in _extra_candidates(node, tag.removeprefix("extra.")):
                    found[(item.node.start_byte, item.kind)] = item
    return sorted(
        found.values(), key=lambda item: (item.node.start_byte, item.kind)
    )


def _extra_candidates(
    node: java_ast.JavaNode,
    kind: str,
) -> list[_Candidate]:
    if kind == "method_reference":
        receiver, target = node.children[0], node.children[-1]
        constructor = target.type == "new"
        name = (
            _simple_type_name(receiver)
            if constructor
            else java_ast.text(target)
        )
        return [
            _Candidate(
                node,
                "constructor_reference" if constructor else kind,
                name,
                receiver,
            )
        ]
    if kind == "constructor":
        target = node.child_by_field_name("type")
        if target is None:
            return []
        return [
            _Candidate(
                node,
                kind,
                _simple_type_name(target),
                arguments=node.child_by_field_name("arguments"),
            )
        ]
    result = []
    for child in node.named_children:
        types = child.named_children if child.type == "type_list" else [child]
        for target in types:
            if target.type in {
                "type_identifier",
                "scoped_type_identifier",
                "generic_type",
                "annotated_type",
            }:
                result.append(
                    _Candidate(target, kind, _simple_type_name(target))
                )
    return result


def _simple_type_name(node: java_ast.JavaNode) -> str:
    """只拆源码类型写法，不根据命名习惯推断接收者类型。"""
    if node.type == "type_identifier":
        return java_ast.text(node)
    for child in reversed(node.named_children):
        if child.type in {
            "type_identifier",
            "scoped_type_identifier",
            "generic_type",
        }:
            return _simple_type_name(child)
    return java_ast.text(node)


def _describe(
    path: str, source: java_ast.JavaSource, candidate: _Candidate, budget: int
) -> JavaReference:
    raw = [
        java_ast.text(candidate.node),
        (
            java_ast.text(candidate.receiver)
            if candidate.receiver is not None
            else None
        ),
        (
            java_ast.text(candidate.arguments)
            if candidate.arguments is not None
            else None
        ),
    ]
    bounded = []
    truncated = False
    for part in raw:
        if part is None:
            bounded.append(None)
            continue
        visible = part[:budget]
        bounded.append(visible)
        truncated |= len(visible) < len(part)
        budget -= len(visible)
    line, column = source.point(candidate.node)
    end_line, end_column = source.point(candidate.node, end=True)
    return JavaReference(
        path,
        candidate.kind,
        candidate.name,
        line,
        column,
        end_line,
        end_column,
        java_ast.owner(candidate.node, source),
        java_ast.enclosing_method(candidate.node),
        source.source_sha256,
        bounded[0] or "",
        bounded[1],
        bounded[2],
        truncated,
        source.source_has_errors,
    )
