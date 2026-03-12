from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def get_runtime_version() -> str:
    """Return a git-describe style version for the current checkout."""
    try:
        proc = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'describe', '--tags', '--always', '--dirty'],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return 'unknown'

    version = proc.stdout.strip()
    return version or 'unknown'
