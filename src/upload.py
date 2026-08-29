"""Conservative NAS deduplication, streaming upload, and verification."""

from __future__ import annotations

import os
import sqlite3
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from .product_code import ProductCodeStatus, extract_product_code
from .scanner import QbitFileRecord


class UploadAction(str, Enum):
    UPLOAD = "UPLOAD"
    ALREADY_UPLOADED = "ALREADY_UPLOADED"
    RECOVER_TEMP = "RECOVER_TEMP"
    RESTART_TEMP = "RESTART_TEMP"
    NEEDS_REVIEW_SIZE_MISMATCH = "NEEDS_REVIEW_SIZE_MISMATCH"
    NEEDS_REVIEW_DUPLICATES = "NEEDS_REVIEW_DUPLICATES"
    NEEDS_REVIEW_STALE_UPLOAD = "NEEDS_REVIEW_STALE_UPLOAD"
    NEEDS_REVIEW_UPLOAD_SIZE = "NEEDS_REVIEW_UPLOAD_SIZE"
    SKIPPED = "SKIPPED"


class UploadStatus(str, Enum):
    READY = "READY"
    ALREADY_UPLOADED = "ALREADY_UPLOADED"
    UPLOADING = "UPLOADING"
    UPLOADED_SIZE_OK = "UPLOADED_SIZE_OK"
    UPLOADED_VERIFIED = "UPLOADED_VERIFIED"
    RECOVERED_COMPLETED_UPLOAD = "RECOVERED_COMPLETED_UPLOAD"
    FAILED = "FAILED"
    FAILED_SIZE_MISMATCH = "FAILED_SIZE_MISMATCH"
    NEEDS_REVIEW_SIZE_MISMATCH = "NEEDS_REVIEW_SIZE_MISMATCH"
    NEEDS_REVIEW_DUPLICATES = "NEEDS_REVIEW_DUPLICATES"
    NEEDS_REVIEW_STALE_UPLOAD = "NEEDS_REVIEW_STALE_UPLOAD"
    NEEDS_REVIEW_UPLOAD_SIZE = "NEEDS_REVIEW_UPLOAD_SIZE"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class UploadPlan:
    product_code: str
    local_path: Path
    local_size: int
    nas_final_path: Path
    nas_temp_path: Path
    action: UploadAction
    reason: str
    matching_final_files: tuple[Path, ...] = ()
    matching_temp_files: tuple[Path, ...] = ()
    qbit_progress: float | None = None


@dataclass(frozen=True)
class UploadResult:
    plan: UploadPlan
    status: UploadStatus
    error: str = ""
    local_size_after: int | None = None
    nas_size_after: int | None = None


def _name_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _same_directory(path: Path, directory: Path) -> bool:
    try:
        return path.parent.resolve() == directory.resolve()
    except OSError:
        return False


def _files_for_code(target_directory: Path, product_code: str) -> tuple[list[Path], list[Path]]:
    formal: list[Path] = []
    temporary: list[Path] = []
    # Parse directory-entry names first. On SMB, stat/is_file for every unrelated
    # entry is very expensive; only same-code names need a metadata request.
    for entry in os.scandir(target_directory):
        result = extract_product_code(entry.name)
        if result.status != ProductCodeStatus.FOUND or result.normalized_code != product_code:
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        item = Path(entry.path)
        if entry.name.endswith(".uploading"):
            temporary.append(item)
        else:
            formal.append(item)
    return formal, temporary


def _qbit_match_by_code_and_size(
    product_code: str, local_size: int, records: Iterable[QbitFileRecord]
) -> tuple[QbitFileRecord | None, str]:
    matches = []
    for record in records:
        code = extract_product_code(record.file_name)
        if (
            code.status == ProductCodeStatus.FOUND
            and code.normalized_code == product_code
            and record.size == local_size
        ):
            matches.append(record)
    if not matches:
        return None, "no qBittorrent file matches this code and exact size"
    if len(matches) > 1:
        return None, "multiple qBittorrent files match this code and exact size"
    return matches[0], ""


def plan_upload(
    local_path: Path,
    product_code: str,
    target_directory: Path,
    qbit_records: Iterable[QbitFileRecord],
    min_video_size_mb: int = 200,
) -> UploadPlan:
    final_path = target_directory / local_path.name
    temp_path = target_directory / f"{local_path.name}.uploading"

    def plan(action: UploadAction, reason: str, **values: object) -> UploadPlan:
        return UploadPlan(
            product_code=product_code,
            local_path=local_path,
            local_size=local_path.stat().st_size if local_path.is_file() else 0,
            nas_final_path=final_path,
            nas_temp_path=temp_path,
            action=action,
            reason=reason,
            **values,
        )

    if not local_path.exists() or local_path.is_symlink() or not local_path.is_file():
        return plan(UploadAction.SKIPPED, "local source is missing or not a regular file")
    local_size = local_path.stat().st_size
    if local_size < min_video_size_mb * 1024 * 1024:
        return plan(UploadAction.SKIPPED, "local source is below minimum size")
    code = extract_product_code(local_path.name)
    if code.status != ProductCodeStatus.FOUND or code.normalized_code != product_code:
        return plan(UploadAction.SKIPPED, "local filename does not contain the approved code")
    if not target_directory.is_dir():
        return plan(UploadAction.SKIPPED, "NAS target directory is unavailable")
    if not _same_directory(final_path, target_directory) or not _same_directory(temp_path, target_directory):
        return plan(UploadAction.SKIPPED, "resolved NAS path is outside target directory")

    qbit, qbit_error = _qbit_match_by_code_and_size(product_code, local_size, qbit_records)
    if qbit is None:
        return plan(UploadAction.SKIPPED, qbit_error)
    if qbit.progress != 1.0:
        return plan(
            UploadAction.SKIPPED,
            "qBittorrent file progress is below 1.0",
            qbit_progress=qbit.progress,
        )

    formal, temporary = _files_for_code(target_directory, product_code)
    formal_tuple = tuple(sorted(formal))
    temp_tuple = tuple(sorted(temporary))
    common = {
        "matching_final_files": formal_tuple,
        "matching_temp_files": temp_tuple,
        "qbit_progress": qbit.progress,
    }
    if len(formal) > 1:
        return plan(
            UploadAction.NEEDS_REVIEW_DUPLICATES,
            "multiple formal NAS files have the same product code",
            **common,
        )
    if len(formal) == 1:
        nas_size = formal[0].stat().st_size
        if nas_size == local_size:
            return plan(
                UploadAction.ALREADY_UPLOADED,
                "one same-code NAS file has the same byte size",
                **common,
            )
        return plan(
            UploadAction.NEEDS_REVIEW_SIZE_MISMATCH,
            f"same-code NAS file size differs: local={local_size}, NAS={nas_size}",
            **common,
        )

    exact_temps = [item for item in temporary if item.name == temp_path.name]
    other_temps = [item for item in temporary if item.name != temp_path.name]
    if other_temps or len(exact_temps) > 1:
        return plan(
            UploadAction.NEEDS_REVIEW_STALE_UPLOAD,
            "same-code temporary file does not exactly match the current target",
            **common,
        )
    if not exact_temps:
        return plan(UploadAction.UPLOAD, "no same-code formal or temporary NAS file", **common)

    existing_temp = exact_temps[0]
    if not _same_directory(existing_temp, target_directory):
        return plan(
            UploadAction.NEEDS_REVIEW_STALE_UPLOAD,
            "temporary file resolves outside target directory",
            **common,
        )
    temp_size = existing_temp.stat().st_size
    if temp_size == local_size:
        return plan(
            UploadAction.RECOVER_TEMP,
            "temporary file already has the complete byte size",
            **common,
        )
    if temp_size < local_size:
        return plan(
            UploadAction.RESTART_TEMP,
            f"confirmed incomplete exact temporary file: {temp_size} < {local_size}",
            **common,
        )
    return plan(
        UploadAction.NEEDS_REVIEW_UPLOAD_SIZE,
        f"temporary file is larger than local source: {temp_size} > {local_size}",
        **common,
    )


class UploadStateStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS upload_state (
                    product_code TEXT PRIMARY KEY,
                    local_path TEXT NOT NULL,
                    local_filename TEXT NOT NULL,
                    local_size INTEGER NOT NULL,
                    nas_final_path TEXT NOT NULL,
                    nas_temp_path TEXT NOT NULL,
                    upload_status TEXT NOT NULL,
                    uploaded_at TEXT,
                    verified_at TEXT,
                    error TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def record(self, plan: UploadPlan, status: UploadStatus, error: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        uploaded_at = now if status in {
            UploadStatus.UPLOADED_SIZE_OK,
            UploadStatus.UPLOADED_VERIFIED,
            UploadStatus.RECOVERED_COMPLETED_UPLOAD,
        } else None
        verified_at = now if status in {
            UploadStatus.UPLOADED_VERIFIED,
            UploadStatus.RECOVERED_COMPLETED_UPLOAD,
            UploadStatus.ALREADY_UPLOADED,
        } else None
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT uploaded_at, verified_at FROM upload_state WHERE product_code=?",
                (plan.product_code,),
            ).fetchone()
            if existing:
                uploaded_at = uploaded_at or existing[0]
                verified_at = verified_at or existing[1]
            connection.execute(
                """
                INSERT INTO upload_state
                    (product_code, local_path, local_filename, local_size,
                     nas_final_path, nas_temp_path, upload_status, uploaded_at,
                     verified_at, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_code) DO UPDATE SET
                    local_path=excluded.local_path,
                    local_filename=excluded.local_filename,
                    local_size=excluded.local_size,
                    nas_final_path=excluded.nas_final_path,
                    nas_temp_path=excluded.nas_temp_path,
                    upload_status=excluded.upload_status,
                    uploaded_at=excluded.uploaded_at,
                    verified_at=excluded.verified_at,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    plan.product_code, str(plan.local_path), plan.local_path.name,
                    plan.local_size, str(plan.nas_final_path), str(plan.nas_temp_path),
                    status.value, uploaded_at, verified_at, error, now,
                ),
            )

    def get(self, product_code: str) -> dict[str, object] | None:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM upload_state WHERE product_code=?", (product_code,)
            ).fetchone()
        return dict(row) if row else None


ProgressCallback = Callable[[int, int], None]
CopyImplementation = Callable[[Path, Path, int, ProgressCallback | None], None]


def stream_copy(
    source: Path,
    destination: Path,
    buffer_size: int = 8 * 1024 * 1024,
    progress: ProgressCallback | None = None,
) -> None:
    total = source.stat().st_size
    copied = 0
    with source.open("rb") as source_handle, destination.open("xb") as target_handle:
        while True:
            chunk = source_handle.read(buffer_size)
            if not chunk:
                break
            target_handle.write(chunk)
            copied += len(chunk)
            if progress:
                progress(copied, total)
        target_handle.flush()
        os.fsync(target_handle.fileno())


def _status_for_action(action: UploadAction) -> UploadStatus:
    mapping = {
        UploadAction.ALREADY_UPLOADED: UploadStatus.ALREADY_UPLOADED,
        UploadAction.NEEDS_REVIEW_SIZE_MISMATCH: UploadStatus.NEEDS_REVIEW_SIZE_MISMATCH,
        UploadAction.NEEDS_REVIEW_DUPLICATES: UploadStatus.NEEDS_REVIEW_DUPLICATES,
        UploadAction.NEEDS_REVIEW_STALE_UPLOAD: UploadStatus.NEEDS_REVIEW_STALE_UPLOAD,
        UploadAction.NEEDS_REVIEW_UPLOAD_SIZE: UploadStatus.NEEDS_REVIEW_UPLOAD_SIZE,
        UploadAction.SKIPPED: UploadStatus.SKIPPED,
    }
    return mapping.get(action, UploadStatus.READY)


def execute_upload(
    plan: UploadPlan,
    state_store: UploadStateStore,
    buffer_size: int = 8 * 1024 * 1024,
    progress: ProgressCallback | None = None,
    copier: CopyImplementation = stream_copy,
) -> UploadResult:
    if plan.action not in {UploadAction.UPLOAD, UploadAction.RESTART_TEMP, UploadAction.RECOVER_TEMP}:
        status = _status_for_action(plan.action)
        state_store.record(plan, status, plan.reason if "REVIEW" in status.value else "")
        return UploadResult(plan, status)

    source = plan.local_path
    final = plan.nas_final_path
    temp = plan.nas_temp_path
    target_directory = final.parent
    try:
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("local source disappeared or is no longer a regular file")
        if source.stat().st_size != plan.local_size:
            raise RuntimeError("local source size changed after preflight")
        if not _same_directory(final, target_directory) or not _same_directory(temp, target_directory):
            raise RuntimeError("NAS paths no longer resolve inside target directory")
        formal, _temporary = _files_for_code(target_directory, plan.product_code)
        if formal:
            raise RuntimeError("a same-code formal NAS file appeared after preflight")

        if plan.action == UploadAction.RECOVER_TEMP:
            if not temp.is_file() or temp.is_symlink() or temp.stat().st_size != plan.local_size:
                raise RuntimeError("complete temporary file changed after preflight")
            temp.rename(final)
            verified = final.is_file() and not temp.exists() and final.stat().st_size == plan.local_size
            if not verified:
                raise RuntimeError("recovered final file failed verification")
            state_store.record(plan, UploadStatus.RECOVERED_COMPLETED_UPLOAD)
            return UploadResult(
                plan, UploadStatus.RECOVERED_COMPLETED_UPLOAD,
                local_size_after=source.stat().st_size,
                nas_size_after=final.stat().st_size,
            )

        if plan.action == UploadAction.RESTART_TEMP:
            if (
                not temp.is_file()
                or temp.is_symlink()
                or temp.name != f"{final.name}.uploading"
                or temp.parent.resolve() != target_directory.resolve()
                or final.exists()
                or temp.stat().st_size >= plan.local_size
            ):
                state_store.record(
                    plan, UploadStatus.NEEDS_REVIEW_STALE_UPLOAD,
                    "temporary file failed the safe-delete recheck",
                )
                return UploadResult(
                    plan, UploadStatus.NEEDS_REVIEW_STALE_UPLOAD,
                    "temporary file failed the safe-delete recheck",
                )
            temp.unlink()
        elif temp.exists():
            raise RuntimeError("temporary file appeared after preflight")

        state_store.record(plan, UploadStatus.UPLOADING)
        copier(source, temp, buffer_size, progress)
        local_size_after = source.stat().st_size
        temp_size = temp.stat().st_size if temp.is_file() else None
        if local_size_after != plan.local_size or temp_size != local_size_after:
            error = f"size mismatch after copy: local={local_size_after}, temp={temp_size}"
            state_store.record(plan, UploadStatus.FAILED_SIZE_MISMATCH, error)
            return UploadResult(
                plan, UploadStatus.FAILED_SIZE_MISMATCH, error,
                local_size_after, temp_size,
            )
        state_store.record(plan, UploadStatus.UPLOADED_SIZE_OK)
        if final.exists():
            raise RuntimeError("formal target appeared before final rename")
        temp.rename(final)
        verified = (
            final.is_file()
            and not temp.exists()
            and source.stat().st_size == final.stat().st_size == plan.local_size
        )
        if not verified:
            raise RuntimeError("final file failed post-rename verification")
        state_store.record(plan, UploadStatus.UPLOADED_VERIFIED)
        return UploadResult(
            plan, UploadStatus.UPLOADED_VERIFIED,
            local_size_after=source.stat().st_size,
            nas_size_after=final.stat().st_size,
        )
    except (OSError, RuntimeError) as exc:
        error = str(exc)
        state_store.record(plan, UploadStatus.FAILED, error)
        return UploadResult(plan, UploadStatus.FAILED, error)
