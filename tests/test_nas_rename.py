from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.metadata.base import Metadata, MetadataProvider, MetadataStatus
from src.metadata.cache import MetadataCache
from src.metadata.service import MetadataService
from src.nas_rename import (
    NASRenameAction,
    NASRenameStatus,
    build_nas_rename_plans,
    execute_nas_rename,
    reject_batch_target_collisions,
    revalidate_nas_rename_plan,
    scan_nas_videos,
)


def make_file(path: Path, size: int = 1234) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def found(code: str, *actresses: str) -> Metadata:
    return Metadata(
        code,
        actresses=tuple(actresses),
        source="test",
        status=MetadataStatus.FOUND,
    )


class FakeLookup:
    def __init__(self, values: dict[str, Metadata] | None = None) -> None:
        self.values = values or {}
        self.calls: list[str] = []

    def get(self, product_code: str) -> Metadata:
        self.calls.append(product_code)
        return self.values.get(product_code, Metadata(product_code))


class CountingProvider(MetadataProvider):
    name = "counting"

    def __init__(self, result: Metadata) -> None:
        self.result = result
        self.calls = 0

    def fetch(self, product_code: str) -> Metadata:
        self.calls += 1
        return self.result


class NASRenameTests(unittest.TestCase):
    def test_scanner_identifies_product_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "old-site@DASS356_1080p.mp4")
            videos = scan_nas_videos(root)
            self.assertEqual(len(videos), 1)
            self.assertEqual(videos[0].code_result.normalized_code, "DASS-356")

    def test_metadata_cache_hit_skips_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4")
            cache = MetadataCache(root / "cache.sqlite3")
            cache.put(found("ABC-123", "Actress"))
            provider = CountingProvider(found("ABC-123", "Wrong"))
            service = MetadataService([provider], cache, request_interval_seconds=0)
            plans = build_nas_rename_plans(root, service)
            self.assertEqual(provider.calls, 0)
            self.assertEqual(plans[0].proposed_path.name, "ABC-123 Actress.mp4")

    def test_cache_miss_queries_provider_and_caches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4")
            cache = MetadataCache(root / "cache.sqlite3")
            provider = CountingProvider(found("ABC-123", "Actress"))
            service = MetadataService([provider], cache, request_interval_seconds=0)
            build_nas_rename_plans(root, service)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(cache.get("ABC-123").actresses, ("Actress",))

    def test_already_standardized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 Actress.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.ALREADY_STANDARDIZED)
            self.assertEqual(plan.action, NASRenameAction.NO_ACTION)

    def test_plot_suffix_is_not_already_standardized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "SONE-360 被公公強姦.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"SONE-360": found("SONE-360", "瀬戸環奈")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.RENAME_NON_ACTRESS_SUFFIX)
            self.assertEqual(plan.action, NASRenameAction.RENAME)
            self.assertEqual(plan.proposed_path.name, "SONE-360 瀬戸環奈.mp4")

    def test_release_text_is_not_a_name_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 最近更新.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.RENAME_NON_ACTRESS_SUFFIX)

    def test_compact_code_is_normalized_after_metadata_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "SONE360.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"SONE-360": found("SONE-360", "瀬戸環奈")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.RENAME_NORMALIZED_CODE)
            self.assertEqual(plan.proposed_path.name, "SONE-360 瀬戸環奈.mp4")

    def test_ch_lookup_falls_back_to_verified_base_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "SONE360CH.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"SONE-360": found("SONE-360", "瀬戸環奈")})
            )[0]
            self.assertEqual(plan.product_code, "SONE-360")
            self.assertEqual(plan.subtitle_flag, "CH")
            self.assertEqual(
                plan.status, NASRenameStatus.RENAME_SUBTITLE_SUFFIX_CORRECTED
            )
            self.assertEqual(plan.proposed_path.name, "SONE-360 瀬戸環奈.mp4")

    def test_c_lookup_falls_back_to_verified_base_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "SONE-360C.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"SONE-360": found("SONE-360", "瀬戸環奈")})
            )[0]
            self.assertEqual(plan.subtitle_flag, "C")
            self.assertEqual(plan.product_code, "SONE-360")

    def test_both_suffix_and_base_metadata_found_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "SONE-360CH.mp4")
            plan = build_nas_rename_plans(
                root,
                FakeLookup(
                    {
                        "SONE-360CH": found("SONE-360CH", "Other"),
                        "SONE-360": found("SONE-360", "瀬戸環奈"),
                    }
                ),
            )[0]
            self.assertEqual(
                plan.status, NASRenameStatus.NEEDS_REVIEW_SUFFIX_AMBIGUITY
            )
            self.assertEqual(plan.action, NASRenameAction.NEEDS_REVIEW)

    def test_multiple_actresses_match_in_different_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 Actress B，Actress A.mp4")
            plan = build_nas_rename_plans(
                root,
                FakeLookup(
                    {"ABC-123": found("ABC-123", "Actress A", "Actress B")}
                ),
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.ALREADY_STANDARDIZED)

    def test_unicode_normalized_actress_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 はずき.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "はずき")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.ALREADY_STANDARDIZED)

    def test_metadata_not_found_uses_cleaned_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "old-site@ABC123_1080p.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.status, NASRenameStatus.METADATA_NOT_FOUND)
            self.assertEqual(plan.proposed_path.name, "ABC-123.mp4")
            self.assertEqual(plan.action, NASRenameAction.RENAME)

    def test_metadata_not_found_preserves_name_like_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 Some Actress.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.proposed_path.name, "ABC-123 Some Actress.mp4")
            self.assertEqual(plan.action, NASRenameAction.NO_ACTION)

    def test_metadata_not_found_preserves_chinese_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 某段剧情描述.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.proposed_path.name, "ABC-123 某段剧情描述.mp4")

    def test_metadata_not_found_preserves_japanese_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 美谷朱音.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.proposed_path.name, "ABC-123 美谷朱音.mp4")

    def test_metadata_not_found_compact_code_preserves_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC123 某演员.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.proposed_path.name, "ABC-123 某演员.mp4")
            self.assertEqual(plan.action, NASRenameAction.RENAME)

    def test_metadata_not_found_empty_suffix_can_normalize_code_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC123.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.proposed_path.name, "ABC-123.mp4")

    def test_metadata_not_found_does_not_remove_repeated_suffix_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 note ABC-123 remains.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(
                plan.proposed_path.name, "ABC-123 note ABC-123 remains.mp4"
            )

    def test_metadata_not_found_blocks_potential_information_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC123 note: keep.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(
                plan.status, NASRenameStatus.NEEDS_REVIEW_INFORMATION_LOSS
            )
            self.assertEqual(plan.action, NASRenameAction.NEEDS_REVIEW)

    def test_no_code_in_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ordinary movie.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.status, NASRenameStatus.NO_CODE_IN_FILENAME)

    def test_extra_token_requires_metadata_and_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "GANA-2057 200.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"GANA-2057": found("GANA-2057", "Actress")})
            )[0]
            self.assertEqual(plan.extra_token, "200")
            self.assertEqual(plan.product_code, "GANA-2057")
            self.assertEqual(plan.action, NASRenameAction.RENAME)
            self.assertEqual(plan.recovery_outcome.value, "RECOVERED_CODE_METADATA_FOUND")

    def test_extra_token_not_found_stays_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "GANA-2057 200.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(
                plan.status, NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND
            )
            self.assertEqual(plan.action, NASRenameAction.NEEDS_REVIEW)

    def test_year_like_code_needs_metadata_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "GANA-2057.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"GANA-2057": found("GANA-2057", "Actress")})
            )[0]
            self.assertEqual(plan.product_code, "GANA-2057")
            self.assertEqual(plan.recovery_outcome.value, "RECOVERED_CODE_METADATA_FOUND")

    def test_year_like_code_without_metadata_stays_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "GANA-2057.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(
                plan.status, NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND
            )

    def test_uncen_suffix_uses_only_verified_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "IPX-506UNCEN 楓カレン.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"IPX-506": found("IPX-506", "楓カレン")})
            )[0]
            self.assertEqual(plan.product_code, "IPX-506")
            self.assertEqual(plan.subtitle_flag, "UNCEN")
            self.assertEqual(plan.action, NASRenameAction.RENAME)

    def test_uncen_suffix_not_found_stays_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "IPX-506UNCEN 楓カレン.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(
                plan.status, NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND
            )

    def test_uncen_raw_and_base_found_is_suffix_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "IPX-506UNCEN.mp4")
            plan = build_nas_rename_plans(
                root,
                FakeLookup({
                    "IPX-506UNCEN": found("IPX-506UNCEN", "Other"),
                    "IPX-506": found("IPX-506", "楓カレン"),
                }),
            )[0]
            self.assertEqual(
                plan.status, NASRenameStatus.NEEDS_REVIEW_SUFFIX_AMBIGUITY
            )

    def test_multipart_pair_is_not_duplicate_and_preserves_parts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "HEZ-252A 被公公強姦.mp4")
            make_file(root / "HEZ-252B 被公公強姦.mp4")
            plans = build_nas_rename_plans(
                root, FakeLookup({"HEZ-252": found("HEZ-252", "Actress")})
            )
            self.assertEqual({plan.part_flag for plan in plans}, {"A", "B"})
            self.assertEqual(
                {plan.proposed_path.name for plan in plans},
                {"HEZ-252A Actress.mp4", "HEZ-252B Actress.mp4"},
            )
            self.assertTrue(all(plan.action == NASRenameAction.RENAME for plan in plans))

    def test_same_multipart_part_is_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "HEZ-252A one.mp4")
            make_file(root / "HEZ-252A two.mkv")
            plans = build_nas_rename_plans(
                root, FakeLookup({"HEZ-252": found("HEZ-252", "Actress")})
            )
            self.assertTrue(all(
                plan.status == NASRenameStatus.NEEDS_REVIEW_DUPLICATE_CODE
                for plan in plans
            ))

    def test_unverified_multipart_stays_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "HEZ-257A note.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.status, NASRenameStatus.NEEDS_REVIEW_MULTI_PART)
            self.assertEqual(plan.action, NASRenameAction.NEEDS_REVIEW)

    def test_fc2_split_is_validated_without_ordinary_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "PPV-115485 FC29.mp4")
            plan = build_nas_rename_plans(
                root,
                FakeLookup({
                    "FC2-PPV-1154859": found("FC2-PPV-1154859", "Actress")
                }),
            )[0]
            self.assertEqual(plan.product_code, "FC2-PPV-1154859")
            self.assertEqual(plan.action, NASRenameAction.RENAME)
            self.assertNotEqual(plan.status, NASRenameStatus.AMBIGUOUS_PRODUCT_CODE)

    def test_fc2_split_validation_failure_stays_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "PPV-115485 FC29 Existing Text.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(
                plan.status, NASRenameStatus.RECOVERED_CODE_METADATA_NOT_FOUND
            )
            self.assertEqual(plan.action, NASRenameAction.NEEDS_REVIEW)

    def test_ambiguous_product_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 DEF-456.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.status, NASRenameStatus.AMBIGUOUS_PRODUCT_CODE)

    def test_duplicate_code_marks_every_file_without_metadata_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4", 100)
            make_file(root / "ABC-123 old.mkv", 200)
            lookup = FakeLookup()
            plans = build_nas_rename_plans(root, lookup)
            self.assertEqual(len(plans), 2)
            self.assertTrue(
                all(plan.status == NASRenameStatus.NEEDS_REVIEW_DUPLICATE_CODE for plan in plans)
            )
            self.assertEqual(lookup.calls, [])

    def test_metadata_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 Human Name.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Provider Name")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.NEEDS_REVIEW_METADATA_CONFLICT)
            self.assertEqual(plan.current_description, "Human Name")

    def test_avwiki_found_conflict_is_authoritative_safe_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABW-207 Rukawa Yuu.mp4")
            metadata = replace(
                found("ABW-207", "流川夕"), source="av-wiki.net"
            )
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABW-207": metadata})
            )[0]
            self.assertEqual(
                plan.status, NASRenameStatus.RENAME_AUTHORITATIVE_METADATA
            )
            self.assertEqual(plan.action, NASRenameAction.RENAME)
            self.assertEqual(plan.proposed_path.name, "ABW-207 流川夕.mp4")

    def test_non_avwiki_found_conflict_still_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 Human Name.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Provider Name")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.NEEDS_REVIEW_METADATA_CONFLICT)

    def test_avwiki_found_without_actress_is_not_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123 Human Name.mp4")
            metadata = Metadata(
                "ABC-123", source="av-wiki.net", status=MetadataStatus.FOUND
            )
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": metadata})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.NEEDS_REVIEW_METADATA_CONFLICT)

    def test_authoritative_policy_does_not_remove_not_found_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC123 某演员.mp4")
            plan = build_nas_rename_plans(root, FakeLookup())[0]
            self.assertEqual(plan.metadata.status, MetadataStatus.NOT_FOUND)
            self.assertEqual(plan.proposed_path.name, "ABC-123 某演员.mp4")

    def test_batch_target_collision_skips_all_colliding_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "ABC-123 One.mp4"
            second = root / "DEF-456 Two.mp4"
            make_file(first)
            make_file(second)
            metadata = replace(found("ABC-123", "Actress"), source="av-wiki.net")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": metadata, "DEF-456": metadata})
            )
            collided_target = root / "COLLISION Actress.mp4"
            altered = [replace(item, proposed_path=collided_target) for item in plan]
            checked = reject_batch_target_collisions(altered)
            self.assertTrue(
                all(item.status == NASRenameStatus.SKIPPED_STATE_CHANGED for item in checked)
            )
            self.assertTrue(all(item.action == NASRenameAction.NO_ACTION for item in checked))

    def test_target_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4")
            (root / "ABC-123 Actress.mp4").mkdir()
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            )[0]
            self.assertEqual(plan.status, NASRenameStatus.NEEDS_REVIEW_TARGET_EXISTS)

    def test_unicode_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "美谷朱音")})
            )[0]
            self.assertEqual(plan.proposed_path.name, "ABC-123 美谷朱音.mp4")

    def test_rename_preserves_size_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ABC-123.mp4"
            make_file(source, 9876)
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            )[0]
            with patch("src.nas_rename.os.path.ismount", return_value=True):
                result = execute_nas_rename(plan, root, root)
            self.assertEqual(result.status, NASRenameStatus.RENAMED_OK)
            self.assertEqual(result.size_after, 9876)
            self.assertEqual(result.proposed_path.suffix, ".mp4")

    def test_revalidation_accepts_unchanged_safe_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4")
            lookup = FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            plan = build_nas_rename_plans(root, lookup)[0]
            fresh = revalidate_nas_rename_plan(plan, root, lookup)
            self.assertEqual(fresh.status, NASRenameStatus.READY_RENAME)
            self.assertEqual(fresh.action, NASRenameAction.RENAME)

    def test_revalidation_skips_when_source_size_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ABC-123.mp4"
            make_file(source, 100)
            lookup = FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            plan = build_nas_rename_plans(root, lookup)[0]
            with source.open("ab") as handle:
                handle.write(b"changed")
            fresh = revalidate_nas_rename_plan(plan, root, lookup)
            self.assertEqual(fresh.status, NASRenameStatus.SKIPPED_STATE_CHANGED)

    def test_revalidation_skips_when_metadata_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4")
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            )[0]
            fresh = revalidate_nas_rename_plan(
                plan, root, FakeLookup({"ABC-123": found("ABC-123", "Other")})
            )
            self.assertEqual(fresh.status, NASRenameStatus.SKIPPED_STATE_CHANGED)

    def test_revalidation_skips_when_target_appears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4")
            lookup = FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            plan = build_nas_rename_plans(root, lookup)[0]
            make_file(root / "ABC-123 Actress.mp4")
            fresh = revalidate_nas_rename_plan(plan, root, lookup)
            self.assertEqual(fresh.status, NASRenameStatus.SKIPPED_STATE_CHANGED)

    def test_execute_revalidates_before_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ABC-123.mp4"
            make_file(source, 200)
            lookup = FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            plan = build_nas_rename_plans(root, lookup)[0]
            with patch("src.nas_rename.os.path.ismount", return_value=True):
                result = execute_nas_rename(plan, root, root, lookup)
            self.assertEqual(result.status, NASRenameStatus.RENAMED_OK)
            self.assertEqual(result.size_after, 200)

    def test_ignores_uploading_nonvideo_and_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "ABC-123.mp4.uploading")
            make_file(root / "ABC-123.txt")
            make_file(root / ".ABC-123.mp4")
            self.assertEqual(scan_nas_videos(root), [])

    def test_does_not_scan_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_file(root / "nested" / "ABC-123.mp4")
            self.assertEqual(scan_nas_videos(root), [])

    def test_cannot_rename_outside_target_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ABC-123.mp4"
            make_file(source)
            plan = build_nas_rename_plans(
                root, FakeLookup({"ABC-123": found("ABC-123", "Actress")})
            )[0]
            outside = root.parent / "outside-ABC-123.mp4"
            malicious = replace(plan, proposed_path=outside)
            with patch("src.nas_rename.os.path.ismount", return_value=True):
                result = execute_nas_rename(malicious, root, root)
            self.assertEqual(result.status, NASRenameStatus.NEEDS_REVIEW)
            self.assertTrue(source.exists())
            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
