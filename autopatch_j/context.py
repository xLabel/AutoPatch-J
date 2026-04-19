from __future__ import annotations

from pathlib import Path

from autopatch_j.mentions import ParsedPrompt


def build_context_preview(repo_root: Path, parsed: ParsedPrompt, max_lines: int = 20) -> str:
    lines = [f"Intent: {parsed.clean_text or '(none)'}"]

    if not parsed.mentions:
        lines.append("Mentioned scope: (none)")
        return "\n".join(lines)

    lines.append("Mentioned scope:")
    for resolution in parsed.mentions:
        if resolution.selected is None:
            lines.append(f"- {resolution.raw}: unresolved")
            continue

        entry = resolution.selected
        lines.append(f"- {entry.path} ({entry.kind})")
        resolved_path = repo_root / entry.path
        if entry.kind == "file":
            preview = read_file_preview(resolved_path, max_lines=max_lines)
            lines.append(preview)
        else:
            preview = read_directory_preview(resolved_path)
            lines.append(preview)

    return "\n".join(lines)


def read_file_preview(path: Path, max_lines: int = 20, max_chars: int = 1600) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="replace")

    selected_lines = content.splitlines()
    preview_lines = selected_lines[:max_lines]
    preview = "\n".join(preview_lines)
    if len(preview) > max_chars:
        preview = preview[: max_chars - 3].rstrip() + "..."
    if len(selected_lines) > max_lines:
        preview += "\n..."
    return f"  ```\n{indent_block(preview)}\n  ```"


def read_directory_preview(path: Path, max_entries: int = 8) -> str:
    candidates = sorted(item.name for item in path.iterdir() if not item.name.startswith("."))
    preview_items = candidates[:max_entries]
    suffix = " ..." if len(candidates) > max_entries else ""
    listing = ", ".join(preview_items) if preview_items else "(empty)"
    return f"  contents: {listing}{suffix}"


def indent_block(text: str) -> str:
    return "\n".join(f"  {line}" if line else "  " for line in text.splitlines())
