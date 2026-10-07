"""Free memory on this machine, for sizing how many workers may start (Kiro Crew: memory,
not a count, bounds concurrency). Returns None when it can't be read, so callers fall back
to the configured ceiling instead of guessing."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_GB = 1024 ** 3


def _from_vm_stat(text: str) -> float | None:
    """macOS: free + inactive + speculative pages are available without swapping."""
    page = re.search(r"page size of (\d+) bytes", text)
    if not page:
        return None
    size = int(page.group(1))
    pages = 0
    for key in ("Pages free", "Pages inactive", "Pages speculative"):
        m = re.search(rf"{key}:\s+(\d+)\.", text)
        if m:
            pages += int(m.group(1))
    return pages * size / _GB


def _from_meminfo(text: str) -> float | None:
    """Linux: the kernel's own estimate of memory available for new work."""
    m = re.search(r"MemAvailable:\s+(\d+)\s+kB", text)
    return int(m.group(1)) * 1024 / _GB if m else None


def available_gb() -> float | None:
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            return _from_vm_stat(r.stdout) if r.returncode == 0 else None
        meminfo = Path("/proc/meminfo")
        return _from_meminfo(meminfo.read_text()) if meminfo.exists() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
