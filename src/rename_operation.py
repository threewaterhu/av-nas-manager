"""Conservative local-only rename preflight and execution."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterable

from .metadata.base import Metadata, MetadataStatus
from .product_code import ProductCodeStatus, extract_product_code
from .renamer import (
    clean_original_description,
    description_without_code,
    propose_filename,
)
from .scanner import QbitFileRecord, VIDEO_EXTENSIONS, match_qbit_file


class RenameStatus(str, Enum):
    READY = "READY"
    RENAMED_OK = "RENAMED_OK"
    ALREADY_STANDARDIZED = "ALREADY_STANDARDIZED"
    SKIPPED = "SKIPPED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RenamePlan:
    product_code: str
    original_path: Path
    target_path: Path
    status: RenameStatus
    reason: str = ""
    size_before: int | None = None
    size_after: int | None = None
    qbit_progress: float | None = None
    fallback_raw_description: str = ""
    fallback_clean_description: str = ""


def _name_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except (OSError, ValueError):
        return False


def _conflicting_sibling(path: Path, target_name: str) -> Path | None:
    target_key = _name_key(target_name)
    try:
        siblings = path.parent.iterdir()
        for sibling in siblings:
            if sibling == path:
                continue
            if _name_key(sibling.name) == target_key:
                return sibling
    except OSError:
        return path.parent
    return None


def _exact_sibling(path: Path, target_name: str) -> Path | None:
    try:
        for sibling in path.parent.iterdir():
            if sibling != path and sibling.name == target_name:
                return sibling
    except OSError:
        return path.parent
    return None


def preflight_rename(
    path: Path,
    source_directory: Path,
    metadata: Metadata,
    qbit_records: Iterable[QbitFileRecord],
    min_video_size_mb: int = 200,
    max_actresses: int = 3,
) -> RenamePlan:
    code = metadata.product_code
    actresses = metadata.actresses if metadata.status == MetadataStatus.FOUND else ()
    raw_description = description_without_code(path, code) if path.suffix else ""
    clean_description = clean_original_description(path, code) if path.suffix else ""
    proposed_name = propose_filename(path, code, actresses, max_actresses)
    target = path.with_name(proposed_name)

    def plan(status: RenameStatus, reason: str, **values: object) -> RenamePlan:
        return RenamePlan(
            product_code=code,
            original_path=path,
            target_path=target,
            status=status,
            reason=reason,
            fallback_raw_description=raw_description,
            fallback_clean_description=clean_description,
            **values,
        )

    if not path.exists():
        return plan(RenameStatus.SKIPPED, "source file does not exist")
    if path.is_symlink() or not path.is_file():
        return plan(RenameStatus.NEEDS_REVIEW, "source is not a regular file")
    if not _inside(path, source_directory):
        return plan(RenameStatus.SKIPPED, "source is outside configured Downloads directory")
    code_result = extract_product_code(path.name)
    if (
        code_result.status != ProductCodeStatus.FOUND
        or code_result.normalized_code != code
    ):
        return plan(RenameStatus.NEEDS_REVIEW, "filename code does not match approved code")
    if path.suffix.casefold() not in VIDEO_EXTENSIONS:
        return plan(RenameStatus.SKIPPED, "unsupported video extension")

    try:
        size = path.stat().st_size
    except OSError as exc:
        return plan(RenameStatus.SKIPPED, f"cannot stat source: {exc}")
    if size < min_video_size_mb * 1024 * 1024:
        return plan(RenameStatus.SKIPPED, "source is below minimum size", size_before=size)
    if target.parent != path.parent:
        return plan(RenameStatus.NEEDS_REVIEW, "target would move to another directory", size_before=size)
    if target.suffix != path.suffix:
        return plan(RenameStatus.NEEDS_REVIEW, "target extension changed", size_before=size)
    if unicodedata.normalize("NFC", path.name) == unicodedata.normalize("NFC", proposed_name):
        return plan(
            RenameStatus.ALREADY_STANDARDIZED,
            "filename already matches the current naming rule",
            size_before=size,
            size_after=size,
        )

    if metadata.status != MetadataStatus.FOUND and clean_description:
        if not any(character.isalpha() for character in clean_description):
            return plan(
                RenameStatus.NEEDS_REVIEW,
                "fallback description contains no meaningful letters",
                size_before=size,
            )

    exact_target = _exact_sibling(path, proposed_name)
    if exact_target is not None:
        return plan(RenameStatus.SKIPPED, "target already exists", size_before=size)
    conflict = _conflicting_sibling(path, proposed_name)
    if conflict is not None:
        return plan(
            RenameStatus.NEEDS_REVIEW,
            f"target conflicts by case or Unicode normalization: {conflict.name}",
            size_before=size,
        )
    matched = match_qbit_file(path, size, qbit_records)
    if matched is None:
        return plan(
            RenameStatus.SKIPPED,
            "source cannot be matched to a qBittorrent file",
            size_before=size,
        )
    if matched.progress != 1.0:
        return plan(
            RenameStatus.SKIPPED,
            "qBittorrent file progress is below 1.0",
            size_before=size,
            qbit_progress=matched.progress,
        )
    return plan(
        RenameStatus.READY,
        "all preflight checks passed",
        size_before=size,
        qbit_progress=matched.progress,
    )


def execute_rename(plan: RenamePlan) -> RenamePlan:
    """Execute exactly one same-directory rename after a READY preflight."""
    if plan.status != RenameStatus.READY:
        return plan
    source = plan.original_path
    target = plan.target_path
    try:
        if not source.exists():
            return replace(plan, status=RenameStatus.FAILED, reason="source disappeared before rename")
        if (
            _exact_sibling(source, target.name) is not None
            or _conflicting_sibling(source, target.name) is not None
        ):
            return replace(plan, status=RenameStatus.FAILED, reason="target appeared before rename")
        size_before = source.stat().st_size
        extension_before = source.suffix
        source.rename(target)
        new_exists = target.exists()
        old_absent = not source.exists()
        size_after = target.stat().st_size if new_exists else None
        extension_preserved = target.suffix == extension_before
        if not (new_exists and old_absent and size_after == size_before and extension_preserved):
            return replace(
                plan,
                status=RenameStatus.FAILED,
                reason="post-rename verification failed; no recovery attempted",
                size_before=size_before,
                size_after=size_after,
            )
        return replace(
            plan,
            status=RenameStatus.RENAMED_OK,
            reason="rename and immediate verification succeeded",
            size_before=size_before,
            size_after=size_after,
        )
    except OSError as exc:
        return replace(plan, status=RenameStatus.FAILED, reason=f"rename failed: {exc}")


def discover_approved_candidates(
    source_directory: Path, approved_codes: set[str]
) -> dict[str, list[Path]]:
    found = {code: [] for code in approved_codes}
    for root, directories, files in os.walk(source_directory, followlinks=False):
        directories.sort()
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            result = extract_product_code(path.name)
            if result.status == ProductCodeStatus.FOUND and result.normalized_code in found:
                found[result.normalized_code].append(path)
    return found
