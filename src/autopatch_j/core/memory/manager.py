from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from autopatch_j.core.domain import IntentType

from .delta import MemoryDeltaApplier
from .prompt_context import MemoryPromptContextBuilder
from .constants import (
    MAX_ASSISTANT_TEXT,
    MAX_SCOPE_PATHS,
    MAX_USER_TEXT,
    ORDINARY_INTENTS,
    SOFT_FILE_BYTES,
)
from .signals import LONG_TERM_SIGNALS
from .models import MemoryDocument, MemoryEpisode
from .text_utils import (
    clip_text,
    generate_id,
    now_iso,
)
from .store import MemoryStore
from .triggers import MemorySummaryTrigger


class MemoryManager:
    """
    管理 AutoPatch-J 的普通问答记忆。

    作用范围：
    - 只服务 code_explain 和 general_chat
    - 不服务 code_audit、patch_explain、patch_revise
    - 不保存源码全文、补丁 diff、工具输出或推理链

    职责：
    - 作为记忆子系统对外入口，协调 store、delta applier 和 prompt context builder
    - 只暴露业务需要的读写、摘要触发判断和上下文构建能力
    - 把 LLM 生成内容交给程序侧硬校验后再写入 memory JSON
    """

    def __init__(self, memory_file: Path) -> None:
        self._lock = RLock()
        self.store = MemoryStore(memory_file)
        self.delta_applier = MemoryDeltaApplier()
        self.prompt_context_builder = MemoryPromptContextBuilder()

    @property
    def memory_file(self) -> Path:
        return self.store.memory_file

    def build_prompt_context(self, intent: IntentType, current_user_text: str = "") -> str:
        if intent not in ORDINARY_INTENTS:
            return ""
        with self._lock:
            memory = self.store.load_document()
            return self.prompt_context_builder.build(memory, intent, current_user_text)

    def build_prompt_context_debug_summary(self, intent: IntentType, current_user_text: str = "") -> str:
        if intent not in ORDINARY_INTENTS:
            return ""
        with self._lock:
            memory = self.store.load_document()
            return self.prompt_context_builder.build_debug_summary(memory, intent, current_user_text)

    def append_recent_turn(
        self,
        intent: IntentType,
        user_text: str,
        assistant_text: str,
        scope_paths: list[str] | None = None,
    ) -> None:
        if intent not in ORDINARY_INTENTS:
            return

        with self._lock:
            memory = self.store.load_document()
            episode = MemoryEpisode(
                id=generate_id("episode"),
                intent=intent.value,
                user_goal=clip_text(user_text, MAX_USER_TEXT),
                assistant_result=clip_text(assistant_text, MAX_ASSISTANT_TEXT),
                summary="",
                summary_status="pending",
                scope_paths=[clip_text(path, 240) for path in (scope_paths or [])[:MAX_SCOPE_PATHS]],
                importance=3,
                created_at=now_iso(),
                last_accessed_at=now_iso(),
                access_count=0,
            )
            updated_memory = MemoryDocument(
                updated_at=memory.updated_at,
                active_topics=memory.active_topics,
                pending_episode_ids=[*memory.pending_episode_ids, episode.id],
                episodes=[*memory.episodes, episode],
                repo_profile=memory.repo_profile,
                user_preferences=memory.user_preferences,
                project_notes=memory.project_notes,
                codebase_concepts=memory.codebase_concepts,
                collaboration_preferences=memory.collaboration_preferences,
                maintenance=memory.maintenance,
                version=memory.version,
            )
            self.store.save_document(updated_memory)

    def apply_delta(self, delta: dict[str, Any], repo_profile: dict[str, Any] | None = None) -> bool:
        with self._lock:
            memory = self.store.load()
            changed = False
            if repo_profile is not None:
                memory["repo_profile"] = repo_profile
                changed = True
            changed = self.delta_applier.apply(memory, delta) or changed
            return self.store.save(memory) if changed else False

    def should_summarize(self, last_user_text: str = "") -> bool:
        return self.find_summary_trigger(last_user_text) is not None

    def find_summary_trigger(
        self,
        last_user_text: str = "",
        force_project_code_explain: bool = False,
    ) -> MemorySummaryTrigger | None:
        if force_project_code_explain:
            return MemorySummaryTrigger.PROJECT_CODE_EXPLAIN

        with self._lock:
            memory = self.store.load_document()
            pending_count = len(memory.pending_episode_ids)
            if pending_count >= 2:
                return MemorySummaryTrigger.PENDING_EPISODES
            if len(memory.episodes) >= 6:
                return MemorySummaryTrigger.RECENT_EPISODES
            if self.store.file_size() > SOFT_FILE_BYTES:
                return MemorySummaryTrigger.FILE_SIZE
            if any(signal in last_user_text for signal in LONG_TERM_SIGNALS):
                return MemorySummaryTrigger.LONG_TERM_SIGNAL
            return None

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self.store.load()

    def load_document(self) -> MemoryDocument:
        with self._lock:
            return self.store.load_document()

    def save(self, memory: dict[str, Any]) -> bool:
        with self._lock:
            return self.store.save(memory)

    def save_document(self, memory: MemoryDocument) -> bool:
        with self._lock:
            return self.store.save_document(memory)

    def clear(self) -> None:
        with self._lock:
            self.store.clear()
