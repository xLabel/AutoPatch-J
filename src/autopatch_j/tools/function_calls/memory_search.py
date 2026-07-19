from __future__ import annotations

import json
from typing import Annotated

from autopatch_j.core.memory.models import MemoryRequestState
from autopatch_j.tools.contract import FunctionTool, ToolArg, ToolExecutionResult, function_tool
from autopatch_j.tools.names import FunctionToolName


class MemorySearchTool(FunctionTool):
    """在当前项目可用的长期 Memory 中定位候选条目。"""

    @function_tool(
        name=FunctionToolName.MEMORY_SEARCH,
        description=(
            "搜索当前请求 policy 允许的项目 Memory；repair 请求只会返回项目决定和用户偏好。"
            "只返回最多 8 条候选摘要；需要正文和来源证据时再调用 memory_read。"
        ),
    )
    def execute(
        self,
        query: Annotated[str, ToolArg("用于定位历史 Memory 的非空查询，可包含主题、别名、代码概念或用户原话；路径只用于缩小适用范围。")],
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
        request_state = getattr(context, "memory_request_state", None)
        if not isinstance(request_state, MemoryRequestState):
            return ToolExecutionResult(
                status="error",
                message="Memory 当前请求尚未完成 admission。",
                summary="Memory 搜索失败: 请求未完成 admission",
            )

        try:
            hits = manager.search_memory_request(request_state, normalized_query)
        except Exception as exc:
            return ToolExecutionResult(
                status="error",
                message=f"Memory 搜索失败：{exc}",
                summary="Memory 搜索失败: policy 或额度拒绝",
            )
        payload = {
            "query": normalized_query,
            "hits": [
                {
                    "id": hit.id,
                    "kind": hit.kind,
                    "subject": hit.subject,
                    "statement": hit.statement,
                    "match_type": hit.match_type,
                }
                for hit in hits[:8]
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
