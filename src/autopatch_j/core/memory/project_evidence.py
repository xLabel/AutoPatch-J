from __future__ import annotations

from pathlib import Path

MAX_PROJECT_EVIDENCE_ITEMS = 4
MAX_PROJECT_EVIDENCE_TEXT = 700


class ProjectEvidenceCollector:
    """Collects small repo-owned snippets that may justify project_fact memory writes."""

    def __init__(self, repo_root: Path | None) -> None:
        self.repo_root = repo_root

    def collect(self) -> list[dict[str, str]]:
        if self.repo_root is None:
            return []

        candidates = ("README_CN.md", "README.md", "pom.xml", "build.gradle", "settings.gradle")
        evidence: list[dict[str, str]] = []
        for index, name in enumerate(candidates, start=1):
            path = self.repo_root / name
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            compact = " ".join(content.split())
            if not compact:
                continue
            evidence.append(
                {
                    "evidence_id": f"project_evidence_{index}",
                    "source": name,
                    "text": compact[:MAX_PROJECT_EVIDENCE_TEXT],
                }
            )
            if len(evidence) >= MAX_PROJECT_EVIDENCE_ITEMS:
                break
        return evidence
