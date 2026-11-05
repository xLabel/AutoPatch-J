"""扫描展示片段使用当前磁盘证据。"""

import pathlib

from autopatch_j.core import finding
from autopatch_j.scanners.semgrep import results


def test_build_semgrep_scan_result_prefers_source_lines_over_dirty_extra_lines(
    tmp_path: pathlib.Path,
) -> None:
    java_file = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    java_file.parent.mkdir(parents=True)
    source = (
        "package demo;\n\n"
        "public class UserService {\n"
        "    public boolean isAdmin(User "
        "user) {\n"
        "        return user.getName().eq"
        'uals("admin");\n'
        "    }\n"
        "}\n"
    )
    with java_file.open("w", encoding="utf-8", newline="") as stream:
        stream.write(source)
    evidence = 'return user.getName().equals("admin");'
    region = _source_region(source, evidence)

    payload: dict[str, object] = {
        "results": [
            {
                "check_id": "autopatch-j.java.correctness.unsafe-equals-order",
                "path": "src/main/java/demo/UserService.java",
                "start": {
                    "line": region.start_line,
                    "col": region.start_column,
                    "offset": region.start_offset,
                },
                "end": {
                    "line": region.end_line,
                    "col": region.end_column,
                    "offset": region.end_offset,
                },
                "extra": {
                    "severity": "WARNING",
                    "message": "unsafe equals order",
                    "lines": "requires login",
                },
            }
        ],
        "errors": [],
    }

    result = results.build_semgrep_scan_result(
        payload=payload,
        repo_root=tmp_path,
        scope=["src/main/java/demo/UserService.java"],
        targets=["src/main/java/demo/UserService.java"],
    )

    assert len(result.findings) == 1
    assert (
        result.findings[0].snippet == 'return user.getName().equals("admin");'
    )


def _source_region(source: str, evidence: str) -> finding.SourceRegion:
    start = source.index(evidence)
    prefix = source[:start]
    line = prefix.count("\n") + 1
    column = len(prefix.rsplit("\n", 1)[-1]) + 1
    return finding.SourceRegion(
        line,
        column,
        line,
        column + len(evidence),
        len(prefix.encode()),
        len((prefix + evidence).encode()),
    )
