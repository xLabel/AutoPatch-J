from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.index_service import IndexService, IndexEntry
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.cli.app import AutoPatchCLI
from autopatch_j.agent.llm_client import LLMResponse, ToolCall

@pytest.fixture
def setup_env(tmp_path: Path):
    repo_root = tmp_path
    
    # 建立目录结构
    (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
    
    # 1. 正常 Java 文件
    (repo_root / "src" / "main" / "java" / "demo" / "LegacyConfig.java").write_text(
        "public class LegacyConfig { void isDebug() { config.getMode().equals(\"debug\"); } }", encoding="utf-8"
    )
    (repo_root / "src" / "main" / "java" / "demo" / "AppConfig.java").write_text(
        "public class AppConfig {\n    public AppConfig(String mode) {\n        this.mode = mode;\n    }\n}", encoding="utf-8"
    )
    (repo_root / "src" / "main" / "java" / "demo" / "User.java").write_text(
        "public class User {\n    public User(String name) {\n        this.name = name;\n    }\n}", encoding="utf-8"
    )
    
    # 2. 非 Java 文件 (MD)
    (repo_root / "README.md").write_text("# This is a MD file\n" * 300, encoding="utf-8")
    
    # 3. 巨型文件模拟 (超过 3000 行)
    giant_content = "public class Giant {\n" + "\n".join([f"    // line {i}" for i in range(4000)]) + "\n}\n"
    (repo_root / "src" / "main" / "java" / "demo" / "Giant.java").write_text(giant_content, encoding="utf-8")
    
    artifacts = ArtifactManager(repo_root)
    indexer = IndexService(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)
    
    indexer.perform_rebuild()
    
    return repo_root, artifacts, indexer, patch_engine, fetcher

def test_scenario_1_single_file_fix(setup_env):
    """场景 1: 单点精准爆破"""
    repo_root, artifacts, indexer, patch_engine, fetcher = setup_env
    
    mock_llm = MagicMock()
    responses = [
        # LLM 准确只扫该文件
        LLMResponse(
            content="扫描文件",
            tool_calls=[ToolCall(name="scan_project", arguments={"scope": ["src/main/java/demo/LegacyConfig.java"]}, call_id="c1")]
        ),
        # 直接提出补丁
        LLMResponse(
            content="提出修复",
            tool_calls=[ToolCall(name="propose_patch", arguments={
                "file_path": "src/main/java/demo/LegacyConfig.java",
                "old_string": "config.getMode().equals(\"debug\")",
                "new_string": "\"debug\".equals(config.getMode())",
                "rationale": "修复 NPE"
            }, call_id="c2")]
        ),
        LLMResponse(content="修复完成")
    ]
    mock_llm.chat.side_effect = responses
    
    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    agent.chat("请检查并修复 src/main/java/demo/LegacyConfig.java 中的漏洞。")
    
    # 验证行为
    assert mock_llm.chat.call_count == 3
    pending = artifacts.fetch_pending_patch()
    assert pending is not None
    assert pending.file_path == "src/main/java/demo/LegacyConfig.java"

def test_scenario_2_pure_inquiry(setup_env):
    """场景 2: 技术探讨 (Pure Inquiry)"""
    repo_root, artifacts, indexer, patch_engine, fetcher = setup_env
    
    mock_llm = MagicMock()
    responses = [
        LLMResponse(content="这段代码的意思是判断模式是否为debug。有空指针风险。")
    ]
    mock_llm.chat.side_effect = responses
    
    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    agent.chat("@LegacyConfig.java 这段代码什么意思")
    
    assert mock_llm.chat.call_count == 1
    assert artifacts.fetch_pending_patch() is None

def test_scenario_3_global_blind_scan(setup_env):
    """场景 3: 全局盲扫"""
    repo_root, artifacts, indexer, patch_engine, fetcher = setup_env
    
    mock_llm = MagicMock()
    responses = [
        LLMResponse(
            content="全局扫描",
            tool_calls=[ToolCall(name="scan_project", arguments={"scope": ["."]}, call_id="c1")]
        ),
        LLMResponse(content="未发现问题")
    ]
    mock_llm.chat.side_effect = responses
    
    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    agent.chat("检查项目代码")
    
    assert mock_llm.chat.call_count == 2
    last_msg = agent.messages[-2]
    assert last_msg["name"] == "scan_project"
    assert "共发现" in last_msg["content"]

def test_scenario_4_irrelevant_file(setup_env):
    """场景 4: 无关文件豁免"""
    repo_root, artifacts, indexer, patch_engine, fetcher = setup_env
    
    entries = indexer.search("README.md")
    content = fetcher.fetch_entry(entries[0])
    
    # 验证物理防线生效：非 Java 文件截断显示
    assert "[系统防线] 非 Java 文件" in content

    mock_llm = MagicMock()
    responses = [
        LLMResponse(content="这是Markdown文件，不涉及Java代码安全问题。")
    ]
    mock_llm.chat.side_effect = responses
    
    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    agent.chat("@README.md 检查漏洞")
    
    assert mock_llm.chat.call_count == 1
    assert artifacts.fetch_pending_patch() is None

def test_scenario_5_directory_mention(setup_env):
    """场景 5: 目录级大范围侦察 (拦截)"""
    repo_root, artifacts, indexer, patch_engine, fetcher = setup_env
    
    # 手工模拟 Agent 拿到一个目录
    dir_entry = IndexEntry(path="src/main/java/demo", name="demo", kind="dir")
    content = fetcher.fetch_entry(dir_entry)
    
    # 验证物理防线拦截生效
    assert "[系统防线]" in content
    assert "目录" in content
    assert "拦截代码全量注入" in content

def test_scenario_7_patch_queue(setup_env):
    """场景 7: 补丁向导模式队列测试"""
    repo_root, artifacts, indexer, patch_engine, fetcher = setup_env
    
    mock_llm = MagicMock()
    responses = [
        LLMResponse(
            content="我将修复两个文件",
            tool_calls=[
                ToolCall(name="propose_patch", arguments={
                    "file_path": "src/main/java/demo/AppConfig.java",
                    "old_string": "    public AppConfig(String mode) {\n        this.mode = mode;\n    }",
                    "new_string": "    public AppConfig(String mode) {\n        if(mode==null)throw new IllegalArgumentException();\n        this.mode = mode;\n    }",
                    "rationale": "Fix 1"
                }, call_id="c1"),
                ToolCall(name="propose_patch", arguments={
                    "file_path": "src/main/java/demo/User.java",
                    "old_string": "    public User(String name) {\n        this.name = name;\n    }",
                    "new_string": "    public User(String name) {\n        if(name==null)throw new IllegalArgumentException();\n        this.name = name;\n    }",
                    "rationale": "Fix 2"
                }, call_id="c2")
            ]
        ),
        LLMResponse(content="修复完成")
    ]
    mock_llm.chat.side_effect = responses
    
    agent = AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)
    agent.chat("修复所有问题")
    
    # 验证行为：两个补丁都应该在队列中，并且因为是栈模式 (LIFO)，第二个补丁在最前面
    queue = artifacts.fetch_pending_patches()
    assert len(queue) == 2
    
    # 获取第一个补丁应该是 User.java (最后生成的)
    pending_1 = artifacts.fetch_pending_patch()
    assert pending_1 is not None
    assert pending_1.file_path == "src/main/java/demo/User.java"
    
    # 弹出第一个补丁后，第二个应该是 AppConfig.java
    artifacts.pop_pending_patch()
    pending_2 = artifacts.fetch_pending_patch()
    assert pending_2 is not None
    assert pending_2.file_path == "src/main/java/demo/AppConfig.java"


def test_scenario_8_init_clears_pending_patches(setup_env):
    repo_root, artifacts, indexer, patch_engine, fetcher = setup_env

    from autopatch_j.core.patch_engine import PatchDraft
    from autopatch_j.validators.java_syntax import SyntaxValidationResult

    artifacts.persist_pending_patch(
        PatchDraft(
            file_path="src/main/java/demo/User.java",
            old_string="old",
            new_string="new",
            diff="diff",
            validation=SyntaxValidationResult(status="ok", message="ok"),
            status="ok",
            message="ok",
            rationale="draft",
            target_check_id=None,
            target_snippet=None,
        )
    )
    assert artifacts.fetch_pending_patch() is not None

    cli = AutoPatchCLI(repo_root)
    cli.handle_init()

    assert cli.artifacts is not None
    assert cli.artifacts.fetch_pending_patch() is None

