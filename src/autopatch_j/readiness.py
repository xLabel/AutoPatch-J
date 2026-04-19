from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path

from autopatch_j.scanners import JavaScanner, SemgrepScanner, UnsupportedJavaScanner


@dataclass(slots=True)
class ReadinessCheck:
    name: str
    status: str
    message: str


@dataclass(slots=True)
class ReadinessReport:
    checks: list[ReadinessCheck] = field(default_factory=list)


def build_readiness_report(
    repo_root: Path | None,
    scanner: JavaScanner,
    planner_label: str,
    edit_drafter_label: str | None,
) -> ReadinessReport:
    checks = [
        build_project_check(repo_root),
        build_scanner_check(repo_root, scanner),
        build_tree_sitter_check(),
        build_llm_planner_check(planner_label),
        build_llm_drafter_check(edit_drafter_label),
    ]
    return ReadinessReport(checks=checks)


def build_project_check(repo_root: Path | None) -> ReadinessCheck:
    if repo_root is None:
        return ReadinessCheck(
            name="project",
            status="unavailable",
            message="No active project. Run /init to initialize repository state.",
        )
    return ReadinessCheck(
        name="project",
        status="ok",
        message=f"Active project: {repo_root}",
    )


def build_scanner_check(repo_root: Path | None, scanner: JavaScanner) -> ReadinessCheck:
    if isinstance(scanner, UnsupportedJavaScanner):
        return ReadinessCheck(
            name="scanner",
            status="error",
            message=(
                "Unsupported Java scanner configured. "
                f"Current scanner: {scanner.label}. Supported scanner: semgrep."
            ),
    )

    if isinstance(scanner, SemgrepScanner):
        resolved = scanner.resolve_binary_with_source(repo_root)
        if resolved:
            semgrep_path, source = resolved
            return ReadinessCheck(
                name="scanner",
                status="ok",
                message=(
                    f"Scanner ready: {scanner.label}. "
                    f"Using semgrep binary from {source} at {semgrep_path}."
                ),
            )
        return ReadinessCheck(
            name="scanner",
            status="error",
            message=(
                f"Scanner configured as {scanner.label}, but the Semgrep runtime binary "
                "is missing or not executable under runtime/semgrep/bin/<platform>/."
            ),
        )

    return ReadinessCheck(
        name="scanner",
        status="ok",
        message=f"Custom scanner configured: {scanner.label}",
    )


def build_tree_sitter_check() -> ReadinessCheck:
    has_tree_sitter = importlib.util.find_spec("tree_sitter") is not None
    has_tree_sitter_java = importlib.util.find_spec("tree_sitter_java") is not None
    if has_tree_sitter and has_tree_sitter_java:
        return ReadinessCheck(
            name="java_syntax_validator",
            status="ok",
            message="Tree-sitter Java syntax validation is available through Python modules.",
        )
    missing: list[str] = []
    if not has_tree_sitter:
        missing.append("tree_sitter")
    if not has_tree_sitter_java:
        missing.append("tree_sitter_java")
    return ReadinessCheck(
        name="java_syntax_validator",
        status="unavailable",
        message=(
            "Tree-sitter Java syntax validation is unavailable. Missing Python modules: "
            f"{', '.join(missing)}. Install packages: tree-sitter, tree-sitter-java."
        ),
    )


def has_llm_api_key() -> bool:
    return bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))


def build_llm_planner_check(planner_label: str) -> ReadinessCheck:
    api_key = has_llm_api_key()
    if not api_key:
        return ReadinessCheck(
            name="llm_planner",
            status="unavailable",
            message=(
                "No LLM API key is set. Set LLM_API_KEY or OPENAI_API_KEY "
                "to enable natural-language agent planning."
            ),
        )
    return ReadinessCheck(
        name="llm_planner",
        status="ok",
        message=f"LLM planner is enabled: {planner_label}.",
    )


def build_llm_drafter_check(edit_drafter_label: str | None) -> ReadinessCheck:
    api_key = has_llm_api_key()
    if not api_key:
        return ReadinessCheck(
            name="llm_patch_drafter",
            status="unavailable",
            message=(
                "No LLM API key is set. Set LLM_API_KEY or OPENAI_API_KEY "
                "to enable patch drafting."
            ),
        )
    return ReadinessCheck(
        name="llm_patch_drafter",
        status="ok",
        message=f"LLM patch drafter is enabled: {edit_drafter_label or 'configured LLM'}.",
    )
