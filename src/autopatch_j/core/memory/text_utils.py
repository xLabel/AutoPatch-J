from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .constants import MAX_SCOPE_PATHS


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
