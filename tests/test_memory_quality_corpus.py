from __future__ import annotations

import json
from pathlib import Path


CORPUS_PATH = Path(__file__).parent / "fixtures" / "memory_quality_v3.json"


def test_memory_quality_corpus_is_versioned_and_covers_required_behaviors() -> None:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    assert corpus["version"] == 3
    cases = corpus["cases"]
    case_ids = [case["id"] for case in cases]
    assert len(case_ids) == len(set(case_ids))
    assert {
        "explicit-output-preference",
        "implicit-style-is-not-preference",
        "explicit-project-decision",
        "assistant-proposal-user-confirmation",
        "code-fact-is-not-long-term-memory",
        "assistant-only-claim-is-not-decision",
        "decision-supersedes-old-decision",
        "discussion-does-not-cross-new-thread",
        "forget-suppresses-derived-memory",
        "unrelated-query-has-no-hit",
        "english-explicit-preference",
        "english-temporary-is-not-preference",
        "english-explicit-project-decision",
        "english-code-fact-is-not-memory",
        "english-undecided-is-not-decision",
        "english-assistant-proposal-user-confirmation",
    } <= set(case_ids)

    for case in cases:
        assert case["turns"]
        assert all(turn.get("user") for turn in case["turns"])
        expected = set(case.get("expected_kinds", []))
        forbidden = set(case.get("forbidden_kinds", []))
        assert expected.isdisjoint(forbidden)

        forget = case.get("forget")
        if forget is not None:
            assert forget["kind"] in expected
            assert forget["queries"]

        assert set(case.get("queries", ())).isdisjoint(
            case.get("no_hit_queries", ())
        )
