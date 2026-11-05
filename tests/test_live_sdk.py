"""本次明确授权后才运行的真实模型集成；只操作临时 Java 项目。"""

import pathlib

import pytest

from autopatch_j.agent import result
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.llm import factory
from autopatch_j.llm import runtime as model_runtime

pytestmark = pytest.mark.live_llm


def authorize(runtime, paths, goal):
    repair = runtime.repair
    initial = repair.store.load()
    started = repair.commit(initial, repair.start(initial, goal, tuple(paths)))
    return repair.approve_scope(started)


def test_live_source_evidence_and_reasoning(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
):
    original = "class Demo { int twice(int n) { return n * 2; } }"
    (tmp_path / "Demo.java").write_text(original, encoding="utf-8")
    runtime = cli_runtime.build_cli_runtime(tmp_path, lambda: live_llm_client)
    reasoning = []
    tools = []
    try:
        response = runtime.agent.run(
            "请先调用 read_source_file 读取 Demo.java，"
            "再调用 read_source_context 读取第 1 行，然后解释 twice。只是解释。",
            on_reasoning=reasoning.append,
            on_tool_start=tools.append,
        )
        assert response.status is result.RunStatus.COMPLETED
        assert {"read_source_file", "read_source_context"} <= set(tools)
        assert reasoning and response.final_answer
        assert runtime.repair.store.load().status == "idle"
        assert (tmp_path / "Demo.java").read_text() == original
    finally:
        runtime.close()


def test_live_notebook_and_restart(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
):
    runtime = cli_runtime.build_cli_runtime(tmp_path, lambda: live_llm_client)
    try:
        response = runtime.agent.run(
            "我明确确认长期决定：本项目补丁必须人工审核，"
            "决定标识 APJ-LIVE-DECISION-42。请用 write_memory 写入 MEMORY.md，"
            "并 read_memory 读回确认。"
        )
        assert response.status is result.RunStatus.COMPLETED
        note = runtime.model_runtime.run(runtime.memory.show())
        assert note is not None and "APJ-LIVE-DECISION-42" in note.content
        runtime.model_runtime.run(runtime.conversations.new())
    finally:
        runtime.close()
    restarted = cli_runtime.build_cli_runtime(
        tmp_path, factory.build_model_runtime
    )
    try:
        response = restarted.agent.run(
            "本项目的长期审核决定是什么？包含决定标识。"
        )
        assert response.status is result.RunStatus.COMPLETED
        assert "APJ-LIVE-DECISION-42" in response.final_answer
    finally:
        restarted.close()


def test_live_scan_proposal_review_apply_rescan(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
):
    original = (
        "import java.security.MessageDigest;\n"
        "class Demo { MessageDigest digest() throws Exception {\n"
        'return MessageDigest.getInstance("MD5"); } }\n'
    )
    source = tmp_path / "Demo.java"
    source.write_text(original, encoding="utf-8")
    runtime = cli_runtime.build_cli_runtime(tmp_path, lambda: live_llm_client)
    try:
        authorize(runtime, ["Demo.java"], "将当前 MD5 摘要改为 SHA-256")
        response = runtime.agent.run(
            "请用 scan_java 扫描 Demo.java，核对 MD5 finding 后将摘要改为 SHA-256。"
            "用 propose_patch 提交完整提案并关联扫描句柄，等待我审核。"
        )
        assert response.status is result.RunStatus.COMPLETED
        pending = runtime.repair.store.load()
        assert pending.proposal is not None
        assert (
            pending.status == "pending" and pending.proposal.target is not None
        )
        assert source.read_text() == original
        outcome = runtime.repair.apply(
            pending
        )  # 此临时项目用例明确代表人工批准。
        assert outcome.status == "applied"
        assert (
            outcome.checks[-1].kind == "target_rescan"
            and outcome.checks[-1].status == "passed"
        )
        assert '"SHA-256"' in source.read_text()
    finally:
        runtime.close()


def test_live_multifile_proposal_and_readonly_followup(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
):
    originals = {
        "A.java": "class A { static int defaultCount() { return 1; } }",
        "B.java": (
            "class B { int count = A.defaultCount(); "
            "// expected default: 1\n}"
        ),
    }
    for name, content in originals.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    runtime = cli_runtime.build_cli_runtime(tmp_path, lambda: live_llm_client)
    try:
        authorize(runtime, list(originals), "默认数值应为 2，同步调用方注释")
        response = runtime.agent.run(
            "默认数值应为 2。请读取 A.java 与 B.java，把 A 的默认返回值改为 2。"
            "同步 B 的 expected default 注释为 2，一项多文件完整提案，不改其他内容。"
        )
        assert response.status is result.RunStatus.COMPLETED
        pending = runtime.repair.store.load()
        assert pending.proposal is not None
        assert {item.path for item in pending.proposal.files} == set(originals)
        assert {
            name: (tmp_path / name).read_text() for name in originals
        } == originals
        explained = runtime.agent.run(
            "只解释当前提案为什么需要两个文件，不要修订或改变任务。"
        )
        assert explained.status is result.RunStatus.COMPLETED
        assert runtime.repair.store.load() == pending
        assert runtime.repair.apply(pending).status == "applied"
        assert "return 2" in (tmp_path / "A.java").read_text()
        assert "expected default: 2" in (tmp_path / "B.java").read_text()
    finally:
        runtime.close()
