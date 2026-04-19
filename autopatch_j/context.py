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


def build_mention_context_text(
    repo_root: Path,
    parsed: ParsedPrompt,
    max_files: int = 3,
    max_file_lines: int = 80,
    max_file_chars: int = 4000,
) -> str:
    if not parsed.mentions:
        return "(none)"

    sections: list[str] = []
    rendered_files = 0
    for resolution in parsed.mentions:
        if resolution.selected is None:
            sections.append(f"- {resolution.raw}: unresolved")
            continue

        entry = resolution.selected
        resolved_path = repo_root / entry.path
        if entry.kind == "file":
            if rendered_files >= max_files:
                sections.append("- additional file mentions omitted because mention context limit was reached")
                break
            excerpt = read_file_excerpt(
                resolved_path,
                max_lines=max_file_lines,
                max_chars=max_file_chars,
            )
            sections.append(
                f"- {entry.path} ({entry.kind})\n"
                f"```text\n{excerpt}\n```"
            )
            rendered_files += 1
            continue

        preview = read_directory_preview(resolved_path)
        sections.append(f"- {entry.path} ({entry.kind})\n{preview}")

    return "\n\n".join(sections) if sections else "(none)"


def read_file_preview(path: Path, max_lines: int = 20, max_chars: int = 1600) -> str:
    preview = read_file_excerpt(path, max_lines=max_lines, max_chars=max_chars)
    return f"  ```\n{indent_block(preview)}\n  ```"


def read_file_excerpt(path: Path, max_lines: int = 20, max_chars: int = 1600) -> str:
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
    return preview


def read_directory_preview(path: Path, max_entries: int = 8) -> str:
    candidates = sorted(item.name for item in path.iterdir() if not item.name.startswith("."))
    preview_items = candidates[:max_entries]
    suffix = " ..." if len(candidates) > max_entries else ""
    listing = ", ".join(preview_items) if preview_items else "(empty)"
    return f"  contents: {listing}{suffix}"


def indent_block(text: str) -> str:
    return "\n".join(f"  {line}" if line else "  " for line in text.splitlines())
