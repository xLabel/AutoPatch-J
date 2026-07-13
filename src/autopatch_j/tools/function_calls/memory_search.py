from __future__ import annotations

import json
from typing import Annotated

from autopatch_j.tools.contract import FunctionTool, ToolArg, ToolExecutionResult, function_tool
from autopatch_j.tools.names import FunctionToolName


class MemorySearchTool(FunctionTool):
    """在当前项目可用的长期 Memory 中定位候选条目。"""

    @function_tool(
        name=FunctionToolName.MEMORY_SEARCH,
        description=(
            "搜索当前项目中可用的用户偏好、项目决定和当前 thread 讨论索引。"
            "只返回最多 5 条候选摘要；需要正文和来源证据时再调用 memory_read。"
        ),
    )
    def execute(
        self,
        query: Annotated[str, ToolArg("用于定位历史 Memory 的非空查询，可包含主题、别名、路径或标识符。")],
    ) -> ToolExecutionResult:
        normalized_query = query.strip()
        if not normalized_query:
            return ToolExecutionResult(
                status="error",
                message="Memory 搜索失败：query 不能为空。",
                summary="Memory 搜索失败: 空查询",
            )
        context = self.require_context()
        manager = getattr(context, "memory_manager", None)
        if manager is None:
            return ToolExecutionResult(
                status="error",
                message="Memory 当前不可用。",
                summary="Memory 不可用",
            )
        thread_id = getattr(context, "memory_thread_id", None)
        if not isinstance(thread_id, str) or not thread_id:
            return ToolExecutionResult(
                status="error",
                message="Memory 当前请求尚未完成 admission。",
                summary="Memory 搜索失败: 请求未绑定 thread",
            )

        hits = manager.search(normalized_query, limit=5, thread_id=thread_id)
        payload = {
            "query": normalized_query,
            "hits": [
                {
                    "id": hit.id,
                    "kind": hit.kind,
                    "title": hit.title,
                    "synopsis": hit.synopsis,
                    "match_type": hit.match_type,
                }
                for hit in hits[:5]
            ],
        }
        if not payload["hits"]:
            return ToolExecutionResult(
                status="ok",
                message="未找到与查询相关的可用 Memory。",
                summary="Memory 搜索无命中",
                payload=payload,
            )
        return ToolExecutionResult(
            status="ok",
            message="Memory 搜索结果：\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            summary=f"Memory 搜索命中 {len(payload['hits'])} 条",
            payload=payload,
        )
