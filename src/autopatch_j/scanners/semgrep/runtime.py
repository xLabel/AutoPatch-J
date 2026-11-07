"""受管 Semgrep 安装、系统互斥锁及子进程运行环境。"""

from __future__ import annotations

from collections.abc import Generator
import contextlib
import dataclasses
import os
import pathlib
import re
import subprocess
import sys
from typing import Literal

import certifi
import filelock

from autopatch_j import config
from autopatch_j import error_messages


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """受管程序的实际探测结果；配置版本不能充当探测版本。"""

    status: Literal["missing", "broken", "version_mismatch", "ready"]
    required_version: str
    actual_version: str | None = None
    binary: str | None = None
    message: str = ""


def probe_managed_semgrep(required_version: str) -> RuntimeStatus:
    """有限时运行受管程序 --version；不安装或改用系统程序。"""
    binary = resolve_managed_semgrep_binary()
    if binary is None:
        return RuntimeStatus(
            "missing",
            required_version,
            message=error_messages.SCANNER_UNAVAILABLE,
        )
    env = os.environ.copy()
    env["SEMGREP_ENABLE_VERSION_CHECK"] = "0"
    env["SEMGREP_SEND_METRICS"] = "off"
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            encoding="utf-8",
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        return RuntimeStatus(
            "broken",
            required_version,
            binary=binary,
            message=f"版本探测失败：{exc}",
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:2000]
        return RuntimeStatus(
            "broken",
            required_version,
            binary=binary,
            message=f"版本探测退出 {completed.returncode}：{detail}",
        )
    actual = completed.stdout.strip()
    if re.fullmatch(r"\d+\.\d+\.\d+[a-zA-Z0-9.+-]*", actual) is None:
        return RuntimeStatus(
            "broken",
            required_version,
            binary=binary,
            message="版本探测未返回可识别的 Semgrep 版本。",
        )
    if actual != required_version:
        label = actual[:200] or "空输出"
        return RuntimeStatus(
            "version_mismatch",
            required_version,
            actual[:200],
            binary,
            f"要求 {required_version}，实际探测为 {label}；请执行 /init。",
        )
    return RuntimeStatus(
        "ready", required_version, actual, binary, "受管 Semgrep 版本已核实。"
    )


def resolve_managed_semgrep_binary() -> str | None:
    """返回实际可执行的受管 Semgrep 路径，缺失时为 None。"""
    return resolve_existing_executable(managed_semgrep_binary_path())


def resolve_existing_executable(candidate: pathlib.Path) -> str | None:
    """仅返回解析后实际可执行的文件，不创建或安装程序。"""
    try:
        resolved = candidate.expanduser().resolve()
    except OSError:
        return None
    if not is_executable_file(resolved):
        return None
    return str(resolved)


def is_executable_file(candidate: pathlib.Path) -> bool:
    """目标是否存在、为普通文件且可执行。"""
    return (
        candidate.exists()
        and candidate.is_file()
        and os.access(candidate, os.X_OK)
    )


def managed_semgrep_binary_path() -> pathlib.Path:
    """受管虚拟环境中的预期 Semgrep 可执行路径。"""
    return semgrep_venv_bin_dir() / semgrep_binary_name()


def semgrep_runtime_dir() -> pathlib.Path:
    """当前用户的受管 Semgrep 目录，不创建目录。"""
    home = pathlib.Path.home()
    return home / ".autopatch-j" / "scanners" / "semgrep"


def semgrep_install_lock_path() -> pathlib.Path:
    """受管安装的系统互斥锁路径。"""
    return semgrep_runtime_dir() / "install.lock"


def semgrep_venv_dir() -> pathlib.Path:
    """受管 Semgrep 的独立虚拟环境目录。"""
    return semgrep_runtime_dir() / "venv"


def semgrep_venv_bin_dir() -> pathlib.Path:
    """当前平台的受管可执行程序目录。"""
    if os.name == "nt":
        return semgrep_venv_dir() / "Scripts"
    return semgrep_venv_dir() / "bin"


def semgrep_rules_path() -> pathlib.Path:
    """随本项目打包的 Java 规则文件。"""
    return (
        pathlib.Path(__file__).resolve().parent.parent
        / "resources"
        / "semgrep"
        / "rules"
        / "java.yml"
    )


def install_managed_semgrep_runtime(
    version: str,
    python_executable: str = sys.executable,
    *,
    lock_timeout: int = 600,
) -> RuntimeStatus:
    """显式准备用户级受管程序，锁内复检并验证安装后的实际版本。

    Args:
        version: 要求的准确 Semgrep 版本。
        python_executable: 创建受管虚拟环境的解释器。
        lock_timeout: 安装互斥锁的最长等待秒数。

    Returns:
        实际探测结果或安装失败原因；命令完成不等于程序就绪。

    Raises:
        OSError: 创建目录失败或安装锁等待超时，未伪装成安装成功。
    """
    semgrep_runtime_dir().mkdir(parents=True, exist_ok=True)
    with semgrep_install_lock(lock_timeout):
        current = probe_managed_semgrep(version)
        if current.status == "ready":
            return current

        venv_result = run_setup_command(
            [python_executable, "-m", "venv", str(semgrep_venv_dir())]
        )
        if venv_result is not None:
            return RuntimeStatus("broken", version, message=venv_result)

        pip_executable = semgrep_venv_bin_dir() / (
            "pip.exe" if os.name == "nt" else "pip"
        )
        install_result = run_setup_command(
            [
                str(pip_executable),
                "install",
                "--quiet",
                "--upgrade",
                "--force-reinstall",
                f"semgrep=={version}",
            ]
        )
        if install_result is not None:
            return RuntimeStatus("broken", version, message=install_result)
        return probe_managed_semgrep(version)


def run_setup_command(command: list[str]) -> str | None:
    """捕获安装命令的进程失败和启动异常，返回可显示的失败原因。"""
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return f"Semgrep 运行时初始化失败：{detail[:2000]}"
    except OSError as exc:
        return f"Semgrep 运行时初始化失败：{exc}"
    return None


@contextlib.contextmanager
def semgrep_install_lock(
    timeout_seconds: int = 600,
) -> Generator[None]:
    """等待受管安装的系统锁；进程退出自动释放，不以残留文件判断占用。"""
    lock_path = semgrep_install_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # OS 锁随进程退出自动释放；残留文件本身不表示仍被占用。
    with filelock.FileLock(
        str(lock_path), timeout=timeout_seconds, preserve_lock_file=True
    ):
        yield


def semgrep_binary_name() -> str:
    """当前平台的 Semgrep 可执行文件名。"""
    return "semgrep.exe" if os.name == "nt" else "semgrep"


def build_semgrep_subprocess_env(repo_root: pathlib.Path) -> dict[str, str]:
    """为扫描进程准备独立配置与缓存目录，默认关闭 metrics。"""
    env = os.environ.copy()
    runtime_root = (
        config.get_project_state_dir(repo_root) / "runtime" / "semgrep"
    )
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
    env.setdefault(
        "SEMGREP_VERSION_CACHE_PATH", str(cache_home / "semgrep_version")
    )
    env.setdefault("SEMGREP_SEND_METRICS", "off")
    env.setdefault("SEMGREP_ENABLE_VERSION_CHECK", "0")

    env.setdefault("SSL_CERT_FILE", detect_certifi_bundle())
    return env


def detect_certifi_bundle() -> str:
    """取得固定依赖 certifi 的证书路径。"""
    return certifi.where()
