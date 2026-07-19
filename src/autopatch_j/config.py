from __future__ import annotations

import json
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
    llm_extra_body_error: str | None

    # 可选值：standard、bailian-dsml；阿里云百炼旧版 DeepSeek DSML 流式输出使用 bailian-dsml。
    llm_stream_dialect: str

    # 模型 context 容量；未知模型必须显式配置 window。
    llm_context_window: int | None
    llm_max_output_tokens: int | None

    # 仅 "true" 开启调试输出。
    debug_mode: bool

    # 扫描器配置
    semgrep_version: str
    semgrep_install_lock_timeout: int
    scanner_timeout: int

    # 索引器配置
    ignored_dirs: set[str] = field(default_factory=lambda: set(IGNORED_DIRS))

    # 审计配置
    audit_batch_limit: int = 4

    @classmethod
    def from_env(cls) -> "AppConfig":
        llm_extra_body = os.getenv("AUTOPATCH_LLM_EXTRA_BODY", "{}")
        return cls(
            llm_api_key = os.getenv("AUTOPATCH_LLM_API_KEY", ""),
            llm_base_url = os.getenv("AUTOPATCH_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            llm_model = os.getenv("AUTOPATCH_LLM_MODEL", "deepseek-v4-flash"),
            llm_reasoning_effort = os.getenv("AUTOPATCH_LLM_REASONING_EFFORT"),
            llm_extra_body = llm_extra_body,
            llm_extra_body_error = cls._validate_llm_extra_body(llm_extra_body),
            llm_stream_dialect = os.getenv("AUTOPATCH_LLM_STREAM_DIALECT", "standard"),
            llm_context_window = cls._optional_positive_int_env(
                "AUTOPATCH_LLM_CONTEXT_WINDOW"
            ),
            llm_max_output_tokens = cls._optional_positive_int_env(
                "AUTOPATCH_LLM_MAX_OUTPUT_TOKENS"
            ),
            debug_mode = os.getenv("AUTOPATCH_DEBUG", "false").lower() == "true",
            semgrep_version = "1.160.0",
            semgrep_install_lock_timeout = 600,
            scanner_timeout = 300,
            audit_batch_limit = cls._positive_int_env("AUTOPATCH_AUDIT_BATCH_LIMIT", 4),
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

    def resolve_llm_context_profile(self):
        from autopatch_j.llm.context_window import resolve_context_profile

        return resolve_context_profile(
            model=self.llm_model,
            context_window=self.llm_context_window,
            max_output_tokens=self.llm_max_output_tokens,
        )

    @staticmethod
    def _validate_llm_extra_body(value: str) -> str | None:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            return f"AUTOPATCH_LLM_EXTRA_BODY 不是有效 JSON：{exc.msg}"
        if not isinstance(parsed, dict):
            return "AUTOPATCH_LLM_EXTRA_BODY 必须是 JSON object"
        return None

    @staticmethod
    def _positive_int_env(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            return default
        return value if value > 0 else default

    @staticmethod
    def _optional_positive_int_env(name: str) -> int | None:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return None
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是正整数") from exc
        if value <= 0:
            raise ValueError(f"{name} 必须是正整数")
        return value


GlobalConfig = AppConfig.from_env()
