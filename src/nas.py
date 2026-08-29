"""Read-only NAS mount diagnostics."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NASStatus:
    mount_path: Path
    target_path: Path
    mounted: bool
    target_exists: bool
    target_is_directory: bool
    readable: bool
    writable: bool

    @property
    def ready(self) -> bool:
        return all(
            (
                self.mounted,
                self.target_exists,
                self.target_is_directory,
                self.readable,
                self.writable,
            )
        )


def check_nas(mount_path: Path, target_path: Path) -> NASStatus:
    """Inspect paths and permissions without creating a write-test file."""
    target_exists = target_path.exists()
    return NASStatus(
        mount_path=mount_path,
        target_path=target_path,
        mounted=os.path.ismount(mount_path),
        target_exists=target_exists,
        target_is_directory=target_exists and target_path.is_dir(),
        readable=target_exists and os.access(target_path, os.R_OK),
        writable=target_exists and os.access(target_path, os.W_OK),
    )
