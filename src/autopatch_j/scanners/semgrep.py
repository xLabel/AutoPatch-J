from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from autopatch_j.config import GlobalConfig, get_project_state_dir, SEMGREP_RULE_RELATIVE_PATH
from autopatch_j.core.finding_snippet_service import FindingSnippetService
from autopatch_j.scanners.base import Finding, ScannerMeta, ScannerName, ScanResult

DEFAULT_SEMGREP_CONFIG_LABEL = "autopatch-j/java-default"


class SemgrepScanner:
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


def resolve_user_runtime_binary() -> str | None:
    return resolve_existing_executable(user_runtime_binary_path())


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


def user_runtime_binary_path() -> Path:
    return semgrep_venv_bin_dir() / semgrep_binary_name()


def semgrep_runtime_dir() -> Path:
    home = Path.home()
    return home / ".autopatch-j" / "scanners" / "semgrep"


def semgrep_install_lock_path() -> Path:
    return semgrep_runtime_dir() / "install.lock"


def semgrep_venv_dir() -> Path:
    return semgrep_runtime_dir() / "venv"


def semgrep_venv_bin_dir() -> Path:
    if os.name == "nt":
        return semgrep_venv_dir() / "Scripts"
    return semgrep_venv_dir() / "bin"


def semgrep_rules_path() -> Path:
    return Path(__file__).resolve().parent / "resources" / "semgrep" / "rules" / "java.yml"


def install_managed_semgrep_runtime(
    version: str = GlobalConfig.semgrep_version,
    python_executable: str = sys.executable,
) -> tuple[str, str]:
    if resolve_user_runtime_binary() is not None:
        return "ok", f"AutoPatch-J 管理的 Semgrep 已存在：{user_runtime_binary_path()}"

    semgrep_runtime_dir().mkdir(parents=True, exist_ok=True)
    with semgrep_install_lock():
        if resolve_user_runtime_binary() is not None:
            return "ok", f"AutoPatch-J 管理的 Semgrep 已存在：{user_runtime_binary_path()}"

        subprocess.run(
            [python_executable, "-m", "venv", str(semgrep_venv_dir())],
            check=True,
            capture_output=True,
            encoding="utf-8"
        )

        pip_executable = semgrep_venv_bin_dir() / ("pip.exe" if os.name == "nt" else "pip")
        subprocess.run(
            [str(pip_executable), "install", "--quiet", f"semgrep=={version}"],
            check=True,
            capture_output=True,
            encoding="utf-8"
        )
    if resolve_user_runtime_binary() is None:
        return (
            "error",
            f"Semgrep 安装完成，但未在预期路径找到可执行文件：{user_runtime_binary_path()}",
        )
    return "ok", f"已安装 AutoPatch-J 管理的 Semgrep {version}：{user_runtime_binary_path()}"


@contextmanager
def semgrep_install_lock(
    timeout_seconds: int = 600,
) -> Iterator[None]:
    lock_path = semgrep_install_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if resolve_user_runtime_binary() is not None:
                yield
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待 Semgrep 安装锁超时：{lock_path}")
            time.sleep(1)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(f"pid={os.getpid()}\n")
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def semgrep_binary_name() -> str:
    return "semgrep.exe" if os.name == "nt" else "semgrep"


def build_semgrep_subprocess_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    runtime_root = get_project_state_dir(repo_root) / "runtime" / "semgrep"
    config_home = runtime_root / "config"
    cache_home = runtime_root / "cache"
    user_data_dir = config_home / ".semgrep"
    config_home.mkdir(parents=True, exist_ok=True)
    cache_home.mkdir(parents=True, exist_ok=True)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    env["PYTHONUTF8"] = "1"

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
    repo_root: Path,
    scope: list[str],
    targets: list[str],
) -> ScanResult:
    raw_results = payload.get("results", [])
    findings: list[Finding] = []
    snippet_service = FindingSnippetService(repo_root)

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        extra = item.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}
        severity = str(extra.get("severity", "unknown")).lower()

        finding_path = str(item.get("path", "")).replace("\\", "/")
        start_line = int(_nested_number(item, "start", "line"))
        end_line = int(_nested_number(item, "end", "line"))
        fallback_snippet = str(extra.get("lines", "")).strip()

        findings.append(
            Finding(
                check_id=normalize_check_id(str(item.get("check_id", ""))),
                path=finding_path,
                start_line=start_line,
                end_line=end_line,
                severity=severity,
                message=str(extra.get("message", "")),
                rule=extract_rule(extra),
                snippet=snippet_service.fetch_resolved_snippet(
                    file_path=finding_path,
                    start_line=start_line,
                    end_line=end_line,
                    fallback_snippet=fallback_snippet,
                ),
            )
        )

    message = f"Semgrep 扫描完成，发现 {len(findings)} 个问题。"
    return ScanResult(
        engine="semgrep",
        scope=scope,
        targets=targets,
        status="ok",
        message=message,
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
