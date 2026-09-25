from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from main import _build_rename_plans, _build_upload_plans
from src.metadata.base import Metadata, MetadataStatus
from src.metadata.cache import MetadataCache
from src.metadata.service import MetadataService
from src.rename_operation import RenameStatus, execute_rename
from src.scanner import QbitFileRecord, scan_directory
from src.upload import UploadAction, UploadPlan, UploadStateStore, UploadStatus


MIB = 1024 * 1024


def make_file(path: Path, size: int = 2 * MIB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def qbit_record(path: Path, stored_name: str | None = None) -> QbitFileRecord:
    name = stored_name or path.name
    return QbitFileRecord(
        torrent_hash="hash-" + name,
        torrent_name=Path(name).stem,
        save_path=str(path.parent),
        content_path=str(path.parent / name),
        file_name=name,
        size=path.stat().st_size,
        progress=1.0,
    )


def found(code: str, actress: str) -> Metadata:
    return Metadata(
        code,
        actresses=(actress,),
        source="test",
        status=MetadataStatus.FOUND,
    )


class FakeMetadataService:
    def __init__(self, values: dict[str, Metadata]) -> None:
        self.values = values
        self.calls: list[str] = []

    def get(self, code: str) -> Metadata:
        self.calls.append(code)
        return self.values.get(code, Metadata(code))


class FailingProvider:
    name = "failing"

    def fetch(self, product_code: str) -> Metadata:
        raise AssertionError(f"unexpected provider query for {product_code}")


def config(root: Path) -> SimpleNamespace:
    nas = root / "nas"
    nas.mkdir(exist_ok=True)
    return SimpleNamespace(
        source_directory=root / "Downloads",
        nas_target_directory=nas,
        min_video_size_mb=1,
        max_actresses_in_filename=3,
    )


class CurrentPipelineTests(unittest.TestCase):
    def test_deleted_history_never_creates_missing_current_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_config = config(root)
            current = app_config.source_directory / "D" / "site@DDD-004.mp4"
            make_file(current)
            records = [qbit_record(current)]
            results = scan_directory(app_config.source_directory, 1, records)
            service = FakeMetadataService({"DDD-004": found("DDD-004", "Actress D")})

            rename_items = _build_rename_plans(
                app_config, records, results, service  # type: ignore[arg-type]
            )
            upload_plans = _build_upload_plans(app_config, records, rename_items)

            self.assertEqual([item[0].product_code for item in rename_items], ["DDD-004"])
            self.assertEqual([plan.product_code for plan in upload_plans], ["DDD-004"])
            self.assertNotIn("<missing-", str(rename_items) + str(upload_plans))

    def test_metadata_cache_history_does_not_create_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_config = config(root)
            current = app_config.source_directory / "DDD-004.mp4"
            make_file(current)
            records = [qbit_record(current)]
            cache = MetadataCache(root / "metadata.sqlite3")
            for code in ("AAA-001", "BBB-002", "CCC-003"):
                cache.put(found(code, "Historical"))
            cache.put(found("DDD-004", "Current"))
            service = MetadataService([FailingProvider()], cache, 0)

            items = _build_rename_plans(
                app_config,
                records,
                scan_directory(app_config.source_directory, 1, records),
                service,
            )
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0][0].product_code, "DDD-004")

    def test_uploaded_verified_history_does_not_create_upload_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_config = config(root)
            store = UploadStateStore(root / "state.sqlite3")
            for code in ("AAA-001", "BBB-002", "CCC-003"):
                historical = UploadPlan(
                    code,
                    app_config.source_directory / f"{code}.mp4",
                    123,
                    app_config.nas_target_directory / f"{code}.mp4",
                    app_config.nas_target_directory / f"{code}.mp4.uploading",
                    UploadAction.UPLOAD,
                    "historical",
                )
                store.record(historical, UploadStatus.UPLOADED_VERIFIED)

            current = app_config.source_directory / "DDD-004 Current.mp4"
            make_file(current)
            records = [qbit_record(current)]
            service = FakeMetadataService({"DDD-004": found("DDD-004", "Current")})
            items = _build_rename_plans(
                app_config,
                records,
                scan_directory(app_config.source_directory, 1, records),
                service,  # type: ignore[arg-type]
            )
            plans = _build_upload_plans(app_config, records, items)

            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].product_code, "DDD-004")
            self.assertIsNotNone(store.get("AAA-001"))

    def test_cleanup_then_new_batch_runs_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_config = config(root)

            old_path = app_config.source_directory / "AAA-001 Actor A.mp4"
            make_file(old_path)
            old_path.unlink()  # Simulate the user's normal post-upload cleanup.

            raw = app_config.source_directory / "batch-b" / "site@BBB-002.mp4"
            make_file(raw)
            stored_name = raw.name
            records = [qbit_record(raw, stored_name)]
            service = FakeMetadataService({"BBB-002": found("BBB-002", "Actor B")})

            first_scan = scan_directory(app_config.source_directory, 1, records)
            first_items = _build_rename_plans(
                app_config, records, first_scan, service  # type: ignore[arg-type]
            )
            self.assertEqual(len(first_items), 1)
            self.assertEqual(first_items[0][0].status, RenameStatus.READY)
            renamed = execute_rename(first_items[0][0])
            self.assertEqual(renamed.status, RenameStatus.RENAMED_OK)

            second_scan = scan_directory(app_config.source_directory, 1, records)
            second_items = _build_rename_plans(
                app_config, records, second_scan, service  # type: ignore[arg-type]
            )
            self.assertEqual(len(second_items), 1)
            self.assertEqual(second_items[0][0].status, RenameStatus.ALREADY_STANDARDIZED)
            upload_plans = _build_upload_plans(app_config, records, second_items)
            self.assertEqual(len(upload_plans), 1)
            self.assertEqual(upload_plans[0].product_code, "BBB-002")
            self.assertEqual(upload_plans[0].action, UploadAction.UPLOAD)


if __name__ == "__main__":
    unittest.main()
