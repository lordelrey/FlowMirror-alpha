"""Timestamped backups of files that are about to be overwritten."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def backup_existing(path: str | Path) -> Path | None:
    """If path exists, copy it to <path>.bak_<ts> and return the backup path.

    Returns None when path does not exist (nothing to back up).
    """
    p = Path(path)
    if not p.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = p.with_name(p.name + f".bak_{ts}")
    shutil.copy2(p, backup)
    return backup
