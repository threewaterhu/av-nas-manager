"""Top-level-only NAS filename normalization with conservative conflicts."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from .metadata.base import Metadata, MetadataStatus
from .product_code import (
    HistoricalCodeCandidate,
    ProductCodeResult,
    ProductCodeStatus,
    RecoveryKind,
    extract_product_code,
    extract_historical_code_candidates,
    extract_subtitle_code_candidates,
    has_product_code_shape,
)
from .renamer import (
    clean_original_description,
    propose_filename,
    propose_preserved_fallback_filename,
    sanitize_component,
)
from .scanner import VIDEO_EXTENSIONS


USABLE_METADATA = {MetadataStatus.FOUND, MetadataStatus.MANUAL_CONFIRMED}


class MetadataLookup(Protocol):
    def get(self, product_code: str) -> Metadata: ...


class NASRenameStatus(str, Enum):
    READY_RENAME = "READY_RENAME"
    RENAME_AUTHORITATIVE_METADATA = "RENAME_AUTHORITATIVE_METADATA"
    RENAME_NON_ACTRESS_SUFFIX = "RENAME_NON_ACTRESS_SUFFIX"
    RENAME_NORMALIZED_CODE = "RENAME_NORMALIZED_CODE"
    RENAME_SUBTITLE_SUFFIX_CORRECTED = "RENAME_SUBTITLE_SUFFIX_CORRECTED"
    ALREADY_STANDARDIZED = "ALREADY_STANDARDIZED"
    METADATA_NOT_FOUND = "METADATA_NOT_FOUND"
    PRODUCT_CODE_NOT_FOUND = "PRODUCT_CODE_NOT_FOUND"
    AMBIGUOUS_PRODUCT_CODE = "AMBIGUOUS_PRODUCT_CODE"
    NEEDS_REVIEW_DUPLICATE_CODE = "NEEDS_REVIEW_DUPLICATE_CODE"
    NEEDS_REVIEW_METADATA_CONFLICT = "NEEDS_REVIEW_METADATA_CONFLICT"
    NEEDS_REVIEW_TARGET_EXISTS = "NEEDS_REVIEW_TARGET_EXISTS"
    NEEDS_REVIEW_METADATA_ERROR = "NEEDS_REVIEW_METADATA_ERROR"
    NEEDS_REVIEW_SUFFIX_AMBIGUITY = "NEEDS_REVIEW_SUFFIX_AMBIGUITY"
    NEEDS_REVIEW_INFORMATION_LOSS = "NEEDS_REVIEW_INFORMATION_LOSS"
    RECOVERED_CODE_METADATA_FOUND = "RECOVERED_CODE_METADATA_FOUND"
    RECOVERED_CODE_METADATA_NOT_FOUND = "RECOVERED_CODE_METADATA_NOT_FOUND"
    NEEDS_REVIEW_MULTI_PART = "NEEDS_REVIEW_MULTI_PART"
    NO_CODE_IN_FILENAME = "NO_CODE_IN_FILENAME"
    SKIPPED_STATE_CHANGED = "SKIPPED_STATE_CHANGED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    RENAMED_OK = "RENAMED_OK"
    FAILED = "FAILED"


class NASRenameAction(str, Enum):
    RENAME = "RENAME"
    NO_ACTION = "NO_ACTION"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class RecoveryOutcome(str, Enum):
    NONE = "NONE"
    RECOVERED_CODE_METADATA_FOUND = "RECOVERED_CODE_METADATA_FOUND"
    RECOVERED_CODE_METADATA_NOT_FOUND = "RECOVERED_CODE_METADATA_NOT_FOUND"
    NEEDS_REVIEW_MULTI_PART = "NEEDS_REVIEW_MULTI_PART"
    NO_CODE_IN_FILENAME = "NO_CODE_IN_FILENAME"
    NEEDS_REVIEW_SUFFIX_AMBIGUITY = "NEEDS_REVIEW_SUFFIX_AMBIGUITY"
    OTHER = "OTHER"


@dataclass(frozen=True)
class NASVideo:
    path: Path
    size: int
    code_result: ProductCodeResult


@dataclass(frozen=True)
class NASRenamePlan:
    current_path: Path
    current_size: int
    product_code: str | None
    metadata: Metadata | None
    proposed_path: Path | None
    status: NASRenameStatus
    action: NASRenameAction
    reason: str
    current_description: str = ""
    subtitle_flag: str | None = None
    normalized_code_changed: bool = False
    duplicate_files: tuple[Path, ...] = ()
    conflict_path: Path | None = None
    size_after: int | None = None
    canonical_base_code: str | None = None
    display_code: str | None = None
    part_flag: str | None = None
    extra_token: str | None = None
    recovery_kind: str | None = None
    raw_candidates: tuple[str, ...] = ()
    recovery_outcome: RecoveryOutcome = RecoveryOutcome.NONE


@dataclass(frozen=True)
class _ResolvedVideo:
    video: NASVideo
    product_code: str | None
    metadata: Metadata | None
    subtitle_flag: str | None = None
    review_status: NASRenameStatus | None = None
    reason: str = ""
    canonical_base_code: str | None = None
    display_code: str | None = None
    part_flag: str | None = None
    extra_token: str | None = None
    recovery_kind: str | None = None
    raw_candidates: tuple[str, ...] = ()
    recovery_outcome: RecoveryOutcome = RecoveryOutcome.NONE


_NAME_SEPARATOR = re.compile(r"\s*[,，、/・&]\s*")
_STORY_OR_RELEASE = re.compile(
    r"(?i)(?:中文字幕|中字|字幕|有码|有碼|无码|無碼|unc(?:en)?|watch|download|"
    r"在线观看|影片|視頻|视频|發行|发行|日期|メーカー|作品|剧情|劇情|廣告|广告|"
    r"論壇|论坛|高畫質|高清|1080|2160|4k|fhd|hd|hevc|h26[45]|x26[45]|"
    r"被|強姦|强姦|性交|做愛|做爱|中出し|調教|调教|痴女|人妻|女教師|教师|"
    r"老師|老师|公公|義父|岳父|老婆|彼女|女友|妻|母|姐姐|妹妹|巨乳|高潮|"
    r"深喉|欲望|欲求|服務|服务|秘密|撮影|配信|ヌード|交換|時間以上|"
    r"最近更新|筋肉|トレーナー|銀行職員|新人出道|肉棒|高速再生|淫乱|淫亂|"
    r"眼鏡妹|眼镜妹|家庭菜園|我媽|幹|ドスケベ|一叹)"
)
_STORY_PUNCTUATION = re.compile(r"[。！？!?；;：:…《》【】\[\]{}()（）]")


def _name_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _person_key(name: str) -> str:
    return re.sub(r"\s+", "", sanitize_component(name)).casefold()


def _actresses_match(
    description: str,
    actresses: tuple[str, ...],
    max_actresses: int,
) -> bool:
    current = [part for part in _NAME_SEPARATOR.split(description) if part]
    expected = [name for name in actresses[:max_actresses] if sanitize_component(name)]
    return sorted(_person_key(name) for name in current) == sorted(
        _person_key(name) for name in expected
    )


def _is_clearly_non_actress(description: str) -> bool:
    """Recognize only obvious plot/release text; uncertain names stay review-only."""
    normalized = sanitize_component(description)
    if not normalized:
        return False
    if _STORY_OR_RELEASE.search(normalized) or _STORY_PUNCTUATION.search(normalized):
        return True
    if any(character.isdigit() for character in normalized):
        return True
    cjk_count = sum(
        "\u3040" <= character <= "\u30ff"
        or "\u3400" <= character <= "\u9fff"
        for character in normalized
    )
    if cjk_count >= 10:
        return True
    if len(normalized.split()) > 4 or len(normalized) > 40:
        return True
    return False


def _is_authoritative_avwiki_metadata(metadata: Metadata) -> bool:
    """Return true only for the explicitly approved AV-Wiki conflict policy."""
    return (
        metadata.status == MetadataStatus.FOUND
        and metadata.source.casefold() == "av-wiki.net"
        and any(sanitize_component(name) for name in metadata.actresses)
    )


def _has_standard_code_prefix(path: Path, product_code: str) -> bool:
    stem = unicodedata.normalize("NFC", path.stem)
    return re.match(rf"^{re.escape(product_code)}(?:\s+|$)", stem) is not None


def scan_nas_videos(target_directory: Path) -> list[NASVideo]:
    """Scan regular videos at exactly one directory level."""
    videos = []
    with os.scandir(target_directory) as entries:
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name.endswith(".uploading"):
                continue
            if Path(name).suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            path = Path(entry.path)
            videos.append(
                NASVideo(
                    path=path,
                    size=entry.stat(follow_symlinks=False).st_size,
                    code_result=extract_product_code(name),
                )
            )
    return sorted(videos, key=lambda video: _name_key(video.path.name))


def _target_conflict(current: Path, proposed: Path) -> Path | None:
    key = _name_key(proposed.name)
    try:
        with os.scandir(current.parent) as entries:
            for entry in entries:
                if entry.name == current.name:
                    continue
                if _name_key(entry.name) == key:
                    return Path(entry.path)
    except OSError:
        return current.parent
    return None


def _plan_unique_video(
    video: NASVideo,
    code: str,
    metadata: Metadata,
    max_actresses: int,
    subtitle_flag: str | None = None,
    *,
    canonical_base_code: str | None = None,
    part_flag: str | None = None,
    extra_token: str | None = None,
    recovery_kind: str | None = None,
    raw_candidates: tuple[str, ...] = (),
    recovery_outcome: RecoveryOutcome = RecoveryOutcome.NONE,
) -> NASRenamePlan:
    extra_fields = dict(
        canonical_base_code=canonical_base_code or code,
        display_code=code,
        part_flag=part_flag,
        extra_token=extra_token,
        recovery_kind=recovery_kind,
        raw_candidates=raw_candidates,
        recovery_outcome=recovery_outcome,
    )
    normalized_code_changed = (
        not _has_standard_code_prefix(video.path, code) and subtitle_flag is None
    )
    if metadata.status == MetadataStatus.ERROR:
        return NASRenamePlan(
            video.path, video.size, code, metadata, None,
            NASRenameStatus.NEEDS_REVIEW_METADATA_ERROR,
            NASRenameAction.NEEDS_REVIEW,
            metadata.error or "metadata providers failed",
            normalized_code_changed=normalized_code_changed,
            **extra_fields,
        )
    if metadata.status in USABLE_METADATA:
        proposed = video.path.with_name(
            propose_filename(video.path, code, metadata.actresses, max_actresses)
        )
        description = clean_original_description(video.path, code)
        unsafe_information_loss = False
    else:
        fallback_name, description, unsafe_information_loss = (
            propose_preserved_fallback_filename(video.path, code)
        )
        proposed = video.path.with_name(fallback_name)
    status: NASRenameStatus
    action: NASRenameAction
    reason: str

    if metadata.status in USABLE_METADATA:
        suffix_matches = _actresses_match(
            description, metadata.actresses, max_actresses
        )
        if _has_standard_code_prefix(video.path, code) and suffix_matches:
            return NASRenamePlan(
                video.path, video.size, code, metadata, proposed,
                NASRenameStatus.ALREADY_STANDARDIZED,
                NASRenameAction.NO_ACTION,
                "standard code and actress list match verified metadata",
                current_description=description,
                subtitle_flag=subtitle_flag,
                normalized_code_changed=normalized_code_changed,
                **extra_fields,
            )
        if description and not suffix_matches:
            if not _is_clearly_non_actress(description):
                if _is_authoritative_avwiki_metadata(metadata):
                    status = NASRenameStatus.RENAME_AUTHORITATIVE_METADATA
                    action = NASRenameAction.RENAME
                    reason = (
                        "AV-Wiki FOUND metadata is authoritative for the conflicting suffix"
                    )
                else:
                    return NASRenamePlan(
                        video.path, video.size, code, metadata, proposed,
                        NASRenameStatus.NEEDS_REVIEW_METADATA_CONFLICT,
                        NASRenameAction.NEEDS_REVIEW,
                        "current suffix looks name-like but differs from verified metadata",
                        current_description=description,
                        subtitle_flag=subtitle_flag,
                        normalized_code_changed=normalized_code_changed,
                        **extra_fields,
                    )
            else:
                status = NASRenameStatus.RENAME_NON_ACTRESS_SUFFIX
                action = NASRenameAction.RENAME
                reason = "current suffix is clearly plot, release, or technical text"
        elif subtitle_flag:
            status = NASRenameStatus.RENAME_SUBTITLE_SUFFIX_CORRECTED
            action = NASRenameAction.RENAME
            reason = "metadata validated the base code after removing subtitle suffix"
        elif not _has_standard_code_prefix(video.path, code):
            status = NASRenameStatus.RENAME_NORMALIZED_CODE
            action = NASRenameAction.RENAME
            reason = "product code formatting requires normalization"
        else:
            status = NASRenameStatus.READY_RENAME
            action = NASRenameAction.RENAME
            reason = "verified actress metadata can be added"
    else:
        if unsafe_information_loss:
            return NASRenamePlan(
                video.path, video.size, code, metadata, proposed,
                NASRenameStatus.NEEDS_REVIEW_INFORMATION_LOSS,
                NASRenameAction.NEEDS_REVIEW,
                "mechanical normalization would sanitize potentially meaningful text",
                current_description=description,
                subtitle_flag=subtitle_flag,
                normalized_code_changed=normalized_code_changed,
                **extra_fields,
            )
        status = NASRenameStatus.METADATA_NOT_FOUND
        same_name = unicodedata.normalize("NFC", video.path.name) == unicodedata.normalize(
            "NFC", proposed.name
        )
        action = NASRenameAction.NO_ACTION if same_name else NASRenameAction.RENAME
        reason = (
            "metadata not found; existing cleaned fallback is unchanged"
            if same_name
            else "metadata not found; preserve the cleaned useful description"
        )

    conflict = _target_conflict(video.path, proposed) if action == NASRenameAction.RENAME else None
    if conflict:
        return NASRenamePlan(
            video.path, video.size, code, metadata, proposed,
            NASRenameStatus.NEEDS_REVIEW_TARGET_EXISTS,
            NASRenameAction.NEEDS_REVIEW,
            "proposed target already exists by exact, case, or Unicode-normalized name",
            current_description=description,
            subtitle_flag=subtitle_flag,
            normalized_code_changed=normalized_code_changed,
            conflict_path=conflict,
            **extra_fields,
        )
    return NASRenamePlan(
        video.path, video.size, code, metadata, proposed,
        status, action, reason,
        current_description=description,
        subtitle_flag=subtitle_flag,
        normalized_code_changed=normalized_code_changed,
        **extra_fields,
    )


def _resolve_suffix_candidate(
    video: NASVideo,
    metadata_lookup: MetadataLookup,
    candidate: HistoricalCodeCandidate,
) -> _ResolvedVideo:
    original_metadata = metadata_lookup.get(candidate.raw_code)
    base_metadata = metadata_lookup.get(candidate.lookup_code)
    original_found = original_metadata.status in USABLE_METADATA
    base_found = base_metadata.status in USABLE_METADATA
    if original_found and base_found:
        return _ResolvedVideo(
            video, candidate.display_code, base_metadata, candidate.suffix_flag,
            NASRenameStatus.NEEDS_REVIEW_SUFFIX_AMBIGUITY,
            "both raw and stripped suffix codes have valid metadata",
            canonical_base_code=candidate.lookup_code,
            display_code=candidate.display_code,
            recovery_kind=candidate.kind.value,
            raw_candidates=(candidate.raw_code, candidate.lookup_code),
            recovery_outcome=RecoveryOutcome.NEEDS_REVIEW_SUFFIX_AMBIGUITY,
        )
    if original_found and base_metadata.status == MetadataStatus.NOT_FOUND:
        return _ResolvedVideo(
            video, candidate.raw_code, original_metadata,
            canonical_base_code=candidate.raw_code,
            display_code=candidate.raw_code,
            recovery_kind=candidate.kind.value,
            raw_candidates=(candidate.raw_code, candidate.lookup_code),
        )
    if base_found and original_metadata.status == MetadataStatus.NOT_FOUND:
        return _ResolvedVideo(
            video, candidate.display_code, base_metadata, candidate.suffix_flag,
            canonical_base_code=candidate.lookup_code,
            display_code=candidate.display_code,
            recovery_kind=candidate.kind.value,
            raw_candidates=(candidate.raw_code, candidate.lookup_code),
            recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_FOUND,
        )
    if (
        original_metadata.status == MetadataStatus.ERROR
        or base_metadata.status == MetadataStatus.ERROR
    ):
        return _ResolvedVideo(
            video, candidate.display_code, base_metadata, candidate.suffix_flag,
            NASRenameStatus.NEEDS_REVIEW_METADATA_ERROR,
            "suffix candidate validation had a metadata provider error",
            canonical_base_code=candidate.lookup_code,
            display_code=candidate.display_code,
            recovery_kind=candidate.kind.value,
            raw_candidates=(candidate.raw_code, candidate.lookup_code),
            recovery_outcome=RecoveryOutcome.OTHER,
        )
    return _ResolvedVideo(
        video, candidate.display_code, base_metadata, candidate.suffix_flag,
        NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND,
        "neither the raw suffix code nor stripped base code has metadata",
        canonical_base_code=candidate.lookup_code,
        display_code=candidate.display_code,
        recovery_kind=candidate.kind.value,
        raw_candidates=(candidate.raw_code, candidate.lookup_code),
        recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_NOT_FOUND,
    )


def _resolve_video(video: NASVideo, metadata_lookup: MetadataLookup) -> _ResolvedVideo:
    direct = video.code_result
    histories = extract_historical_code_candidates(video.path.name)
    raw_candidates = direct.original_matches

    # This corruption pattern must be considered before ordinary ambiguity.
    fc2 = [item for item in histories if item.kind == RecoveryKind.FC2_SPLIT_RECOVERY]
    if fc2:
        candidate = fc2[0]
        metadata = metadata_lookup.get(candidate.lookup_code)
        if metadata.status in USABLE_METADATA:
            return _ResolvedVideo(
                video, candidate.display_code, metadata,
                canonical_base_code=candidate.lookup_code,
                display_code=candidate.display_code,
                recovery_kind=candidate.kind.value,
                raw_candidates=raw_candidates,
                recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_FOUND,
            )
        status = (
            NASRenameStatus.NEEDS_REVIEW_METADATA_ERROR
            if metadata.status == MetadataStatus.ERROR
            else NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND
        )
        return _ResolvedVideo(
            video, candidate.display_code, metadata,
            review_status=status,
            reason="reconstructed FC2-PPV candidate requires successful metadata validation",
            canonical_base_code=candidate.lookup_code,
            display_code=candidate.display_code,
            recovery_kind=candidate.kind.value,
            raw_candidates=raw_candidates,
            recovery_outcome=(
                RecoveryOutcome.OTHER
                if metadata.status == MetadataStatus.ERROR
                else RecoveryOutcome.RECOVERED_CODE_METADATA_NOT_FOUND
            ),
        )

    subtitle_candidates = extract_subtitle_code_candidates(video.path.name)
    suffix_histories = [
        HistoricalCodeCandidate(
            RecoveryKind.RELEASE_SUFFIX,
            item.original_match,
            item.base_code,
            item.base_code,
            raw_code=item.original_code,
            suffix_flag=item.subtitle_flag,
        )
        for item in subtitle_candidates
    ] + [item for item in histories if item.kind == RecoveryKind.RELEASE_SUFFIX]

    if direct.status == ProductCodeStatus.AMBIGUOUS_PRODUCT_CODE:
        return _ResolvedVideo(
            video, None, None,
            review_status=NASRenameStatus.AMBIGUOUS_PRODUCT_CODE,
            reason="multiple distinct product codes were found",
            raw_candidates=raw_candidates,
            recovery_outcome=RecoveryOutcome.OTHER,
        )
    if len(suffix_histories) > 1:
        return _ResolvedVideo(
            video, None, None,
            review_status=NASRenameStatus.AMBIGUOUS_PRODUCT_CODE,
            reason="multiple suffix-ending product-code candidates were found",
            raw_candidates=tuple(item.original_match for item in suffix_histories),
            recovery_outcome=RecoveryOutcome.OTHER,
        )
    if direct.status == ProductCodeStatus.FOUND:
        code = direct.normalized_code
        assert code is not None
        extras = [item for item in histories if item.kind == RecoveryKind.EXTRA_TOKEN]
        matching_extra = next((item for item in extras if item.lookup_code == code), None)
        return _ResolvedVideo(
            video, code, None,
            canonical_base_code=code,
            display_code=code,
            extra_token=matching_extra.extra_token if matching_extra else None,
            recovery_kind=matching_extra.kind.value if matching_extra else None,
            raw_candidates=raw_candidates,
        )
    if suffix_histories:
        return _resolve_suffix_candidate(video, metadata_lookup, suffix_histories[0])

    multipart = [item for item in histories if item.kind == RecoveryKind.MULTIPART]
    if len(multipart) == 1:
        candidate = multipart[0]
        metadata = metadata_lookup.get(candidate.lookup_code)
        if metadata.status in USABLE_METADATA:
            return _ResolvedVideo(
                video, candidate.display_code, metadata,
                canonical_base_code=candidate.lookup_code,
                display_code=candidate.display_code,
                part_flag=candidate.part_flag,
                recovery_kind=candidate.kind.value,
                raw_candidates=(candidate.original_match,),
                recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_FOUND,
            )
        return _ResolvedVideo(
            video, candidate.display_code, metadata,
            review_status=(
                NASRenameStatus.NEEDS_REVIEW_METADATA_ERROR
                if metadata.status == MetadataStatus.ERROR
                else NASRenameStatus.NEEDS_REVIEW_MULTI_PART
            ),
            reason="multipart base code requires successful metadata validation",
            canonical_base_code=candidate.lookup_code,
            display_code=candidate.display_code,
            part_flag=candidate.part_flag,
            recovery_kind=candidate.kind.value,
            raw_candidates=(candidate.original_match,),
            recovery_outcome=(
                RecoveryOutcome.OTHER
                if metadata.status == MetadataStatus.ERROR
                else RecoveryOutcome.NEEDS_REVIEW_MULTI_PART
            ),
        )

    extras = [item for item in histories if item.kind == RecoveryKind.EXTRA_TOKEN]
    if len(extras) == 1:
        candidate = extras[0]
        metadata = metadata_lookup.get(candidate.lookup_code)
        if metadata.status in USABLE_METADATA:
            return _ResolvedVideo(
                video, candidate.display_code, metadata,
                canonical_base_code=candidate.lookup_code,
                display_code=candidate.display_code,
                extra_token=candidate.extra_token,
                recovery_kind=candidate.kind.value,
                raw_candidates=(candidate.original_match,),
                recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_FOUND,
            )
        return _ResolvedVideo(
            video, candidate.display_code, metadata,
            review_status=(
                NASRenameStatus.NEEDS_REVIEW_METADATA_ERROR
                if metadata.status == MetadataStatus.ERROR
                else NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND
            ),
            reason="extra-token candidate requires successful metadata validation",
            canonical_base_code=candidate.lookup_code,
            display_code=candidate.display_code,
            extra_token=candidate.extra_token,
            recovery_kind=candidate.kind.value,
            raw_candidates=(candidate.original_match,),
            recovery_outcome=(
                RecoveryOutcome.OTHER
                if metadata.status == MetadataStatus.ERROR
                else RecoveryOutcome.RECOVERED_CODE_METADATA_NOT_FOUND
            ),
        )

    generic = [
        item for item in histories
        if item.kind == RecoveryKind.GENERIC_METADATA_VALIDATED
    ]
    if len(generic) == 1:
        candidate = generic[0]
        metadata = metadata_lookup.get(candidate.lookup_code)
        if metadata.status in USABLE_METADATA:
            return _ResolvedVideo(
                video, candidate.display_code, metadata,
                canonical_base_code=candidate.lookup_code,
                display_code=candidate.display_code,
                recovery_kind=candidate.kind.value,
                raw_candidates=(candidate.original_match,),
                recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_FOUND,
            )
        return _ResolvedVideo(
            video, candidate.display_code, metadata,
            review_status=(
                NASRenameStatus.NEEDS_REVIEW_METADATA_ERROR
                if metadata.status == MetadataStatus.ERROR
                else NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND
            ),
            reason="generic historical candidate requires successful metadata validation",
            canonical_base_code=candidate.lookup_code,
            display_code=candidate.display_code,
            recovery_kind=candidate.kind.value,
            raw_candidates=(candidate.original_match,),
            recovery_outcome=(
                RecoveryOutcome.OTHER
                if metadata.status == MetadataStatus.ERROR
                else RecoveryOutcome.RECOVERED_CODE_METADATA_NOT_FOUND
            ),
        )

    if not has_product_code_shape(video.path.name):
        return _ResolvedVideo(
            video, None, None,
            review_status=NASRenameStatus.NO_CODE_IN_FILENAME,
            reason="filename has no conservative product-code shape",
            raw_candidates=raw_candidates,
            recovery_outcome=RecoveryOutcome.NO_CODE_IN_FILENAME,
        )
    return _ResolvedVideo(
        video, None, None,
        review_status=NASRenameStatus.PRODUCT_CODE_NOT_FOUND,
        reason="code-like text exists but no safely recoverable code was validated",
        raw_candidates=raw_candidates,
        recovery_outcome=RecoveryOutcome.OTHER,
    )


def build_nas_rename_plans(
    target_directory: Path,
    metadata_lookup: MetadataLookup,
    max_actresses: int = 3,
    progress: Callable[[int, int], None] | None = None,
) -> list[NASRenamePlan]:
    videos = scan_nas_videos(target_directory)
    resolved = []
    for video in videos:
        resolved.append(_resolve_video(video, metadata_lookup))
    grouped: dict[str, list[_ResolvedVideo]] = {}
    for item in resolved:
        if item.product_code is not None and item.review_status is None:
            grouped.setdefault(item.product_code, []).append(item)

    for index, item in enumerate(resolved, start=1):
        if (
            item.review_status is None
            and item.product_code is not None
            and item.metadata is None
            and len(grouped[item.product_code]) == 1
        ):
            lookup_code = item.canonical_base_code or item.product_code
            metadata = metadata_lookup.get(lookup_code)
            if item.recovery_kind == RecoveryKind.EXTRA_TOKEN.value:
                if metadata.status in USABLE_METADATA:
                    item = replace(
                        item,
                        recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_FOUND,
                    )
                elif metadata.status == MetadataStatus.NOT_FOUND:
                    resolved[index - 1] = replace(
                        item,
                        metadata=metadata,
                        review_status=NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND,
                        reason="extra-token candidate requires successful metadata validation",
                        recovery_outcome=RecoveryOutcome.RECOVERED_CODE_METADATA_NOT_FOUND,
                    )
                    if progress:
                        progress(index, len(videos))
                    continue
            resolved[index - 1] = replace(
                item, metadata=metadata
            )
        if progress:
            progress(index, len(videos))

    plans = []
    for item in resolved:
        video = item.video
        if item.review_status is not None:
            plans.append(
                NASRenamePlan(
                    video.path, video.size, item.product_code, item.metadata, None,
                    item.review_status,
                    NASRenameAction.NEEDS_REVIEW,
                    item.reason,
                    subtitle_flag=item.subtitle_flag,
                    canonical_base_code=item.canonical_base_code,
                    display_code=item.display_code,
                    part_flag=item.part_flag,
                    extra_token=item.extra_token,
                    recovery_kind=item.recovery_kind,
                    raw_candidates=item.raw_candidates,
                    recovery_outcome=item.recovery_outcome,
                )
            )
            continue
        code = item.product_code
        assert code is not None
        same_code = grouped[code]
        if len(same_code) > 1:
            plans.append(
                NASRenamePlan(
                    video.path, video.size, code, None, None,
                    NASRenameStatus.NEEDS_REVIEW_DUPLICATE_CODE,
                    NASRenameAction.NEEDS_REVIEW,
                    "multiple formal NAS videos share this product code",
                    duplicate_files=tuple(match.video.path for match in same_code),
                    subtitle_flag=item.subtitle_flag,
                    canonical_base_code=item.canonical_base_code,
                    display_code=item.display_code,
                    part_flag=item.part_flag,
                    extra_token=item.extra_token,
                    recovery_kind=item.recovery_kind,
                    raw_candidates=item.raw_candidates,
                    recovery_outcome=item.recovery_outcome,
                )
            )
            continue
        assert item.metadata is not None
        plans.append(
            _plan_unique_video(
                video,
                code,
                item.metadata,
                max_actresses,
                item.subtitle_flag,
                canonical_base_code=item.canonical_base_code,
                part_flag=item.part_flag,
                extra_token=item.extra_token,
                recovery_kind=item.recovery_kind,
                raw_candidates=item.raw_candidates,
                recovery_outcome=item.recovery_outcome,
            )
        )
    return plans


def _state_changed(plan: NASRenamePlan, reason: str) -> NASRenamePlan:
    return replace(
        plan,
        status=NASRenameStatus.SKIPPED_STATE_CHANGED,
        action=NASRenameAction.NO_ACTION,
        reason=reason,
    )


def reject_batch_target_collisions(
    plans: list[NASRenamePlan],
) -> list[NASRenamePlan]:
    """Skip every executable plan sharing a case/Unicode-equivalent target."""
    by_target: dict[str, list[int]] = {}
    for index, plan in enumerate(plans):
        if plan.action == NASRenameAction.RENAME and plan.proposed_path is not None:
            by_target.setdefault(_name_key(plan.proposed_path.name), []).append(index)
    collided = {index for indexes in by_target.values() if len(indexes) > 1 for index in indexes}
    return [
        _state_changed(plan, "same-batch target collision") if index in collided else plan
        for index, plan in enumerate(plans)
    ]


def _has_potential_duplicate_code(current: Path, product_code: str) -> bool:
    """Conservatively detect a second current filename that may resolve to code."""
    with os.scandir(current.parent) as entries:
        for entry in entries:
            if entry.name == current.name or entry.name.startswith("."):
                continue
            if entry.name.endswith(".uploading"):
                continue
            if Path(entry.name).suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            direct = extract_product_code(entry.name)
            if (
                direct.status == ProductCodeStatus.FOUND
                and direct.normalized_code == product_code
            ):
                return True
            for candidate in extract_subtitle_code_candidates(entry.name):
                if product_code in {candidate.base_code, candidate.original_code}:
                    return True
            for candidate in extract_historical_code_candidates(entry.name):
                if candidate.display_code == product_code:
                    return True
    return False


def revalidate_nas_rename_plan(
    plan: NASRenamePlan,
    target_directory: Path,
    metadata_lookup: MetadataLookup,
    max_actresses: int = 3,
) -> NASRenamePlan:
    """Recompute one planned rename against current disk and metadata state."""
    if plan.action != NASRenameAction.RENAME or plan.proposed_path is None:
        return _state_changed(plan, "plan is no longer an executable rename")
    source = plan.current_path
    try:
        if not source.is_file() or source.is_symlink():
            return _state_changed(plan, "source no longer exists as a regular file")
        if source.parent.resolve() != target_directory.resolve():
            return _state_changed(plan, "source is no longer in the NAS target directory")
        size = source.stat().st_size
        if size != plan.current_size:
            return _state_changed(plan, "source size changed since planning")
        video = NASVideo(source, size, extract_product_code(source.name))
        resolved = _resolve_video(video, metadata_lookup)
        if resolved.review_status is not None or resolved.product_code is None:
            return _state_changed(plan, "fresh product-code or metadata state needs review")
        if _has_potential_duplicate_code(source, resolved.product_code):
            return _state_changed(plan, "a potential same-code NAS file is now present")
        metadata = resolved.metadata or metadata_lookup.get(
            resolved.canonical_base_code or resolved.product_code
        )
        fresh = _plan_unique_video(
            video,
            resolved.product_code,
            metadata,
            max_actresses,
            resolved.subtitle_flag,
            canonical_base_code=resolved.canonical_base_code,
            part_flag=resolved.part_flag,
            extra_token=resolved.extra_token,
            recovery_kind=resolved.recovery_kind,
            raw_candidates=resolved.raw_candidates,
            recovery_outcome=resolved.recovery_outcome,
        )
        old_metadata = plan.metadata
        same_metadata = (
            old_metadata is not None
            and old_metadata.status == metadata.status
            and old_metadata.actresses == metadata.actresses
        )
        if (
            fresh.action != NASRenameAction.RENAME
            or fresh.status != plan.status
            or fresh.product_code != plan.product_code
            or fresh.proposed_path != plan.proposed_path
            or fresh.subtitle_flag != plan.subtitle_flag
            or fresh.canonical_base_code != plan.canonical_base_code
            or fresh.display_code != plan.display_code
            or fresh.part_flag != plan.part_flag
            or fresh.extra_token != plan.extra_token
            or fresh.recovery_kind != plan.recovery_kind
            or fresh.recovery_outcome != plan.recovery_outcome
            or not same_metadata
        ):
            return _state_changed(plan, "fresh rename classification differs from the plan")
        return fresh
    except OSError as exc:
        return _state_changed(plan, f"state revalidation failed: {exc}")


def execute_nas_rename(
    plan: NASRenamePlan,
    nas_mount_path: Path,
    target_directory: Path,
    metadata_lookup: MetadataLookup | None = None,
    max_actresses: int = 3,
) -> NASRenamePlan:
    """Execute one preflighted same-directory rename without overwrite."""
    if plan.action != NASRenameAction.RENAME or plan.proposed_path is None:
        return plan
    if metadata_lookup is not None:
        plan = revalidate_nas_rename_plan(
            plan, target_directory, metadata_lookup, max_actresses
        )
        if plan.status == NASRenameStatus.SKIPPED_STATE_CHANGED:
            return plan
    source = plan.current_path
    target = plan.proposed_path
    try:
        if not os.path.ismount(nas_mount_path):
            return replace(
                plan, status=NASRenameStatus.NEEDS_REVIEW,
                action=NASRenameAction.NEEDS_REVIEW,
                reason="NAS mount is no longer mounted",
            )
        resolved_directory = target_directory.resolve()
        if source.parent.resolve() != resolved_directory or target.parent.resolve() != resolved_directory:
            return replace(
                plan, status=NASRenameStatus.NEEDS_REVIEW,
                action=NASRenameAction.NEEDS_REVIEW,
                reason="source or target is outside the configured NAS target directory",
            )
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("source disappeared or is no longer a regular file")
        if source.stat().st_size != plan.current_size:
            return replace(
                plan, status=NASRenameStatus.NEEDS_REVIEW,
                action=NASRenameAction.NEEDS_REVIEW,
                reason="source size changed after Preview",
            )
        if source.suffix != target.suffix or source.parent != target.parent:
            return replace(
                plan, status=NASRenameStatus.NEEDS_REVIEW,
                action=NASRenameAction.NEEDS_REVIEW,
                reason="rename would change extension or directory",
            )
        if _name_key(source.name) == _name_key(target.name):
            return _state_changed(plan, "source and target are the same normalized filename")
        conflict = _target_conflict(source, target)
        if conflict is not None:
            return replace(
                plan, status=NASRenameStatus.NEEDS_REVIEW_TARGET_EXISTS,
                action=NASRenameAction.NEEDS_REVIEW,
                reason="target appeared after Preview",
                conflict_path=conflict,
            )
        size_before = source.stat().st_size
        extension_before = source.suffix
        source.rename(target)
        size_after = target.stat().st_size if target.is_file() else None
        verified = (
            target.is_file()
            and not source.exists()
            and size_after == size_before
            and target.suffix == extension_before
            and target.parent.resolve() == resolved_directory
            and not target.with_name(target.name + ".uploading").exists()
        )
        if not verified:
            return replace(
                plan, status=NASRenameStatus.FAILED,
                action=NASRenameAction.NEEDS_REVIEW,
                reason="post-rename verification failed; no recovery attempted",
                size_after=size_after,
            )
        return replace(
            plan, status=NASRenameStatus.RENAMED_OK,
            action=NASRenameAction.NO_ACTION,
            reason="rename and immediate verification succeeded",
            size_after=size_after,
        )
    except (OSError, RuntimeError) as exc:
        return replace(
            plan, status=NASRenameStatus.FAILED,
            action=NASRenameAction.NEEDS_REVIEW,
            reason=str(exc),
        )
