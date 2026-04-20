from __future__ import annotations

import re
from pathlib import Path


FINDING_INDEX_PATTERNS = (
    re.compile(r"第\s*(\d+)\s*个"),
    re.compile(r"\b(\d+)\b"),
)

CHINESE_FINDING_INDEX = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

CHINESE_FINDING_INDEX_PATTERN = re.compile(r"第\s*([一二两三四五六七八九十])\s*个")


def extract_requested_finding_index(text: str) -> int | None:
    for pattern in FINDING_INDEX_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return int(match.group(1))

    match = CHINESE_FINDING_INDEX_PATTERN.search(text)
    if match is None:
        return None
    return CHINESE_FINDING_INDEX.get(match.group(1))


def extract_planned_finding_index(tool_args: dict[str, object]) -> int | None:
    raw_index = tool_args.get("finding_index")
    if raw_index is None:
        return None
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def build_finding_instruction(
    finding: object,
    user_request: str | None = None,
    mention_context: str | None = None,
) -> str:
    check_id = getattr(finding, "check_id", "")
    severity = getattr(finding, "severity", "")
    message = getattr(finding, "message", "")
    start_line = getattr(finding, "start_line", 0)
    end_line = getattr(finding, "end_line", 0)
    rule = getattr(finding, "rule", "")
    snippet = getattr(finding, "snippet", "")
    return (
        "Draft one minimal search-replace edit for this finding.\n"
        f"user_request: {user_request or '(none)'}\n"
        f"check_id: {check_id}\n"
        f"severity: {severity}\n"
        f"message: {message}\n"
        f"rule: {rule}\n"
        f"line_range: {start_line}-{end_line}\n"
        f"snippet:\n{snippet}\n"
        f"mention_context:\n{mention_context or '(none)'}\n"
    )
