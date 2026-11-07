"""显式读取的不可变配置；无效输入由界面报告，不伪造有效默认配置。"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from typing import Final
from urllib import parse

from pydantic_ai import usage

from autopatch_j import error_messages
from autopatch_j.llm import context_window

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


def discover_repo_root(start_path: pathlib.Path) -> pathlib.Path:
    """返回项目边界：用户启动 CLI 时所在的目录即为根目录。"""
    return start_path.resolve()


def get_project_state_dir(repo_root: pathlib.Path) -> pathlib.Path:
    """获取项目的状态存储目录 (.autopatch-j)"""
    state_dir = repo_root / ".autopatch-j"
    state_dir.mkdir(exist_ok=True)
    return state_dir


@dataclasses.dataclass(frozen=True, slots=True)
class AppConfig:
    """一次启动的配置快照，保存模型错误但不以默认容量代替无效输入。"""

    llm_api_key: str = dataclasses.field(default="", repr=False)
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_context_window: int | None = None
    llm_max_output_tokens: int | None = None
    llm_request_limit: int | None = dataclasses.field(
        default_factory=lambda: usage.UsageLimits().request_limit
    )
    llm_config_error: str | None = None
    debug_mode: bool = False
    semgrep_version: str = "1.160.0"
    semgrep_install_lock_timeout: int = 600
    scanner_timeout: int = 300
    ignored_dirs: frozenset[str] = IGNORED_DIRS

    @classmethod
    def from_env(cls) -> "AppConfig":
        """只在显式调用时读取环境；错误只含变量名，不保存无效原值。"""
        errors: list[str] = []

        def capacity(name: str) -> int | None:
            try:
                return cls._optional_positive_int_env(name)
            except ValueError as exc:
                errors.append(str(exc))
                return None

        window = capacity("AUTOPATCH_LLM_CONTEXT_WINDOW")
        output = capacity("AUTOPATCH_LLM_MAX_OUTPUT_TOKENS")
        try:
            request_limit = cls._request_limit_env()
        except ValueError as exc:
            errors.append(str(exc))
            request_limit = usage.UsageLimits().request_limit
        return cls(
            llm_api_key=os.getenv("AUTOPATCH_LLM_API_KEY", ""),
            llm_base_url=os.getenv(
                "AUTOPATCH_LLM_BASE_URL", "https://api.deepseek.com"
            ).rstrip("/"),
            llm_model=os.getenv(
                "AUTOPATCH_LLM_MODEL", "deepseek-flash"
            ).strip(),
            llm_context_window=window,
            llm_max_output_tokens=output,
            llm_request_limit=request_limit,
            llm_config_error="；".join(errors) if errors else None,
            debug_mode=os.getenv("AUTOPATCH_DEBUG", "false").lower() == "true",
            semgrep_version="1.160.0",
            semgrep_install_lock_timeout=600,
            scanner_timeout=300,
        )

    def is_llm_ready(self) -> bool:
        """检查完整模型配置，不发送请求。"""
        return self.llm_unavailable_reason() is None

    def llm_unavailable_reason(self) -> str | None:
        """返回可公开展示的配置问题；不回显凭据或非法输入。"""
        try:
            self.resolve_llm_context_profile()
        except ValueError as exc:
            return str(exc)
        if not self.llm_api_key.strip():
            return error_messages.API_KEY_REQUIRED
        try:
            url = parse.urlsplit(self.llm_base_url)
            valid_url = url.scheme in {"http", "https"} and bool(url.hostname)
            _ = url.port  # urlsplit 在访问端口时才校验端口是否合法。
        except ValueError:
            valid_url = False
        if not valid_url:
            return error_messages.INVALID_BASE_URL
        if not self.llm_model.strip():
            return error_messages.EMPTY_MODEL
        return None

    def get_missing_llm_message(self) -> str:
        """获取配置指引"""
        return self.llm_unavailable_reason() or "LLM 配置已就绪。"

    def resolve_llm_context_profile(self) -> context_window.ModelContextProfile:
        """解析模型容量；未知部署缺少显式容量时拒绝启动请求。"""
        if self.llm_config_error:
            raise ValueError(self.llm_config_error)
        if self.llm_request_limit is not None and (
            not isinstance(self.llm_request_limit, int)
            or isinstance(self.llm_request_limit, bool)
            or self.llm_request_limit <= 0
        ):
            raise ValueError(
                error_messages.positive_integer(
                    "AUTOPATCH_LLM_REQUEST_LIMIT", allow_none=True
                )
            )
        if not self.llm_model.strip():
            raise ValueError(error_messages.EMPTY_MODEL)
        return context_window.resolve_context_profile(
            model=self.llm_model,
            context_window=self.llm_context_window,
            max_output_tokens=self.llm_max_output_tokens,
        )

    @staticmethod
    def _request_limit_env() -> int | None:
        name = "AUTOPATCH_LLM_REQUEST_LIMIT"
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return usage.UsageLimits().request_limit
        if raw.strip().lower() == "none":
            return None
        try:
            return AppConfig._optional_positive_int_env(name)
        except ValueError as exc:
            raise ValueError(
                error_messages.positive_integer(name, allow_none=True)
            ) from exc

    @staticmethod
    def _optional_positive_int_env(name: str) -> int | None:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return None
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(error_messages.positive_integer(name)) from exc
        if value <= 0:
            raise ValueError(error_messages.positive_integer(name))
        return value
