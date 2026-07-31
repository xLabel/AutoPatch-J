from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ThreadStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TurnState(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class MemoryKind(str, Enum):
    USER_PREFERENCE = "user_preference"
    PROJECT_DECISION = "project_decision"
    DISCUSSION_CONTEXT = "discussion_context"


class MemoryStrength(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class MemoryOrigin(str, Enum):
    EXPLICIT = "explicit"
    ADOPTED_PROPOSAL = "adopted_proposal"
    INFERRED_REPETITION = "inferred_repetition"


class MemoryRecallMode(str, Enum):
    ALWAYS = "always"
    ON_MATCH = "on_match"


class MemoryRecallLane(str, Enum):
    STANDING = "standing"
    RELEVANT = "relevant"


class MemoryItemStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class CandidateStatus(str, Enum):
    PENDING = "pending"
    CONSOLIDATED = "consolidated"
    REJECTED = "rejected"
    SUPPRESSED = "suppressed"


class JobKind(str, Enum):
    EXTRACTION = "extraction"
    CONSOLIDATION = "consolidation"


class JobStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    SUCCEEDED_NO_OUTPUT = "succeeded_no_output"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MemoryThread:
    id: str
    status: str
    compaction: str
    compaction_sequence: int
    created_at: str
    archived_at: str | None


@dataclass(frozen=True, slots=True)
class TurnHandle:
    id: str
    thread_id: str
    sequence: int
    intent: str
    lease_owner: str
    lease_expires_at: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TurnRecord:
    id: str
    thread_id: str
    sequence: int
    intent: str
    user_text: str
    assistant_text: str
    scope_paths: tuple[str, ...]
    evidence_keys: tuple[str, ...]
    state: str
    lease_owner: str
    lease_expires_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemoryJob:
    id: str
    kind: str
    thread_id: str | None
    payload: dict[str, Any]
    status: str
    generation: int
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: str | None
    next_retry_at: str | None
    last_error: str
    created_at: str
    updated_at: str
    llm_call_count: int = 0


@dataclass(frozen=True, slots=True)
class ClaimedJobBatch:
    jobs: tuple[MemoryJob, ...]
    owner: str
    generation: int


@dataclass(frozen=True, slots=True)
class CandidateSource:
    turn_id: str
    role: str
    quote: str


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    id: str
    extraction_job_id: str
    thread_id: str
    kind: str
    subject: str
    statement: str
    content: str
    strength: str
    origin: str
    recall_mode: str
    applies_to_paths: tuple[str, ...]
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]
    status: str
    sources: tuple[CandidateSource, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class MemorySource:
    turn_id: str
    role: str
    quote: str
    created_at: str


@dataclass(frozen=True, slots=True)
class MemorySearchHit:
    id: str
    kind: str
    subject: str
    statement: str
    match_type: str


@dataclass(frozen=True, slots=True)
class MemoryItemSummary:
    id: str
    kind: str
    subject: str
    statement: str
    strength: str
    origin: str
    recall_mode: str
    applies_to_paths: tuple[str, ...]
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemoryDetail:
    id: str
    logical_id: str
    revision: int
    kind: str
    thread_id: str | None
    subject: str
    statement: str
    content: str
    strength: str
    origin: str
    recall_mode: str
    applies_to_paths: tuple[str, ...]
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]
    status: str
    sources: tuple[MemorySource, ...]
    access_count: int
    last_accessed_at: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemorySummarySnapshot:
    active_thread_id: str
    thread_checkpoint: str
    items: tuple[MemoryDetail, ...]


@dataclass(frozen=True, slots=True)
class MemorySummaryStatus:
    path: Path
    state: str
    active_item_count: int
    last_projected_at: str | None
    last_error: str


@dataclass(frozen=True, slots=True)
class MemorySummaryRefreshResult:
    status: MemorySummaryStatus
    changed: bool


@dataclass(frozen=True, slots=True)
class RecallQuery:
    intent: str
    thread_id: str
    user_text: str
    paths: tuple[str, ...] = ()
    finding_path: str | None = None
    check_id: str | None = None
    finding_message: str | None = None
    patch_path: str | None = None
    bound_finding_id: str | None = None
    search_text: str | None = None


@dataclass(frozen=True, slots=True)
class RecallPolicy:
    intent: str
    thread_id: str
    allowed_kinds: tuple[str, ...]
    allow_recent_history: bool
    allow_thread_checkpoint: bool
    allow_discussion: bool
    durable_token_budget: int
    map_token_budget: int
    max_search_calls: int = 4
    max_search_results: int = 8
    max_read_ids: int = 8


@dataclass(frozen=True, slots=True)
class MemoryMapEntry:
    id: str
    lane: str
    kind: str
    subject: str
    statement: str
    strength: str
    origin: str
    applies_to_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryMap:
    entries: tuple[MemoryMapEntry, ...]
    omitted_count: int
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class MemoryRecallMatch:
    entry: MemoryMapEntry
    match_type: str
    path_specificity: int
    subject_exact: bool
    alias_or_keyword_exact: bool
    content_term_coverage: int
    updated_at: str


@dataclass(slots=True)
class MemoryRequestState:
    query: RecallQuery
    policy: RecallPolicy
    memory_map: MemoryMap
    remaining_tokens: int
    readable_ids: set[str] = field(default_factory=set)
    search_queries: set[str] = field(default_factory=set)
    search_cache: dict[str, tuple[MemorySearchHit, ...]] = field(default_factory=dict)
    read_ids: set[str] = field(default_factory=set)
    read_cache: dict[str, MemoryDetail] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryStatus:
    healthy: bool
    degraded: bool
    db_path: Path
    schema_version: int
    generation: int
    active_thread_id: str | None
    thread_count: int
    turn_count: int
    active_item_count: int
    pending_jobs: int
    leased_jobs: int
    retry_wait_jobs: int
    last_error: str
    last_succeeded_at: str | None
    failed_jobs: int = 0
    last_failure: MemoryFailureDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class MemoryFailureDiagnostic:
    category: str
    phase: str
    stage: str
    attempt_count: int
    llm_call_count: int
    path: str = ""
    rule: str = ""
    observed_type: str = ""
    observed_length: int | None = None
    recognized_keys: tuple[str, ...] = ()
    missing_keys: tuple[str, ...] = ()
    unknown_key_count: int = 0
    response_id: str | None = None
    finish_reason: str | None = None
    exception_type: str | None = None
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "category": self.category,
            "phase": self.phase,
            "stage": self.stage,
            "attempt_count": self.attempt_count,
            "llm_call_count": self.llm_call_count,
        }
        optional = {
            "path": self.path,
            "rule": self.rule,
            "observed_type": self.observed_type,
            "observed_length": self.observed_length,
            "recognized_keys": list(self.recognized_keys),
            "missing_keys": list(self.missing_keys),
            "unknown_key_count": self.unknown_key_count,
            "response_id": self.response_id,
            "finish_reason": self.finish_reason,
            "exception_type": self.exception_type,
            "status_code": self.status_code,
        }
        result.update(
            {
                key: value
                for key, value in optional.items()
                if value not in ("", None, [], 0)
            }
        )
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MemoryFailureDiagnostic:
        return cls(
            category=str(raw.get("category", "unknown")),
            phase=str(raw.get("phase", "unknown")),
            stage=str(raw.get("stage", "primary")),
            attempt_count=int(raw.get("attempt_count", 0)),
            llm_call_count=int(raw.get("llm_call_count", 0)),
            path=str(raw.get("path", "")),
            rule=str(raw.get("rule", "")),
            observed_type=str(raw.get("observed_type", "")),
            observed_length=(
                int(raw["observed_length"])
                if isinstance(raw.get("observed_length"), int)
                else None
            ),
            recognized_keys=tuple(
                str(value) for value in raw.get("recognized_keys", ())
            ),
            missing_keys=tuple(str(value) for value in raw.get("missing_keys", ())),
            unknown_key_count=int(raw.get("unknown_key_count", 0)),
            response_id=_normalized_optional_text(raw.get("response_id")),
            finish_reason=_normalized_optional_text(raw.get("finish_reason")),
            exception_type=_normalized_optional_text(raw.get("exception_type")),
            status_code=(
                int(raw["status_code"])
                if isinstance(raw.get("status_code"), int)
                else None
            ),
        )


def _normalized_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class ForgetResult:
    memory_id: str
    forgotten: bool
    raw_turns_retained: bool = True


@dataclass(frozen=True, slots=True)
class ClearResult:
    generation: int
    active_thread_id: str
    deleted_threads: int
    deleted_turns: int
    deleted_items: int
    deleted_jobs: int


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    thread_count: int
    turn_count: int
    item_count: int


@dataclass(frozen=True, slots=True)
class FlushResult:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    pending: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ExtractionSourceInput:
    turn_id: str
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class ExtractionCandidateInput:
    kind: str
    subject: str
    statement: str
    content: str
    strength: str
    origin: str
    recall_mode: str
    applies_to_paths: tuple[str, ...]
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]
    sources: tuple[CandidateSource, ...]


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    thread_compaction: str
    candidates: tuple[ExtractionCandidateInput, ...]


@dataclass(frozen=True, slots=True)
class ConsolidationOperation:
    operation: str
    candidate_ids: tuple[str, ...]
    target_id: str | None
    kind: str
    subject: str
    statement: str
    content: str
    strength: str
    origin: str
    recall_mode: str
    applies_to_paths: tuple[str, ...]
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    operations: tuple[ConsolidationOperation, ...]
