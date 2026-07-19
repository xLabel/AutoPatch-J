from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from autopatch_j.core.memory import MemoryManager
from autopatch_j.core.memory.constants import JOB_LEASE_SECONDS, TURN_LEASE_SECONDS
from autopatch_j.core.memory.errors import (
    MemoryContractError,
    MemoryLeaseError,
    MemorySchemaError,
    MemoryStorageError,
)
from autopatch_j.core.memory.models import (
    CandidateSource,
    ConsolidationOperation,
    ConsolidationResult,
    ExtractionCandidateInput,
    ExtractionResult,
    RecallQuery,
)
from autopatch_j.core.memory.store import MemoryStore


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _complete_turn(
    store: MemoryStore,
    user_text: str,
    assistant_text: str,
    *,
    owner: str,
    intent: str = "general_chat",
    paths: tuple[str, ...] = (),
) -> str:
    turn = store.begin_turn(intent, user_text, owner, paths)
    store.complete_turn(turn.id, assistant_text, owner)
    return turn.id


def _seed_candidate(
    store: MemoryStore,
    *,
    owner: str,
    kind: str = "user_preference",
    subject: str = "answer brevity",
    statement: str = "回答默认保持简洁",
    user_text: str = "以后默认保持简洁回答",
    recall_mode: str = "always",
) -> str:
    turn_id = _complete_turn(store, user_text, "收到", owner=f"turn-{owner}")
    batch = store.claim_extraction_batch(f"extract-{owner}", force=True)
    assert batch is not None
    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            thread_compaction=statement,
            candidates=(
                ExtractionCandidateInput(
                    kind=kind,
                    subject=subject,
                    statement=statement,
                    content=statement,
                    strength="soft" if kind == "discussion_context" else "hard",
                    origin="explicit",
                    recall_mode=recall_mode,
                    applies_to_paths=(),
                    aliases=(subject,),
                    keywords=("memory",),
                    sources=(CandidateSource(turn_id, "user", user_text),),
                ),
            ),
        ),
    )
    assert len(candidate_ids) == 1
    return candidate_ids[0]


def _activate_candidate(
    store: MemoryStore,
    candidate_id: str,
    *,
    owner: str,
    kind: str = "user_preference",
    subject: str = "answer brevity",
    statement: str = "回答默认保持简洁",
    recall_mode: str = "always",
) -> str:
    batch = store.claim_consolidation_job(f"consolidate-{owner}", force=True)
    assert batch is not None
    return store.apply_consolidation(
        batch,
        ConsolidationResult(
            operations=(
                ConsolidationOperation(
                    operation="create",
                    candidate_ids=(candidate_id,),
                    target_id=None,
                    kind=kind,
                    subject=subject,
                    statement=statement,
                    content=statement,
                    strength="soft" if kind == "discussion_context" else "hard",
                    origin="explicit",
                    recall_mode=recall_mode,
                    applies_to_paths=(),
                    aliases=(subject,),
                    keywords=("memory",),
                ),
            )
        ),
    )[0]


def test_concurrent_managers_initialize_one_v3_active_thread(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    barrier = Barrier(4)

    def initialize(_: int) -> tuple[bool, str | None]:
        barrier.wait()
        manager = MemoryManager(db_path=db_path)
        return manager.status().healthy, manager.status().active_thread_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(initialize, range(4)))

    assert all(healthy for healthy, _ in statuses)
    assert len({thread_id for _, thread_id in statuses}) == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM threads WHERE status = 'active'"
        ).fetchone()[0] == 1


def test_concurrent_managers_allocate_unique_turn_sequences(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    managers = [
        MemoryManager(db_path=db_path, worker_id="manager-a"),
        MemoryManager(db_path=db_path, worker_id="manager-b"),
    ]

    def write(index: int) -> int:
        manager = managers[index % 2]
        turn = manager.begin_turn(
            intent="general_chat",
            user_text=f"user-{index}",
        )
        manager.complete_turn(turn.id, assistant_text=f"assistant-{index}")
        return turn.sequence

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(write, range(20)))

    assert sorted(sequences) == list(range(1, 21))
    assert managers[0].status().turn_count == 20


def test_turn_lease_rejects_other_owner_and_recovers_expired_turn(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    first = MemoryStore(tmp_path / "memory.db", clock=clock)
    second = MemoryStore(tmp_path / "memory.db", clock=clock)
    turn = first.begin_turn("general_chat", "仍在处理", "owner-a")

    with pytest.raises(MemoryLeaseError):
        second.complete_turn(turn.id, "越权结果", "owner-b")
    assert second.recover_startup() == 0

    clock.advance(TURN_LEASE_SECONDS + 1)
    assert second.recover_startup() == 1
    with pytest.raises(MemoryLeaseError):
        first.complete_turn(turn.id, "过期结果", "owner-a")
    with sqlite3.connect(tmp_path / "memory.db") as connection:
        assert connection.execute(
            "SELECT state FROM turns WHERE id = ?", (turn.id,)
        ).fetchone()[0] == "interrupted"


def test_extraction_batch_is_head_of_line_bounded_and_lease_fenced(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = MemoryStore(tmp_path / "memory.db", clock=clock)
    for index in range(5):
        _complete_turn(
            store,
            f"user-{index}",
            f"assistant-{index}",
            owner=f"turn-{index}",
        )

    stale = store.claim_extraction_batch("worker-a", force=True)
    assert stale is not None
    assert len(stale.jobs) == 4
    assert store.claim_extraction_batch("worker-b", force=True) is None

    clock.advance(JOB_LEASE_SECONDS + 1)
    with pytest.raises(MemoryLeaseError):
        store.complete_extraction(stale, ExtractionResult("stale", ()))
    current = store.claim_extraction_batch("worker-b", force=True)
    assert current is not None
    assert [job.id for job in current.jobs] == [job.id for job in stale.jobs]
    store.complete_extraction(current, ExtractionResult("fresh", ()))
    final_batch = store.claim_extraction_batch("worker-b", force=True)
    assert final_batch is not None
    assert len(final_batch.jobs) == 1


def test_two_stores_cannot_claim_same_consolidation_job(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    first = MemoryStore(db_path)
    second = MemoryStore(db_path)
    _seed_candidate(first, owner="exclusive")
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

    assert sum(batch is not None for batch in claims) == 1


def test_invalid_consolidation_rolls_back_entire_operation_set(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    first_id = _seed_candidate(store, owner="first", subject="answer brevity")
    second_id = _seed_candidate(
        store,
        owner="second",
        subject="answer language",
        statement="回答默认使用中文",
        user_text="以后默认使用中文回答",
    )
    batch = store.claim_consolidation_job("atomic", force=True)
    assert batch is not None

    with pytest.raises(MemoryContractError):
        store.apply_consolidation(
            batch,
            ConsolidationResult(
                operations=(
                    ConsolidationOperation(
                        operation="create",
                        candidate_ids=(first_id,),
                        target_id=None,
                        kind="user_preference",
                        subject="answer brevity",
                        statement="回答默认保持简洁",
                        content="回答默认保持简洁",
                        strength="hard",
                        origin="explicit",
                        recall_mode="always",
                        applies_to_paths=(),
                        aliases=("answer brevity",),
                        keywords=(),
                    ),
                        ConsolidationOperation(
                            operation="merge",
                            candidate_ids=(second_id,),
                            target_id=None,
                            kind=None,
                            subject=None,
                            statement=None,
                            content=None,
                            strength=None,
                            origin=None,
                            recall_mode=None,
                            applies_to_paths=(),
                            aliases=(),
                            keywords=(),
                        ),
                )
            ),
        )

    assert store.list_items() == []
    with sqlite3.connect(tmp_path / "memory.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_candidates WHERE status = 'pending'"
        ).fetchone()[0] == 2


def test_clear_increments_generation_and_fences_stale_worker(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    _complete_turn(store, "pending user", "pending answer", owner="turn")
    stale = store.claim_extraction_batch("stale-worker", force=True)
    assert stale is not None

    result = store.clear()

    assert result.generation == stale.generation + 1
    assert store.status().turn_count == 0
    assert store.status().thread_count == 1
    with pytest.raises(MemoryLeaseError):
        store.complete_extraction(stale, ExtractionResult("late", ()))


def test_unknown_unversioned_schema_is_not_mutated_and_clear_is_explicit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE foreign_data(value TEXT)")
        connection.execute("INSERT INTO foreign_data VALUES ('keep-me')")
    before = db_path.read_bytes()

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert db_path.read_bytes() == before
    exported = manager.export(tmp_path / "exports")
    payload = json.loads(exported.path.read_text(encoding="utf-8"))
    assert payload["foreign_data"] == [{"value": "keep-me"}]

    manager.clear()
    assert manager.status().healthy
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'foreign_data'"
        ).fetchone()[0] == 0


def test_runtime_bootstrap_damage_fails_closed_until_explicit_clear(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    manager = MemoryManager(db_path=db_path)
    store = manager.store
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM memory_meta")

    with pytest.raises(MemorySchemaError, match="memory_meta"):
        store.begin_turn("general_chat", "不得写入", "owner")
    with pytest.raises(MemorySchemaError, match="memory_meta"):
        manager.build_thread_history()
    policy = manager.build_recall_policy(
        intent="general_chat",
        thread_id="missing",
        durable_token_budget=100,
        map_token_budget=50,
    )
    with pytest.raises(MemorySchemaError, match="memory_meta"):
        manager.build_memory_map(
            RecallQuery(
                intent="general_chat",
                thread_id="missing",
                user_text="anything",
            ),
            policy,
        )
    assert manager.status().degraded

    manager.clear()
    assert manager.status().healthy


def test_foreign_key_check_detects_dangling_provenance(tmp_path: Path) -> None:
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


def test_archived_thread_discussion_is_not_recalled_in_new_thread(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = manager.store
    old_thread = manager.ensure_active_thread()
    candidate = _seed_candidate(
        store,
        owner="discussion",
        kind="discussion_context",
        subject="retry strategy discussion",
        statement="下一轮继续讨论 retry backoff 方案",
        user_text="下一轮继续讨论 retry backoff 方案",
        recall_mode="on_match",
    )
    item_id = _activate_candidate(
        store,
        candidate,
        owner="discussion",
        kind="discussion_context",
        subject="retry strategy discussion",
        statement="下一轮继续讨论 retry backoff 方案",
        recall_mode="on_match",
    )

    manager.start_new_thread(expected_thread_id=old_thread.id)
    new_thread = manager.ensure_active_thread()
    policy = manager.build_recall_policy(
        intent="general_chat",
        thread_id=new_thread.id,
        durable_token_budget=1_000,
        map_token_budget=500,
    )
    memory_map = manager.build_memory_map(
        RecallQuery(
            intent="general_chat",
            thread_id=new_thread.id,
            user_text="retry strategy discussion",
        ),
        policy,
    )

    assert item_id not in {entry.id for entry in memory_map.entries}


def test_concurrent_exports_are_unique_complete_snapshots(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", clock=FakeClock())
    _complete_turn(store, "RAW concurrent user", "RAW final answer", owner="turn")
    export_dir = tmp_path / "exports"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.export(export_dir), range(8)))

    assert len({result.path for result in results}) == 8
    for result in results:
        payload = json.loads(result.path.read_text(encoding="utf-8"))
        assert payload["turns"][0]["user_text"] == "RAW concurrent user"
        assert payload["turns"][0]["assistant_text"] == "RAW final answer"
    assert not any(path.suffix in {".tmp", ".lock"} for path in export_dir.iterdir())


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
