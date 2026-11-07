"""源码字节解码的统一入口，仅返回无损文本。"""

import dataclasses
import pathlib

from autopatch_j import error_messages


@dataclasses.dataclass(frozen=True, slots=True)
class SourceText:
    """保留原字节、编码及换行策略。"""

    content: str
    encoding: str
    raw_bytes: bytes
    newline: str


def read(path: pathlib.Path) -> SourceText:
    """优先 UTF-8，其次 GBK；无法无损解码时明确失败。"""
    return decode(path.read_bytes(), path)


def decode(raw: bytes, path: pathlib.Path) -> SourceText:
    """无损解码已有字节，保留编码和换行证据，不再读取文件。"""
    newline = (
        "\r\n"
        if b"\r\n" in raw
        else "\n" if b"\n" in raw else "\r" if b"\r" in raw else "\n"
    )
    for encoding in ("utf-8", "gbk"):
        try:
            return SourceText(raw.decode(encoding), encoding, raw, newline)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(error_messages.source_decode_failed(path.name))
