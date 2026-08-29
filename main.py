"""Diagnostics, preview, and explicit local rename command."""

from __future__ import annotations

import sys
from pathlib import Path

from src.config import load_config
from src.metadata import MetadataCache, MetadataService
from src.metadata.provider_avwiki import AVWikiProvider
from src.metadata.provider_javlibrary import JavLibraryProvider
from src.nas import check_nas
from src.qbittorrent_client import QBittorrentClient, QBittorrentError
from src.rename_operation import (
    RenamePlan,
    RenameStatus,
    discover_approved_candidates,
    execute_rename,
    preflight_rename,
)
from src.scanner import ScanStatus, collect_qbit_files, scan_directory
from src.upload import (
    UploadAction,
    UploadPlan,
    UploadStateStore,
    UploadStatus,
    execute_upload,
    plan_upload,
)


PROJECT_ROOT = Path(__file__).resolve().parent
APPROVED_CODES = (
    "259LUXU-1891",
    "DASS-356",
    "JUR-092",
    "KING-296",
    "KING-298",
    "MFYD-165",
    "MRSS-190",
    "SIMW-010",
    "SNOS-275",
    "SNOS-353",
    "SPJUR-001",
    "START-511",
    "START-603",
)


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


def _build_rename_plans(config: object, qbit_records: list[object]):
    candidates = discover_approved_candidates(
        config.source_directory, set(APPROVED_CODES)
    )
    service = _metadata_service(config)
    items = []
    for code in APPROVED_CODES:
        paths = candidates[code]
        metadata = service.get(code)
        if len(paths) == 0:
            missing = config.source_directory / f"<missing-{code}>"
            plan = RenamePlan(
                code, missing, missing, RenameStatus.SKIPPED,
                "no local video with this approved code was found",
            )
        elif len(paths) > 1:
            plan = RenamePlan(
                code, paths[0], paths[0], RenameStatus.NEEDS_REVIEW,
                "multiple local videos have this approved code: "
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


def _build_upload_plans(config: object, qbit_records: list[object]) -> list[UploadPlan]:
    candidates = discover_approved_candidates(
        config.source_directory, set(APPROVED_CODES)
    )
    plans = []
    for code in APPROVED_CODES:
        paths = candidates[code]
        if len(paths) == 1:
            plans.append(
                plan_upload(
                    paths[0], code, config.nas_target_directory,
                    qbit_records, config.min_video_size_mb,
                )
            )
            continue
        placeholder = paths[0] if paths else config.source_directory / f"<missing-{code}>"
        plans.append(
            UploadPlan(
                product_code=code,
                local_path=placeholder,
                local_size=placeholder.stat().st_size if placeholder.is_file() else 0,
                nas_final_path=config.nas_target_directory / placeholder.name,
                nas_temp_path=config.nas_target_directory / f"{placeholder.name}.uploading",
                action=UploadAction.SKIPPED,
                reason=(
                    "no approved local source was found"
                    if not paths
                    else "multiple local files have this approved code"
                ),
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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ["rename"], ["upload"]):
        print("Usage: python main.py [rename|upload]")
        return 2
    rename_mode = argv == ["rename"]
    upload_mode = argv == ["upload"]
    config = load_config(PROJECT_ROOT / "config.yaml")
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
    plan_items = _build_rename_plans(config, qbit_records)
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
    upload_plans = _build_upload_plans(config, qbit_records)
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
