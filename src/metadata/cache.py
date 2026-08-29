"""SQLite metadata cache."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .base import Metadata, MetadataStatus


class MetadataCache:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    product_code TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    actresses_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL
                )
                """
            )

    def get(self, product_code: str) -> Metadata | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT title, actresses_json, source, source_url, status, error
                   FROM metadata_cache WHERE product_code = ?""",
                (product_code,),
            ).fetchone()
        if row is None:
            return None
        title, actresses_json, source, source_url, status, error = row
        return Metadata(
            product_code=product_code,
            title=title,
            actresses=tuple(json.loads(actresses_json)),
            source=source,
            source_url=source_url,
            status=MetadataStatus(status),
            error=error,
            from_cache=True,
        )

    def put(self, metadata: Metadata) -> None:
        if metadata.status == MetadataStatus.ERROR:
            return
        fetched_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO metadata_cache
                    (product_code, title, actresses_json, source, source_url,
                     fetched_at, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_code) DO UPDATE SET
                    title=excluded.title,
                    actresses_json=excluded.actresses_json,
                    source=excluded.source,
                    source_url=excluded.source_url,
                    fetched_at=excluded.fetched_at,
                    status=excluded.status,
                    error=excluded.error
                """,
                (
                    metadata.product_code,
                    metadata.title,
                    json.dumps(metadata.actresses, ensure_ascii=False),
                    metadata.source,
                    metadata.source_url,
                    fetched_at,
                    metadata.status.value,
                    metadata.error,
                ),
            )
