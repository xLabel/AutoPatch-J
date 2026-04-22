from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.scanners.base import ScanResult, Finding
from autopatch_j.agent.llm_client import LLMResponse, ToolCall


def test_agent_react_deep_loop(tmp_path: Path):
    """
    深度 ReAct 循环测试：
    1. 用户要求修复漏洞。
    2. Agent 第一轮调用 scan_project。
    3. Observation 返回漏洞 ID (F1)。
    4. Agent 第二轮调用 get_finding_detail。
    5. Observation 返回源码详情。
    6. Agent 第三轮调用 propose_patch。
    7. 验证最终 artifacts 中是否存在 pending patch。
    """
    repo_root = tmp_path
    java_file = repo_root / "src" / "main" / "java" / "demo" / "Legacy.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text("public class Legacy { void t() { a.equals(\"debug\"); } }", encoding="utf-8")

    artifacts = ArtifactManager(repo_root)
    indexer = IndexService(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)

    mock_llm = MagicMock()
    
    # 模拟三轮推理
    responses = [
        # Round 1: 扫描项目
        LLMResponse(
            content="我需要先扫描项目。",
            tool_calls=[ToolCall(name="scan_project", arguments={"scope": ["."]}, call_id="c1")]
        ),
        # Round 2: 获取漏洞详情
        LLMResponse(
            content="扫描到了一个 F1。让我看看详情。",
            tool_calls=[ToolCall(name="get_finding_detail", arguments={"finding_id": "F1"}, call_id="c2")]
        ),
        # Round 3: 提出补丁
        LLMResponse(
            content="我看到漏洞了，现在提出修复。",
            tool_calls=[ToolCall(name="propose_patch", arguments={
                "file_path": "src/main/java/demo/Legacy.java",
                "old_string": "a.equals(\"debug\")",
                "new_string": "\"debug\".equals(a)",
                "rationale": "修复 NPE 风险",
                "associated_finding_id": "F1"
            }, call_id="c3")]
        ),
        # Round 4: 结束
        LLMResponse(content="我已经完成了修复提议。")
    ]
    mock_llm.chat.side_effect = responses

    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    
    # 执行
    agent.chat("请检查并修复项目中的 NPE 漏洞。")

    # 验证断言
    # 1. 验证是否产生了 Pending Patch
    pending = artifacts.fetch_pending_patch()
    assert pending is not None
    assert pending.file_path == "src/main/java/demo/Legacy.java"
    assert pending.rationale == "修复 NPE 风险"
    
    # 2. 验证工具调用次数
    assert mock_llm.chat.call_count == 4
    
    print("\n深度 ReAct 链路测试通过：Agent 已成功完成多轮工具调度并达成最终状态。")

if __name__ == "__main__":
    # 允许直接运行此脚本进行快速验证
    pytest.main([__file__])
