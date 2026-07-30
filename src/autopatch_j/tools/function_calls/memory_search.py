from __future__ import annotations

from typing import Annotated

from autopatch_j.core.memory.errors import MemoryContractError, MemoryStorageError
from autopatch_j.core.memory.models import MemoryRequestState
from autopatch_j.core.memory.search_response import build_memory_search_response
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
        except MemoryStorageError as exc:
            notice = manager.degraded_notice(exc)
            return ToolExecutionResult(
                status="error",
                message=notice or f"Memory degraded：搜索失败。原因：{exc}",
                summary=notice or "Memory degraded：搜索失败",
            )
        except MemoryContractError as exc:
            return ToolExecutionResult(
                status="error",
                message=f"Memory 搜索被请求约束拒绝：{exc}",
                summary="Memory 搜索失败: 请求约束拒绝",
            )
        except Exception as exc:
            return ToolExecutionResult(
                status="error",
                message=f"Memory 搜索执行异常：{type(exc).__name__}: {exc}",
                summary="Memory 搜索失败: 执行异常",
            )
        payload, message = build_memory_search_response(normalized_query, hits[:8])
        if not payload["hits"]:
            return ToolExecutionResult(
                status="ok",
                message=message,
                summary="Memory 搜索无命中",
                payload=payload,
            )
        return ToolExecutionResult(
            status="ok",
            message=message,
            summary=f"Memory 搜索命中 {len(payload['hits'])} 条",
            payload=payload,
        )
