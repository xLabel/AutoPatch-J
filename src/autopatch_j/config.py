from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

SEMGREP_RULE_RELATIVE_PATH: Final = "scanners/resources/semgrep/rules/java.yml"

IGNORED_DIRS: Final = frozenset(
    {
        ".autopatch-j",
        ".git",
        ".hg",
        ".svn",
        "build",
        "node_modules",
        "out",
        "target",
        "venv",
        ".venv",
        "bin",
        "obj",
    }
)


def discover_repo_root(start_path: Path) -> Path:
    """返回项目边界：用户启动 CLI 时所在的目录即为根目录。"""
    return start_path.resolve()


def get_project_state_dir(repo_root: Path) -> Path:
    """获取项目的状态存储目录 (.autopatch-j)"""
    state_dir = repo_root / ".autopatch-j"
    state_dir.mkdir(exist_ok=True)
    return state_dir


@dataclass(slots=True)
class AppConfig:
    """全局配置中心：统一管理 LLM、扫描器和路径相关配置。"""

    # LLM 配置
    # AUTOPATCH_LLM_API_KEY 和 AUTOPATCH_LLM_BASE_URL 必填；AUTOPATCH_LLM_BASE_URL 使用 OpenAI 兼容地址。
    llm_api_key: str
    llm_base_url: str

    # 默认使用 deepseek-v4-flash；如供应商支持，也可切换为 qwen-max 等 OpenAI 兼容模型名。
    llm_model: str

    # 可选值取决于模型供应商，常见为 low、medium、high、max；不需要时留空。
    llm_reasoning_effort: str | None

    # 供应商私有扩展参数，必须是 JSON 字符串；例如 '{"thinking": {"type": "enabled"}}'。
    llm_extra_body: str

    # 可选值：standard、bailian-dsml；阿里云百炼旧版 DeepSeek DSML 流式输出使用 bailian-dsml。
    llm_stream_dialect: str

    # 仅 "true" 开启调试输出。
    debug_mode: bool

    # 扫描器配置
    semgrep_version: str
    semgrep_install_lock_timeout: int
    scanner_timeout: int

    # 索引器配置
    ignored_dirs: set[str] = field(default_factory=lambda: set(IGNORED_DIRS))

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            llm_api_key = os.getenv("AUTOPATCH_LLM_API_KEY", ""),
            llm_base_url = os.getenv("AUTOPATCH_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            llm_model = os.getenv("AUTOPATCH_LLM_MODEL", "deepseek-v4-flash"),
            llm_reasoning_effort = os.getenv("AUTOPATCH_LLM_REASONING_EFFORT"),
            llm_extra_body = os.getenv("AUTOPATCH_LLM_EXTRA_BODY", "{}"),
            llm_stream_dialect = os.getenv("AUTOPATCH_LLM_STREAM_DIALECT", "standard"),
            debug_mode = os.getenv("AUTOPATCH_DEBUG", "false").lower() == "true",
            semgrep_version = "1.160.0",
            semgrep_install_lock_timeout = 600,
            scanner_timeout = 300,
        )

    def is_llm_ready(self) -> bool:
        """检查 LLM 必要配置是否就绪"""
        return bool(self.llm_api_key and self.llm_base_url)

    def get_missing_llm_message(self) -> str:
        """获取配置指引"""
        return (
            "LLM 核心配置缺失。请确保已设置以下环境变量：\n"
            "1. AUTOPATCH_LLM_API_KEY\n"
            "2. AUTOPATCH_LLM_BASE_URL\n"
            f"3. AUTOPATCH_LLM_MODEL (可选，默认 {self.llm_model})"
        )


GlobalConfig = AppConfig.from_env()
