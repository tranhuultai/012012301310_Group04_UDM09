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
        """Create *path* and any missing parents, silently if already exists.

        Args:
            path: Directory path to create.
        """
        path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def load_json(file_path: Path, default=None):
        """Load and return JSON from *file_path*, or *default* on any error.

        Args:
            file_path: Path to the JSON file.
            default: Value returned when the file is missing or unreadable.

        Returns:
            Parsed JSON value, or *default*.
        """
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
        """Atomically write *data* as JSON to *file_path*.

        Writes to a sibling .tmp file first, then renames it into place.
        This guarantees the target file is never left in a partial state.

        Args:
            file_path: Destination path.
            data: JSON-serialisable value to persist.
        """
        temp_file = file_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=4, ensure_ascii=False)
        temp_file.replace(file_path)
