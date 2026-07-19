from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from autopatch_j.llm.diagnostics import format_raw_llm_exception
from autopatch_j.llm.options import LLMCallPurpose

from .contracts import parse_consolidation_response, parse_extraction_response
from .models import ClaimedJobBatch, JobKind
from .prompts import MEMORY_CONSOLIDATION_SYSTEM_PROMPT, MEMORY_EXTRACTION_SYSTEM_PROMPT
from .store import MemoryStore


class MemoryLLM(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        purpose: LLMCallPurpose = LLMCallPurpose.REACT,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PipelineStepResult:
    processed: int
    succeeded: int
    failed: int
    error: str = ""
    processed_job_ids: tuple[str, ...] = ()
    job_kind: str = ""
    spawned_job_ids: tuple[str, ...] = ()


class MemoryPipeline:
    def __init__(self, store: MemoryStore, llm: MemoryLLM, owner: str) -> None:
        self.store = store
        self.llm = llm
        self.owner = owner

    def process_one(
        self,
        *,
        force: bool = False,
        thread_id: str | None = None,
        allowed_job_ids: Collection[str] | None = None,
    ) -> PipelineStepResult | None:
        batch = self.store.claim_extraction_batch(
            self.owner,
            force=force,
            thread_id=thread_id,
            allowed_job_ids=allowed_job_ids,
        )
        if batch is not None:
            return self._process_extraction(batch)
        batch = self.store.claim_consolidation_job(
            self.owner,
            force=force,
            thread_id=thread_id,
            allowed_job_ids=allowed_job_ids,
        )
        if batch is not None:
            return self._process_consolidation(batch)
        return None

    def _process_extraction(self, batch: ClaimedJobBatch) -> PipelineStepResult:
        try:
            payload = self.store.extraction_payload(batch)
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": MEMORY_EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                tools=None,
                purpose=LLMCallPurpose.MEMORY_EXTRACTION,
            )
            result = parse_extraction_response(response.content)
            evidence_turn_ids = tuple(
                str(turn["turn_id"])
                for turn in payload.get("recent_repair_evidence", ())
            )
            candidate_ids = self.store.complete_extraction(
                batch,
                result,
                evidence_turn_ids=evidence_turn_ids,
            )
            spawned_job_ids = self.store.consolidation_job_ids_for_candidates(
                candidate_ids
            )
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=len(batch.jobs),
                failed=0,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.EXTRACTION.value,
                spawned_job_ids=spawned_job_ids,
            )
        except Exception as exc:
            summary = self._failure_summary("extraction", exc)
            self._record_failure(batch, summary)
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=0,
                failed=len(batch.jobs),
                error=summary,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.EXTRACTION.value,
            )

    def _process_consolidation(self, batch: ClaimedJobBatch) -> PipelineStepResult:
        try:
            payload = self.store.consolidation_payload(batch)
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": MEMORY_CONSOLIDATION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                tools=None,
                purpose=LLMCallPurpose.MEMORY_CONSOLIDATION,
            )
            result = parse_consolidation_response(response.content)
            self.store.apply_consolidation(batch, result)
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=len(batch.jobs),
                failed=0,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.CONSOLIDATION.value,
            )
        except Exception as exc:
            summary = self._failure_summary("consolidation", exc)
            self._record_failure(batch, summary)
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=0,
                failed=len(batch.jobs),
                error=summary,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.CONSOLIDATION.value,
            )

    def _record_failure(self, batch: ClaimedJobBatch, summary: str) -> None:
        try:
            self.store.record_job_failure(batch, summary)
        except Exception:
            # clear/lease fencing errors are already the authoritative result; a stale
            # worker must never turn them into a second write.
            return

    @staticmethod
    def _failure_summary(phase: str, exc: Exception) -> str:
        return f"Memory {phase} failed: {format_raw_llm_exception(exc)}"
