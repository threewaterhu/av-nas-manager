"""Diagnostics, preview, and explicit local rename command."""

from __future__ import annotations

import sys
from pathlib import Path

from src.config import load_config
from src.metadata import MetadataCache, MetadataService
from src.metadata.base import MetadataStatus
from src.metadata.provider_avwiki import AVWikiProvider
from src.metadata.provider_javlibrary import JavLibraryProvider
from src.nas import check_nas
from src.nas_rename import (
    NASRenameAction,
    NASRenamePlan,
    NASRenameStatus,
    build_nas_rename_plans,
    execute_nas_rename,
    reject_batch_target_collisions,
)
from src.qbittorrent_client import QBittorrentClient, QBittorrentError
from src.rename_operation import (
    RenamePlan,
    RenameStatus,
    execute_rename,
    preflight_rename,
)
from src.product_code import ProductCodeStatus, extract_product_code
from src.scanner import ScanResult, ScanStatus, collect_qbit_files, scan_directory
from src.upload import (
    UploadAction,
    UploadPlan,
    UploadStateStore,
    UploadStatus,
    execute_upload,
    plan_upload,
)


PROJECT_ROOT = Path(__file__).resolve().parent
def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"


def _metadata_service(config: object) -> MetadataService:
    cache_path = config.database_path
    if not cache_path.is_absolute():
        cache_path = PROJECT_ROOT / cache_path
    return MetadataService(
        providers=[
            AVWikiProvider(config.metadata_request_timeout_seconds),
            JavLibraryProvider(config.metadata_request_timeout_seconds),
        ],
        cache=MetadataCache(cache_path),
        request_interval_seconds=config.metadata_request_interval_seconds,
    )


def _current_complete_candidates(
    scan_results: list[ScanResult],
) -> dict[str, list[Path]]:
    """Build the ephemeral work queue exclusively from this scan's COMPLETE files."""
    candidates: dict[str, list[Path]] = {}
    for result in scan_results:
        if result.status != ScanStatus.COMPLETE:
            continue
        code = extract_product_code(result.path.name)
        if code.status != ProductCodeStatus.FOUND or not code.normalized_code:
            continue
        candidates.setdefault(code.normalized_code, []).append(result.path)
    return candidates


def _build_rename_plans(
    config: object,
    qbit_records: list[object],
    scan_results: list[ScanResult],
    metadata_service: MetadataService | None = None,
):
    candidates = _current_complete_candidates(scan_results)
    service = metadata_service or _metadata_service(config)
    items = []
    for code, paths in sorted(candidates.items()):
        paths = sorted(paths)
        metadata = service.get(code)
        if len(paths) > 1:
            plan = RenamePlan(
                code, paths[0], paths[0], RenameStatus.NEEDS_REVIEW,
                "multiple current COMPLETE videos have this product code: "
                + ", ".join(str(path) for path in paths),
            )
        else:
            plan = preflight_rename(
                paths[0],
                config.source_directory,
                metadata,
                qbit_records,
                config.min_video_size_mb,
                config.max_actresses_in_filename,
            )
        items.append((plan, metadata))
    return items


def _print_rename_plan(plan: RenamePlan, metadata: object) -> None:
    print("\n---")
    print(f"Original: {plan.original_path.name}")
    print(f"Path: {plan.original_path}")
    print(f"Code: {plan.product_code}")
    print(f"Metadata: {metadata.status.value}")
    print(f"Actresses: {', '.join(metadata.actresses) if metadata.actresses else '-'}")
    print(f"Metadata source: {metadata.source or '-'}")
    if plan.product_code == "SPJUR-001":
        print(f"Description after code removal: {plan.fallback_raw_description or '-'}")
        print(f"Cleaned description: {plan.fallback_clean_description or '-'}")
    print(f"Proposed: {plan.target_path.name}")
    print(f"Preflight: {plan.status.value}")
    print(f"Reason: {plan.reason}")


def _build_upload_plans(
    config: object,
    qbit_records: list[object],
    rename_items: list[tuple[RenamePlan, object]],
) -> list[UploadPlan]:
    """Plan uploads only for current scan items; history never creates work."""
    plans = []
    for rename_plan, _metadata in rename_items:
        code = rename_plan.product_code
        path = rename_plan.original_path
        if rename_plan.status == RenameStatus.ALREADY_STANDARDIZED:
            plans.append(
                plan_upload(
                    path, code, config.nas_target_directory,
                    qbit_records, config.min_video_size_mb,
                )
            )
            continue
        plans.append(
            UploadPlan(
                product_code=code,
                local_path=path,
                local_size=path.stat().st_size if path.is_file() else 0,
                nas_final_path=config.nas_target_directory / path.name,
                nas_temp_path=config.nas_target_directory / f"{path.name}.uploading",
                action=UploadAction.SKIPPED,
                reason="current local file must pass rename standardization before upload: "
                + rename_plan.reason,
            )
        )
    return plans


def _print_upload_plan(plan: UploadPlan) -> None:
    print("\n---")
    print(f"Local: {plan.local_path.name}")
    print(f"Code: {plan.product_code}")
    print(f"Local bytes: {plan.local_size}")
    print(f"NAS final: {plan.nas_final_path.name}")
    print(f"Same-code formal files: {len(plan.matching_final_files)}")
    for path in plan.matching_final_files:
        print(f"  Formal match: {path.name} ({path.stat().st_size} bytes)")
    print(f"Same-code uploading files: {len(plan.matching_temp_files)}")
    for path in plan.matching_temp_files:
        print(f"  Temp match: {path.name} ({path.stat().st_size} bytes)")
    print(f"Planned action: {plan.action.value}")
    print(f"Reason: {plan.reason}")


def _print_nas_rename_plan(plan: NASRenamePlan) -> None:
    print("\n---")
    print(f"Current filename: {plan.current_path.name}")
    print(f"Current bytes: {plan.current_size}")
    print(f"Product code: {plan.product_code or '-'}")
    print(f"Canonical base code: {plan.canonical_base_code or '-'}")
    print(f"Display code: {plan.display_code or '-'}")
    print(f"Part flag: {plan.part_flag or 'None'}")
    print(f"Subtitle flag: {plan.subtitle_flag or 'None'}")
    print(f"Extra token: {plan.extra_token or 'None'}")
    print(f"Recovery kind: {plan.recovery_kind or 'None'}")
    print(f"Recovery outcome: {plan.recovery_outcome.value}")
    print(f"Raw parser candidates: {', '.join(plan.raw_candidates) if plan.raw_candidates else '-'}")
    print(f"Metadata status: {plan.metadata.status.value if plan.metadata else 'NOT_QUERIED'}")
    print(f"Metadata source: {plan.metadata.source if plan.metadata and plan.metadata.source else '-'}")
    print(
        "Actresses: "
        + (", ".join(plan.metadata.actresses) if plan.metadata and plan.metadata.actresses else "-")
    )
    if plan.current_description:
        print(f"Current description: {plan.current_description}")
    print(f"Proposed filename: {plan.proposed_path.name if plan.proposed_path else '-'}")
    print(f"Status: {plan.status.value}")
    print(f"Planned action: {plan.action.value}")
    print(f"Reason: {plan.reason}")
    if plan.duplicate_files:
        for path in plan.duplicate_files:
            print(f"Duplicate: {path.name} ({path.stat().st_size} bytes)")
    if plan.conflict_path:
        size = plan.conflict_path.stat().st_size if plan.conflict_path.is_file() else "not-a-file"
        print(f"Target conflict: {plan.conflict_path.name} ({size} bytes)")


def _run_nas_rename(config: object, execute: bool) -> int:
    nas = check_nas(config.nas_mount_path, config.nas_target_directory)
    print("av-nas-manager NAS rename")
    print(f"MODE: {'EXPLICIT NAS RENAME' if execute else 'NAS RENAME PREVIEW ONLY'}")
    print(f"NAS mount: {config.nas_mount_path}")
    print(f"NAS target: {config.nas_target_directory}")
    if not nas.mounted or not nas.target_exists or not nas.target_is_directory or not nas.readable:
        print("REFUSED: NAS mount and readable target directory are required")
        return 2
    if execute and not nas.writable:
        print("REFUSED: NAS target directory is not writable")
        return 2

    def show_progress(done: int, total: int) -> None:
        if done == total or done % 10 == 0:
            print(f"Planning: {done}/{total}", flush=True)

    metadata_service = _metadata_service(config)
    plans = build_nas_rename_plans(
        config.nas_target_directory,
        metadata_service,
        config.max_actresses_in_filename,
        progress=show_progress,
    )
    if execute:
        plans = reject_batch_target_collisions(plans)
    original_plans = plans
    for plan in plans:
        _print_nas_rename_plan(plan)

    if execute:
        print("\nNAS RENAME RESULTS")
        results = []
        for plan in plans:
            result = execute_nas_rename(
                plan,
                config.nas_mount_path,
                config.nas_target_directory,
                metadata_service,
                config.max_actresses_in_filename,
            )
            results.append(result)
            if plan.action == NASRenameAction.RENAME:
                print(
                    f"{result.status.value}: {plan.current_path.name} -> "
                    f"{plan.proposed_path.name if plan.proposed_path else '-'} "
                    f"({result.reason})"
                )
        plans = results

    def count_status(status: NASRenameStatus) -> int:
        return sum(plan.status == status for plan in original_plans)

    needs_review = sum(
        plan.action == NASRenameAction.NEEDS_REVIEW for plan in original_plans
    )
    print("\nNAS RENAME SUMMARY")
    print(f"Scanned videos: {len(original_plans)}")
    print(f"Already standardized: {count_status(NASRenameStatus.ALREADY_STANDARDIZED)}")
    print(f"Suggested rename: {sum(plan.action == NASRenameAction.RENAME for plan in original_plans)}")
    print(
        "Rename metadata found: "
        + str(sum(
            plan.action == NASRenameAction.RENAME
            and plan.metadata is not None
            and plan.metadata.status in {MetadataStatus.FOUND, MetadataStatus.MANUAL_CONFIRMED}
            for plan in original_plans
        ))
    )
    print(f"Rename non-actress suffix: {count_status(NASRenameStatus.RENAME_NON_ACTRESS_SUFFIX)}")
    print(
        "Rename authoritative metadata: "
        + str(count_status(NASRenameStatus.RENAME_AUTHORITATIVE_METADATA))
    )
    print(
        "Rename normalized code: "
        + str(sum(
            plan.action == NASRenameAction.RENAME and plan.normalized_code_changed
            for plan in original_plans
        ))
    )
    print(
        "Rename subtitle suffix corrected: "
        + str(sum(
            plan.action == NASRenameAction.RENAME and plan.subtitle_flag is not None
            for plan in original_plans
        ))
    )
    print(
        "Metadata not found: "
        + str(sum(plan.metadata is not None and plan.metadata.status.value == "NOT_FOUND" for plan in original_plans))
    )
    print(
        "Metadata not found fallback: "
        + str(sum(
            plan.status == NASRenameStatus.METADATA_NOT_FOUND
            and plan.action == NASRenameAction.RENAME
            for plan in original_plans
        ))
    )
    print(f"Product code not found: {count_status(NASRenameStatus.PRODUCT_CODE_NOT_FOUND)}")
    print(f"No code in filename: {count_status(NASRenameStatus.NO_CODE_IN_FILENAME)}")
    print(
        "Recovered code metadata found: "
        + str(sum(
            plan.recovery_outcome.value == "RECOVERED_CODE_METADATA_FOUND"
            for plan in original_plans
        ))
    )
    print(
        "Recovered code metadata not found: "
        + str(count_status(NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND))
    )
    print(f"Needs review multipart: {count_status(NASRenameStatus.NEEDS_REVIEW_MULTI_PART)}")
    print(f"Ambiguous product code: {count_status(NASRenameStatus.AMBIGUOUS_PRODUCT_CODE)}")
    print(f"Duplicate code files: {count_status(NASRenameStatus.NEEDS_REVIEW_DUPLICATE_CODE)}")
    print(f"Metadata conflicts: {count_status(NASRenameStatus.NEEDS_REVIEW_METADATA_CONFLICT)}")
    print(f"Suffix ambiguity: {count_status(NASRenameStatus.NEEDS_REVIEW_SUFFIX_AMBIGUITY)}")
    print(f"Information loss review: {count_status(NASRenameStatus.NEEDS_REVIEW_INFORMATION_LOSS)}")
    print(f"Target exists: {count_status(NASRenameStatus.NEEDS_REVIEW_TARGET_EXISTS)}")
    print(f"Needs review: {needs_review}")
    if execute:
        print(f"RENAMED_OK: {sum(plan.status == NASRenameStatus.RENAMED_OK for plan in plans)}")
        print(f"SKIPPED_STATE_CHANGED: {sum(plan.status == NASRenameStatus.SKIPPED_STATE_CHANGED for plan in plans)}")
        print(f"FAILED: {sum(plan.status == NASRenameStatus.FAILED for plan in plans)}")
        renamed = [plan for plan in plans if plan.status == NASRenameStatus.RENAMED_OK]
        print(
            "RENAMED_SIZE_VERIFIED: "
            + f"{sum(plan.size_after == plan.current_size for plan in renamed)}/{len(renamed)}"
        )
        return 0 if not any(plan.status == NASRenameStatus.FAILED for plan in plans) else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ["rename"], ["upload"], ["nas-rename-preview"], ["nas-rename"]):
        print("Usage: python main.py [rename|upload|nas-rename-preview|nas-rename]")
        return 2
    config = load_config(PROJECT_ROOT / "config.yaml")
    if argv == ["nas-rename-preview"]:
        return _run_nas_rename(config, execute=False)
    if argv == ["nas-rename"]:
        return _run_nas_rename(config, execute=True)
    rename_mode = argv == ["rename"]
    upload_mode = argv == ["upload"]
    print("av-nas-manager phase 5")
    mode = "EXPLICIT LOCAL RENAME" if rename_mode else (
        "EXPLICIT NAS UPLOAD" if upload_mode else "PREVIEW ONLY"
    )
    print(f"MODE: {mode}")
    print(f"DRY RUN: {_yes_no(config.dry_run)}")

    qbit_records = []
    qbit_status = "CONNECTED"
    qbit_error = ""
    try:
        client = QBittorrentClient(config.qbittorrent)
        qbit_records = collect_qbit_files(client)
    except QBittorrentError as exc:
        qbit_status = "UNAVAILABLE"
        qbit_error = str(exc)

    nas = check_nas(config.nas_mount_path, config.nas_target_directory)
    results = scan_directory(
        config.source_directory,
        config.min_video_size_mb,
        qbit_records,
    )

    for result in results:
        size_mb = result.size / (1024 * 1024)
        progress = "" if result.progress is None else f" progress={result.progress:.6f}"
        print(f"{result.status.value:28} {size_mb:10.1f} MiB  {result.path}{progress}")

    eligible = [r for r in results if r.status != ScanStatus.SKIPPED_TOO_SMALL]
    complete = sum(r.status == ScanStatus.COMPLETE for r in eligible)
    incomplete = sum(r.status == ScanStatus.INCOMPLETE for r in eligible)
    unknown = sum(
        r.status == ScanStatus.UNKNOWN_NOT_IN_QBITTORRENT for r in eligible
    )

    print("\nDIAGNOSTIC SUMMARY")
    print(f"qBittorrent API: {qbit_status}")
    print(f"qBittorrent endpoint: http://{config.qbittorrent.host}:{config.qbittorrent.port}")
    if qbit_error:
        print(f"qBittorrent error: {qbit_error}")
    print(f"qBittorrent files indexed: {len(qbit_records)}")
    print(f"NAS mounted volume: {_yes_no(nas.mounted)}")
    print(f"NAS target exists: {_yes_no(nas.target_exists and nas.target_is_directory)}")
    print(f"NAS target readable: {_yes_no(nas.readable)}")
    print(f"NAS target writable (permission check only): {_yes_no(nas.writable)}")
    print(f"NAS ready: {_yes_no(nas.ready)}")
    print(f"Candidate videos: {len(results)}")
    print(f">={config.min_video_size_mb} MiB: {len(eligible)}")
    print(f"COMPLETE: {complete}")
    print(f"INCOMPLETE: {incomplete}")
    print(f"UNKNOWN_NOT_IN_QBITTORRENT: {unknown}")

    print("\nMetadata / Rename Preview")
    if not rename_mode and not upload_mode and not config.dry_run:
        print("REFUSED: ordinary preview requires dry_run: true")
        return 2
    plan_items = _build_rename_plans(config, qbit_records, results)
    for plan, metadata in plan_items:
        _print_rename_plan(plan, metadata)

    if rename_mode:
        print("\nLOCAL RENAME RESULTS")
        outcomes = []
        for plan, _metadata in plan_items:
            outcome = execute_rename(plan)
            outcomes.append(outcome)
            print(
                f"{outcome.status.value}: {outcome.original_path.name}"
                f" -> {outcome.target_path.name} ({outcome.reason})"
            )
            if outcome.status == RenameStatus.RENAMED_OK:
                print(
                    f"  verified size: {outcome.size_before} == {outcome.size_after}; "
                    f"extension: {outcome.target_path.suffix}"
                )
        blocked = sum(
            outcome.status in {RenameStatus.SKIPPED, RenameStatus.NEEDS_REVIEW, RenameStatus.FAILED}
            for outcome in outcomes
        )
        renamed = sum(outcome.status == RenameStatus.RENAMED_OK for outcome in outcomes)
        standardized = sum(
            outcome.status == RenameStatus.ALREADY_STANDARDIZED for outcome in outcomes
        )
        print(
            f"RENAME SUMMARY: RENAMED_OK={renamed} "
            f"ALREADY_STANDARDIZED={standardized} BLOCKED={blocked}"
        )
        return 0 if blocked == 0 else 1

    print("\nNAS Upload Preview")
    upload_plans = _build_upload_plans(config, qbit_records, plan_items)
    for plan in upload_plans:
        _print_upload_plan(plan)
    if not upload_mode:
        return 0 if qbit_status == "CONNECTED" and nas.ready else 1
    if config.verification_mode != "size":
        print(f"REFUSED: unsupported verification_mode={config.verification_mode!r}")
        return 2
    if qbit_status != "CONNECTED" or not nas.ready:
        print("REFUSED: qBittorrent and NAS must both be ready")
        return 2

    cache_path = config.database_path
    if not cache_path.is_absolute():
        cache_path = PROJECT_ROOT / cache_path
    state_store = UploadStateStore(cache_path)
    print("\nNAS UPLOAD RESULTS")
    upload_results = []
    for plan in upload_plans:
        last_bucket = [-1]

        def progress(copied: int, total: int, code: str = plan.product_code) -> None:
            percent = 100.0 if total == 0 else copied * 100.0 / total
            bucket = int(percent // 5)
            if bucket > last_bucket[0] or copied == total:
                last_bucket[0] = bucket
                print(
                    f"PROGRESS {code}: {copied / (1024 * 1024):.1f} / "
                    f"{total / (1024 * 1024):.1f} MiB ({percent:.1f}%)",
                    flush=True,
                )

        result = execute_upload(
            plan,
            state_store,
            buffer_size=config.upload_buffer_size_mib * 1024 * 1024,
            progress=progress,
        )
        upload_results.append(result)
        print(
            f"{result.status.value}: {plan.local_path.name} -> "
            f"{plan.nas_final_path.name}"
            + (f" ({result.error})" if result.error else ""),
            flush=True,
        )
        if result.nas_size_after is not None:
            print(
                f"  verified bytes: {result.local_size_after} == {result.nas_size_after}",
                flush=True,
            )
    blocked_statuses = {
        UploadStatus.FAILED,
        UploadStatus.FAILED_SIZE_MISMATCH,
        UploadStatus.NEEDS_REVIEW_SIZE_MISMATCH,
        UploadStatus.NEEDS_REVIEW_DUPLICATES,
        UploadStatus.NEEDS_REVIEW_STALE_UPLOAD,
        UploadStatus.NEEDS_REVIEW_UPLOAD_SIZE,
        UploadStatus.SKIPPED,
    }
    blocked = sum(result.status in blocked_statuses for result in upload_results)
    verified = sum(result.status == UploadStatus.UPLOADED_VERIFIED for result in upload_results)
    recovered = sum(
        result.status == UploadStatus.RECOVERED_COMPLETED_UPLOAD for result in upload_results
    )
    already = sum(result.status == UploadStatus.ALREADY_UPLOADED for result in upload_results)
    print(
        f"UPLOAD SUMMARY: UPLOADED_VERIFIED={verified} RECOVERED={recovered} "
        f"ALREADY_UPLOADED={already} BLOCKED={blocked}"
    )
    return 0 if blocked == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
