from __future__ import annotations

from autopatch_j.validators.java_syntax import (
    SyntaxValidationResult,
    SyntaxValidator,
    TreeSitterJavaValidator,
)
from autopatch_j.validators.rescan import RescanValidationResult, validate_post_apply_rescan


def build_java_syntax_validator() -> TreeSitterJavaValidator:
    return TreeSitterJavaValidator()
