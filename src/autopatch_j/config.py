from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- 默认硬编码常量 ---
DEFAULT_LLM_MODEL = "gpt-4o"
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"

DEFAULT_SEMGREP_VERSION = "1.160.0"
SEMGREP_RULE_RELATIVE_PATH = "scanners/resources/semgrep/rules/java.yml"


@dataclass(slots=True)
class AppConfig:
    """
    全局配置中心 (Global Configuration)
    职责：统一管理 LLM、扫描器和索引器的配置项。
    """
    
    # 1. LLM 配置
    llm_api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL)
    llm_base_url: str = field(default_factory=lambda: (
        os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or DEFAULT_LLM_BASE_URL
    ).rstrip("/"))

    # 2. 扫描器配置
    semgrep_version: str = DEFAULT_SEMGREP_VERSION
    semgrep_install_lock_timeout: int = 600
    scanner_timeout: int = 300  # 扫描默认 5 分钟超时
    
    # 3. 索引器配置
    ignored_dirs: set[str] = field(default_factory=lambda: {
        ".autopatch", ".autopatch-j", ".git", ".hg", ".svn",
        "build", "node_modules", "out", "target", "venv", ".venv",
        "bin", "obj"
    })

    def is_llm_ready(self) -> bool:
        """检查 LLM 必要配置是否就绪"""
        return bool(self.llm_api_key)

    def get_missing_llm_msg(self) -> str:
        """获取 LLM 配置缺失的提示信息"""
        return (
            "LLM_API_KEY 环境变量缺失。"
            "如果您使用的是 OpenAI 兼容服务，请同时设置 LLM_BASE_URL 和 LLM_MODEL。"
        )

# 单例模式，方便全局调用
GlobalConfig = AppConfig()
