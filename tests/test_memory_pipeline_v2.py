from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autopatch_j.core.memory import (
    MemoryItemSummary,
    MemoryManager,
    MemoryStorageError,
)
from autopatch_j.core.memory.models import (
    CandidateSource,
    ExtractionCandidateInput,
    ExtractionResult,
)
from autopatch_j.core.memory.constants import MAX_JOB_ERROR_CHARS
from autopatch_j.llm.diagnostics import MAX_RAW_LLM_ERROR_CHARS


class RecordingMemoryLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    def chat(self, messages, tools=None, purpose=None):
        del tools
        payload = json.loads(messages[-1]["content"])
        self.calls.append((purpose, payload))
        if "turns" in payload:
            turn = payload["turns"][-1]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "thread_compaction": "用户在讨论回答风格",
                        "candidates": [
                            {
                                "kind": "user_preference",
                                "title": "简洁回答",
                                "content": "用户明确偏好简洁回答",
                                "aliases": ["concise answers", "短回答"],
                                "sources": [
                                    {
                                        "turn_id": turn["turn_id"],
                                        "role": "user",
                                        "quote": "偏好简洁回答",
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )
        candidate = payload["candidates"][0]
        return SimpleNamespace(
            content=json.dumps(
                {
                    "operations": [
                        {
                            "operation": "create",
                            "candidate_ids": [candidate["id"]],
                            "target_id": None,
                            "title": "简洁回答",
                            "content": "用户明确偏好简洁回答",
                            "synopsis": "回答保持简洁",
                            "aliases": ["concise answers", "短回答"],
                            "keywords": ["回答风格", "response style"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )


class EchoingFailureLLM:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, purpose=None):
        del tools, purpose
        self.calls += 1
        raise RuntimeError(f"provider echoed RAW prompt: {messages[-1]['content']}")


class LongFailureLLM:
    def chat(self, messages, tools=None, purpose=None):
        del messages, tools, purpose
        raise RuntimeError("provider RAW body: " + "x" * 25_000)


class EmptyCandidateLLM:
    def chat(self, messages, tools=None, purpose=None):
        del messages, tools, purpose
        return SimpleNamespace(
            content=json.dumps(
                {
                    "thread_compaction": "本轮没有长期记忆候选",
                    "candidates": [],
                },
                ensure_ascii=False,
            )
        )


class InjectingMemoryLLM(RecordingMemoryLLM):
    def __init__(self, other_manager: MemoryManager) -> None:
        super().__init__()
        self.other_manager = other_manager
        self.injected_turn_id: str | None = None

    def chat(self, messages, tools=None, purpose=None):
        payload = json.loads(messages[-1]["content"])
        if "turns" in payload and self.injected_turn_id is None:
            turn = self.other_manager.begin_turn(
                intent="general_chat",
                user_text="我明确偏好简洁回答，这是并发新增 turn",
            )
            self.other_manager.complete_turn(turn.id, assistant_text="并发 turn 已完成")
            self.injected_turn_id = turn.id
        return super().chat(messages, tools=tools, purpose=purpose)


class TransientPipeline:
    def __init__(self) -> None:
        self.calls = 0
        self.recovered = Event()

    def process_one(self, *, force=False, thread_id=None):
        del force, thread_id
        self.calls += 1
        if self.calls == 1:
            raise MemoryStorageError("database is temporarily locked")
        self.recovered.set()
        return None


def _seed_pending_consolidation(manager: MemoryManager) -> str:
    turn = manager.begin_turn(
        intent="general_chat",
        user_text="以后默认采用详细回答",
    )
    manager.complete_turn(turn.id, assistant_text="收到")
    extraction = manager.store.claim_extraction_batch("seed-worker", force=True)
    assert extraction is not None
    candidate_ids = manager.store.complete_extraction(
        extraction,
        ExtractionResult(
            thread_compaction="用户在讨论回答风格",
            candidates=(
                ExtractionCandidateInput(
                    kind="user_preference",
                    title="详细回答",
                    content="用户明确偏好详细回答",
                    aliases=("detailed answers",),
                    sources=(
                        CandidateSource(
                            turn_id=turn.id,
                            role="user",
                            quote="默认采用详细回答",
                        ),
                    ),
                ),
            ),
        ),
    )
    job_ids = manager.store.consolidation_job_ids_for_candidates(candidate_ids)
    assert len(job_ids) == 1
    return job_ids[0]


def test_end_to_end_extraction_consolidation_search_read_and_forget(
    tmp_path: Path,
) -> None:
    llm = RecordingMemoryLLM()
    manager = MemoryManager(db_path=tmp_path / "memory.db", llm=llm)
    turn = manager.begin_turn(
        intent="general_chat",
        user_text="我明确偏好简洁回答",
    )
    manager.complete_turn(turn.id, assistant_text="以后会保持简洁")

    result = manager.flush_once("test")
    hits_cn = manager.search("短回答")
    hits_en = manager.search("concise answers")
    detail = manager.read(hits_cn[0].id)
    routing = manager.build_routing_context("general_chat")
    forgotten = manager.forget(detail.id)

    assert result.processed == 2
    assert result.succeeded == 2
    assert hits_cn[0].id == hits_en[0].id
    assert detail.sources[0].quote == "偏好简洁回答"
    assert detail.access_count == 1
    assert "明确偏好" in routing
    assert forgotten.raw_turns_retained
    assert manager.search("简洁回答") == []
    assert manager.status().turn_count == 1


def test_flush_uses_initial_job_snapshot_but_completes_extraction_child_chain(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    second_manager = MemoryManager(db_path=db_path, worker_id="second-manager")
    llm = InjectingMemoryLLM(second_manager)
    manager = MemoryManager(
        db_path=db_path,
        llm=llm,
        worker_id="flush-manager",
    )
    initial = manager.begin_turn(
        intent="general_chat",
        user_text="我明确偏好简洁回答，这是初始 turn",
    )
    manager.complete_turn(initial.id, assistant_text="初始 turn 已完成")

    first = manager.flush_once("snapshot")

    assert first.processed == 2
    assert first.succeeded == 2
    assert first.pending == 1
    assert llm.injected_turn_id is not None
    assert [turn["turn_id"] for turn in llm.calls[0][1]["turns"]] == [initial.id]

    second = manager.flush_once("next-snapshot")

    assert second.processed == 2
    assert second.succeeded == 2
    assert second.pending == 0
    second_extraction = next(
        payload
        for _, payload in llm.calls[2:]
        if "turns" in payload
    )
    assert [turn["turn_id"] for turn in second_extraction["turns"]] == [
        llm.injected_turn_id
    ]


def test_flush_snapshot_child_is_not_blocked_by_non_allowlisted_consolidation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=RecordingMemoryLLM())
    old_thread = manager.ensure_active_thread()
    blocker_job_id = _seed_pending_consolidation(manager)
    current_thread = manager.start_new_thread(expected_thread_id=old_thread.id)
    turn = manager.begin_turn(
        intent="general_chat",
        user_text="我明确偏好简洁回答",
    )
    manager.complete_turn(turn.id, assistant_text="当前 thread turn 已完成")

    result = manager.flush_once("snapshot", thread_id=current_thread.id)

    assert result.processed == 2
    assert result.succeeded == 2
    assert result.pending == 1
    with sqlite3.connect(db_path) as connection:
        blocker_status = connection.execute(
            "SELECT status FROM memory_jobs WHERE id = ?", (blocker_job_id,)
        ).fetchone()[0]
        current_statuses = connection.execute(
            """
            SELECT kind, status FROM memory_jobs
            WHERE thread_id = ? ORDER BY created_at, id
            """,
            (current_thread.id,),
        ).fetchall()
    assert blocker_status == "pending"
    assert current_statuses == [
        ("extraction", "succeeded"),
        ("consolidation", "succeeded"),
    ]


def test_new_thread_hides_old_discussion_but_keeps_repo_memory(tmp_path: Path) -> None:
    llm = RecordingMemoryLLM()
    manager = MemoryManager(db_path=tmp_path / "memory.db", llm=llm)
    active = manager.ensure_active_thread()
    turn = manager.begin_turn(intent="general_chat", user_text="我明确偏好简洁回答")
    manager.complete_turn(turn.id, assistant_text="收到")
    manager.flush_once("test")

    manager.start_new_thread(expected_thread_id=active.id)

    assert manager.search("简洁回答")
    assert manager.build_thread_history() == []


def test_degraded_memory_is_not_silently_projected_as_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    db_path.write_bytes(b"not sqlite")
    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    with pytest.raises(MemoryStorageError):
        manager.build_thread_history()
    with pytest.raises(MemoryStorageError):
        manager.build_routing_context("general_chat")
    assert manager.build_routing_context("code_audit") == ""


def test_routing_budget_keeps_all_indexes_before_compaction(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = MagicMock()
    store.active_thread_compaction.return_value = "c" * 4_000

    def summaries(kind: str) -> list[MemoryItemSummary]:
        return [
            MemoryItemSummary(
                id=f"{kind}-{index}",
                kind=kind,
                title="标题" * 80,
                synopsis="摘要" * 120,
                updated_at="2026-07-13T00:00:00+00:00",
            )
            for index in range(5)
        ]

    preferences = summaries("user_preference")
    decisions = summaries("project_decision")
    discussions = summaries("discussion_context")
    store.active_items_for_routing.return_value = (
        preferences,
        decisions,
        discussions,
    )
    manager._store = store

    context = manager.build_routing_context("general_chat")

    assert len(context) <= 4_000
    assert preferences[-1].id in context
    assert decisions[-1].id in context
    assert discussions[-1].id in context
    assert "### 当前 thread 摘要" in context


def test_extraction_payload_preserves_complete_raw_turn(tmp_path: Path) -> None:
    llm = RecordingMemoryLLM()
    manager = MemoryManager(db_path=tmp_path / "memory.db", llm=llm)
    raw_user = "我明确偏好简洁回答\n" + "x" * 1_000_000
    raw_assistant = "完整回应\n" + "y" * 100_000
    turn = manager.begin_turn(intent="general_chat", user_text=raw_user)
    manager.complete_turn(turn.id, assistant_text=raw_assistant)

    result = manager.flush_once("raw-contract")

    assert result.failed == 0
    extraction_payload = llm.calls[0][1]
    assert extraction_payload["turns"][0]["user"] == raw_user
    assert extraction_payload["turns"][0]["assistant"] == raw_assistant


def test_provider_echo_is_persisted_in_job_error(tmp_path: Path) -> None:
    llm = EchoingFailureLLM()
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=llm)
    raw_secret = "raw-provider-sentinel"
    turn = manager.begin_turn(intent="general_chat", user_text=raw_secret)
    manager.complete_turn(turn.id, assistant_text="assistant raw")

    result = manager.flush_once("provider-failure")
    status = manager.status()

    assert result.failed == 1
    assert status.retry_wait_jobs == 1
    assert raw_secret in status.last_error
    assert status.last_error.startswith(
        "Memory extraction failed: RuntimeError: provider echoed RAW prompt:"
    )
    with sqlite3.connect(db_path) as connection:
        job_error = connection.execute(
            "SELECT last_error FROM memory_jobs WHERE kind = 'extraction'"
        ).fetchone()[0]
    assert job_error == status.last_error

    exported = manager.export(tmp_path / "exports")
    snapshot = json.loads(exported.path.read_text(encoding="utf-8"))
    assert snapshot["memory_meta"][0]["last_error"] == status.last_error
    assert snapshot["memory_jobs"][0]["last_error"] == status.last_error


def test_provider_error_is_bounded_and_job_meta_store_identical_text(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=LongFailureLLM())
    turn = manager.begin_turn(intent="general_chat", user_text="触发超长 provider 错误")
    manager.complete_turn(turn.id, assistant_text="assistant raw")

    result = manager.flush_once("long-provider-failure")
    status = manager.status()

    assert result.failed == 1
    assert MAX_JOB_ERROR_CHARS == MAX_RAW_LLM_ERROR_CHARS
    assert len(status.last_error) == MAX_JOB_ERROR_CHARS
    assert status.last_error.startswith(
        "Memory extraction failed: RuntimeError: provider RAW body:"
    )
    assert status.last_error.endswith(
        f"...[truncated to {MAX_JOB_ERROR_CHARS} characters]"
    )
    with sqlite3.connect(db_path) as connection:
        job_error = connection.execute(
            "SELECT last_error FROM memory_jobs WHERE kind = 'extraction'"
        ).fetchone()[0]
        meta_error = connection.execute(
            "SELECT last_error FROM memory_meta WHERE id = 1"
        ).fetchone()[0]
    assert job_error == meta_error == status.last_error


def test_extraction_without_candidates_succeeds_once_without_child_job(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=EmptyCandidateLLM())
    turn = manager.begin_turn(intent="general_chat", user_text="这是一次临时问答")
    manager.complete_turn(turn.id, assistant_text="这是临时回答")

    result = manager.flush_once("empty-candidates")

    assert result.processed == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.pending == 0
    with sqlite3.connect(db_path) as connection:
        jobs = connection.execute(
            "SELECT kind, status FROM memory_jobs ORDER BY created_at, id"
        ).fetchall()
    assert jobs == [("extraction", "succeeded_no_output")]
    assert manager.store.claim_extraction_batch("other-worker", force=True) is None


def test_flush_attempts_a_failed_job_at_most_once_per_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autopatch_j.core.memory import store as memory_store_module

    monkeypatch.setattr(memory_store_module, "RETRY_BACKOFF_SECONDS", (0,))
    llm = EchoingFailureLLM()
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, llm=llm)
    turn = manager.begin_turn(intent="general_chat", user_text="会失败的 turn")
    manager.complete_turn(turn.id, assistant_text="会失败的回答")

    first = manager.flush_once("first-failure")
    with sqlite3.connect(db_path) as connection:
        first_attempts = connection.execute(
            "SELECT attempt_count FROM memory_jobs WHERE kind = 'extraction'"
        ).fetchone()[0]

    assert first.processed == 1
    assert first.failed == 1
    assert first.pending == 1
    assert first_attempts == 1
    assert llm.calls == 1

    second = manager.flush_once("second-failure")

    assert second.processed == 1
    assert second.failed == 1
    assert llm.calls == 2


def test_worker_survives_transient_pipeline_failure(tmp_path: Path) -> None:
    manager = MemoryManager(
        db_path=tmp_path / "memory.db", llm=RecordingMemoryLLM()
    )
    pipeline = TransientPipeline()
    manager._pipeline = pipeline

    manager.start()
    try:
        assert pipeline.recovered.wait(timeout=3)
        assert manager._thread is not None
        assert manager._thread.is_alive()
    finally:
        manager.close()
