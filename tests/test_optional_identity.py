"""验收器同时拒绝值丢失、错误异常及正确代码未参与返回的补丁。"""

import pytest

from tests import optional_identity


@pytest.mark.parametrize(
    "expression",
    [
        "return value.orElseThrow();",
        "return value.orElseThrow(java.util.NoSuchElementException::new);",
        "return value.orElseThrow(NoSuchElementException::new);",
        "return value.orElseThrow(() -> new NoSuchElementException());",
        "return value.orElseThrow(() -> new "
        'java.util.NoSuchElementException("No value present"));',
        "return value.orElseThrow(() -> { return "
        'new NoSuchElementException("message"); });',
        "if (value.isPresent()) { return value.get(); } "
        'throw new java.util.NoSuchElementException("No value present");',
        "if (!value.isPresent()) throw new NoSuchElementException(); "
        "return value.get();",
        "if (value.isEmpty()) { throw new NoSuchElementException(); } "
        "else { return value.get(); }",
        "return value.isPresent() ? value.get() : value.orElseThrow();",
    ],
)
def test_correct_identity_contract(expression):
    source = (
        "class Store { String load(java.util.Optional<String> value) {"
        + expression
        + "} }"
    )
    optional_identity.assert_optional_identity(source.encode())


@pytest.mark.parametrize(
    "expression",
    [
        "return value.orElse(null);",
        "return value.orElseThrow(IllegalArgumentException::new);",
        "return value.filter(s -> !s.isEmpty()).orElseThrow();",
        'String correct = value.orElseThrow(); return "wrong";',
        "value = java.util.Optional.empty(); return value.orElseThrow();",
        'return value.orElseThrow(() -> new IllegalArgumentException("x"));',
        "return value.orElseThrow(unused -> new NoSuchElementException());",
        "return value.orElseThrow(() -> { audit(); "
        "return new NoSuchElementException(); });",
        "return value.orElseThrow(() -> new NoSuchElementException(effect()));",
        "if (value.isPresent()) throw new NoSuchElementException(); "
        "return value.get();",
        'if (value.isPresent()) return "wrong"; '
        "throw new NoSuchElementException();",
        "if (value.isEmpty()) throw new IllegalArgumentException(); "
        "return value.get();",
        "if (value.isPresent()) { audit(); return value.get(); } "
        "throw new NoSuchElementException();",
        "if (other.isPresent()) return value.get(); "
        "throw new NoSuchElementException();",
    ],
)
def test_wrong_identity_contract_is_not_accepted(expression):
    source = (
        "class Store { String load(java.util.Optional<String> value) {"
        + expression
        + "} }"
    )
    with pytest.raises(AssertionError):
        optional_identity.assert_optional_identity(source.encode())
