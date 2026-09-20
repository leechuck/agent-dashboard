"""Shared bounds and disk-space checks for environment transfers."""

import shutil
from pathlib import Path

MAX_BYTES = 32 * 1024**3
REQUEST_TIMEOUT = 7200
RESERVE_BYTES = 512 * 1024**2


def require_space(path: Path, needed: int) -> None:
    require_spaces([(path, needed)])


def require_spaces(items: list[tuple[Path, int]]) -> None:
    """Group planned writes by filesystem, including paths not yet created."""
    volumes: dict[int, tuple[Path, int]] = {}
    for path, needed in items:
        if needed <= 0:
            continue
        while not path.exists():
            path = path.parent
        device = path.stat().st_dev
        previous = volumes.get(device, (path, 0))
        volumes[device] = (previous[0], previous[1] + needed)
    for path, needed in volumes.values():
        free = shutil.disk_usage(path).free
        if free < needed + RESERVE_BYTES:
            raise OSError(
                f"not enough free disk space on {path}: "
                f"need {needed / 1024**3:.2f} GiB plus 0.5 GiB reserve; "
                f"{free / 1024**3:.2f} GiB available"
            )
