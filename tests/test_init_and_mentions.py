from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.intent import has_scan_intent
from autopatch_j.mentions import parse_prompt
from autopatch_j.project import discover_repo_root, initialize_project
from autopatch_j.session import APP_DIR_NAME, SessionState, load_session
from autopatch_j.tools.scan_java import normalize_semgrep_payload, select_targets


class AutoPatchInitTests(unittest.TestCase):
    def test_initialize_project_creates_state_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
            (repo_root / "src" / "main" / "java" / "demo" / "App.java").write_text(
                "class App {}\n",
                encoding="utf-8",
            )
            (repo_root / "target").mkdir()
            (repo_root / "target" / "Ignored.java").write_text("class Ignored {}\n", encoding="utf-8")

            session, index, summary = initialize_project(repo_root)

            self.assertEqual(session.repo_root, str(repo_root.resolve()))
            self.assertEqual(summary.indexed_java_files, 1)
            self.assertTrue((repo_root / APP_DIR_NAME / "config.json").exists())
            self.assertTrue((repo_root / APP_DIR_NAME / "session.json").exists())
            self.assertTrue((repo_root / APP_DIR_NAME / "index.json").exists())
            self.assertTrue(any(entry.path == "src/main/java/demo/App.java" for entry in index))
            self.assertFalse(any(entry.path.startswith("target/") for entry in index))

    def test_discover_repo_root_finds_initialized_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            nested = repo_root / "src" / "main"
            nested.mkdir(parents=True)
            initialize_project(repo_root)

            discovered = discover_repo_root(nested)
            self.assertEqual(discovered, repo_root.resolve())


class MentionResolutionTests(unittest.TestCase):
    def test_parse_prompt_resolves_unique_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
            (repo_root / "src" / "main" / "java" / "demo" / "UserService.java").write_text(
                "class UserService {}\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "test" / "java" / "demo").mkdir(parents=True)
            (repo_root / "src" / "test" / "java" / "demo" / "UserServiceTest.java").write_text(
                "class UserServiceTest {}\n",
                encoding="utf-8",
            )

            _, index, _ = initialize_project(repo_root)
            parsed = parse_prompt("@UserService.java scan this file", index)

            self.assertEqual(parsed.clean_text, "scan this file")
            self.assertEqual(len(parsed.mentions), 1)
            self.assertEqual(parsed.mentions[0].status, "resolved")
            self.assertEqual(parsed.mentions[0].selected.path, "src/main/java/demo/UserService.java")

    def test_parse_prompt_marks_ambiguous_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
            (repo_root / "src" / "main" / "java" / "demo" / "UserService.java").write_text(
                "class UserService {}\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "legacy" / "java" / "demo").mkdir(parents=True)
            (repo_root / "src" / "legacy" / "java" / "demo" / "UserService.java").write_text(
                "class LegacyUserService {}\n",
                encoding="utf-8",
            )

            _, index, _ = initialize_project(repo_root)
            parsed = parse_prompt("@UserService.java scan this file", index)

            self.assertEqual(parsed.mentions[0].status, "ambiguous")
            self.assertGreaterEqual(len(parsed.mentions[0].candidates), 2)

    def test_parse_prompt_resolves_exact_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
            (repo_root / "src" / "main" / "java" / "demo" / "UserService.java").write_text(
                "class UserService {}\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "legacy" / "java" / "demo").mkdir(parents=True)
            (repo_root / "src" / "legacy" / "java" / "demo" / "UserService.java").write_text(
                "class LegacyUserService {}\n",
                encoding="utf-8",
            )

            _, index, _ = initialize_project(repo_root)
            parsed = parse_prompt("@src/main/java/demo/UserService.java scan this file", index)

            self.assertEqual(parsed.mentions[0].status, "resolved")
            self.assertEqual(parsed.mentions[0].selected.path, "src/main/java/demo/UserService.java")


class SessionTests(unittest.TestCase):
    def test_load_session_defaults_to_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / APP_DIR_NAME).mkdir(parents=True)
            session = load_session(repo_root)
            self.assertIsInstance(session, SessionState)
            self.assertEqual(session.repo_root, str(repo_root.resolve()))


class IntentTests(unittest.TestCase):
    def test_has_scan_intent_detects_cn_and_en(self) -> None:
        self.assertTrue(has_scan_intent("scan this repository"))
        self.assertTrue(has_scan_intent("扫描整个仓库的问题"))
        self.assertFalse(has_scan_intent("explain this class"))


class ScanToolTests(unittest.TestCase):
    def test_select_targets_prefers_java_files_and_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "Demo.java").write_text("class Demo {}", encoding="utf-8")
            (repo_root / "README.md").write_text("# demo", encoding="utf-8")

            targets = select_targets(repo_root, ["src", "src/Demo.java", "README.md"])
            self.assertEqual(targets, ["src", "src/Demo.java"])

    def test_normalize_semgrep_payload(self) -> None:
        payload = {
            "results": [
                {
                    "check_id": "java.lang.correctness.demo",
                    "path": "src/main/java/demo/App.java",
                    "start": {"line": 12},
                    "end": {"line": 12},
                    "extra": {
                        "severity": "ERROR",
                        "message": "Avoid direct string equality on nullable values",
                        "lines": "if (user.getName().equals(\"admin\")) {",
                        "metadata": {"cwe": "CWE-476"},
                    },
                }
            ]
        }

        result = normalize_semgrep_payload(
            payload,
            scope=["src/main/java/demo/App.java"],
            targets=["src/main/java/demo/App.java"],
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.summary["total"], 1)
        self.assertEqual(result.findings[0].severity, "error")
        self.assertEqual(result.findings[0].rule, "CWE-476")


if __name__ == "__main__":
    unittest.main()
