"""补丁字节准备与单文件原子替换；不处理审核或任务持久化。"""

import contextlib
import difflib
import hashlib
import os
import pathlib
import stat
import tempfile

from autopatch_j import error_messages
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_text
from autopatch_j.core.repair import checks
from autopatch_j.core.repair import models


def digest(content: bytes) -> str:
    """返回完整 bytes 的 SHA-256 指纹。"""
    return hashlib.sha256(content).hexdigest()


def normalize_newlines(text: str) -> str:
    """返回用于匹配或展示的 LF 文本，不用于整文件写回。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def prepare_file(
    root: pathlib.Path, edit: models.FileEdit
) -> models.PreparedFile:
    """所有替换在内存中依次唯一匹配，保留未替换 bytes。"""
    path = repo_path.resolve_source_path(root, edit.path)
    decoded = source_text.read(path)
    if b"\x00" in decoded.raw_bytes:
        raise models.RepairError(
            f"{edit.path} 不是可无损编辑的文本，不能生成补丁。"
        )
    candidate_bytes = decoded.raw_bytes
    spans = []
    for index, replacement in enumerate(edit.replacements, 1):
        candidate_text_before_edit = candidate_bytes.decode(decoded.encoding)
        normalized_candidate = normalize_newlines(candidate_text_before_edit)
        match_text = normalize_newlines(replacement.old_text)
        replacement_text = normalize_newlines(replacement.new_text)
        match_start = normalized_candidate.find(match_text)
        repeated = (
            match_start >= 0
            and normalized_candidate.find(match_text, match_start + 1) >= 0
        )
        if match_start < 0 or repeated:
            count = "至少 2" if repeated else "0"
            raise models.RepairError(
                error_messages.replacement_matches(edit.path, index, count)
            )
        if match_text == replacement_text:
            raise models.RepairError(error_messages.no_change(edit.path))
        # 以原始字符索引建立边界，不能规范化整个文件后重新编码。
        character_offsets = [0]
        character_position = 0
        while character_position < len(candidate_text_before_edit):
            character_position += (
                2
                if candidate_text_before_edit[
                    character_position : character_position + 2
                ]
                == "\r\n"
                else 1
            )
            character_offsets.append(character_position)
        byte_start = len(
            candidate_text_before_edit[: character_offsets[match_start]].encode(
                decoded.encoding
            )
        )
        byte_end = len(
            candidate_text_before_edit[
                : character_offsets[match_start + len(match_text)]
            ].encode(decoded.encoding)
        )
        try:
            replacement_bytes = replacement_text.replace(
                "\n", decoded.newline
            ).encode(decoded.encoding)
        except UnicodeEncodeError as exc:
            raise models.RepairError(
                error_messages.unencodable_replacement(
                    edit.path, decoded.encoding
                )
            ) from exc
        candidate_bytes = (
            candidate_bytes[:byte_start]
            + replacement_bytes
            + candidate_bytes[byte_end:]
        )
        spans.append(
            models.EditSpan(
                start=byte_start,
                end=byte_end,
                replacement_length=len(replacement_bytes),
            )
        )
    if candidate_bytes == decoded.raw_bytes:
        raise models.RepairError(error_messages.no_change(edit.path))
    candidate_text = candidate_bytes.decode(decoded.encoding)
    syntax_check = checks.check_java_syntax(edit.path, candidate_text)
    if syntax_check.status == "failed":
        raise models.RepairError(
            error_messages.invalid_candidate(edit.path, syntax_check.message)
        )
    relative = path.relative_to(root.resolve()).as_posix()
    diff_lines = difflib.unified_diff(
        normalize_newlines(decoded.content).splitlines(keepends=True),
        normalize_newlines(candidate_text).splitlines(keepends=True),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
    )
    diff = "".join(
        (
            line
            if line.endswith("\n")
            else line + "\n\\ No newline at end of file\n"
        )
        for line in diff_lines
    )
    return models.PreparedFile(
        path=relative,
        resolved_path=str(path),
        original=decoded.raw_bytes,
        candidate=candidate_bytes,
        original_sha256=digest(decoded.raw_bytes),
        candidate_sha256=digest(candidate_bytes),
        encoding=decoded.encoding,
        permission_mode=stat.S_IMODE(path.stat().st_mode),
        diff=diff,
        spans=tuple(spans),
        syntax=syntax_check,
    )


def atomic_write(
    path: pathlib.Path,
    content: bytes,
    *,
    expected: bytes | None,
    mode: int | None = None,
) -> None:
    """同目录临时写入后复核当前 bytes，再原子替换；失败不覆盖已知外部修改。"""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, delete=False
        ) as stream:
            temporary = pathlib.Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        if expected is not None and path.read_bytes() != expected:
            raise models.RepairError(
                error_messages.source_changed_during_apply(path.name)
            )
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            # 清理失败不能掩盖原始写入或预检失败。
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
