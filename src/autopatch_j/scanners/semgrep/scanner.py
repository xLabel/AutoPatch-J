"""使用内置规则运行 Semgrep，并归一化成功与失败结果。"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import override

from autopatch_j import error_messages
from autopatch_j.scanners import contracts
from autopatch_j.scanners import models
from autopatch_j.scanners.semgrep import results
from autopatch_j.scanners.semgrep import runtime
from autopatch_j.scanners.semgrep import targets as semgrep_targets

DEFAULT_SEMGREP_CONFIG_LABEL = "autopatch-j/java-default"


class SemgrepScanner(contracts.StaticScanner):
    """Semgrep Java 扫描器适配器，负责选择目标、调用 runtime 并归一化结果。"""

    name = models.ScannerName.SEMGREP

    def __init__(
        self, *, required_version: str = "1.160.0", timeout: int = 300
    ) -> None:
        self.config = default_semgrep_config()
        self.required_version = required_version
        self.timeout = timeout

    @property
    def label(self) -> str:
        """扫描器展示名称。"""
        return f"semgrep:{self.config_label}"

    @property
    def config_label(self) -> str:
        """内置规则使用稳定标签，外部规则配置保留其标识。"""
        if is_default_semgrep_config(self.config):
            return DEFAULT_SEMGREP_CONFIG_LABEL
        return self.config

    @override
    def scan(
        self, repo_root: pathlib.Path, scope: list[str]
    ) -> models.ScanResult:
        """仅接受完整成功扫描，进程失败或证据不完整返回失败结果。"""
        targets = semgrep_targets.select_semgrep_targets(repo_root, scope)
        if not targets:
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=[],
                status="skipped",
                message="没有选中可扫描的 Java 文件或目录。",
                findings=[],
            )

        status = self.probe()
        if status.status != "ready" or status.binary is None:
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=status.message,
                findings=[],
            )
        resolved_binary = status.binary

        command = [
            resolved_binary,
            "scan",
            "--json",
            "--config",
            self.config,
            *self._command_targets(repo_root, targets),
        ]
        timeout = self.timeout
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                encoding="utf-8",
                check=False,
                env=runtime.build_semgrep_subprocess_env(repo_root),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=f"扫描执行超时（上限 {timeout}"
                f"s），请尝试缩小扫描范围。",
                findings=[],
            )
        except OSError as exc:
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=f"Semgrep 进程启动失败：{exc}",
                findings=[],
            )

        if completed.returncode not in {0, 1}:
            stderr = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "semgrep failed"
            )
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=stderr,
                findings=[],
            )

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=error_messages.INVALID_SCAN_OUTPUT,
                findings=[],
            )
        if not isinstance(payload, dict):
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=error_messages.INVALID_SCAN_OUTPUT,
                findings=[],
            )
        return results.build_semgrep_scan_result(
            payload,
            repo_root=repo_root,
            scope=list(scope),
            targets=targets,
        )

    @override
    def get_meta(
        self, repo_root: pathlib.Path | None = None
    ) -> models.ScannerMeta:
        """返回 Semgrep 当前实际可用性及版本信息。"""
        del repo_root
        status = self.probe()
        return models.ScannerMeta(
            name=self.name,
            is_implemented=True,
            status=status.status,
            version=status.actual_version or "未确认",
            description="核心扫描引擎，支持自定义 Java 安全规则集。",
            availability="ready" if status.status == "ready" else "unavailable",
            reason=f"要求版本 {status.required_version}；{status.message}",
        )

    def probe(self) -> runtime.RuntimeStatus:
        """状态展示与扫描共享同一个版本探测入口，不缓存历史就绪结论。"""
        return runtime.probe_managed_semgrep(self.required_version)

    def _command_targets(
        self, repo_root: pathlib.Path, targets: list[str]
    ) -> list[str]:
        """将已准入目标转换为扫描命令参数，防止相对路径被当作选项。"""
        normalized_repo_root = repo_root.resolve()
        command_targets: list[str] = []
        for target in targets:
            if target == ".":
                command_targets.append(str(normalized_repo_root))
            else:
                command_targets.append(
                    str((normalized_repo_root / target).resolve())
                )
        return command_targets


def default_semgrep_config() -> str:
    """内置 Java 规则配置路径。"""
    return str(runtime.semgrep_rules_path())


def is_default_semgrep_config(config: str) -> bool:
    """配置是否指向本项目打包的 Java 规则。"""
    try:
        return (
            pathlib.Path(config).resolve()
            == pathlib.Path(default_semgrep_config()).resolve()
        )
    except OSError:
        return False
