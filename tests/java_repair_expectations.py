"""两个简单 Java 夹具的返回值判据；不分析任意 Java 程序或执行编译。"""

import decimal
import json

import tree_sitter
import tree_sitter_java

_COMMENTS = {"line_comment", "block_comment"}


def _children(node: tree_sitter.Node) -> list[tree_sitter.Node]:
    return [
        child for child in node.named_children if child.type not in _COMMENTS
    ]


def _field(node: tree_sitter.Node, name: str) -> tree_sitter.Node:
    field = node.child_by_field_name(name)
    assert field is not None, (node.type, name)
    return field


def _text(node: tree_sitter.Node) -> bytes:
    content = node.text
    assert content is not None
    return content


def _nodes(root: tree_sitter.Node) -> list[tree_sitter.Node]:
    nodes = []
    pending = [root]
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(_children(node))
    return nodes


def _resolve(
    node: tree_sitter.Node,
    initializers: dict[bytes, tree_sitter.Node],
    used: set[bytes],
) -> tree_sitter.Node:
    """识别括号和只赋初值的局部别名，不把无关正确节点视为返回值。"""
    seen = set()
    while True:
        if node.type == "parenthesized_expression":
            node = _children(node)[0]
        elif node.type == "identifier" and _text(node) in initializers:
            name = _text(node)
            assert name not in seen, "局部别名循环"
            seen.add(name)
            used.add(name)
            node = initializers[name]
        else:
            return node


def _returned(
    candidate: bytes, method_name: bytes
) -> tuple[tree_sitter.Node, dict[bytes, tree_sitter.Node]]:
    """限于夹具的顺序局部声明和单个返回，拒绝赋值、分支和隐藏的值转换。"""
    parser = tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_java.language())
    )
    tree = parser.parse(candidate)
    assert not tree.root_node.has_error
    methods = [
        node
        for node in _nodes(tree.root_node)
        if node.type == "method_declaration"
        and _text(_field(node, "name")) == method_name
    ]
    assert len(methods) == 1
    body = _field(methods[0], "body")
    assert not any(
        node.type in {"assignment_expression", "update_expression"}
        for node in _nodes(body)
    )
    statements = _children(body)
    assert statements and statements[-1].type == "return_statement"
    assert all(
        node.type == "local_variable_declaration" for node in statements[:-1]
    )
    initializers = {}
    for statement in statements[:-1]:
        for declaration in _children(statement):
            if declaration.type == "variable_declarator":
                name = _text(_field(declaration, "name"))
                assert name not in initializers
                initializers[name] = _field(declaration, "value")
    return _children(statements[-1])[0], initializers


def _string(
    node: tree_sitter.Node,
    initializers: dict[bytes, tree_sitter.Node],
    used: set[bytes],
) -> str:
    """只折叠夹具中的字符串常量、连接及局部别名。"""
    node = _resolve(node, initializers, used)
    if node.type == "string_literal":
        value = json.loads(_text(node).decode("utf-8"))
        assert isinstance(value, str)
        return value
    assert node.type == "binary_expression"
    assert _text(_field(node, "operator")) == b"+"
    return _string(_field(node, "left"), initializers, used) + _string(
        _field(node, "right"), initializers, used
    )


def assert_optional_policy(candidate: bytes, key: str) -> None:
    """read 必须返回未改变的 Optional 值，实际 supplier 产生带正确前缀的异常。"""
    returned, initializers = _returned(candidate, b"read")
    used = set()
    returned = _resolve(returned, initializers, used)
    assert returned.type == "method_invocation"
    assert _text(_field(returned, "name")) == b"orElseThrow"
    receiver = _resolve(_field(returned, "object"), initializers, used)
    assert receiver.type == "identifier" and _text(receiver) == b"value"
    arguments = _children(_field(returned, "arguments"))
    assert len(arguments) == 1
    supplier = _resolve(arguments[0], initializers, used)
    assert supplier.type == "lambda_expression"
    assert b"".join(_text(_field(supplier, "parameters")).split()) == b"()"
    exception = _field(supplier, "body")
    if exception.type == "block":
        statements = _children(exception)
        assert len(statements) == 1 and statements[0].type == "return_statement"
        exception = _children(statements[0])[0]
    exception = _resolve(exception, initializers, used)
    assert exception.type == "object_creation_expression"
    assert (
        _text(_field(exception, "type")).split(b".")[-1]
        == b"IllegalArgumentException"
    )
    arguments = _children(_field(exception, "arguments"))
    assert len(arguments) == 1
    message = _string(arguments[0], initializers, used)
    assert message.startswith("CONFIG_MISSING:") and key in message
    assert used == initializers.keys(), "不接受返回值或异常消息之外的额外初始化"


def assert_amount_policy(candidate: bytes) -> None:
    """amount 实际返回 String 构造的十进制 0.10，不能用未使用的正确构造蒙混通过。"""
    returned, initializers = _returned(candidate, b"amount")
    used = set()
    returned = _resolve(returned, initializers, used)
    assert returned.type == "object_creation_expression"
    assert _text(_field(returned, "type")).split(b".")[-1] == b"BigDecimal"
    arguments = _children(_field(returned, "arguments"))
    assert len(arguments) == 1
    value = _string(arguments[0], initializers, used)
    assert value == value.strip() and "_" not in value
    amount = decimal.Decimal(value)
    assert amount.is_finite() and amount == decimal.Decimal("0.10")
    assert amount.as_tuple().exponent == -2
    assert used == initializers.keys(), "不接受返回值之外的额外初始化"
