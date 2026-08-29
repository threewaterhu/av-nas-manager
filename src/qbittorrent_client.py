"""Read-only qBittorrent Web API client."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any

from .config import QBittorrentConfig


class QBittorrentError(RuntimeError):
    """Base error for qBittorrent communication."""


class QBittorrentAuthenticationError(QBittorrentError):
    """Raised when qBittorrent rejects or lacks credentials."""


@dataclass(frozen=True)
class Torrent:
    hash: str
    name: str
    save_path: str
    content_path: str


@dataclass(frozen=True)
class TorrentFile:
    name: str
    size: int
    progress: float


class QBittorrentClient:
    """Minimal client whose public data methods issue GET requests only."""

    def __init__(self, config: QBittorrentConfig, timeout: float = 5.0) -> None:
        self.config = config
        self.timeout = timeout
        self.base_url = f"http://{config.host}:{config.port}"
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self._authenticated = False

    def _open(self, request: urllib.request.Request) -> bytes:
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise QBittorrentAuthenticationError(
                    "qBittorrent authentication was rejected"
                ) from exc
            raise QBittorrentError(f"qBittorrent HTTP error {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise QBittorrentError(f"Cannot connect to qBittorrent: {exc.reason}") from exc

    def authenticate(self) -> None:
        if not self.config.username or not self.config.password:
            raise QBittorrentAuthenticationError(
                "qBittorrent username/password are missing from local config.yaml"
            )
        payload = urllib.parse.urlencode(
            {"username": self.config.username, "password": self.config.password}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/v2/auth/login",
            data=payload,
            method="POST",
            headers={"Referer": self.base_url},
        )
        if self._open(request).strip() != b"Ok.":
            raise QBittorrentAuthenticationError(
                "qBittorrent authentication was rejected"
            )
        self._authenticated = True

    def _get_json(self, endpoint: str, params: dict[str, str] | None = None) -> Any:
        if not self._authenticated:
            self.authenticate()
        query = "" if not params else "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}{query}",
            method="GET",
            headers={"Referer": self.base_url},
        )
        try:
            return json.loads(self._open(request).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QBittorrentError("qBittorrent returned invalid JSON") from exc

    def get_torrents(self) -> list[Torrent]:
        items = self._get_json("/api/v2/torrents/info")
        return [
            Torrent(
                hash=str(item["hash"]),
                name=str(item.get("name", "")),
                save_path=str(item.get("save_path", "")),
                content_path=str(item.get("content_path", "")),
            )
            for item in items
        ]

    def get_torrent_files(self, torrent_hash: str) -> list[TorrentFile]:
        items = self._get_json(
            "/api/v2/torrents/files", {"hash": torrent_hash}
        )
        return [
            TorrentFile(
                name=str(item["name"]),
                size=int(item.get("size", 0)),
                progress=float(item.get("progress", 0.0)),
            )
            for item in items
        ]
