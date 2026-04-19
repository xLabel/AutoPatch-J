from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autopatch_j.scanners import ScanResult
from autopatch_j.session import app_dir
from autopatch_j.validators.rescan import RescanValidationResult


def save_scan_result(repo_root: Path, result: ScanResult) -> str:
    artifact_id = build_artifact_id("scan")
    target = app_dir(repo_root) / "findings" / f"{artifact_id}.json"
    target.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return artifact_id


def load_scan_result(repo_root: Path, artifact_id: str) -> ScanResult | None:
    target = app_dir(repo_root) / "findings" / f"{artifact_id}.json"
    if not target.exists():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return ScanResult.from_dict(payload)


def save_validation_result(repo_root: Path, result: RescanValidationResult) -> str:
    artifact_id = build_artifact_id("validation")
    target = app_dir(repo_root) / "validations" / f"{artifact_id}.json"
    target.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return artifact_id


def load_validation_result(repo_root: Path, artifact_id: str) -> RescanValidationResult | None:
    target = app_dir(repo_root) / "validations" / f"{artifact_id}.json"
    if not target.exists():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return RescanValidationResult.from_dict(payload)


def build_artifact_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{timestamp}"
