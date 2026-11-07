"""有界 Java 验收：已有 Optional 值原样返回，缺值保持 NoSuchElementException。"""

import tree_sitter
import tree_sitter_java


def assert_optional_identity(candidate: bytes) -> None:
    """验证已有值及缺值异常类型，不验证异常消息、编译或完整业务等价。

    只判定可直接证明的返回形式；超出范围须调查，不能误放行。
    """
    parser = tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_java.language())
    )
    tree = parser.parse(candidate)
    assert not tree.root_node.has_error
    pending = [tree.root_node]
    selected = []
    while pending:
        node = pending.pop()
        if node.type == "method_declaration":
            name = node.child_by_field_name("name")
            parameters = node.child_by_field_name("parameters")
            if (
                name is not None
                and name.text == b"load"
                and parameters is not None
            ):
                if (
                    parameters.text is not None
                    and b"Optional<" in parameters.text
                ):
                    selected.append(node)
        pending.extend(node.named_children)
    assert len(selected) == 1, "须明确找到目标 Optional 重载"
    method = selected[0]
    parameters = method.child_by_field_name("parameters")
    body = method.child_by_field_name("body")
    assert parameters is not None and body is not None
    formal = parameters.named_children[0]
    value = formal.child_by_field_name("name")
    assert value is not None and value.text is not None
    for present in (True, False):
        _assert_statements(_statements(body), value.text, present)


def _statements(node: tree_sitter.Node) -> list[tree_sitter.Node]:
    if node.type == "block":
        return [child for child in node.named_children if not child.is_extra]
    return [node]


def _assert_statements(
    statements: list[tree_sitter.Node], value: bytes, present: bool
) -> None:
    """仅沿已知 Optional 条件检查有值/无值两条路径，拒绝未知语句。"""
    assert statements, "目标路径必须明确返回值或抛出异常"
    first, rest = statements[0], statements[1:]
    if first.type == "if_statement":
        condition = first.child_by_field_name("condition")
        assert condition is not None
        branch = first.child_by_field_name(
            "consequence"
            if _condition(condition, value, present)
            else "alternative"
        )
        _assert_statements(
            _statements(branch) if branch is not None else rest,
            value,
            present,
        )
        return
    assert not rest, "不接受终止语句之外的额外代码"
    children = [child for child in first.named_children if not child.is_extra]
    assert len(children) == 1
    expression = children[0]
    if first.type == "throw_statement":
        assert not present and _exception_matches(expression)
        return
    assert first.type == "return_statement", "不接受未知语句或副作用"
    if expression.type == "ternary_expression":
        condition = expression.child_by_field_name("condition")
        assert condition is not None
        expression = expression.child_by_field_name(
            "consequence"
            if _condition(condition, value, present)
            else "alternative"
        )
        assert expression is not None
    assert expression.type == "method_invocation"
    receiver = expression.child_by_field_name("object")
    name = expression.child_by_field_name("name")
    arguments = expression.child_by_field_name("arguments")
    assert receiver is not None and receiver.text == value
    assert arguments is not None
    actual_arguments = arguments.named_children
    if name is not None and name.text == b"get":
        assert present and not actual_arguments
        return
    assert name is not None and name.text == b"orElseThrow"
    if not actual_arguments:
        return
    assert len(actual_arguments) == 1
    supplier = actual_arguments[0]
    assert _exception_supplier_matches(
        supplier
    ), "缺值异常类型必须保持；不能通过有副作用或未经核实的供应器误放行"


def _condition(node: tree_sitter.Node, value: bytes, present: bool) -> bool:
    if node.type == "parenthesized_expression":
        return _condition(node.named_children[0], value, present)
    if node.type == "unary_expression":
        assert node.children[0].text == b"!"
        return not _condition(node.named_children[-1], value, present)
    assert node.type == "method_invocation", "仅判定原参数的 Optional 存在性"
    receiver = node.child_by_field_name("object")
    name = node.child_by_field_name("name")
    arguments = node.child_by_field_name("arguments")
    assert receiver is not None and receiver.text == value
    assert arguments is not None and not arguments.named_children
    assert name is not None and name.text in {b"isPresent", b"isEmpty"}
    return present if name.text == b"isPresent" else not present


def _exception_supplier_matches(supplier: tree_sitter.Node) -> bool:
    names = {b"NoSuchElementException", b"java.util.NoSuchElementException"}
    if supplier.type == "method_reference":
        return (
            supplier.children[-1].type == "new"
            and supplier.named_children[0].text in names
        )
    if supplier.type != "lambda_expression":
        return False
    parameters = supplier.child_by_field_name("parameters")
    body = supplier.child_by_field_name("body")
    if parameters is None or parameters.named_children or body is None:
        return False
    if parameters.type not in {"formal_parameters", "inferred_parameters"}:
        return False
    if body.type == "block":
        statements = [node for node in body.named_children if not node.is_extra]
        if len(statements) != 1 or statements[0].type != "return_statement":
            return False
        body = statements[0].named_children[0]
    return _exception_matches(body)


def _exception_matches(body: tree_sitter.Node) -> bool:
    names = {b"NoSuchElementException", b"java.util.NoSuchElementException"}
    if body.type != "object_creation_expression":
        return False
    target = body.child_by_field_name("type")
    arguments = body.child_by_field_name("arguments")
    if target is None or target.text not in names or arguments is None:
        return False
    if any(child.type == "class_body" for child in body.named_children):
        return False
    actual = arguments.named_children
    return not actual or (
        len(actual) == 1 and actual[0].type == "string_literal"
    )
