from __future__ import annotations

from types import SimpleNamespace
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
            "MemoryReadTool",
            "MemorySearchTool",
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

    memory_search = schema_by_name[FunctionToolName.MEMORY_SEARCH.value]
    memory_read = schema_by_name[FunctionToolName.MEMORY_READ.value]
    assert memory_search["parameters"]["required"] == ["query"]
    assert memory_read["parameters"]["required"] == ["memory_id"]
    assert "最多 5 条" in memory_search["description"]
    assert "来源摘录" in memory_read["description"]


def test_memory_search_tool_returns_bounded_structured_hits() -> None:
    context = MagicMock()
    context.memory_thread_id = "thread-1"
    context.memory_manager.search.return_value = [
        SimpleNamespace(
            id="m-1",
            kind="project_decision",
            title="使用 Java 17",
            synopsis="项目统一使用 Java 17。",
            match_type="exact",
        )
    ]
    tool = FunctionToolCatalog.for_context(context).get(FunctionToolName.MEMORY_SEARCH)

    result = tool.execute(query="  Java 17  ")

    assert result.status == "ok"
    assert result.payload["hits"] == [
        {
            "id": "m-1",
            "kind": "project_decision",
            "title": "使用 Java 17",
            "synopsis": "项目统一使用 Java 17。",
            "match_type": "exact",
        }
    ]
    context.memory_manager.search.assert_called_once_with(
        "Java 17", limit=5, thread_id="thread-1"
    )


def test_memory_read_tool_bounds_content_and_provenance() -> None:
    context = MagicMock()
    context.memory_thread_id = "thread-1"
    context.memory_manager.read.return_value = SimpleNamespace(
        id="m-1",
        kind="user_preference",
        title="输出偏好",
        content="x" * 5_000,
        non_factual=False,
        thread_id=None,
        sources=[
            SimpleNamespace(turn_id=f"t-{index}", role="user", quote="q" * 1_000, created_at="now")
            for index in range(4)
        ],
    )
    tool = FunctionToolCatalog.for_context(context).get(FunctionToolName.MEMORY_READ)

    result = tool.execute(memory_id="m-1")

    assert result.status == "ok"
    assert len(result.payload["content"]) == 4_000
    assert len(result.payload["sources"]) == 3
    assert all(len(source["quote"]) == 800 for source in result.payload["sources"])
    context.memory_manager.read.assert_called_once_with(
        "m-1", thread_id="thread-1"
    )


def test_memory_tools_require_an_admitted_request_thread() -> None:
    context = MagicMock()
    context.memory_thread_id = None
    catalog = FunctionToolCatalog.for_context(context)

    search = catalog.get(FunctionToolName.MEMORY_SEARCH).execute(query="Java 17")
    read = catalog.get(FunctionToolName.MEMORY_READ).execute(memory_id="m-1")

    assert search.status == "error"
    assert read.status == "error"
    assert "admission" in search.message
    assert "admission" in read.message
    context.memory_manager.search.assert_not_called()
    context.memory_manager.read.assert_not_called()


def test_memory_tools_reject_empty_or_unavailable_requests() -> None:
    context = MagicMock()
    context.memory_manager = None
    catalog = FunctionToolCatalog.for_context(context)

    empty_search = catalog.get(FunctionToolName.MEMORY_SEARCH).execute(query=" ")
    unavailable_read = catalog.get(FunctionToolName.MEMORY_READ).execute(memory_id="m-1")

    assert empty_search.status == "error"
    assert unavailable_read.status == "error"
