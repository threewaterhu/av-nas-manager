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


_DOMAIN_PREFIX = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?[a-z0-9-]+\.(?:com|net|org|tv|co|cc|me|xyz)(?:@|[\s._-]+)?"
)
_CODE = re.compile(
    r"(?i)(?<![a-z0-9])(?P<prefix>(?:\d{2,4})?[a-z]{2,10})[\s._-]?(?P<number>\d{2,5})(?![a-z0-9])"
)
_NOISE_PREFIXES = {
    "FHD", "FULLHD", "H", "H264", "H265", "HD", "HEVC", "UHD", "X264", "X265"
}
_NOISE_NUMBERS = {"1080", "2160", "4320"}


def _normalize(prefix: str, number: str) -> str | None:
    prefix = prefix.upper()
    numeric = int(number)
    if (
        prefix in _NOISE_PREFIXES
        or number in _NOISE_NUMBERS
        or 1900 <= numeric <= 2099
    ):
        return None
    return f"{prefix}-{number}"


def extract_product_code(filename: str | Path) -> ProductCodeResult:
    """Extract one reliable code, reporting ambiguity rather than guessing."""
    stem = Path(filename).stem
    searchable = _DOMAIN_PREFIX.sub(" ", stem)
    matches: list[tuple[str, str]] = []
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
