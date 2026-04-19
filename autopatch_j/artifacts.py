from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autopatch_j.session import app_dir
from autopatch_j.tools.scan_java import ScanResult


def save_scan_result(repo_root: Path, result: ScanResult) -> str:
    artifact_id = build_artifact_id("scan")
    target = app_dir(repo_root) / "findings" / f"{artifact_id}.json"
    target.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return artifact_id


def build_artifact_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{timestamp}"
