"""Per-peer chat history persistence."""

import threading
from pathlib import Path
from typing import Any

from storage.storage_manager import StorageManager


class MessageHistory:
    """Persistent per-peer message log.

    Each peer gets its own JSON file under data/storage/chat_history/
    named <peer_id>.json.  Records follow the HistoryRecord DTO::

        {
            "message_id": "...",
            "peer_id":    "...",
            "direction":  "sent" | "received",
            "content":    "...",
            "timestamp":  1234567890.0
        }
    """

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
        """Load and return all history records for *peer_id*.

        Args:
            peer_id: SHA-256 peer identifier.

        Returns:
            List of HistoryRecord dicts, oldest first.  Empty list if no
            history exists or the file is unreadable.
        """
        file_path = self.base_dir / f"{peer_id}.json"
        history = StorageManager.load_json(file_path, [])
        if not isinstance(history, list):
            return []
        return history

    def save_history(self, peer_id: str, records: list[dict[str, Any]]) -> None:
        """Overwrite the history file for *peer_id* with *records*.

        Args:
            peer_id: SHA-256 peer identifier.
            records: Complete list of HistoryRecord dicts to persist.
        """
        file_path = self.base_dir / f"{peer_id}.json"
        StorageManager.save_json(file_path, records)

    def append_message(self, peer_id: str, record: dict[str, Any]) -> None:
        """Append one message record and persist atomically.

        Thread-safe: acquires _lock for the full read-modify-write cycle
        so that concurrent calls from multiple receive threads cannot lose
        each other's messages.

        Args:
            peer_id: SHA-256 peer identifier.
            record: HistoryRecord dict to append.

        Note:
            This loads the entire file on every call (O(n) read).  Acceptable
            for the expected message volumes in a course-project context.
        """
        with self._lock:
            history = self.load_history(peer_id)
            history.append(record)
            self.save_history(peer_id, history)

    def clear_history(self, peer_id: str) -> None:
        """Delete all history for *peer_id*.

        Args:
            peer_id: SHA-256 peer identifier.
        """
        file_path = self.base_dir / f"{peer_id}.json"
        if file_path.exists():
            file_path.unlink()

    def get_message_count(self, peer_id: str) -> int:
        """Return the number of stored messages for *peer_id*.

        Args:
            peer_id: SHA-256 peer identifier.
        """
        return len(self.load_history(peer_id))
