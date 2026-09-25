"""Provider-neutral metadata types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
import ssl


class MetadataStatus(str, Enum):
    FOUND = "FOUND"
    MANUAL_CONFIRMED = "MANUAL_CONFIRMED"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Metadata:
    product_code: str
    title: str = ""
    actresses: tuple[str, ...] = ()
    source: str = ""
    source_url: str = ""
    status: MetadataStatus = MetadataStatus.NOT_FOUND
    error: str = ""
    from_cache: bool = False

    def cached_copy(self) -> "Metadata":
        return replace(self, from_cache=True)


class MetadataProvider(ABC):
    name: str

    @abstractmethod
    def fetch(self, product_code: str) -> Metadata:
        """Fetch one code without authenticating or bypassing access controls."""


def verified_ssl_context() -> ssl.SSLContext:
    """Use Python's CA bundle, falling back to macOS's system CA bundle."""
    paths = ssl.get_default_verify_paths()
    if paths.cafile:
        return ssl.create_default_context()
    system_ca = Path("/etc/ssl/cert.pem")
    if system_ca.is_file():
        return ssl.create_default_context(cafile=str(system_ca))
    return ssl.create_default_context()
