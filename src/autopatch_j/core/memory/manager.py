from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from uuid import uuid4

from autopatch_j.core.domain import IntentType
from autopatch_j.llm.options import LLMCallDiagnostic
from autopatch_j.llm.context_window import estimate_text_tokens

from .constants import REPAIR_MEMORY_INTENTS, WORKER_POLL_SECONDS
from .errors import MemoryContractError, MemoryNotFoundError, MemoryStorageError
from .models import (
    ClearResult,
    ExportResult,
    FlushResult,
    ForgetResult,
    JobKind,
    MemoryDetail,
    MemoryItemSummary,
    MemoryMap,
    MemoryMapEntry,
    MemoryRequestState,
    MemorySearchHit,
    MemoryStatus,
    MemorySummaryRefreshResult,
    MemorySummaryStatus,
    MemoryThread,
    RecallPolicy,
    RecallQuery,
    TurnHandle,
    TurnRecord,
)
from .pipeline import MemoryLLM, MemoryPipeline, PipelineStepResult
from .store import MemoryStore
from .summary_projection import MemorySummaryProjector
from .text_utils import compact_text, normalize_text, utc_now


_SUMMARY_RETRY_SECONDS = (1.0, 5.0, 30.0, 60.0)


def _clip_text_tokens(text: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    if estimate_text_tokens(text) <= token_budget:
        return text
    marker = "\n… [按 recall 预算截断]"
    content_budget = max(0, token_budget - estimate_text_tokens(marker))
    encoded = text.encode("utf-8")[: content_budget * 3]
    while encoded:
        try:
            return encoded.decode("utf-8").rstrip() + marker
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return ""


class MemoryManager:
    """Memory v3 facade：SQLite 是唯一事实源，后台处理可恢复。"""

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
        self._summary_lock = Lock()
        self._reported_degraded_errors: set[str] = set()
        self._thread: Thread | None = None
        self.summary_path = self.db_path.with_name("memory_summary.md")
        self._summary_projector = MemorySummaryProjector(
            self.summary_path,
            clock=self._clock,
        )
        self._summary_dirty = True
        self._summary_retry_attempt = 0
        self._summary_retry_at = 0.0
        try:
            self._store = MemoryStore(
                self.db_path,
                legacy_json_path=legacy_json_path,
                clock=self._clock,
            )
        except MemoryStorageError as exc:
            self._initialization_error = str(exc)
            self._summary_projector.mark_stale(self._initialization_error)
        if self._store is not None and llm is not None:
            self._pipeline = MemoryPipeline(self._store, llm, self._worker_id)

    def degraded_notice(self, error: BaseException) -> str:
        detail = compact_text(str(error), 500) or type(error).__name__
        with self._state_lock:
            if detail in self._reported_degraded_errors:
                return ""
            self._reported_degraded_errors.add(detail)
        return (
            "Memory degraded：本轮继续执行，但项目 Memory 不可用或本轮结果无法保存。"
            f"可用 /memory status 查看详情。原因：{detail}"
        )

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            raise MemoryStorageError(self._initialization_error or "Memory database 不可用")
        return self._store

    def ensure_active_thread(self) -> MemoryThread:
        return self.store.ensure_active_thread()

    def start_new_thread(self, expected_thread_id: str | None = None) -> MemoryThread:
        result = self.store.start_new_thread(expected_thread_id)
        self._refresh_summary_after_commit()
        return result

    def begin_turn(
        self,
        *,
        intent: IntentType | str,
        user_text: str,
        scope_paths: list[str] | None = None,
        evidence_keys: list[str] | None = None,
    ) -> TurnHandle:
        intent_value = intent.value if isinstance(intent, IntentType) else str(intent)
        return self.store.begin_turn(
            intent_value,
            user_text,
            self._worker_id,
            scope_paths,
            evidence_keys,
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
        max_tokens: int = 384 * 1024,
    ) -> list[dict[str, str]]:
        return self.store.build_thread_history(
            thread_id,
            exclude_turn_id,
            max_tokens,
        )

    def active_thread_checkpoint(
        self,
        thread_id: str | None = None,
        *,
        max_tokens: int,
    ) -> str:
        return _clip_text_tokens(
            self.store.active_thread_compaction(thread_id),
            max_tokens,
        )

    def build_memory_map(
        self,
        query: RecallQuery,
        policy: RecallPolicy,
    ) -> MemoryMap:
        matches = self.store.match_recall(
            query,
            policy,
            include_standing_without_match=True,
        )
        ordered = [
            *(match for match in matches if match.entry.lane == "standing"),
            *(match for match in matches if match.entry.lane == "relevant"),
        ]
        selected: list[MemoryMapEntry] = []
        used_tokens = 0
        for match in ordered:
            entry = match.entry
            rendered = (
                f"{entry.id} {entry.lane} {entry.kind} {entry.subject} "
                f"{entry.statement} {' '.join(entry.applies_to_paths)}"
            )
            entry_tokens = estimate_text_tokens(rendered)
            if used_tokens + entry_tokens > policy.map_token_budget:
                continue
            selected.append(entry)
            used_tokens += entry_tokens
        return MemoryMap(
            entries=tuple(selected),
            omitted_count=len(ordered) - len(selected),
            estimated_tokens=used_tokens,
        )

    def search_recall(
        self,
        query: RecallQuery,
        policy: RecallPolicy,
        *,
        limit: int | None = None,
    ) -> list[MemorySearchHit]:
        bounded_limit = min(
            max(limit if limit is not None else policy.max_search_results, 0),
            policy.max_search_results,
        )
        matches = self.store.match_recall(
            query,
            policy,
            include_standing_without_match=False,
        )
        return [
            MemorySearchHit(
                id=match.entry.id,
                kind=match.entry.kind,
                subject=match.entry.subject,
                statement=match.entry.statement,
                match_type=match.match_type,
            )
            for match in matches[:bounded_limit]
        ]

    def build_recall_policy(
        self,
        *,
        intent: IntentType | str,
        thread_id: str,
        durable_token_budget: int,
        map_token_budget: int,
    ) -> RecallPolicy:
        intent_value = intent.value if isinstance(intent, IntentType) else str(intent)
        repair = intent_value in REPAIR_MEMORY_INTENTS
        return RecallPolicy(
            intent=intent_value,
            thread_id=thread_id,
            allowed_kinds=(
                ("user_preference", "project_decision")
                if repair
                else (
                    "user_preference",
                    "project_decision",
                    "discussion_context",
                )
            ),
            allow_recent_history=not repair,
            allow_thread_checkpoint=not repair,
            allow_discussion=not repair,
            durable_token_budget=durable_token_budget,
            map_token_budget=map_token_budget,
        )

    def open_memory_request(
        self,
        query: RecallQuery,
        policy: RecallPolicy,
    ) -> MemoryRequestState:
        memory_map = self.build_memory_map(query, policy)
        return MemoryRequestState(
            query=query,
            policy=policy,
            memory_map=memory_map,
            remaining_tokens=max(
                0,
                policy.durable_token_budget - memory_map.estimated_tokens,
            ),
            readable_ids={entry.id for entry in memory_map.entries},
        )

    def refresh_memory_request(
        self,
        state: MemoryRequestState,
        *,
        map_token_budget: int | None = None,
    ) -> MemoryMap:
        consumed_tokens = max(
            0,
            state.policy.durable_token_budget
            - state.memory_map.estimated_tokens
            - state.remaining_tokens,
        )
        available_map_tokens = max(
            0,
            state.policy.durable_token_budget - consumed_tokens,
        )
        refresh_policy = replace(
            state.policy,
            map_token_budget=min(
                (
                    state.policy.map_token_budget
                    if map_token_budget is None
                    else max(0, map_token_budget)
                ),
                available_map_tokens,
            ),
        )
        memory_map = self.build_memory_map(state.query, refresh_policy)
        state.memory_map = memory_map
        state.remaining_tokens = max(
            0,
            state.policy.durable_token_budget
            - consumed_tokens
            - memory_map.estimated_tokens,
        )
        state.readable_ids.update(entry.id for entry in memory_map.entries)
        return memory_map

    @staticmethod
    def render_memory_map(memory_map: MemoryMap) -> str:
        if not memory_map.entries:
            return ""
        lines = [
            "## Project Memory",
            "仅在与当前任务相关时采用；当前用户指令和当前源码证据优先。",
        ]
        for lane, title in (("standing", "Standing"), ("relevant", "Relevant")):
            entries = [entry for entry in memory_map.entries if entry.lane == lane]
            if not entries:
                continue
            lines.extend(("", f"### {title}"))
            for entry in entries:
                path_suffix = (
                    f" [paths: {', '.join(entry.applies_to_paths)}]"
                    if entry.applies_to_paths
                    else ""
                )
                lines.append(
                    f"- `{entry.id}` ({entry.kind}, {entry.strength}, {entry.origin}) "
                    f"{entry.statement}{path_suffix}"
                )
        if memory_map.omitted_count:
            lines.append(
                f"\n- 另有 {memory_map.omitted_count} 条因 Map token 预算未自动注入；"
                "仅在确有需要时使用 memory_search。"
            )
        return "\n".join(lines)

    def search_memory_request(
        self,
        state: MemoryRequestState,
        query_text: str,
    ) -> list[MemorySearchHit]:
        normalized = normalize_text(query_text)
        if not normalized:
            return []
        cached = state.search_cache.get(normalized)
        if cached is not None:
            return list(cached)
        if len(state.search_queries) >= state.policy.max_search_calls:
            raise MemoryContractError("本请求的 memory_search 调用额度已用尽")
        state.search_queries.add(normalized)
        combined = replace(
            state.query,
            user_text=f"{query_text}\n{state.query.user_text}".strip(),
        )
        hits = self.search_recall(combined, state.policy)
        selected: list[MemorySearchHit] = []
        for hit in hits:
            tokens = estimate_text_tokens(
                f"{hit.id} {hit.kind} {hit.subject} {hit.statement} {hit.match_type}"
            )
            if tokens > state.remaining_tokens:
                continue
            selected.append(hit)
            state.remaining_tokens -= tokens
            state.readable_ids.add(hit.id)
        state.search_cache[normalized] = tuple(selected)
        return selected

    def read_memory_request(
        self,
        state: MemoryRequestState,
        memory_id: str,
    ) -> MemoryDetail:
        normalized_id = memory_id.strip()
        if normalized_id not in state.readable_ids:
            raise MemoryNotFoundError("Memory ID 未由本请求的 Map/search 暴露")
        cached = state.read_cache.get(normalized_id)
        if cached is not None:
            return cached
        if len(state.read_ids) >= state.policy.max_read_ids:
            raise MemoryContractError("本请求的 memory_read ID 额度已用尽")
        detail = self.store.read_recall(
            normalized_id,
            state.query,
            state.policy,
        )
        bounded, used_tokens = self._bound_detail(detail, state.remaining_tokens)
        state.remaining_tokens -= used_tokens
        state.read_ids.add(normalized_id)
        state.read_cache[normalized_id] = bounded
        return bounded

    def _bound_detail(
        self,
        detail: MemoryDetail,
        token_budget: int,
    ) -> tuple[MemoryDetail, int]:
        full_tokens = estimate_text_tokens(repr(detail))
        if full_tokens <= token_budget:
            return detail, full_tokens
        base = replace(detail, content="", sources=())
        base_tokens = estimate_text_tokens(repr(base))
        if base_tokens > token_budget:
            raise MemoryContractError("durable recall token pool 不足以读取该 Memory")
        remaining = token_budget - base_tokens
        content_budget = max(0, (remaining * 2) // 3)
        content = _clip_text_tokens(detail.content, content_budget)
        used = base_tokens + estimate_text_tokens(content)
        sources = []
        for source in detail.sources:
            source_tokens = estimate_text_tokens(repr(source))
            if used + source_tokens > token_budget:
                break
            sources.append(source)
            used += source_tokens
        return replace(detail, content=content, sources=tuple(sources)), used

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
        return self._diagnostic_store().show_item(memory_id)

    def forget(self, memory_id: str) -> ForgetResult:
        result = self.store.forget(memory_id)
        self._refresh_summary_after_commit()
        return result

    def summary_status(self) -> MemorySummaryStatus:
        with self._summary_lock:
            status = self._summary_projector.status()
            if status.state != "current" and self._store is not None:
                self._summary_dirty = True
                self._summary_retry_at = 0.0
                self._wake.set()
            return status

    def rebuild_summary(self) -> MemorySummaryRefreshResult:
        self._mark_summary_dirty(reset_retry=True)
        return self._refresh_summary(force=True)

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
            self._refresh_summary_after_commit()
            self.start()
            return result
        result = self.store.clear()
        self._refresh_summary_after_commit()
        return result

    def export(self, export_dir: Path | None = None) -> ExportResult:
        return self._diagnostic_store().export(export_dir)

    def _diagnostic_store(self) -> MemoryStore:
        if self._store is not None:
            return self._store
        return MemoryStore.open_recovery_view(
            self.db_path,
            clock=self._clock,
        )

    def start(self) -> None:
        if self._store is None:
            return
        try:
            self._store.recover_startup()
        except MemoryStorageError:
            # 周期 worker 会继续重试；瞬时锁竞争不应阻止 CLI 启动。
            pass
        self._mark_summary_dirty(reset_retry=True)
        self._refresh_summary(force=True)
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
        allowed_job_ids = set(self._store.pending_job_ids(thread_id))
        return self._flush_job_ids(thread_id, allowed_job_ids)

    def flush_thread_watermark(
        self,
        *,
        reason: str,
        thread_id: str,
        wait_seconds: float,
    ) -> FlushResult:
        del reason
        if wait_seconds <= 0:
            raise ValueError("Memory watermark wait_seconds 必须大于 0")
        if self._store is None:
            return FlushResult(failed=1, errors=(self._initialization_error,))
        if self._pipeline is None:
            return FlushResult(
                pending=self._store.pending_job_count(),
                errors=("Memory LLM 未配置，pending job 已保留",),
            )
        allowed_job_ids = set(self._store.pending_job_ids(thread_id))
        if not allowed_job_ids:
            return FlushResult(pending=self._store.pending_job_count())
        completed = Event()
        results: list[FlushResult] = []

        def process_watermark() -> None:
            try:
                results.append(self._flush_job_ids(thread_id, allowed_job_ids))
            except Exception as exc:
                results.append(
                    FlushResult(
                        failed=1,
                        pending=self.store.pending_job_count(),
                        errors=(self._safe_worker_error("watermark flush", exc),),
                    )
                )
            finally:
                completed.set()

        Thread(
            target=process_watermark,
            name="memory-watermark-flush",
            daemon=True,
        ).start()
        if completed.wait(timeout=wait_seconds):
            return results[0]
        return FlushResult(
            pending=self._store.pending_job_count(),
            errors=(
                f"旧 thread Memory watermark 在 {wait_seconds:g}s 内未完成；"
                "任务已保留并在后台继续",
            ),
        )

    def _flush_job_ids(
        self,
        thread_id: str | None,
        allowed_job_ids: set[str],
    ) -> FlushResult:
        assert self._store is not None
        assert self._pipeline is not None
        processed = succeeded = failed = 0
        errors: list[str] = []
        with self._process_lock:
            processed_job_ids: set[str] = set()
            while remaining_job_ids := allowed_job_ids - processed_job_ids:
                try:
                    self.store.heartbeat_open_turns(self._worker_id)
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
                self._refresh_summary_for_step(step)
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
            self._retry_summary_if_due()
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
                    self._refresh_summary_for_step(step)

    def _refresh_summary_for_step(self, step: PipelineStepResult) -> None:
        if step.succeeded > 0:
            self._refresh_summary_after_commit()

    def _refresh_summary_after_commit(self) -> None:
        self._mark_summary_dirty(reset_retry=True)
        self._refresh_summary(force=False)

    def _mark_summary_dirty(self, *, reset_retry: bool) -> None:
        with self._summary_lock:
            self._summary_dirty = True
            self._summary_retry_at = 0.0
            if reset_retry:
                self._summary_retry_attempt = 0
        self._wake.set()

    def _refresh_summary(self, *, force: bool) -> MemorySummaryRefreshResult:
        with self._summary_lock:
            if self._store is None:
                status = self._summary_projector.mark_stale(
                    self._initialization_error or "Memory database 不可用"
                )
                return MemorySummaryRefreshResult(status=status, changed=False)
            try:
                snapshot = self._store.summary_snapshot()
                result = self._summary_projector.refresh(snapshot, force=force)
            except Exception as exc:
                status = self._summary_projector.mark_stale(
                    f"Memory summary refresh failed: {type(exc).__name__}: {exc}"
                )
                self._summary_dirty = True
                delay = _SUMMARY_RETRY_SECONDS[
                    min(self._summary_retry_attempt, len(_SUMMARY_RETRY_SECONDS) - 1)
                ]
                self._summary_retry_attempt += 1
                self._summary_retry_at = monotonic() + delay
                return MemorySummaryRefreshResult(status=status, changed=False)
            self._summary_dirty = False
            self._summary_retry_attempt = 0
            self._summary_retry_at = 0.0
            return result

    def _retry_summary_if_due(self) -> None:
        with self._summary_lock:
            due = self._summary_dirty and monotonic() >= self._summary_retry_at
        if due:
            self._refresh_summary(force=False)

    @staticmethod
    def _safe_worker_error(phase: str, exc: Exception) -> str:
        return f"Memory {phase} 暂时失败 ({type(exc).__name__})"
