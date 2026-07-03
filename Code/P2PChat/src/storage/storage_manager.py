"""Shared atomic-write JSON storage helpers."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class StorageManager:
    """Low-level storage primitives used by all persistence services.

    All writes use an atomic temp-file rename pattern to prevent partial
    writes from corrupting stored data (important for identity and trust).
    """

    @staticmethod
    def ensure_dir(path: Path) -> None:
        """Create path and any missing parents; no-op if it already exists."""
        path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def load_json(file_path: Path, default=None):
        """Load JSON from file_path, or return default if missing/unreadable."""
        if default is None:
            default = {}

        if not file_path.exists():
            return default

        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("[STORAGE] Failed to load %s: %s", file_path, exc)
            return default

    @staticmethod
    def save_json(file_path: Path, data) -> None:
        """Write data as JSON via a temp file + rename, so a crash mid-write
        can never leave file_path partially written."""
        temp_file = file_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=4, ensure_ascii=False)
        temp_file.replace(file_path)
