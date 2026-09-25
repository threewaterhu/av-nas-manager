from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.qbittorrent_client import Torrent, TorrentFile
from src.scanner import (
    QbitFileRecord,
    ScanStatus,
    collect_qbit_files,
    match_qbit_file,
    scan_directory,
)


def make_sparse_file(path: Path, size: int) -> None:
    with path.open("wb") as handle:
        handle.truncate(size)


def record(
    *,
    torrent_name: str = "MoviePack",
    save_path: str = "/Users/test/Downloads",
    content_path: str = "/Users/test/Downloads/MoviePack",
    file_name: str = "MoviePack/movie.mp4",
    size: int = 300 * 1024 * 1024,
    progress: float = 1.0,
) -> QbitFileRecord:
    return QbitFileRecord(
        torrent_hash="abc123",
        torrent_name=torrent_name,
        save_path=save_path,
        content_path=content_path,
        file_name=file_name,
        size=size,
        progress=progress,
    )


class PathMatchingTests(unittest.TestCase):
    def test_multi_file_torrent_path(self) -> None:
        item = record()
        matched = match_qbit_file(
            Path("/Users/test/Downloads/MoviePack/movie.mp4"), item.size, [item]
        )
        self.assertEqual(matched, item)

    def test_single_file_torrent_path(self) -> None:
        item = record(
            torrent_name="movie.mp4",
            content_path="/Users/test/Downloads/movie.mp4",
            file_name="movie.mp4",
        )
        matched = match_qbit_file(
            Path("/Users/test/Downloads/movie.mp4"), item.size, [item]
        )
        self.assertEqual(matched, item)

    def test_ambiguous_basename_fallback_is_not_matched(self) -> None:
        first = record(save_path="/elsewhere/one", content_path="/elsewhere/one")
        second = record(save_path="/elsewhere/two", content_path="/elsewhere/two")
        self.assertIsNone(
            match_qbit_file(Path("/local/movie.mp4"), first.size, [first, second])
        )

    def test_standardized_rename_matches_unique_code_and_exact_size(self) -> None:
        item = record(
            save_path="/Downloads/SNOS-339",
            content_path="/Downloads/SNOS-339/4k688.com@SNOS-339.mp4",
            file_name="4k688.com@SNOS-339.mp4",
            size=123456,
        )
        matched = match_qbit_file(
            Path("/Downloads/SNOS-339/SNOS-339 Actress.mp4"), 123456, [item]
        )
        self.assertEqual(matched, item)

    def test_code_and_size_fallback_must_be_unique(self) -> None:
        first = record(file_name="site@SNOS-339.mp4", size=123456)
        second = record(file_name="other@SNOS-339.mkv", size=123456)
        self.assertIsNone(
            match_qbit_file(
                Path("/Downloads/SNOS-339/SNOS-339 Actress.mp4"),
                123456,
                [first, second],
            )
        )


class FakeClient:
    def get_torrents(self) -> list[Torrent]:
        return [
            Torrent("multi", "Pack", "/Downloads", "/Downloads/Pack"),
            Torrent("single", "solo.mp4", "/Downloads", "/Downloads/solo.mp4"),
        ]

    def get_torrent_files(self, torrent_hash: str) -> list[TorrentFile]:
        if torrent_hash == "multi":
            return [
                TorrentFile("Pack/a.mp4", 10, 1.0),
                TorrentFile("Pack/b.mp4", 20, 0.5),
            ]
        return [TorrentFile("solo.mp4", 30, 1.0)]


class TorrentCollectionTests(unittest.TestCase):
    def test_collects_files_from_multi_and_single_file_torrents(self) -> None:
        items = collect_qbit_files(FakeClient())  # type: ignore[arg-type]
        self.assertEqual(len(items), 3)
        self.assertEqual([item.progress for item in items], [1.0, 0.5, 1.0])


class ScannerTests(unittest.TestCase):
    def test_200_mb_threshold_is_inclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            below = root / "below.mp4"
            exact = root / "exact.mkv"
            below_size = 200 * 1024 * 1024 - 1
            exact_size = 200 * 1024 * 1024
            make_sparse_file(below, below_size)
            make_sparse_file(exact, exact_size)

            results = scan_directory(root, 200)
            statuses = {item.path.name: item.status for item in results}
            self.assertEqual(statuses["below.mp4"], ScanStatus.SKIPPED_TOO_SMALL)
            self.assertEqual(
                statuses["exact.mkv"], ScanStatus.UNKNOWN_NOT_IN_QBITTORRENT
            )

    def test_progress_must_equal_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            complete_path = root / "complete.mp4"
            incomplete_path = root / "incomplete.mp4"
            size = 201 * 1024 * 1024
            make_sparse_file(complete_path, size)
            make_sparse_file(incomplete_path, size)
            records = [
                record(
                    torrent_name="complete.mp4",
                    save_path=str(root),
                    content_path=str(complete_path),
                    file_name="complete.mp4",
                    size=size,
                    progress=1.0,
                ),
                record(
                    torrent_name="incomplete.mp4",
                    save_path=str(root),
                    content_path=str(incomplete_path),
                    file_name="incomplete.mp4",
                    size=size,
                    progress=0.999999,
                ),
            ]
            results = scan_directory(root, 200, records)
            statuses = {item.path.name: item.status for item in results}
            self.assertEqual(statuses["complete.mp4"], ScanStatus.COMPLETE)
            self.assertEqual(statuses["incomplete.mp4"], ScanStatus.INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
