from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4


_SEPARATOR_RE = re.compile(r"[^\w./:$#@+-]+", re.UNICODE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def iso_from_timestamp(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\r\n", "\n").split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(part for part in _SEPARATOR_RE.split(text) if part)


def retrieval_terms(values: Iterable[str]) -> tuple[str, ...]:
    """Return normalized field values without weakening exact-match semantics."""

    terms: list[str] = []
    seen: set[str] = set()
    for raw in values:
        normalized = normalize_text(raw)
        if normalized and normalized not in seen:
            terms.append(normalized)
            seen.add(normalized)
    return tuple(terms)


def content_terms(value: Any, *, limit: int, item_limit: int) -> tuple[str, ...]:
    """Return bounded normalized tokens for the low-priority content fallback."""

    terms: list[str] = []
    seen: set[str] = set()
    for token in normalize_text(value).split():
        if not token or len(token) > item_limit or token in seen:
            continue
        terms.append(token)
        seen.add(token)
        if len(terms) >= limit:
            break
    return tuple(terms)


def normalize_string_list(raw: Any, *, limit: int, item_limit: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError("expected a list")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("list items must be strings")
        value = compact_text(item, item_limit)
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return tuple(values)
