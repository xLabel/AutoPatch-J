from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from autopatch_j.scanners import JavaScanner, SemgrepScanner, UnsupportedJavaScanner


@dataclass(slots=True)
class DoctorCheck:
    name: str
    status: str
    message: str


@dataclass(slots=True)
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)


def build_doctor_report(
    repo_root: Path | None,
    scanner: JavaScanner,
    decision_engine_label: str,
    edit_drafter_label: str | None,
) -> DoctorReport:
    checks = [
        build_project_check(repo_root),
        build_scanner_check(repo_root, scanner),
        build_tree_sitter_check(),
        build_openai_decision_check(decision_engine_label),
        build_openai_drafter_check(edit_drafter_label),
    ]
    return DoctorReport(checks=checks)


def build_project_check(repo_root: Path | None) -> DoctorCheck:
    if repo_root is None:
        return DoctorCheck(
            name="project",
            status="unavailable",
            message="No active project. Run /init to initialize repository state.",
        )
    return DoctorCheck(
        name="project",
        status="ok",
        message=f"Active project: {repo_root}",
    )


def build_scanner_check(repo_root: Path | None, scanner: JavaScanner) -> DoctorCheck:
    if isinstance(scanner, UnsupportedJavaScanner):
        return DoctorCheck(
            name="scanner",
            status="error",
            message=(
                "Unsupported Java scanner configured. "
                f"Current scanner: {scanner.label}. Supported scanner: semgrep."
            ),
        )

    if isinstance(scanner, SemgrepScanner):
        semgrep_path = scanner.resolve_binary(repo_root)
        if semgrep_path:
            source = (
                f"configured binary at {semgrep_path}"
                if scanner.binary_path
                else f"PATH at {semgrep_path}"
            )
            return DoctorCheck(
                name="scanner",
                status="ok",
                message=(
                    f"Scanner ready: {scanner.label}. "
                    f"Using semgrep binary from {source}."
                ),
            )
        if scanner.binary_path:
            return DoctorCheck(
                name="scanner",
                status="error",
                message=(
                    f"Scanner configured as {scanner.label}, but the configured semgrep binary "
                    f"is missing or not executable: {scanner.binary_path}"
                ),
            )
        return DoctorCheck(
            name="scanner",
            status="error",
            message=(
                f"Scanner configured as {scanner.label}, but semgrep is not available on PATH. "
                "Set AUTOPATCH_SEMGREP_BIN or install a local runtime binary."
            ),
        )

    return DoctorCheck(
        name="scanner",
        status="ok",
        message=f"Custom scanner configured: {scanner.label}",
    )


def build_tree_sitter_check() -> DoctorCheck:
    has_tree_sitter = importlib.util.find_spec("tree_sitter") is not None
    has_tree_sitter_java = importlib.util.find_spec("tree_sitter_java") is not None
    if has_tree_sitter and has_tree_sitter_java:
        return DoctorCheck(
            name="java_syntax_validator",
            status="ok",
            message="Tree-sitter Java syntax validation is available through Python modules.",
        )
    missing: list[str] = []
    if not has_tree_sitter:
        missing.append("tree_sitter")
    if not has_tree_sitter_java:
        missing.append("tree_sitter_java")
    return DoctorCheck(
        name="java_syntax_validator",
        status="unavailable",
        message=(
            "Tree-sitter Java syntax validation is unavailable. Missing Python modules: "
            f"{', '.join(missing)}. Install packages: tree-sitter, tree-sitter-java."
        ),
    )


def build_openai_decision_check(decision_engine_label: str) -> DoctorCheck:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return DoctorCheck(
            name="openai_decision_engine",
            status="unavailable",
            message="OPENAI_API_KEY is not set. LLM decision making is unavailable.",
        )
    return DoctorCheck(
        name="openai_decision_engine",
        status="ok",
        message=f"OpenAI decision engine is enabled: {decision_engine_label}.",
    )


def build_openai_drafter_check(edit_drafter_label: str | None) -> DoctorCheck:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return DoctorCheck(
            name="openai_edit_drafter",
            status="unavailable",
            message="OPENAI_API_KEY is not set. Patch drafting is unavailable.",
        )
    return DoctorCheck(
        name="openai_edit_drafter",
        status="ok",
        message=f"OpenAI edit drafter is enabled: {edit_drafter_label or 'openai'}.",
    )
