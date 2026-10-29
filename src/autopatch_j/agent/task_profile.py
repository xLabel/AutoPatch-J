"""五类任务与零 finding 复核可调用的工具权限。"""

from __future__ import annotations

import dataclasses

from autopatch_j.core.domain import intent as domain_intent
from autopatch_j.tools import names

SOURCE_READING_TOOLS = (
    names.FunctionToolName.READ_SOURCE_FILE,
    names.FunctionToolName.READ_SOURCE_BLOCK,
    names.FunctionToolName.READ_SOURCE_CONTEXT,
)
MEMORY_TOOLS = (
    names.FunctionToolName.MEMORY_SEARCH,
    names.FunctionToolName.MEMORY_READ,
    names.FunctionToolName.MEMORY_WRITE,
    names.FunctionToolName.MEMORY_DELETE,
)


@dataclasses.dataclass(frozen=True, slots=True)
class TaskProfile:
    """Agent 任务的静态执行边界。

    intent 决定系统提示词，tool_names 决定本轮 ReAct 可调用的工具集合。
    它只描述任务配置，不保存运行时状态。
    """

    intent: domain_intent.IntentType
    tool_names: tuple[names.FunctionToolName, ...]


TASK_PROFILES: dict[domain_intent.IntentType, TaskProfile] = {
    domain_intent.IntentType.CODE_AUDIT: TaskProfile(
        intent=domain_intent.IntentType.CODE_AUDIT,
        tool_names=(
            names.FunctionToolName.GET_FINDING_DETAIL,
            names.FunctionToolName.READ_SOURCE_CONTEXT,
            names.FunctionToolName.READ_SOURCE_BLOCK,
            names.FunctionToolName.READ_SOURCE_FILE,
            names.FunctionToolName.PROPOSE_PATCH,
            *MEMORY_TOOLS,
        ),
    ),
    domain_intent.IntentType.CODE_EXPLAIN: TaskProfile(
        intent=domain_intent.IntentType.CODE_EXPLAIN,
        tool_names=(
            names.FunctionToolName.SEARCH_SYMBOLS,
            *SOURCE_READING_TOOLS,
            *MEMORY_TOOLS,
        ),
    ),
    domain_intent.IntentType.GENERAL_CHAT: TaskProfile(
        intent=domain_intent.IntentType.GENERAL_CHAT,
        tool_names=MEMORY_TOOLS,
    ),
    domain_intent.IntentType.PATCH_EXPLAIN: TaskProfile(
        intent=domain_intent.IntentType.PATCH_EXPLAIN,
        tool_names=(
            names.FunctionToolName.SEARCH_SYMBOLS,
            *SOURCE_READING_TOOLS,
            *MEMORY_TOOLS,
        ),
    ),
    domain_intent.IntentType.PATCH_REVISE: TaskProfile(
        intent=domain_intent.IntentType.PATCH_REVISE,
        tool_names=(
            names.FunctionToolName.SEARCH_SYMBOLS,
            *SOURCE_READING_TOOLS,
            names.FunctionToolName.GET_FINDING_DETAIL,
            names.FunctionToolName.REVISE_PATCH,
            *MEMORY_TOOLS,
        ),
    ),
}

CODE_EXPLAIN_SINGLE_FILE_PROFILE = TaskProfile(
    intent=domain_intent.IntentType.CODE_EXPLAIN,
    tool_names=(*SOURCE_READING_TOOLS, *MEMORY_TOOLS),
)
ZERO_FINDING_REVIEW_PROFILE = TaskProfile(
    intent=domain_intent.IntentType.CODE_AUDIT,
    tool_names=(
        *SOURCE_READING_TOOLS,
        names.FunctionToolName.PROPOSE_PATCH,
        *MEMORY_TOOLS,
    ),
)


def fetch_task_profile(intent: domain_intent.IntentType) -> TaskProfile:
    """返回任务允许的工具集合，不携带执行状态。"""
    return TASK_PROFILES[intent]


def fetch_code_explain_profile(allow_symbol_search: bool) -> TaskProfile:
    """按导航许可选择讲解工具，单文件模式不开放符号搜索。"""
    if allow_symbol_search:
        return TASK_PROFILES[domain_intent.IntentType.CODE_EXPLAIN]
    return CODE_EXPLAIN_SINGLE_FILE_PROFILE
