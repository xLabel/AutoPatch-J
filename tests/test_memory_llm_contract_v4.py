from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from autopatch_j.core.memory import MemoryManager, MemoryOutputContractError
from autopatch_j.core.memory.contracts import parse_extraction_response
from autopatch_j.core.memory.models import ExtractionResult
from autopatch_j.core.memory.pipeline import MemoryPipeline
from autopatch_j.core.memory.store import MemoryStore
from autopatch_j.llm.client import LLMClient
from autopatch_j.llm.models import LLMResponse
from autopatch_j.llm.options import LLMCallPurpose
from autopatch_j.llm.parser import LLMResponseParser
from autopatch_j.llm.dialects import StandardDialect


class SequenceMemoryLLM:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.purposes: list[LLMCallPurpose] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        purpose: LLMCallPurpose = LLMCallPurpose.REACT,
    ) -> SimpleNamespace:
        assert tools is None
        self.purposes.append(purpose)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            outcome = outcome(messages, purpose)
        content = outcome if isinstance(outcome, str) else json.dumps(
            outcome,
            ensure_ascii=False,
        )
        return SimpleNamespace(
            content=content,
            response_id=f"response-{len(self.purposes)}",
            finish_reason="stop",
        )


def _complete_turn(manager: MemoryManager, user_text: str = "临时讨论") -> str:
    turn = manager.begin_turn(intent="general_chat", user_text=user_text)
    manager.complete_turn(turn.id, assistant_text="assistant response")
    return turn.id


def _valid_empty_extraction() -> dict[str, Any]:
    return {
        "thread_compaction": "当前 thread 的非空滚动摘要",
        "candidates": [],
    }


@pytest.mark.parametrize(
    ("content", "path", "rule", "observed_type"),
    (
        ("", "extraction", "valid_json_object", "string"),
        ("null", "extraction", "object_required", "null"),
        ("[]", "extraction", "object_required", "array"),
        (
            '{"thread_compaction":null,"candidates":[]}',
            "thread_compaction",
            "non_empty_string",
            "null",
        ),
        (
            '{"thread_compaction":"   ","candidates":[]}',
            "thread_compaction",
            "non_empty_string",
            "string",
        ),
    ),
)
def test_extraction_contract_reports_safe_typed_violation(
    content: str,
    path: str,
    rule: str,
    observed_type: str,
) -> None:
    with pytest.raises(MemoryOutputContractError) as raised:
        parse_extraction_response(content)

    violation = raised.value.violation
    assert violation.path == path
    assert violation.rule == rule
    assert violation.observed_type == observed_type
    if content:
        assert content not in str(raised.value)


def test_extraction_contract_accepts_empty_candidates_with_nonempty_compaction() -> None:
    result = parse_extraction_response(json.dumps(_valid_empty_extraction()))

    assert result.thread_compaction == "当前 thread 的非空滚动摘要"
    assert result.candidates == ()


def test_memory_request_uses_standard_json_mode_without_hidden_retry(
    mock_openai_sdk: MagicMock,
) -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        stream_dialect="bailian-dsml",
    )
    sdk = mock_openai_sdk
    sdk.with_options.return_value = sdk
    sdk.chat.completions.create.side_effect = RuntimeError(
        "unsupported extra_body with RAW provider body"
    )
    client.client = sdk

    with pytest.raises(RuntimeError):
        client.chat(
            [{"role": "user", "content": "memory input"}],
            purpose=LLMCallPurpose.MEMORY_EXTRACTION,
        )

    sdk.with_options.assert_called_once_with(max_retries=0)
    sdk.chat.completions.create.assert_called_once()
    kwargs = sdk.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["max_tokens"] == 4096
    assert "extra_body" not in kwargs
    assert "reasoning_effort" not in kwargs
    assert client.diagnostics[-1].error == "RuntimeError"


def test_extraction_repair_uses_same_expanded_output_budget() -> None:
    from autopatch_j.llm.options import resolve_request_options

    primary = resolve_request_options(LLMCallPurpose.MEMORY_EXTRACTION)
    repair = resolve_request_options(LLMCallPurpose.MEMORY_EXTRACTION_REPAIR)

    assert primary.max_tokens == repair.max_tokens == 4096


def test_consolidation_repair_uses_same_expanded_output_budget() -> None:
    from autopatch_j.llm.options import resolve_request_options

    primary = resolve_request_options(LLMCallPurpose.MEMORY_CONSOLIDATION)
    repair = resolve_request_options(LLMCallPurpose.MEMORY_CONSOLIDATION_REPAIR)

    assert primary.max_tokens == repair.max_tokens == 4096


def test_memory_response_forces_standard_parser(
    mock_openai_sdk: MagicMock,
) -> None:
    client = LLMClient(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        stream_dialect="bailian-dsml",
    )
    sdk = mock_openai_sdk
    sdk.with_options.return_value = sdk
    sdk.chat.completions.create.return_value = SimpleNamespace(choices=[])
    client.client = sdk
    client.response_parser.parse_non_stream_response = MagicMock(
        side_effect=AssertionError("provider dialect must not parse Memory")
    )
    client.standard_response_parser.parse_non_stream_response = MagicMock(
        return_value=LLMResponse(
            content=json.dumps(_valid_empty_extraction()),
            response_id="response-safe",
            finish_reason="stop",
        )
    )

    response = client.chat(
        [{"role": "user", "content": "memory input"}],
        purpose=LLMCallPurpose.MEMORY_EXTRACTION,
    )

    assert response.response_id == "response-safe"
    client.standard_response_parser.parse_non_stream_response.assert_called_once()
    client.response_parser.parse_non_stream_response.assert_not_called()


def test_non_stream_parser_captures_bounded_response_metadata() -> None:
    parser = LLMResponseParser(StandardDialect)
    response = SimpleNamespace(
        id="response-" + "x" * 200,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="{}",
                    reasoning_content=None,
                    tool_calls=None,
                ),
            )
        ],
    )

    parsed = parser.parse_non_stream_response(response)

    assert parsed.response_id is not None
    assert len(parsed.response_id) == 128
    assert parsed.finish_reason == "stop"


def test_invalid_primary_is_repaired_once_and_committed(tmp_path: Path) -> None:
    llm = SequenceMemoryLLM(
        [
            {"thread_compaction": None, "candidates": []},
            _valid_empty_extraction(),
        ]
    )
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=llm)
    _complete_turn(manager)

    result = manager.flush_once("repair-success")

    assert result.failed == 0
    assert llm.purposes == [
        LLMCallPurpose.MEMORY_EXTRACTION,
        LLMCallPurpose.MEMORY_EXTRACTION_REPAIR,
    ]
    assert manager.active_thread_checkpoint(max_tokens=2_000)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT status, attempt_count, llm_call_count
            FROM memory_jobs WHERE kind = 'extraction'
            """
        ).fetchone()
    assert row == ("succeeded_no_output", 1, 2)


def test_invalid_repair_fails_terminal_without_partial_commit(tmp_path: Path) -> None:
    llm = SequenceMemoryLLM(
        [
            {"thread_compaction": None, "candidates": []},
            {"thread_compaction": " ", "candidates": []},
        ]
    )
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=llm)
    _complete_turn(manager)

    result = manager.flush_once("repair-failure")
    status = manager.status()

    assert result.failed == 1
    assert status.failed_jobs == 1
    assert status.pending_jobs == status.retry_wait_jobs == 0
    assert status.last_failure is not None
    assert status.last_failure.category == "output_contract"
    assert status.last_failure.stage == "repair"
    assert status.last_failure.llm_call_count == 2
    assert manager.active_thread_checkpoint(max_tokens=2_000) == ""
    assert manager.list_items() == []
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT status, llm_call_count FROM memory_jobs"
        ).fetchone()
        candidate_count = connection.execute(
            "SELECT COUNT(*) FROM memory_candidates"
        ).fetchone()[0]
    assert row == ("failed", 2)
    assert candidate_count == 0
    assert manager.flush_once("must-not-retry").processed == 0
    assert len(llm.purposes) == 2


def test_transient_primary_retries_once_then_uses_final_repair_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autopatch_j.core.memory import store as memory_store_module

    monkeypatch.setattr(memory_store_module, "JOB_RETRY_DELAY_SECONDS", 0)
    llm = SequenceMemoryLLM(
        [
            TimeoutError("RAW timeout"),
            {"thread_compaction": None, "candidates": []},
            _valid_empty_extraction(),
        ]
    )
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=llm)
    _complete_turn(manager)

    first = manager.flush_once("transient-primary")
    second = manager.flush_once("retry-and-repair")

    assert first.failed == 0
    assert first.pending == 1
    assert second.failed == 0
    assert len(llm.purposes) == 3
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT status, attempt_count, llm_call_count
            FROM memory_jobs WHERE kind = 'extraction'
            """
        ).fetchone()
    assert row == ("succeeded_no_output", 2, 3)
    assert manager.flush_once("budget-exhausted").processed == 0


def test_repair_capacity_failure_does_not_send_second_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = SequenceMemoryLLM(
        [{"thread_compaction": None, "candidates": []}]
    )
    manager = MemoryManager(db_path=tmp_path / "memory.db", llm=llm)
    _complete_turn(manager)
    pipeline = manager._pipeline
    assert pipeline is not None
    original_fit = pipeline._fit_extraction_payload

    def fail_only_repair(
        payload: dict[str, Any],
        *,
        system_prompt: str = "",
        violation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if violation is not None:
            from autopatch_j.llm.context_window import ContextCapacityError

            raise ContextCapacityError("repair envelope too large")
        return original_fit(
            payload,
            system_prompt=system_prompt,
            violation=violation,
        )

    monkeypatch.setattr(pipeline, "_fit_extraction_payload", fail_only_repair)

    result = manager.flush_once("repair-capacity")

    assert result.failed == 1
    assert len(llm.purposes) == 1
    assert manager.status().last_failure is not None
    assert manager.status().last_failure.category == "capacity"


def test_failed_consolidation_suppresses_pending_candidates(
    tmp_path: Path,
) -> None:
    def extraction(
        messages: list[dict[str, Any]],
        purpose: LLMCallPurpose,
    ) -> dict[str, Any]:
        assert purpose is LLMCallPurpose.MEMORY_EXTRACTION
        payload = json.loads(messages[-1]["content"])
        turn = payload["turns"][0]
        return {
            "thread_compaction": "已记录明确项目决定",
            "candidates": [
                {
                    "kind": "project_decision",
                    "subject": "storage",
                    "statement": "项目最终决定使用本地 SQLite。",
                    "content": "项目最终决定使用本地 SQLite。",
                    "strength": "hard",
                    "origin": "explicit",
                    "recall_mode": "always",
                    "applies_to_paths": [],
                    "aliases": ["local SQLite storage"],
                    "keywords": ["SQLite"],
                    "sources": [
                        {
                            "turn_id": turn["turn_id"],
                            "role": "user",
                            "quote": turn["user"],
                        }
                    ],
                }
            ],
        }

    llm = SequenceMemoryLLM(
        [
            extraction,
            {"operations": None},
            {"operations": []},
        ]
    )
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=llm)
    _complete_turn(manager, "项目最终决定使用本地 SQLite。")

    result = manager.flush_once("failed-consolidation")

    assert result.failed == 1
    assert manager.list_items() == []
    with sqlite3.connect(db_path) as connection:
        candidate_status = connection.execute(
            "SELECT status FROM memory_candidates"
        ).fetchone()[0]
        job = connection.execute(
            """
            SELECT status, llm_call_count FROM memory_jobs
            WHERE kind = 'consolidation'
            """
        ).fetchone()
    assert candidate_status == "suppressed"
    assert job == ("failed", 2)


def test_payload_relative_consolidation_semantics_trigger_repair(
    tmp_path: Path,
) -> None:
    def extraction(
        messages: list[dict[str, Any]],
        purpose: LLMCallPurpose,
    ) -> dict[str, Any]:
        assert purpose is LLMCallPurpose.MEMORY_EXTRACTION
        turn = json.loads(messages[-1]["content"])["turns"][0]
        return {
            "thread_compaction": "向量库方案仍在讨论，尚未形成项目决定。",
            "candidates": [
                {
                    "kind": "discussion_context",
                    "subject": "vector database evaluation",
                    "statement": "用户希望继续讨论是否使用向量库，当前尚未决定。",
                    "content": "是否使用向量库仍待后续评估。",
                    "strength": "soft",
                    "origin": "explicit",
                    "recall_mode": "on_match",
                    "applies_to_paths": [],
                    "aliases": ["vector database evaluation"],
                    "keywords": ["vector database"],
                    "sources": [
                        {
                            "turn_id": turn["turn_id"],
                            "role": "user",
                            "quote": turn["user"],
                        }
                    ],
                }
            ],
        }

    def consolidation(
        messages: list[dict[str, Any]],
        purpose: LLMCallPurpose,
    ) -> dict[str, Any]:
        request = json.loads(messages[-1]["content"])
        payload = (
            request["business_input"]
            if purpose is LLMCallPurpose.MEMORY_CONSOLIDATION_REPAIR
            else request
        )
        candidate = payload["candidates"][0]
        if purpose is LLMCallPurpose.MEMORY_CONSOLIDATION_REPAIR:
            assert request["violation"]["rule"] == "discussion_kind_required"
        return {
            "operations": [
                {
                    "operation": "create",
                    "candidate_ids": [candidate["id"]],
                    "target_id": None,
                    "kind": (
                        "discussion_context"
                        if purpose is LLMCallPurpose.MEMORY_CONSOLIDATION_REPAIR
                        else "project_decision"
                    ),
                    "subject": candidate["subject"],
                    "statement": candidate["statement"],
                    "content": candidate["content"],
                    "strength": candidate["strength"],
                    "origin": candidate["origin"],
                    "recall_mode": candidate["recall_mode"],
                    "applies_to_paths": candidate["applies_to_paths"],
                    "aliases": candidate["aliases"],
                    "keywords": candidate["keywords"],
                }
            ]
        }

    llm = SequenceMemoryLLM([extraction, consolidation, consolidation])
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=llm)
    _complete_turn(manager, "先讨论是否使用向量库，不要现在做决定。")

    result = manager.flush_once("semantic-consolidation-repair")

    assert result.failed == 0
    assert llm.purposes == [
        LLMCallPurpose.MEMORY_EXTRACTION,
        LLMCallPurpose.MEMORY_CONSOLIDATION,
        LLMCallPurpose.MEMORY_CONSOLIDATION_REPAIR,
    ]
    assert [item.kind for item in manager.list_items()] == ["discussion_context"]
    with sqlite3.connect(db_path) as connection:
        job = connection.execute(
            """
            SELECT status, llm_call_count FROM memory_jobs
            WHERE kind = 'consolidation'
            """
        ).fetchone()
    assert job == ("succeeded", 2)


def test_store_renews_lease_and_enforces_homogeneous_claims(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    first_turn = store.begin_turn("general_chat", "one", "turn-owner")
    store.complete_turn(first_turn.id, "assistant one", "turn-owner")
    first = store.claim_extraction_batch("worker", force=True)
    assert first is not None
    reserved = store.reserve_llm_call(first)
    store.defer_job_retry(reserved, '{"category":"provider"}')

    second_turn = store.begin_turn("general_chat", "two", "turn-owner")
    store.complete_turn(second_turn.id, "assistant two", "turn-owner")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE memory_jobs SET next_retry_at = 0 WHERE id = ?",
            (first.jobs[0].id,),
        )
        connection.commit()

    retry = store.claim_extraction_batch("worker", force=True)
    assert retry is not None
    assert [job.id for job in retry.jobs] == [first.jobs[0].id]
    renewed = store.renew_job_lease(retry)
    assert renewed.jobs[0].lease_expires_at is not None
    store.complete_extraction(
        renewed,
        ExtractionResult("retry completed", ()),
        source_inputs=(),
    )

    fresh = store.claim_extraction_batch("worker", force=True)
    assert fresh is not None
    assert [job.id for job in fresh.jobs] != [first.jobs[0].id]


def test_second_lease_expiry_becomes_terminal_failed(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    turn = store.begin_turn("general_chat", "one", "turn-owner")
    store.complete_turn(turn.id, "assistant one", "turn-owner")
    first = store.claim_extraction_batch("worker-one", force=True)
    assert first is not None
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE memory_jobs SET lease_expires_at = 0 WHERE id = ?",
            (first.jobs[0].id,),
        )
        connection.commit()

    assert store.claim_extraction_batch("worker-two", force=True) is None
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE memory_jobs SET next_retry_at = 0 WHERE id = ?",
            (first.jobs[0].id,),
        )
        connection.commit()
    second = store.claim_extraction_batch("worker-two", force=True)
    assert second is not None
    assert second.jobs[0].attempt_count == 2
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE memory_jobs SET lease_expires_at = 0 WHERE id = ?",
            (first.jobs[0].id,),
        )
        connection.commit()

    assert store.claim_extraction_batch("worker-three", force=True) is None
    status = store.status()
    assert status.failed_jobs == 1
    assert status.pending_jobs == status.leased_jobs == status.retry_wait_jobs == 0


def test_transient_classifier_is_explicit_and_bounded() -> None:
    pipeline = MemoryPipeline.__new__(MemoryPipeline)

    assert pipeline._is_transient_primary_error(TimeoutError("RAW")) is True
    assert pipeline._is_transient_primary_error(
        sqlite3.OperationalError("database is locked")
    ) is True
    assert pipeline._is_transient_primary_error(RuntimeError("unknown")) is False
