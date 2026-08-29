"""Conservative JavLibrary fallback; no login or challenge bypass."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request

from .base import Metadata, MetadataProvider, MetadataStatus, verified_ssl_context


class JavLibraryProvider(MetadataProvider):
    name = "JavLibrary"
    _CAST_LINK = re.compile(
        r'(?is)<a[^>]+href=["\'][^"\']*vl_star\.php[^"\']*["\'][^>]*>(.*?)</a>'
    )
    _TAGS = re.compile(r"<[^>]+>")
    _TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self.ssl_context = verified_ssl_context()

    def fetch(self, product_code: str) -> Metadata:
        query = urllib.parse.urlencode({"keyword": product_code})
        url = f"https://www.javlibrary.com/en/vl_searchbyid.php?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "av-nas-manager/0.1 (personal metadata preview; low frequency)"
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self.ssl_context
            ) as response:
                body = response.read(512 * 1024).decode("utf-8", errors="replace")
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return Metadata(product_code, source=self.name, source_url=url)
            return Metadata(
                product_code, source=self.name, source_url=url,
                status=MetadataStatus.ERROR, error=f"HTTP {exc.code}"
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            return Metadata(
                product_code, source=self.name, source_url=url,
                status=MetadataStatus.ERROR, error=f"Network error: {exc}"
            )

        names = []
        for raw in self._CAST_LINK.findall(body):
            name = " ".join(html.unescape(self._TAGS.sub("", raw)).split())
            if name and name not in names:
                names.append(name)
        title_match = self._TITLE.search(body)
        title = "" if not title_match else " ".join(
            html.unescape(self._TAGS.sub("", title_match.group(1))).split()
        )
        if product_code.casefold() not in body.casefold() or not names:
            return Metadata(product_code, title=title, source=self.name, source_url=final_url)
        return Metadata(
            product_code=product_code,
            title=title,
            actresses=tuple(names),
            source=self.name,
            source_url=final_url,
            status=MetadataStatus.FOUND,
        )
