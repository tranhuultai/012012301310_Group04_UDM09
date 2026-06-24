"""Chat history persistence."""

from pathlib import Path
from typing import Any

from storage.storage_manager import StorageManager

class MessageHistory:
    """
    Per-peer message history.

    data/storage/chat_history/

    peer_id.json
    """

    def __init__(self):

        self.base_dir = Path(
            "data/storage/chat_history"
        )

        StorageManager.ensure_dir(
            self.base_dir
        )

    # --------------------------------
    # FREEZE API
    # --------------------------------

    def load_history(
        self,
        peer_id: str
    ) -> list[dict[str, Any]]:

        file_path = (
            self.base_dir /
            f"{peer_id}.json"
        )

        history = StorageManager.load_json(
            file_path,
            []
        )
        if not isinstance(
            history,
            list
        ):
            return []

        return history

    def save_history(
        self,
        peer_id: str,
        records: list[dict[str, Any]]
    ):

        file_path = (
            self.base_dir /
            f"{peer_id}.json"
        )

        StorageManager.save_json(
            file_path,
            records
        )

    def append_message(
        self,
        peer_id: str,
        record: dict[str, Any]
    ):
        """
        Append one message.
        record format:
        {
            "message_id": "...",
            "peer_id": "...",
            "direction": "sent",
            "content": "...",
            "timestamp": 123456
        }
        """

        history = self.load_history(
            peer_id
        )

        history.append(
            record
        )

        self.save_history(
            peer_id,
            history
        )
     
    def clear_history(
        self,
        peer_id: str
    ):
        """Delete all history for one peer."""

        file_path = (
            self.base_dir /
            f"{peer_id}.json"
        )

        if file_path.exists():
            file_path.unlink()

    def get_message_count(
        self,
        peer_id: str
    ) -> int:
        """Return message count."""

        history = self.load_history(
            peer_id
        )

        return len(history)
    def get_history(
        self,
        peer_id: str,
        limit: int = 100
   ):
      """Return recent messages."""

      history = self.load_history(
        peer_id
    )

      return history[-limit:]
    def get_recent(
       self,
       limit: int = 50
    ):
       """Recent messages across all peers."""

       records = []

       for file_path in self.base_dir.glob(
                "*.json"
            ):

        peer_id = file_path.stem

        history = self.load_history(
            peer_id
        )

        records.extend(
            history
        )

        records.sort(
        key=lambda x:
        x.get(
            "timestamp",
            0
        )
    )

       return records[-limit:]
    def export(
       self,
       peer_id: str,
       filepath: Path
   ):
       """Export history to txt file."""

       history = self.load_history(
        peer_id
       )

       with open(
        filepath,
        "w",
        encoding="utf-8"
    ) as f:

        for record in history:

            timestamp = record.get(
                "timestamp",
                ""
            )

            direction = record.get(
                "direction",
                ""
            )

            content = record.get(
                "content",
                ""
            )

            f.write(
                f"[{timestamp}] "
                f"{direction}: "
                f"{content}\n"
            )
