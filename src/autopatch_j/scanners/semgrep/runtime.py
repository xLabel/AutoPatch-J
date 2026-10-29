"""受管 Semgrep 安装、系统互斥锁及子进程运行环境。"""

from __future__ import annotations

from collections.abc import Generator
import contextlib
import os
import pathlib
import subprocess
import sys

import filelock

try:
    import certifi
except ImportError:
    certifi = None

from autopatch_j import config


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
    version: str = config.GLOBAL_CONFIG.semgrep_version,
    python_executable: str = sys.executable,
) -> tuple[str, str]:
    """持有系统互斥锁安装固定版本，取得锁后复检已安装状态。"""
    if resolve_managed_semgrep_binary() is not None:
        return (
            "ok",
            f"AutoPatch-J 管理的 Semgrep 已存在：{managed_semgrep_binary_path()}",
        )

    semgrep_runtime_dir().mkdir(parents=True, exist_ok=True)
    with semgrep_install_lock():
        if resolve_managed_semgrep_binary() is not None:
            return (
                "ok",
                f"AutoPatch-J 管理的 Semgrep 已存在：{managed_semgrep_binary_path()}",
            )

        venv_result = run_setup_command(
            [python_executable, "-m", "venv", str(semgrep_venv_dir())]
        )
        if venv_result is not None:
            return venv_result

        pip_executable = semgrep_venv_bin_dir() / (
            "pip.exe" if os.name == "nt" else "pip"
        )
        install_result = run_setup_command(
            [str(pip_executable), "install", "--quiet", f"semgrep=={version}"]
        )
        if install_result is not None:
            return install_result
    if resolve_managed_semgrep_binary() is None:
        return (
            "error",
            f"Semgrep 安装完成，但未在预期路径找到可执行文件：{managed_semgrep_binary_path()}",
        )
    return (
        "ok",
        f"已安装 AutoPatch-J 管理的 Semgrep "
        f"{version}"
        f"：{managed_semgrep_binary_path()}",
    )


def run_setup_command(command: list[str]) -> tuple[str, str] | None:
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
        return "error", f"Semgrep 运行时初始化失败：{detail}"
    except OSError as exc:
        return "error", f"Semgrep 运行时初始化失败：{exc}"
    return None


@contextlib.contextmanager
def semgrep_install_lock(
    timeout_seconds: int = 600,
) -> Generator[None]:
    """等待受管安装的系统锁；进程退出自动释放，不以残留文件判断占用。"""
    lock_path = semgrep_install_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # OS 锁随进程退出自动释放；残留文件本身不表示仍被占用。
    with filelock.FileLock(str(lock_path), timeout=timeout_seconds):
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

    cert_file = detect_certifi_bundle()
    if cert_file is not None:
        env.setdefault("SSL_CERT_FILE", cert_file)
    return env


def detect_certifi_bundle() -> str | None:
    """可选取得 certifi 证书路径；缺失时沿用子进程默认信任配置。"""
    return certifi.where() if certifi is not None else None
