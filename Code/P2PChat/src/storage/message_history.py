"""Per-peer chat history persistence."""

import threading
from pathlib import Path
from typing import Any

from storage.storage_manager import StorageManager


class MessageHistory:
    """Persistent per-peer message log: one JSON file per peer_id under
    data/storage/chat_history/, each record a HistoryRecord DTO
    (message_id, peer_id, direction, content, timestamp)."""

    def __init__(self) -> None:
        """Initialise and ensure the history directory exists."""
        self.base_dir = Path("data/storage/chat_history")
        StorageManager.ensure_dir(self.base_dir)
        # Guards concurrent append_message calls from multiple receive threads.
        # Without this, two peers sending simultaneously can cause a lost-write:
        # both threads load, both append, the later save overwrites the earlier.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # CRUD                                                                 #
    # ------------------------------------------------------------------ #

    def load_history(self, peer_id: str) -> list[dict[str, Any]]:
        """Return all HistoryRecords for peer_id, oldest first (empty if none)."""
        file_path = self.base_dir / f"{peer_id}.json"
        history = StorageManager.load_json(file_path, [])
        if not isinstance(history, list):
            return []
        return history

    def save_history(self, peer_id: str, records: list[dict[str, Any]]) -> None:
        """Overwrite peer_id's history file with records."""
        file_path = self.base_dir / f"{peer_id}.json"
        StorageManager.save_json(file_path, records)

    def append_message(self, peer_id: str, record: dict[str, Any]) -> None:
        """Append one record under the lock; reloads the whole file each
        call (O(n), fine at course-project volumes)."""
        with self._lock:
            history = self.load_history(peer_id)
            history.append(record)
            self.save_history(peer_id, history)

    def clear_history(self, peer_id: str) -> None:
        """Delete all history for peer_id."""
        file_path = self.base_dir / f"{peer_id}.json"
        if file_path.exists():
            file_path.unlink()

    def get_message_count(self, peer_id: str) -> int:
        """Return the number of stored messages for peer_id."""
        return len(self.load_history(peer_id))
