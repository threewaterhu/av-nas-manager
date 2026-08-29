from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.metadata.base import Metadata, MetadataStatus
from src.rename_operation import RenameStatus, execute_rename, preflight_rename
from src.scanner import QbitFileRecord


MIB = 1024 * 1024


def make_file(path: Path, size: int = 2 * MIB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def metadata(
    code: str = "ABC-123", actresses: tuple[str, ...] = ("Actor",)
) -> Metadata:
    return Metadata(
        code,
        actresses=actresses,
        source="test",
        status=MetadataStatus.FOUND if actresses else MetadataStatus.NOT_FOUND,
    )


def qbit_record(path: Path, progress: float = 1.0) -> QbitFileRecord:
    return QbitFileRecord(
        torrent_hash="hash",
        torrent_name=path.name,
        save_path=str(path.parent),
        content_path=str(path),
        file_name=path.name,
        size=path.stat().st_size,
        progress=progress,
    )


class RenameOperationTests(unittest.TestCase):
    def ready_plan(
        self,
        root: Path,
        name: str = "site.com@ABC-123.mp4",
        item_metadata: Metadata | None = None,
    ):
        path = root / name
        make_file(path)
        item_metadata = item_metadata or metadata()
        return preflight_rename(
            path, root, item_metadata, [qbit_record(path)], min_video_size_mb=1
        )

    def test_single_actress_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.ready_plan(root)
            outcome = execute_rename(plan)
            self.assertEqual(outcome.status, RenameStatus.RENAMED_OK)
            self.assertEqual(outcome.target_path.name, "ABC-123 Actor.mp4")

    def test_multiple_actress_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.ready_plan(root, item_metadata=metadata(actresses=("A", "B")))
            outcome = execute_rename(plan)
            self.assertEqual(outcome.target_path.name, "ABC-123 A, B.mp4")

    def test_metadata_not_found_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.ready_plan(
                root,
                name="site.com@ABC-123 useful 1080p x265.mp4",
                item_metadata=metadata(actresses=()),
            )
            self.assertEqual(plan.target_path.name, "ABC-123 useful.mp4")
            self.assertEqual(plan.status, RenameStatus.READY)

    def test_fallback_description_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.ready_plan(
                root,
                name="site.com@ABC_123_[4K]__useful-title_HEVC.mp4",
                item_metadata=metadata(actresses=()),
            )
            self.assertEqual(plan.fallback_clean_description, "useful title")

    def test_empty_fallback_becomes_code_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.ready_plan(
                root,
                name="site.com@ABC-123_4K_UNC.mp4",
                item_metadata=metadata(actresses=()),
            )
            self.assertEqual(plan.fallback_clean_description, "")
            self.assertEqual(plan.target_path.name, "ABC-123.mp4")
            self.assertEqual(plan.status, RenameStatus.READY)

    def test_target_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "site.com@ABC-123.mp4"
            make_file(source)
            make_file(root / "ABC-123 Actor.mp4")
            plan = preflight_rename(
                source, root, metadata(), [qbit_record(source)], min_video_size_mb=1
            )
            self.assertEqual(plan.status, RenameStatus.SKIPPED)

    def test_source_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = preflight_rename(
                root / "ABC-123.mp4", root, metadata(), [], min_video_size_mb=1
            )
            self.assertEqual(plan.status, RenameStatus.SKIPPED)

    def test_incomplete_qbittorrent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "site.com@ABC-123.mp4"
            make_file(source)
            plan = preflight_rename(
                source, root, metadata(), [qbit_record(source, 0.9)], min_video_size_mb=1
            )
            self.assertEqual(plan.status, RenameStatus.SKIPPED)
            self.assertEqual(plan.qbit_progress, 0.9)

    def test_qbittorrent_match_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "site.com@ABC-123.mp4"
            make_file(source)
            plan = preflight_rename(source, root, metadata(), [], min_video_size_mb=1)
            self.assertEqual(plan.status, RenameStatus.SKIPPED)

    def test_below_minimum_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "site.com@ABC-123.mp4"
            make_file(source, MIB - 1)
            plan = preflight_rename(
                source, root, metadata(), [qbit_record(source)], min_video_size_mb=1
            )
            self.assertEqual(plan.status, RenameStatus.SKIPPED)

    def test_unicode_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.ready_plan(root, item_metadata=metadata(actresses=("美谷朱音",)))
            outcome = execute_rename(plan)
            self.assertEqual(outcome.target_path.name, "ABC-123 美谷朱音.mp4")
            self.assertTrue(outcome.target_path.exists())

    def test_case_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "site.com@ABC-123.mp4"
            make_file(source)
            make_file(root / "abc-123 actor.mp4")
            plan = preflight_rename(
                source, root, metadata(), [qbit_record(source)], min_video_size_mb=1
            )
            self.assertEqual(plan.status, RenameStatus.NEEDS_REVIEW)

    def test_original_extension_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.ready_plan(Path(directory), name="site.com@ABC-123.MKV")
            outcome = execute_rename(plan)
            self.assertEqual(outcome.target_path.suffix, ".MKV")

    def test_size_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.ready_plan(Path(directory))
            outcome = execute_rename(plan)
            self.assertEqual(outcome.size_before, outcome.size_after)

    def test_already_standardized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ABC-123 Actor.mp4"
            make_file(source)
            plan = preflight_rename(source, root, metadata(), [], min_video_size_mb=1)
            self.assertEqual(plan.status, RenameStatus.ALREADY_STANDARDIZED)

    def test_second_run_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = execute_rename(self.ready_plan(root))
            second = preflight_rename(
                first.target_path, root, metadata(), [], min_video_size_mb=1
            )
            self.assertEqual(first.status, RenameStatus.RENAMED_OK)
            self.assertEqual(second.status, RenameStatus.ALREADY_STANDARDIZED)


if __name__ == "__main__":
    unittest.main()
