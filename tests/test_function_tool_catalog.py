from __future__ import annotations

from typing import Annotated
from unittest.mock import MagicMock

import autopatch_j.tools as tools
from autopatch_j.tools.catalog import FunctionToolCatalog
from autopatch_j.tools.contract import FunctionTool, ToolArg, ToolExecutionResult, function_tool
from autopatch_j.tools.names import FunctionToolName


def test_tools_root_exports_only_tool_infrastructure() -> None:
    assert tools.__all__ == [
        "FunctionTool",
        "FunctionToolCatalog",
        "FunctionToolName",
        "FunctionToolSpec",
        "ToolArg",
        "ToolExecutionResult",
        "ToolRuntimeContext",
        "build_function_tool_spec",
        "function_tool",
    ]
    assert not any(
        hasattr(tools, concrete_tool_name)
        for concrete_tool_name in [
            "GetFindingDetailTool",
            "ProposePatchTool",
            "ReadSourceBlockTool",
            "ReadSourceContextTool",
            "ReadSourceFileTool",
            "RevisePatchTool",
            "SearchSymbolsTool",
        ]
    )


def test_catalog_registers_only_function_call_modules() -> None:
    catalog = FunctionToolCatalog.for_context(MagicMock())

    assert set(catalog.tools) == {tool_name.value for tool_name in FunctionToolName}
    assert all(".function_calls." in tool.__class__.__module__ for tool in catalog.tools.values())


def test_catalog_rejects_duplicate_tool_names() -> None:
    class FirstTool(FunctionTool):
        @function_tool(name=FunctionToolName.SEARCH_SYMBOLS, description="first")
        def execute(self, query: Annotated[str, ToolArg("query")]) -> ToolExecutionResult:
            return ToolExecutionResult(status="ok", message="")

    class SecondTool(FunctionTool):
        @function_tool(name=FunctionToolName.SEARCH_SYMBOLS, description="second")
        def execute(self, query: Annotated[str, ToolArg("query")]) -> ToolExecutionResult:
            return ToolExecutionResult(status="ok", message="")

    try:
        FunctionToolCatalog([FirstTool(MagicMock()), SecondTool(MagicMock())])
    except ValueError as exc:
        assert "重复的 function_call 工具名" in str(exc)
    else:
        raise AssertionError("duplicate tool names should fail")


def test_catalog_rejects_missing_tool_arg_annotation() -> None:
    class BrokenTool(FunctionTool):
        @function_tool(name=FunctionToolName.SEARCH_SYMBOLS, description="broken")
        def execute(self, query: str) -> ToolExecutionResult:
            return ToolExecutionResult(status="ok", message="")

    try:
        FunctionToolCatalog([BrokenTool(MagicMock())])
    except TypeError as exc:
        assert "Annotated" in str(exc)
    else:
        raise AssertionError("missing ToolArg annotation should fail")


def test_catalog_exports_stable_function_call_schema() -> None:
    catalog = FunctionToolCatalog.for_context(MagicMock())
    schemas = catalog.schemas(tuple(FunctionToolName))

    schema_by_name = {schema["function"]["name"]: schema["function"] for schema in schemas}
    assert list(schema_by_name) == [tool_name.value for tool_name in FunctionToolName]

    propose_patch = schema_by_name[FunctionToolName.PROPOSE_PATCH.value]
    revise_patch = schema_by_name[FunctionToolName.REVISE_PATCH.value]
    assert propose_patch["parameters"]["required"] == ["file_path", "old_string", "new_string", "rationale"]
    assert revise_patch["parameters"]["required"] == ["file_path", "old_string", "new_string", "rationale"]
    assert propose_patch["parameters"]["properties"].keys() == revise_patch["parameters"]["properties"].keys()
    assert propose_patch["parameters"]["properties"]["file_path"]["type"] == "string"
    assert propose_patch["parameters"]["properties"]["associated_finding_id"]["type"] == "string"

    assert "源码读取工具" in propose_patch["description"]
    assert "old_string 不匹配" in propose_patch["description"]
    assert "不会修改文件系统" in propose_patch["description"]
    assert "只是询问补丁含义" in revise_patch["description"]
    assert "不会影响后续补丁队列" in revise_patch["description"]
    assert "read_source_context" in propose_patch["parameters"]["properties"]["old_string"]["description"]

    read_context = schema_by_name[FunctionToolName.READ_SOURCE_CONTEXT.value]
    assert read_context["parameters"]["required"] == ["path", "line"]
    assert read_context["parameters"]["properties"]["line"]["type"] == "integer"
