from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autopatch_j.scanners.semgrep import (  # noqa: E402
    bundled_runtime_binary_path,
    ensure_executable,
    is_executable_file,
    user_runtime_binary_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install a local Semgrep executable into AutoPatch-J runtime.",
    )
    parser.add_argument(
        "--source",
        help=(
            "Path to an official Semgrep executable. Defaults to AutoPatch-J's "
            "bundled runtime for the current platform."
        ),
    )
    args = parser.parse_args()

    source = resolve_source(args.source)
    if source is None:
        print(
            "Semgrep executable was not found.\n"
            "Put the AutoPatch-J bundled binary under runtime/semgrep/bin/<platform>/, "
            "or download an official Semgrep executable and run:\n"
            "  python3 scripts/install_semgrep_runtime.py --source /path/to/semgrep",
            file=sys.stderr,
        )
        return 1

    target = runtime_binary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    ensure_executable(target)
    print(f"Installed Semgrep runtime: {target}")
    return 0


def resolve_source(source_arg: str | None) -> Path | None:
    if source_arg:
        source = Path(source_arg).expanduser().resolve()
        return source if is_executable_file(source) else None

    source = bundled_runtime_binary_path()
    return source if is_executable_file(source) else None


def runtime_binary_path() -> Path:
    return user_runtime_binary_path()


if __name__ == "__main__":
    raise SystemExit(main())
