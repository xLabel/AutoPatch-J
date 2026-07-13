from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from uuid import uuid4

from autopatch_j.core.domain import IntentType
from autopatch_j.llm.options import LLMCallDiagnostic

from .constants import (
    MAX_COMPACTION_CHARS,
    MAX_ROUTING_CONTEXT_CHARS,
    ORDINARY_INTENTS,
    WORKER_POLL_SECONDS,
)
from .errors import MemoryStorageError
from .models import (
    ClearResult,
    ExportResult,
    FlushResult,
    ForgetResult,
    JobKind,
    MemoryDetail,
    MemoryItemSummary,
    MemorySearchHit,
    MemoryStatus,
    MemoryThread,
    TurnHandle,
    TurnRecord,
)
from .pipeline import MemoryLLM, MemoryPipeline
from .store import MemoryStore
from .text_utils import compact_text, utc_now


class MemoryManager:
    """Memory v2 facade：SQLite 是唯一事实源，后台处理可恢复。"""

    def __init__(
        self,
        *,
        db_path: Path,
        llm: MemoryLLM | None = None,
        clock: Callable[[], datetime] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        if self.db_path.suffix.lower() != ".db":
            raise ValueError("db_path 必须指向 .db 文件")
        legacy_json_path = self.db_path.with_name("memory.json")
        self._clock = clock or utc_now
        self._llm = llm
        self._worker_id = worker_id or f"memory-worker-{uuid4().hex}"
        self._store: MemoryStore | None = None
        self._initialization_error = ""
        self._pipeline: MemoryPipeline | None = None
        self._wake = Event()
        self._stop = Event()
        self._state_lock = Lock()
        self._process_lock = Lock()
        self._thread: Thread | None = None
        try:
            self._store = MemoryStore(
                self.db_path,
                legacy_json_path=legacy_json_path,
                clock=self._clock,
            )
        except MemoryStorageError as exc:
            self._initialization_error = str(exc)
        if self._store is not None and llm is not None:
            self._pipeline = MemoryPipeline(self._store, llm, self._worker_id)

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            raise MemoryStorageError(self._initialization_error or "Memory database 不可用")
        return self._store

    def ensure_active_thread(self) -> MemoryThread:
        return self.store.ensure_active_thread()

    def start_new_thread(self, expected_thread_id: str | None = None) -> MemoryThread:
        return self.store.start_new_thread(expected_thread_id)

    def begin_turn(
        self,
        *,
        intent: IntentType | str,
        user_text: str,
        scope_paths: list[str] | None = None,
    ) -> TurnHandle:
        intent_value = intent.value if isinstance(intent, IntentType) else str(intent)
        return self.store.begin_turn(
            intent_value,
            user_text,
            self._worker_id,
            scope_paths,
        )

    def complete_turn(self, turn_id: str, *, assistant_text: str) -> TurnRecord:
        result = self.store.complete_turn(turn_id, assistant_text, self._worker_id)
        self.notify_turn_ready(turn_id)
        return result

    def fail_turn(self, turn_id: str, *, error: str) -> TurnRecord:
        del error
        result = self.store.fail_turn(turn_id, self._worker_id)
        self.notify_turn_ready(turn_id)
        return result

    def build_thread_history(
        self,
        thread_id: str | None = None,
        exclude_turn_id: str | None = None,
    ) -> list[dict[str, str]]:
        return self.store.build_thread_history(thread_id, exclude_turn_id)

    def build_routing_context(
        self,
        intent: IntentType | str,
        thread_id: str | None = None,
    ) -> str:
        intent_value = intent.value if isinstance(intent, IntentType) else str(intent)
        ordinary_values = {item.value for item in ORDINARY_INTENTS}
        if intent_value not in ordinary_values:
            return ""
        store = self.store
        compaction = compact_text(
            store.active_thread_compaction(thread_id), MAX_COMPACTION_CHARS
        )
        preferences, decisions, discussions = store.active_items_for_routing(thread_id)
        if not compaction and not preferences and not decisions and not discussions:
            return ""
        lines = [
            "Memory 仅用于讨论连续性，不是源码证据；当前用户指令始终优先。",
            "需要详情时使用 memory_search 和 memory_read，并核对来源。",
        ]
        self._append_index(lines, "明确偏好", preferences, include_synopsis=True)
        self._append_index(lines, "项目决定", decisions, include_synopsis=True)
        self._append_index(lines, "当前讨论索引", discussions, include_synopsis=False)
        if compaction:
            remaining = MAX_ROUTING_CONTEXT_CHARS - len("\n".join(lines)) - 30
            if remaining > 0:
                lines.extend(
                    ("", "### 当前 thread 摘要", compact_text(compaction, remaining))
                )
        rendered = "\n".join(lines)
        if len(rendered) > MAX_ROUTING_CONTEXT_CHARS:
            rendered = rendered[: MAX_ROUTING_CONTEXT_CHARS - 1].rstrip() + "…"
        return rendered

    def _append_index(
        self,
        lines: list[str],
        title: str,
        items: list[MemoryItemSummary],
        *,
        include_synopsis: bool,
    ) -> None:
        if not items:
            return
        lines.extend(("", f"### {title}"))
        for item in items:
            item_title = compact_text(item.title, 80)
            synopsis = compact_text(item.synopsis, 80)
            suffix = f"：{synopsis}" if include_synopsis else ""
            lines.append(f"- `{item.id}` {item_title}{suffix}")

    def latest_diagnostic(self) -> LLMCallDiagnostic | None:
        diagnostics = getattr(self._llm, "diagnostics", None)
        if not isinstance(diagnostics, list) or not diagnostics:
            return None
        latest = diagnostics[-1]
        return latest if isinstance(latest, LLMCallDiagnostic) else None

    def search(
        self,
        query: str,
        limit: int = 5,
        thread_id: str | None = None,
    ) -> list[MemorySearchHit]:
        return self.store.search(query, limit, thread_id)

    def read(self, memory_id: str, thread_id: str | None = None) -> MemoryDetail:
        return self.store.read(memory_id, thread_id)

    def status(self) -> MemoryStatus:
        if self._store is None:
            return MemoryStatus(
                healthy=False,
                degraded=True,
                db_path=self.db_path,
                schema_version=0,
                generation=0,
                active_thread_id=None,
                thread_count=0,
                turn_count=0,
                active_item_count=0,
                pending_jobs=0,
                leased_jobs=0,
                retry_wait_jobs=0,
                last_error=self._initialization_error,
                last_succeeded_at=None,
            )
        try:
            return self._store.status()
        except MemoryStorageError as exc:
            return MemoryStatus(
                healthy=False,
                degraded=True,
                db_path=self.db_path,
                schema_version=0,
                generation=0,
                active_thread_id=None,
                thread_count=0,
                turn_count=0,
                active_item_count=0,
                pending_jobs=0,
                leased_jobs=0,
                retry_wait_jobs=0,
                last_error=str(exc),
                last_succeeded_at=None,
            )

    def list_items(self) -> list[MemoryItemSummary]:
        return self.store.list_items()

    def show_item(self, memory_id: str) -> MemoryDetail:
        return self.store.show_item(memory_id)

    def forget(self, memory_id: str) -> ForgetResult:
        return self.store.forget(memory_id)

    def clear(self) -> ClearResult:
        if self._store is None:
            for path in (
                self.db_path,
                Path(str(self.db_path) + "-wal"),
                Path(str(self.db_path) + "-shm"),
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    raise MemoryStorageError(
                        f"无法删除损坏的 Memory database 工件 {path}: {exc}"
                    ) from exc
            self._store = MemoryStore(self.db_path, clock=self._clock)
            self._initialization_error = ""
            if self._llm is not None:
                self._pipeline = MemoryPipeline(
                    self._store, self._llm, self._worker_id
                )
            result = self.store.clear()
            self.start()
            return result
        return self.store.clear()

    def export(self, export_dir: Path | None = None) -> ExportResult:
        return self.store.export(export_dir)

    def start(self) -> None:
        if self._store is None:
            return
        try:
            self._store.recover_startup()
        except MemoryStorageError:
            # 周期 worker 会继续重试；瞬时锁竞争不应阻止 CLI 启动。
            pass
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(
                target=self._worker_loop,
                name="memory-worker",
                daemon=True,
            )
            self._thread.start()
        self._wake.set()

    def notify_turn_ready(self, turn_id: str) -> None:
        del turn_id
        self._wake.set()

    def flush_once(
        self,
        reason: str,
        thread_id: str | None = None,
    ) -> FlushResult:
        del reason
        if self._store is None:
            return FlushResult(failed=1, errors=(self._initialization_error,))
        if self._pipeline is None:
            return FlushResult(
                pending=self._store.pending_job_count(),
                errors=("Memory LLM 未配置，pending job 已保留",),
            )
        processed = succeeded = failed = 0
        errors: list[str] = []
        with self._process_lock:
            allowed_job_ids = set(self._store.pending_job_ids(thread_id))
            processed_job_ids: set[str] = set()
            while remaining_job_ids := allowed_job_ids - processed_job_ids:
                try:
                    step = self._pipeline.process_one(
                        force=True,
                        thread_id=thread_id,
                        allowed_job_ids=remaining_job_ids,
                    )
                except Exception as exc:
                    failed += 1
                    errors.append(self._safe_worker_error("flush", exc))
                    break
                if step is None:
                    break
                if not step.processed_job_ids:
                    errors.append("Memory flush 无法确认已处理的 job，已停止本轮处理")
                    break
                processed_job_ids.update(step.processed_job_ids)
                if (
                    step.job_kind == JobKind.EXTRACTION.value
                    and step.succeeded > 0
                ):
                    allowed_job_ids.update(step.spawned_job_ids)
                processed += step.processed
                succeeded += step.succeeded
                failed += step.failed
                if step.error:
                    errors.append(compact_text(step.error, 300))
        return FlushResult(
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            pending=self._store.pending_job_count(),
            errors=tuple(errors),
        )

    def close(self) -> None:
        with self._state_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            self._wake.set()
        if thread is not current_thread():
            thread.join()
        with self._state_lock:
            if self._thread is thread:
                self._thread = None

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=WORKER_POLL_SECONDS)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.store.heartbeat_open_turns(self._worker_id)
                self.store.recover_startup()
            except Exception:
                # SQLite 的瞬时锁竞争或短暂 I/O 错误不能杀死 daemon。
                continue
            if self._pipeline is None:
                continue
            with self._process_lock:
                while not self._stop.is_set():
                    try:
                        self.store.heartbeat_open_turns(self._worker_id)
                        step = self._pipeline.process_one(force=False)
                    except Exception:
                        # 下一次 poll 重新 claim；lease/retry 是持久恢复边界。
                        break
                    if step is None:
                        break

    @staticmethod
    def _safe_worker_error(phase: str, exc: Exception) -> str:
        return f"Memory {phase} 暂时失败 ({type(exc).__name__})"
