"""用一组真实扫描正反例核对新增本地候选模式，不治理每条用户规则。"""

import pytest

from autopatch_j.scanners.semgrep import scanner

EXAMPLES = """import java.math.BigDecimal;
class Candidates {
  boolean same(String left, String right) { return left == right; }
  boolean different(java.lang.String left, java.lang.String right) { return left != right; }
  BigDecimal fractional() { return new BigDecimal(0.10); }
  BigDecimal floating(float amount) { return new java.math.BigDecimal(amount); }
  void launch(Thread worker) { worker.run(); }
  void qualified(java.lang.Thread worker) { worker.run(); }
  boolean absent(String value) { return value == null; }
  boolean present(String value) { return null != value; }
  boolean numeric(int left, int right) { return left == right; }
  BigDecimal exact() { return new BigDecimal("0.10"); }
  BigDecimal integral() { return new BigDecimal(10); }
  void synchronous(Runnable task) { task.run(); }
}
"""

EXPECTED = {
    ("autopatch-j.java.correctness.string-reference-equality", 3),
    ("autopatch-j.java.correctness.string-reference-equality", 4),
    ("autopatch-j.java.correctness.bigdecimal-floating-constructor", 5),
    ("autopatch-j.java.correctness.bigdecimal-floating-constructor", 6),
    ("autopatch-j.java.correctness.direct-thread-run", 7),
    ("autopatch-j.java.correctness.direct-thread-run", 8),
}


def test_classic_java_candidates_include_only_representative_positives(
    tmp_path,
):
    engine = scanner.SemgrepScanner()
    status = engine.probe()
    if status.status != "ready":
        pytest.skip(f"受管 Semgrep 未就绪，未安装或检查规则：{status.message}")
    (tmp_path / "Candidates.java").write_text(EXAMPLES, encoding="utf-8")
    scanned = engine.scan(tmp_path, ["Candidates.java"])
    assert scanned.status == "ok", scanned.message
    rule_ids = {rule for rule, _ in EXPECTED}
    observed = {
        (finding.check_id, finding.region.start_line)
        for finding in scanned.findings
        if finding.check_id in rule_ids
    }
    assert observed == EXPECTED
