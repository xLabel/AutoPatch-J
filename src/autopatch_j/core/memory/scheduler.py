from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from autopatch_j.llm.client import LLMClient

from .manager import MemoryManager
from .schema import MemorySummaryTrigger
from .summarizer import MemorySummarizer


class MemorySummaryScheduler:
    """单线程调度普通问答摘要，避免短 LLM 阻塞 Agent 主流程。"""

    def __init__(
        self,
        memory_manager: MemoryManager,
        llm: LLMClient,
        repo_root: Path,
    ) -> None:
        self.memory_manager = memory_manager
        self.llm = llm
        self.repo_root = repo_root
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-summary")
        self._lock = Lock()
        self._generation = 0
        self._pending: Future[None] | None = None

    def submit_if_needed(
        self,
        trigger: MemorySummaryTrigger | None,
        last_user_text: str,
    ) -> None:
        if trigger is None:
            return

        with self._lock:
            if self._pending is not None and not self._pending.done():
                return
            generation = self._generation
            self._pending = self._executor.submit(self._run, generation, trigger, last_user_text)

    def discard_pending_results(self) -> None:
        with self._lock:
            self._generation += 1

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _run(
        self,
        generation: int,
        trigger: MemorySummaryTrigger,
        last_user_text: str,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return

        result = MemorySummarizer(
            memory_manager=self.memory_manager,
            llm=self.llm,
            repo_root=self.repo_root,
        ).summarize_delta(
            last_user_text=last_user_text,
            trigger=trigger,
        )
        if result is None:
            return

        with self._lock:
            if generation != self._generation:
                return
            self.memory_manager.apply_delta(
                result.delta,
                allowed_project_evidence_ids=result.allowed_project_evidence_ids,
            )
