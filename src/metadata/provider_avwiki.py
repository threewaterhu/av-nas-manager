"""Public AV-Wiki page provider."""

from __future__ import annotations

import urllib.error
import urllib.request

from .base import Metadata, MetadataProvider, MetadataStatus, verified_ssl_context
from .html_parser import PublicPageParser


class AVWikiProvider(MetadataProvider):
    name = "av-wiki.net"

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self.ssl_context = verified_ssl_context()

    def fetch(self, product_code: str) -> Metadata:
        url = f"https://av-wiki.net/{product_code.lower()}/"
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

        parser = PublicPageParser()
        parser.feed(body)
        title = parser.og_title or parser.page_title
        code_present = product_code.casefold() in body.casefold()
        if not code_present or "ページが見つかりません" in parser.page_title:
            return Metadata(product_code, source=self.name, source_url=url)
        if not parser.actresses:
            return Metadata(
                product_code=product_code,
                title=title,
                source=self.name,
                source_url=url,
                status=MetadataStatus.NOT_FOUND,
                error="Page found but no actress tags were present",
            )
        return Metadata(
            product_code=product_code,
            title=title,
            actresses=tuple(parser.actresses),
            source=self.name,
            source_url=url,
            status=MetadataStatus.FOUND,
        )
