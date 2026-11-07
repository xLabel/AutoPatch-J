"""本轮授权的两个真实用例：执行失败反馈及 Java 调用约定取证。"""

import dataclasses
import json
import signal

from pydantic_ai import messages
import pytest

from autopatch_j import config
from autopatch_j.cli import app
from autopatch_j.core.repair import models
from autopatch_j.llm import factory
from tests import agent_helpers
from tests import cli_helpers
from tests import optional_identity

pytestmark = pytest.mark.live_llm
OPTIONAL_RULE = "autopatch-j.java.correctness.optional-get-without-check"


def _last_answer(project):
    saved = project.model_runtime.run(project.conversations.load())
    for message in reversed(saved.messages):
        if isinstance(message, messages.ModelResponse):
            text = "".join(
                part.content
                for part in message.parts
                if isinstance(part, messages.TextPart)
            )
            if text:
                return text
    raise AssertionError("没有保存完成的模型回答")


def test_live_restarted_cli_explains_failed_rescan(
    tmp_path, live_llm_client, monkeypatch
):
    source = tmp_path / "Store.java"
    source.write_text(
        "class Store {\n String load(java.util.Optional<String> value) {\n"
        "  return value.get();\n }\n}\n",
        encoding="utf-8",
    )
    old_handler = signal.getsignal(signal.SIGINT)
    first = app.AutoPatchCli(tmp_path, config.AppConfig())
    restarted = None
    try:
        assert first.runtime is not None
        project = first.runtime
        scan = project.evidence.scan(("Store.java",))
        index = next(
            i
            for i, finding in enumerate(scan.result.findings, 1)
            if finding.check_id == OPTIONAL_RULE
        )
        target, _ = project.evidence.finding(f"{scan.id}:F{index}")
        initial = project.repair.store.load()
        active = project.repair.commit(
            initial,
            project.repair.start(initial, "核查执行反馈", ("Store.java",)),
        )
        active = project.repair.approve_scope(active)
        edit = models.FileEdit(
            path="Store.java",
            replacements=(
                models.Replacement(
                    old_text="return value.get();",
                    new_text="return value.get(); // 此改动故意未解决缺陷，用于验证执行反馈",
                ),
            ),
        )
        pending = project.repair.commit(
            active,
            project.repair.prepare(
                active, (edit,), "测试夹具：保留目标缺陷", target
            ),
        )

        def first_input(command, state, unused_output):
            if command == "apply":
                assert state == pending
            elif command is None:
                assert state.status == "applied"
                assert any(
                    check.kind == "target_rescan" and check.status == "failed"
                    for check in state.checks
                )

        cli_helpers.drive_terminal(first, ["apply"], first_input)
        written = source.read_bytes()
        requests = agent_helpers.record_model_requests(monkeypatch)
        with monkeypatch.context() as construct:
            construct.setattr(
                factory,
                "build_model_runtime",
                lambda _settings: live_llm_client,
            )
            restarted = app.AutoPatchCli(tmp_path, config.AppConfig.from_env())
        assert restarted.runtime is not None
        saved_state = restarted.runtime.repair.store.load()
        answers = []

        def next_input(command, state, unused_output):
            assert source.read_bytes() == written
            if command is None:
                assert state == saved_state
                answers.append(_last_answer(restarted.runtime))

        cli_helpers.drive_terminal(
            restarted,
            [
                "请解释上次 apply 的实际结果：源码是否已经写入、目标复扫是否通过、编译和测试是否执行？"
                "本次只解释已有执行事实，不开始任务、不修订或应用补丁。"
            ],
            next_input,
        )
        projected = agent_helpers.request_binding(requests[0])
        assert (
            projected["status"] == "applied" and not projected["pending_commit"]
        )
        assert any(
            check["kind"] == "target_rescan" and check["status"] == "failed"
            for check in projected["checks"]
        )
        answer = answers[0]
        assert "失败" in answer or "未通过" in answer or "failed" in answer
        assert "写入" in answer or "已应用" in answer or "applied" in answer
        assert (
            "未执行" in answer or "未运行" in answer or "unverified" in answer
        )
        print("真实执行反馈说明：", answer[:1600])
    finally:
        first.clear_runtime()
        if restarted is not None:
            restarted.clear_runtime()
        signal.signal(signal.SIGINT, old_handler)


def test_live_cli_uses_java_references_and_preserves_caller_contract(
    tmp_path, live_llm_client, monkeypatch
):
    originals = {
        "Store.java": (
            "class Store {\n String load(java.util.Optional<String> value) "
            "{ return value.get(); }\n String load(int value) "
            "{ return Integer.toString(value); }\n}\n"
        ),
        "Other.java": (
            'class Other { String load(int value) { return "OTHER:" + value; } '
            "String load(String value) { return value; } }"
        ),
        "ZCheckout.java": (
            "class ZCheckout { String use(Store store, "
            "java.util.Optional<String> value) { try { "
            "return store.load(value); } "
            "catch (java.util.NoSuchElementException missing) "
            '{ return "CALLER_MISSING"; } } }'
        ),
        "ZBatch.java": (
            "class ZBatch { String use(Store store, "
            "java.util.Optional<String> value) { "
            "java.util.function.Function<java.util.Optional<String>, String> "
            "getter = store::load; try { return getter.apply(value); } "
            "catch (java.util.NoSuchElementException missing) "
            '{ return "BATCH_MISSING"; } } }'
        ),
    }
    for number in range(25):
        originals[f"A{number:02}.java"] = (
            f"class A{number} {{ String use(Other other) {{ "
            f"return other.load({number}); }} }}"
        )
    for name, text in originals.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    requests = agent_helpers.record_model_requests(monkeypatch)
    settings = dataclasses.replace(
        config.AppConfig.from_env(), debug_mode=False
    )
    assert settings.llm_model == "deepseek-flash"
    old_handler = signal.getsignal(signal.SIGINT)
    with monkeypatch.context() as construct:
        construct.setattr(
            factory, "build_model_runtime", lambda _settings: live_llm_client
        )
        cli = app.AutoPatchCli(tmp_path, settings)
    try:
        assert cli.runtime is not None
        cli.runtime.symbol_index.rebuild_index()

        def before_input(command, state, output):
            for name, original in originals.items():
                if name != "Store.java" or command in {"allow", "apply"}:
                    assert (tmp_path / name).read_text(
                        encoding="utf-8"
                    ) == original
            if command == "allow":
                assert state.requested_paths == ("Store.java",)
            elif command == "apply":
                assert state.status == "pending" and state.proposal is not None
                assert [item.path for item in state.proposal.files] == [
                    "Store.java"
                ]
                assert (
                    state.proposal.target is not None
                    and state.proposal.target.identity.check_id == OPTIONAL_RULE
                )
                optional_identity.assert_optional_identity(
                    state.proposal.files[0].candidate
                )
                assert (
                    "String load(int value) { return Integer.toString(value); }"
                    in state.proposal.files[0].candidate.decode()
                )
                assert "预览: Store.java" in output
                saved = cli.runtime.model_runtime.run(
                    cli.runtime.conversations.load()
                )
                reference_returns = [
                    json.dumps(part.content, ensure_ascii=False, default=str)
                    for message in saved.messages
                    if isinstance(message, messages.ModelRequest)
                    for part in message.parts
                    if isinstance(part, messages.ToolReturnPart)
                    and part.tool_name == "search_java_references"
                ]
                assert any(
                    "ZCheckout.java" in text and "ZBatch.java" in text
                    for text in reference_returns
                )
                wire = json.dumps(requests, ensure_ascii=False)
                assert "CALLER_MISSING" in wire and "BATCH_MISSING" in wire
            elif command is None:
                assert state.status == "applied"
                assert any(
                    check.kind == "target_rescan" and check.status == "passed"
                    for check in state.checks
                )
                print(
                    "真实引用取证及补丁说明：", _last_answer(cli.runtime)[:1200]
                )

        cli_helpers.drive_terminal(
            cli,
            [
                "请扫描并修复 Store.java 的 Optional.get 问题。先调查仓库中 Store.load 的实际使用约定，"
                "缺值异常要与当前调用方的处理保持一致，已有值（包括空字符串）必须原样返回。"
                "只修改 Store.java，保留 int 重载和无关类型，准备完整补丁供我审核。",
                "allow",
                "apply",
            ],
            before_input,
        )
    finally:
        cli.clear_runtime()
        signal.signal(signal.SIGINT, old_handler)
