from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from autopatch_j.core.memory import (
    MemoryManager,
    RecallQuery,
    MemoryStorageError,
)
from autopatch_j.core.memory.models import (
    CandidateSource,
    ExtractionCandidateInput,
    ExtractionResult,
)
from autopatch_j.core.memory.constants import MAX_JOB_ERROR_CHARS
from autopatch_j.core.memory.pipeline import PipelineStepResult
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
                                "subject": "answer brevity",
                                "statement": "回答默认保持简洁",
                                "content": "用户明确偏好简洁回答",
                                "strength": "hard",
                                "origin": "explicit",
                                "recall_mode": "always",
                                "applies_to_paths": [],
                                "aliases": ["concise answers", "短回答"],
                                "keywords": ["回答风格", "response style"],
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
        target_id = (
            payload["active_items"][0]["id"]
            if payload.get("active_items")
            else None
        )
        return SimpleNamespace(
            content=json.dumps(
                {
                    "operations": [
                        {
                            "operation": "revise" if target_id else "create",
                            "candidate_ids": [candidate["id"]],
                            "target_id": target_id,
                            "kind": "user_preference",
                            "subject": "answer brevity",
                            "statement": "回答默认保持简洁",
                            "content": "用户明确偏好简洁回答",
                            "strength": "hard",
                            "origin": "explicit",
                            "recall_mode": "always",
                            "applies_to_paths": [],
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


class BlockingEmptyMemoryLLM:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def chat(self, messages, tools=None, purpose=None):
        del messages, tools, purpose
        self.started.set()
        assert self.release.wait(timeout=3)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "thread_compaction": "本轮无长期候选",
                    "candidates": [],
                },
                ensure_ascii=False,
            )
        )


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 19, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class AdvancingWatermarkPipeline:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.started = Event()
        self.release = Event()
        self.finished = Event()
        self.calls = 0

    def process_one(
        self,
        *,
        force: bool = False,
        thread_id: str | None = None,
        allowed_job_ids=None,
    ) -> PipelineStepResult:
        del force, thread_id
        assert allowed_job_ids
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            assert self.release.wait(timeout=3)
            self.clock.advance(70)
            return PipelineStepResult(
                processed=1,
                succeeded=1,
                failed=0,
                processed_job_ids=(next(iter(allowed_job_ids)),),
                job_kind="extraction",
                spawned_job_ids=("child-job",),
            )
        self.clock.advance(70)
        self.finished.set()
        return PipelineStepResult(
            processed=1,
            succeeded=1,
            failed=0,
            processed_job_ids=("child-job",),
            job_kind="consolidation",
        )


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
                    subject="answer detail",
                    statement="回答默认提供详细说明",
                    content="用户明确偏好详细回答",
                    strength="hard",
                    origin="explicit",
                    recall_mode="always",
                    applies_to_paths=(),
                    aliases=("detailed answers",),
                    keywords=("回答风格",),
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
    forgotten = manager.forget(detail.id)

    assert result.processed == 2
    assert result.succeeded == 2
    assert hits_cn[0].id == hits_en[0].id
    assert detail.sources[0].quote == "偏好简洁回答"
    assert detail.access_count == 1
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

    assert manager.search("短回答")
    assert manager.build_thread_history() == []


def test_degraded_memory_is_not_silently_projected_as_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    db_path.write_bytes(b"not sqlite")
    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    with pytest.raises(MemoryStorageError):
        manager.build_thread_history()
    with pytest.raises(MemoryStorageError):
        manager.build_memory_map(
            RecallQuery(
                intent="general_chat",
                thread_id="unavailable",
                user_text="anything",
            ),
            manager.build_recall_policy(
                intent="general_chat",
                thread_id="unavailable",
                durable_token_budget=100,
                map_token_budget=50,
            ),
        )


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


def test_thread_watermark_returns_on_deadline_and_finishes_in_background(
    tmp_path: Path,
) -> None:
    llm = BlockingEmptyMemoryLLM()
    manager = MemoryManager(db_path=tmp_path / "memory.db", llm=llm)
    thread = manager.ensure_active_thread()
    turn = manager.begin_turn(intent="general_chat", user_text="待异步处理")
    manager.complete_turn(turn.id, assistant_text="收到")

    result = manager.flush_thread_watermark(
        reason="new",
        thread_id=thread.id,
        wait_seconds=0.01,
    )

    assert llm.started.is_set()
    assert result.pending == 1
    assert "后台继续" in result.errors[0]

    manager.start_new_thread(expected_thread_id=thread.id)
    llm.release.set()
    deadline = monotonic() + 3
    status = manager.status()
    while (
        status.pending_jobs + status.leased_jobs + status.retry_wait_jobs
        and monotonic() < deadline
    ):
        sleep(0.01)
        status = manager.status()

    assert status.pending_jobs + status.leased_jobs + status.retry_wait_jobs == 0
    assert manager.ensure_active_thread().id != thread.id


def test_background_watermark_heartbeats_new_thread_turn(tmp_path: Path) -> None:
    clock = FakeClock()
    manager = MemoryManager(
        db_path=tmp_path / "memory.db",
        llm=RecordingMemoryLLM(),
        clock=clock,
    )
    old_thread = manager.ensure_active_thread()
    old_turn = manager.begin_turn(intent="general_chat", user_text="旧 thread turn")
    manager.complete_turn(old_turn.id, assistant_text="收到")
    pipeline = AdvancingWatermarkPipeline(clock)
    manager._pipeline = pipeline

    result = manager.flush_thread_watermark(
        reason="new",
        thread_id=old_thread.id,
        wait_seconds=0.01,
    )

    assert pipeline.started.is_set()
    assert "后台继续" in result.errors[0]
    manager.start_new_thread(expected_thread_id=old_thread.id)
    new_turn = manager.begin_turn(intent="general_chat", user_text="新 thread turn")
    pipeline.release.set()
    assert pipeline.finished.wait(timeout=3)

    completed = manager.complete_turn(new_turn.id, assistant_text="新 turn 完成")

    assert completed.state == "completed"
