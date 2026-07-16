from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Protocol, Union, get_args, get_origin, get_type_hints

from autopatch_j.tools.names import FunctionToolName


_FUNCTION_TOOL_META_ATTR = "__autopatch_function_tool__"


@dataclass(frozen=True, slots=True)
class FunctionToolSpec:
    """LLM function_call 可见的工具声明。"""

    name: FunctionToolName
    description: str
    parameters: dict[str, Any]

    @property
    def json_name(self) -> str:
        return self.name.value


@dataclass(frozen=True, slots=True)
class ToolArg:
    """LLM function_call 参数说明，配合 typing.Annotated 写在 execute 签名上。"""

    description: str


@dataclass(frozen=True, slots=True)
class FunctionToolMeta:
    """装饰器保存的工具名称和用途说明。"""

    name: FunctionToolName
    description: str


def function_tool(name: FunctionToolName, description: str):
    """声明本地工具对 LLM 暴露的名称和用途，参数 schema 从 execute 签名生成。"""

    def decorator(func):
        setattr(func, _FUNCTION_TOOL_META_ATTR, FunctionToolMeta(name=name, description=description))
        return func

    return decorator


def build_function_tool_spec(execute_method: Any) -> FunctionToolSpec:
    """从带 @function_tool 的 execute 方法签名生成 OpenAI-compatible tool spec。"""
    func = getattr(execute_method, "__func__", execute_method)
    meta = getattr(func, _FUNCTION_TOOL_META_ATTR, None)
    if not isinstance(meta, FunctionToolMeta):
        raise TypeError(f"{func.__qualname__} 缺少 @function_tool 声明。")

    signature = inspect.signature(func)
    type_hints = get_type_hints(func, include_extras=True)
    properties: dict[str, dict[str, str]] = {}
    required: list[str] = []

    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind not in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}:
            raise TypeError(f"{func.__qualname__}.{parameter.name} 使用了不支持的参数类型。")

        annotation = type_hints.get(parameter.name)
        value_type, tool_arg = _parse_tool_arg(func.__qualname__, parameter.name, annotation)
        properties[parameter.name] = {
            "type": _json_schema_type(func.__qualname__, parameter.name, value_type),
            "description": tool_arg.description,
        }
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)

    return FunctionToolSpec(
        name=meta.name,
        description=meta.description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


def _parse_tool_arg(qualname: str, parameter_name: str, annotation: Any) -> tuple[Any, ToolArg]:
    if annotation is None:
        raise TypeError(f"{qualname}.{parameter_name} 缺少 Annotated[..., ToolArg(...)] 参数声明。")
    if get_origin(annotation) is not Annotated:
        raise TypeError(f"{qualname}.{parameter_name} 必须使用 Annotated[..., ToolArg(...)]。")

    value_type, *metadata = get_args(annotation)
    tool_args = [item for item in metadata if isinstance(item, ToolArg)]
    if len(tool_args) != 1:
        raise TypeError(f"{qualname}.{parameter_name} 必须且只能声明一个 ToolArg。")
    return value_type, tool_args[0]


def _json_schema_type(qualname: str, parameter_name: str, value_type: Any) -> str:
    if get_origin(value_type) in {Union, UnionType}:
        non_none_types = [item for item in get_args(value_type) if item is not type(None)]
        if len(non_none_types) != 1:
            raise TypeError(f"{qualname}.{parameter_name} 使用了不支持的 Union 类型。")
        value_type = non_none_types[0]

    type_mapping = {
        str: "string",
        int: "integer",
        bool: "boolean",
        float: "number",
    }
    if value_type not in type_mapping:
        raise TypeError(f"{qualname}.{parameter_name} 使用了不支持的参数类型：{value_type!r}。")
    return type_mapping[value_type]


@dataclass(slots=True)
class ToolExecutionResult:
    """本地工具执行后回写给 ReAct 循环的统一结果。"""

    status: str
    message: str
    summary: str | None = None
    payload: Any = None


class ToolRuntimeContext(Protocol):
    """
    ReAct 工具运行时依赖契约。

    工具层只通过这个 Protocol 访问仓库、artifact、索引、补丁和缓存能力，
    避免直接依赖 Agent 或 CLI 流程类，便于单测替换为轻量上下文对象。
    """

    repo_root: Path
    artifact_manager: Any
    workspace_manager: Any
    symbol_indexer: Any
    patch_engine: Any
    code_fetcher: Any
    patch_verifier: Any
    memory_manager: Any
    focus_paths: list[str]
    patch_source_hint: str | None

    def is_focus_locked(self) -> bool: ...
    def is_path_in_focus(self, path: str) -> bool: ...
    def normalize_repo_path(self, path: str) -> str: ...
    def fetch_cached_source_read(self, tool_name: str, path: str, line: int | None) -> ToolExecutionResult | None: ...
    def persist_cached_source_read(
        self,
        tool_name: str,
        path: str,
        line: int | None,
        result: ToolExecutionResult,
    ) -> None: ...
    def set_proposed_patch_draft(self, draft: Any) -> None: ...
    def clear_proposed_patch_draft(self) -> None: ...
    def set_revised_patch_draft(self, draft: Any) -> None: ...


class FunctionTool:
    """
    所有 LLM function_call 工具的基类。

    子类只负责声明 schema 和执行本地能力；流程推进、入队和用户确认由上层 workflow 决定。
    """

    def __init__(self, context: ToolRuntimeContext | None = None) -> None:
        self.context = context

    @classmethod
    def spec(cls) -> FunctionToolSpec:
        cached = cls.__dict__.get("_function_tool_spec_cache")
        if isinstance(cached, FunctionToolSpec):
            return cached
        spec = build_function_tool_spec(cls.execute)
        setattr(cls, "_function_tool_spec_cache", spec)
        return spec

    @property
    def name(self) -> str:
        return self.spec().json_name

    @property
    def description(self) -> str:
        return self.spec().description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.spec().parameters

    def require_context(self) -> ToolRuntimeContext:
        if self.context is None:
            raise RuntimeError(f"工具 {self.name} 缺少运行时上下文。")
        return self.context

    def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行工具逻辑，子类通过 self.context 访问运行时服务。"""
        raise NotImplementedError
