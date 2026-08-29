"""Small, dependency-free loader for this project's deliberately simple YAML."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QBittorrentConfig:
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True)
class AppConfig:
    source_directory: Path
    nas_mount_path: Path
    nas_target_directory: Path
    min_video_size_mb: int
    dry_run: bool
    max_actresses_in_filename: int
    qbittorrent: QBittorrentConfig
    database_path: Path
    log_path: Path
    metadata_request_timeout_seconds: float
    metadata_request_interval_seconds: float
    verification_mode: str
    upload_buffer_size_mib: int


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value[0:1] in {'"', "'"}:
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError(f"Expected a string, got {type(parsed).__name__}")
        return parsed
    try:
        return int(value)
    except ValueError:
        return value


def _read_simple_yaml(path: Path) -> dict[str, Any]:
    """Read the flat mapping plus one nested mapping used by config.yaml."""
    result: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if ":" not in raw_line:
            raise ValueError(f"Invalid configuration line {line_number}")
        key, raw_value = raw_line.strip().split(":", 1)
        if indent == 0:
            if raw_value.strip() == "":
                section: dict[str, Any] = {}
                result[key] = section
                current_section = section
            else:
                result[key] = _scalar(raw_value)
                current_section = None
        elif indent == 2 and current_section is not None:
            current_section[key] = _scalar(raw_value)
        else:
            raise ValueError(f"Unsupported indentation on configuration line {line_number}")
    return result


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    raw = _read_simple_yaml(config_path)
    qbit = raw["qbittorrent"]
    metadata = raw.get("metadata", {})
    upload = raw.get("upload", {})
    if not isinstance(qbit, dict):
        raise ValueError("qbittorrent must be a mapping")
    return AppConfig(
        source_directory=Path(str(raw["source_directory"])).expanduser(),
        nas_mount_path=Path(str(raw["nas_mount_path"])).expanduser(),
        nas_target_directory=Path(str(raw["nas_target_directory"])).expanduser(),
        min_video_size_mb=int(raw["min_video_size_mb"]),
        dry_run=bool(raw["dry_run"]),
        max_actresses_in_filename=int(raw.get("max_actresses_in_filename", 3)),
        qbittorrent=QBittorrentConfig(
            host=str(qbit["host"]),
            port=int(qbit["port"]),
            username=str(qbit.get("username", "")),
            password=str(qbit.get("password", "")),
        ),
        database_path=Path(str(raw["database_path"])),
        log_path=Path(str(raw["log_path"])),
        metadata_request_timeout_seconds=float(metadata.get("request_timeout_seconds", 15)),
        metadata_request_interval_seconds=float(metadata.get("request_interval_seconds", 1)),
        verification_mode=str(upload.get("verification_mode", "size")),
        upload_buffer_size_mib=int(upload.get("buffer_size_mib", 8)),
    )
