from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class SyntaxValidationResult:
    status: str
    message: str
    errors: list[str] = field(default_factory=list)


class SyntaxValidator(Protocol):
    def validate(self, file_path: str, source: str) -> SyntaxValidationResult:
        """Validate source text for a given repository-relative file path."""


class TreeSitterJavaValidator:
    def validate(self, file_path: str, source: str) -> SyntaxValidationResult:
        if Path(file_path).suffix.lower() != ".java":
            return SyntaxValidationResult(
                status="skipped",
                message="Syntax validation is only enforced for Java files.",
            )

        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava
        except ImportError:
            return SyntaxValidationResult(
                status="unavailable",
                message="tree_sitter and tree_sitter_java are required for Java syntax validation.",
            )

        language = Language(tsjava.language())
        parser = Parser(language)
        tree = parser.parse(source.encode("utf-8"))
        errors = collect_tree_errors(tree.root_node)
        if errors:
            return SyntaxValidationResult(
                status="error",
                message="Tree-sitter detected Java syntax errors in the edited content.",
                errors=errors,
            )
        return SyntaxValidationResult(
            status="ok",
            message="Tree-sitter validated the edited Java source successfully.",
        )


def collect_tree_errors(node: object) -> list[str]:
    errors: list[str] = []
    walk_tree(node, errors)
    return errors


def walk_tree(node: object, errors: list[str]) -> None:
    is_error = bool(getattr(node, "is_error", False))
    is_missing = bool(getattr(node, "is_missing", False))
    if is_error or is_missing:
        node_type = str(getattr(node, "type", "unknown"))
        start_point = getattr(node, "start_point", None)
        if start_point is not None and len(start_point) >= 2:
            row = int(start_point[0]) + 1
            column = int(start_point[1]) + 1
            location = f"{row}:{column}"
        else:
            location = "unknown"
        kind = "MISSING" if is_missing else "ERROR"
        errors.append(f"{kind} {node_type} at {location}")

    children = getattr(node, "children", [])
    for child in children:
        walk_tree(child, errors)
