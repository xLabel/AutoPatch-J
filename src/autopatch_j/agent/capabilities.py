"""SDK hook 上的业务投影、工具展示和预期 Memory 存储失败边界。"""

import dataclasses
import json
from typing import override

from pydantic_ai import capabilities
from pydantic_ai import messages
from pydantic_ai import models
from pydantic_ai import tools

from autopatch_j.agent import callbacks
from autopatch_j.agent import run_context


class AgentRunPolicy(
    capabilities.AbstractCapability[run_context.AgentRunContext]
):
    """每次请求提供带待提交标识的业务事实，不依赖历史摘要恢复执行结论。"""

    def __init__(self, output: callbacks.AgentCallbacks) -> None:
        self.output = output

    @override
    async def prepare_tools(
        self,
        ctx: tools.RunContext[run_context.AgentRunContext],
        tool_defs: list[tools.ToolDefinition],
    ) -> list[tools.ToolDefinition]:
        del ctx
        return [
            dataclasses.replace(item, sequential=True) for item in tool_defs
        ]

    @override
    async def before_model_request(
        self,
        ctx: tools.RunContext[run_context.AgentRunContext],
        request_context: models.ModelRequestContext,
    ) -> models.ModelRequestContext:
        binding = {
            "user_request": ctx.deps.user_text,
            **ctx.deps.repair_status().model_dump(
                mode="json",
                exclude={
                    "source_evidence": {
                        "__all__": {"expected_sha256", "current_sha256"}
                    }
                },
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
        ctx: tools.RunContext[run_context.AgentRunContext],
        *,
        call: messages.ToolCallPart,
        tool_def: tools.ToolDefinition,
        args: capabilities.ValidatedToolArgs,
    ) -> capabilities.ValidatedToolArgs:
        del call
        ctx.deps.require_current()
        if self.output.on_tool_start:
            self.output.on_tool_start(tool_def.name)
        return args
