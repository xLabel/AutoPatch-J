from __future__ import annotations

from dataclasses import dataclass

from autopatch_j.core.domain import IntentType
from autopatch_j.tools.names import FunctionToolName


@dataclass(frozen=True, slots=True)
class TaskProfile:
    """
    Agent 任务的静态执行边界。

    intent 决定系统提示词，tool_names 决定本轮 ReAct 可调用的工具集合。
    它只描述任务配置，不保存运行时状态。
    """

    intent: IntentType
    tool_names: tuple[FunctionToolName, ...]


TASK_PROFILES: dict[IntentType, TaskProfile] = {
    IntentType.CODE_AUDIT: TaskProfile(
        intent=IntentType.CODE_AUDIT,
        tool_names=(
            FunctionToolName.GET_FINDING_DETAIL,
            FunctionToolName.READ_SOURCE_CODE,
            FunctionToolName.PROPOSE_PATCH,
        ),
    ),
    IntentType.CODE_EXPLAIN: TaskProfile(
        intent=IntentType.CODE_EXPLAIN,
        tool_names=(
            FunctionToolName.SEARCH_SYMBOLS,
            FunctionToolName.READ_SOURCE_CODE,
        ),
    ),
    IntentType.GENERAL_CHAT: TaskProfile(
        intent=IntentType.GENERAL_CHAT,
        tool_names=(),
    ),
    IntentType.PATCH_EXPLAIN: TaskProfile(
        intent=IntentType.PATCH_EXPLAIN,
        tool_names=(
            FunctionToolName.SEARCH_SYMBOLS,
            FunctionToolName.READ_SOURCE_CODE,
        ),
    ),
    IntentType.PATCH_REVISE: TaskProfile(
        intent=IntentType.PATCH_REVISE,
        tool_names=(
            FunctionToolName.SEARCH_SYMBOLS,
            FunctionToolName.READ_SOURCE_CODE,
            FunctionToolName.GET_FINDING_DETAIL,
            FunctionToolName.REVISE_PATCH,
        ),
    ),
}

CODE_EXPLAIN_SINGLE_FILE_PROFILE = TaskProfile(
    intent=IntentType.CODE_EXPLAIN,
    tool_names=(FunctionToolName.READ_SOURCE_CODE,),
)
ZERO_FINDING_REVIEW_PROFILE = TaskProfile(
    intent=IntentType.CODE_AUDIT,
    tool_names=(
        FunctionToolName.READ_SOURCE_CODE,
        FunctionToolName.PROPOSE_PATCH,
    ),
)


def fetch_task_profile(intent: IntentType) -> TaskProfile:
    return TASK_PROFILES[intent]


def fetch_code_explain_profile(allow_symbol_search: bool) -> TaskProfile:
    if allow_symbol_search:
        return TASK_PROFILES[IntentType.CODE_EXPLAIN]
    return CODE_EXPLAIN_SINGLE_FILE_PROFILE
