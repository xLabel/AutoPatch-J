"""在当前平台执行示例启动脚本；uv 替身只记录进程参数和工作目录。"""

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(name="launcher", params=["windows", "macos"])
def make_launcher(
    request: pytest.FixtureRequest, tmp_path: pathlib.Path
) -> pathlib.Path:
    """复制脚本到带空格的路径，只运行当前平台对应的入口。"""
    platform = request.param
    if (platform == "windows" and sys.platform != "win32") or (
        platform == "macos" and sys.platform != "darwin"
    ):
        pytest.skip("仅实跑当前平台的启动脚本")
    root = tmp_path / "project with spaces"
    (root / "examples" / "demo-repo").mkdir(parents=True)
    extension = "bat" if platform == "windows" else "sh"
    name = f"run_on_{platform}.{extension}"
    source = pathlib.Path(__file__).resolve().parents[1] / name
    return pathlib.Path(shutil.copyfile(source, root / name))


def _run_launcher(
    launcher: pathlib.Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """重定向输入以避免失败后的交互暂停，保留真实脚本退出码。"""
    command = (
        [os.environ["COMSPEC"], "/d", "/c", launcher.name]
        if sys.platform == "win32"
        else ["/bin/bash", str(launcher)]
    )
    return subprocess.run(
        command,
        cwd=launcher.parent,
        env=env,
        input="",
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )


def test_missing_uv_reports_installation_without_bootstrapping(
    launcher: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    search_path = str(tmp_path / "empty-bin")
    if sys.platform == "darwin":
        search_path += os.pathsep + os.defpath
    env = {**os.environ, "PATH": search_path}
    result = _run_launcher(launcher, env)
    assert result.returncode != 0
    assert "https://docs.astral.sh/uv/getting-started/installation/" in (
        result.stdout + result.stderr
    )
    assert not (launcher.parent / ".venv").exists()


@pytest.mark.parametrize("exit_code", [0, 37])
def test_uv_receives_project_and_demo_directory_and_preserves_exit_code(
    launcher: pathlib.Path, tmp_path: pathlib.Path, exit_code: int
) -> None:
    binary_dir = tmp_path / "fake tools"
    binary_dir.mkdir()
    record = tmp_path / "invocation.json"
    logger = binary_dir / "record_call.py"
    logger.write_text(
        "import json\nimport os\nimport pathlib\nimport sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    sys.exit(0)\n"
        "pathlib.Path(os.environ['UV_TEST_RECORD']).write_text(\n"
        "    json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}),\n"
        "    encoding='utf-8')\n"
        "sys.exit(int(os.environ['UV_TEST_EXIT_CODE']))\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        (binary_dir / "uv.cmd").write_text(
            f'@echo off\n"{sys.executable}" "{logger}" %*\n'
            "exit /b %ERRORLEVEL%\n",
            encoding="utf-8",
        )
    else:
        binary = binary_dir / "uv"
        binary.write_text(
            "#!/bin/bash\n"
            f"exec {shlex.quote(sys.executable)} {shlex.quote(str(logger))} "
            '"$@"\n',
            encoding="utf-8",
        )
        binary.chmod(0o755)
    search_path = str(binary_dir)
    if sys.platform == "darwin":
        search_path += os.pathsep + os.defpath
    env = {
        **os.environ,
        "PATH": search_path,
        "UV_TEST_RECORD": str(record),
        "UV_TEST_EXIT_CODE": str(exit_code),
        "AUTOPATCH_LLM_API_KEY": "",
    }
    result = _run_launcher(launcher, env)
    assert result.returncode == exit_code, result.stdout + result.stderr
    invocation = json.loads(record.read_text(encoding="utf-8"))
    arguments = invocation["args"]
    assert arguments[:2] == ["run", "--project"]
    assert pathlib.Path(arguments[2]).resolve() == launcher.parent.resolve()
    assert arguments[3:] == [
        "--locked",
        "--no-dev",
        "--no-python-downloads",
        "python",
        "-m",
        "autopatch_j",
    ]
    assert pathlib.Path(invocation["cwd"]) == (
        launcher.parent / "examples" / "demo-repo"
    )
    assert not (launcher.parent / ".venv").exists()
