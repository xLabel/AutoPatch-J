from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from autopatch_j.config import GlobalConfig, get_project_state_dir


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
            encoding="utf-8",
        )

        pip_executable = semgrep_venv_bin_dir() / ("pip.exe" if os.name == "nt" else "pip")
        subprocess.run(
            [str(pip_executable), "install", "--quiet", f"semgrep=={version}"],
            check=True,
            capture_output=True,
            encoding="utf-8",
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
