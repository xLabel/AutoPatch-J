"""基于精确区域的草案、重定位和字节保真的原子源码替换。"""

from __future__ import annotations

import dataclasses
import difflib
import os
import pathlib
import stat
import tempfile

from autopatch_j.core import finding
from autopatch_j.core.patching import types
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_text


class SearchReplacePatchEngine:
    """精确字符串替换补丁引擎。

    职责边界：
    1. 基于 old_string/new_string 生成内存草案、unified diff 和精确匹配区域。
    2. 在用户确认 apply 后通过同目录临时文件原子替换源码。
    3. 不判断修复是否正确，也不做语法/语义校验；这些由 PatchQualityVerifier 负责。
    """

    def __init__(self, repo_root: pathlib.Path) -> None:
        self.repo_root = repo_root

    def create_draft(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> types.PatchDraftBuildResult:
        """以唯一精确源码匹配生成内存草案；不写入文件。"""
        target_path = self._resolve_safe_path(file_path)
        if not target_path.exists() or not target_path.is_file():
            raise types.TargetFileNotFoundError("目标文件不存在。")

        decoded = self._read_file(target_path)
        if not decoded.safe_to_write:
            raise types.UnsafeSourceEncodingError(
                "目标文件无法无损解码，不能建立可信补丁区域。"
            )

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
        decoded: source_text.SourceText,
    ) -> types.PatchDraftBuildResult:
        """基于无损解码结果唯一匹配，构造 diff 及原字节位置。"""
        if not decoded.safe_to_write:
            raise types.UnsafeSourceEncodingError(
                "目标文件无法无损解码，不能建立可信补丁区域。"
            )

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
        return types.PatchDraftBuildResult(
            updated_source=updated_norm_content,
            diff=self._generate_unified_diff(
                file_path, norm_content, updated_norm_content
            ),
            match_region=match_region,
        )

    def rebase_draft(
        self,
        draft: types.SearchReplacePatchDraft,
        source_region: finding.SourceRegion,
        changed_region: finding.SourceRegion,
    ) -> types.PatchDraftRebaseResult:
        """按前序实际编辑重建非重叠草案的区域和 diff；不可靠时标记失效。"""
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
            rebased_target_finding: finding.FindingIdentity | None = None
            if draft.target_finding is not None:
                rebased_target_region = self._rebase_region(
                    draft.target_finding.region,
                    source_region=source_region,
                    changed_region=changed_region,
                    decoded=decoded,
                    region_label="finding",
                )
                rebased_target_finding = dataclasses.replace(
                    draft.target_finding,
                    region=rebased_target_region,
                )
            build_result = self._create_draft_from_decoded(
                file_path=draft.file_path,
                old_string=draft.old_string,
                new_string=draft.new_string,
                decoded=decoded,
            )
        except (
            types.OldStringNotFoundError,
            types.OldStringNotUniqueError,
        ) as exc:
            return self._rebase_failure(str(exc))
        except (
            types.UnsafeSourceEncodingError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            return self._rebase_failure(str(exc))

        if build_result.match_region != expected_match_region:
            return self._rebase_failure("old_string 已偏离预期重定位位置。")
        if (
            rebased_target_finding is not None
            and not build_result.match_region.intersects(
                rebased_target_finding.region
            )
        ):
            return self._rebase_failure(
                "old_string 不再覆盖目标 finding 区域。"
            )
        return types.PatchDraftRebaseResult(
            rebased=True,
            message="补丁已根据前序修改重新定位。",
            build_result=build_result,
            rebased_target_finding=rebased_target_finding,
        )

    def apply_patch(
        self, draft: types.SearchReplacePatchDraft
    ) -> types.PatchApplicationResult:
        """复核草案与当前 bytes 后原子替换，仅修改绑定区域并保留编码及权限。"""
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
            return self._failure(
                "UNSAFE_SOURCE_ENCODING", "目标文件无法无损解码，拒绝写入。"
            )

        norm_content = self._normalize_newlines(decoded.content)
        norm_old = self._normalize_newlines(draft.old_string)
        norm_new = self._normalize_newlines(draft.new_string)
        try:
            match_start = self._locate_unique_match(norm_content, norm_old)
        except types.OldStringNotFoundError, types.OldStringNotUniqueError:
            return self._failure(
                "SOURCE_CHANGED", "目标源码已变化，old_string 不再唯一匹配。"
            )
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
            return self._failure(
                "SOURCE_CHANGED",
                "目标源码已变化，old_string 已偏离 finding 区域。",
            )

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
                decoded=dataclasses.replace(decoded, raw_bytes=final_bytes),
                start_offset=changed_start_offset,
                end_offset=changed_end_offset,
            )
        except UnicodeEncodeError as exc:
            return self._failure(
                "ENCODING_FAILED", f"补丁内容无法使用原文件编码写入：{exc}"
            )

        temp_path: pathlib.Path | None = None
        file_descriptor = -1
        write_error: OSError | None = None
        try:
            file_descriptor, raw_temp_path = tempfile.mkstemp(
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                dir=target_path.parent,
            )
            temp_path = pathlib.Path(raw_temp_path)
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
            return self._failure(
                "TEMP_WRITE_FAILED", f"临时文件写入失败：{write_error}"
            )

        try:
            try:
                current_bytes = target_path.read_bytes()
            except OSError as exc:
                return self._failure(
                    "FILE_READ_FAILED", f"替换前读取目标文件失败：{exc}"
                )
            if current_bytes != decoded.raw_bytes:
                return self._failure(
                    "SOURCE_CHANGED", "目标源码在补丁应用期间被外部修改。"
                )
            try:
                assert temp_path is not None
                os.replace(temp_path, target_path)
            except OSError as exc:
                return self._failure(
                    "ATOMIC_REPLACE_FAILED", f"原子替换失败：{exc}"
                )
            temp_path = None
            return types.PatchApplicationResult(
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

    def _failure(
        self, error_code: str, message: str
    ) -> types.PatchApplicationResult:
        return types.PatchApplicationResult(
            applied=False,
            error_code=error_code,
            message=message,
        )

    def _rebase_failure(self, reason: str) -> types.PatchDraftRebaseResult:
        return types.PatchDraftRebaseResult(
            rebased=False,
            error_code="STALE_DRAFT",
            message=f"待审补丁已失效：{reason}。请 discard，或 abort 后重新扫描。",
        )

    def _rebase_region(
        self,
        region: finding.SourceRegion,
        *,
        source_region: finding.SourceRegion,
        changed_region: finding.SourceRegion,
        decoded: source_text.SourceText,
        region_label: str,
    ) -> finding.SourceRegion:
        """按前序编辑映射非重叠区域；重叠或无效映射拒绝重定位。"""
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
        decoded: source_text.SourceText,
        start_offset: int,
        end_offset: int,
    ) -> finding.SourceRegion:
        """以原编码和换行将字节偏移转换为可持久源码坐标。"""
        if (
            start_offset < 0
            or end_offset < start_offset
            or end_offset > len(decoded.raw_bytes)
        ):
            raise ValueError("重定位后的 byte offsets 超出当前文件范围")
        start_prefix = decoded.raw_bytes[:start_offset].decode(decoded.encoding)
        end_prefix = decoded.raw_bytes[:end_offset].decode(decoded.encoding)
        start_line, start_column = self._position_after_prefix(start_prefix)
        end_line, end_column = self._position_after_prefix(end_prefix)
        return finding.SourceRegion(
            start_line=start_line,
            start_column=start_column,
            end_line=end_line,
            end_column=end_column,
            start_offset=start_offset,
            end_offset=end_offset,
        )

    def _position_after_prefix(self, prefix: str) -> tuple[int, int]:
        normalized = self._normalize_newlines(prefix)
        return (
            normalized.count("\n") + 1,
            len(normalized.rsplit("\n", 1)[-1]) + 1,
        )

    def _resolve_safe_path(self, file_path: str) -> pathlib.Path:
        try:
            return repo_path.resolve_source_path(self.repo_root, file_path)
        except repo_path.UnsafeRepoPathError as exc:
            raise PermissionError(f"安全风险拦截：{exc}") from exc

    def _read_file(self, path: pathlib.Path) -> source_text.SourceText:
        return source_text.read(path)

    def _locate_unique_match(self, content: str, old_string: str) -> int:
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise types.OldStringNotFoundError(
                "在文件中未找到指定的 old_string（内容不匹配）。"
            )
        if occurrences > 1:
            raise types.OldStringNotUniqueError(occurrences)
        return content.find(old_string)

    def _region_in_original(
        self,
        *,
        decoded: source_text.SourceText,
        normalized_content: str,
        start_index: int,
        end_index: int,
    ) -> finding.SourceRegion:
        """将规范化文本匹配位置映射回原始 bytes，保留 CRLF 与编码差异。"""
        original_start = self._original_index_for_normalized_index(
            decoded.content, start_index
        )
        original_end = self._original_index_for_normalized_index(
            decoded.content, end_index
        )
        return finding.SourceRegion(
            start_line=self._line_column(normalized_content, start_index)[0],
            start_column=self._line_column(normalized_content, start_index)[1],
            end_line=self._line_column(normalized_content, end_index)[0],
            end_column=self._line_column(normalized_content, end_index)[1],
            start_offset=len(
                decoded.content[:original_start].encode(decoded.encoding)
            ),
            end_offset=len(
                decoded.content[:original_end].encode(decoded.encoding)
            ),
        )

    def _original_index_for_normalized_index(
        self, content: str, normalized_index: int
    ) -> int:
        """跳过 CRLF 中被规范化的回车，将文本索引映射回原文。"""
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

    def _generate_unified_diff(
        self, file_path: str, old_text: str, new_text: str
    ) -> str:
        diff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=3,
        )
        return "".join(diff)
