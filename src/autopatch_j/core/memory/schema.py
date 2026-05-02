from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any
from uuid import uuid4

from autopatch_j.core.models import IntentType


MEMORY_VERSION = 1
MAX_RECENT_TURNS = 12
COMPACTION_RECENT_TURNS = 8
KEEP_RECENT_TURNS_AFTER_COMPACTION = 4
MAX_PROMPT_READY_SUMMARIES = 3
MAX_PROMPT_PENDING_TURNS = 2
MAX_ACTIVE_TOPICS = 8
MAX_LONG_TERM_ITEMS = 50
MAX_SCOPE_PATHS = 10
MAX_USER_TEXT = 1000
MAX_ASSISTANT_TEXT = 2000
MAX_LABEL = 60
MAX_SUMMARY = 300
SOFT_FILE_BYTES = 24 * 1024
HARD_FILE_BYTES = 32 * 1024

ORDINARY_INTENTS = {IntentType.CODE_EXPLAIN, IntentType.GENERAL_CHAT}
LONG_TERM_SIGNALS = (
    "以后",
    "每次",
    "必须",
    "不要",
    "我希望",
    "我不喜欢",
    "优先",
    "规则",
    "守则",
    "记住",
    "默认",
)
PROJECT_SIGNALS = ("项目", "仓库", "模块", "目录", "代码", "工程", "repo", "repository")


class MemorySummaryTrigger(Enum):
    """普通问答记忆摘要触发原因，便于调试和测试具体触发路径。"""

    PENDING_TURNS = auto()
    RECENT_TURNS = auto()
    FILE_SIZE = auto()
    LONG_TERM_SIGNAL = auto()
    PROJECT_CODE_EXPLAIN = auto()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def generate_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid4().hex[:8]}"


def non_empty(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def clip_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\r\n", "\n").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_string_list(raw_values: Any, limit: int, item_limit: int) -> list[str]:
    if not isinstance(raw_values, list):
        return []
    values: list[str] = []
    for raw in raw_values:
        value = clip_text(raw, item_limit)
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def normalize_scope_paths(raw_paths: Any) -> list[str]:
    return normalize_string_list(raw_paths, MAX_SCOPE_PATHS, 240)
