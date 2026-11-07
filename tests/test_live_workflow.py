"""自然业务请求通过真实 CLI、扫描、审核和跨会话笔记完成修复。"""

import dataclasses
import json
import pathlib
import signal

import pydantic_ai
from pydantic_ai import exceptions
from pydantic_ai import messages
import pytest

from autopatch_j import config
from autopatch_j.cli import app
from autopatch_j.core import memory
from autopatch_j.core.repair import models
from autopatch_j.llm import diagnostics
from autopatch_j.llm import factory
from autopatch_j.llm import runtime
from autopatch_j.scanners.semgrep import scanner
from tests import cli_helpers
from tests import java_repair_expectations

pytestmark = pytest.mark.live_llm
OPTIONAL_RULE = "autopatch-j.java.correctness.optional-get-without-check"
DECIMAL_RULE = "autopatch-j.java.correctness.bigdecimal-floating-constructor"


def _assert_reviewed_target(
    cli: app.AutoPatchCli,
    state: models.RepairState,
    path: str,
    rule: str,
    output: str,
) -> None:
    assert cli.runtime is not None
    assert state.status == "pending" and state.proposal is not None
    assert state == cli.runtime.repair.store.load()
    assert state.write_paths == (path,)
    assert [item.path for item in state.proposal.files] == [path]
    assert state.proposal.files[0].syntax.status == "passed"
    target = state.proposal.target
    assert target is not None and target.identity.check_id == rule
    assert cli.runtime.evidence.finding(target.handle)[0] == target
    assert f"预览: {path}" in output and "待审修复" in output


def _assert_applied(state: models.RepairState) -> None:
    assert state.status == "applied"
    assert any(
        c.kind == "target_rescan" and c.status == "passed" for c in state.checks
    )
    assert any(
        c.kind == "build_and_tests" and c.status == "unverified"
        for c in state.checks
    )


def _delivered_memory(
    history: list[messages.ModelMessage], *, tools_only: bool = False
) -> list[str]:
    """只取保存的原生注入或读取结果，不能用数据库里有笔记替代上下文证据。"""
    delivered = []
    for message in history:
        if not isinstance(message, messages.ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, messages.ToolReturnPart) and part.tool_name in {
                "read_memory",
                "search_memory",
            }:
                delivered.append(
                    json.dumps(part.content, ensure_ascii=False, default=str)
                )
            elif (
                not tools_only
                and isinstance(part, messages.UserPromptPart)
                and not isinstance(part.content, str)
            ):
                delivered.extend(
                    item.content
                    for item in part.content
                    if isinstance(item, messages.TextContent)
                    and item.metadata is not None
                    and item.metadata.get("id") == "memory"
                )
    return delivered


def test_live_optional_repair_reuses_generated_memory_after_restart(
    tmp_path: pathlib.Path,
    live_llm_client: runtime.ModelRuntime,
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
    engine = scanner.SemgrepScanner()
    assert engine.probe().status == "ready", "本场景必须使用已就绪的真实扫描器"
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    originals = {}
    for name in ("PortConfig.java", "HostConfig.java"):
        class_name = name.removesuffix(".java")
        originals[name] = (
            f"class {class_name} {{ String read("
            "java.util.Optional<String> value) { return value.get(); } }"
        )
    for name, text in originals.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    initial_bytes = {name: (tmp_path / name).read_bytes() for name in originals}
    settings = dataclasses.replace(config.AppConfig.from_env(), debug_mode=True)
    assert settings.llm_model == "deepseek-flash"
    old_handler = signal.getsignal(signal.SIGINT)
    opened: list[app.AutoPatchCli] = []
    try:
        with monkeypatch.context() as build:
            build.setattr(
                factory,
                "build_model_runtime",
                lambda _settings: live_llm_client,
            )
            first = app.AutoPatchCli(tmp_path, settings)
            opened.append(first)
        assert first.runtime is not None
        first.runtime.symbol_index.rebuild_index()

        def first_input(command, state, output):
            assert (tmp_path / "HostConfig.java").read_bytes() == initial_bytes[
                "HostConfig.java"
            ]
            if command in {"allow", "apply"}:
                assert (
                    tmp_path / "PortConfig.java"
                ).read_bytes() == initial_bytes["PortConfig.java"]
            if command == "allow":
                assert state.requested_paths == ("PortConfig.java",)
                assert "待批准写范围：PortConfig.java" in output
            elif command == "apply":
                _assert_reviewed_target(
                    first,
                    state,
                    "PortConfig.java",
                    OPTIONAL_RULE,
                    output,
                )
                java_repair_expectations.assert_optional_policy(
                    state.proposal.files[0].candidate, "server.port"
                )
            elif command == "/new":
                _assert_applied(state)
                notes = first.runtime.model_runtime.run(
                    first.runtime.memory.paths()
                )
                contents = []
                for path in notes:
                    note = first.runtime.model_runtime.run(
                        first.runtime.memory.show(path)
                    )
                    assert note is not None
                    contents.append(note.content)
                assert any(
                    "CONFIG_MISSING:" in text
                    and "IllegalArgumentException" in text
                    and "orElseThrow" in text
                    for text in contents
                )
            elif command is None:
                assert state.status == "idle"
                assert not first.runtime.model_runtime.run(
                    first.runtime.conversations.load()
                ).messages

        cli_helpers.drive_terminal(
            first,
            [
                "请扫描并修复 PortConfig.java 的配置读取问题，配置键是 server.port。"
                "入参 Optional 不为 null，empty 表示缺失。"
                "这是本项目长期约定，请记住供以后修复使用："
                "缺失配置用 orElseThrow 抛 IllegalArgumentException，"
                "消息以 CONFIG_MISSING: 开头并包含配置键；有值必须原样返回，包括存在的空字符串。请准备补丁供我审核。",
                "allow",
                "apply",
                "/new",
            ],
            first_input,
        )
        assert first.runtime.model_runtime.closed
        restarted = app.AutoPatchCli(tmp_path, settings)
        opened.append(restarted)
        assert restarted.runtime is not None

        def second_input(command, state, output):
            if command in {"allow", "apply"}:
                assert (
                    tmp_path / "HostConfig.java"
                ).read_bytes() == initial_bytes["HostConfig.java"]
            if command == "allow":
                assert state.requested_paths == ("HostConfig.java",)
            elif command == "apply":
                _assert_reviewed_target(
                    restarted,
                    state,
                    "HostConfig.java",
                    OPTIONAL_RULE,
                    output,
                )
                java_repair_expectations.assert_optional_policy(
                    state.proposal.files[0].candidate, "server.host"
                )
                saved = restarted.runtime.model_runtime.run(
                    restarted.runtime.conversations.load()
                )
                assert any(
                    "CONFIG_MISSING:" in text
                    and "IllegalArgumentException" in text
                    for text in _delivered_memory(saved.messages)
                )
            elif command is None:
                _assert_applied(state)

        cli_helpers.drive_terminal(
            restarted,
            [
                "请扫描并按项目约定修复 HostConfig.java 的配置读取，配置键是 server.host，准备补丁供我审核。",
                "allow",
                "apply",
            ],
            second_input,
        )
    finally:
        for opened_cli in opened:
            if opened_cli.runtime is not None:
                opened_cli.runtime.close()
        signal.signal(signal.SIGINT, old_handler)


def test_live_money_repair_reads_topic_memory_before_reviewed_apply(
    tmp_path: pathlib.Path,
    live_llm_client: runtime.ModelRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert scanner.SemgrepScanner().probe().status == "ready"
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    source = tmp_path / "Price.java"
    source.write_text(
        "class Price { java.math.BigDecimal amount() { "
        "return new java.math.BigDecimal(0.10); } }",
        encoding="utf-8",
    )
    original = source.read_bytes()
    old_handler = signal.getsignal(signal.SIGINT)
    opened: list[app.AutoPatchCli] = []
    try:
        with monkeypatch.context() as build:
            build.setattr(
                factory,
                "build_model_runtime",
                lambda _settings: live_llm_client,
            )
            cli = app.AutoPatchCli(tmp_path, config.AppConfig.from_env())
            opened.append(cli)
        assert cli.runtime is not None
        cli.runtime.model_runtime.run(
            cli.runtime.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                "涉及金额常量或 Price 模块时，查阅 money.md 中已确认的项目约定。",
                expected_version=None,
            )
        )
        cli.runtime.model_runtime.run(
            cli.runtime.memory.store.write(
                memory.PREFIX + "money.md",
                "团队已确认：金额常量必须用 BigDecimal 的 String 构造，并保留两位小数；此处金额为十进制 0.10。",
                expected_version=None,
            )
        )

        def before_input(command, state, output):
            if command in {"allow", "apply"}:
                assert source.read_bytes() == original
            if command == "allow":
                assert state.requested_paths == ("Price.java",)
            elif command == "apply":
                _assert_reviewed_target(
                    cli,
                    state,
                    "Price.java",
                    DECIMAL_RULE,
                    output,
                )
                candidate = state.proposal.files[0].candidate
                java_repair_expectations.assert_amount_policy(candidate)
                saved = cli.runtime.model_runtime.run(
                    cli.runtime.conversations.load()
                )
                assert any(
                    "两位小数" in text and "String" in text
                    for text in _delivered_memory(
                        saved.messages, tools_only=True
                    )
                )
            elif command is None:
                _assert_applied(state)

        cli_helpers.drive_terminal(
            cli,
            [
                "请扫描并按项目约定修复 Price.java 的金额精度问题，准备补丁供我审核。",
                "allow",
                "apply",
            ],
            before_input,
        )
    finally:
        for opened_cli in opened:
            if opened_cli.runtime is not None:
                opened_cli.runtime.close()
        signal.signal(signal.SIGINT, old_handler)
