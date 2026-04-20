from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import stat
from pathlib import Path

from autopatch_j.paths import project_state_dir, user_state_dir
from autopatch_j.scanners.base import Finding, ScanResult

DEFAULT_SEMGREP_RULE_PATH = Path("runtime") / "semgrep" / "rules" / "java.yml"
DEFAULT_SEMGREP_CONFIG_LABEL = "autopatch-j/java-default"


class SemgrepScanner:
    name = "Semgrep"

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
        resolved = self.resolve_binary_with_source(repo_root)
        return resolved[0] if resolved is not None else None

    def resolve_binary_with_source(self, repo_root: Path | None = None) -> tuple[str, str] | None:
        del repo_root
        user_runtime = resolve_user_runtime_binary()
        if user_runtime is not None:
            return user_runtime, "user runtime"

        bundled_runtime = resolve_bundled_runtime_binary()
        if bundled_runtime is not None:
            return bundled_runtime, "bundled runtime"
        return None

    def missing_binary_result(self, scope: list[str], targets: list[str]) -> ScanResult:
        message = (
            "Semgrep runtime binary is missing or not executable. Expected user runtime: "
            f"{user_runtime_binary_path()}. Bundled fallback: {bundled_runtime_binary_path()}. "
            "Install it with: python3 scripts/install_semgrep_runtime.py --source /path/to/semgrep"
        )
        return ScanResult(
            engine="semgrep",
            scope=scope,
            targets=targets,
            status="error",
            message=message,
            summary={"total": 0},
            findings=[],
        )


def default_semgrep_config() -> str:
    return str(repo_runtime_rules_path())


def is_default_semgrep_config(config: str) -> bool:
    try:
        return Path(config).resolve() == Path(default_semgrep_config()).resolve()
    except OSError:
        return False


def resolve_user_runtime_binary() -> str | None:
    return resolve_existing_executable(user_runtime_binary_path())


def resolve_bundled_runtime_binary() -> str | None:
    return resolve_existing_executable(bundled_runtime_binary_path())


def resolve_existing_executable(candidate: Path) -> str | None:
    try:
        resolved = candidate.expanduser().resolve()
    except OSError:
        return None
    if not is_executable_file(resolved):
        return None
    return str(resolved)


def is_executable_file(candidate: Path) -> bool:
    return candidate.exists() and candidate.is_file() and os.access(candidate, os.X_OK)


def repo_root_from_module() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "runtime" / "semgrep").exists():
            return candidate
    return Path(__file__).resolve().parents[3]


def user_runtime_binary_path() -> Path:
    return user_state_dir() / "scanners" / "semgrep" / "bin" / platform_tag() / semgrep_binary_name()


def bundled_runtime_binary_path() -> Path:
    return repo_root_from_module() / "runtime" / "semgrep" / "bin" / platform_tag() / semgrep_binary_name()


def repo_runtime_binary_path() -> Path:
    return bundled_runtime_binary_path()


def repo_runtime_rules_path() -> Path:
    return repo_root_from_module() / DEFAULT_SEMGREP_RULE_PATH


def install_bundled_semgrep_runtime() -> tuple[str, str]:
    target = user_runtime_binary_path()
    if resolve_existing_executable(target) is not None:
        return "ok", f"Semgrep user runtime already installed: {target}"

    source = bundled_runtime_binary_path()
    if resolve_existing_executable(source) is None:
        return (
            "missing",
            (
                "AutoPatch-J bundled Semgrep runtime is missing or not executable. "
                f"Expected: {source}. Download an official Semgrep executable, then run: "
                "python3 scripts/install_semgrep_runtime.py --source /path/to/semgrep"
            ),
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    ensure_executable(target)
    return "ok", f"Installed Semgrep user runtime: {target}"


def semgrep_binary_name() -> str:
    return "semgrep.exe" if os.name == "nt" else "semgrep"


def ensure_executable(path: Path) -> None:
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def platform_tag() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if system.startswith("darwin"):
        return f"darwin-{arch}"
    if system.startswith("linux"):
        return f"linux-{arch}"
    if system.startswith("win"):
        return f"windows-{arch}"
    return f"{system}-{arch}"


def build_semgrep_subprocess_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    runtime_root = project_state_dir(repo_root) / "runtime" / "semgrep"
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
                check_id=normalize_check_id(str(item.get("check_id", ""))),
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


def normalize_check_id(raw_check_id: str) -> str:
    for marker in ("autopatch-j.",):
        marker_index = raw_check_id.find(marker)
        if marker_index >= 0:
            return raw_check_id[marker_index:]
    return raw_check_id


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
