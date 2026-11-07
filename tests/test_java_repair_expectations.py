"""从业务要求给 Java 夹具判据正反例，防止正确节点掩盖错误返回路径。"""

import pytest

from tests import java_repair_expectations


def _optional(body: str) -> bytes:
    return (
        "class Config { String read(java.util.Optional<String> value) { "
        + body
        + " } }"
    ).encode("utf-8")


@pytest.mark.parametrize(
    "body",
    [
        "return value.orElseThrow(() -> new "
        'IllegalArgumentException("CONFIG_MISSING: server.port"));',
        'String prefix = "CONFIG_MISSING:"; String key = "server.port"; '
        "java.util.Optional<String> input = value; "
        "String kept = input.orElseThrow(() -> new "
        'java.lang.IllegalArgumentException(prefix + " " + key)); '
        "return (kept);",
        "return (value.orElseThrow(() -> { return new "
        'IllegalArgumentException("CONFIG_MISSING: " + '
        '"server.port"); }));',
    ],
)
def test_optional_predicate_accepts_unchanged_value_and_correct_supplier(body):
    java_repair_expectations.assert_optional_policy(
        _optional(body), "server.port"
    )


@pytest.mark.parametrize(
    "body",
    [
        "value = value.filter(s -> !s.isEmpty()); "
        "return value.orElseThrow(() -> new "
        'IllegalArgumentException("CONFIG_MISSING: server.port"));',
        "java.util.Optional<String> filtered = value.filter(s -> "
        "!s.isEmpty()); "
        "return filtered.orElseThrow(() -> new "
        'IllegalArgumentException("CONFIG_MISSING: server.port"));',
        "return value.orElseThrow(() -> new "
        'IllegalArgumentException("wrong CONFIG_MISSING: '
        'server.port"));',
        "IllegalArgumentException unused = new "
        'IllegalArgumentException("CONFIG_MISSING: server.port"); '
        "return value.orElseThrow(() -> new "
        'IllegalStateException("CONFIG_MISSING: server.port"));',
        "return value.orElseThrow(() -> new "
        'IllegalArgumentException("CONFIG_MISSING: wrong.key"));',
        "return value.orElseThrow(() -> new "
        'IllegalArgumentException("CONFIG_MISSING: '
        'server.port")).trim();',
    ],
)
def test_optional_predicate_rejects_changed_value_or_wrong_actual_exception(
    body,
):
    with pytest.raises(AssertionError):
        java_repair_expectations.assert_optional_policy(
            _optional(body), "server.port"
        )


@pytest.mark.parametrize(
    "body",
    [
        'return new java.math.BigDecimal("0.10");',
        'String exact = "0." + "10"; '
        "java.math.BigDecimal amount = new "
        "java.math.BigDecimal(exact); return (amount);",
        'return new java.math.BigDecimal("00.10");',
    ],
)
def test_amount_predicate_accepts_actual_decimal_return_and_scale(body):
    candidate = (
        "class Price { java.math.BigDecimal amount() { " + body + " } }"
    ).encode("utf-8")
    java_repair_expectations.assert_amount_policy(candidate)


@pytest.mark.parametrize(
    "body",
    [
        'new java.math.BigDecimal("0.10"); return java.math.BigDecimal.ZERO;',
        "java.math.BigDecimal unused = new "
        'java.math.BigDecimal("0.10"); return '
        "java.math.BigDecimal.ZERO;",
        'return new java.math.BigDecimal("0.1");',
        'return new java.math.BigDecimal("1.00");',
        "return new java.math.BigDecimal(0.10);",
    ],
)
def test_amount_predicate_rejects_unused_correct_nodes_and_wrong_return(body):
    candidate = (
        "class Price { java.math.BigDecimal amount() { " + body + " } }"
    ).encode("utf-8")
    with pytest.raises(AssertionError):
        java_repair_expectations.assert_amount_policy(candidate)


def test_optional_predicate_rejects_eager_unused_get_before_correct_return():
    candidate = _optional(
        "String unused = value.get(); return value.orElseThrow("
        '() -> new IllegalArgumentException("CONFIG_MISSING: server.port"));'
    )
    with pytest.raises(AssertionError):
        java_repair_expectations.assert_optional_policy(
            candidate, "server.port"
        )


def test_amount_predicate_rejects_unrelated_failing_initializer():
    candidate = (
        "class Price { java.math.BigDecimal amount() { "
        'int unused = Integer.parseInt("wrong"); '
        'return new java.math.BigDecimal("0.10"); } }'
    ).encode("utf-8")
    with pytest.raises(AssertionError):
        java_repair_expectations.assert_amount_policy(candidate)
