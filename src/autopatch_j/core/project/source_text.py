"""源码字节解码的统一入口，显式区分无损结果与只读降级。"""

import dataclasses
import pathlib


@dataclasses.dataclass(frozen=True, slots=True)
class SourceText:
    """保留原字节、编码及换行策略，补丁操作必须检查 safe_to_write。"""

    content: str
    encoding: str
    safe_to_write: bool
    raw_bytes: bytes
    newline: str


def read(path: pathlib.Path) -> SourceText:
    """优先 UTF-8，其次 GBK；无法无损解码时仅供只读展示。"""
    raw = path.read_bytes()
    newline = (
        "\r\n"
        if b"\r\n" in raw
        else "\n" if b"\n" in raw else "\r" if b"\r" in raw else "\n"
    )
    for encoding in ("utf-8", "gbk"):
        try:
            return SourceText(
                raw.decode(encoding), encoding, True, raw, newline
            )
        except UnicodeDecodeError:
            continue
    return SourceText(
        raw.decode("utf-8", errors="replace"), "utf-8", False, raw, newline
    )
