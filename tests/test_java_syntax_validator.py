from __future__ import annotations

import unittest

from autopatch_j.validators.java_syntax import SyntaxValidationResult, TreeSitterJavaValidator


class JavaSyntaxValidatorTests(unittest.TestCase):
    def test_validator_skips_non_java_files(self) -> None:
        validator = TreeSitterJavaValidator()
        result = validator.validate("notes.txt", "plain text")
        self.assertEqual(result.status, "skipped")

    def test_validation_result_defaults_errors_list(self) -> None:
        result = SyntaxValidationResult(status="ok", message="done")
        self.assertEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
