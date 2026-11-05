"""模型请求设置及公开 hook 上的正常结束校验。"""

from typing import override

from pydantic_ai import capabilities
from pydantic_ai import messages as sdk_messages
from pydantic_ai import models
from pydantic_ai.models import openai as openai_model
from pydantic_ai import tools

from autopatch_j.llm import context_window


class ModelFailure(RuntimeError):
    """模型未形成可采用的完整结果。"""


def request_settings(
    profile: context_window.ModelContextProfile,
) -> openai_model.OpenAIChatModelSettings:
    """固定任务用途的请求策略；非 DeepSeek 部署保留 SDK baseline。"""
    result: openai_model.OpenAIChatModelSettings = {
        "max_tokens": profile.max_output_tokens
    }
    if profile.model in {"deepseek-flash", "deepseek-v4-pro"}:
        result["extra_body"] = {"thinking": {"type": "enabled"}}
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
