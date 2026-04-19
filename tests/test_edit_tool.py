from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.tools.edit_tool import (
    SearchReplaceEdit,
    apply_search_replace,
    preview_search_replace,
)


class EditToolTests(unittest.TestCase):
    def test_preview_search_replace_returns_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / "Demo.java"
            target.write_text(
                "class Demo {\n"
                "    void run() {\n"
                "        if (user.getName().equals(\"admin\")) {\n"
                "        }\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )

            preview = preview_search_replace(
                repo_root,
                SearchReplaceEdit(
                    file_path="Demo.java",
                    old_string='user.getName().equals("admin")',
                    new_string='"admin".equals(user.getName())',
                ),
            )

            self.assertEqual(preview.status, "ok")
            self.assertIn("--- a/Demo.java", preview.diff)
            self.assertIn("+++ b/Demo.java", preview.diff)
            self.assertIn("@@", preview.diff)

    def test_preview_search_replace_rejects_ambiguous_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / "Demo.java"
            target.write_text(
                "class Demo {\n"
                "    void a() { value(); }\n"
                "    void b() { value(); }\n"
                "}\n",
                encoding="utf-8",
            )

            preview = preview_search_replace(
                repo_root,
                SearchReplaceEdit(
                    file_path="Demo.java",
                    old_string="value()",
                    new_string="safeValue()",
                ),
            )

            self.assertEqual(preview.status, "ambiguous")
            self.assertEqual(preview.occurrences, 2)
            self.assertEqual(preview.diff, "")

    def test_apply_search_replace_updates_file_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / "Demo.java"
            target.write_text(
                "class Demo {\n"
                "    void run() {\n"
                "        call();\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )

            preview = apply_search_replace(
                repo_root,
                SearchReplaceEdit(
                    file_path="Demo.java",
                    old_string="call();",
                    new_string="safeCall();",
                ),
            )

            self.assertEqual(preview.status, "ok")
            self.assertIn("safeCall();", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
