import unittest
from pathlib import Path

from src.renamer import propose_filename, sanitize_component


class RenamerTests(unittest.TestCase):
    def test_single_actress(self) -> None:
        proposed = propose_filename(
            Path("ABC-123 1080p.mp4"), "ABC-123", ("Actress Name",)
        )
        self.assertEqual(proposed, "ABC-123 Actress Name.mp4")

    def test_multiple_actresses_honors_limit(self) -> None:
        proposed = propose_filename(
            Path("ABC-123.mkv"), "ABC-123", ("A", "B", "C", "D"), 3
        )
        self.assertEqual(proposed, "ABC-123 A, B, C.mkv")

    def test_no_actress_uses_cleaned_description(self) -> None:
        proposed = propose_filename(
            Path("site.com@ABC_123 useful description 1080p x265.mp4"), "ABC-123"
        )
        self.assertEqual(proposed, "ABC-123 useful description.mp4")

    def test_illegal_characters_are_removed(self) -> None:
        self.assertEqual(sanitize_component(' A/B:C*D?E"F<G>H|I\x00 '), "A B C D E F G H I")


if __name__ == "__main__":
    unittest.main()
