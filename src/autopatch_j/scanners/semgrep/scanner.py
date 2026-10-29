"""使用内置规则运行 Semgrep，并归一化成功与失败结果。"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import override

from autopatch_j import config as autopatch_j_config
from autopatch_j.scanners import contracts
from autopatch_j.scanners import models
from autopatch_j.scanners.semgrep import results
from autopatch_j.scanners.semgrep import runtime
from autopatch_j.scanners.semgrep import targets as semgrep_targets

DEFAULT_SEMGREP_CONFIG_LABEL = "autopatch-j/java-default"


class SemgrepScanner(contracts.StaticScanner):
    """Semgrep Java 扫描器适配器，负责选择目标、调用 runtime 并归一化结果。"""

    name = models.ScannerName.SEMGREP

    def __init__(self) -> None:
        self.config = default_semgrep_config()

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

        resolved_binary = self.resolve_binary(repo_root)
        if resolved_binary is None:
            return self.missing_binary_result(
                scope=list(scope), targets=targets
            )

        command = [
            resolved_binary,
            "scan",
            "--json",
            "--config",
            self.config,
            *self._command_targets(repo_root, targets),
        ]
        timeout = autopatch_j_config.GLOBAL_CONFIG.scanner_timeout
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
                message="Semgrep 输出不是有效 JSON，请检查扫描器运行时状态。",
                findings=[],
            )
        if not isinstance(payload, dict):
            return models.ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message="Semgrep 输出 JSON 结构不符合预期，请检查扫描器运行时状态。",
                findings=[],
            )
        return results.build_semgrep_scan_result(
            payload,
            repo_root=repo_root,
            scope=list(scope),
            targets=targets,
        )

    def resolve_binary(
        self, repo_root: pathlib.Path | None = None
    ) -> str | None:
        """选择当前实际可用的 Semgrep 程序，缺失时返回 None。"""
        resolved = self.resolve_binary_with_source(repo_root)
        return resolved[0] if resolved is not None else None

    def resolve_binary_with_source(
        self, repo_root: pathlib.Path | None = None
    ) -> tuple[str, str] | None:
        """返回可执行程序及来源标签，不自动安装。"""
        del repo_root
        user_runtime = runtime.resolve_managed_semgrep_binary()
        if user_runtime is not None:
            return user_runtime, "AutoPatch-J 管理的 Semgrep"
        return None

    @override
    def get_meta(
        self, repo_root: pathlib.Path | None = None
    ) -> models.ScannerMeta:
        """返回 Semgrep 当前实际可用性及版本信息。"""
        resolved = self.resolve_binary_with_source(repo_root)
        if resolved is None:
            return models.ScannerMeta(
                name=self.name,
                is_implemented=True,
                status="未就绪 (Runtime Missing)",
                version=autopatch_j_config.GLOBAL_CONFIG.semgrep_version,
                description="核心扫描引擎，支持自定义 Java 安全规则集。",
                availability="unavailable",
                reason="AutoPatch-J 管理的 Semgrep 缺失或不可执行，请执行 /init。",
            )

        return models.ScannerMeta(
            name=self.name,
            is_implemented=True,
            status="就绪 (Ready)",
            version=autopatch_j_config.GLOBAL_CONFIG.semgrep_version,
            description="核心扫描引擎，支持自定义 Java 安全规则集。",
            availability="ready",
            reason=resolved[1],
        )

    def missing_binary_result(
        self, scope: list[str], targets: list[str]
    ) -> models.ScanResult:
        """构造扫描器不可用结果，不将其当作零 finding。"""
        message = (
            "AutoPatch-J 管理的 Semgrep 缺失或不可执行。请执行"
            " /init 初始化 scanner runtime。"
        )
        return models.ScanResult(
            engine="semgrep",
            scope=scope,
            targets=targets,
            status="error",
            message=message,
            findings=[],
        )

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
