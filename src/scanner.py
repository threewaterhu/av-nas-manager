"""Read-only local video scanner and qBittorrent file matcher."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .product_code import ProductCodeStatus, extract_product_code
from .qbittorrent_client import QBittorrentClient, Torrent, TorrentFile


VIDEO_EXTENSIONS = {
    ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg",
    ".mpg", ".ts", ".webm", ".wmv",
}


class ScanStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN_NOT_IN_QBITTORRENT = "UNKNOWN_NOT_IN_QBITTORRENT"
    SKIPPED_TOO_SMALL = "SKIPPED_TOO_SMALL"


@dataclass(frozen=True)
class QbitFileRecord:
    torrent_hash: str
    torrent_name: str
    save_path: str
    content_path: str
    file_name: str
    size: int
    progress: float


@dataclass(frozen=True)
class ScanResult:
    path: Path
    size: int
    status: ScanStatus
    torrent_hash: str | None = None
    torrent_file: str | None = None
    progress: float | None = None


def _path_key(path: Path | str) -> str:
    text = os.path.abspath(os.path.normpath(os.fspath(path)))
    return unicodedata.normalize("NFC", text).casefold()


def expected_paths(record: QbitFileRecord) -> set[str]:
    """Return plausible absolute paths emitted by qBittorrent for one file."""
    file_path = Path(record.file_name)
    save_path = Path(record.save_path)
    content_path = Path(record.content_path) if record.content_path else None
    candidates = {save_path / file_path}

    if content_path:
        # For a single-file torrent content_path normally points at the file.
        if content_path.name.casefold() == file_path.name.casefold():
            candidates.add(content_path)
        # Multi-file API names may include or omit the torrent root directory.
        candidates.add(content_path / file_path)
        parts = file_path.parts
        if parts and parts[0].casefold() in {
            record.torrent_name.casefold(), content_path.name.casefold()
        }:
            candidates.add(content_path.joinpath(*parts[1:]))
    return {_path_key(path) for path in candidates}


def match_qbit_file(path: Path, size: int, records: Iterable[QbitFileRecord]) -> QbitFileRecord | None:
    records = list(records)
    key = _path_key(path)
    exact = [record for record in records if key in expected_paths(record)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        same_size = [record for record in exact if record.size == size]
        return same_size[0] if len(same_size) == 1 else None

    # Conservative fallback for path representation differences: basename and
    # exact byte size must identify one and only one torrent file.
    basename = unicodedata.normalize("NFC", path.name).casefold()
    fallback = [
        record
        for record in records
        if unicodedata.normalize("NFC", Path(record.file_name).name).casefold() == basename
        and record.size == size
    ]
    if len(fallback) == 1:
        return fallback[0]
    if len(fallback) > 1:
        return None

    # A local same-directory rename intentionally leaves qBittorrent's stored
    # path stale. Preserve the safety signal by requiring one unique record with
    # the same normalized product code and exact byte size.
    local_code = extract_product_code(path.name)
    if local_code.status != ProductCodeStatus.FOUND:
        return None
    code_matches = []
    for record in records:
        record_code = extract_product_code(Path(record.file_name).name)
        if (
            record_code.status == ProductCodeStatus.FOUND
            and record_code.normalized_code == local_code.normalized_code
            and record.size == size
        ):
            code_matches.append(record)
    return code_matches[0] if len(code_matches) == 1 else None


def collect_qbit_files(client: QBittorrentClient) -> list[QbitFileRecord]:
    records: list[QbitFileRecord] = []
    for torrent in client.get_torrents():
        for item in client.get_torrent_files(torrent.hash):
            records.append(_record(torrent, item))
    return records


def _record(torrent: Torrent, item: TorrentFile) -> QbitFileRecord:
    return QbitFileRecord(
        torrent_hash=torrent.hash,
        torrent_name=torrent.name,
        save_path=torrent.save_path,
        content_path=torrent.content_path,
        file_name=item.name,
        size=item.size,
        progress=item.progress,
    )


def scan_directory(
    source_directory: Path,
    min_video_size_mb: int,
    qbit_records: Iterable[QbitFileRecord] = (),
) -> list[ScanResult]:
    """Recursively inspect video files without modifying them."""
    threshold = min_video_size_mb * 1024 * 1024
    records = list(qbit_records)
    results: list[ScanResult] = []
    for root, directories, files in os.walk(source_directory, followlinks=False):
        directories.sort()
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < threshold:
                results.append(ScanResult(path, size, ScanStatus.SKIPPED_TOO_SMALL))
                continue
            matched = match_qbit_file(path, size, records)
            if matched is None:
                results.append(
                    ScanResult(path, size, ScanStatus.UNKNOWN_NOT_IN_QBITTORRENT)
                )
            else:
                status = (
                    ScanStatus.COMPLETE
                    if matched.progress == 1.0
                    else ScanStatus.INCOMPLETE
                )
                results.append(
                    ScanResult(
                        path=path,
                        size=size,
                        status=status,
                        torrent_hash=matched.torrent_hash,
                        torrent_file=matched.file_name,
                        progress=matched.progress,
                    )
                )
    return results
