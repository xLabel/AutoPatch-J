from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.scanners.base import ScanResult, Finding
from autopatch_j.agent.llm_client import LLMResponse, ToolCall


def test_full_fix_flow_integrated(tmp_path: Path):
    """
    全流程集成测试：验证思考链、回复、观察结果的多回调链路
    """
    repo_root = tmp_path
    java_file = repo_root / "Auth.java"
    java_file.write_text("public class Auth { String p = \"123\"; }", encoding="utf-8")

    artifacts = ArtifactManager(repo_root)
    indexer = IndexService(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)

    # Mock 扫描结果
    mock_finding = Finding(
        check_id="hardcoded-password",
        path="Auth.java", start_line=1, end_line=1,
        severity="error", message="发现硬编码密码", snippet="String p = \"123\";"
    )
    scan_result = ScanResult(
        engine="semgrep", scope=["."], targets=["Auth.java"], 
        status="ok", message="", findings=[mock_finding]
    )
    artifacts.persist_scan_result(scan_result)

    mock_llm = MagicMock()
    
    # 构造响应序列
    responses = [
        LLMResponse(
            content="发现漏洞，准备获取详情。",
            reasoning_content="分析中...",
            tool_calls=[ToolCall(name="get_finding_detail", arguments={"finding_id": "F1"}, call_id="c1")]
        ),
        LLMResponse(
            content="我已生成补丁。",
            reasoning_content="构思修复中...",
            tool_calls=[ToolCall(name="propose_patch", arguments={
                "file_path": "Auth.java",
                "old_string": "String p = \"123\";",
                "new_string": "String p = System.getenv(\"PWD\");",
                "rationale": "修复漏洞"
            }, call_id="c2")]
        ),
        LLMResponse(content="流程结束。", reasoning_content="收尾。")
    ]

    # 核心：模拟 LLM 触发回调的行为
    def mock_chat_impl(*args, **kwargs):
        resp = responses.pop(0)
        # 模拟流式输出触发回调
        if kwargs.get('on_reasoning_token') and resp.reasoning_content:
            kwargs['on_reasoning_token'](resp.reasoning_content)
        if kwargs.get('on_token') and resp.content:
            kwargs['on_token'](resp.content)
        return resp

    mock_llm.chat.side_effect = mock_chat_impl

    # 定义测试用的回调累加器
    collected_tokens = []
    collected_reasoning = []
    observations = []

    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    
    # 执行 Agent
    agent.chat(
        "请修复漏洞。",
        on_token=lambda t: collected_tokens.append(t),
        on_reasoning=lambda t: collected_reasoning.append(t),
        on_observation=lambda m: observations.append(m)
    )

    # 5. 断言验证
    pending = artifacts.fetch_pending_patch()
    assert pending is not None, "Agent 应成功生成补丁草案"
    assert len(collected_reasoning) > 0, "思考链回调应被触发"
    assert len(observations) > 0, "工具观察回调应被触发"
    assert "System.getenv" in pending.new_string
    print("\n集成测试通过：全链路回调闭环验证成功。")
