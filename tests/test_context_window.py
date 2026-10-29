"""完整请求容量预检：schema、中文和设置同样计入。"""

import asyncio

from pydantic_ai import messages
from pydantic_ai import models
from pydantic_ai import settings as model_settings
from pydantic_ai import tools
from pydantic_ai.models import test
import pytest

from autopatch_j.llm import context_window
from autopatch_j.llm import policy


def test_deepseek_capacity_uses_official_window_and_application_budget():
    profile = context_window.resolve_context_profile(model="deepseek-flash")
    assert profile.context_window == 1_048_576
    assert profile.provider_max_output_tokens == 393_216
    assert profile.input_capacity == 887_808
    assert profile.max_output_tokens == 128_000


@pytest.mark.parametrize(
    "model",
    ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "custom"],
)
def test_unknown_models_require_explicit_capacities(model):
    with pytest.raises(ValueError):
        context_window.resolve_context_profile(model=model)


def test_schemas_and_chinese_are_included_without_clipping():
    profile = context_window.ModelContextProfile("test", 1000, 100, 100, 100)
    model = policy.CapacityGuard(test.TestModel(), profile)
    history: list[messages.ModelMessage] = [
        messages.ModelRequest(
            parts=[messages.UserPromptPart("当前用户必须保留")]
        )
    ]
    parameters = models.ModelRequestParameters(
        function_tools=[
            tools.ToolDefinition(
                name="read_source",
                parameters_json_schema={"description": "中文" * 200},
            )
        ]
    )
    with pytest.raises(policy.ContextCapacityError):
        asyncio.run(model.request(history, {"max_tokens": 100}, parameters))
    request = history[0]
    assert isinstance(request, messages.ModelRequest)
    part = request.parts[0]
    assert isinstance(part, messages.UserPromptPart)
    assert part.content == "当前用户必须保留"
    assert policy.text_units("中文") == 6


def test_output_reservation_is_checked_even_for_short_prompt():
    profile = context_window.ModelContextProfile("test", 1000, 900, 900, 100)
    model = policy.CapacityGuard(test.TestModel(), profile)
    with pytest.raises(policy.ContextCapacityError):
        asyncio.run(
            model.request(
                [messages.ModelRequest(parts=[messages.UserPromptPart("x")])],
                {"max_tokens": 900},
                models.ModelRequestParameters(),
            )
        )


@pytest.mark.parametrize(
    "component", ["user", "instructions", "output_schema", "settings"]
)
def test_required_context_is_rejected_before_sdk_transport(
    component, sdk_scenario
):
    scenario = sdk_scenario([])
    runtime = scenario.runtime()
    profile = context_window.ModelContextProfile("test", 1000, 100, 100, 100)
    assert runtime.model is not None
    guarded = policy.CapacityGuard(runtime.model, profile)
    history: list[messages.ModelMessage] = [
        messages.ModelRequest(
            parts=[
                messages.UserPromptPart(
                    "必需中文证据" * 100 if component == "user" else "短问题"
                )
            ]
        )
    ]
    parameters = models.ModelRequestParameters()
    settings: model_settings.ModelSettings = {"max_tokens": 100}
    if component == "instructions":
        parameters = models.ModelRequestParameters(
            instruction_parts=[
                messages.InstructionPart("当前用户的必要约束" * 100)
            ]
        )
    elif component == "output_schema":
        parameters = models.ModelRequestParameters(
            output_tools=[
                tools.ToolDefinition(
                    name="answer",
                    parameters_json_schema={
                        "description": "完整中文输出结构" * 100
                    },
                )
            ]
        )
    elif component == "settings":
        settings["extra_body"] = {"required_context": "附加必需输入" * 100}
    try:
        with pytest.raises(policy.ContextCapacityError):
            runtime.run(guarded.request(history, settings, parameters))
        assert scenario.requests == []
    finally:
        runtime.close()
