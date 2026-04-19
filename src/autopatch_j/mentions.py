from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from autopatch_j.indexer import IndexEntry

MENTION_PATTERN = re.compile(r"(?<!\S)@([^\s]+)")


@dataclass(slots=True)
class MentionCandidate:
    entry: IndexEntry
    score: int


@dataclass(slots=True)
class MentionResolution:
    raw: str
    query: str
    status: str
    selected: IndexEntry | None = None
    candidates: list[MentionCandidate] = field(default_factory=list)


@dataclass(slots=True)
class ParsedPrompt:
    original_text: str
    clean_text: str
    mentions: list[MentionResolution]


def extract_mentions(text: str) -> list[str]:
    return [match.group(1) for match in MENTION_PATTERN.finditer(text)]


def parse_prompt(text: str, index: list[IndexEntry]) -> ParsedPrompt:
    resolutions = [resolve_mention(query, index) for query in extract_mentions(text)]
    clean_text = " ".join(MENTION_PATTERN.sub("", text).split())
    return ParsedPrompt(original_text=text, clean_text=clean_text, mentions=resolutions)


def resolve_mention(query: str, index: list[IndexEntry], limit: int = 5) -> MentionResolution:
    candidates = search_index(index, query, limit=limit)
    if not candidates:
        return MentionResolution(raw=f"@{query}", query=query, status="missing")
    if len(candidates) == 1:
        return MentionResolution(
            raw=f"@{query}",
            query=query,
            status="resolved",
            selected=candidates[0].entry,
            candidates=candidates,
        )

    top_score = candidates[0].score
    second_score = candidates[1].score
    exact_path_matches = [candidate for candidate in candidates if is_exact_path_match(query, candidate.entry)]
    if len(exact_path_matches) == 1:
        return MentionResolution(
            raw=f"@{query}",
            query=query,
            status="resolved",
            selected=exact_path_matches[0].entry,
            candidates=candidates,
        )

    if top_score >= 95 and top_score - second_score >= 8:
        return MentionResolution(
            raw=f"@{query}",
            query=query,
            status="resolved",
            selected=candidates[0].entry,
            candidates=candidates,
        )

    return MentionResolution(
        raw=f"@{query}",
        query=query,
        status="ambiguous",
        candidates=candidates,
    )


def search_index(index: list[IndexEntry], query: str, limit: int = 5) -> list[MentionCandidate]:
    scored: list[MentionCandidate] = []
    for entry in index:
        score = score_entry(query, entry)
        if score > 0:
            scored.append(MentionCandidate(entry=entry, score=score))

    scored.sort(key=lambda item: (-item.score, item.entry.kind, item.entry.path))
    return scored[:limit]


def score_entry(query: str, entry: IndexEntry) -> int:
    query_norm = query.strip().lower()
    path_norm = entry.path.lower()
    name_norm = entry.name.lower()

    if query_norm == path_norm:
        return 100
    if query_norm == name_norm:
        return 96 if entry.kind == "file" else 94
    if path_norm.endswith(query_norm):
        return 92
    if name_norm.startswith(query_norm):
        return 88
    if query_norm in name_norm:
        return 82
    if query_norm in path_norm:
        return 76

    ratio = max(
        SequenceMatcher(None, query_norm, name_norm).ratio(),
        SequenceMatcher(None, query_norm, path_norm).ratio(),
    )
    if ratio < 0.58:
        return 0
    return int(ratio * 70)


def is_exact_path_match(query: str, entry: IndexEntry) -> bool:
    query_norm = query.strip().lower()
    return query_norm == entry.path.lower()


def mention_completion_candidates(
    index: list[IndexEntry],
    query: str,
    recent_paths: list[str] | None = None,
    limit: int = 10,
) -> list[IndexEntry]:
    query_norm = query.strip()
    if not query_norm:
        return default_completion_candidates(index, recent_paths=recent_paths, limit=limit)

    selected: list[IndexEntry] = []
    seen_paths: set[str] = set()
    for candidate in search_index(index, query_norm, limit=max(limit * 3, limit)):
        if candidate.entry.path in seen_paths:
            continue
        selected.append(candidate.entry)
        seen_paths.add(candidate.entry.path)
        if len(selected) >= limit:
            break
    return selected


def default_completion_candidates(
    index: list[IndexEntry],
    recent_paths: list[str] | None = None,
    limit: int = 10,
) -> list[IndexEntry]:
    selected: list[IndexEntry] = []
    seen_paths: set[str] = set()
    by_path = {entry.path: entry for entry in index}

    for path in recent_paths or []:
        entry = by_path.get(path)
        if entry is None or entry.path in seen_paths:
            continue
        selected.append(entry)
        seen_paths.add(entry.path)
        if len(selected) >= limit:
            return selected

    ordered_index = sorted(
        index,
        key=lambda entry: (
            entry.kind != "file",
            not entry.is_java,
            entry.path,
        ),
    )
    for entry in ordered_index:
        if entry.path in seen_paths:
            continue
        selected.append(entry)
        seen_paths.add(entry.path)
        if len(selected) >= limit:
            break
    return selected


def build_mention_completions(
    index: list[IndexEntry],
    token: str,
    recent_paths: list[str] | None = None,
    limit: int = 10,
) -> list[str]:
    if not token.startswith("@"):
        return []

    candidates = mention_completion_candidates(
        index=index,
        query=token[1:],
        recent_paths=recent_paths,
        limit=limit,
    )
    return [f"@{entry.path} " for entry in candidates]
