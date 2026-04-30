from __future__ import annotations

import json
import subprocess
from pathlib import Path

from autopatch_j.config import GlobalConfig
from autopatch_j.scanners.base import ScannerMeta, ScannerName, ScanResult
from autopatch_j.scanners.semgrep_result import (
    extract_rule,
    normalize_check_id,
    normalize_semgrep_payload,
)
from autopatch_j.scanners.semgrep_runtime import (
    build_semgrep_subprocess_env,
    install_managed_semgrep_runtime,
    resolve_user_runtime_binary,
    semgrep_rules_path,
)

DEFAULT_SEMGREP_CONFIG_LABEL = "autopatch-j/java-default"


class SemgrepScanner:
    """Semgrep Java 扫描器适配器，负责选择目标、调用 runtime 并归一化结果。"""

    name = ScannerName.SEMGREP

    def __init__(self) -> None:
        self.config = default_semgrep_config()

    @property
    def label(self) -> str:
        return f"semgrep:{self.config_label}"

    @property
    def config_label(self) -> str:
        if is_default_semgrep_config(self.config):
            return DEFAULT_SEMGREP_CONFIG_LABEL
        return self.config

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        targets = select_targets(repo_root, scope)
        if not targets:
            return ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=[],
                status="skipped",
                message="没有选中可扫描的 Java 文件或目录。",
                findings=[],
            )

        resolved_binary = self.resolve_binary(repo_root)
        if resolved_binary is None:
            return self.missing_binary_result(scope=list(scope), targets=targets)

        command = [resolved_binary, "scan", "--json", "--config", self.config, *targets]
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                encoding="utf-8", 
                check=False,
                env=build_semgrep_subprocess_env(repo_root),
                timeout=GlobalConfig.scanner_timeout
            )
        except subprocess.TimeoutExpired:
            return ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=f"扫描执行超时（上限 {GlobalConfig.scanner_timeout}s），请尝试缩小扫描范围。",
                findings=[],
            )

        if completed.returncode not in {0, 1}:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "semgrep failed"
            return ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=stderr,
                findings=[],
            )

        payload = json.loads(completed.stdout or "{}")
        return normalize_semgrep_payload(
            payload,
            repo_root=repo_root,
            scope=list(scope),
            targets=targets,
        )

    def resolve_binary(self, repo_root: Path | None = None) -> str | None:
        resolved = self.resolve_binary_with_source(repo_root)
        return resolved[0] if resolved is not None else None

    def resolve_binary_with_source(self, repo_root: Path | None = None) -> tuple[str, str] | None:
        user_runtime = resolve_user_runtime_binary()
        if user_runtime is not None:
            return user_runtime, "AutoPatch-J 管理的 Semgrep"
        return None

    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        resolved = self.resolve_binary_with_source(repo_root)
        if resolved is None:
            return ScannerMeta(
                name=self.name,
                is_implemented=True,
                status="未就绪 (Runtime Missing)",
                version=GlobalConfig.semgrep_version,
                description="核心扫描引擎，支持自定义 Java 安全规则集。"
            )

        return ScannerMeta(
            name=self.name,
            is_implemented=True,
            status="就绪 (Ready)",
            version=GlobalConfig.semgrep_version,
            description="核心扫描引擎，支持自定义 Java 安全规则集。"
        )

    def missing_binary_result(self, scope: list[str], targets: list[str]) -> ScanResult:
        message = (
            "AutoPatch-J 管理的 Semgrep 缺失或不可执行。请执行 /init 初始化 scanner runtime。"
        )
        return ScanResult(
            engine="semgrep",
            scope=scope,
            targets=targets,
            status="error",
            message=message,
            findings=[],
        )


def default_semgrep_config() -> str:
    return str(semgrep_rules_path())


def is_default_semgrep_config(config: str) -> bool:
    try:
        return Path(config).resolve() == Path(default_semgrep_config()).resolve()
    except OSError:
        return False


def select_targets(repo_root: Path, scope: list[str]) -> list[str]:
    if not scope:
        return ["."]

    targets: list[str] = []
    for entry in scope:
        candidate = (repo_root / entry).resolve()
        if not candidate.exists():
            continue
        if candidate.is_dir():
            targets.append(Path(entry).as_posix())
            continue
        if candidate.suffix.lower() == ".java":
            targets.append(Path(entry).as_posix())
    return targets
