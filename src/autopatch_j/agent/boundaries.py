"""原生能力上的请求权限、观察与可降级笔记边界。"""

import dataclasses
import json
from typing import Any
from typing import override

from pydantic_ai import capabilities
from pydantic_ai import messages
from pydantic_ai import models
from pydantic_ai import tools
from pydantic_ai_harness import compaction
from pydantic_ai_harness import memory

from autopatch_j.agent import callbacks
from autopatch_j.agent import session
from autopatch_j.agent import task_profile
from autopatch_j.core import memory as project_memory
from autopatch_j.core.review import finding_lookup
from autopatch_j.llm import policy
from autopatch_j.tools import contract


class RequestBoundary(capabilities.AbstractCapability[session.AgentSession]):
    """在执行前校验工具授权，并采集真正的业务观察结果。"""

    def __init__(
        self, allowed: set[str], output: callbacks.AgentCallbacks
    ) -> None:
        self.allowed = frozenset(allowed)
        self.output = output

    @override
    async def prepare_tools(
        self,
        ctx: tools.RunContext[session.AgentSession],
        tool_defs: list[tools.ToolDefinition],
    ) -> list[tools.ToolDefinition]:
        """过滤本轮工具白名单，强制串行以保护共享业务状态。"""
        del ctx
        return [
            dataclasses.replace(item, sequential=True)
            for item in tool_defs
            if item.name in self.allowed
        ]

    @override
    async def before_model_request(
        self,
        ctx: tools.RunContext[session.AgentSession],
        request_context: models.ModelRequestContext,
    ) -> models.ModelRequestContext:
        """将当前 finding 或审核草案的程序绑定重新投影到受保护的用户输入。"""
        current = ctx.deps
        draft = current.proposed_patch_draft or current.revised_patch_draft
        if draft is None and current.current_review_item is not None:
            draft = current.current_review_item.draft.to_patch_draft()
        if draft is not None:
            identity = {
                "file": draft.file_path,
                "scan": draft.source_scan_id,
                "finding": draft.associated_finding_id,
                "region": draft.match_region.to_dict(),
            }
        elif current.current_finding is not None:
            lookup = finding_lookup.resolve_finding_handle(
                current.artifact_manager,
                current.workspace_manager,
                current.current_finding.finding_id,
            )
            identity = {
                "file": lookup.finding.path,
                "scan": lookup.scan_id,
                "finding": lookup.finding_id,
                "region": lookup.finding.region.to_dict(),
            }
        else:
            return request_context
        binding = json.dumps(identity, ensure_ascii=False)
        projected: list[messages.ModelMessage] = []
        for message in request_context.messages:
            if not isinstance(message, messages.ModelRequest):
                projected.append(message)
                continue
            parts = []
            for part in message.parts:
                if compaction.is_pinned(part):
                    assert isinstance(part, messages.UserPromptPart)
                    content = part.content
                    text = (
                        "".join(
                            item.content
                            for item in content
                            if isinstance(item, messages.TextContent)
                        )
                        if not isinstance(content, str)
                        else content
                    )
                    part = compaction.pin(
                        text
                        + "\n\n## 程序中的当前 finding / 暂存草案绑定（不表示已审核）\n"
                        + binding
                    )
                parts.append(part)
            projected.append(dataclasses.replace(message, parts=parts))
        return dataclasses.replace(request_context, messages=projected)

    @override
    async def before_tool_execute(
        self,
        ctx: tools.RunContext[session.AgentSession],
        *,
        call: messages.ToolCallPart,
        tool_def: tools.ToolDefinition,
        args: capabilities.ValidatedToolArgs,
    ) -> capabilities.ValidatedToolArgs:
        """执行前复核授权并发送工具开始事件；越权立即终止。"""
        del ctx, call
        if tool_def.name not in self.allowed:
            raise policy.ModelFailure(f"当前任务未授权工具：{tool_def.name}")
        if self.output.on_tool_start:
            self.output.on_tool_start(tool_def.name)
        return args

    @override
    async def after_tool_execute(
        self,
        ctx: tools.RunContext[session.AgentSession],
        *,
        call: messages.ToolCallPart,
        tool_def: tools.ToolDefinition,
        args: capabilities.ValidatedToolArgs,
        result: Any,
    ) -> Any:
        """收集实际业务观察并通知展示回调，不把工具结果当作审核提交。"""
        del call, args
        if isinstance(result, contract.ToolExecutionResult):
            result.name = tool_def.name
            ctx.deps.observations.append(result)
            if self.output.on_observation:
                self.output.on_observation(result.message, result.summary)
        return result

    @override
    async def on_tool_execute_error(
        self,
        ctx: tools.RunContext[session.AgentSession],
        *,
        call: messages.ToolCallPart,
        tool_def: tools.ToolDefinition,
        args: capabilities.ValidatedToolArgs,
        error: Exception,
    ) -> Any:
        """只将笔记预期存储故障降级为观察；编程和权限错误继续传播。"""
        del call, args
        memory_tools = {name.value for name in task_profile.MEMORY_TOOLS}
        if tool_def.name in memory_tools and isinstance(
            error, project_memory.STORAGE_ERRORS
        ):
            ctx.deps.notices.append("Memory 存储不可用，本次操作未完成。")
            return "Memory 存储不可用，请继续主任务；不要将此错误当作没有记忆。"
        raise error


class ProjectMemoryCapability(memory.Memory[session.AgentSession]):
    """只隔离预期存储故障；框架 contract 或编程错误继续传播。"""

    @override
    async def before_model_request(
        self,
        ctx: tools.RunContext[session.AgentSession],
        request_context: models.ModelRequestContext,
    ) -> models.ModelRequestContext:
        """笔记预期存储故障时提示降级；其他 hook 错误继续传播。"""
        try:
            return await super().before_model_request(ctx, request_context)
        except project_memory.STORAGE_ERRORS:
            ctx.deps.notices.append("Memory 存储不可用，本轮未载入项目笔记。")
            return request_context


def unpin_history(
    history: list[messages.ModelMessage],
) -> list[messages.ModelMessage]:
    """仅当前请求受 pin 保护；旧普通用户轮次仍可在将来压缩。"""
    result = []
    for message in history:
        if isinstance(message, messages.ModelRequest):
            parts = []
            for part in message.parts:
                if compaction.is_pinned(part):
                    assert isinstance(
                        part, messages.UserPromptPart
                    ) and not isinstance(part.content, str)
                    part = dataclasses.replace(
                        part,
                        content=[
                            (
                                dataclasses.replace(item, metadata=None)
                                if isinstance(item, messages.TextContent)
                                else item
                            )
                            for item in part.content
                        ],
                    )
                parts.append(part)
            message = dataclasses.replace(message, parts=parts)
        result.append(message)
    return result
