import unittest

from src.product_code import (
    ProductCodeStatus,
    RecoveryKind,
    extract_historical_code_candidates,
    extract_product_code,
    extract_subtitle_code_candidates,
)


class ProductCodeTests(unittest.TestCase):
    def assert_code(self, filename: str, expected: str) -> None:
        result = extract_product_code(filename)
        self.assertEqual(result.status, ProductCodeStatus.FOUND)
        self.assertEqual(result.normalized_code, expected)
        self.assertTrue(result.original_matches)

    def test_already_normalized(self) -> None:
        self.assert_code("ABC-123.mp4", "ABC-123")

    def test_compact(self) -> None:
        self.assert_code("ABC123.mkv", "ABC-123")

    def test_underscore_and_lowercase(self) -> None:
        self.assert_code("abc_123.mp4", "ABC-123")

    def test_website_prefix_is_ignored(self) -> None:
        self.assert_code("hhd800.com@abc-123.mp4", "ABC-123")

    def test_resolution_and_codec_are_ignored(self) -> None:
        self.assert_code("[4K] ABC-123 1080p HEVC x265.mp4", "ABC-123")

    def test_leading_digits_in_prefix(self) -> None:
        self.assert_code("259LUXU-1891.mp4", "259LUXU-1891")

    def test_multiple_codes_are_ambiguous(self) -> None:
        result = extract_product_code("ABC-123 DEF_456.mp4")
        self.assertEqual(result.status, ProductCodeStatus.AMBIGUOUS_PRODUCT_CODE)
        self.assertEqual(result.original_matches, ("ABC-123", "DEF_456"))

    def test_long_ppv_code_and_short_code_are_ambiguous(self) -> None:
        result = extract_product_code("PPV-115485 FC29.mp4")
        self.assertEqual(result.status, ProductCodeStatus.AMBIGUOUS_PRODUCT_CODE)

    def test_fc2_ppv_canonical_format(self) -> None:
        self.assert_code("FC2-PPV-1154859.mp4", "FC2-PPV-1154859")
        self.assert_code("FC2PPV-3237031.mp4", "FC2-PPV-3237031")

    def test_fc2_split_recovery_has_priority_candidate(self) -> None:
        candidates = extract_historical_code_candidates("PPV-115485 FC29.mp4")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, RecoveryKind.FC2_SPLIT_RECOVERY)
        self.assertEqual(candidates[0].lookup_code, "FC2-PPV-1154859")

    def test_extra_numeric_token_is_recorded(self) -> None:
        candidate = extract_historical_code_candidates("GANA-2057 200.mp4")[0]
        self.assertEqual(candidate.kind, RecoveryKind.EXTRA_TOKEN)
        self.assertEqual(candidate.lookup_code, "GANA-2057")
        self.assertEqual(candidate.extra_token, "200")

    def test_year_like_number_can_only_be_metadata_validated_candidate(self) -> None:
        result = extract_product_code("GANA-2057.mp4")
        self.assertEqual(result.status, ProductCodeStatus.PRODUCT_CODE_NOT_FOUND)
        candidates = extract_historical_code_candidates("GANA-2057.mp4")
        self.assertEqual(candidates[0].kind, RecoveryKind.GENERIC_METADATA_VALIDATED)
        self.assertEqual(candidates[0].lookup_code, "GANA-2057")

    def test_uncen_release_suffix_candidate(self) -> None:
        candidate = extract_historical_code_candidates(
            "IPX-506UNCEN 楓カレン.mp4"
        )[0]
        self.assertEqual(candidate.kind, RecoveryKind.RELEASE_SUFFIX)
        self.assertEqual(candidate.lookup_code, "IPX-506")
        self.assertEqual(candidate.raw_code, "IPX-506UNCEN")

    def test_multipart_candidate_separates_lookup_and_display(self) -> None:
        first = extract_historical_code_candidates("HEZ-252A xxx.mp4")[0]
        second = extract_historical_code_candidates("HEZ-252B xxx.mp4")[0]
        self.assertEqual((first.lookup_code, first.display_code, first.part_flag),
                         ("HEZ-252", "HEZ-252A", "A"))
        self.assertEqual((second.lookup_code, second.display_code, second.part_flag),
                         ("HEZ-252", "HEZ-252B", "B"))

    def test_seven_digit_fc2ppv_code(self) -> None:
        self.assert_code("FC2-PPV-3237031.mp4", "FC2-PPV-3237031")

    def test_compact_known_examples(self) -> None:
        self.assert_code("SONE360.mp4", "SONE-360")
        self.assert_code("FSDSS183.mp4", "FSDSS-183")
        self.assert_code("IPX415.mp4", "IPX-415")
        self.assert_code("EBOD750.mp4", "EBOD-750")
        self.assert_code("HODV21530.mp4", "HODV-21530")

    def test_compact_ch_subtitle_candidate(self) -> None:
        candidate = extract_subtitle_code_candidates("SONE360CH.mp4")[0]
        self.assertEqual(candidate.original_code, "SONE-360CH")
        self.assertEqual(candidate.base_code, "SONE-360")
        self.assertEqual(candidate.subtitle_flag, "CH")

    def test_hyphenated_c_subtitle_candidate(self) -> None:
        candidate = extract_subtitle_code_candidates("SONE-360C.mp4")[0]
        self.assertEqual(candidate.original_code, "SONE-360C")
        self.assertEqual(candidate.base_code, "SONE-360")
        self.assertEqual(candidate.subtitle_flag, "C")

    def test_year_and_resolution_are_not_codes(self) -> None:
        result = extract_product_code("holiday 2024 1080p 4K.mp4")
        self.assertEqual(result.status, ProductCodeStatus.PRODUCT_CODE_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
