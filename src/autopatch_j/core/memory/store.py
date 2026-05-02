from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import (
    HARD_FILE_BYTES,
    KEEP_RECENT_TURNS_AFTER_COMPACTION,
    MAX_ACTIVE_TOPICS,
    MAX_ASSISTANT_TEXT,
    MAX_LABEL,
    MAX_LONG_TERM_ITEMS,
    MAX_RECENT_TURNS,
    MAX_SUMMARY,
    MAX_USER_TEXT,
    MEMORY_VERSION,
    ORDINARY_INTENTS,
    SOFT_FILE_BYTES,
    clip_text,
    generate_id,
    non_empty,
    normalize_scope_paths,
    normalize_string_list,
    now_iso,
)


class MemoryStore:
    """负责 memory JSON 的读写、结构归一化和容量保护。"""

    def __init__(self, memory_file: Path) -> None:
        self.memory_file = memory_file

    def load(self) -> dict[str, Any]:
        if not self.memory_file.exists():
            return self.empty_memory()
        try:
            raw = json.loads(self.memory_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._backup_corrupt_file()
            return self.empty_memory()
        return self.normalize_memory(raw)

    def save(self, memory: dict[str, Any]) -> bool:
        normalized = self.normalize_memory(memory)
        normalized["updated_at"] = now_iso()
        normalized = self._fit_size(normalized)
        payload = self._dump(normalized)
        if len(payload.encode("utf-8")) > HARD_FILE_BYTES:
            return False

        try:
            self.memory_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.memory_file.with_suffix(self.memory_file.suffix + ".tmp")
            tmp_file.write_text(payload, encoding="utf-8")
            tmp_file.replace(self.memory_file)
            return True
        except OSError:
            return False

    def clear(self) -> None:
        self.save(self.empty_memory())

    def file_size(self) -> int:
        try:
            return self.memory_file.stat().st_size
        except OSError:
            return 0

    def normalize_memory(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return self.empty_memory()

        memory = self.empty_memory()
        memory["version"] = MEMORY_VERSION
        memory["updated_at"] = str(raw.get("updated_at") or memory["updated_at"])
        working = raw.get("working_memory") if isinstance(raw.get("working_memory"), dict) else {}
        long_term = raw.get("long_term_memory") if isinstance(raw.get("long_term_memory"), dict) else {}
        memory["working_memory"]["recent_turns"] = self._normalize_recent_turns(working.get("recent_turns"))
        memory["working_memory"]["active_topics"] = self._normalize_topics(working.get("active_topics"))
        memory["long_term_memory"]["durable_preferences"] = self._normalize_long_term_items(
            long_term.get("durable_preferences"),
            "durable_preference",
        )
        memory["long_term_memory"]["project_facts"] = self._normalize_long_term_items(
            long_term.get("project_facts"),
            "project_fact",
        )
        return memory

    def empty_memory(self) -> dict[str, Any]:
        return {
            "version": MEMORY_VERSION,
            "updated_at": now_iso(),
            "working_memory": {
                "active_topics": [],
                "recent_turns": [],
            },
            "long_term_memory": {
                "durable_preferences": [],
                "project_facts": [],
            },
        }

    def _normalize_recent_turns(self, raw_turns: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_turns, list):
            return []
        turns: list[dict[str, Any]] = []
        allowed_intents = {intent.value for intent in ORDINARY_INTENTS}
        for raw in raw_turns:
            if not isinstance(raw, dict) or raw.get("intent") not in allowed_intents:
                continue
            turns.append(
                {
                    "id": non_empty(raw.get("id"), generate_id("turn")),
                    "intent": raw["intent"],
                    "user_text": clip_text(raw.get("user_text", ""), MAX_USER_TEXT),
                    "assistant_text": clip_text(raw.get("assistant_text", ""), MAX_ASSISTANT_TEXT),
                    "summary": clip_text(raw.get("summary", ""), MAX_SUMMARY),
                    "summary_status": "ready" if raw.get("summary_status") == "ready" else "pending",
                    "scope_paths": normalize_scope_paths(raw.get("scope_paths")),
                    "created_at": non_empty(raw.get("created_at"), now_iso()),
                }
            )
        return turns[-MAX_RECENT_TURNS:]

    def _normalize_topics(self, raw_topics: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_topics, list):
            return []
        topics: list[dict[str, Any]] = []
        for raw in raw_topics:
            if not isinstance(raw, dict):
                continue
            label = clip_text(raw.get("label", ""), MAX_LABEL)
            summary = clip_text(raw.get("summary", ""), MAX_SUMMARY)
            if not label or not summary:
                continue
            topics.append(
                {
                    "id": non_empty(raw.get("id"), generate_id("topic")),
                    "label": label,
                    "summary": summary,
                    "related_turn_ids": normalize_string_list(raw.get("related_turn_ids"), 20, 120),
                    "last_touched_at": non_empty(raw.get("last_touched_at"), now_iso()),
                }
            )
        return sorted(topics, key=lambda item: item["last_touched_at"])[-MAX_ACTIVE_TOPICS:]

    def _normalize_long_term_items(self, raw_items: Any, item_type: str) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            label = clip_text(raw.get("label", ""), MAX_LABEL)
            summary = clip_text(raw.get("summary", ""), MAX_SUMMARY)
            if not label or not summary:
                continue
            now = now_iso()
            source = raw.get("source")
            if source not in {"user_explicit", "repo_verified"}:
                source = "user_explicit"
            items.append(
                {
                    "id": non_empty(raw.get("id"), generate_id("mem")),
                    "type": item_type,
                    "label": label,
                    "summary": summary,
                    "status": "inactive" if raw.get("status") == "inactive" else "active",
                    "source": source,
                    "created_at": non_empty(raw.get("created_at"), now),
                    "updated_at": non_empty(raw.get("updated_at"), now),
                }
            )
        return sorted(items, key=lambda item: (item["status"] == "active", item["updated_at"]))[-MAX_LONG_TERM_ITEMS:]

    def _fit_size(self, memory: dict[str, Any]) -> dict[str, Any]:
        if len(self._dump(memory).encode("utf-8")) <= SOFT_FILE_BYTES:
            return memory

        memory["working_memory"]["recent_turns"] = memory["working_memory"]["recent_turns"][
            -KEEP_RECENT_TURNS_AFTER_COMPACTION:
        ]
        if len(self._dump(memory).encode("utf-8")) <= HARD_FILE_BYTES:
            return memory

        memory["working_memory"]["recent_turns"] = []
        if len(self._dump(memory).encode("utf-8")) <= HARD_FILE_BYTES:
            return memory

        memory["working_memory"]["active_topics"] = []
        return memory

    def _backup_corrupt_file(self) -> None:
        try:
            backup = self.memory_file.with_name("memory.corrupt.json")
            if backup.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                backup = self.memory_file.with_name(f"memory.corrupt.{stamp}.json")
            self.memory_file.replace(backup)
        except OSError:
            return

    def _dump(self, memory: dict[str, Any]) -> str:
        return json.dumps(memory, ensure_ascii=False, indent=2)
