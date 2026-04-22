from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.agent.llm_client import LLMResponse, ToolCall


def test_agent_full_fix_flow_with_real_tools(tmp_path: Path):
    """
    全链路真实工具集成测试 (除了 LLM)
    """
    repo_root = tmp_path
    # 1. 准备漏洞源码
    src_dir = repo_root / "src" / "main" / "java" / "demo"
    src_dir.mkdir(parents=True)
    java_file = src_dir / "AppConfig.java"
    java_file.write_text(
        "package demo;\n\n"
        "public class AppConfig {\n"
        "    private String mode;\n"
        "    public AppConfig(String mode) {\n"
        "        this.mode = mode;\n"
        "    }\n"
        "}\n",
        encoding="utf-8"
    )

    # 2. 初始化核心服务
    artifacts = ArtifactManager(repo_root)
    indexer = IndexService(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)
    
    # 构建索引
    indexer.perform_rebuild()

    # 3. 模拟 LLM 行为：扫描 -> 详情 -> 补丁
    mock_llm = MagicMock()
    responses = [
        # Step 1: 扫描
        LLMResponse(
            content="我将先扫描项目以发现漏洞。",
            tool_calls=[ToolCall(name="scan_project", arguments={"scope": ["."]}, call_id="c1")]
        ),
        # Step 2: 发现 F1 后获取详情
        LLMResponse(
            content="扫描到了 F1 (missing-constructor-null-check)。让我看看详情。",
            tool_calls=[ToolCall(name="get_finding_detail", arguments={"finding_id": "F1"}, call_id="c2")]
        ),
        # Step 3: 提出补丁
        LLMResponse(
            content="漏洞确认。现在提出补丁。",
            tool_calls=[ToolCall(name="propose_patch", arguments={
                "file_path": "src/main/java/demo/AppConfig.java",
                "old_string": "    public AppConfig(String mode) {\n        this.mode = mode;\n    }",
                "new_string": "    public AppConfig(String mode) {\n        if (mode == null) throw new IllegalArgumentException();\n        this.mode = mode;\n    }",
                "rationale": "修复构造函数空值检查漏洞",
                "associated_finding_id": "F1"
            }, call_id="c3")]
        ),
        # Step 4: 结束
        LLMResponse(content="流程结束。")
    ]
    mock_llm.chat.side_effect = responses

    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    
    # 4. 执行 Agent 任务
    agent.chat("请检查并修复 AppConfig.java 中的漏洞。")

    # 5. 深度验证
    # A. 验证补丁是否已持久化
    pending = artifacts.fetch_pending_patch()
    assert pending is not None, "补丁应已成功生成并挂起"
    assert "IllegalArgumentException" in pending.new_string
    assert pending.rationale == "修复构造函数空值检查漏洞"
    
    # B. 验证 Diff 是否生成 (证明 PatchEngine 逻辑正常)
    assert "+++" in pending.diff
    assert "AppConfig.java" in pending.diff

    print("\n✅ 全链路测试通过：扫描、发现、补丁起草及持久化均已打通！")

if __name__ == "__main__":
    pytest.main([__file__])
