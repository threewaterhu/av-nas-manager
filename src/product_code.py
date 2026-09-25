"""Conservative product-code extraction from video filenames."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ProductCodeStatus(str, Enum):
    FOUND = "FOUND"
    PRODUCT_CODE_NOT_FOUND = "PRODUCT_CODE_NOT_FOUND"
    AMBIGUOUS_PRODUCT_CODE = "AMBIGUOUS_PRODUCT_CODE"


@dataclass(frozen=True)
class ProductCodeResult:
    status: ProductCodeStatus
    original_matches: tuple[str, ...]
    normalized_code: str | None


@dataclass(frozen=True)
class SubtitleCodeCandidate:
    original_match: str
    original_code: str
    base_code: str
    subtitle_flag: str


class RecoveryKind(str, Enum):
    EXTRA_TOKEN = "EXTRA_TOKEN"
    RELEASE_SUFFIX = "RELEASE_SUFFIX"
    MULTIPART = "MULTIPART"
    FC2_SPLIT_RECOVERY = "FC2_SPLIT_RECOVERY"
    GENERIC_METADATA_VALIDATED = "GENERIC_METADATA_VALIDATED"


@dataclass(frozen=True)
class HistoricalCodeCandidate:
    kind: RecoveryKind
    original_match: str
    lookup_code: str
    display_code: str
    raw_code: str = ""
    suffix_flag: str = ""
    part_flag: str = ""
    extra_token: str = ""


_DOMAIN_PREFIX = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?[a-z0-9-]+\.(?:com|net|org|tv|co|cc|me|xyz)(?:@|[\s._-]+)?"
)
_CODE = re.compile(
    r"(?i)(?<![a-z0-9])(?P<prefix>fc2ppv|(?:\d{2,4})?[a-z]{2,10})[\s._-]?(?P<number>\d{2,7})(?![a-z0-9])"
)
_FC2_CANONICAL = re.compile(
    r"(?i)(?<![a-z0-9])fc2[_-]ppv[\s._-]?(?P<number>\d{5,9})(?![a-z0-9])"
)
_SUBTITLE_CODE = re.compile(
    r"(?i)(?<![a-z0-9])(?P<prefix>fc2ppv|(?:\d{2,4})?[a-z]{2,10})"
    r"[\s._-]?(?P<number>\d{2,7})(?P<flag>ch|c)(?![a-z0-9])"
)
_FC2_SPLIT = re.compile(
    r"(?i)(?<![a-z0-9])ppv[\s._-]?(?P<main>\d{5,7})\s+fc(?P<tail>\d{2})(?![a-z0-9])"
)
_RELEASE_SUFFIX = re.compile(
    r"(?i)(?<![a-z0-9])(?P<prefix>(?:\d{2,4})?[a-z]{2,10})"
    r"[\s._-]?(?P<number>\d{2,7})(?P<suffix>uncensored|uncen|unc)(?![a-z0-9])"
)
_MULTIPART = re.compile(
    r"(?i)(?<![a-z0-9])(?P<prefix>(?:\d{2,4})?[a-z]{2,10})"
    r"[\s._-]?(?P<number>\d{2,7})(?P<part>a|b)(?![a-z0-9])"
)
_EXTRA_TOKEN = re.compile(
    r"(?i)(?<![a-z0-9])(?P<prefix>(?:\d{2,4})?[a-z]{2,10})"
    r"[\s._-]+(?P<number>\d{2,7})\s+(?P<extra>\d{2,4})(?![a-z0-9])"
)
_CODE_LIKE = re.compile(
    r"(?i)(?<![a-z0-9])(?:\d{2,4})?[a-z]{2,10}[\s._-]*\d{2,7}"
)
_GENERIC_RECOVERY = re.compile(
    r"(?i)(?<![a-z0-9])(?P<prefix>(?:\d{2,4})?[a-z]{2,10})"
    r"[\s._-]+(?P<number>\d{2,7})(?![a-z0-9])"
)
_NOISE_PREFIXES = {
    "FHD", "FULLHD", "H", "H264", "H265", "HD", "HEVC", "UHD", "X264", "X265"
}
_NOISE_NUMBERS = {"1080", "2160", "4320"}


def _normalize(prefix: str, number: str) -> str | None:
    prefix = prefix.upper()
    if prefix == "FC2PPV":
        return f"FC2-PPV-{number}"
    numeric = int(number)
    if (
        prefix in _NOISE_PREFIXES
        or number in _NOISE_NUMBERS
        or 1900 <= numeric <= 2099
    ):
        return None
    return f"{prefix}-{number}"


def _candidate_code(prefix: str, number: str) -> str | None:
    """Format a code-like recovery candidate; metadata must validate it later."""
    prefix = prefix.upper()
    if prefix in _NOISE_PREFIXES or number in _NOISE_NUMBERS:
        return None
    return f"{prefix}-{number}"


def extract_product_code(filename: str | Path) -> ProductCodeResult:
    """Extract one reliable code, reporting ambiguity rather than guessing."""
    stem = Path(filename).stem
    searchable = _DOMAIN_PREFIX.sub(" ", stem)
    matches: list[tuple[str, str]] = []
    canonical_spans = []
    for match in _FC2_CANONICAL.finditer(searchable):
        matches.append((match.group(0), f"FC2-PPV-{match.group('number')}"))
        canonical_spans.append(match.span())
    if canonical_spans:
        characters = list(searchable)
        for start, end in canonical_spans:
            characters[start:end] = " " * (end - start)
        searchable = "".join(characters)
    for match in _CODE.finditer(searchable):
        normalized = _normalize(match.group("prefix"), match.group("number"))
        if normalized:
            matches.append((match.group(0), normalized))

    unique_codes = list(dict.fromkeys(normalized for _, normalized in matches))
    originals = tuple(original for original, _ in matches)
    if not unique_codes:
        return ProductCodeResult(
            ProductCodeStatus.PRODUCT_CODE_NOT_FOUND, originals, None
        )
    if len(unique_codes) > 1:
        return ProductCodeResult(
            ProductCodeStatus.AMBIGUOUS_PRODUCT_CODE, originals, None
        )
    return ProductCodeResult(ProductCodeStatus.FOUND, originals, unique_codes[0])


def extract_historical_code_candidates(
    filename: str | Path,
) -> tuple[HistoricalCodeCandidate, ...]:
    """Generate conservative historical candidates that still need metadata validation."""
    stem = Path(filename).stem
    searchable = _DOMAIN_PREFIX.sub(" ", stem)
    candidates: list[HistoricalCodeCandidate] = []

    split = _FC2_SPLIT.search(searchable)
    if split:
        tail = split.group("tail")
        reconstructed = f"FC2-PPV-{split.group('main')}{tail[1:]}"
        candidates.append(
            HistoricalCodeCandidate(
                RecoveryKind.FC2_SPLIT_RECOVERY,
                split.group(0),
                reconstructed,
                reconstructed,
                raw_code=split.group(0),
            )
        )
        return tuple(candidates)

    for match in _RELEASE_SUFFIX.finditer(searchable):
        base = _candidate_code(match.group("prefix"), match.group("number"))
        if base:
            suffix = match.group("suffix").upper()
            candidates.append(
                HistoricalCodeCandidate(
                    RecoveryKind.RELEASE_SUFFIX,
                    match.group(0),
                    base,
                    base,
                    raw_code=f"{base}{suffix}",
                    suffix_flag=suffix,
                )
            )
    for match in _MULTIPART.finditer(searchable):
        base = _candidate_code(match.group("prefix"), match.group("number"))
        if base:
            part = match.group("part").upper()
            candidates.append(
                HistoricalCodeCandidate(
                    RecoveryKind.MULTIPART,
                    match.group(0),
                    base,
                    f"{base}{part}",
                    part_flag=part,
                )
            )
    for match in _EXTRA_TOKEN.finditer(searchable):
        base = _candidate_code(match.group("prefix"), match.group("number"))
        if base:
            candidates.append(
                HistoricalCodeCandidate(
                    RecoveryKind.EXTRA_TOKEN,
                    match.group(0),
                    base,
                    base,
                    extra_token=match.group("extra"),
                )
            )
    for match in _GENERIC_RECOVERY.finditer(searchable):
        code = _candidate_code(match.group("prefix"), match.group("number"))
        if code:
            candidates.append(
                HistoricalCodeCandidate(
                    RecoveryKind.GENERIC_METADATA_VALIDATED,
                    match.group(0),
                    code,
                    code,
                )
            )
    unique: dict[tuple[RecoveryKind, str, str], HistoricalCodeCandidate] = {}
    for candidate in candidates:
        unique.setdefault(
            (candidate.kind, candidate.lookup_code, candidate.display_code), candidate
        )
    return tuple(unique.values())


def has_product_code_shape(filename: str | Path) -> bool:
    stem = _DOMAIN_PREFIX.sub(" ", Path(filename).stem)
    return _CODE_LIKE.search(stem) is not None or bool(
        re.fullmatch(r"(?i)[a-z0-9._~-]{4,30}", stem.strip())
    )


def extract_subtitle_code_candidates(
    filename: str | Path,
) -> tuple[SubtitleCodeCandidate, ...]:
    """Return C/CH-ending candidates for cache/provider validation by callers."""
    stem = Path(filename).stem
    searchable = _DOMAIN_PREFIX.sub(" ", stem)
    candidates: list[SubtitleCodeCandidate] = []
    seen: set[tuple[str, str]] = set()
    for match in _SUBTITLE_CODE.finditer(searchable):
        base_code = _normalize(match.group("prefix"), match.group("number"))
        if base_code is None:
            continue
        flag = match.group("flag").upper()
        key = (base_code, flag)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            SubtitleCodeCandidate(
                original_match=match.group(0),
                original_code=f"{base_code}{flag}",
                base_code=base_code,
                subtitle_flag=flag,
            )
        )
    return tuple(candidates)
