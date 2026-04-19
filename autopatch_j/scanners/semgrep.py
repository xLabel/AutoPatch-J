from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from autopatch_j.scanners.model import Finding, ScanResult


class SemgrepScanner:
    def __init__(self, config: str = "p/java", binary_path: str | None = None) -> None:
        self.config = config
        self.binary_path = binary_path

    @property
    def label(self) -> str:
        return f"semgrep:{self.config}"

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        targets = select_targets(repo_root, scope)
        if not targets:
            return ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=[],
                status="skipped",
                message="No Java files or directories were selected for scanning.",
                summary={"total": 0},
                findings=[],
            )

        resolved_binary = self.resolve_binary(repo_root)
        if resolved_binary is None:
            return self.missing_binary_result(scope=list(scope), targets=targets)

        command = [resolved_binary, "scan", "--json", "--config", self.config, *targets]
        completed = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=build_semgrep_subprocess_env(repo_root),
        )

        if completed.returncode not in {0, 1}:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "semgrep failed"
            return ScanResult(
                engine="semgrep",
                scope=list(scope),
                targets=targets,
                status="error",
                message=stderr,
                summary={"total": 0},
                findings=[],
            )

        payload = json.loads(completed.stdout or "{}")
        return normalize_semgrep_payload(payload, scope=list(scope), targets=targets)

    def resolve_binary(self, repo_root: Path | None = None) -> str | None:
        if self.binary_path:
            return resolve_explicit_binary(self.binary_path, repo_root)
        return shutil.which("semgrep")

    def missing_binary_result(self, scope: list[str], targets: list[str]) -> ScanResult:
        if self.binary_path:
            message = (
                "Configured semgrep binary was not found or is not executable: "
                f"{self.binary_path}"
            )
        else:
            message = "semgrep is not installed or not available on PATH."
        return ScanResult(
            engine="semgrep",
            scope=scope,
            targets=targets,
            status="error",
            message=message,
            summary={"total": 0},
            findings=[],
        )


def resolve_explicit_binary(binary_path: str, repo_root: Path | None = None) -> str | None:
    candidate = Path(binary_path).expanduser()
    if not candidate.is_absolute():
        base_dir = repo_root if repo_root is not None else Path.cwd()
        candidate = base_dir / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    if not os.access(resolved, os.X_OK):
        return None
    return str(resolved)


def build_semgrep_subprocess_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    runtime_root = repo_root / ".autopatch" / "runtime" / "semgrep"
    config_home = runtime_root / "config"
    cache_home = runtime_root / "cache"
    user_data_dir = config_home / ".semgrep"
    config_home.mkdir(parents=True, exist_ok=True)
    cache_home.mkdir(parents=True, exist_ok=True)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    env.setdefault("XDG_CONFIG_HOME", str(config_home))
    env.setdefault("XDG_CACHE_HOME", str(cache_home))
    env.setdefault("SEMGREP_LOG_FILE", str(user_data_dir / "semgrep.log"))
    env.setdefault("SEMGREP_SETTINGS_FILE", str(user_data_dir / "settings.yml"))
    env.setdefault("SEMGREP_VERSION_CACHE_PATH", str(cache_home / "semgrep_version"))
    env.setdefault("SEMGREP_SEND_METRICS", "off")
    env.setdefault("SEMGREP_ENABLE_VERSION_CHECK", "0")

    cert_file = detect_certifi_bundle()
    if cert_file is not None:
        env.setdefault("SSL_CERT_FILE", cert_file)
    return env


def detect_certifi_bundle() -> str | None:
    try:
        import certifi
    except ImportError:
        return None
    return certifi.where()


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


def normalize_semgrep_payload(
    payload: dict[str, object],
    scope: list[str],
    targets: list[str],
) -> ScanResult:
    raw_results = payload.get("results", [])
    findings: list[Finding] = []
    severity_counts: dict[str, int] = {}

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        extra = item.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}
        severity = str(extra.get("severity", "unknown")).lower()
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        findings.append(
            Finding(
                check_id=str(item.get("check_id", "")),
                path=str(item.get("path", "")),
                start_line=int(_nested_number(item, "start", "line")),
                end_line=int(_nested_number(item, "end", "line")),
                severity=severity,
                message=str(extra.get("message", "")),
                rule=extract_rule(extra),
                snippet=str(extra.get("lines", "")).strip(),
            )
        )

    summary = {"total": len(findings), **severity_counts}
    message = f"Semgrep completed with {len(findings)} finding(s)."
    return ScanResult(
        engine="semgrep",
        scope=scope,
        targets=targets,
        status="ok",
        message=message,
        summary=summary,
        findings=findings,
    )


def extract_rule(extra: dict[str, object]) -> str:
    metadata = extra.get("metadata", {})
    if not isinstance(metadata, dict):
        return ""
    cwe = metadata.get("cwe")
    if cwe:
        return str(cwe)
    owasp = metadata.get("owasp")
    if owasp:
        return str(owasp)
    return ""


def _nested_number(payload: dict[str, object], *keys: str) -> int:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return 0
        current = current.get(key, 0)
    try:
        return int(current)
    except (TypeError, ValueError):
        return 0
