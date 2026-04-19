from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

IGNORED_DIRS = {
    ".autopatch",
    ".git",
    ".hg",
    ".svn",
    "build",
    "node_modules",
    "out",
    "target",
}


@dataclass(slots=True)
class IndexEntry:
    path: str
    name: str
    kind: str
    ext: str
    is_java: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "ext": self.ext,
            "is_java": self.is_java,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "IndexEntry":
        return cls(
            path=str(data["path"]),
            name=str(data["name"]),
            kind=str(data["kind"]),
            ext=str(data.get("ext", "")),
            is_java=bool(data.get("is_java", False)),
        )


def build_index(repo_root: Path) -> list[IndexEntry]:
    repo_root = repo_root.resolve()
    entries: list[IndexEntry] = []

    for current_root, dirnames, filenames in os.walk(repo_root, topdown=True):
        dirnames[:] = sorted(
            name for name in dirnames if name not in IGNORED_DIRS and not name.startswith(".")
        )
        filenames = sorted(name for name in filenames if not name.startswith("."))

        current_path = Path(current_root)
        for dirname in dirnames:
            resolved = current_path / dirname
            relative = resolved.relative_to(repo_root).as_posix()
            entries.append(
                IndexEntry(
                    path=relative,
                    name=dirname,
                    kind="dir",
                    ext="",
                    is_java=False,
                )
            )

        for filename in filenames:
            resolved = current_path / filename
            relative = resolved.relative_to(repo_root).as_posix()
            suffix = resolved.suffix.lower().lstrip(".")
            entries.append(
                IndexEntry(
                    path=relative,
                    name=filename,
                    kind="file",
                    ext=suffix,
                    is_java=suffix == "java",
                )
            )

    return sorted(entries, key=lambda entry: (entry.kind, entry.path))


def summarize_index(index: list[IndexEntry]) -> dict[str, int]:
    file_count = sum(1 for entry in index if entry.kind == "file")
    java_count = sum(1 for entry in index if entry.is_java)
    dir_count = sum(1 for entry in index if entry.kind == "dir")
    return {
        "files": file_count,
        "java_files": java_count,
        "directories": dir_count,
        "entries": len(index),
    }


def load_index(index_file: Path) -> list[IndexEntry]:
    if not index_file.exists():
        return []
    payload = json.loads(index_file.read_text(encoding="utf-8"))
    return [IndexEntry.from_dict(item) for item in payload]


def save_index(index_file: Path, index: list[IndexEntry]) -> None:
    payload = [entry.to_dict() for entry in index]
    index_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
