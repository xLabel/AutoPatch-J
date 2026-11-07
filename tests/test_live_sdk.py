"""本次明确授权后才运行的真实模型集成；只操作临时 Java 项目。"""

import dataclasses
import pathlib
import re

import pydantic_ai
from pydantic_ai import exceptions
from pydantic_ai import messages
import pytest

from autopatch_j import config as autopatch_j_config
from autopatch_j.agent import callbacks
from autopatch_j.agent import result
from autopatch_j.application import runtime as project_runtime
from autopatch_j.llm import diagnostics
from autopatch_j.llm import runtime as model_runtime

pytestmark = pytest.mark.live_llm


def authorize(
    runtime: project_runtime.ProjectRuntime, paths: list[str], goal: str
):
    repair = runtime.repair
    initial = repair.store.load()
    started = repair.commit(initial, repair.start(initial, goal, tuple(paths)))
    return repair.approve_scope(started)


@pytest.mark.parametrize("scenario", ["simulation", "content_id"])
def test_live_judgment_respects_business_contract(
    tmp_path: pathlib.Path,
    live_llm_client: model_runtime.ModelRuntime,
    scenario: str,
) -> None:
    if scenario == "simulation":
        originals = {
            "Simulation.java": (
                "import java.util.Random;\nclass Simulation {\n"
                "  int sample() { return new Random(731).nextInt(100); }\n}\n"
            )
        }
        guide = (
            "本模块是可复现的蒙特卡洛模拟样例，sample必须使用种子731产生确定序列，便于复现实验。"
            "这里不生成令牌、口令或任何安全随机值。"
        )
        rule = "insecure-random"
    else:
        originals = {
            "ContentIds.java": """import java.security.MessageDigest;
import java.util.HexFormat;
class ContentIds {
  static String id(byte[] data) throws Exception {
    return HexFormat.of().formatHex(
        MessageDigest.getInstance("MD5").digest(data));
  }
}
""",
            "Lookup.java": (
                "class Lookup { String key(byte[] data) throws Exception {\n"
                '  return "blobs/" + ContentIds.id(data);\n} }\n'
            ),
        }
        guide = (
            "本模块实现内容寻址协议：内容标识明确为MD5(data)的小写十六进制值。"
            "接收方按同一算法计算并查找blobs/<标识>；例如UTF-8的abc对应"
            "900150983cd24fb0d6963f7d28e17f72。Lookup.key必须与接收方计算结果相同。"
            "此值不用于密码、防篡改或身份认证。本次审阅不包含修改双方协议。"
        )
        rule = "weak-crypto-md5"
    for path, original in originals.items():
        (tmp_path / path).write_text(original, encoding="utf-8")
    original_bytes = {
        path: (tmp_path / path).read_bytes() for path in originals
    }
    (tmp_path / "AGENTS.md").write_text(guide, encoding="utf-8")
    project = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), lambda: live_llm_client
    )
    try:
        record = project.evidence.scan(tuple(originals))
        index = next(
            i
            for i, item in enumerate(record.result.findings, 1)
            if item.check_id.endswith(rule)
        )
        handle = f"{record.id}:F{index}"
        authorize(project, list(originals), "审阅并处理当前扫描候选")
        response = project.agent.run(
            f"请结合源码实际用途及项目规范，核实扫描候选 {handle} 是否需要处理并完成本项审阅。"
            "如需修改，准备完整提案供审核。"
        )
        assert response.status is result.RunStatus.COMPLETED
        state = project.repair.store.load()
        assert state.status == "assessed"
        assert (
            state.assessment is not None
            and state.assessment.outcome == "no_change"
        )
        assert state.assessment.reason.strip()
        assert state.proposal is None
        assert {
            path: (tmp_path / path).read_bytes() for path in originals
        } == original_bytes
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == guide
        # 已核实项目知识允许主动保存；本例核对不修改结论、源码与正式规范。
    finally:
        project.close()


def test_live_conflicting_guidance_requires_clarification(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
) -> None:
    original = (
        "import java.time.*;\nclass Dates { Instant start(LocalDate day) {\n"
        "  return day.atStartOfDay(ZoneOffset.UTC).toInstant();\n} }\n"
    )
    guide = "日期转换规范 RULE-UTC：本模块LocalDate转Instant统一使用UTC，禁止使用本地时区。"
    note = "已由团队确认的本模块日期转换约定 NOTE-SHANGHAI：LocalDate转Instant统一使用Asia/Shanghai。"
    (tmp_path / "Dates.java").write_text(original, encoding="utf-8")
    original_bytes = (tmp_path / "Dates.java").read_bytes()
    (tmp_path / "AGENTS.md").write_text(guide, encoding="utf-8")
    project = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), lambda: live_llm_client
    )
    try:
        project.model_runtime.run(
            project.memory.store.write(
                "project/main/MEMORY.md", note, expected_version=None
            )
        )
        authorize(project, ["Dates.java"], "按项目约定审阅并处理日期转换")
        response = project.agent.run(
            "请按适用项目约定审阅并处理 Dates.java 的日期转换；如需修改，准备提案供审核。"
        )
        assert response.status is result.RunStatus.COMPLETED
        state = project.repair.store.load()
        assert state.assessment is not None
        assert state.assessment.outcome == "insufficient_evidence"
        assert state.status == "investigating" and state.proposal is None
        assert (
            "UTC" in state.assessment.reason
            and "Shanghai" in state.assessment.reason
        )
        assert (tmp_path / "Dates.java").read_bytes() == original_bytes
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == guide
        saved_note = project.model_runtime.run(project.memory.show())
        assert saved_note is not None and saved_note.content == note
    finally:
        project.close()


def test_live_withdrawn_requirement_revises_same_proposal(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
) -> None:
    original = (
        "class Formatter { static String format(String value) { "
        'return "plain:" + value; } }'
    )
    source = tmp_path / "Formatter.java"
    source.write_text(original, encoding="utf-8")
    project = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), lambda: live_llm_client
    )
    try:
        authorize(project, ["Formatter.java"], "修复 null 格式化结果")
        first = project.agent.run(
            "修复 Formatter.format 在 value 为 null 时生成 plain:null 的问题。"
            "null 应返回空字符串，非 null 保留 plain: 前缀，请准备提案。"
        )
        assert first.status is result.RunStatus.COMPLETED
        prior = project.repair.store.load()
        assert prior.proposal is not None
        withdrawn = {
            item for item in prior.constraints if "保留 plain:" in item
        }
        assert withdrawn
        revised = project.agent.run(
            "撤回刚才保留 plain: 前缀的要求；非 null 时改为直接返回 value。"
            "null 仍返回空字符串。请修订同一提案。"
        )
        assert revised.status is result.RunStatus.COMPLETED
        state = project.repair.store.load()
        assert state.proposal is not None
        assert state.issue_id == prior.issue_id
        assert state.proposal.id != prior.proposal.id
        assert withdrawn.isdisjoint(state.constraints)
        assert any("直接返回" in item for item in state.constraints)
        candidate = state.proposal.files[0].candidate.decode("utf-8")
        assert '"plain:"' not in candidate and '""' in candidate
        assert source.read_text(encoding="utf-8") == original
        assert project.repair.apply(state).status == "applied"
    finally:
        project.close()


def test_live_project_knowledge_is_saved_and_rechecked(
    tmp_path: pathlib.Path,
    live_llm_client: model_runtime.ModelRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def safe_error(exc: Exception) -> str:
        if isinstance(exc, exceptions.UnexpectedModelBehavior):
            cause = exc.__cause__
            detail = (
                str(cause) if isinstance(cause, pydantic_ai.ModelRetry) else ""
            )
            return f"{exc}; {detail}"
        return type(exc).__name__

    monkeypatch.setattr(diagnostics, "format_raw_llm_exception", safe_error)
    entry = (
        "class Entry { int configuredLimit() { return PlanService.limit(); } }"
    )
    service = "class PlanService { static int limit() { return 7; } }"
    (tmp_path / "Entry.java").write_text(entry, encoding="utf-8")
    service_path = tmp_path / "PlanService.java"
    service_path.write_text(service, encoding="utf-8")
    project = project_runtime.build_project_runtime(
        tmp_path,
        autopatch_j_config.AppConfig(debug_mode=True),
        lambda: live_llm_client,
    )
    try:
        # 对应使用手册 /init 的索引准备；本场景只验证源码调查和知识维护。
        project.symbol_index.rebuild_index()
        response = project.agent.run(
            "梳理 Entry.configuredLimit 的入口、调用关系和数值来源。"
            "后续还会维护这个模块，请保留有用的已核实项目知识以便下次继续。"
            "只调查，不修改源码。"
        )
        assert (
            response.status is result.RunStatus.COMPLETED
        ), response.final_answer
        paths = project.model_runtime.run(project.memory.paths())
        assert paths
        contents = []
        for path in paths:
            note = project.model_runtime.run(project.memory.show(path))
            assert note is not None
            contents.append(note.content)
        knowledge = "\n".join(contents)
        assert "Entry.java" in knowledge and "PlanService.java" in knowledge
        assert service_path.read_text(encoding="utf-8") == service
        project.new_conversation(project.repair.store.load())
    finally:
        project.close()
    # 模拟使用者后续修改；旧笔记中的数值不能替代当前源码事实。
    service_path.write_text(
        service.replace("return 7", "return 11"), encoding="utf-8"
    )
    restarted = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig.from_env()
    )
    tools = []
    try:
        response = restarted.agent.run(
            "Entry.configuredLimit 现在实际返回多少？请依据当前代码核实。",
            output=callbacks.AgentCallbacks(on_tool_start=tools.append),
        )
        assert response.status is result.RunStatus.COMPLETED
        assert "11" in response.final_answer
        assert set(tools).intersection(
            {"read_source_file", "read_source_context", "read_source_block"}
        )
        assert (tmp_path / "Entry.java").read_text(encoding="utf-8") == entry
        assert restarted.repair.store.load().status == "idle"
    finally:
        restarted.close()


def test_live_compacted_task_resumes_after_limit_and_restart(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
) -> None:
    original = (
        "class Totals { static int total(int unit, int count) { "
        "return unit + count; } }"
    )
    source = tmp_path / "Totals.java"
    source.write_text(original, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "金额计算保持 Java int 运算语义，不增加溢出检查或其他业务规则。",
        encoding="utf-8",
    )
    project = project_runtime.build_project_runtime(
        tmp_path,
        autopatch_j_config.AppConfig(llm_request_limit=2),
        lambda: live_llm_client,
    )
    try:
        authorize(project, ["Totals.java"], "总价应为单价 unit 乘数量 count")
        # 仅缩小测试内的摘要阈值；供应商模型和实际服务端容量不改变。
        assert project.model_runtime.profile is not None
        project.model_runtime.profile = dataclasses.replace(
            project.model_runtime.profile,
            context_window=200_000,
        )
        saved = project.model_runtime.run(project.conversations.load())
        history = [
            message
            for index in range(20)
            for message in (
                messages.ModelRequest(
                    parts=[
                        messages.UserPromptPart(
                            f"已结束的背景讨论 {index}：" + "x" * 5000
                        )
                    ]
                ),
                messages.ModelResponse(
                    parts=[
                        messages.TextPart(
                            "背景事项已说明，不涉及当前修复。" + "y" * 5000
                        )
                    ]
                ),
            )
        ]
        project.model_runtime.run(
            project.conversations.save(saved.summary, history)
        )
        response = project.agent.run(
            "修复 Totals.total：总价应为单价 unit 乘数量 count。"
            "核对当前源码后准备完整提案，等待我审核。"
        )
        assert response.status is result.RunStatus.LIMITED
        captured = project.model_runtime.run(project.conversations.load())
        assert any(
            isinstance(part, messages.CompactionPart)
            for message in captured.messages
            if isinstance(message, messages.ModelRequest)
            for part in message.parts
        )
        assert source.read_text(encoding="utf-8") == original
    finally:
        project.close()
    settings = dataclasses.replace(
        autopatch_j_config.AppConfig.from_env(), llm_request_limit=50
    )
    restarted = project_runtime.build_project_runtime(tmp_path, settings)
    try:
        response = restarted.agent.run(
            "继续刚才的修复，按之前已经确认的目标准备完整提案。"
        )
        assert response.status is result.RunStatus.COMPLETED
        state = restarted.repair.store.load()
        assert state.proposal is not None and state.status == "pending"
        candidate = state.proposal.files[0].candidate.decode("utf-8")
        assert re.search(r"unit\s*\*\s*count|count\s*\*\s*unit", candidate)
        assert source.read_text(encoding="utf-8") == original
        assert restarted.repair.apply(state).status == "applied"
    finally:
        restarted.close()


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
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), lambda: live_llm_client
    )
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
