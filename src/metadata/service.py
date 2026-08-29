"""Cache-first provider orchestration with polite request pacing."""

from __future__ import annotations

import time
from collections.abc import Sequence

from .base import Metadata, MetadataProvider, MetadataStatus
from .cache import MetadataCache


class MetadataService:
    def __init__(
        self,
        providers: Sequence[MetadataProvider],
        cache: MetadataCache,
        request_interval_seconds: float = 1.0,
    ) -> None:
        self.providers = providers
        self.cache = cache
        self.request_interval_seconds = request_interval_seconds
        self._last_request_at: float | None = None

    def _pace(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self.request_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def get(self, product_code: str) -> Metadata:
        cached = self.cache.get(product_code)
        if cached is not None:
            return cached

        errors: list[str] = []
        saw_not_found = False
        for provider in self.providers:
            self._pace()
            result = provider.fetch(product_code)
            if result.status == MetadataStatus.FOUND:
                self.cache.put(result)
                return result
            if result.status == MetadataStatus.NOT_FOUND:
                saw_not_found = True
            if result.error:
                errors.append(f"{provider.name}: {result.error}")

        status = MetadataStatus.NOT_FOUND if saw_not_found else MetadataStatus.ERROR
        result = Metadata(
            product_code=product_code,
            status=status,
            error="; ".join(errors),
        )
        if status == MetadataStatus.NOT_FOUND:
            self.cache.put(result)
        return result
