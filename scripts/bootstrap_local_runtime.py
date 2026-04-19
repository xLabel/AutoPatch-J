from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import venv
from pathlib import Path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a project-local virtual environment for AutoPatch-J and install "
            "the Python runtime dependencies plus a local semgrep executable."
        )
    )
    parser.add_argument(
        "--venv",
        default=".venv",
        help="Repository-local virtual environment directory. Default: .venv",
    )
    parser.add_argument(
        "--skip-semgrep",
        action="store_true",
        help="Install only the project dependencies declared in pyproject.toml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands without executing them.",
    )
    return parser.parse_args(argv)


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def venv_bin_dir(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts"
    return venv_dir / "bin"


def venv_python(venv_dir: Path) -> Path:
    name = "python.exe" if os.name == "nt" else "python"
    return venv_bin_dir(venv_dir) / name


def venv_pip(venv_dir: Path) -> Path:
    name = "pip.exe" if os.name == "nt" else "pip"
    return venv_bin_dir(venv_dir) / name


def venv_semgrep(venv_dir: Path) -> Path:
    name = "semgrep.exe" if os.name == "nt" else "semgrep"
    return venv_bin_dir(venv_dir) / name


def ensure_venv(venv_dir: Path, dry_run: bool) -> None:
    if venv_dir.exists():
        return
    if dry_run:
        print(f"+ create venv at {venv_dir}")
        return
    print(f"Creating virtual environment at {venv_dir}")
    venv.EnvBuilder(with_pip=True, clear=False, symlinks=(os.name != "nt")).create(venv_dir)


def run_command(command: list[str], cwd: Path, dry_run: bool) -> None:
    print(f"+ {shlex.join(command)}")
    if dry_run:
        return
    subprocess.run(command, cwd=cwd, check=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = repo_root_from_script()
    venv_dir = (repo_root / args.venv).resolve()

    ensure_venv(venv_dir, dry_run=args.dry_run)

    python_bin = venv_python(venv_dir)
    pip_bin = venv_pip(venv_dir)
    semgrep_bin = venv_semgrep(venv_dir)

    run_command(
        [str(python_bin), "-m", "pip", "install", "--upgrade", "pip", "setuptools>=68", "wheel"],
        cwd=repo_root,
        dry_run=args.dry_run,
    )
    run_command(
        [str(pip_bin), "install", "-e", ".", "--no-build-isolation"],
        cwd=repo_root,
        dry_run=args.dry_run,
    )
    if not args.skip_semgrep:
        run_command([str(pip_bin), "install", "semgrep"], cwd=repo_root, dry_run=args.dry_run)

    print()
    print("Bootstrap complete.")
    print(f"- repo root: {repo_root}")
    print(f"- venv python: {python_bin}")
    print(f"- venv pip: {pip_bin}")
    if args.skip_semgrep:
        print("- semgrep: skipped by --skip-semgrep")
    else:
        print(f"- semgrep bin: {semgrep_bin}")
        print("- scanner lookup:")
        print(f"  AutoPatch-J will detect {semgrep_bin.relative_to(repo_root)} automatically.")
        print("  Run /tools in the CLI to confirm scanner readiness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
