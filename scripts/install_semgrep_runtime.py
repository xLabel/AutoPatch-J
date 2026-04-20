from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autopatch_j.scanners.semgrep import platform_tag, semgrep_binary_name  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install a local Semgrep executable into AutoPatch-J runtime.",
    )
    parser.add_argument(
        "--source",
        help="Path to an existing semgrep executable. Defaults to semgrep found on PATH.",
    )
    args = parser.parse_args()

    source = resolve_source(args.source)
    if source is None:
        print(
            "Semgrep executable was not found.\n"
            "Install semgrep first, then run one of:\n"
            "  python3 scripts/install_semgrep_runtime.py\n"
            "  python3 scripts/install_semgrep_runtime.py --source /path/to/semgrep",
            file=sys.stderr,
        )
        return 1

    target = runtime_binary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    ensure_executable(target)
    print(f"Installed Semgrep runtime: {target}")
    return 0


def resolve_source(source_arg: str | None) -> Path | None:
    if source_arg:
        source = Path(source_arg).expanduser().resolve()
        return source if is_executable_file(source) else None

    discovered = shutil.which("semgrep")
    if not discovered:
        return None
    source = Path(discovered).resolve()
    return source if is_executable_file(source) else None


def runtime_binary_path() -> Path:
    return REPO_ROOT / "runtime" / "semgrep" / "bin" / platform_tag() / semgrep_binary_name()


def is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def ensure_executable(path: Path) -> None:
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


if __name__ == "__main__":
    raise SystemExit(main())
