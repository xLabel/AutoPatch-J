from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace
from typing import Any

import pytest

from autopatch_j.core.domain import IntentType
from autopatch_j.core.memory import MemoryLeaseError, MemoryManager
from autopatch_j.core.memory.constants import (
    MAX_CONTENT_TERMS,
    MAX_HISTORY_CHARS,
    MAX_RELATED_ITEMS,
    MAX_SEARCH_QUERY_CHARS,
)
from autopatch_j.core.memory.errors import (
    MemoryContractError,
    MemoryNotFoundError,
    MemorySchemaError,
    MemoryStorageError,
)
from autopatch_j.core.memory.models import (
    CandidateSource,
    ConsolidationOperation,
    ConsolidationResult,
    ExtractionCandidateInput,
    ExtractionResult,
    MemoryCandidate,
)
from autopatch_j.core.memory.store import MemoryStore


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 12, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class EmptyExtractionLLM:
    def chat(self, messages, tools=None, purpose=None):
        del tools, purpose
        payload = json.loads(messages[-1]["content"])
        last = payload["turns"][-1]["user"]
        return SimpleNamespace(
            content=json.dumps(
                {"thread_compaction": f"讨论：{last}", "candidates": []},
                ensure_ascii=False,
            )
        )


def _complete_turn(
    store: MemoryStore,
    user: str,
    assistant: str,
    owner: str = "test-owner",
) -> str:
    handle = store.begin_turn("general_chat", user, owner)
    store.complete_turn(handle.id, assistant, owner)
    return handle.id


def _seed_active_item(
    store: MemoryStore,
    *,
    kind: str = "user_preference",
    title: str = "简洁回答",
    content: str = "用户明确偏好简洁回答",
    aliases: tuple[str, ...] = ("concise answers",),
    keywords: tuple[str, ...] = ("回答风格", "response style"),
) -> str:
    if kind == "user_preference":
        user_text = f"以后默认按 {title} 处理，具体内容是 {content}"
    elif kind == "project_decision":
        user_text = f"项目决定采用 {title}，具体内容是 {content}"
    else:
        user_text = f"我们正在讨论 {title}，具体内容是 {content}"
    turn_id = _complete_turn(store, user_text, "收到")
    extraction = store.claim_extraction_batch("extractor", force=True)
    assert extraction is not None
    candidate_ids = store.complete_extraction(
        extraction,
        ExtractionResult(
            thread_compaction="用户正在讨论回答风格",
            candidates=(
                ExtractionCandidateInput(
                    kind=kind,
                    title=title,
                    content=content,
                    aliases=aliases,
                    sources=(CandidateSource(turn_id, "user", user_text),),
                ),
            ),
        ),
    )
    consolidation = store.claim_consolidation_job("consolidator", force=True)
    assert consolidation is not None
    item_ids = store.apply_consolidation(
        consolidation,
        ConsolidationResult(
            operations=(
                ConsolidationOperation(
                    operation="create",
                    candidate_ids=candidate_ids,
                    target_id=None,
                    title=title,
                    content=content,
                    synopsis=content,
                    aliases=aliases,
                    keywords=keywords,
                ),
            )
        ),
    )
    return item_ids[0]


def _extract_candidate(
    store: MemoryStore,
    *,
    title: str,
    owner: str,
    kind: str = "user_preference",
    aliases: tuple[str, ...] | None = None,
) -> str:
    if kind == "user_preference":
        user_text = f"以后默认按 {title} 处理"
        content = f"用户明确偏好 {title}"
    elif kind == "project_decision":
        user_text = f"项目决定采用 {title}"
        content = f"项目决定采用 {title}"
    else:
        user_text = f"我们正在讨论 {title}"
        content = f"正在讨论 {title}"
    turn_id = _complete_turn(
        store,
        user_text,
        "收到",
        owner=f"turn-{owner}",
    )
    batch = store.claim_extraction_batch(owner, force=True)
    assert batch is not None
    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            thread_compaction=f"正在讨论 {title}",
            candidates=(
                ExtractionCandidateInput(
                    kind=kind,
                    title=title,
                    content=content,
                    aliases=aliases if aliases is not None else (title,),
                    sources=(CandidateSource(turn_id, "user", user_text),),
                ),
            ),
        ),
    )
    return candidate_ids[0]


def test_initializes_v2_and_deletes_legacy_json(tmp_path: Path) -> None:
    state_dir = tmp_path / ".autopatch-j"
    state_dir.mkdir()
    legacy = state_dir / "memory.json"
    legacy.write_text('{"version": 1}', encoding="utf-8")

    manager = MemoryManager(db_path=state_dir / "memory.db")

    assert not legacy.exists()
    assert manager.status().schema_version == 2
    assert manager.status().active_thread_id == manager.ensure_active_thread().id
    with sqlite3.connect(state_dir / "memory.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(memory_terms)")
        }
        assert "terms_lookup" in indexes


def test_initializes_existing_blank_unversioned_database(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0] == 0

    manager = MemoryManager(db_path=db_path)

    status = manager.status()
    assert status.healthy
    assert status.schema_version == 2
    assert status.thread_count == 1


def test_concurrent_managers_initialize_blank_database_once(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    worker_count = 4
    barrier = Barrier(worker_count)

    def initialize() -> tuple[bool, str | None]:
        barrier.wait()
        manager = MemoryManager(db_path=db_path)
        status = manager.status()
        manager.close()
        return status.healthy, status.active_thread_id

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        statuses = list(executor.map(lambda _: initialize(), range(worker_count)))

    assert all(healthy for healthy, _ in statuses)
    assert len({thread_id for _, thread_id in statuses}) == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM threads WHERE status = 'active'"
        ).fetchone()[0] == 1


def test_legacy_json_delete_failure_is_visible_as_degraded_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".autopatch-j"
    state_dir.mkdir()
    legacy = state_dir / "memory.json"
    legacy.write_text('{"version": 1}', encoding="utf-8")
    original_unlink = Path.unlink

    def deny_legacy_delete(path: Path, missing_ok: bool = False) -> None:
        if path == legacy:
            raise PermissionError("read-only legacy file")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_legacy_delete)

    manager = MemoryManager(db_path=state_dir / "memory.db")
    status = manager.status()

    assert status.degraded is True
    assert status.healthy is False
    assert "无法删除旧 Memory 文件" in status.last_error
    assert legacy.exists()


def test_concurrent_managers_keep_unique_turn_sequences(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    first = MemoryManager(db_path=db_path)
    second = MemoryManager(db_path=db_path)

    def write(index: int) -> int:
        manager = first if index % 2 else second
        handle = manager.begin_turn(
            intent=IntentType.GENERAL_CHAT,
            user_text=f"user-{index}",
        )
        manager.complete_turn(handle.id, assistant_text=f"assistant-{index}")
        return handle.sequence

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(write, range(20)))

    assert sorted(sequences) == list(range(1, 21))
    assert first.status().turn_count == 20
    assert first.status().thread_count == 1


def test_turn_owner_lease_protects_live_manager_and_recovers_after_expiry(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    first = MemoryManager(
        db_path=tmp_path / "memory.db", clock=clock, worker_id="manager-a"
    )
    second = MemoryManager(
        db_path=tmp_path / "memory.db", clock=clock, worker_id="manager-b"
    )
    handle = first.begin_turn(intent="general_chat", user_text="仍在处理")

    assert second.store.recover_startup() == 0
    with pytest.raises(MemoryLeaseError):
        second.complete_turn(handle.id, assistant_text="越权结果")

    clock.advance(100)
    heartbeat = Event()
    original_heartbeat = first.store.heartbeat_open_turns

    def track_heartbeat(owner: str) -> int:
        updated = original_heartbeat(owner)
        if updated:
            heartbeat.set()
        return updated

    first.store.heartbeat_open_turns = track_heartbeat
    first.start()
    try:
        assert heartbeat.wait(timeout=2)
    finally:
        first.close()
    clock.advance(100)
    assert second.store.recover_startup() == 0

    clock.advance(21)
    assert second.store.recover_startup() == 1
    with pytest.raises(MemoryLeaseError):
        first.complete_turn(handle.id, assistant_text="过期结果")
    with sqlite3.connect(tmp_path / "memory.db") as connection:
        state = connection.execute(
            "SELECT state FROM turns WHERE id = ?", (handle.id,)
        ).fetchone()[0]
    assert state == "interrupted"


def test_history_projection_obeys_hard_budget_for_oversized_latest_turn(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    _complete_turn(store, "u" * 20_000, "a" * 20_000)

    history = store.build_thread_history()

    assert len(history) == 2
    assert sum(len(message["content"]) for message in history) <= MAX_HISTORY_CHARS
    assert history[0]["content"].endswith("…")
    assert history[1]["content"].endswith("…")


def test_extraction_is_head_of_line_and_lease_expiry_is_fenced(tmp_path: Path) -> None:
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    for index in range(5):
        _complete_turn(store, f"user-{index}", f"assistant-{index}")

    first = store.claim_extraction_batch("worker-a", force=True)
    assert first is not None
    assert len(first.jobs) == 4
    assert store.claim_extraction_batch("worker-b", force=True) is None

    clock.advance(121)
    with pytest.raises(MemoryLeaseError):
        store.complete_extraction(first, ExtractionResult("stale", ()))

    reclaimed = store.claim_extraction_batch("worker-b", force=True)
    assert reclaimed is not None
    assert [job.id for job in reclaimed.jobs] == [job.id for job in first.jobs]
    store.complete_extraction(reclaimed, ExtractionResult("fresh", ()))
    next_batch = store.claim_extraction_batch("worker-b", force=True)
    assert next_batch is not None
    assert len(next_batch.jobs) == 1


def test_extraction_natural_schedule_uses_count_age_and_batch_limit(
    tmp_path: Path,
) -> None:
    threshold_clock = FakeClock()
    threshold_store = MemoryStore(
        tmp_path / "threshold-memory.db", clock=threshold_clock
    )
    first_turn = _complete_turn(threshold_store, "first user", "first assistant")

    assert threshold_store.claim_extraction_batch("early-worker") is None
    threshold_clock.advance(29)
    assert threshold_store.claim_extraction_batch("still-early-worker") is None

    second_turn = _complete_turn(threshold_store, "second user", "second assistant")
    threshold_batch = threshold_store.claim_extraction_batch("threshold-worker")
    assert threshold_batch is not None
    assert [job.payload["turn_id"] for job in threshold_batch.jobs] == [
        first_turn,
        second_turn,
    ]

    age_clock = FakeClock()
    age_store = MemoryStore(tmp_path / "age-memory.db", clock=age_clock)
    aged_turn = _complete_turn(age_store, "aged user", "aged assistant")
    age_clock.advance(30)

    age_batch = age_store.claim_extraction_batch("age-worker")
    assert age_batch is not None
    assert [job.payload["turn_id"] for job in age_batch.jobs] == [aged_turn]

    limit_store = MemoryStore(tmp_path / "limit-memory.db", clock=FakeClock())
    turn_ids = [
        _complete_turn(limit_store, f"user-{index}", f"assistant-{index}")
        for index in range(5)
    ]

    limit_batch = limit_store.claim_extraction_batch("limit-worker")
    assert limit_batch is not None
    assert [job.payload["turn_id"] for job in limit_batch.jobs] == turn_ids[:4]


def test_unrelated_success_keeps_unresolved_job_error_visible(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    old_thread = store.ensure_active_thread()
    _complete_turn(store, "old user", "old assistant", owner="front-old")
    failed = store.claim_extraction_batch("worker-old", force=True)
    assert failed is not None
    store.record_job_failure(failed, "RAW_OLD_FAILURE")

    store.start_new_thread(expected_thread_id=old_thread.id)
    _complete_turn(store, "new user", "new assistant", owner="front-new")
    succeeded = store.claim_extraction_batch("worker-new", force=True)
    assert succeeded is not None
    store.complete_extraction(succeeded, ExtractionResult("new compaction", ()))

    status = store.status()
    assert status.retry_wait_jobs == 1
    assert status.last_error == "RAW_OLD_FAILURE"


def test_global_job_error_falls_back_until_all_failed_jobs_succeed(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    first_thread = store.ensure_active_thread()
    _complete_turn(store, "first user", "first assistant", owner="front-first")
    first_failed = store.claim_extraction_batch("worker-first", force=True)
    assert first_failed is not None
    store.record_job_failure(first_failed, "RAW_FIRST_FAILURE")

    store.start_new_thread(expected_thread_id=first_thread.id)
    clock.advance(1)
    _complete_turn(store, "second user", "second assistant", owner="front-second")
    second_failed = store.claim_extraction_batch("worker-second", force=True)
    assert second_failed is not None
    store.record_job_failure(second_failed, "RAW_SECOND_FAILURE")
    assert store.status().last_error == "RAW_SECOND_FAILURE"

    clock.advance(5)
    first_retry = store.claim_extraction_batch("retry-first", force=True)
    assert first_retry is not None
    assert first_retry.jobs[0].id == first_failed.jobs[0].id
    assert store.status().last_error == "RAW_FIRST_FAILURE"
    store.complete_extraction(first_retry, ExtractionResult("first resolved", ()))
    assert store.status().last_error == "RAW_SECOND_FAILURE"

    second_retry = store.claim_extraction_batch("retry-second", force=True)
    assert second_retry is not None
    assert second_retry.jobs[0].id == second_failed.jobs[0].id
    store.complete_extraction(second_retry, ExtractionResult("second resolved", ()))
    status = store.status()
    assert status.retry_wait_jobs == 0
    assert status.last_error == ""
    assert status.last_succeeded_at is not None


def test_global_job_error_uses_job_id_tie_break_when_timestamps_match(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    first_thread = store.ensure_active_thread()
    _complete_turn(store, "first user", "first assistant", owner="front-first")
    store.start_new_thread(expected_thread_id=first_thread.id)
    _complete_turn(store, "second user", "second assistant", owner="front-second")

    first_claim = store.claim_extraction_batch("worker-first", force=True)
    second_claim = store.claim_extraction_batch("worker-second", force=True)
    assert first_claim is not None
    assert second_claim is not None
    batches = {
        first_claim.jobs[0].id: first_claim,
        second_claim.jobs[0].id: second_claim,
    }
    low_id, high_id = sorted(batches)

    store.record_job_failure(batches[high_id], "RAW_HIGH_ID_FAILURE")
    store.record_job_failure(batches[low_id], "RAW_LOW_ID_FAILURE")

    assert store.status().last_error == "RAW_HIGH_ID_FAILURE"
    store.clear()
    assert store.status().last_error == ""


def test_expired_job_lease_refreshes_global_error_on_recovery_and_claim(
    tmp_path: Path,
) -> None:
    recovery_clock = FakeClock()
    recovery_store = MemoryStore(
        tmp_path / "recovery-memory.db",
        clock=recovery_clock,
    )
    _complete_turn(recovery_store, "recover user", "recover assistant")
    recovery_batch = recovery_store.claim_extraction_batch("stale-worker", force=True)
    assert recovery_batch is not None
    recovery_clock.advance(121)

    recovery_store.recover_startup()

    recovery_status = recovery_store.status()
    assert recovery_status.retry_wait_jobs == 1
    assert recovery_status.last_error == "lease expired before startup recovery"

    claim_clock = FakeClock()
    claim_store = MemoryStore(tmp_path / "claim-memory.db", clock=claim_clock)
    _complete_turn(claim_store, "claim user", "claim assistant")
    claim_batch = claim_store.claim_extraction_batch("stale-worker", force=True)
    assert claim_batch is not None
    claim_clock.advance(121)

    reclaimed = claim_store.claim_extraction_batch("current-worker", force=True)

    assert reclaimed is not None
    claim_status = claim_store.status()
    assert claim_status.leased_jobs == 1
    assert claim_status.last_error == "lease expired"


def test_consolidation_stale_completion_and_failure_are_fenced(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    candidate_id = _extract_candidate(store, title="简洁回答", owner="extract")
    stale = store.claim_consolidation_job("worker-a", force=True)
    assert stale is not None

    clock.advance(121)
    current = store.claim_consolidation_job("worker-b", force=True)
    assert current is not None
    assert current.jobs[0].id == stale.jobs[0].id
    result = ConsolidationResult(
        operations=(
            ConsolidationOperation(
                operation="create",
                candidate_ids=(candidate_id,),
                target_id=None,
                title="简洁回答",
                content="用户明确偏好简洁回答",
                synopsis="回答保持简洁",
                aliases=("concise answers",),
                keywords=("回答风格",),
            ),
        )
    )

    with pytest.raises(MemoryLeaseError):
        store.apply_consolidation(stale, result)
    with pytest.raises(MemoryLeaseError):
        store.record_job_failure(stale, "Memory consolidation failed (TimeoutError)")

    created = store.apply_consolidation(current, result)
    assert len(created) == 1


def test_consolidation_multiple_operations_roll_back_atomically(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path)
    existing_id = _seed_active_item(store, title="简洁回答")
    user_text = "以后默认简洁回答，并且以后默认使用中文。"
    turn_id = _complete_turn(store, user_text, "收到")
    extraction = store.claim_extraction_batch("atomic-extractor", force=True)
    assert extraction is not None
    candidate_ids = store.complete_extraction(
        extraction,
        ExtractionResult(
            thread_compaction="用户偏好简洁并使用中文",
            candidates=(
                ExtractionCandidateInput(
                    kind="user_preference",
                    title="简洁回答",
                    content="用户明确偏好简洁回答",
                    aliases=("concise answers",),
                    sources=(
                        CandidateSource(turn_id, "user", "以后默认简洁回答"),
                    ),
                ),
                ExtractionCandidateInput(
                    kind="user_preference",
                    title="中文回答",
                    content="用户明确偏好使用中文回答",
                    aliases=("Chinese answers",),
                    sources=(
                        CandidateSource(turn_id, "user", "以后默认使用中文"),
                    ),
                ),
            ),
        ),
    )
    assert len(candidate_ids) == 2
    consolidation = store.claim_consolidation_job("atomic-consolidator", force=True)
    assert consolidation is not None

    with pytest.raises(MemoryContractError, match="不允许的 consolidation operation"):
        store.apply_consolidation(
            consolidation,
            ConsolidationResult(
                operations=(
                    ConsolidationOperation(
                        operation="revise",
                        candidate_ids=(candidate_ids[0],),
                        target_id=existing_id,
                        title="简洁回答",
                        content="用户明确偏好非常简洁的回答",
                        synopsis="回答保持非常简洁",
                        aliases=("concise answers",),
                        keywords=("回答风格",),
                    ),
                    ConsolidationOperation(
                        operation="merge",
                        candidate_ids=(candidate_ids[1],),
                        target_id=None,
                        title="中文回答",
                        content="用户明确偏好使用中文回答",
                        synopsis="默认使用中文",
                        aliases=("Chinese answers",),
                        keywords=("回答语言",),
                    ),
                )
            ),
        )

    detail = store.read(existing_id)
    assert detail.id == existing_id
    assert detail.revision == 1
    assert detail.content == "用户明确偏好简洁回答"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT id, revision, status FROM memory_items ORDER BY id"
        ).fetchall() == [(existing_id, 1, "active")]
        assert connection.execute(
            "SELECT id, status FROM memory_candidates WHERE id IN (?, ?) ORDER BY id",
            candidate_ids,
        ).fetchall() == sorted(
            [(candidate_ids[0], "pending"), (candidate_ids[1], "pending")]
        )


def test_two_managers_cannot_claim_the_same_consolidation_job(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    db_path = tmp_path / "memory.db"
    first = MemoryStore(db_path, clock=clock)
    second = MemoryStore(db_path, clock=clock)
    _extract_candidate(first, title="简洁回答", owner="extract")
    barrier = Barrier(2)

    def claim(store: MemoryStore, owner: str):
        barrier.wait()
        return store.claim_consolidation_job(owner, force=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda args: claim(*args),
                ((first, "worker-a"), (second, "worker-b")),
            )
        )

    winners = [batch for batch in claims if batch is not None]
    assert len(winners) == 1
    assert winners[0].jobs[0].attempt_count == 1


def test_short_confirmation_requires_assistant_proposal_source(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    proposal_turn = _complete_turn(store, "给我一个方案", "建议采用 SQLite 两阶段方案")
    confirm_turn = _complete_turn(store, "同意", "收到")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    invalid = ExtractionResult(
        "已确认方案",
        (
            ExtractionCandidateInput(
                kind="project_decision",
                title="Memory 架构",
                content="采用 SQLite 两阶段方案",
                aliases=("memory architecture",),
                sources=(CandidateSource(confirm_turn, "user", "同意"),),
            ),
        ),
    )
    assert store.complete_extraction(batch, invalid) == ()

    store = MemoryStore(tmp_path / "valid-memory.db")
    proposal_turn = _complete_turn(store, "给我一个方案", "建议采用 SQLite 两阶段方案")
    confirm_turn = _complete_turn(store, "同意，就这么做。", "收到")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    valid = ExtractionResult(
        "已确认方案",
        (
            ExtractionCandidateInput(
                kind="project_decision",
                title="Memory 架构",
                content="采用 SQLite 两阶段方案",
                aliases=("memory architecture",),
                sources=(
                    CandidateSource(
                        proposal_turn, "assistant", "建议采用 SQLite 两阶段方案"
                    ),
                    CandidateSource(confirm_turn, "user", "同意，就这么做。"),
                ),
            ),
        ),
    )
    candidate_ids = store.complete_extraction(batch, valid)
    assert len(candidate_ids) == 1


def test_structural_provenance_error_still_rejects_the_batch(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    turn_id = _complete_turn(store, "以后默认用中文回答", "收到")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    with pytest.raises(MemoryContractError, match="精确子串"):
        store.complete_extraction(
            batch,
            ExtractionResult(
                "用户偏好中文回答",
                (
                    ExtractionCandidateInput(
                        kind="user_preference",
                        title="中文回答",
                        content="以后默认用中文回答",
                        aliases=("Chinese answers",),
                        sources=(CandidateSource(turn_id, "user", "不存在的原文"),),
                    ),
                ),
            ),
        )


def test_semantic_filter_keeps_valid_candidate_and_compaction(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    raw = "以后默认用中文回答；pom.xml 当前配置的是 Java 17。"
    turn_id = _complete_turn(store, raw, "收到")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            "用户偏好中文回答，并提到当前 Java 配置。",
            (
                ExtractionCandidateInput(
                    kind="user_preference",
                    title="中文回答",
                    content="以后默认用中文回答",
                    aliases=("Chinese answers",),
                    sources=(CandidateSource(turn_id, "user", raw),),
                ),
                ExtractionCandidateInput(
                    kind="project_decision",
                    title="Java 版本",
                    content="pom.xml 使用 Java 17",
                    aliases=("Java version",),
                    sources=(CandidateSource(turn_id, "user", raw),),
                ),
            ),
        ),
    )

    assert len(candidate_ids) == 1
    with sqlite3.connect(tmp_path / "memory.db") as connection:
        assert connection.execute(
            "SELECT kind FROM memory_candidates WHERE id = ?", candidate_ids
        ).fetchone() == ("user_preference",)
        assert connection.execute(
            "SELECT compaction FROM threads WHERE status = 'active'"
        ).fetchone() == ("用户偏好中文回答，并提到当前 Java 配置。",)
        assert connection.execute(
            "SELECT status FROM memory_jobs WHERE id = ?", (batch.jobs[0].id,)
        ).fetchone() == ("succeeded",)


def test_semantic_filter_rejects_code_fact_as_discussion_context(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    raw = "pom.xml 当前配置的是 Java 17。"
    turn_id = _complete_turn(store, raw, "当前配置确实如此")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            "用户提到了当前 Java 配置。",
            (
                ExtractionCandidateInput(
                    kind="discussion_context",
                    title="当前 Java 配置",
                    content="pom.xml 当前配置的是 Java 17",
                    aliases=("Java version",),
                    sources=(CandidateSource(turn_id, "user", raw),),
                ),
            ),
        ),
    )

    assert candidate_ids == ()
    assert store.status().pending_jobs == 0


def test_explicit_code_configuration_change_is_a_decision(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    raw = "项目决定将 pom.xml 的 Java 版本改为 21。"
    turn_id = _complete_turn(store, raw, "已确认")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            "项目将 Java 版本升级到 21。",
            (
                ExtractionCandidateInput(
                    kind="project_decision",
                    title="Java 21 配置",
                    content="将 pom.xml 的 Java 版本改为 21",
                    aliases=("Java version", "pom.xml"),
                    sources=(CandidateSource(turn_id, "user", raw),),
                ),
            ),
        ),
    )

    assert len(candidate_ids) == 1


def test_short_confirmation_rejects_assistant_fact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    assistant_text = "当前 pom.xml 配置的是 Java 17"
    fact_turn = _complete_turn(store, "帮我看当前配置", assistant_text)
    confirm_turn = _complete_turn(store, "同意，就这么做。", "收到")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    assert store.complete_extraction(
        batch,
        ExtractionResult(
            "讨论 Java 配置",
            (
                ExtractionCandidateInput(
                    kind="project_decision",
                    title="Java 17",
                    content="pom.xml 使用 Java 17",
                    aliases=("Java version",),
                    sources=(
                        CandidateSource(fact_turn, "assistant", assistant_text),
                        CandidateSource(confirm_turn, "user", "同意，就这么做。"),
                    ),
                ),
            ),
        ),
    ) == ()


def test_short_confirmation_rejects_non_adjacent_proposal(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    proposal_turn = _complete_turn(store, "给我方案", "建议采用 SQLite 两阶段方案")
    _complete_turn(store, "再说说风险", "主要风险是调度延迟")
    confirm_turn = _complete_turn(store, "同意", "收到")
    batch = store.claim_extraction_batch("worker", force=True)
    assert batch is not None

    assert store.complete_extraction(
        batch,
        ExtractionResult(
            "讨论 SQLite 方案",
            (
                ExtractionCandidateInput(
                    kind="project_decision",
                    title="SQLite 两阶段方案",
                    content="采用 SQLite 两阶段方案",
                    aliases=("SQLite",),
                    sources=(
                        CandidateSource(
                            proposal_turn, "assistant", "建议采用 SQLite 两阶段方案"
                        ),
                        CandidateSource(confirm_turn, "user", "同意"),
                    ),
                ),
            ),
        ),
    ) == ()


def test_clear_removes_linked_repo_item_and_fences_claim(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    item_id = _seed_active_item(store)
    another_turn = _complete_turn(store, "另一轮", "结果")
    del another_turn
    claimed = store.claim_extraction_batch("stale", force=True)
    assert claimed is not None

    result = store.clear()

    assert result.deleted_items == 1
    assert store.list_items() == []
    assert store.status().turn_count == 0
    assert store.status().thread_count == 1
    with pytest.raises(MemoryLeaseError):
        store.complete_extraction(claimed, ExtractionResult("late", ()))
    with pytest.raises(LookupError):
        store.show_item(item_id)


def test_forget_suppresses_old_candidates_and_rejects_replayed_consolidation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path)
    item_id = _seed_active_item(store, title="简洁回答")
    with sqlite3.connect(db_path) as connection:
        candidate_id, thread_id = connection.execute(
            """
            SELECT c.id, c.thread_id
            FROM memory_candidates c
            JOIN memory_item_candidates mic ON mic.candidate_id = c.id
            WHERE mic.item_id = ?
            """,
            (item_id,),
        ).fetchone()
        generation = connection.execute(
            "SELECT generation FROM memory_meta WHERE id = 1"
        ).fetchone()[0]

    store.forget(item_id)

    with sqlite3.connect(db_path) as connection:
        status = connection.execute(
            "SELECT status FROM memory_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()[0]
        now = store._now()
        connection.execute(
            """
            INSERT INTO memory_jobs(
                id, kind, thread_id, payload_json, idempotency_key, status,
                generation, created_at, updated_at
            ) VALUES (?, 'consolidation', ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                "job_replayed_suppressed_candidate",
                thread_id,
                json.dumps({"candidate_ids": [candidate_id]}),
                f"replay:{candidate_id}",
                generation,
                now,
                now,
            ),
        )
    assert status == "suppressed"

    replay = store.claim_consolidation_job("replay-worker", force=True)
    assert replay is not None
    with pytest.raises(MemoryContractError, match="不存在的 candidate"):
        store.consolidation_payload(replay)
    with pytest.raises(MemoryContractError, match="candidates 已丢失"):
        store.apply_consolidation(
            replay,
            ConsolidationResult(
                operations=(
                    ConsolidationOperation(
                        operation="create",
                        candidate_ids=(candidate_id,),
                        target_id=None,
                        title="简洁回答",
                        content="用户明确偏好简洁回答",
                        synopsis="回答保持简洁",
                        aliases=(),
                        keywords=(),
                    ),
                )
            ),
        )
    assert store.search("简洁回答") == []


def test_read_rejects_forgotten_archived_and_superseded_items(
    tmp_path: Path,
) -> None:
    forgotten_store = MemoryStore(tmp_path / "forgotten-memory.db")
    forgotten_id = _seed_active_item(forgotten_store, title="简洁回答")
    forgotten_store.forget(forgotten_id)

    with pytest.raises(MemoryNotFoundError):
        forgotten_store.read(forgotten_id)

    archived_store = MemoryStore(tmp_path / "archived-memory.db")
    old_thread = archived_store.ensure_active_thread()
    archived_id = _seed_active_item(
        archived_store,
        kind="discussion_context",
        title="旧 thread 讨论",
        content="继续讨论旧 thread 的 Memory 方案",
        aliases=("old thread",),
        keywords=(),
    )
    archived_store.start_new_thread(expected_thread_id=old_thread.id)
    current_id = _seed_active_item(
        archived_store,
        kind="discussion_context",
        title="当前 thread 讨论",
        content="继续讨论当前 thread 的 Memory 方案",
        aliases=("current thread",),
        keywords=(),
    )

    with pytest.raises(MemoryNotFoundError):
        archived_store.read(archived_id)
    assert archived_store.read(current_id).id == current_id

    superseded_store = MemoryStore(tmp_path / "superseded-memory.db")
    superseded_id = _seed_active_item(superseded_store, title="简洁回答")
    candidate_id = _extract_candidate(
        superseded_store,
        title="简洁回答",
        owner="supersede-extractor",
        aliases=("concise answers",),
    )
    consolidation = superseded_store.claim_consolidation_job(
        "supersede-consolidator", force=True
    )
    assert consolidation is not None
    revised_ids = superseded_store.apply_consolidation(
        consolidation,
        ConsolidationResult(
            operations=(
                ConsolidationOperation(
                    operation="revise",
                    candidate_ids=(candidate_id,),
                    target_id=superseded_id,
                    title="简洁回答",
                    content="用户明确偏好进一步精简回答",
                    synopsis="回答进一步精简",
                    aliases=("concise answers",),
                    keywords=("回答风格",),
                ),
            )
        ),
    )

    assert len(revised_ids) == 1
    with pytest.raises(MemoryNotFoundError):
        superseded_store.read(superseded_id)
    assert superseded_store.read(revised_ids[0]).id == revised_ids[0]


def test_corrupt_database_is_degraded_and_explicit_clear_recovers(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    db_path.write_bytes(b"not-a-sqlite-database")
    manager = MemoryManager(db_path=db_path, llm=EmptyExtractionLLM())
    assert manager.status().degraded

    manager.clear()
    handle = manager.begin_turn(intent="general_chat", user_text="恢复后的对话")
    manager.complete_turn(handle.id, assistant_text="已恢复")
    flushed = manager.flush_once("test")
    manager.close()

    assert flushed.failed == 0
    assert flushed.succeeded == 1
    assert manager.status().healthy


def test_rejects_nonempty_unversioned_business_database(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE turns(id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO turns(id) VALUES ('legacy-turn')")

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert "静默升级" in manager.status().last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_rejects_unknown_populated_unversioned_database_without_mutation_and_clear_recovers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE foreign_data(value TEXT)")
        connection.execute("INSERT INTO foreign_data(value) VALUES ('keep-me')")
    with sqlite3.connect(db_path) as connection:
        original_journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    manager = MemoryManager(db_path=db_path)
    status = manager.status()

    assert status.degraded
    assert "table:foreign_data" in status.last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            connection.execute("PRAGMA journal_mode").fetchone()[0]
            == original_journal_mode
        )
        assert (
            connection.execute("SELECT value FROM foreign_data").fetchone()[0]
            == "keep-me"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'memory_meta'"
        ).fetchone()[0] == 0

    manager.clear()
    try:
        assert manager.status().healthy
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'foreign_data'"
            ).fetchone()[0] == 0
    finally:
        manager.close()


@pytest.mark.parametrize(
    ("ddl", "expected_object"),
    (
        ("CREATE TABLE foreign_empty(value TEXT)", "table:foreign_empty"),
        ("CREATE VIEW foreign_view AS SELECT 1 AS value", "view:foreign_view"),
    ),
)
def test_rejects_unknown_empty_unversioned_schema(
    tmp_path: Path,
    ddl: str,
    expected_object: str,
) -> None:
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(ddl)

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert expected_object in manager.status().last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        object_type, object_name = expected_object.split(":", maxsplit=1)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
            (object_type, object_name),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'memory_meta'"
        ).fetchone()[0] == 0


def test_rejects_v2_table_with_missing_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE turns")
        connection.execute("CREATE TABLE turns(id TEXT PRIMARY KEY)")

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert "table:turns" in manager.status().last_error


def test_rejects_v2_missing_table_without_repair_or_journal_change(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path, isolation_level=None) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        original_journal_mode = connection.execute(
            "PRAGMA journal_mode=DELETE"
        ).fetchone()[0]
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE memory_terms")

    manager = MemoryManager(db_path=db_path)
    status = manager.status()

    assert status.degraded
    assert "table:memory_terms" in status.last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == original_journal_mode
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'memory_terms'"
        ).fetchone()[0] == 0


def test_rejects_v2_missing_required_index_without_repair(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX terms_lookup")

    manager = MemoryManager(db_path=db_path)
    status = manager.status()

    assert status.degraded
    assert "index:terms_lookup" in status.last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'terms_lookup'"
        ).fetchone()[0] == 0


def test_rejects_v2_table_with_expected_columns_and_index_but_missing_constraints(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE memory_terms")
        connection.execute(
            """
            CREATE TABLE memory_terms (
                item_id TEXT NOT NULL,
                term TEXT NOT NULL,
                term_type TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX terms_lookup
            ON memory_terms(term_type, term COLLATE BINARY, item_id)
            """
        )

    manager = MemoryManager(db_path=db_path)
    status = manager.status()

    assert status.degraded
    assert "table:memory_terms" in status.last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_list(memory_terms)").fetchall() == []
        assert {row[1] for row in connection.execute("PRAGMA index_list(memory_terms)")} == {
            "terms_lookup"
        }


@pytest.mark.parametrize(
    ("ddl", "object_name"),
    (
        ("CREATE TABLE unexpected_table(value TEXT)", "unexpected_table"),
        ("CREATE VIEW unexpected_view AS SELECT 1 AS value", "unexpected_view"),
        ("CREATE INDEX unexpected_index ON threads(created_at)", "unexpected_index"),
        (
            "CREATE TRIGGER unexpected_trigger AFTER INSERT ON threads BEGIN SELECT 1; END",
            "unexpected_trigger",
        ),
    ),
)
def test_rejects_v2_unexpected_schema_object(
    tmp_path: Path,
    ddl: str,
    object_name: str,
) -> None:
    db_path = tmp_path / f"{object_name}.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(ddl)

    manager = MemoryManager(db_path=db_path)
    status = manager.status()

    assert status.degraded
    assert object_name in status.last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (object_name,)
        ).fetchone()[0] == 1


def test_rejects_v2_missing_meta_row_without_repair_and_clear_recovers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM memory_meta")

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert "memory_meta" in manager.status().last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_meta").fetchone()[0] == 0

    manager.clear()
    try:
        assert manager.status().healthy
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM memory_meta WHERE id = 1"
            ).fetchone()[0] == 1
    finally:
        manager.close()


def test_rejects_v2_missing_active_thread_without_repair(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM threads")

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert "active thread" in manager.status().last_error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM threads WHERE status = 'active'"
        ).fetchone()[0] == 0


def test_valid_v2_reopens_after_vacuum(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("VACUUM")

    manager = MemoryManager(db_path=db_path)

    assert manager.status().healthy


def test_runtime_missing_active_thread_is_typed_and_status_is_degraded(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path)
    store = manager.store
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM threads")

    with pytest.raises(MemorySchemaError, match="active thread"):
        store.ensure_active_thread()
    with pytest.raises(MemorySchemaError, match="active thread"):
        store.begin_turn("general_chat", "不得自动修复", "test-owner")
    with pytest.raises(MemorySchemaError, match="active thread"):
        store.recover_startup()
    with pytest.raises(MemorySchemaError, match="active thread"):
        manager.build_thread_history()
    with pytest.raises(MemorySchemaError, match="active thread"):
        manager.build_routing_context("general_chat")

    assert manager.status().degraded
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM threads WHERE status = 'active'"
        ).fetchone()[0] == 0


def test_runtime_missing_meta_row_is_typed_and_status_is_degraded(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path)
    store = manager.store
    handle = store.begin_turn("general_chat", "等待完成", "test-owner")
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM memory_meta")

    assert manager.status().degraded
    with pytest.raises(MemorySchemaError, match="memory_meta"):
        store.complete_turn(handle.id, "不应提交", "test-owner")
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT state FROM turns WHERE id = ?", (handle.id,)
        ).fetchone()[0] == "open"


def test_runtime_missing_meta_rejects_begin_turn_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path)
    store = manager.store
    with sqlite3.connect(db_path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        connection.execute("DELETE FROM memory_meta")

    with pytest.raises(MemorySchemaError, match="memory_meta"):
        store.begin_turn("general_chat", "不得写入", "test-owner")

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == before
    assert manager.status().degraded


def test_healthy_empty_memory_returns_empty_projections(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")

    assert manager.build_thread_history() == []
    assert manager.build_routing_context("general_chat") == ""
    assert manager.search("不存在的主题") == []
    assert manager.status().healthy


@pytest.mark.parametrize("damaged_state", ["meta", "active_thread"])
def test_same_manager_diagnoses_and_clears_runtime_bootstrap_damage(
    tmp_path: Path,
    damaged_state: str,
) -> None:
    clock = FakeClock()
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path, clock=clock)
    store = manager.store
    item_id = _seed_active_item(store, title="bootstrap recovery")
    _complete_turn(store, "stale user", "stale assistant", owner="stale-front")
    stale = store.claim_extraction_batch("stale-worker", force=True)
    assert stale is not None
    with sqlite3.connect(db_path) as connection:
        old_created_at = connection.execute(
            "SELECT created_at FROM memory_meta WHERE id = 1"
        ).fetchone()[0]
        if damaged_state == "meta":
            connection.execute("DELETE FROM memory_meta")
        else:
            connection.execute("DELETE FROM threads")
    clock.advance(60)
    recovery_time = store._now()

    assert manager.status().degraded
    assert manager.show_item(item_id).id == item_id
    exported = manager.export(tmp_path / f"exports-{damaged_state}")
    snapshot = json.loads(exported.path.read_text(encoding="utf-8"))
    assert snapshot["memory_items"][0]["id"] == item_id

    result = manager.clear()

    assert result.generation == stale.generation + 1
    with sqlite3.connect(db_path) as connection:
        meta = connection.execute("SELECT * FROM memory_meta").fetchall()
        active = connection.execute(
            "SELECT id FROM threads WHERE status = 'active'"
        ).fetchall()
        assert len(meta) == 1
        assert len(active) == 1
        assert meta[0][1] == result.generation
        created_at = meta[0][4]
        assert created_at == (
            recovery_time if damaged_state == "meta" else old_created_at
        )
        assert meta[0][2] == ""
        assert meta[0][3] is None
        for table in (
            "turns",
            "memory_jobs",
            "memory_candidates",
            "candidate_sources",
            "memory_items",
            "memory_item_candidates",
            "memory_terms",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    with pytest.raises(MemoryLeaseError):
        store.complete_extraction(stale, ExtractionResult("late", ()))
    assert manager.status().healthy


def test_explicit_thread_scope_survives_new_thread_switch(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = manager.store
    old_thread = manager.ensure_active_thread()
    _complete_turn(store, "old history", "old answer")
    discussion_id = _seed_active_item(
        store,
        kind="discussion_context",
        title="old thread topic",
        content="continue the old discussion",
        aliases=("old topic",),
        keywords=(),
    )

    new_thread = manager.start_new_thread(expected_thread_id=old_thread.id)

    assert manager.build_thread_history() == []
    assert manager.build_thread_history(old_thread.id)
    assert discussion_id not in manager.build_routing_context("general_chat")
    assert discussion_id in manager.build_routing_context(
        "general_chat", old_thread.id
    )
    assert manager.search("old thread topic", thread_id=new_thread.id) == []
    assert [
        hit.id for hit in manager.search("old thread topic", thread_id=old_thread.id)
    ] == [discussion_id]
    assert manager.read(discussion_id, thread_id=old_thread.id).id == discussion_id
    with pytest.raises(MemoryNotFoundError):
        manager.read(discussion_id, thread_id=new_thread.id)


def test_foreign_key_check_detects_dangling_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    MemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO candidate_sources(candidate_id, turn_id, role, quote)
            VALUES ('missing-candidate', 'missing-turn', 'user', 'raw')
            """
        )

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert "foreign_key_check" in manager.status().last_error


def test_export_contains_raw_turn_and_is_one_time_snapshot(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    _complete_turn(store, "RAW user secret", "RAW final answer")

    first = store.export()
    second = store.export()
    payload = json.loads(first.path.read_text(encoding="utf-8"))

    assert first.path != second.path
    assert payload["turns"][0]["user_text"] == "RAW user secret"
    assert payload["turns"][0]["assistant_text"] == "RAW final answer"


def test_concurrent_exports_with_same_timestamp_are_unique_and_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_count = 16
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    _complete_turn(store, "RAW concurrent user", "RAW concurrent assistant")
    export_dir = tmp_path / "exports"
    barrier = Barrier(worker_count)
    seen_threads: set[int] = set()
    seen_lock = Lock()
    original_exists = Path.exists

    def synchronized_exists(path: Path) -> bool:
        should_wait = False
        if path.parent == export_dir and path.suffix == ".json":
            thread_id = get_ident()
            with seen_lock:
                if thread_id not in seen_threads:
                    seen_threads.add(thread_id)
                    should_wait = True
        if should_wait:
            barrier.wait(timeout=10)
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", synchronized_exists)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(store.export, export_dir) for _ in range(worker_count)]
        results = [future.result() for future in futures]

    paths = [result.path for result in results]
    assert len(set(paths)) == worker_count
    assert len(list(export_dir.glob("*.json"))) == worker_count
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["turns"][0]["user_text"] == "RAW concurrent user"
        assert payload["turns"][0]["assistant_text"] == "RAW concurrent assistant"
    assert not any(
        path.suffix in {".tmp", ".lock"} for path in export_dir.iterdir()
    )


def test_export_write_failure_cleans_reserved_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "memory.db", clock=FakeClock())
    export_dir = tmp_path / "exports"
    original_write_text = Path.write_text

    def fail_temp_write(path: Path, *args: Any, **kwargs: Any) -> int:
        if path.suffix == ".tmp":
            raise OSError("simulated export write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temp_write)

    with pytest.raises(MemoryStorageError, match="simulated export write failure"):
        store.export(export_dir)

    assert list(export_dir.iterdir()) == []


def test_clear_preserves_existing_export_artifact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    _complete_turn(store, "RAW user", "RAW assistant")
    exported = store.export()
    snapshot = exported.path.read_text(encoding="utf-8")

    result = store.clear()

    assert result.deleted_turns == 1
    assert exported.path.exists()
    assert exported.path.read_text(encoding="utf-8") == snapshot
    assert store.status().turn_count == 0
    assert store.status().thread_count == 1


def test_consolidation_target_must_be_related_active_item(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    unrelated_id = _seed_active_item(
        store,
        title="深色主题",
        content="用户明确偏好深色主题",
        aliases=("dark theme",),
    )
    candidate_id = _extract_candidate(store, title="简洁回答", owner="extract-b")
    batch = store.claim_consolidation_job("consolidator-b", force=True)
    assert batch is not None

    with pytest.raises(MemoryContractError, match="related active items"):
        store.apply_consolidation(
            batch,
            ConsolidationResult(
                operations=(
                    ConsolidationOperation(
                        operation="revise",
                        candidate_ids=(candidate_id,),
                        target_id=unrelated_id,
                        title="简洁回答",
                        content="用户明确偏好简洁回答",
                        synopsis="回答保持简洁",
                        aliases=("concise",),
                        keywords=("回答风格",),
                    ),
                )
            ),
        )


def test_consolidation_retry_blocks_later_jobs_head_of_line(tmp_path: Path) -> None:
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    _extract_candidate(store, title="简洁回答", owner="extract-a")
    clock.advance(1)
    _extract_candidate(store, title="深色主题", owner="extract-b")

    first = store.claim_consolidation_job("worker-a", force=True)
    assert first is not None
    store.record_job_failure(first, "Memory consolidation failed (TimeoutError)")

    assert store.claim_consolidation_job("worker-b", force=True) is None
    clock.advance(5)
    reclaimed = store.claim_consolidation_job("worker-b", force=True)
    assert reclaimed is not None
    assert reclaimed.jobs[0].id == first.jobs[0].id


def test_search_ranks_strict_metadata_matches_before_content(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", clock=FakeClock())
    title_id = _seed_active_item(
        store,
        title="memory",
        content="title payload",
        aliases=(),
        keywords=(),
    )
    alias_id = _seed_active_item(
        store,
        title="alias record",
        content="alias payload",
        aliases=("memory",),
        keywords=(),
    )
    keyword_id = _seed_active_item(
        store,
        title="keyword record",
        content="keyword payload",
        aliases=(),
        keywords=("memory",),
    )
    prefix_id = _seed_active_item(
        store,
        title="memory architecture",
        content="prefix payload",
        aliases=(),
        keywords=(),
    )
    substring_id = _seed_active_item(
        store,
        title="agent memory architecture",
        content="substring payload",
        aliases=(),
        keywords=(),
    )

    hits = store.search("ＭＥＭＯＲＹ")

    assert [hit.id for hit in hits] == [
        title_id,
        alias_id,
        keyword_id,
        prefix_id,
        substring_id,
    ]
    assert [hit.match_type for hit in hits] == [
        "exact",
        "exact",
        "exact",
        "prefix",
        "substring",
    ]


def test_search_content_terms_are_bounded_and_never_promoted_to_exact(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    oversized = "x" * 161
    content = " ".join((oversized, *(f"token{index}" for index in range(300))))
    item_id = _seed_active_item(
        store,
        title="Fallback Record",
        content=content,
        aliases=(),
        keywords=(),
    )

    with sqlite3.connect(tmp_path / "memory.db") as connection:
        title_terms = connection.execute(
            "SELECT term FROM memory_terms WHERE item_id = ? AND term_type = 'title'",
            (item_id,),
        ).fetchall()
        stored_content_terms = connection.execute(
            "SELECT term FROM memory_terms WHERE item_id = ? AND term_type = 'content'",
            (item_id,),
        ).fetchall()

    assert title_terms == [("fallback record",)]
    assert len(stored_content_terms) == MAX_CONTENT_TERMS
    assert (oversized,) not in stored_content_terms
    assert store.search("token0")[0].match_type == "content_term"
    assert store.search(f"token{MAX_CONTENT_TERMS - 1}")[0].id == item_id
    assert store.search(f"token{MAX_CONTENT_TERMS}") == []


def test_search_rejects_oversized_normalized_query_without_truncation(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    exact = "x" * MAX_SEARCH_QUERY_CHARS
    item_id = _seed_active_item(
        store,
        title=exact,
        content="bounded query payload",
        aliases=(),
        keywords=(),
    )

    assert [hit.id for hit in store.search(exact)] == [item_id]
    assert store.search(exact + "y") == []


def test_search_is_one_directional_and_treats_underscore_literally(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db", clock=FakeClock())
    _seed_active_item(
        store,
        title="short alias",
        content="unrelated payload",
        aliases=("memory",),
        keywords=(),
    )
    literal_id = _seed_active_item(
        store,
        title="literal underscore",
        content="literal payload",
        aliases=("foo_bar",),
        keywords=(),
    )
    _seed_active_item(
        store,
        title="different separator",
        content="different payload",
        aliases=("fooxbar",),
        keywords=(),
    )
    first_tie = _seed_active_item(
        store,
        title="stable alpha",
        content="first stable payload",
        aliases=(),
        keywords=(),
    )
    second_tie = _seed_active_item(
        store,
        title="stable beta",
        content="second stable payload",
        aliases=(),
        keywords=(),
    )

    assert store.search("memory database plan") == []
    assert [hit.id for hit in store.search("ＦＯＯ＿ＢＡＲ")] == [literal_id]
    assert [hit.id for hit in store.search("stable")] == sorted(
        (first_tie, second_tie)
    )


def test_search_uses_at_most_three_sql_queries_without_per_item_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    item_id = _seed_active_item(
        store,
        title="fallback only",
        content="needle payload",
        aliases=(),
        keywords=(),
    )
    thread_id = store.ensure_active_thread().id
    statements: list[str] = []
    original_connect = sqlite3.connect

    def traced_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)

    hits = store.search("needle", thread_id=thread_id)

    query_statements = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "WITH"))
    ]
    assert [hit.id for hit in hits] == [item_id]
    assert len(query_statements) == 3
    assert not any(
        "SELECT TERM FROM MEMORY_TERMS WHERE ITEM_ID" in statement.upper()
        for statement in query_statements
    )


def test_related_items_use_one_metadata_query_and_respect_kind(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    preference_id = _seed_active_item(
        store,
        kind="user_preference",
        title="preference match",
        content="preference payload",
        aliases=("shared",),
        keywords=(),
    )
    decision_id = _seed_active_item(
        store,
        kind="project_decision",
        title="decision match",
        content="decision payload",
        aliases=("shared",),
        keywords=(),
    )
    content_only_id = _seed_active_item(
        store,
        kind="user_preference",
        title="content only",
        content="shared payload",
        aliases=(),
        keywords=(),
    )
    candidate_id = _extract_candidate(store, title="shared", owner="related")
    statements: list[str] = []

    with store._connect() as connection:
        candidates = store._load_candidates(connection, (candidate_id,))
        connection.set_trace_callback(statements.append)
        related = store._related_active_items(connection, candidates)
        connection.set_trace_callback(None)

    query_statements = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "WITH"))
    ]
    assert [str(row["id"]) for row in related] == [preference_id]
    assert decision_id not in {str(row["id"]) for row in related}
    assert content_only_id not in {str(row["id"]) for row in related}
    assert len(query_statements) == 1
    assert not any(
        "SELECT TERM FROM MEMORY_TERMS WHERE ITEM_ID" in statement.upper()
        for statement in query_statements
    )


def test_search_and_related_items_hide_archived_thread_discussion(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    old_thread = store.ensure_active_thread()
    old_id = _seed_active_item(
        store,
        kind="discussion_context",
        title="thread topic",
        content="old discussion",
        aliases=("shared discussion",),
        keywords=(),
    )
    store.start_new_thread(expected_thread_id=old_thread.id)
    current_id = _seed_active_item(
        store,
        kind="discussion_context",
        title="thread topic",
        content="current discussion",
        aliases=("shared discussion",),
        keywords=(),
    )
    candidate_id = _extract_candidate(
        store,
        title="shared discussion",
        owner="discussion-related",
        kind="discussion_context",
    )

    with store._connect() as connection:
        candidates = store._load_candidates(connection, (candidate_id,))
        related = store._related_active_items(connection, candidates)

    assert [hit.id for hit in store.search("thread topic")] == [current_id]
    assert [str(row["id"]) for row in related] == [current_id]
    assert old_id not in {str(row["id"]) for row in related}


def test_new_thread_removes_real_discussion_item_from_routing_context(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    old_thread = manager.ensure_active_thread()
    discussion_id = _seed_active_item(
        manager.store,
        kind="discussion_context",
        title="Memory 检索方向",
        content="继续讨论是否需要向量库",
        aliases=("memory retrieval",),
        keywords=("向量库",),
    )

    before = manager.build_routing_context("general_chat")
    manager.start_new_thread(expected_thread_id=old_thread.id)
    after = manager.build_routing_context("general_chat")

    assert discussion_id in before
    assert "Memory 检索方向" in before
    assert discussion_id not in after
    assert "Memory 检索方向" not in after


def test_related_items_have_stable_twenty_item_limit(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", clock=FakeClock())
    thread = store.ensure_active_thread()
    now = store._now()
    expected_ids = [f"item_{index:02d}" for index in range(MAX_RELATED_ITEMS)]
    with store._transaction() as connection:
        for index in range(MAX_RELATED_ITEMS + 5):
            item_id = f"item_{index:02d}"
            connection.execute(
                """
                INSERT INTO memory_items(
                    id, logical_id, revision, kind, thread_id, title, content,
                    synopsis, status, non_factual, created_at, updated_at
                ) VALUES (?, ?, 1, 'user_preference', NULL, 'shared',
                          'payload', 'synopsis', 'active', 0, ?, ?)
                """,
                (item_id, f"logical_{index:02d}", now, now),
            )
            store._insert_terms(
                connection,
                item_id,
                "shared",
                "payload",
                (),
                (),
            )
    candidate = MemoryCandidate(
        id="candidate_related_limit",
        extraction_job_id="job_related_limit",
        thread_id=thread.id,
        kind="user_preference",
        title="shared",
        content="candidate payload",
        aliases=(),
        status="pending",
        non_factual=False,
        sources=(),
        created_at="2026-07-12T00:00:00+00:00",
    )

    with store._connect() as connection:
        first = store._related_active_items(connection, (candidate,))
        second = store._related_active_items(connection, (candidate,))

    assert [str(row["id"]) for row in first] == expected_ids
    assert [str(row["id"]) for row in second] == expected_ids
