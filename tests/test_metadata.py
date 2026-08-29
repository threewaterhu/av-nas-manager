from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.metadata.base import Metadata, MetadataProvider, MetadataStatus
from src.metadata.cache import MetadataCache
from src.metadata.html_parser import PublicPageParser
from src.metadata.service import MetadataService


class CountingProvider(MetadataProvider):
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, product_code: str) -> Metadata:
        self.calls += 1
        return Metadata(
            product_code,
            title="Title",
            actresses=("Name",),
            source=self.name,
            status=MetadataStatus.FOUND,
        )


class MetadataTests(unittest.TestCase):
    def test_cache_hit_skips_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = MetadataCache(Path(directory) / "cache.sqlite3")
            provider = CountingProvider()
            service = MetadataService([provider], cache, request_interval_seconds=0)
            first = service.get("ABC-123")
            second = service.get("ABC-123")
            self.assertEqual(first.actresses, ("Name",))
            self.assertTrue(second.from_cache)
            self.assertEqual(provider.calls, 1)

    def test_avwiki_parser_supports_multiple_actresses(self) -> None:
        parser = PublicPageParser()
        parser.feed(
            '<meta property="og:title" content="Title">'
            '<a href="https://av-wiki.net/av-actress/a/" rel="tag">A</a>'
            '<a href="https://av-wiki.net/av-actress/b/" rel="tag">B</a>'
            '<a href="https://av-wiki.net/av-actress/related/">Ignore</a>'
        )
        self.assertEqual(parser.og_title, "Title")
        self.assertEqual(parser.actresses, ["A", "B"])


if __name__ == "__main__":
    unittest.main()
