from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.rename_operation import RenameStatus
from src.scanner import QbitFileRecord
from src.upload import (
    UploadAction,
    UploadStateStore,
    UploadStatus,
    execute_upload,
    plan_upload,
)


MIB = 1024 * 1024


def make_file(path: Path, size: int = 2 * MIB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def record(path: Path, progress: float = 1.0) -> QbitFileRecord:
    return QbitFileRecord(
        torrent_hash="hash",
        torrent_name="ABC-123",
        save_path=str(path.parent),
        content_path=str(path),
        file_name="old-site@ABC-123.mp4",
        size=path.stat().st_size,
        progress=progress,
    )


class UploadTests(unittest.TestCase):
    def environment(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        local = root / "local" / "ABC-123 Actor.mp4"
        nas = root / "nas"
        make_file(local)
        nas.mkdir()
        store = UploadStateStore(root / "state.sqlite3")
        return temporary, local, nas, store

    def plan(self, local: Path, nas: Path, progress: float = 1.0):
        return plan_upload(local, "ABC-123", nas, [record(local, progress)], 1)

    def test_no_same_code_means_upload(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            self.assertEqual(self.plan(local, nas).action, UploadAction.UPLOAD)

    def test_same_code_same_size(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            make_file(nas / "ABC-123 Older Name.mp4", local.stat().st_size)
            self.assertEqual(
                self.plan(local, nas).action, UploadAction.ALREADY_UPLOADED
            )

    def test_same_code_different_size(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            make_file(nas / "ABC-123 Older Name.mp4", MIB)
            self.assertEqual(
                self.plan(local, nas).action,
                UploadAction.NEEDS_REVIEW_SIZE_MISMATCH,
            )

    def test_multiple_same_code_files(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            make_file(nas / "ABC-123 A.mp4")
            make_file(nas / "ABC-123 B.mp4")
            self.assertEqual(
                self.plan(local, nas).action, UploadAction.NEEDS_REVIEW_DUPLICATES
            )

    def test_uploading_absent(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            plan = self.plan(local, nas)
            self.assertFalse(plan.nas_temp_path.exists())
            self.assertEqual(plan.action, UploadAction.UPLOAD)

    def test_uploading_smaller(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            make_file(nas / "ABC-123 Actor.mp4.uploading", MIB)
            self.assertEqual(self.plan(local, nas).action, UploadAction.RESTART_TEMP)

    def test_uploading_same_size(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            make_file(nas / "ABC-123 Actor.mp4.uploading", 2 * MIB)
            self.assertEqual(self.plan(local, nas).action, UploadAction.RECOVER_TEMP)

    def test_uploading_larger(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            make_file(nas / "ABC-123 Actor.mp4.uploading", 3 * MIB)
            self.assertEqual(
                self.plan(local, nas).action, UploadAction.NEEDS_REVIEW_UPLOAD_SIZE
            )

    def test_interrupted_upload_keeps_temp(self) -> None:
        temporary, local, nas, store = self.environment()
        with temporary:
            plan = self.plan(local, nas)

            def interrupted(source, destination, buffer_size, progress):
                with destination.open("xb") as handle:
                    handle.write(b"partial")
                raise OSError("simulated interruption")

            result = execute_upload(plan, store, copier=interrupted)
            self.assertEqual(result.status, UploadStatus.FAILED)
            self.assertTrue(plan.nas_temp_path.exists())
            self.assertTrue(local.exists())

    def test_copy_size_mismatch(self) -> None:
        temporary, local, nas, store = self.environment()
        with temporary:
            plan = self.plan(local, nas)

            def short_copy(source, destination, buffer_size, progress):
                make_file(destination, MIB)

            result = execute_upload(plan, store, copier=short_copy)
            self.assertEqual(result.status, UploadStatus.FAILED_SIZE_MISMATCH)
            self.assertTrue(plan.nas_temp_path.exists())

    def test_success_renames_temp_to_final_and_verifies(self) -> None:
        temporary, local, nas, store = self.environment()
        with temporary:
            plan = self.plan(local, nas)
            result = execute_upload(plan, store, buffer_size=MIB)
            self.assertEqual(result.status, UploadStatus.UPLOADED_VERIFIED)
            self.assertTrue(plan.nas_final_path.exists())
            self.assertFalse(plan.nas_temp_path.exists())
            self.assertEqual(result.local_size_after, result.nas_size_after)
            self.assertTrue(local.exists())

    def test_recover_complete_temp(self) -> None:
        temporary, local, nas, store = self.environment()
        with temporary:
            make_file(nas / "ABC-123 Actor.mp4.uploading", 2 * MIB)
            result = execute_upload(self.plan(local, nas), store)
            self.assertEqual(result.status, UploadStatus.RECOVERED_COMPLETED_UPLOAD)
            self.assertFalse((nas / "ABC-123 Actor.mp4.uploading").exists())
            self.assertTrue((nas / "ABC-123 Actor.mp4").exists())

    def test_repeated_run_does_not_upload_again(self) -> None:
        temporary, local, nas, store = self.environment()
        with temporary:
            first_plan = self.plan(local, nas)
            first = execute_upload(first_plan, store, buffer_size=MIB)
            second_plan = self.plan(local, nas)
            second = execute_upload(second_plan, store)
            self.assertEqual(first.status, UploadStatus.UPLOADED_VERIFIED)
            self.assertEqual(second_plan.action, UploadAction.ALREADY_UPLOADED)
            self.assertEqual(second.status, UploadStatus.ALREADY_UPLOADED)

    def test_sqlite_state_survives_new_store_instance(self) -> None:
        temporary, local, nas, store = self.environment()
        with temporary:
            plan = self.plan(local, nas)
            execute_upload(plan, store, buffer_size=MIB)
            reopened = UploadStateStore(store.database_path)
            row = reopened.get("ABC-123")
            self.assertIsNotNone(row)
            self.assertEqual(row["upload_status"], UploadStatus.UPLOADED_VERIFIED.value)

    def test_outside_temp_is_never_deleted(self) -> None:
        temporary, local, nas, store = self.environment()
        with temporary:
            safe_plan = self.plan(local, nas)
            outside = Path(temporary.name) / "outside" / safe_plan.nas_temp_path.name
            make_file(outside, MIB)
            malicious = replace(
                safe_plan,
                action=UploadAction.RESTART_TEMP,
                nas_temp_path=outside,
            )
            result = execute_upload(malicious, store)
            self.assertEqual(result.status, UploadStatus.FAILED)
            self.assertTrue(outside.exists())

    def test_incomplete_qbittorrent_file_is_skipped(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            self.assertEqual(self.plan(local, nas, 0.5).action, UploadAction.SKIPPED)

    def test_small_file_is_skipped(self) -> None:
        temporary, local, nas, _ = self.environment()
        with temporary:
            make_file(local, MIB - 1)
            self.assertEqual(self.plan(local, nas).action, UploadAction.SKIPPED)


if __name__ == "__main__":
    unittest.main()
