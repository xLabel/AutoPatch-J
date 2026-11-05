"""补丁字节准备与单文件原子替换；不处理审核或任务持久化。"""

import contextlib
import difflib
import hashlib
import os
import pathlib
import stat
import tempfile

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
    if not decoded.safe_to_write or b"\x00" in decoded.raw_bytes:
        raise models.RepairError(
            f"{edit.path} 不是可无损编辑的文本，不能生成补丁。"
        )
    current = decoded.raw_bytes
    spans = []
    for index, replacement in enumerate(edit.replacements, 1):
        text = current.decode(decoded.encoding)
        normalized = normalize_newlines(text)
        old = normalize_newlines(replacement.old_text)
        new = normalize_newlines(replacement.new_text)
        start = normalized.find(old)
        repeated = start >= 0 and normalized.find(old, start + 1) >= 0
        if start < 0 or repeated:
            count = "至少 2" if repeated else "0"
            raise models.RepairError(
                f"{edit.path} 第 {index} 项 old_text 匹配 {count} 次，必须恰好一次。"
            )
        if old == new:
            raise models.RepairError(f"{edit.path} 第 {index} 项替换没有变化。")
        # 以原始字符索引建立边界，不能规范化整个文件后重新编码。
        boundaries = [0]
        position = 0
        while position < len(text):
            position += 2 if text[position : position + 2] == "\r\n" else 1
            boundaries.append(position)
        byte_start = len(text[: boundaries[start]].encode(decoded.encoding))
        byte_end = len(
            text[: boundaries[start + len(old)]].encode(decoded.encoding)
        )
        inserted = new.replace("\n", decoded.newline).encode(decoded.encoding)
        current = current[:byte_start] + inserted + current[byte_end:]
        spans.append(
            models.EditSpan(
                start=byte_start, end=byte_end, replacement_length=len(inserted)
            )
        )
    if current == decoded.raw_bytes:
        raise models.RepairError(f"{edit.path} 的所有替换合计没有变化。")
    candidate_text = current.decode(decoded.encoding)
    check = checks.check_java(edit.path, candidate_text)
    if check.status == "failed":
        raise models.RepairError(f"{edit.path}: {check.message}")
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
        candidate=current,
        original_sha256=digest(decoded.raw_bytes),
        candidate_sha256=digest(current),
        encoding=decoded.encoding,
        permission_mode=stat.S_IMODE(path.stat().st_mode),
        diff=diff,
        spans=tuple(spans),
        syntax=check,
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
            raise models.RepairError(f"{path.name} 在应用期间被外部修改。")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            # 清理失败不能掩盖原始写入或预检失败。
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
