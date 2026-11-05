"""SDK hook 上的业务投影、工具展示和预期 Memory 存储失败边界。"""

from collections.abc import Sequence
import dataclasses
import json
from typing import Any
from typing import override

from pydantic_ai import capabilities
from pydantic_ai import messages
from pydantic_ai import models
from pydantic_ai import settings
from pydantic_ai import tools
from pydantic_ai_harness import compaction
from pydantic_ai_harness import memory

from autopatch_j.agent import callbacks
from autopatch_j.agent import session
from autopatch_j.core import memory as project_memory


class RequestBoundary(capabilities.AbstractCapability[session.AgentSession]):
    """每次请求重申当前用户原文和持久任务身份，不依赖历史摘要正确保留它们。"""

    def __init__(self, output: callbacks.AgentCallbacks) -> None:
        self.output = output

    @override
    async def prepare_tools(
        self,
        ctx: tools.RunContext[session.AgentSession],
        tool_defs: list[tools.ToolDefinition],
    ) -> list[tools.ToolDefinition]:
        del ctx
        return [
            dataclasses.replace(item, sequential=True) for item in tool_defs
        ]

    @override
    async def before_model_request(
        self,
        ctx: tools.RunContext[session.AgentSession],
        request_context: models.ModelRequestContext,
    ) -> models.ModelRequestContext:
        current = ctx.deps.state
        proposal = current.proposal
        binding = {
            "user_request": ctx.deps.user_text,
            "task_id": current.task_id,
            "issue_id": current.issue_id,
            "goal": current.goal,
            "status": current.status,
            "approved_write_paths": current.write_paths,
            "scope_awaiting_human_approval": current.requested_paths,
            "temporary_requirements": current.constraints,
            "current_assessment": (
                current.assessment.model_dump() if current.assessment else None
            ),
            "proposal_id": proposal.id if proposal else None,
            "finding_handle": (
                proposal.target.handle if proposal and proposal.target else None
            ),
        }
        parameters = dataclasses.replace(
            request_context.model_request_parameters,
            instruction_parts=[
                *(
                    request_context.model_request_parameters.instruction_parts
                    or []
                ),
                messages.InstructionPart(
                    content="当前用户与业务绑定（不代表源码已应用）："
                    + json.dumps(binding, ensure_ascii=False)
                ),
            ],
        )
        return dataclasses.replace(
            request_context, model_request_parameters=parameters
        )

    @override
    async def before_tool_execute(
        self,
        ctx: tools.RunContext[session.AgentSession],
        *,
        call: messages.ToolCallPart,
        tool_def: tools.ToolDefinition,
        args: capabilities.ValidatedToolArgs,
    ) -> capabilities.ValidatedToolArgs:
        del ctx, call
        if self.output.on_tool_start:
            self.output.on_tool_start(tool_def.name)
        return args

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
        del call, args
        if tool_def.name in {
            "read_memory",
            "write_memory",
            "delete_memory",
            "search_memory",
        } and isinstance(error, project_memory.STORAGE_ERRORS):
            ctx.deps.notices.append("Memory 存储不可用，本次操作未完成。")
            return "Memory 存储不可用；不能当作空笔记，不要根据旧历史恢复已删除笔记。"
        raise error


class ProjectMemoryCapability(memory.Memory[session.AgentSession]):
    """仅隔离预期存储故障；contract 或编程错误继续传播。"""

    @override
    async def before_model_request(
        self,
        ctx: tools.RunContext[session.AgentSession],
        request_context: models.ModelRequestContext,
    ) -> models.ModelRequestContext:
        try:
            return await super().before_model_request(ctx, request_context)
        except project_memory.STORAGE_ERRORS:
            ctx.deps.notices.append("Memory 存储不可用，本轮未载入项目笔记。")
            return request_context


class ProjectCompaction(compaction.SummarizingCompaction[session.AgentSession]):
    """沿用原生摘要；本轮摘要无收益后不重复做无效摘要。"""

    def __init__(
        self,
        *,
        max_tokens: int,
        keep_messages: int = 20,
        model_settings: settings.ModelSettings | None = None,
        summarization_capabilities: Sequence[
            capabilities.AbstractCapability[None]
        ] = (),
    ) -> None:
        super().__init__(
            max_tokens=max_tokens,
            keep_messages=keep_messages,
            model_settings=model_settings,
            summarization_capabilities=summarization_capabilities,
        )
        self.no_reclaim = False

    @override
    async def before_model_request(
        self,
        ctx: tools.RunContext[session.AgentSession],
        request_context: models.ModelRequestContext,
    ) -> models.ModelRequestContext:
        if self.no_reclaim:
            return request_context
        previous = list(ctx.messages)
        result = await super().before_model_request(ctx, request_context)
        # 未达到阈值同样返回原历史；只在阈值确实达到且未缩小时停止再尝试。
        if ctx.messages == previous:
            parameters = request_context.model_request_parameters
            self.no_reclaim = compaction.estimate_context_tokens(
                previous,
                model_request_parameters=parameters,
            ) >= (self.max_tokens or 0)
        return result


def unpin_history(
    history: list[messages.ModelMessage],
) -> list[messages.ModelMessage]:
    """仅当前请求受 pin 保护；旧用户轮次仍可在将来压缩。"""
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
