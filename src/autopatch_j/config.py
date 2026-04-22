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
    
    # 1. LLM 核心配置
    llm_api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "").rstrip("/"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))

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

    def get_missing_llm_msg(self) -> str:
        """获取配置指引"""
        return (
            "LLM 核心配置缺失。请确保已设置以下环境变量：\n"
            "1. LLM_API_KEY\n"
            "2. LLM_BASE_URL\n"
            "3. LLM_MODEL (可选，默认 deepseek-chat)"
        )

# 单例模式
GlobalConfig = AppConfig()
