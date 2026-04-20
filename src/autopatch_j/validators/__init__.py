from __future__ import annotations

from autopatch_j.validators.java_syntax import (
    SyntaxValidationResult,
    SyntaxValidator,
    TreeSitterJavaValidator,
    ValidatorName,
)
from autopatch_j.validators.rescan import RescanValidationResult, validate_post_apply_rescan

DEFAULT_VALIDATOR_NAME = ValidatorName.TREE_SITTER_JAVA

ALL_VALIDATORS = [
    TreeSitterJavaValidator(),
]


def get_validator(name: ValidatorName) -> SyntaxValidator | None:
    for validator in ALL_VALIDATORS:
        if validator.name == name:
            return validator
    return None
