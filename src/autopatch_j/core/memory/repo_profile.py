from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .text_utils import now_iso


class RepoProfileCollector:
    """Collects conservative repo metadata for ordinary chat memory."""

    def __init__(self, repo_root: Path | None) -> None:
        self.repo_root = repo_root

    def collect(self) -> dict[str, Any]:
        profile = empty_repo_profile()
        if self.repo_root is None:
            return profile

        self._read_maven_profile(profile)
        self._read_gradle_profile(profile)
        self._read_readme_title(profile)
        if profile["source_files"]:
            profile["updated_at"] = now_iso()
        return profile

    def _read_maven_profile(self, profile: dict[str, Any]) -> None:
        pom_path = self.repo_root / "pom.xml"
        if not pom_path.is_file():
            return
        profile["build_tool"] = "maven"
        self._append_unique(profile["source_files"], "pom.xml")
        try:
            root = ET.fromstring(pom_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ET.ParseError):
            return

        profile["project_name"] = profile["project_name"] or self._child_text(root, "artifactId")
        properties = self._child(root, "properties")
        if properties is not None:
            for name in ("maven.compiler.release", "maven.compiler.source", "java.version"):
                profile["java_version"] = profile["java_version"] or self._child_text(properties, name)

        modules = self._child(root, "modules")
        if modules is not None:
            for module in self._children(modules, "module"):
                if module.text:
                    self._append_unique(profile["modules"], module.text.strip())

        dependency_text = " ".join(
            text
            for dependency in root.iter()
            if self._local_name(dependency.tag) in {"groupId", "artifactId"}
            and (text := (dependency.text or "").strip())
        )
        self._append_detected_frameworks(profile["frameworks"], dependency_text)

    def _read_gradle_profile(self, profile: dict[str, Any]) -> None:
        build_path = self.repo_root / "build.gradle"
        settings_path = self.repo_root / "settings.gradle"
        build_text = self._read_optional_text(build_path)
        settings_text = self._read_optional_text(settings_path)
        if not build_text and not settings_text:
            return

        if not profile["build_tool"]:
            profile["build_tool"] = "gradle"
        if build_text:
            self._append_unique(profile["source_files"], "build.gradle")
        if settings_text:
            self._append_unique(profile["source_files"], "settings.gradle")

        profile["project_name"] = profile["project_name"] or self._match_first(
            settings_text,
            r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]",
        )
        for module in re.findall(r"include\s+(.+)", settings_text):
            for name in re.findall(r"['\"]:?([^,'\"]+)['\"]", module):
                self._append_unique(profile["modules"], name)

        profile["java_version"] = profile["java_version"] or self._match_first(
            build_text,
            r"(?:sourceCompatibility|targetCompatibility)\s*=\s*['\"]?([0-9][^'\"\s]*)",
        )
        profile["java_version"] = profile["java_version"] or self._match_first(
            build_text,
            r"JavaLanguageVersion\.of\((\d+)\)",
        )
        self._append_detected_frameworks(profile["frameworks"], build_text + "\n" + settings_text)

    def _read_readme_title(self, profile: dict[str, Any]) -> None:
        text = self._read_optional_text(self.repo_root / "README.md")
        if not text:
            return
        self._append_unique(profile["source_files"], "README.md")
        if not profile["project_name"]:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    profile["project_name"] = stripped[2:].strip()
                    return

    def _append_detected_frameworks(self, target: list[str], text: str) -> None:
        lowered = text.lower()
        checks = (
            ("spring boot", ("spring-boot", "org.springframework.boot")),
            ("spring", ("org.springframework", "springframework")),
            ("mybatis", ("mybatis",)),
            ("junit", ("junit",)),
        )
        for label, needles in checks:
            if any(needle in lowered for needle in needles):
                self._append_unique(target, label)

    def _read_optional_text(self, path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _match_first(self, text: str, pattern: str) -> str:
        match = re.search(pattern, text)
        return match.group(1).strip() if match else ""

    def _append_unique(self, target: list[str], value: str) -> None:
        cleaned = value.strip()
        if cleaned and cleaned not in target:
            target.append(cleaned)

    def _child(self, element: ET.Element, name: str) -> ET.Element | None:
        for child in element:
            if self._local_name(child.tag) == name:
                return child
        return None

    def _children(self, element: ET.Element, name: str) -> list[ET.Element]:
        return [child for child in element if self._local_name(child.tag) == name]

    def _child_text(self, element: ET.Element, name: str) -> str:
        child = self._child(element, name)
        return (child.text or "").strip() if child is not None else ""

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]


def empty_repo_profile() -> dict[str, Any]:
    return {
        "build_tool": "",
        "java_version": "",
        "project_name": "",
        "modules": [],
        "frameworks": [],
        "source_files": [],
        "updated_at": "",
    }
