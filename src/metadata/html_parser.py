"""Narrow HTML parser helpers used by public-page providers."""

from __future__ import annotations

import html
from html.parser import HTMLParser


class PublicPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og_title = ""
        self.page_title_parts: list[str] = []
        self._in_title = False
        self.actresses: list[str] = []
        self._actress_link = False
        self._actress_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("property") == "og:title":
            self.og_title = html.unescape(values.get("content", ""))
        if tag == "title":
            self._in_title = True
        href = values.get("href", "") or ""
        if tag == "a" and "/av-actress/" in href and values.get("rel") == "tag":
            self._actress_link = True
            self._actress_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._actress_link:
            name = " ".join("".join(self._actress_parts).split())
            if name and name not in self.actresses:
                self.actresses.append(name)
            self._actress_link = False
            self._actress_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.page_title_parts.append(data)
        if self._actress_link:
            self._actress_parts.append(data)

    @property
    def page_title(self) -> str:
        return " ".join("".join(self.page_title_parts).split())
