"""录制真实 SDK 工具定义，并通过原生校验及分派执行全部注册工具。"""

import json
from unittest import mock

import pytest

from autopatch_j import config
from autopatch_j.agent import instructions
from autopatch_j.agent import result
from autopatch_j.application import runtime
from autopatch_j.core import finding
from autopatch_j.core import memory
from autopatch_j.scanners import models
from autopatch_j.tools import names
from tests import sdk_fakes


def _call(tool_name, **arguments):
    return sdk_fakes.Reply(
        tool=tool_name, arguments=arguments, finish="tool_calls"
    )


def test_all_registered_tools_are_described_and_execute_via_sdk(
    tmp_path, sdk_scenario
):
    original = b"class A { int n = 1; int read() { return n; } }"
    (tmp_path / "A.java").write_bytes(original)
    (tmp_path / "B.java").write_bytes(b"class B {}")
    (tmp_path / "AGENTS.md").write_text(
        "ROOT_GUIDE: 保留已有类型。", encoding="utf-8"
    )
    scenario = sdk_scenario([])
    project = runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    offset = original.index(b"n = 1")
    scanner = mock.Mock()
    scanner.scan.return_value = models.ScanResult(
        "semgrep",
        ["A.java"],
        ["A.java"],
        "ok",
        "scan",
        [
            models.Finding(
                "apj-v1:" + "a" * 64 + ":1",
                "test.rule",
                "A.java",
                finding.SourceRegion(
                    1, offset + 1, 1, offset + 6, offset, offset + 5
                ),
                "warning",
                "candidate",
            ),
        ],
    )
    project.evidence.scanner = scanner
    project.repair.scanner = scanner
    project.symbol_index.rebuild_index()
    handles = []

    def investigate():
        yield _call("scan_java", paths=["A.java"])
        scan_id = next(project.evidence.directory.iterdir()).stem
        handles.append(f"{scan_id}:F1")
        yield _call("get_scan_findings", scan_id=scan_id)
        yield _call("get_finding_detail", finding_handle=handles[0])
        yield _call("read_source_file", path="A.java")
        yield _call("read_source_block", path="A.java", line=1)
        yield _call("read_source_context", path="A.java")
        yield _call("search_symbols", query="A", kinds=["file", "class"])
        yield _call("search_symbols", query="", kinds=[])
        yield _call("search_symbols", query="read")
        yield _call("search_java_references", name="A", paths=["."])
        yield _call("start_repair", goal="修复数值", write_paths=["A.java"])
        yield _call("request_write_scope", paths=["B.java"])
        yield _call(
            "update_task_requirements", additions=["保留类型"], removals=[]
        )
        yield _call("get_repair_status")
        yield _call("get_proposal")
        yield _call(
            "assess_issue",
            outcome="insufficient_evidence",
            reason="等待确认修复值",
            finding_handle=handles[0],
        )
        yield _call(
            "write_memory",
            file="topic.md",
            content="已核实事实 A.java 包含 read 方法",
        )
        yield _call("read_memory", file="topic.md")
        yield _call("search_memory", query="read")
        yield _call("delete_memory", file="topic.md")
        yield sdk_fakes.Reply(text="已调查，等待批准范围并确认修复值。")

    scenario.replies = investigate()
    try:
        assert (
            project.agent.run("修复数值，保留类型").status
            is result.RunStatus.COMPLETED
        )
        state = project.repair.store.load()
        assert state.requested_paths == ("A.java", "B.java")
        assert state.constraints == ("保留类型",)
        assert (
            state.assessment is not None
            and state.assessment.outcome == "insufficient_evidence"
        )
        project.repair.approve_scope(state)
        scenario.replies = iter(
            [
                _call(
                    "propose_patch",
                    edits=[
                        {
                            "path": "A.java",
                            "replacements": [
                                {"old_text": "n = 1", "new_text": "n = 2"},
                                {"old_text": "n = 2", "new_text": "n = 3"},
                            ],
                        }
                    ],
                    rationale="按已确认数值修复，保留类型",
                    finding_handle=handles[0],
                ),
                _call("get_repair_status"),
                _call("get_proposal"),
                sdk_fakes.Reply(text="完整提案已准备，未写入。"),
            ]
        )
        assert (
            project.agent.run("allow，最终数值设为 3").status
            is result.RunStatus.COMPLETED
        )
        state = project.repair.store.load()
        assert state.proposal is not None and state.proposal.target is not None
        assert state.proposal.files[0].candidate == original.replace(
            b"n = 1", b"n = 3"
        )
        assert state.write_paths == ("A.java", "B.java")
        assert (tmp_path / "A.java").read_bytes() == original
        calls = {
            reply["function"]["name"]
            for request in scenario.requests
            for message in request["messages"]
            for reply in message.get("tool_calls", [])
        }
        assert calls == {name.value for name in names.FunctionToolName}
        call_names = {}
        outputs = {}
        for message in scenario.requests[-1]["messages"]:
            for call in message.get("tool_calls", []):
                call_names[call["id"]] = call["function"]["name"]
            if message["role"] == "tool":
                content = message["content"]
                if content.startswith("{"):
                    assert "error" not in json.loads(content)
                outputs.setdefault(
                    call_names[message["tool_call_id"]], []
                ).append(content)
        assert (
            json.loads(outputs["read_source_file"][0])["source"]["code"]
            == original.decode()
        )
        assert json.loads(outputs["scan_java"][0])["total"] == 1
        assert json.loads(outputs["get_finding_detail"][0])["evidence_current"]
        assert [
            json.loads(item)["total"] for item in outputs["search_symbols"]
        ] == [4, 0, 1]
        assert "已核实事实" in outputs["read_memory"][0]
        assert "topic.md" in outputs["search_memory"][0]
        definitions = {
            tool["function"]["name"]: tool["function"]
            for tool in scenario.requests[0]["tools"]
        }
        assert set(definitions) == calls
        for tool in definitions.values():
            assert tool["description"]
            for parameter in tool["parameters"].get("properties", {}).values():
                assert parameter["description"]
        assert set(definitions["write_memory"]["parameters"]["properties"]) == {
            "file",
            "content",
            "old_text",
        }
        assert (
            definitions["read_source_context"]["parameters"]["properties"][
                "line"
            ]["default"]
            == 1
        )
        patch_definitions = definitions["propose_patch"]["parameters"]["$defs"]
        patch = patch_definitions["FileEdit"]
        assert patch["properties"]["path"]["description"]
        assert (
            "空字符串"
            in patch_definitions["Replacement"]["properties"]["new_text"][
                "description"
            ]
        )
        systems = [
            message["content"]
            for message in scenario.requests[0]["messages"]
            if message["role"] == "system"
        ]
        assert any(
            instructions.SYSTEM in text and "ROOT_GUIDE" in text
            for text in systems
        )
        assert any("background context" in text for text in systems)
        assert "unavailable" not in instructions.SYSTEM
        assert project.model_runtime.run(project.memory.paths()) == []
        scanner.scan.return_value = models.ScanResult(
            "semgrep", ["A.java"], ["A.java"], "ok", "clean"
        )
        assert project.repair.apply(state).checks[-1].status == "passed"
        assert (tmp_path / "A.java").read_bytes() == original.replace(
            b"n = 1", b"n = 3"
        )
    finally:
        project.close()


def test_long_guidance_and_conflict_sources_reach_request_intact(
    tmp_path, sdk_scenario
):
    directory = tmp_path / "src"
    directory.mkdir()
    (directory / "A.java").write_bytes(b"class A {}")
    guide = "\n".join(
        ["祖先规范保留类型"] * 320 + ["TAIL_GUIDANCE: 需人工核准冲突"]
    )
    (directory / "AGENTS.md").write_bytes(guide.encode())
    scenario = sdk_scenario(
        [
            _call("read_source_file", path="src/A.java"),
            _call("read_source_context", path="src/AGENTS.md", line=321),
            sdk_fakes.Reply(text="当前要求与约定存在冲突，请确认。"),
        ]
    )
    project = runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        project.model_runtime.run(
            project.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                "NOTE_SOURCE: 长期约定允许更名",
                expected_version=None,
            )
        )
        assert (
            project.agent.run("USER_SOURCE: 不更名，核对规范冲突").status
            is result.RunStatus.COMPLETED
        )
        messages = scenario.requests[-1]["messages"]
        encoded = json.dumps(messages, ensure_ascii=False)
        assert "USER_SOURCE" in encoded and "NOTE_SOURCE" in encoded
        tool_results = [
            json.loads(item["content"])
            for item in messages
            if item["role"] == "tool"
        ]
        first = tool_results[0]
        assert first["guidelines"][0]["path"] == "src/AGENTS.md"
        assert first["guidelines"][0]["truncated"]
        assert "TAIL_GUIDANCE" in tool_results[1]["source"]["code"]
        assert project.repair.store.load().status == "idle"
        assert (directory / "AGENTS.md").read_bytes() == guide.encode()
        assert instructions.SYSTEM.count("## ") == 6
        assert "不能用 no_change 结束任务" in instructions.SYSTEM
        assert "用户已经解决的歧义或撤回的要求" in instructions.SYSTEM
    finally:
        project.close()


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("write_memory", {"path": "topic.md", "content": "invalid name"}),
        (
            "propose_patch",
            {
                "edits": [
                    {
                        "path": "A.java",
                        "replacements": [{"old_text": True, "new_text": ""}],
                    }
                ],
                "rationale": "invalid nested type",
            },
        ),
        ("search_symbols", {"query": "A", "kinds": True}),
        ("get_scan_findings", {"scan_id": "a" * 32, "offset": 1.5}),
    ],
)
def test_sdk_rejects_invalid_local_arguments_before_business(
    tmp_path,
    sdk_scenario,
    tool_name,
    arguments,
):
    (tmp_path / "A.java").write_bytes(b"class A {}")
    scenario = sdk_scenario(
        [_call(tool_name, **arguments), sdk_fakes.Reply(text="参数需更正")]
    )
    project = runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        assert (
            project.agent.run("读取并核查参数").status
            is result.RunStatus.COMPLETED
        )
        errors = [
            message["content"]
            for message in scenario.requests[-1]["messages"]
            if message["role"] == "tool"
        ]
        assert len(errors) == 1
        expected = {
            "write_memory": ("extra_forbidden", ["path"]),
            "propose_patch": (
                "string_type",
                ["edits", 0, "replacements", 0, "old_text"],
            ),
            "search_symbols": ("list_type", ["kinds"]),
            "get_scan_findings": ("int_type", ["offset"]),
        }
        error_type, location = expected[tool_name]
        assert any(
            item["type"] == error_type and item["loc"] == location
            for item in json.loads(errors[0])["error"]
        )
        assert not project.model_runtime.run(project.memory.paths())
        assert not project.repair.store.path.exists()
        assert not project.symbol_index.db_path.exists()
        assert (tmp_path / "A.java").read_bytes() == b"class A {}"
    finally:
        project.close()
