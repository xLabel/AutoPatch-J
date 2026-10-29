"""用真实子进程核对系统锁的占用与崩溃释放，不安装扫描器。"""

import os
import subprocess
import sys

import filelock
import pytest

from autopatch_j.scanners.semgrep import runtime


def test_install_lock_released_after_process_death(tmp_path, monkeypatch):
    lock_path = tmp_path / "install.lock"
    monkeypatch.setattr(runtime, "semgrep_install_lock_path", lambda: lock_path)
    script = (
        "import filelock,sys; "
        "lock=filelock.FileLock(sys.argv[1]); lock.acquire(); "
        "print('locked',flush=True); sys.stdin.read()"
    )
    with subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "locked"
            with pytest.raises(filelock.Timeout):
                with runtime.semgrep_install_lock(timeout_seconds=0):
                    pytest.fail("占用期间不应取得锁")
        finally:
            process.kill()
            process.wait(timeout=10)
    assert lock_path.exists()
    with runtime.semgrep_install_lock(timeout_seconds=5):
        assert lock_path.is_file()
