from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from functools import cache
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from .constants import (
    EXTRACTION_BATCH_SIZE,
    EXTRACTION_MAX_WAIT_SECONDS,
    EXTRACTION_PENDING_THRESHOLD,
    JOB_LEASE_SECONDS,
    MAX_CONTENT_TERM_CHARS,
    MAX_CONTENT_TERMS,
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
    MAX_JOB_ERROR_CHARS,
    MAX_READ_SOURCES,
    MAX_RELATED_ITEMS,
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_QUERY_TOKENS,
    MAX_SEARCH_RESULTS,
    MAX_SOURCE_EXCERPT_CHARS,
    MEMORY_SCHEMA_VERSION,
    RETRY_BACKOFF_SECONDS,
    TURN_LEASE_SECONDS,
)
from .errors import (
    MemoryContractError,
    MemoryCorruptError,
    MemoryLeaseError,
    MemoryNotFoundError,
    MemorySchemaError,
    MemoryStorageError,
    MemoryThreadConflictError,
)
from .models import (
    CandidateSource,
    ClaimedJobBatch,
    ClearResult,
    ConsolidationResult,
    ExportResult,
    ExtractionCandidateInput,
    ExtractionResult,
    ForgetResult,
    JobKind,
    JobStatus,
    MemoryCandidate,
    MemoryDetail,
    MemoryItemSummary,
    MemoryJob,
    MemorySearchHit,
    MemorySource,
    MemoryStatus,
    MemoryThread,
    TurnHandle,
    TurnRecord,
    TurnState,
)
from .text_utils import (
    compact_text,
    content_terms,
    generate_id,
    iso_from_timestamp,
    normalize_text,
    retrieval_terms,
    timestamp,
    utc_now,
)


_CLAUSE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_ACKNOWLEDGEMENT_RE = re.compile(
    r"^(?:"
    r"(?:同意|可以|好|好的|没问题|就这样|就这么做|按这个做|照这个做)"
    r"(?: (?:就这么做|按这个做|照这个做))?"
    r"|同意(?:就这么做|按这个做|照这个做)"
    r"|(?:yes|ok|okay|agreed|sounds good|sure)"
    r"(?: (?:let s do it|go with that|do that|use that))?"
    r")$",
    re.IGNORECASE,
)
_PREFERENCE_SIGNAL_RE = re.compile(
    r"(?:我(?:希望|偏好|更喜欢|习惯|要求|不希望|不喜欢)"
    r"|我的(?:偏好|习惯)|明确偏好|请(?:你)?记住"
    r"|\bi prefer\b|\bmy preference\b|\bi want you to\b|\bplease remember\b)",
    re.IGNORECASE,
)
_DURABLE_PREFERENCE_RE = re.compile(
    r"(?:以后|今后|后续|从现在起|默认|始终|一律|每次|总是|长期"
    r"|\bfrom now on\b|\bgoing forward\b|\bby default\b|\balways\b|\bevery time\b)",
    re.IGNORECASE,
)
_TEMPORARY_REQUEST_RE = re.compile(
    r"(?:这次|本次|仅此(?:次|轮)|当前回答|这条回复"
    r"|\bfor (?:this|the current) (?:answer|reply|turn)\b|\bjust this time\b)",
    re.IGNORECASE,
)
_DECISION_SIGNAL_RE = re.compile(
    r"(?:决定|确定|最终|定为|选用|选择|采用|改为|切换(?:到|为)?"
    r"|不再|废弃|取消|直接(?:删除|移除|保留|使用)|就这么做"
    r"|按.{0,20}(?:方案|方式).{0,10}(?:做|执行)"
    r"|\b(?:we\s+)?decid(?:e|ed)\b|\bfinal decision\b|\bcho(?:ose|se)\b"
    r"|\badopt(?:ed)?\b|\bswitch(?:ed)? to\b|\bdrop(?:ped)?\b|\bremove(?:d)?\b"
    r"|\bkeep\b|\blet s (?:do|use|keep|drop)\b)",
    re.IGNORECASE,
)
_UNDECIDED_RE = re.compile(
    r"(?:未决定|尚未(?:决定|确定)|还没(?:决定|确定)|只是讨论|还在考虑"
    r"|\bnot decided\b|\bhave not decided\b|\bhaven t decided\b|\bstill considering\b)",
    re.IGNORECASE,
)
_META_EXAMPLE_RE = re.compile(
    r"(?:例如|比如|假设|举例|这句话|如果用户说"
    r"|\bfor example\b|\bsuppose\b|\bhypothetical\b|\bif (?:a|the) user says\b)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"[?？]|^(?:是否|要不要|你觉得)|^(?:should|do|does|would|could)\b",
    re.IGNORECASE,
)
_CODE_ARTIFACT_RE = re.compile(
    r"(?:`[^`]+`|[\w./-]+\.(?:java|kt|py|js|ts|xml|gradle|properties|ya?ml|json|toml)\b"
    r"|\b(?:class|method|function|config|dependency|version|pom\.xml|build\.gradle|src/)\b"
    r"|\w+\([^\n)]*\)|(?:类|方法|函数|配置|依赖|版本))",
    re.IGNORECASE,
)
_CURRENT_STATE_RE = re.compile(
    r"(?:当前|现在|目前|已经|配置的是|定义在|位于|调用|返回|包含|使用的是"
    r"|\bcurrently\b|\bright now\b|\balready\b|\bis configured\b|\btargets\b"
    r"|\bis defined\b|\bcalls\b|\breturns\b|\bcontains\b|\buses\b)",
    re.IGNORECASE,
)
_PROPOSAL_SIGNAL_RE = re.compile(
    r"(?:建议|推荐|提议|方案|我们可以|可以(?:采用|使用|改为|保留|移除)|应当|应该|不妨"
    r"|\brecommend\b|\bsuggest\b|\bpropos(?:e|al)\b|\bwe can\b|\bcould\b|\bshould\b"
    r"|\blet s\b)",
    re.IGNORECASE,
)
_ANCHOR_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+/#-]+", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
_ANCHOR_STOP_WORDS = {
    "adopt",
    "always",
    "choose",
    "decided",
    "decision",
    "default",
    "every",
    "final",
    "forward",
    "going",
    "keep",
    "prefer",
    "preference",
    "project",
    "remember",
    "remove",
    "switch",
    "system",
    "using",
}
_ANCHOR_STOP_CJK = {
    "以后",
    "今后",
    "后续",
    "默认",
    "始终",
    "每次",
    "希望",
    "偏好",
    "喜欢",
    "决定",
    "确定",
    "最终",
    "选择",
    "采用",
    "改为",
    "切换",
    "直接",
    "使用",
    "保留",
    "删除",
    "移除",
    "执行",
    "方案",
    "项目",
    "同意",
    "可以",
    "建议",
    "推荐",
}

_JOB_ERROR_TRUNCATION_MARKER = (
    f"\n...[truncated to {MAX_JOB_ERROR_CHARS} characters]"
)


def _evidence_clauses(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _CLAUSE_SPLIT_RE.split(value) if part.strip())


def _is_acknowledgement(value: str) -> bool:
    return bool(_ACKNOWLEDGEMENT_RE.fullmatch(normalize_text(value).rstrip(".")))


def _quoted_clauses(raw: str, quote: str) -> tuple[str, ...]:
    return tuple(
        clause
        for clause in _evidence_clauses(raw)
        if quote in clause or clause in quote
    )


def _is_direct_preference(clause: str) -> bool:
    durable = bool(_DURABLE_PREFERENCE_RE.search(clause))
    if (
        _QUESTION_RE.search(clause)
        or _META_EXAMPLE_RE.search(clause)
        or (_TEMPORARY_REQUEST_RE.search(clause) and not durable)
        or _is_obvious_current_code_fact(clause)
    ):
        return False
    return bool(_PREFERENCE_SIGNAL_RE.search(clause) or durable)


def _is_direct_decision(clause: str) -> bool:
    if (
        _QUESTION_RE.search(clause)
        or _META_EXAMPLE_RE.search(clause)
        or _UNDECIDED_RE.search(clause)
        or _is_obvious_current_code_fact(clause)
    ):
        return False
    return bool(_DECISION_SIGNAL_RE.search(clause))


def _is_obvious_current_code_fact(value: str) -> bool:
    if _DECISION_SIGNAL_RE.search(value):
        return False
    return bool(_CODE_ARTIFACT_RE.search(value) and _CURRENT_STATE_RE.search(value))


def _semantic_anchors(value: str) -> set[str]:
    normalized = normalize_text(value)
    anchors = {
        token
        for token in _ANCHOR_TOKEN_RE.findall(normalized)
        if token not in _ANCHOR_STOP_WORDS
        and (len(token) >= 3 or any(character.isdigit() for character in token))
    }
    for run in _CJK_RUN_RE.findall(normalized):
        for index in range(len(run) - 1):
            anchor = run[index : index + 2]
            if anchor not in _ANCHOR_STOP_CJK:
                anchors.add(anchor)
    return anchors


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    generation INTEGER NOT NULL DEFAULT 1,
    last_error TEXT NOT NULL DEFAULT '',
    last_succeeded_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    compaction TEXT NOT NULL DEFAULT '',
    compaction_sequence INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    archived_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_memory_thread
    ON threads(status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    intent TEXT NOT NULL CHECK (intent IN ('code_explain', 'general_chat')),
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL DEFAULT '',
    scope_paths_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL CHECK (state IN ('open', 'completed', 'failed', 'interrupted')),
    lease_owner TEXT NOT NULL,
    lease_expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(thread_id, sequence)
);
CREATE INDEX IF NOT EXISTS turns_thread_sequence ON turns(thread_id, sequence);

CREATE TABLE IF NOT EXISTS memory_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('extraction', 'consolidation')),
    thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'leased', 'retry_wait', 'succeeded', 'succeeded_no_output')
    ),
    generation INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at REAL,
    next_retry_at REAL,
    last_error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_due
    ON memory_jobs(kind, status, next_retry_at, created_at);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT PRIMARY KEY,
    extraction_job_id TEXT NOT NULL REFERENCES memory_jobs(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (
        kind IN ('user_preference', 'project_decision', 'discussion_context')
    ),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'consolidated', 'rejected', 'suppressed')
    ),
    non_factual INTEGER NOT NULL CHECK (non_factual IN (0, 1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    CHECK (
        (kind = 'discussion_context' AND non_factual = 1)
        OR (kind IN ('user_preference', 'project_decision') AND non_factual = 0)
    )
);
CREATE INDEX IF NOT EXISTS candidates_status ON memory_candidates(status, created_at);

CREATE TABLE IF NOT EXISTS candidate_sources (
    candidate_id TEXT NOT NULL REFERENCES memory_candidates(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    quote TEXT NOT NULL,
    PRIMARY KEY(candidate_id, turn_id, role, quote)
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN ('user_preference', 'project_decision', 'discussion_context')
    ),
    thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    synopsis TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'forgotten')),
    non_factual INTEGER NOT NULL CHECK (non_factual IN (0, 1)),
    replaced_by_id TEXT REFERENCES memory_items(id),
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(logical_id, revision),
    CHECK (
        (kind = 'discussion_context' AND thread_id IS NOT NULL AND non_factual = 1)
        OR (
            kind IN ('user_preference', 'project_decision')
            AND thread_id IS NULL
            AND non_factual = 0
        )
    )
);
CREATE INDEX IF NOT EXISTS items_visible ON memory_items(status, kind, updated_at);
CREATE INDEX IF NOT EXISTS active_repo_items
    ON memory_items(updated_at DESC, id)
    WHERE status = 'active'
      AND kind IN ('user_preference', 'project_decision');
CREATE INDEX IF NOT EXISTS active_discussion_items
    ON memory_items(thread_id, updated_at DESC, id)
    WHERE status = 'active' AND kind = 'discussion_context';

CREATE TABLE IF NOT EXISTS memory_item_candidates (
    item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL REFERENCES memory_candidates(id) ON DELETE RESTRICT,
    PRIMARY KEY(item_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS memory_terms (
    item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    term_type TEXT NOT NULL CHECK (term_type IN ('title', 'alias', 'keyword', 'content')),
    PRIMARY KEY(item_id, term, term_type)
);
CREATE INDEX IF NOT EXISTS terms_lookup
    ON memory_terms(term_type, term COLLATE BINARY, item_id);
"""


_SCHEMA_MANIFEST_QUERY = """
SELECT type, name, tbl_name, sql
FROM sqlite_master
WHERE type IN ('table', 'index', 'view', 'trigger')
  AND name NOT GLOB 'sqlite_*'
ORDER BY type, name
"""


def _schema_manifest(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3] or ""),
        )
        for row in connection.execute(_SCHEMA_MANIFEST_QUERY).fetchall()
    )


@cache
def _expected_schema_manifest() -> tuple[tuple[str, str, str, str], ...]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.executescript(_SCHEMA_SQL)
        return _schema_manifest(connection)
    finally:
        connection.close()


class MemoryStore:
    """SQLite v2 repository；每次操作使用短连接和显式事务。"""

    def __init__(
        self,
        db_path: Path,
        *,
        legacy_json_path: Path | None = None,
        clock: Callable[[], datetime] = utc_now,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.db_path = Path(db_path)
        self.legacy_json_path = legacy_json_path or self.db_path.with_name("memory.json")
        self.clock = clock
        self.busy_timeout_ms = busy_timeout_ms
        self._delete_legacy_json()
        self._initialize()

    def _delete_legacy_json(self) -> None:
        try:
            self.legacy_json_path.unlink(missing_ok=True)
        except OSError as exc:
            raise MemoryStorageError(
                f"无法删除旧 Memory 文件 {self.legacy_json_path}: {exc}"
            ) from exc

    def _initialize(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect(enable_wal=False) as connection:
                version = self._validate_database_identity_for_initialization(
                    connection
                )
                if version == MEMORY_SCHEMA_VERSION:
                    self._validate_v2_database(connection)
            with self._connect(enable_wal=False) as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    version = self._validate_database_identity_for_initialization(
                        connection
                    )
                    if version == 0:
                        self._create_v2_database(connection)
                    self._validate_v2_database(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            with self._connect():
                pass
        except MemoryStorageError:
            raise
        except sqlite3.DatabaseError as exc:
            raise MemoryCorruptError(f"无法初始化 Memory database: {exc}") from exc
        except OSError as exc:
            raise MemoryStorageError(f"无法初始化 Memory database: {exc}") from exc

    def _create_v2_database(self, connection: sqlite3.Connection) -> None:
        for statement in _SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
        now = self._now()
        connection.execute(
            """
            INSERT INTO memory_meta(id, generation, created_at, updated_at)
            VALUES (1, 1, ?, ?)
            """,
            (now, now),
        )
        connection.execute(f"PRAGMA user_version={MEMORY_SCHEMA_VERSION}")
        self._create_active_thread_in_transaction(connection, now)

    def _validate_database_identity_for_initialization(
        self, connection: sqlite3.Connection
    ) -> int:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, MEMORY_SCHEMA_VERSION):
            raise MemorySchemaError(f"不支持的 Memory schema version: {version}")
        if version != 0:
            return version
        existing = connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table', 'view', 'index', 'trigger')
              AND name NOT GLOB 'sqlite_*'
            ORDER BY type, name
            LIMIT 1
            """
        ).fetchone()
        if existing is not None:
            raise MemorySchemaError(
                "拒绝把 user_version=0 且包含未知 schema object 的 "
                "Memory database 静默升级到 v2: "
                f"{existing['type']}:{existing['name']}"
            )
        return version

    def _validate_v2_database(self, connection: sqlite3.Connection) -> None:
        check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        if check != "ok":
            raise MemoryCorruptError(f"Memory database quick_check 失败: {check}")
        self._verify_schema(connection)
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            first = foreign_key_errors[0]
            raise MemoryCorruptError(
                "Memory database foreign_key_check 失败: "
                f"table={first[0]}, rowid={first[1]}"
            )
        self._require_bootstrap_state(connection)

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        expected = {
            (entry[0], entry[1]): entry for entry in _expected_schema_manifest()
        }
        actual = {(entry[0], entry[1]): entry for entry in _schema_manifest(connection)}
        changed = sorted(
            key
            for key in expected.keys() & actual.keys()
            if expected[key] != actual[key]
        )
        if changed:
            objects = ", ".join(f"{kind}:{name}" for kind, name in changed)
            raise MemorySchemaError(f"Memory schema object 结构不一致: {objects}")
        missing = sorted(expected.keys() - actual.keys())
        if missing:
            objects = ", ".join(f"{kind}:{name}" for kind, name in missing)
            raise MemorySchemaError(f"Memory schema 缺少 object: {objects}")
        unexpected = sorted(actual.keys() - expected.keys())
        if unexpected:
            objects = ", ".join(f"{kind}:{name}" for kind, name in unexpected)
            raise MemorySchemaError(f"Memory schema 包含未知 object: {objects}")

    @contextmanager
    def _connect(self, *, enable_wal: bool = True) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.db_path,
                timeout=self.busy_timeout_ms / 1_000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=NORMAL")
            if enable_wal:
                self._enable_wal(connection)
            yield connection
        except sqlite3.DatabaseError as exc:
            raise MemoryStorageError(f"Memory database 操作失败: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _operational_connection(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            self._require_bootstrap_state(connection)
            yield connection

    def _enable_wal(self, connection: sqlite3.Connection) -> None:
        deadline = monotonic() + self.busy_timeout_ms / 1_000
        while True:
            try:
                current = connection.execute("PRAGMA journal_mode").fetchone()
                if current is not None and str(current[0]).lower() == "wal":
                    return
                configured = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if configured is None or str(configured[0]).lower() != "wal":
                    raise MemoryStorageError(
                        "Memory database 无法启用 WAL journal mode"
                    )
                return
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                is_lock_error = (
                    isinstance(code, int)
                    and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                )
                remaining = deadline - monotonic()
                if not is_lock_error or remaining <= 0:
                    raise
                sleep(min(0.05, remaining))

    @contextmanager
    def _recovery_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._recovery_transaction() as connection:
            self._require_bootstrap_state(connection)
            yield connection

    def _now(self) -> float:
        return timestamp(self.clock())

    def _create_active_thread_in_transaction(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> sqlite3.Row:
        if connection.execute(
            "SELECT 1 FROM threads WHERE status = 'active'"
        ).fetchone() is not None:
            raise MemoryThreadConflictError("创建 active thread 前已有 active thread")
        thread_id = generate_id("thread")
        connection.execute(
            """
            INSERT INTO threads(id, status, compaction, compaction_sequence, created_at)
            VALUES (?, 'active', '', 0, ?)
            """,
            (thread_id, now),
        )
        return connection.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()

    def _require_active_thread_in_transaction(
        self,
        connection: sqlite3.Connection,
    ) -> sqlite3.Row:
        rows = connection.execute(
            "SELECT * FROM threads WHERE status = 'active' ORDER BY id"
        ).fetchall()
        if len(rows) != 1:
            raise MemorySchemaError(
                "Memory v2 active thread 数量必须为 1，"
                f"实际为 {len(rows)}"
            )
        return rows[0]

    def _require_meta_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        rows = connection.execute("SELECT * FROM memory_meta ORDER BY id").fetchall()
        if len(rows) != 1 or int(rows[0]["id"]) != 1:
            raise MemorySchemaError(
                "Memory v2 memory_meta singleton 缺失或不唯一"
            )
        return rows[0]

    def _require_bootstrap_state(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        return (
            self._require_meta_row(connection),
            self._require_active_thread_in_transaction(connection),
        )

    def ensure_active_thread(self) -> MemoryThread:
        with self._transaction() as connection:
            row = self._require_active_thread_in_transaction(connection)
            return self._thread_from_row(row)

    def start_new_thread(self, expected_thread_id: str | None = None) -> MemoryThread:
        now = self._now()
        with self._transaction() as connection:
            current = self._require_active_thread_in_transaction(connection)
            if expected_thread_id is not None and current["id"] != expected_thread_id:
                raise MemoryThreadConflictError(
                    "active thread 已被其他进程切换，请重新读取状态"
                )
            connection.execute(
                "UPDATE threads SET status = 'archived', archived_at = ? WHERE id = ?",
                (now, current["id"]),
            )
            return self._thread_from_row(
                self._create_active_thread_in_transaction(connection, now)
            )

    def begin_turn(
        self,
        intent: str,
        user_text: str,
        owner: str,
        scope_paths: Sequence[str] | None = None,
    ) -> TurnHandle:
        if intent not in {"code_explain", "general_chat"}:
            raise ValueError(f"repair intent 不得写入 Memory: {intent}")
        if not owner.strip():
            raise ValueError("turn owner 不能为空")
        now = self._now()
        lease_expires_at = now + TURN_LEASE_SECONDS
        turn_id = generate_id("turn")
        with self._transaction() as connection:
            thread = self._require_active_thread_in_transaction(connection)
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM turns WHERE thread_id = ?",
                    (thread["id"],),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO turns(
                    id, thread_id, sequence, intent, user_text, assistant_text,
                    scope_paths_json, state, lease_owner, lease_expires_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, 'open', ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    thread["id"],
                    sequence,
                    intent,
                    user_text,
                    json.dumps(list(scope_paths or ()), ensure_ascii=False),
                    owner,
                    lease_expires_at,
                    now,
                    now,
                ),
            )
        return TurnHandle(
            id=turn_id,
            thread_id=str(thread["id"]),
            sequence=sequence,
            intent=intent,
            lease_owner=owner,
            lease_expires_at=iso_from_timestamp(lease_expires_at) or "",
            created_at=iso_from_timestamp(now) or "",
        )

    def complete_turn(
        self, turn_id: str, assistant_text: str, owner: str
    ) -> TurnRecord:
        return self._finish_turn(
            turn_id, TurnState.COMPLETED.value, assistant_text, owner
        )

    def fail_turn(
        self,
        turn_id: str,
        owner: str,
        state: str = TurnState.FAILED.value,
    ) -> TurnRecord:
        if state not in {TurnState.FAILED.value, TurnState.INTERRUPTED.value}:
            raise ValueError("failed turn state 必须是 failed 或 interrupted")
        return self._finish_turn(turn_id, state, None, owner)

    def _finish_turn(
        self,
        turn_id: str,
        state: str,
        assistant_text: str | None,
        owner: str,
    ) -> TurnRecord:
        now = self._now()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
            if row is None:
                raise MemoryNotFoundError(f"turn 不存在: {turn_id}")
            if row["state"] == TurnState.OPEN.value:
                if (
                    row["lease_owner"] != owner
                    or float(row["lease_expires_at"]) <= now
                ):
                    raise MemoryLeaseError(f"turn lease 已失效: {turn_id}")
                if assistant_text is None:
                    connection.execute(
                        "UPDATE turns SET state = ?, updated_at = ? WHERE id = ?",
                        (state, now, turn_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE turns
                        SET state = ?, assistant_text = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (state, assistant_text, now, turn_id),
                    )
                self._insert_extraction_job(connection, row["thread_id"], turn_id, now)
            elif row["state"] == TurnState.INTERRUPTED.value:
                raise MemoryLeaseError(f"turn lease 已失效并被恢复: {turn_id}")
            elif row["state"] != state:
                raise MemoryStorageError(
                    f"turn {turn_id} 已处于 {row['state']}，不能改为 {state}"
                )
            result = connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
            return self._turn_from_row(result)

    def heartbeat_open_turns(self, owner: str) -> int:
        if not owner.strip():
            raise ValueError("turn owner 不能为空")
        now = self._now()
        with self._transaction() as connection:
            return connection.execute(
                """
                UPDATE turns
                SET lease_expires_at = ?, updated_at = ?
                WHERE state = 'open' AND lease_owner = ? AND lease_expires_at > ?
                """,
                (now + TURN_LEASE_SECONDS, now, owner, now),
            ).rowcount

    def _insert_extraction_job(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        turn_id: str,
        now: float,
    ) -> None:
        generation = self._generation(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO memory_jobs(
                id, kind, thread_id, payload_json, idempotency_key, status,
                generation, created_at, updated_at
            ) VALUES (?, 'extraction', ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                generate_id("job"),
                thread_id,
                json.dumps({"turn_id": turn_id}),
                f"extraction:{turn_id}",
                generation,
                now,
                now,
            ),
        )

    def recover_startup(self) -> int:
        now = self._now()
        recovered = 0
        with self._transaction() as connection:
            self._require_active_thread_in_transaction(connection)
            open_rows = connection.execute(
                """
                SELECT id, thread_id FROM turns
                WHERE state = 'open' AND lease_expires_at <= ?
                """,
                (now,),
            ).fetchall()
            for row in open_rows:
                connection.execute(
                    "UPDATE turns SET state = 'interrupted', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                self._insert_extraction_job(connection, row["thread_id"], row["id"], now)
                recovered += 1
            recovered_jobs = connection.execute(
                """
                UPDATE memory_jobs
                SET status = 'retry_wait', lease_owner = NULL, lease_expires_at = NULL,
                    next_retry_at = ?, updated_at = ?,
                    last_error = CASE
                        WHEN last_error = '' THEN 'lease expired before startup recovery'
                        ELSE last_error
                    END
                WHERE status = 'leased' AND lease_expires_at <= ?
                """,
                (now, now, now),
            ).rowcount
            recovered += recovered_jobs
            if recovered_jobs:
                self._refresh_meta_last_error(connection, now)
        return recovered

    def claim_extraction_batch(
        self,
        owner: str,
        *,
        force: bool = False,
        thread_id: str | None = None,
        allowed_job_ids: Collection[str] | None = None,
    ) -> ClaimedJobBatch | None:
        now = self._now()
        allowed_ids = set(allowed_job_ids) if allowed_job_ids is not None else None
        with self._transaction() as connection:
            self._reclaim_expired_leases(connection, now)
            parameters: list[Any] = []
            thread_clause = ""
            if thread_id is not None:
                thread_clause = " AND j.thread_id = ?"
                parameters.append(thread_id)
            rows = connection.execute(
                f"""
                SELECT j.*, t.sequence AS turn_sequence
                FROM memory_jobs j
                JOIN turns t ON t.id = json_extract(j.payload_json, '$.turn_id')
                WHERE j.kind = 'extraction'
                  AND j.status IN ('pending', 'leased', 'retry_wait'){thread_clause}
                ORDER BY j.created_at ASC, j.thread_id ASC, t.sequence ASC
                """,
                tuple(parameters),
            ).fetchall()
            by_thread: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                by_thread.setdefault(str(row["thread_id"]), []).append(row)
            ordered_threads = sorted(
                by_thread.values(), key=lambda value: float(value[0]["created_at"])
            )
            for thread_rows in ordered_threads:
                head = thread_rows[0]
                if allowed_ids is not None and str(head["id"]) not in allowed_ids:
                    continue
                if head["status"] == "leased":
                    continue
                if head["status"] == "retry_wait" and float(head["next_retry_at"]) > now:
                    continue
                eligible_rows: list[sqlite3.Row] = []
                for row in thread_rows:
                    if allowed_ids is not None and str(row["id"]) not in allowed_ids:
                        break
                    if row["status"] == "leased":
                        break
                    if row["status"] == "retry_wait" and float(row["next_retry_at"]) > now:
                        break
                    eligible_rows.append(row)
                due = force or len(eligible_rows) >= EXTRACTION_PENDING_THRESHOLD
                due = due or float(head["created_at"]) <= now - EXTRACTION_MAX_WAIT_SECONDS
                if due:
                    return self._claim_rows(
                        connection,
                        eligible_rows[:EXTRACTION_BATCH_SIZE],
                        owner,
                        now,
                    )
            return None

    def claim_consolidation_job(
        self,
        owner: str,
        *,
        force: bool = False,
        thread_id: str | None = None,
        allowed_job_ids: Collection[str] | None = None,
    ) -> ClaimedJobBatch | None:
        del force
        now = self._now()
        allowed_ids = (
            sorted(set(allowed_job_ids)) if allowed_job_ids is not None else None
        )
        if allowed_ids == []:
            return None
        with self._transaction() as connection:
            self._reclaim_expired_leases(connection, now)
            filters: list[str] = []
            parameters: list[str] = []
            if thread_id is not None:
                filters.append("AND thread_id = ?")
                parameters.append(thread_id)
            if allowed_ids is not None:
                placeholders = ", ".join("?" for _ in allowed_ids)
                filters.append(f"AND id IN ({placeholders})")
                parameters.extend(allowed_ids)
            row = connection.execute(
                f"""
                SELECT * FROM memory_jobs
                WHERE kind = 'consolidation'
                  AND status IN ('pending', 'leased', 'retry_wait')
                  {" ".join(filters)}
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                tuple(parameters),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == JobStatus.LEASED.value:
                return None
            if (
                row["status"] == JobStatus.RETRY_WAIT.value
                and float(row["next_retry_at"]) > now
            ):
                return None
            return self._claim_rows(connection, [row] if row is not None else [], owner, now)

    def _claim_rows(
        self,
        connection: sqlite3.Connection,
        rows: Sequence[sqlite3.Row],
        owner: str,
        now: float,
    ) -> ClaimedJobBatch | None:
        if not rows:
            return None
        generation = self._generation(connection)
        claimed: list[MemoryJob] = []
        for row in rows:
            if int(row["generation"]) != generation:
                connection.execute(
                    """
                    UPDATE memory_jobs SET status = 'succeeded_no_output',
                        completed_at = ?, updated_at = ?, last_error = 'superseded by clear'
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
                continue
            connection.execute(
                """
                UPDATE memory_jobs
                SET status = 'leased', lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (owner, now + JOB_LEASE_SECONDS, now, row["id"]),
            )
            claimed_row = connection.execute(
                "SELECT * FROM memory_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
            claimed.append(self._job_from_row(claimed_row))
        self._refresh_meta_last_error(connection, now)
        if not claimed:
            return None
        return ClaimedJobBatch(tuple(claimed), owner, generation)

    def _reclaim_expired_leases(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> int:
        reclaimed = connection.execute(
            """
            UPDATE memory_jobs
            SET status = 'retry_wait', lease_owner = NULL, lease_expires_at = NULL,
                next_retry_at = ?, updated_at = ?,
                last_error = CASE WHEN last_error = '' THEN 'lease expired' ELSE last_error END
            WHERE status = 'leased' AND lease_expires_at <= ?
            """,
            (now, now, now),
        ).rowcount
        if reclaimed:
            self._refresh_meta_last_error(connection, now)
        return reclaimed

    def extraction_payload(self, batch: ClaimedJobBatch) -> dict[str, Any]:
        self._assert_batch_shape(batch, JobKind.EXTRACTION.value)
        turn_ids = [str(job.payload.get("turn_id", "")) for job in batch.jobs]
        with self._operational_connection() as connection:
            placeholders = ",".join("?" for _ in turn_ids)
            rows = connection.execute(
                f"SELECT * FROM turns WHERE id IN ({placeholders}) ORDER BY sequence",
                tuple(turn_ids),
            ).fetchall()
            if len(rows) != len(turn_ids):
                raise MemoryContractError("extraction job 引用了不存在的 turn")
            thread_id = batch.jobs[0].thread_id
            thread = connection.execute(
                "SELECT * FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            first_sequence = int(rows[0]["sequence"])
            adjacent = connection.execute(
                """
                SELECT * FROM turns
                WHERE thread_id = ? AND sequence < ? AND state = 'completed'
                ORDER BY sequence DESC LIMIT 1
                """,
                (thread_id, first_sequence),
            ).fetchone()
            return {
                "thread_id": thread_id,
                "previous_compaction": thread["compaction"] if thread else "",
                "adjacent_previous_turn": self._turn_payload(adjacent) if adjacent else None,
                "turns": [self._turn_payload(row) for row in rows],
            }

    def complete_extraction(
        self,
        batch: ClaimedJobBatch,
        result: ExtractionResult,
    ) -> tuple[str, ...]:
        self._assert_batch_shape(batch, JobKind.EXTRACTION.value)
        now = self._now()
        candidate_ids: list[str] = []
        with self._transaction() as connection:
            self._validate_lease(connection, batch)
            valid_turns = self._turn_text_lookup(connection, batch)
            eligible_candidates = self._validate_extraction_result(result, valid_turns)
            max_sequence = max(int(item["sequence"]) for item in valid_turns.values())
            connection.execute(
                """
                UPDATE threads
                SET compaction = ?, compaction_sequence = ?
                WHERE id = ? AND compaction_sequence < ?
                """,
                (
                    result.thread_compaction,
                    max_sequence,
                    batch.jobs[0].thread_id,
                    max_sequence,
                ),
            )
            extraction_job_id = batch.jobs[0].id
            for candidate in eligible_candidates:
                candidate_id = generate_id("candidate")
                candidate_ids.append(candidate_id)
                connection.execute(
                    """
                    INSERT INTO memory_candidates(
                        id, extraction_job_id, thread_id, kind, title, content,
                        aliases_json, status, non_factual, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        extraction_job_id,
                        batch.jobs[0].thread_id,
                        candidate.kind,
                        candidate.title,
                        candidate.content,
                        json.dumps(candidate.aliases, ensure_ascii=False),
                        1 if candidate.kind == "discussion_context" else 0,
                        now,
                        now,
                    ),
                )
                for source in candidate.sources:
                    connection.execute(
                        """
                        INSERT INTO candidate_sources(candidate_id, turn_id, role, quote)
                        VALUES (?, ?, ?, ?)
                        """,
                        (candidate_id, source.turn_id, source.role, source.quote),
                    )
            completion = (
                JobStatus.SUCCEEDED.value
                if candidate_ids
                else JobStatus.SUCCEEDED_NO_OUTPUT.value
            )
            self._complete_jobs(connection, batch, completion, now)
            if candidate_ids:
                generation = self._generation(connection)
                key = "consolidation:" + ":".join(sorted(candidate_ids))
                connection.execute(
                    """
                    INSERT INTO memory_jobs(
                        id, kind, thread_id, payload_json, idempotency_key, status,
                        generation, created_at, updated_at
                    ) VALUES (?, 'consolidation', ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        generate_id("job"),
                        batch.jobs[0].thread_id,
                        json.dumps({"candidate_ids": candidate_ids}),
                        key,
                        generation,
                        now,
                        now,
                    ),
                )
            self._record_success(connection, now)
        return tuple(candidate_ids)

    def consolidation_job_ids_for_candidates(
        self,
        candidate_ids: Sequence[str],
    ) -> tuple[str, ...]:
        if not candidate_ids:
            return ()
        key = "consolidation:" + ":".join(sorted(candidate_ids))
        with self._operational_connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM memory_jobs
                WHERE kind = 'consolidation' AND idempotency_key = ?
                ORDER BY created_at ASC, id ASC
                """,
                (key,),
            ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def _turn_text_lookup(
        self,
        connection: sqlite3.Connection,
        batch: ClaimedJobBatch,
    ) -> dict[str, sqlite3.Row]:
        turn_ids = [str(job.payload.get("turn_id", "")) for job in batch.jobs]
        placeholders = ",".join("?" for _ in turn_ids)
        rows = connection.execute(
            f"SELECT * FROM turns WHERE id IN ({placeholders})", tuple(turn_ids)
        ).fetchall()
        lookup = {str(row["id"]): row for row in rows}
        if len(lookup) != len(turn_ids):
            raise MemoryContractError("extraction source turn 不完整")
        first = min(rows, key=lambda row: int(row["sequence"]))
        adjacent = connection.execute(
            """
            SELECT * FROM turns
            WHERE thread_id = ? AND sequence < ? AND state = 'completed'
            ORDER BY sequence DESC LIMIT 1
            """,
            (first["thread_id"], first["sequence"]),
        ).fetchone()
        if adjacent is not None:
            lookup[str(adjacent["id"])] = adjacent
        return lookup

    def _validate_extraction_result(
        self,
        result: ExtractionResult,
        turns: dict[str, sqlite3.Row],
    ) -> tuple[ExtractionCandidateInput, ...]:
        # Validate the complete provenance envelope before filtering semantics. A
        # forged source invalidates the response; an unsupported interpretation
        # only drops that candidate so compaction and other candidates can commit.
        for candidate in result.candidates:
            if not candidate.sources:
                raise MemoryContractError("candidate sources 不得为空")
            for source in candidate.sources:
                turn = turns.get(source.turn_id)
                if turn is None:
                    raise MemoryContractError(
                        f"candidate source turn 不在合法输入中: {source.turn_id}"
                    )
                if source.role not in {"user", "assistant"}:
                    raise MemoryContractError("candidate source role 非法")
                raw = turn["user_text"] if source.role == "user" else turn["assistant_text"]
                if not source.quote or source.quote not in raw:
                    raise MemoryContractError("candidate quote 不是对应 RAW turn 的精确子串")
        return tuple(
            candidate
            for candidate in result.candidates
            if self._candidate_has_explicit_evidence(candidate, turns)
        )

    def _candidate_has_explicit_evidence(
        self,
        candidate: ExtractionCandidateInput,
        turns: dict[str, sqlite3.Row],
    ) -> bool:
        if candidate.kind == "discussion_context":
            return not _is_obvious_current_code_fact(candidate.content)
        if candidate.kind not in {"user_preference", "project_decision"}:
            return False

        user_sources = [source for source in candidate.sources if source.role == "user"]
        if not user_sources:
            return False
        candidate_anchors = _semantic_anchors(candidate.content)
        if not candidate_anchors:
            return False

        acknowledgement_sources: list[CandidateSource] = []
        for source in user_sources:
            raw_user_text = str(turns[source.turn_id]["user_text"])
            if _is_acknowledgement(raw_user_text):
                acknowledgement_sources.append(source)
                continue
            for clause in _quoted_clauses(raw_user_text, source.quote):
                if not candidate_anchors.intersection(_semantic_anchors(clause)):
                    continue
                if candidate.kind == "user_preference" and _is_direct_preference(clause):
                    return True
                if candidate.kind == "project_decision" and _is_direct_decision(clause):
                    return True

        if candidate.kind != "project_decision":
            return False
        return any(
            self._acknowledgement_has_adjacent_proposal(
                source, candidate, candidate_anchors, turns
            )
            for source in acknowledgement_sources
        )

    @staticmethod
    def _acknowledgement_has_adjacent_proposal(
        acknowledgement: CandidateSource,
        candidate: ExtractionCandidateInput,
        candidate_anchors: set[str],
        turns: dict[str, sqlite3.Row],
    ) -> bool:
        acknowledgement_turn = turns[acknowledgement.turn_id]
        expected_sequence = int(acknowledgement_turn["sequence"]) - 1
        expected_thread = str(acknowledgement_turn["thread_id"])
        for source in candidate.sources:
            if source.role != "assistant":
                continue
            proposal_turn = turns[source.turn_id]
            if (
                str(proposal_turn["thread_id"]) != expected_thread
                or int(proposal_turn["sequence"]) != expected_sequence
                or not _PROPOSAL_SIGNAL_RE.search(source.quote)
                or _is_obvious_current_code_fact(source.quote)
            ):
                continue
            if candidate_anchors.intersection(_semantic_anchors(source.quote)):
                return True
        return False

    def consolidation_payload(self, batch: ClaimedJobBatch) -> dict[str, Any]:
        self._assert_batch_shape(batch, JobKind.CONSOLIDATION.value)
        candidate_ids = tuple(str(value) for value in batch.jobs[0].payload.get("candidate_ids", ()))
        with self._operational_connection() as connection:
            candidates = self._load_candidates(connection, candidate_ids)
            if len(candidates) != len(candidate_ids):
                raise MemoryContractError("consolidation job 引用了不存在的 candidate")
            active_items = self._related_active_items(connection, candidates)
            return {
                "candidates": [self._candidate_payload(item) for item in candidates],
                "active_items": [self._item_payload(row) for row in active_items],
                "allowed_operations": ["create", "revise", "supersede", "reject"],
            }

    def apply_consolidation(
        self,
        batch: ClaimedJobBatch,
        result: ConsolidationResult,
    ) -> tuple[str, ...]:
        self._assert_batch_shape(batch, JobKind.CONSOLIDATION.value)
        now = self._now()
        created_ids: list[str] = []
        with self._transaction() as connection:
            self._validate_lease(connection, batch)
            expected_ids = {
                str(value) for value in batch.jobs[0].payload.get("candidate_ids", ())
            }
            candidates = {
                item.id: item for item in self._load_candidates(connection, tuple(expected_ids))
            }
            if set(candidates) != expected_ids:
                raise MemoryContractError("consolidation candidates 已丢失")
            related_target_ids = {
                str(row["id"])
                for row in self._related_active_items(
                    connection, tuple(candidates.values())
                )
            }
            handled: set[str] = set()
            for operation in result.operations:
                operation_ids = set(operation.candidate_ids)
                if not operation_ids or not operation_ids <= expected_ids:
                    raise MemoryContractError("operation 包含不存在的 candidate id")
                if handled & operation_ids:
                    raise MemoryContractError("同一 candidate 被多个 operation 重复处理")
                handled.update(operation_ids)
                if operation.operation == "reject":
                    if operation.target_id is not None:
                        raise MemoryContractError("reject 不允许 target_id")
                    self._set_candidate_status(
                        connection, operation_ids, "rejected", now
                    )
                    continue
                selected = [candidates[item_id] for item_id in operation.candidate_ids]
                kind, thread_id = self._validate_candidate_group(selected)
                if operation.operation == "create":
                    if operation.target_id is not None:
                        raise MemoryContractError("create 不允许 target_id")
                    logical_id = generate_id("memory")
                    revision = 1
                    previous_candidate_ids: set[str] = set()
                    old_id = None
                else:
                    if operation.operation not in {"revise", "supersede"}:
                        raise MemoryContractError(
                            f"不允许的 consolidation operation: {operation.operation}"
                        )
                    if not operation.target_id:
                        raise MemoryContractError(f"{operation.operation} 需要 target_id")
                    if operation.target_id not in related_target_ids:
                        raise MemoryContractError(
                            "target item 不在本次 consolidation payload 的 related active items 中"
                        )
                    target = connection.execute(
                        "SELECT * FROM memory_items WHERE id = ? AND status = 'active'",
                        (operation.target_id,),
                    ).fetchone()
                    if target is None:
                        raise MemoryContractError("target item 不存在或不是 active")
                    if target["kind"] != kind or target["thread_id"] != thread_id:
                        raise MemoryContractError("target item 与 candidates 的作用域不一致")
                    logical_id = str(target["logical_id"])
                    revision = int(target["revision"]) + 1
                    old_id = str(target["id"])
                    previous_candidate_ids = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT candidate_id FROM memory_item_candidates WHERE item_id = ?",
                            (old_id,),
                        ).fetchall()
                    }
                if not operation.title or not operation.content or not operation.synopsis:
                    raise MemoryContractError("create/revise/supersede 缺少 item 正文")
                item_id = f"{logical_id}_r{revision}"
                connection.execute(
                    """
                    INSERT INTO memory_items(
                        id, logical_id, revision, kind, thread_id, title, content,
                        synopsis, status, non_factual, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        item_id,
                        logical_id,
                        revision,
                        kind,
                        thread_id,
                        operation.title,
                        operation.content,
                        operation.synopsis,
                        1 if kind == "discussion_context" else 0,
                        now,
                        now,
                    ),
                )
                if old_id is not None:
                    connection.execute(
                        """
                        UPDATE memory_items
                        SET status = 'superseded', replaced_by_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (item_id, now, old_id),
                    )
                all_candidate_ids = previous_candidate_ids | operation_ids
                for candidate_id in all_candidate_ids:
                    connection.execute(
                        """
                        INSERT INTO memory_item_candidates(item_id, candidate_id)
                        VALUES (?, ?)
                        """,
                        (item_id, candidate_id),
                    )
                self._insert_terms(
                    connection,
                    item_id,
                    operation.title,
                    operation.content,
                    operation.aliases,
                    operation.keywords,
                )
                self._set_candidate_status(
                    connection, operation_ids, "consolidated", now
                )
                created_ids.append(item_id)
            if handled != expected_ids:
                raise MemoryContractError("consolidation 必须处理本 job 的全部 candidates")
            completion = (
                JobStatus.SUCCEEDED.value
                if created_ids
                else JobStatus.SUCCEEDED_NO_OUTPUT.value
            )
            self._complete_jobs(connection, batch, completion, now)
            self._record_success(connection, now)
        return tuple(created_ids)

    def record_job_failure(self, batch: ClaimedJobBatch, error: str) -> None:
        now = self._now()
        persisted_error = error
        if len(persisted_error) > MAX_JOB_ERROR_CHARS:
            retained_chars = MAX_JOB_ERROR_CHARS - len(
                _JOB_ERROR_TRUNCATION_MARKER
            )
            persisted_error = (
                persisted_error[:retained_chars] + _JOB_ERROR_TRUNCATION_MARKER
            )
        with self._transaction() as connection:
            self._validate_lease(connection, batch)
            for job in batch.jobs:
                delay = RETRY_BACKOFF_SECONDS[
                    min(max(job.attempt_count, 1) - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                ]
                connection.execute(
                    """
                    UPDATE memory_jobs
                    SET status = 'retry_wait', lease_owner = NULL, lease_expires_at = NULL,
                        next_retry_at = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now + delay, persisted_error, now, job.id),
                )
            self._refresh_meta_last_error(connection, now)

    def pending_job_count(self) -> int:
        with self._operational_connection() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM memory_jobs
                    WHERE status IN ('pending', 'leased', 'retry_wait')
                    """
                ).fetchone()[0]
            )

    def pending_job_ids(self, thread_id: str | None = None) -> tuple[str, ...]:
        parameters: tuple[str, ...] = ()
        thread_clause = ""
        if thread_id is not None:
            thread_clause = " AND thread_id = ?"
            parameters = (thread_id,)
        with self._operational_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id FROM memory_jobs
                WHERE status IN ('pending', 'leased', 'retry_wait'){thread_clause}
                ORDER BY created_at ASC, id ASC
                """,
                parameters,
            ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def build_thread_history(
        self,
        thread_id: str | None = None,
        exclude_turn_id: str | None = None,
    ) -> list[dict[str, str]]:
        with self._operational_connection() as connection:
            effective_thread = thread_id or self._active_thread_id(connection)
            if effective_thread is None:
                return []
            parameters: list[Any] = [effective_thread]
            exclude = ""
            if exclude_turn_id is not None:
                exclude = " AND id <> ?"
                parameters.append(exclude_turn_id)
            rows = connection.execute(
                f"""
                SELECT * FROM turns
                WHERE thread_id = ? AND state = 'completed'{exclude}
                ORDER BY sequence DESC LIMIT ?
                """,
                (*parameters, MAX_HISTORY_TURNS),
            ).fetchall()[::-1]
        messages: list[dict[str, str]] = []
        total = 0
        for row in reversed(rows):
            user_text = str(row["user_text"])
            assistant_text = str(row["assistant_text"])
            size = len(user_text) + len(assistant_text)
            remaining = MAX_HISTORY_CHARS - total
            if size > remaining:
                if messages or remaining < 2:
                    break
                user_text, assistant_text = self._clip_turn_pair(
                    user_text, assistant_text, remaining
                )
            pair = [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
            messages[0:0] = pair
            total += len(user_text) + len(assistant_text)
        return messages

    def _clip_turn_pair(
        self,
        user_text: str,
        assistant_text: str,
        budget: int,
    ) -> tuple[str, str]:
        user_budget = min(len(user_text), budget // 2)
        assistant_budget = min(len(assistant_text), budget - user_budget)
        remaining = budget - user_budget - assistant_budget
        if remaining and len(user_text) > user_budget:
            extra = min(remaining, len(user_text) - user_budget)
            user_budget += extra
            remaining -= extra
        if remaining and len(assistant_text) > assistant_budget:
            assistant_budget += min(remaining, len(assistant_text) - assistant_budget)
        return (
            self._clip_raw_projection(user_text, user_budget),
            self._clip_raw_projection(assistant_text, assistant_budget),
        )

    def _clip_raw_projection(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        if limit <= 1:
            return text[:limit]
        return text[: limit - 1] + "…"

    def active_thread_compaction(self, thread_id: str | None = None) -> str:
        with self._operational_connection() as connection:
            effective_thread = thread_id or self._active_thread_id(connection)
            if effective_thread is None:
                return ""
            row = connection.execute(
                "SELECT compaction FROM threads WHERE id = ?", (effective_thread,)
            ).fetchone()
            return str(row[0]) if row else ""

    def list_items(self) -> list[MemoryItemSummary]:
        with self._operational_connection() as connection:
            active_thread = self._active_thread_id(connection)
            rows = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE status = 'active'
                  AND (kind <> 'discussion_context' OR thread_id = ?)
                ORDER BY updated_at DESC, id ASC
                """,
                (active_thread,),
            ).fetchall()
            return [self._summary_from_row(row) for row in rows]

    def search(
        self,
        query: str,
        limit: int = MAX_SEARCH_RESULTS,
        thread_id: str | None = None,
    ) -> list[MemorySearchHit]:
        normalized_query = normalize_text(query)
        if not normalized_query or len(normalized_query) > MAX_SEARCH_QUERY_CHARS:
            return []
        bounded_limit = min(max(limit, 0), MAX_SEARCH_RESULTS)
        if bounded_limit == 0:
            return []
        prefix_upper_bound = normalized_query + "\U0010ffff"
        query_tokens = content_terms(
            normalized_query,
            limit=MAX_SEARCH_QUERY_TOKENS,
            item_limit=MAX_CONTENT_TERM_CHARS,
        )
        with self._connect() as connection:
            rows = self._search_exact_and_prefix(
                connection,
                normalized_query,
                prefix_upper_bound,
                bounded_limit,
                thread_id,
            )
            selected_ids = [str(row["id"]) for row in rows]
            if len(rows) < bounded_limit:
                substring_rows = self._search_substring(
                    connection,
                    normalized_query,
                    prefix_upper_bound,
                    bounded_limit - len(rows),
                    selected_ids,
                    thread_id,
                )
                rows.extend(substring_rows)
                selected_ids.extend(str(row["id"]) for row in substring_rows)
            if len(rows) < bounded_limit and query_tokens:
                rows.extend(
                    self._search_content_terms(
                        connection,
                        query_tokens,
                        bounded_limit - len(rows),
                        selected_ids,
                        thread_id,
                    )
                )
        return [
            MemorySearchHit(
                id=str(row["id"]),
                kind=str(row["kind"]),
                title=str(row["title"]),
                synopsis=str(row["synopsis"]),
                match_type=str(row["match_type"]),
            )
            for row in rows
        ]

    def _search_exact_and_prefix(
        self,
        connection: sqlite3.Connection,
        query: str,
        prefix_upper_bound: str,
        limit: int,
        thread_id: str | None,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            WITH matches AS (
                SELECT
                    i.id, i.kind, i.title, i.synopsis, i.updated_at,
                    mt.term_type, mt.term,
                    CASE
                        WHEN mt.term = :query THEN
                            40 + CASE mt.term_type
                                WHEN 'title' THEN 3
                                WHEN 'alias' THEN 2
                                ELSE 1
                            END
                        ELSE
                            30 + CASE mt.term_type
                                WHEN 'title' THEN 3
                                WHEN 'alias' THEN 2
                                ELSE 1
                            END
                    END AS score,
                    CASE WHEN mt.term = :query THEN 'exact' ELSE 'prefix' END
                        AS match_type
                FROM memory_terms AS mt
                JOIN memory_items AS i ON i.id = mt.item_id
                WHERE mt.term_type IN ('title', 'alias', 'keyword')
                  AND mt.term >= :query AND mt.term < :prefix_upper_bound
                  AND i.status = 'active'
                  AND (
                      (i.kind IN ('user_preference', 'project_decision')
                       AND i.thread_id IS NULL)
                      OR (
                          i.kind = 'discussion_context'
                          AND i.thread_id = COALESCE(
                              :thread_id,
                              (SELECT id FROM threads WHERE status = 'active')
                          )
                      )
                  )
            ), ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY id
                    ORDER BY score DESC, term_type ASC, term ASC
                ) AS row_number
                FROM matches
            )
            SELECT id, kind, title, synopsis, updated_at, match_type
            FROM ranked
            WHERE row_number = 1
            ORDER BY score DESC, updated_at DESC, id COLLATE BINARY ASC
            LIMIT :limit
            """,
            {
                "query": query,
                "prefix_upper_bound": prefix_upper_bound,
                "limit": limit,
                "thread_id": thread_id,
            },
        ).fetchall()

    def _search_substring(
        self,
        connection: sqlite3.Connection,
        query: str,
        prefix_upper_bound: str,
        limit: int,
        excluded_ids: Sequence[str],
        thread_id: str | None,
    ) -> list[sqlite3.Row]:
        exclusion, parameters = self._item_exclusion(excluded_ids)
        parameters.update(
            {
                "query": query,
                "prefix_upper_bound": prefix_upper_bound,
                "limit": limit,
                "thread_id": thread_id,
            }
        )
        return connection.execute(
            f"""
            WITH matches AS (
                SELECT
                    i.id, i.kind, i.title, i.synopsis, i.updated_at,
                    mt.term_type, mt.term,
                    20 + CASE mt.term_type
                        WHEN 'title' THEN 3
                        WHEN 'alias' THEN 2
                        ELSE 1
                    END AS score,
                    'substring' AS match_type
                FROM memory_terms AS mt
                JOIN memory_items AS i ON i.id = mt.item_id
                WHERE mt.term_type IN ('title', 'alias', 'keyword')
                  AND instr(mt.term, :query) > 0
                  AND NOT (
                      mt.term >= :query AND mt.term < :prefix_upper_bound
                  )
                  AND i.status = 'active'
                  AND (
                      (i.kind IN ('user_preference', 'project_decision')
                       AND i.thread_id IS NULL)
                      OR (
                          i.kind = 'discussion_context'
                          AND i.thread_id = COALESCE(
                              :thread_id,
                              (SELECT id FROM threads WHERE status = 'active')
                          )
                      )
                  )
                  {exclusion}
            ), ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY id
                    ORDER BY score DESC, term_type ASC, term ASC
                ) AS row_number
                FROM matches
            )
            SELECT id, kind, title, synopsis, updated_at, match_type
            FROM ranked
            WHERE row_number = 1
            ORDER BY score DESC, updated_at DESC, id COLLATE BINARY ASC
            LIMIT :limit
            """,
            parameters,
        ).fetchall()

    def _search_content_terms(
        self,
        connection: sqlite3.Connection,
        query_tokens: Sequence[str],
        limit: int,
        excluded_ids: Sequence[str],
        thread_id: str | None,
    ) -> list[sqlite3.Row]:
        exclusion, parameters = self._item_exclusion(excluded_ids)
        token_placeholders: list[str] = []
        for index, token in enumerate(query_tokens):
            name = f"query_token_{index}"
            token_placeholders.append(f"(:{name})")
            parameters[name] = token
        parameters["limit"] = limit
        parameters["thread_id"] = thread_id
        return connection.execute(
            f"""
            WITH query_terms(term) AS (
                VALUES {", ".join(token_placeholders)}
            )
            SELECT
                i.id, i.kind, i.title, i.synopsis, i.updated_at,
                'content_term' AS match_type
            FROM query_terms AS query_term
            JOIN memory_terms AS mt
              ON mt.term_type = 'content' AND mt.term = query_term.term
            JOIN memory_items AS i ON i.id = mt.item_id
            WHERE i.status = 'active'
              AND (
                  (i.kind IN ('user_preference', 'project_decision')
                   AND i.thread_id IS NULL)
                  OR (
                      i.kind = 'discussion_context'
                      AND i.thread_id = COALESCE(
                          :thread_id,
                          (SELECT id FROM threads WHERE status = 'active')
                      )
                  )
              )
              {exclusion}
            GROUP BY i.id, i.kind, i.title, i.synopsis, i.updated_at
            ORDER BY i.updated_at DESC, i.id COLLATE BINARY ASC
            LIMIT :limit
            """,
            parameters,
        ).fetchall()

    def _item_exclusion(
        self,
        item_ids: Sequence[str],
    ) -> tuple[str, dict[str, Any]]:
        parameters: dict[str, Any] = {}
        placeholders: list[str] = []
        for index, item_id in enumerate(item_ids):
            name = f"excluded_item_{index}"
            placeholders.append(f":{name}")
            parameters[name] = item_id
        if not placeholders:
            return "", parameters
        return f"AND i.id NOT IN ({', '.join(placeholders)})", parameters

    def read(self, memory_id: str, thread_id: str | None = None) -> MemoryDetail:
        now = self._now()
        with self._transaction() as connection:
            effective_thread = thread_id or self._active_thread_id(connection)
            row = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE id = ? AND status = 'active'
                  AND (kind <> 'discussion_context' OR thread_id = ?)
                """,
                (memory_id, effective_thread),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(f"Memory 不存在或当前不可读取: {memory_id}")
            connection.execute(
                """
                UPDATE memory_items
                SET access_count = access_count + 1, last_accessed_at = ?
                WHERE id = ?
                """,
                (now, memory_id),
            )
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (memory_id,)
            ).fetchone()
            return self._detail_from_row(connection, row, bounded=True)

    def show_item(self, memory_id: str) -> MemoryDetail:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(f"Memory 不存在: {memory_id}")
            return self._detail_from_row(connection, row, bounded=False)

    def forget(self, memory_id: str) -> ForgetResult:
        now = self._now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status FROM memory_items WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(f"Memory 不存在: {memory_id}")
            if row["status"] != "forgotten":
                connection.execute(
                    """
                    UPDATE memory_items SET status = 'forgotten', updated_at = ? WHERE id = ?
                    """,
                    (now, memory_id),
                )
                connection.execute(
                    """
                    UPDATE memory_candidates
                    SET status = 'suppressed', updated_at = ?
                    WHERE id IN (
                        SELECT candidate_id FROM memory_item_candidates WHERE item_id = ?
                    )
                    """,
                    (now, memory_id),
                )
            return ForgetResult(memory_id=memory_id, forgotten=True)

    def clear(self) -> ClearResult:
        now = self._now()
        with self._recovery_transaction() as connection:
            deleted_threads = int(connection.execute("SELECT COUNT(*) FROM threads").fetchone()[0])
            deleted_turns = int(connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
            deleted_items = int(connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0])
            deleted_jobs = int(connection.execute("SELECT COUNT(*) FROM memory_jobs").fetchone()[0])
            meta_rows = connection.execute(
                "SELECT id, generation, created_at FROM memory_meta ORDER BY id"
            ).fetchall()
            valid_meta = (
                meta_rows[0]
                if len(meta_rows) == 1 and int(meta_rows[0]["id"]) == 1
                else None
            )
            meta_generation = int(valid_meta["generation"]) if valid_meta else 0
            job_generation = int(
                connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) FROM memory_jobs"
                ).fetchone()[0]
            )
            generation = max(meta_generation, job_generation, 0) + 1
            created_at = float(valid_meta["created_at"]) if valid_meta else now
            connection.execute("DELETE FROM memory_terms")
            connection.execute("DELETE FROM memory_item_candidates")
            connection.execute("DELETE FROM candidate_sources")
            connection.execute("DELETE FROM memory_items")
            connection.execute("DELETE FROM memory_candidates")
            connection.execute("DELETE FROM memory_jobs")
            connection.execute("DELETE FROM turns")
            connection.execute("DELETE FROM threads")
            connection.execute("DELETE FROM memory_meta")
            connection.execute(
                """
                INSERT INTO memory_meta(
                    id, generation, last_error, last_succeeded_at,
                    created_at, updated_at
                ) VALUES (1, ?, '', NULL, ?, ?)
                """,
                (generation, created_at, now),
            )
            active = self._create_active_thread_in_transaction(connection, now)
            return ClearResult(
                generation=generation,
                active_thread_id=str(active["id"]),
                deleted_threads=deleted_threads,
                deleted_turns=deleted_turns,
                deleted_items=deleted_items,
                deleted_jobs=deleted_jobs,
            )

    def export(self, export_dir: Path | None = None) -> ExportResult:
        destination_dir = export_dir or self.db_path.parent / "memory-exports"
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MemoryStorageError(f"无法创建 Memory export 目录: {exc}") from exc
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                snapshot: dict[str, Any] = {
                    "schema_version": MEMORY_SCHEMA_VERSION,
                    "exported_at": iso_from_timestamp(self._now()),
                }
                for table in (
                    "memory_meta",
                    "threads",
                    "turns",
                    "memory_jobs",
                    "memory_candidates",
                    "candidate_sources",
                    "memory_items",
                    "memory_item_candidates",
                    "memory_terms",
                ):
                    rows = connection.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    ).fetchall()
                    snapshot[table] = [dict(row) for row in rows]
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        serialized = json.dumps(snapshot, ensure_ascii=False, indent=2)
        stamp = self.clock().strftime("%Y%m%dT%H%M%S%f")
        suffix = 0
        while True:
            suffix_text = "" if suffix == 0 else f"-{suffix}"
            path = destination_dir / f"memory-{stamp}{suffix_text}.json"
            lock_path = path.with_suffix(path.suffix + ".lock")
            try:
                lock_path.touch(exist_ok=False)
            except FileExistsError:
                suffix += 1
                continue
            except OSError as exc:
                raise MemoryStorageError(
                    f"无法预留 Memory export 文件名: {exc}"
                ) from exc
            if not path.exists():
                break
            try:
                lock_path.unlink()
            except OSError as exc:
                raise MemoryStorageError(
                    f"无法清理 Memory export lock: {exc}"
                ) from exc
            suffix += 1

        temp_path = path.with_name(f".{path.name}.{generate_id('export')}.tmp")
        write_error: OSError | None = None
        try:
            temp_path.write_text(serialized, encoding="utf-8")
            temp_path.replace(path)
        except OSError as exc:
            write_error = exc
        cleanup_error: OSError | None = None
        for artifact in (temp_path, lock_path):
            try:
                artifact.unlink(missing_ok=True)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if write_error is not None:
            raise MemoryStorageError(
                f"无法写入 Memory export: {write_error}"
            ) from write_error
        if cleanup_error is not None:
            raise MemoryStorageError(
                f"无法清理 Memory export 临时工件: {cleanup_error}"
            ) from cleanup_error
        return ExportResult(
            path=path,
            thread_count=len(snapshot["threads"]),
            turn_count=len(snapshot["turns"]),
            item_count=len(snapshot["memory_items"]),
        )

    def status(self) -> MemoryStatus:
        with self._connect() as connection:
            meta = self._require_meta_row(connection)
            active_thread = self._require_active_thread_in_transaction(connection)
            counts = {
                "threads": int(connection.execute("SELECT COUNT(*) FROM threads").fetchone()[0]),
                "turns": int(connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]),
                "items": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM memory_items WHERE status = 'active'"
                    ).fetchone()[0]
                ),
            }
            job_counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM memory_jobs GROUP BY status"
                ).fetchall()
            }
            return MemoryStatus(
                healthy=True,
                degraded=False,
                db_path=self.db_path,
                schema_version=MEMORY_SCHEMA_VERSION,
                generation=int(meta["generation"]),
                active_thread_id=str(active_thread["id"]),
                thread_count=counts["threads"],
                turn_count=counts["turns"],
                active_item_count=counts["items"],
                pending_jobs=job_counts.get("pending", 0),
                leased_jobs=job_counts.get("leased", 0),
                retry_wait_jobs=job_counts.get("retry_wait", 0),
                last_error=str(meta["last_error"]),
                last_succeeded_at=iso_from_timestamp(meta["last_succeeded_at"]),
            )

    def active_items_for_routing(
        self,
        thread_id: str | None = None,
        per_kind_limit: int = 5,
    ) -> tuple[list[MemoryItemSummary], list[MemoryItemSummary], list[MemoryItemSummary]]:
        with self._operational_connection() as connection:
            effective_thread = thread_id or self._active_thread_id(connection)
            groups: list[list[MemoryItemSummary]] = []
            for kind in ("user_preference", "project_decision", "discussion_context"):
                if kind == "discussion_context":
                    rows = connection.execute(
                        """
                        SELECT * FROM memory_items
                        WHERE status = 'active' AND kind = ? AND thread_id = ?
                        ORDER BY updated_at DESC LIMIT ?
                        """,
                        (kind, effective_thread, per_kind_limit),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT * FROM memory_items
                        WHERE status = 'active' AND kind = ?
                        ORDER BY updated_at DESC LIMIT ?
                        """,
                        (kind, per_kind_limit),
                    ).fetchall()
                groups.append([self._summary_from_row(row) for row in rows])
            return groups[0], groups[1], groups[2]

    def _generation(self, connection: sqlite3.Connection) -> int:
        return int(self._require_meta_row(connection)["generation"])

    def _validate_lease(
        self,
        connection: sqlite3.Connection,
        batch: ClaimedJobBatch,
    ) -> None:
        if self._generation(connection) != batch.generation:
            raise MemoryLeaseError("Memory clear generation 已变化，拒绝 stale worker 回写")
        now = self._now()
        for job in batch.jobs:
            row = connection.execute(
                "SELECT * FROM memory_jobs WHERE id = ?", (job.id,)
            ).fetchone()
            if (
                row is None
                or row["status"] != "leased"
                or row["lease_owner"] != batch.owner
                or int(row["generation"]) != batch.generation
                or row["lease_expires_at"] is None
                or float(row["lease_expires_at"]) <= now
            ):
                raise MemoryLeaseError(f"Memory job lease 已失效: {job.id}")

    def _complete_jobs(
        self,
        connection: sqlite3.Connection,
        batch: ClaimedJobBatch,
        status: str,
        now: float,
    ) -> None:
        for job in batch.jobs:
            connection.execute(
                """
                UPDATE memory_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    next_retry_at = NULL, last_error = '', updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, now, now, job.id),
            )

    def _record_success(self, connection: sqlite3.Connection, now: float) -> None:
        latest_error = self._latest_unresolved_job_error(connection)
        connection.execute(
            """
            UPDATE memory_meta
            SET last_succeeded_at = ?, last_error = ?, updated_at = ? WHERE id = 1
            """,
            (now, latest_error, now),
        )

    def _refresh_meta_last_error(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> None:
        connection.execute(
            "UPDATE memory_meta SET last_error = ?, updated_at = ? WHERE id = 1",
            (self._latest_unresolved_job_error(connection), now),
        )

    def _latest_unresolved_job_error(
        self,
        connection: sqlite3.Connection,
    ) -> str:
        row = connection.execute(
            """
            SELECT last_error FROM memory_jobs
            WHERE status IN ('pending', 'leased', 'retry_wait')
              AND last_error <> ''
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        return "" if row is None else str(row["last_error"])

    def _assert_batch_shape(self, batch: ClaimedJobBatch, kind: str) -> None:
        if not batch.jobs or any(job.kind != kind for job in batch.jobs):
            raise MemoryContractError(f"job batch 不是 {kind}")
        thread_ids = {job.thread_id for job in batch.jobs}
        if len(thread_ids) != 1:
            raise MemoryContractError("job batch 跨越多个 thread")

    def _validate_candidate_group(
        self, candidates: Sequence[MemoryCandidate]
    ) -> tuple[str, str | None]:
        kinds = {item.kind for item in candidates}
        if len(kinds) != 1:
            raise MemoryContractError("一个 item operation 不能合并不同 kind")
        kind = next(iter(kinds))
        if kind == "discussion_context":
            threads = {item.thread_id for item in candidates}
            if len(threads) != 1:
                raise MemoryContractError("discussion context 不能跨 thread 合并")
            return kind, next(iter(threads))
        return kind, None

    def _set_candidate_status(
        self,
        connection: sqlite3.Connection,
        candidate_ids: set[str],
        status: str,
        now: float,
    ) -> None:
        placeholders = ",".join("?" for _ in candidate_ids)
        connection.execute(
            f"UPDATE memory_candidates SET status = ?, updated_at = ? WHERE id IN ({placeholders})",
            (status, now, *sorted(candidate_ids)),
        )

    def _insert_terms(
        self,
        connection: sqlite3.Connection,
        item_id: str,
        title: str,
        content: str,
        aliases: Sequence[str],
        keywords: Sequence[str],
    ) -> None:
        term_groups = (
            ("title", retrieval_terms([title])),
            ("alias", retrieval_terms(aliases)),
            ("keyword", retrieval_terms(keywords)),
            (
                "content",
                content_terms(
                    content,
                    limit=MAX_CONTENT_TERMS,
                    item_limit=MAX_CONTENT_TERM_CHARS,
                ),
            ),
        )
        rows: list[tuple[str, str, str]] = []
        for term_type, terms in term_groups:
            for term in terms:
                rows.append((item_id, term, term_type))
        connection.executemany(
            """
            INSERT OR IGNORE INTO memory_terms(item_id, term, term_type)
            VALUES (?, ?, ?)
            """,
            rows,
        )

    def _load_candidates(
        self,
        connection: sqlite3.Connection,
        candidate_ids: Sequence[str],
    ) -> list[MemoryCandidate]:
        if not candidate_ids:
            return []
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = connection.execute(
            f"SELECT * FROM memory_candidates WHERE id IN ({placeholders}) AND status = 'pending'",
            tuple(candidate_ids),
        ).fetchall()
        return [self._candidate_from_row(connection, row) for row in rows]

    def _related_active_items(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[MemoryCandidate],
    ) -> list[sqlite3.Row]:
        lookup_rows = {
            (
                candidate.kind,
                candidate.thread_id,
                term,
                term + "\U0010ffff",
            )
            for candidate in candidates
            for term in retrieval_terms((candidate.title, *candidate.aliases))
        }
        if not lookup_rows:
            return []
        connection.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS memory_candidate_lookup (
                kind TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                term TEXT NOT NULL,
                prefix_upper_bound TEXT NOT NULL,
                PRIMARY KEY(kind, thread_id, term)
            ) WITHOUT ROWID
            """
        )
        connection.execute("DELETE FROM memory_candidate_lookup")
        connection.executemany(
            """
            INSERT INTO memory_candidate_lookup(
                kind, thread_id, term, prefix_upper_bound
            ) VALUES (?, ?, ?, ?)
            """,
            sorted(lookup_rows),
        )
        return connection.execute(
            """
            WITH matches AS (
                SELECT
                    items.*,
                    40 + CASE terms.term_type
                        WHEN 'title' THEN 3
                        WHEN 'alias' THEN 2
                        ELSE 1
                    END AS score,
                    terms.term_type AS matched_term_type,
                    terms.term AS matched_term,
                    lookup.term AS candidate_term
                FROM memory_candidate_lookup AS lookup
                JOIN memory_terms AS terms
                  ON terms.term_type IN ('title', 'alias', 'keyword')
                 AND terms.term = lookup.term
                JOIN memory_items AS items
                  ON items.id = terms.item_id AND items.kind = lookup.kind
                WHERE items.status = 'active'
                  AND (
                      (
                          items.kind IN ('user_preference', 'project_decision')
                          AND items.thread_id IS NULL
                      )
                      OR (
                          items.kind = 'discussion_context'
                          AND items.thread_id = lookup.thread_id
                      )
                  )

                UNION ALL

                SELECT
                    items.*,
                    30 + CASE terms.term_type
                        WHEN 'title' THEN 3
                        WHEN 'alias' THEN 2
                        ELSE 1
                    END AS score,
                    terms.term_type AS matched_term_type,
                    terms.term AS matched_term,
                    lookup.term AS candidate_term
                FROM memory_candidate_lookup AS lookup
                JOIN memory_terms AS terms
                  ON terms.term_type IN ('title', 'alias', 'keyword')
                 AND terms.term >= lookup.term
                 AND terms.term < lookup.prefix_upper_bound
                 AND terms.term <> lookup.term
                JOIN memory_items AS items
                  ON items.id = terms.item_id AND items.kind = lookup.kind
                WHERE items.status = 'active'
                  AND (
                      (
                          items.kind IN ('user_preference', 'project_decision')
                          AND items.thread_id IS NULL
                      )
                      OR (
                          items.kind = 'discussion_context'
                          AND items.thread_id = lookup.thread_id
                      )
                  )

                UNION ALL

                SELECT
                    items.*,
                    20 + CASE terms.term_type
                        WHEN 'title' THEN 3
                        WHEN 'alias' THEN 2
                        ELSE 1
                    END AS score,
                    terms.term_type AS matched_term_type,
                    terms.term AS matched_term,
                    lookup.term AS candidate_term
                FROM memory_candidate_lookup AS lookup
                JOIN memory_terms AS terms
                  ON terms.term_type IN ('title', 'alias', 'keyword')
                 AND instr(terms.term, lookup.term) > 0
                 AND NOT (
                     terms.term >= lookup.term
                     AND terms.term < lookup.prefix_upper_bound
                 )
                JOIN memory_items AS items
                  ON items.id = terms.item_id AND items.kind = lookup.kind
                WHERE items.status = 'active'
                  AND (
                      (
                          items.kind IN ('user_preference', 'project_decision')
                          AND items.thread_id IS NULL
                      )
                      OR (
                          items.kind = 'discussion_context'
                          AND items.thread_id = lookup.thread_id
                      )
                  )
            ), ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY id
                    ORDER BY
                        score DESC,
                        matched_term_type ASC,
                        matched_term ASC,
                        candidate_term ASC
                ) AS row_number
                FROM matches
            )
            SELECT *
            FROM ranked
            WHERE row_number = 1
            ORDER BY score DESC, updated_at DESC, id COLLATE BINARY ASC
            LIMIT ?
            """,
            (MAX_RELATED_ITEMS,),
        ).fetchall()

    def _detail_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        bounded: bool,
    ) -> MemoryDetail:
        limit = MAX_READ_SOURCES if bounded else 10_000
        source_rows = connection.execute(
            """
            SELECT DISTINCT cs.turn_id, cs.role, cs.quote, t.created_at
            FROM memory_item_candidates mic
            JOIN candidate_sources cs ON cs.candidate_id = mic.candidate_id
            JOIN turns t ON t.id = cs.turn_id
            WHERE mic.item_id = ?
            ORDER BY t.created_at ASC, cs.turn_id ASC
            LIMIT ?
            """,
            (row["id"], limit),
        ).fetchall()
        sources = tuple(
            MemorySource(
                turn_id=str(source["turn_id"]),
                role=str(source["role"]),
                quote=(
                    compact_text(source["quote"], MAX_SOURCE_EXCERPT_CHARS)
                    if bounded
                    else str(source["quote"])
                ),
                created_at=iso_from_timestamp(source["created_at"]) or "",
            )
            for source in source_rows
        )
        return MemoryDetail(
            id=str(row["id"]),
            logical_id=str(row["logical_id"]),
            revision=int(row["revision"]),
            kind=str(row["kind"]),
            thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
            title=str(row["title"]),
            content=str(row["content"]),
            synopsis=str(row["synopsis"]),
            status=str(row["status"]),
            non_factual=bool(row["non_factual"]),
            sources=sources,
            access_count=int(row["access_count"]),
            last_accessed_at=iso_from_timestamp(row["last_accessed_at"]),
        )

    def _active_thread_id(self, connection: sqlite3.Connection) -> str | None:
        row = connection.execute(
            "SELECT id FROM threads WHERE status = 'active'"
        ).fetchone()
        return str(row[0]) if row else None

    def _thread_from_row(self, row: sqlite3.Row) -> MemoryThread:
        return MemoryThread(
            id=str(row["id"]),
            status=str(row["status"]),
            compaction=str(row["compaction"]),
            compaction_sequence=int(row["compaction_sequence"]),
            created_at=iso_from_timestamp(row["created_at"]) or "",
            archived_at=iso_from_timestamp(row["archived_at"]),
        )

    def _turn_from_row(self, row: sqlite3.Row) -> TurnRecord:
        return TurnRecord(
            id=str(row["id"]),
            thread_id=str(row["thread_id"]),
            sequence=int(row["sequence"]),
            intent=str(row["intent"]),
            user_text=str(row["user_text"]),
            assistant_text=str(row["assistant_text"]),
            scope_paths=tuple(json.loads(row["scope_paths_json"])),
            state=str(row["state"]),
            lease_owner=str(row["lease_owner"]),
            lease_expires_at=iso_from_timestamp(row["lease_expires_at"]) or "",
            created_at=iso_from_timestamp(row["created_at"]) or "",
            updated_at=iso_from_timestamp(row["updated_at"]) or "",
        )

    def _job_from_row(self, row: sqlite3.Row) -> MemoryJob:
        return MemoryJob(
            id=str(row["id"]),
            kind=str(row["kind"]),
            thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
            payload=json.loads(row["payload_json"]),
            status=str(row["status"]),
            generation=int(row["generation"]),
            attempt_count=int(row["attempt_count"]),
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] else None,
            lease_expires_at=iso_from_timestamp(row["lease_expires_at"]),
            next_retry_at=iso_from_timestamp(row["next_retry_at"]),
            last_error=str(row["last_error"]),
            created_at=iso_from_timestamp(row["created_at"]) or "",
            updated_at=iso_from_timestamp(row["updated_at"]) or "",
        )

    def _candidate_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> MemoryCandidate:
        sources = tuple(
            CandidateSource(
                turn_id=str(source["turn_id"]),
                role=str(source["role"]),
                quote=str(source["quote"]),
            )
            for source in connection.execute(
                "SELECT * FROM candidate_sources WHERE candidate_id = ? ORDER BY rowid",
                (row["id"],),
            ).fetchall()
        )
        return MemoryCandidate(
            id=str(row["id"]),
            extraction_job_id=str(row["extraction_job_id"]),
            thread_id=str(row["thread_id"]),
            kind=str(row["kind"]),
            title=str(row["title"]),
            content=str(row["content"]),
            aliases=tuple(json.loads(row["aliases_json"])),
            status=str(row["status"]),
            non_factual=bool(row["non_factual"]),
            sources=sources,
            created_at=iso_from_timestamp(row["created_at"]) or "",
        )

    def _summary_from_row(self, row: sqlite3.Row) -> MemoryItemSummary:
        return MemoryItemSummary(
            id=str(row["id"]),
            kind=str(row["kind"]),
            title=str(row["title"]),
            synopsis=str(row["synopsis"]),
            updated_at=iso_from_timestamp(row["updated_at"]) or "",
        )

    def _turn_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "turn_id": str(row["id"]),
            "sequence": int(row["sequence"]),
            "intent": str(row["intent"]),
            "user": str(row["user_text"]),
            "assistant": str(row["assistant_text"]),
            "state": str(row["state"]),
            "scope_paths": json.loads(row["scope_paths_json"]),
        }

    def _candidate_payload(self, candidate: MemoryCandidate) -> dict[str, Any]:
        return {
            "id": candidate.id,
            "thread_id": candidate.thread_id,
            "kind": candidate.kind,
            "title": candidate.title,
            "content": candidate.content,
            "aliases": list(candidate.aliases),
            "non_factual": candidate.non_factual,
            "sources": [
                {
                    "turn_id": source.turn_id,
                    "role": source.role,
                    "quote": source.quote,
                }
                for source in candidate.sources
            ],
        }

    def _item_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "logical_id": str(row["logical_id"]),
            "revision": int(row["revision"]),
            "kind": str(row["kind"]),
            "thread_id": row["thread_id"],
            "title": str(row["title"]),
            "content": str(row["content"]),
            "synopsis": str(row["synopsis"]),
        }
