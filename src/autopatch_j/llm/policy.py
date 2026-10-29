"""SDK 公开扩展点上的容量与正常结束校验。"""

from collections.abc import AsyncGenerator
import contextlib
import json
from typing import Any
from typing import override

from pydantic_ai import capabilities
from pydantic_ai import messages as sdk_messages
from pydantic_ai import models
from pydantic_ai import settings
from pydantic_ai import tools
from pydantic_ai.models import openai as openai_model
from pydantic_ai.models import wrapper

from autopatch_j.llm import context_window


class ModelFailure(RuntimeError):
    """模型未形成可采用的完整结果。"""


class ContextCapacityError(ModelFailure):
    """完整必需请求超出应用的保守容量限值。"""


def text_units(text: str) -> int:
    """使用 UTF-8 字节数作保守估算量纲，不声称是 tokenizer。"""
    return len(text.encode("utf-8"))


def request_settings(
    profile: context_window.ModelContextProfile, *, classifier: bool = False
) -> openai_model.OpenAIChatModelSettings:
    """固定任务用途的请求策略；非 DeepSeek 部署保留 SDK baseline。"""
    result: openai_model.OpenAIChatModelSettings = {
        "max_tokens": 128 if classifier else profile.max_output_tokens
    }
    if classifier:
        result["temperature"] = 0
    if profile.model in {"deepseek-flash", "deepseek-v4-pro"}:
        result["extra_body"] = {
            "thinking": {"type": "disabled" if classifier else "enabled"}
        }
        if not classifier:
            result["openai_reasoning_effort"] = "max"
    return result


class FinishGuard[DepsT](capabilities.AbstractCapability[DepsT]):
    """在工具执行及结果采用前拒绝截断和异常结束。"""

    @override
    async def after_model_request(
        self,
        ctx: tools.RunContext[DepsT],
        *,
        request_context: models.ModelRequestContext,
        response: sdk_messages.ModelResponse,
    ) -> sdk_messages.ModelResponse:
        """仅接受原始 stop 或 tool_calls；异常结束在工具执行前失败。"""
        del ctx, request_context
        raw = (response.provider_details or {}).get("finish_reason")
        if raw not in {"stop", "tool_calls"}:
            raise ModelFailure(f"模型未正常完成：finish_reason={raw!r}")
        return response


class CapacityGuard(wrapper.WrapperModel):
    """仅预检完整 SDK 请求，协议转换和传输仍交给原生模型。"""

    def __init__(
        self, model: models.Model, profile: context_window.ModelContextProfile
    ) -> None:
        super().__init__(model)
        self.context_profile = profile

    def _check(
        self,
        messages: list[sdk_messages.ModelMessage],
        model_settings: settings.ModelSettings | None,
        parameters: models.ModelRequestParameters,
    ) -> None:
        """检查完整必需输入与预留输出，超限抛出 ContextCapacityError。"""
        schemas = [
            {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters_json_schema,
            }
            for item in (*parameters.function_tools, *parameters.output_tools)
        ]
        extra = json.dumps(
            {
                "tools": schemas,
                "instructions": [
                    item.content for item in parameters.instruction_parts or []
                ],
                "settings": model_settings or {},
            },
            ensure_ascii=False,
        )
        units = len(
            sdk_messages.ModelMessagesTypeAdapter.dump_json(messages)
        ) + text_units(extra)
        output = (model_settings or {}).get(
            "max_tokens"
        ) or self.context_profile.max_output_tokens
        if (
            units + output + self.context_profile.provider_safety_tokens
            > self.context_profile.context_window
        ):
            raise ContextCapacityError(
                "必需请求超过模型的保守容量预算，请缩小范围或开启新会话。"
            )

    @override
    async def request(
        self,
        messages: list[sdk_messages.ModelMessage],
        model_settings: settings.ModelSettings | None,
        model_request_parameters: models.ModelRequestParameters,
    ) -> sdk_messages.ModelResponse:
        """预检消息、指令、schema 和设置后委托原生非流式请求。"""
        self._check(messages, model_settings, model_request_parameters)
        return await self.wrapped.request(
            messages, model_settings, model_request_parameters
        )

    @override
    @contextlib.asynccontextmanager
    async def request_stream(
        self,
        messages: list[sdk_messages.ModelMessage],
        model_settings: settings.ModelSettings | None,
        model_request_parameters: models.ModelRequestParameters,
        run_context: tools.RunContext[Any] | None = None,
    ) -> AsyncGenerator[models.StreamedResponse]:
        """预检完整必需输入后委托原生流，退出时交还流资源。"""
        self._check(messages, model_settings, model_request_parameters)
        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response:
            yield response
