from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import (
    HARD_FILE_BYTES,
    KEEP_EPISODES_AFTER_COMPACTION,
    SOFT_FILE_BYTES,
)
from .normalizer import MemoryNormalizer
from .models import MemoryDocument
from .text_utils import now_iso


class MemoryStore:
    """负责 memory JSON 的原子读写、坏文件备份和容量保护。"""

    def __init__(self, memory_file: Path) -> None:
        self.memory_file = memory_file
        self.normalizer = MemoryNormalizer()

    def load(self) -> dict[str, Any]:
        return self.load_document().to_dict()

    def load_document(self) -> MemoryDocument:
        if not self.memory_file.exists():
            return MemoryDocument.empty()
        try:
            raw = json.loads(self.memory_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._backup_corrupt_file()
            return MemoryDocument.empty()
        return self.normalizer.normalize_document(raw)

    def save(self, memory: dict[str, Any]) -> bool:
        return self.save_document(self.normalizer.normalize_document(memory))

    def save_document(self, memory: MemoryDocument) -> bool:
        normalized = self.normalizer.normalize_document(memory.to_dict()).to_dict()
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
        self.save(self.normalizer.empty())

    def file_size(self) -> int:
        try:
            return self.memory_file.stat().st_size
        except OSError:
            return 0

    def _fit_size(self, memory: dict[str, Any]) -> dict[str, Any]:
        if len(self._dump(memory).encode("utf-8")) <= SOFT_FILE_BYTES:
            return memory

        memory["episodic_memory"]["episodes"] = memory["episodic_memory"]["episodes"][
            -KEEP_EPISODES_AFTER_COMPACTION:
        ]
        episode_ids = {episode["id"] for episode in memory["episodic_memory"]["episodes"]}
        memory["working_memory"]["pending_episode_ids"] = [
            episode_id
            for episode_id in memory["working_memory"]["pending_episode_ids"]
            if episode_id in episode_ids
        ]
        if len(self._dump(memory).encode("utf-8")) <= HARD_FILE_BYTES:
            return memory

        memory["episodic_memory"]["episodes"] = []
        memory["working_memory"]["pending_episode_ids"] = []
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
