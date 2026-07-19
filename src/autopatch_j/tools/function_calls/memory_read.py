from __future__ import annotations

import json
from typing import Annotated

from autopatch_j.core.memory.models import MemoryRequestState
from autopatch_j.tools.contract import FunctionTool, ToolArg, ToolExecutionResult, function_tool
from autopatch_j.tools.names import FunctionToolName


class MemoryReadTool(FunctionTool):
    """按 Memory ID 读取有界正文和来源证据。"""

    _MAX_CONTENT_CHARS = 4_000
    _MAX_SOURCE_CHARS = 800

    @function_tool(
        name=FunctionToolName.MEMORY_READ,
        description=(
            "读取一条 active Memory 的有界正文、适用范围和来源摘录。"
            "memory_id 必须来自本请求的 Memory Map 或 memory_search；"
            "不可用、已遗忘或已被替代的条目不会作为有效记忆返回。"
        ),
    )
    def execute(
        self,
        memory_id: Annotated[str, ToolArg("当前 Memory Map 或 memory_search 返回的 Memory ID。")],
    ) -> ToolExecutionResult:
        normalized_id = memory_id.strip()
        if not normalized_id:
            return ToolExecutionResult(
                status="error",
                message="Memory 读取失败：memory_id 不能为空。",
                summary="Memory 读取失败: 空 ID",
            )
        context = self.require_context()
        manager = getattr(context, "memory_manager", None)
        if manager is None:
            return ToolExecutionResult(
                status="error",
                message="Memory 当前不可用。",
                summary="Memory 不可用",
            )
        request_state = getattr(context, "memory_request_state", None)
        if not isinstance(request_state, MemoryRequestState):
            return ToolExecutionResult(
                status="error",
                message="Memory 当前请求尚未完成 admission。",
                summary="Memory 读取失败: 请求未完成 admission",
            )

        try:
            detail = manager.read_memory_request(request_state, normalized_id)
        except Exception:
            return ToolExecutionResult(
                status="error",
                message=f"Memory 不存在或当前不可用：{normalized_id}",
                summary=f"Memory 读取失败: {normalized_id}",
            )

        payload = {
            "id": detail.id,
            "kind": detail.kind,
            "subject": detail.subject,
            "statement": detail.statement,
            "content": detail.content[: self._MAX_CONTENT_CHARS],
            "strength": detail.strength,
            "origin": detail.origin,
            "recall_mode": detail.recall_mode,
            "applies_to_paths": list(detail.applies_to_paths),
            "thread_id": detail.thread_id,
            "sources": [
                {
                    "turn_id": source.turn_id,
                    "role": source.role,
                    "quote": source.quote[: self._MAX_SOURCE_CHARS],
                    "created_at": str(source.created_at),
                }
                for source in detail.sources[:3]
            ],
        }
        return ToolExecutionResult(
            status="ok",
            message="Memory 详情：\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            summary=f"已读取 Memory: {detail.subject}",
            payload=payload,
        )
