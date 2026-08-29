import unittest

from src.product_code import ProductCodeStatus, extract_product_code


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

    def test_year_and_resolution_are_not_codes(self) -> None:
        result = extract_product_code("holiday 2024 1080p 4K.mp4")
        self.assertEqual(result.status, ProductCodeStatus.PRODUCT_CODE_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
