from __future__ import annotations

from unittest.mock import MagicMock

import autopatch_j.tools as tools
from autopatch_j.tools.catalog import FunctionToolCatalog
from autopatch_j.tools.names import FunctionToolName


def test_tools_root_exports_only_tool_infrastructure() -> None:
    assert tools.__all__ == [
        "FunctionTool",
        "FunctionToolCatalog",
        "FunctionToolName",
        "FunctionToolSpec",
        "ToolExecutionResult",
        "ToolRuntimeContext",
    ]
    assert not any(
        hasattr(tools, concrete_tool_name)
        for concrete_tool_name in [
            "GetFindingDetailTool",
            "ProposePatchTool",
            "ReadSourceCodeTool",
            "RevisePatchTool",
            "SearchSymbolsTool",
        ]
    )


def test_catalog_registers_only_function_call_modules() -> None:
    catalog = FunctionToolCatalog.for_context(MagicMock())

    assert set(catalog.tools) == {tool_name.value for tool_name in FunctionToolName}
    assert all(".function_calls." in tool.__class__.__module__ for tool in catalog.tools.values())


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

    assert "read_source_code" in propose_patch["description"]
    assert "不会修改文件系统" in propose_patch["description"]
    assert "不会影响后续补丁队列" in revise_patch["description"]
