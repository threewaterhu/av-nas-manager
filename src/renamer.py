"""Preview-only, filesystem-safe proposed filename generation."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


_DOMAIN = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?[a-z0-9-]+\.(?:com|net|org|tv|co|cc|me|xyz)@?"
)
_LEADING_RELEASE_TAG = re.compile(
    r"(?i)^(?:\[[^\]]+\]\s*)?[a-z0-9][a-z0-9._-]{1,40}@+"
)
_NOISE = re.compile(
    r"(?i)(?<![a-z0-9])(?:1080p?|2160p?|4k|fhd|uhd|hd|hevc|h\.?264|h\.?265|x264|x265|unc)(?![a-z0-9])"
)
_BRACKET_NOISE = re.compile(
    r"[\[（(【][^\]）)】]*(?:1080|2160|4k|fhd|hd|hevc|h26[45]|x26[45])[^\]）)】]*[\]）)】]",
    re.IGNORECASE,
)
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/:\\*?"<>|]')
_SPACES = re.compile(r"\s+")


def _product_code_pattern(product_code: str) -> re.Pattern[str]:
    prefix, number = product_code.rsplit("-", 1)
    if prefix == "FC2-PPV":
        prefix_pattern = r"FC2[\s._-]*PPV"
    else:
        prefix_pattern = re.escape(prefix)
    return re.compile(
        rf"(?i)(?<![a-z0-9]){prefix_pattern}[\s._-]*{re.escape(number)}"
        rf"(?:ch|c)?(?![a-z0-9])"
    )


def sanitize_component(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = _UNSAFE.sub(" ", value)
    value = _SPACES.sub(" ", value).strip(" .")
    return value


def description_without_code(original_path: Path, product_code: str) -> str:
    code_pattern = _product_code_pattern(product_code)
    return code_pattern.sub(" ", original_path.stem).strip(" -_.@")


def preserved_fallback_description(
    original_path: Path,
    product_code: str,
) -> tuple[str, bool]:
    """Remove one primary code plus proven noise, preserving all other suffix text.

    The boolean reports unsafe filename characters whose sanitization could lose
    information; callers must require review instead of renaming in that case.
    """
    code_pattern = _product_code_pattern(product_code)
    description = code_pattern.sub(" ", original_path.stem, count=1)
    description = _LEADING_RELEASE_TAG.sub(" ", description)
    description = _DOMAIN.sub(" ", description)
    description = _BRACKET_NOISE.sub(" ", description)
    description = _NOISE.sub(" ", description)
    description = re.sub(r"[\[（(【]\s*[\]）)】]", " ", description)
    unsafe_information_loss = _UNSAFE.search(description) is not None
    description = sanitize_component(description).strip(" -_.@")
    return description, unsafe_information_loss


def propose_preserved_fallback_filename(
    original_path: Path,
    product_code: str,
) -> tuple[str, str, bool]:
    description, unsafe_information_loss = preserved_fallback_description(
        original_path, product_code
    )
    stem = product_code if not description else f"{product_code} {description}"
    return (
        f"{sanitize_component(stem)}{original_path.suffix}",
        description,
        unsafe_information_loss,
    )


def clean_description(description: str) -> str:
    description = _LEADING_RELEASE_TAG.sub(" ", description)
    description = _DOMAIN.sub(" ", description)
    description = _BRACKET_NOISE.sub(" ", description)
    description = _NOISE.sub(" ", description)
    description = re.sub(r"[@_\[\](){}【】（）.-]+", " ", description)
    return sanitize_component(description)


def clean_original_description(original_path: Path, product_code: str) -> str:
    return clean_description(description_without_code(original_path, product_code))


def propose_filename(
    original_path: Path,
    product_code: str,
    actresses: tuple[str, ...] = (),
    max_actresses: int = 3,
) -> str:
    extension = original_path.suffix
    selected = [sanitize_component(name) for name in actresses[:max_actresses]]
    selected = [name for name in selected if name]
    if selected:
        stem = f"{product_code} {', '.join(selected)}"
    else:
        description = clean_original_description(original_path, product_code)
        stem = product_code if not description else f"{product_code} {description}"
    return f"{sanitize_component(stem)}{extension}"
