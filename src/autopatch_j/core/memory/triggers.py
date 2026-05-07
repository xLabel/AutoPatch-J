from __future__ import annotations

from enum import Enum, auto


class MemorySummaryTrigger(Enum):
    """Why ordinary-chat memory summarization was requested."""

    PENDING_TURNS = auto()
    RECENT_TURNS = auto()
    FILE_SIZE = auto()
    LONG_TERM_SIGNAL = auto()
    PROJECT_CODE_EXPLAIN = auto()
