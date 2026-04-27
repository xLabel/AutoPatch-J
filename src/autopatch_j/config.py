from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# 内部资源路径
SEMGREP_RULE_RELATIVE_PATH = "scanners/resources/semgrep/rules/java.yml"


def discover_repo_root(start_path: Path) -> Path | None:
    """向上查找包含 .git 或 .autopatch-j 的目录"""
    current = start_path.resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists() or (parent / ".autopatch-j").exists():
            return parent
    return None


def get_project_state_dir(repo_root: Path) -> Path:
    """获取项目的状态存储目录 (.autopatch-j)"""
    state_dir = repo_root / ".autopatch-j"
    state_dir.mkdir(exist_ok=True)
    return state_dir


@dataclass(slots=True)
class AppConfig:
    """
    全局配置中心 (Global Configuration)
    职责：统一管理 LLM、扫描器和路径逻辑。
    """
    
    # 1. LLM 核心配置 (通过环境变量注入)

    # API 密钥 (必填)
    llm_api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY"))

    # 兼容 OpenAI 协议的 API 基础地址 (必填，如 https://api.deepseek.com/v1)
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "").rstrip("/"))

    # 调用的模型名称 (默认: deepseek-v4-flash, 也支持 qwen-max 等)
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-v4-flash"))

    # 推理思考力度 (可选)。适用于 OpenAI o1/o3 规范或 DeepSeek V4。可选值: "low", "medium", "high", "max"
    llm_reasoning_effort: str | None = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT"))

    # 扩展请求体 (可选，JSON格式字符串)。黑盒参数逃生舱，用于注入特定厂商的私有参数。
    # 例如原生 DeepSeek V4 开启思考: '{"thinking": {"type": "enabled"}}'
    llm_extra_body: str = field(default_factory=lambda: os.getenv("LLM_EXTRA_BODY", "{}"))

    # 流式响应解析方言 (默认: "standard")。
    # 标准 API 请保持 standard；若使用阿里云百炼的旧版 DeepSeek 接口(包含 <｜DSML｜> 标签)，请配置为 "bailian-dsml"
    llm_stream_dialect: str = field(default_factory=lambda: os.getenv("LLM_STREAM_DIALECT", "standard"))

    # 2. 扫描器运行时配置
    semgrep_version: str = "1.160.0"
    semgrep_install_lock_timeout: int = 600
    scanner_timeout: int = 300
    
    # 3. 索引器过滤配置
    ignored_dirs: set[str] = field(default_factory=lambda: {
        ".autopatch-j", ".git", ".hg", ".svn",
        "build", "node_modules", "out", "target", "venv", ".venv",
        "bin", "obj"
    })

    def is_llm_ready(self) -> bool:
        """检查 LLM 必要配置是否就绪"""
        return bool(self.llm_api_key and self.llm_base_url)

    def get_missing_llm_message(self) -> str:
        """获取配置指引"""
        return (
            "LLM 核心配置缺失。请确保已设置以下环境变量：\n"
            "1. LLM_API_KEY\n"
            "2. LLM_BASE_URL\n"
            "3. LLM_MODEL (可选，默认 deepseek-v4-flash)"
        )

# 单例模式
GlobalConfig = AppConfig()
