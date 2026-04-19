from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path


@dataclass(slots=True)
class SearchReplaceEdit:
    file_path: str
    old_string: str
    new_string: str


@dataclass(slots=True)
class EditPreview:
    file_path: str
    status: str
    message: str
    occurrences: int
    diff: str


def preview_search_replace(
    repo_root: Path,
    edit: SearchReplaceEdit,
    context_lines: int = 3,
) -> EditPreview:
    target = resolve_repo_file(repo_root, edit.file_path)
    if not target.exists() or not target.is_file():
        return EditPreview(
            file_path=edit.file_path,
            status="error",
            message="Target file does not exist.",
            occurrences=0,
            diff="",
        )

    original = read_text(target)
    occurrences = original.count(edit.old_string)
    if occurrences == 0:
        return EditPreview(
            file_path=edit.file_path,
            status="missing",
            message="old_string was not found in the target file.",
            occurrences=0,
            diff="",
        )
    if occurrences > 1:
        return EditPreview(
            file_path=edit.file_path,
            status="ambiguous",
            message="old_string matched multiple locations in the target file.",
            occurrences=occurrences,
            diff="",
        )

    updated = original.replace(edit.old_string, edit.new_string, 1)
    diff = build_unified_diff(edit.file_path, original, updated, context_lines=context_lines)
    return EditPreview(
        file_path=edit.file_path,
        status="ok",
        message="Edit preview generated successfully.",
        occurrences=1,
        diff=diff,
    )


def apply_search_replace(
    repo_root: Path,
    edit: SearchReplaceEdit,
    context_lines: int = 3,
) -> EditPreview:
    preview = preview_search_replace(repo_root, edit, context_lines=context_lines)
    if preview.status != "ok":
        return preview

    target = resolve_repo_file(repo_root, edit.file_path)
    original = read_text(target)
    updated = original.replace(edit.old_string, edit.new_string, 1)
    target.write_text(updated, encoding="utf-8")
    return preview


def build_unified_diff(
    file_path: str,
    original: str,
    updated: str,
    context_lines: int = 3,
) -> str:
    original_lines = original.splitlines(keepends=True)
    updated_lines = updated.splitlines(keepends=True)
    diff_lines = unified_diff(
        original_lines,
        updated_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=context_lines,
    )
    return "".join(diff_lines)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def resolve_repo_file(repo_root: Path, file_path: str) -> Path:
    candidate = (repo_root / file_path).resolve()
    candidate.relative_to(repo_root.resolve())
    return candidate
