from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

SEMGREP_RULE_RELATIVE_PATH: Final = "scanners/resources/semgrep/rules/java.yml"

DEFAULT_LLM_MODEL: Final = "deepseek-v4-flash"
DEFAULT_LLM_EXTRA_BODY: Final = "{}"
DEFAULT_LLM_STREAM_DIALECT: Final = "standard"
DEFAULT_SEMGREP_VERSION: Final = "1.160.0"
DEFAULT_SEMGREP_INSTALL_LOCK_TIMEOUT: Final = 600
DEFAULT_SCANNER_TIMEOUT: Final = 300
DEFAULT_IGNORED_DIRS: Final = frozenset(
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
    # LLM_API_KEY 和 LLM_BASE_URL 必填；LLM_BASE_URL 使用 OpenAI 兼容地址。
    llm_api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "").rstrip("/"))

    # 默认使用 deepseek-v4-flash；如供应商支持，也可切换为 qwen-max 等 OpenAI 兼容模型名。
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL))

    # 可选值取决于模型供应商，常见为 low、medium、high、max；不需要时留空。
    llm_reasoning_effort: str | None = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT"))

    # 供应商私有扩展参数，必须是 JSON 字符串；例如 '{"thinking": {"type": "enabled"}}'。
    llm_extra_body: str = field(default_factory=lambda: os.getenv("LLM_EXTRA_BODY", DEFAULT_LLM_EXTRA_BODY))

    # 可选值：standard、bailian-dsml；阿里云百炼旧版 DeepSeek DSML 流式输出使用 bailian-dsml。
    llm_stream_dialect: str = field(
        default_factory=lambda: os.getenv("LLM_STREAM_DIALECT", DEFAULT_LLM_STREAM_DIALECT)
    )

    # 仅 "true" 开启调试输出。
    debug_mode: bool = field(
        default_factory=lambda: os.getenv("AUTOPATCH_DEBUG", "false").lower() == "true"
    )

    # 扫描器配置
    semgrep_version: str = DEFAULT_SEMGREP_VERSION
    semgrep_install_lock_timeout: int = DEFAULT_SEMGREP_INSTALL_LOCK_TIMEOUT
    scanner_timeout: int = DEFAULT_SCANNER_TIMEOUT

    # 索引器配置
    ignored_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_IGNORED_DIRS))

    def is_llm_ready(self) -> bool:
        """检查 LLM 必要配置是否就绪"""
        return bool(self.llm_api_key and self.llm_base_url)

    def get_missing_llm_message(self) -> str:
        """获取配置指引"""
        return (
            "LLM 核心配置缺失。请确保已设置以下环境变量：\n"
            "1. LLM_API_KEY\n"
            "2. LLM_BASE_URL\n"
            f"3. LLM_MODEL (可选，默认 {DEFAULT_LLM_MODEL})"
        )


GlobalConfig = AppConfig()
