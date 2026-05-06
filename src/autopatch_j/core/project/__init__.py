from autopatch_j.core.project.repo_path import (
    UnsafeRepoPathError,
    normalize_repo_path,
    resolve_repo_path,
    to_repo_relative_path,
    try_resolve_repo_path,
)
from autopatch_j.core.project.scope import ScopeResolver
from autopatch_j.core.project.source_reader import SourceReader
from autopatch_j.core.project.symbol_index import SymbolIndex, SymbolIndexEntry

__all__ = [
    "ScopeResolver",
    "SourceReader",
    "SymbolIndex",
    "SymbolIndexEntry",
    "UnsafeRepoPathError",
    "normalize_repo_path",
    "resolve_repo_path",
    "to_repo_relative_path",
    "try_resolve_repo_path",
]
