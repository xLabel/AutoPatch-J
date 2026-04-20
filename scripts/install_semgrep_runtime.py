from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autopatch_j.scanners.semgrep import (  # noqa: E402
    DEFAULT_SEMGREP_VERSION,
    install_managed_semgrep_runtime,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install AutoPatch-J managed Semgrep under ~/.autopatch-j.",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_SEMGREP_VERSION,
        help=f"Semgrep package version to install. Default: {DEFAULT_SEMGREP_VERSION}.",
    )
    args = parser.parse_args()

    status, message = install_managed_semgrep_runtime(version=args.version)
    print(f"{status}: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
