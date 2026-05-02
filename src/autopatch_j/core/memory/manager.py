from __future__ import annotations

from pathlib import Path
from typing import Any

from autopatch_j.core.models import IntentType

from .delta import MemoryDeltaApplier
from .prompt_context import MemoryPromptContextBuilder
from .schema import (
    LONG_TERM_SIGNALS,
    MAX_ASSISTANT_TEXT,
    MAX_SCOPE_PATHS,
    MAX_USER_TEXT,
    ORDINARY_INTENTS,
    SOFT_FILE_BYTES,
    MemorySummaryTrigger,
    clip_text,
    generate_id,
    now_iso,
)
from .store import MemoryStore


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
        self.store = MemoryStore(memory_file)
        self.delta_applier = MemoryDeltaApplier()
        self.prompt_context_builder = MemoryPromptContextBuilder()

    @property
    def memory_file(self) -> Path:
        return self.store.memory_file

    def build_prompt_context(self, intent: IntentType, current_user_text: str = "") -> str:
        if intent not in ORDINARY_INTENTS:
            return ""
        memory = self.load()
        return self.prompt_context_builder.build(memory, intent, current_user_text)

    def append_recent_turn(
        self,
        intent: IntentType,
        user_text: str,
        assistant_text: str,
        scope_paths: list[str] | None = None,
    ) -> None:
        if intent not in ORDINARY_INTENTS:
            return

        memory = self.load()
        memory["working_memory"]["recent_turns"].append(
            {
                "id": generate_id("turn"),
                "intent": intent.value,
                "user_text": clip_text(user_text, MAX_USER_TEXT),
                "assistant_text": clip_text(assistant_text, MAX_ASSISTANT_TEXT),
                "summary": "",
                "summary_status": "pending",
                "scope_paths": [clip_text(path, 240) for path in (scope_paths or [])[:MAX_SCOPE_PATHS]],
                "created_at": now_iso(),
            }
        )
        self.save(memory)

    def apply_delta(
        self,
        delta: dict[str, Any],
        allowed_project_evidence_ids: set[str] | None = None,
    ) -> bool:
        memory = self.load()
        changed = self.delta_applier.apply(
            memory,
            delta,
            allowed_project_evidence_ids=allowed_project_evidence_ids,
        )
        return self.save(memory) if changed else False

    def should_summarize(self, last_user_text: str = "") -> bool:
        return self.find_summary_trigger(last_user_text) is not None

    def find_summary_trigger(
        self,
        last_user_text: str = "",
        force_project_code_explain: bool = False,
    ) -> MemorySummaryTrigger | None:
        if force_project_code_explain:
            return MemorySummaryTrigger.PROJECT_CODE_EXPLAIN

        memory = self.load()
        recent_turns = memory["working_memory"]["recent_turns"]
        pending_count = sum(1 for turn in recent_turns if turn.get("summary_status") == "pending")
        if pending_count >= 2:
            return MemorySummaryTrigger.PENDING_TURNS
        if len(recent_turns) >= 6:
            return MemorySummaryTrigger.RECENT_TURNS
        if self.store.file_size() > SOFT_FILE_BYTES:
            return MemorySummaryTrigger.FILE_SIZE
        if any(signal in last_user_text for signal in LONG_TERM_SIGNALS):
            return MemorySummaryTrigger.LONG_TERM_SIGNAL
        return None

    def load(self) -> dict[str, Any]:
        return self.store.load()

    def save(self, memory: dict[str, Any]) -> bool:
        return self.store.save(memory)

    def clear(self) -> None:
        self.store.clear()
