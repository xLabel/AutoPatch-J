from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, replace
from difflib import unified_diff
from pathlib import Path

from autopatch_j.core.patching.types import (
    OldStringNotFoundError,
    OldStringNotUniqueError,
    PatchApplicationResult,
    PatchDraftBuildResult,
    PatchDraftRebaseResult,
    SearchReplacePatchDraft,
    TargetFileNotFoundError,
    UnsafeSourceEncodingError,
)
from autopatch_j.core.finding import FindingIdentity, SourceRegion
from autopatch_j.core.project.repo_path import UnsafeRepoPathError, resolve_repo_path


@dataclass(frozen=True, slots=True)
class _DecodedFile:
    content: str
    encoding: str
    safe_to_write: bool
    raw_bytes: bytes
    newline: str


class SearchReplacePatchEngine:
    """
    精确字符串替换补丁引擎。

    职责边界：
    1. 基于 old_string/new_string 生成内存草案、unified diff 和精确匹配区域。
    2. 在用户确认 apply 后通过同目录临时文件原子替换源码。
    3. 不判断修复是否正确，也不做语法/语义校验；这些由 PatchQualityVerifier 负责。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def create_draft(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> PatchDraftBuildResult:
        target_path = self._resolve_safe_path(file_path)
        if not target_path.exists() or not target_path.is_file():
            raise TargetFileNotFoundError("目标文件不存在。")

        decoded = self._read_file(target_path)
        if not decoded.safe_to_write:
            raise UnsafeSourceEncodingError("目标文件无法无损解码，不能建立可信补丁区域。")

        return self._create_draft_from_decoded(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            decoded=decoded,
        )

    def _create_draft_from_decoded(
        self,
        *,
        file_path: str,
        old_string: str,
        new_string: str,
        decoded: _DecodedFile,
    ) -> PatchDraftBuildResult:
        if not decoded.safe_to_write:
            raise UnsafeSourceEncodingError("目标文件无法无损解码，不能建立可信补丁区域。")

        norm_content = self._normalize_newlines(decoded.content)
        norm_old = self._normalize_newlines(old_string)
        norm_new = self._normalize_newlines(new_string)
        match_start = self._locate_unique_match(norm_content, norm_old)
        match_region = self._region_in_original(
            decoded=decoded,
            normalized_content=norm_content,
            start_index=match_start,
            end_index=match_start + len(norm_old),
        )

        updated_norm_content = (
            norm_content[:match_start]
            + norm_new
            + norm_content[match_start + len(norm_old) :]
        )
        return PatchDraftBuildResult(
            updated_source=updated_norm_content,
            diff=self._generate_unified_diff(file_path, norm_content, updated_norm_content),
            match_region=match_region,
        )

    def rebase_draft(
        self,
        draft: SearchReplacePatchDraft,
        source_region: SourceRegion,
        changed_region: SourceRegion,
    ) -> PatchDraftRebaseResult:
        if draft.error_code == "STALE_DRAFT":
            return self._rebase_failure("该草案此前已失去可信源码绑定")
        try:
            target_path = self._resolve_safe_path(draft.file_path)
        except PermissionError as exc:
            return self._rebase_failure(str(exc))
        if not target_path.exists() or not target_path.is_file():
            return self._rebase_failure("目标文件不存在。")

        try:
            decoded = self._read_file(target_path)
        except OSError as exc:
            return self._rebase_failure(f"读取目标文件失败：{exc}")
        if not decoded.safe_to_write:
            return self._rebase_failure("目标文件无法无损解码。")

        try:
            expected_match_region = self._rebase_region(
                draft.match_region,
                source_region=source_region,
                changed_region=changed_region,
                decoded=decoded,
                region_label="old_string",
            )
            rebased_target_finding: FindingIdentity | None = None
            if draft.target_finding is not None:
                rebased_target_region = self._rebase_region(
                    draft.target_finding.region,
                    source_region=source_region,
                    changed_region=changed_region,
                    decoded=decoded,
                    region_label="finding",
                )
                rebased_target_finding = replace(
                    draft.target_finding,
                    region=rebased_target_region,
                )
            build_result = self._create_draft_from_decoded(
                file_path=draft.file_path,
                old_string=draft.old_string,
                new_string=draft.new_string,
                decoded=decoded,
            )
        except (OldStringNotFoundError, OldStringNotUniqueError) as exc:
            return self._rebase_failure(str(exc))
        except (UnsafeSourceEncodingError, UnicodeDecodeError, ValueError) as exc:
            return self._rebase_failure(str(exc))

        if build_result.match_region != expected_match_region:
            return self._rebase_failure("old_string 已偏离预期重定位位置。")
        if (
            rebased_target_finding is not None
            and not build_result.match_region.intersects(rebased_target_finding.region)
        ):
            return self._rebase_failure("old_string 不再覆盖目标 finding 区域。")
        return PatchDraftRebaseResult(
            rebased=True,
            message="补丁已根据前序修改重新定位。",
            build_result=build_result,
            rebased_target_finding=rebased_target_finding,
        )

    def apply_patch(self, draft: SearchReplacePatchDraft) -> PatchApplicationResult:
        try:
            target_path = self._resolve_safe_path(draft.file_path)
        except PermissionError as exc:
            return self._failure("UNSAFE_PATH", str(exc))
        if not target_path.exists() or not target_path.is_file():
            return self._failure("FILE_NOT_FOUND", "目标文件不存在。")

        try:
            decoded = self._read_file(target_path)
            permission_mode = stat.S_IMODE(target_path.stat().st_mode)
        except OSError as exc:
            return self._failure("FILE_READ_FAILED", f"读取目标文件失败：{exc}")
        if not decoded.safe_to_write:
            return self._failure("UNSAFE_SOURCE_ENCODING", "目标文件无法无损解码，拒绝写入。")

        norm_content = self._normalize_newlines(decoded.content)
        norm_old = self._normalize_newlines(draft.old_string)
        norm_new = self._normalize_newlines(draft.new_string)
        try:
            match_start = self._locate_unique_match(norm_content, norm_old)
        except (OldStringNotFoundError, OldStringNotUniqueError):
            return self._failure("SOURCE_CHANGED", "目标源码已变化，old_string 不再唯一匹配。")
        current_match_region = self._region_in_original(
            decoded=decoded,
            normalized_content=norm_content,
            start_index=match_start,
            end_index=match_start + len(norm_old),
        )
        if current_match_region != draft.match_region:
            return self._failure(
                "SOURCE_CHANGED",
                "目标源码已变化，old_string 已偏离待审草案绑定位置。",
            )
        if (
            draft.target_finding is not None
            and not current_match_region.intersects(draft.target_finding.region)
        ):
            return self._failure("SOURCE_CHANGED", "目标源码已变化，old_string 已偏离 finding 区域。")

        try:
            replacement_bytes = self._restore_newlines(
                norm_new,
                decoded.newline,
            ).encode(decoded.encoding)
            changed_start_offset = current_match_region.start_offset
            changed_end_offset = changed_start_offset + len(replacement_bytes)
            final_bytes = (
                decoded.raw_bytes[:changed_start_offset]
                + replacement_bytes
                + decoded.raw_bytes[current_match_region.end_offset :]
            )
            changed_region = self._region_from_byte_offsets(
                decoded=replace(decoded, raw_bytes=final_bytes),
                start_offset=changed_start_offset,
                end_offset=changed_end_offset,
            )
        except UnicodeEncodeError as exc:
            return self._failure("ENCODING_FAILED", f"补丁内容无法使用原文件编码写入：{exc}")

        temp_path: Path | None = None
        file_descriptor = -1
        write_error: OSError | None = None
        try:
            file_descriptor, raw_temp_path = tempfile.mkstemp(
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                dir=target_path.parent,
            )
            temp_path = Path(raw_temp_path)
            with os.fdopen(file_descriptor, "wb") as handle:
                file_descriptor = -1
                handle.write(final_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, permission_mode)
        except OSError as exc:
            write_error = exc
        finally:
            if file_descriptor >= 0:
                try:
                    os.close(file_descriptor)
                except OSError as exc:
                    write_error = write_error or exc
        if write_error is not None:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return self._failure("TEMP_WRITE_FAILED", f"临时文件写入失败：{write_error}")

        try:
            try:
                current_bytes = target_path.read_bytes()
            except OSError as exc:
                return self._failure("FILE_READ_FAILED", f"替换前读取目标文件失败：{exc}")
            if current_bytes != decoded.raw_bytes:
                return self._failure("SOURCE_CHANGED", "目标源码在补丁应用期间被外部修改。")
            try:
                assert temp_path is not None
                os.replace(temp_path, target_path)
            except OSError as exc:
                return self._failure("ATOMIC_REPLACE_FAILED", f"原子替换失败：{exc}")
            temp_path = None
            return PatchApplicationResult(
                applied=True,
                message="补丁已原子应用。",
                source_region=current_match_region,
                changed_region=changed_region,
            )
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _failure(self, error_code: str, message: str) -> PatchApplicationResult:
        return PatchApplicationResult(
            applied=False,
            error_code=error_code,
            message=message,
        )

    def _rebase_failure(self, reason: str) -> PatchDraftRebaseResult:
        return PatchDraftRebaseResult(
            rebased=False,
            error_code="STALE_DRAFT",
            message=f"待审补丁已失效：{reason}。请 discard，或 abort 后重新扫描。",
        )

    def _rebase_region(
        self,
        region: SourceRegion,
        *,
        source_region: SourceRegion,
        changed_region: SourceRegion,
        decoded: _DecodedFile,
        region_label: str,
    ) -> SourceRegion:
        if region.end_offset <= source_region.start_offset:
            start_offset = region.start_offset
            end_offset = region.end_offset
        elif region.start_offset >= source_region.end_offset:
            byte_delta = (
                changed_region.end_offset
                - changed_region.start_offset
                - (source_region.end_offset - source_region.start_offset)
            )
            start_offset = region.start_offset + byte_delta
            end_offset = region.end_offset + byte_delta
        else:
            raise ValueError(f"{region_label} 区域与前序补丁修改区域相交")
        return self._region_from_byte_offsets(
            decoded=decoded,
            start_offset=start_offset,
            end_offset=end_offset,
        )

    def _region_from_byte_offsets(
        self,
        *,
        decoded: _DecodedFile,
        start_offset: int,
        end_offset: int,
    ) -> SourceRegion:
        if start_offset < 0 or end_offset < start_offset or end_offset > len(decoded.raw_bytes):
            raise ValueError("重定位后的 byte offsets 超出当前文件范围")
        start_prefix = decoded.raw_bytes[:start_offset].decode(decoded.encoding)
        end_prefix = decoded.raw_bytes[:end_offset].decode(decoded.encoding)
        start_line, start_column = self._position_after_prefix(start_prefix)
        end_line, end_column = self._position_after_prefix(end_prefix)
        return SourceRegion(
            start_line=start_line,
            start_column=start_column,
            end_line=end_line,
            end_column=end_column,
            start_offset=start_offset,
            end_offset=end_offset,
        )

    def _position_after_prefix(self, prefix: str) -> tuple[int, int]:
        normalized = self._normalize_newlines(prefix)
        return normalized.count("\n") + 1, len(normalized.rsplit("\n", 1)[-1]) + 1

    def _resolve_safe_path(self, file_path: str) -> Path:
        try:
            return resolve_repo_path(self.repo_root, file_path)
        except UnsafeRepoPathError as exc:
            raise PermissionError(f"安全风险拦截：路径 '{file_path}' 超出了项目根目录范围。") from exc

    def _read_file(self, path: Path) -> _DecodedFile:
        raw_bytes = path.read_bytes()
        newline = self._detect_newline(raw_bytes)
        try:
            return _DecodedFile(raw_bytes.decode("utf-8"), "utf-8", True, raw_bytes, newline)
        except UnicodeDecodeError:
            try:
                return _DecodedFile(raw_bytes.decode("gbk"), "gbk", True, raw_bytes, newline)
            except UnicodeDecodeError:
                return _DecodedFile(
                    raw_bytes.decode("utf-8", errors="replace"),
                    "utf-8",
                    False,
                    raw_bytes,
                    newline,
                )

    def _locate_unique_match(self, content: str, old_string: str) -> int:
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise OldStringNotFoundError("在文件中未找到指定的 old_string（内容不匹配）。")
        if occurrences > 1:
            raise OldStringNotUniqueError(occurrences)
        return content.find(old_string)

    def _region_in_original(
        self,
        *,
        decoded: _DecodedFile,
        normalized_content: str,
        start_index: int,
        end_index: int,
    ) -> SourceRegion:
        original_start = self._original_index_for_normalized_index(decoded.content, start_index)
        original_end = self._original_index_for_normalized_index(decoded.content, end_index)
        return SourceRegion(
            start_line=self._line_column(normalized_content, start_index)[0],
            start_column=self._line_column(normalized_content, start_index)[1],
            end_line=self._line_column(normalized_content, end_index)[0],
            end_column=self._line_column(normalized_content, end_index)[1],
            start_offset=len(decoded.content[:original_start].encode(decoded.encoding)),
            end_offset=len(decoded.content[:original_end].encode(decoded.encoding)),
        )

    def _original_index_for_normalized_index(self, content: str, normalized_index: int) -> int:
        original_index = 0
        current_normalized_index = 0
        while current_normalized_index < normalized_index:
            if content.startswith("\r\n", original_index):
                original_index += 2
            else:
                original_index += 1
            current_normalized_index += 1
        return original_index

    def _line_column(self, content: str, index: int) -> tuple[int, int]:
        line_start = content.rfind("\n", 0, index) + 1
        return content.count("\n", 0, index) + 1, index - line_start + 1

    def _normalize_newlines(self, content: str) -> str:
        return content.replace("\r\n", "\n").replace("\r", "\n")

    def _restore_newlines(self, content: str, newline: str) -> str:
        return content if newline == "\n" else content.replace("\n", newline)

    def _detect_newline(self, content: bytes) -> str:
        if b"\r\n" in content:
            return "\r\n"
        if b"\n" in content:
            return "\n"
        if b"\r" in content:
            return "\r"
        return "\n"

    def _generate_unified_diff(self, file_path: str, old_text: str, new_text: str) -> str:
        diff = unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=3,
        )
        return "".join(diff)
