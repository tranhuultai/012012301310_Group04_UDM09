"""Persistent trust store: maps peer_id → {fingerprint, trust_state}."""

import json
import logging
import threading
from pathlib import Path

from trust.trust_state import TrustState

logger = logging.getLogger(__name__)

_SCHEMA = {"fingerprint": str, "trust_state": str}


class TrustStore:
    """Thread-safe JSON-backed store for peer trust records.

    File layout (data/trust/known_peers.json)::

        {
            "<peer_id>": {
                "fingerprint": "<hex>",
                "trust_state": "VERIFIED"
            }
        }
    """

    def __init__(self) -> None:
        """Initialise and load the trust store from disk."""
        self._data_dir   = Path("data/trust")
        self._store_file = self._data_dir / "known_peers.json"
        self._lock       = threading.RLock()
        self._peers: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        """Load peers from disk; reset to empty on any error."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if not self._store_file.exists():
            return
        try:
            with open(self._store_file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            # Validate each record; drop malformed entries.
            peers: dict[str, dict] = {}
            for pid, rec in raw.items():
                if (isinstance(rec, dict)
                        and isinstance(rec.get("fingerprint"), str)
                        and TrustState.is_valid(rec.get("trust_state", ""))):
                    peers[pid] = rec
                else:
                    logger.warning("[TRUST] Dropping malformed record for %s", pid[:12])
            self._peers = peers
            logger.debug("[TRUST] Loaded %d peer records.", len(self._peers))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("[TRUST] Failed to load trust store: %s — starting fresh.", exc)
            self._peers = {}

    def _save(self) -> None:
        """Schedule an asynchronous atomic write of the trust store.

        Taking a snapshot of the current data and writing in a background
        thread prevents the discovery or receive threads from stalling on
        slow storage while trust records are updated.
        """
        # Snapshot under the existing lock (caller holds it).
        snapshot = dict(self._peers)
        threading.Thread(
            target=self._write_snapshot,
            args=(snapshot,),
            daemon=True,
            name="TrustStoreSave",
        ).start()

    def _write_snapshot(self, snapshot: dict) -> None:
        """Atomically write *snapshot* to disk (runs in a background thread).

        Args:
            snapshot: Copy of the peers dict taken while the lock was held.
        """
        tmp = self._store_file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, indent=4)
            tmp.replace(self._store_file)
        except OSError as exc:
            logger.error("[TRUST] Failed to save trust store: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # CRUD                                                                 #
    # ------------------------------------------------------------------ #

    def get_peer(self, peer_id: str) -> dict | None:
        """Return the trust record for *peer_id*, or None if not found."""
        with self._lock:
            rec = self._peers.get(peer_id)
            return dict(rec) if rec else None

    def add_peer(self, peer_id: str, fingerprint: str, trust_state: str) -> None:
        """Insert a new peer record (overwrites if already exists).

        Args:
            peer_id: SHA-256 peer identifier.
            fingerprint: Colon-separated hex fingerprint.
            trust_state: One of the TrustState constants.
        """
        with self._lock:
            self._peers[peer_id] = {
                "fingerprint": fingerprint,
                "trust_state": trust_state,
            }
            self._save()

    def update_peer(self, peer_id: str, fingerprint: str, trust_state: str) -> None:
        """Update an existing peer record.

        Args:
            peer_id: SHA-256 peer identifier.
            fingerprint: New fingerprint value.
            trust_state: New trust state.
        """
        with self._lock:
            self._peers[peer_id] = {
                "fingerprint": fingerprint,
                "trust_state": trust_state,
            }
            self._save()

    def remove_peer(self, peer_id: str) -> None:
        """Remove a peer from the store, if present.

        Args:
            peer_id: SHA-256 peer identifier.
        """
        with self._lock:
            if peer_id in self._peers:
                del self._peers[peer_id]
                self._save()

    def get_all_peers(self) -> dict[str, dict]:
        """Return a shallow copy of all peer records."""
        with self._lock:
            return {pid: dict(rec) for pid, rec in self._peers.items()}

    def is_blocked(self, peer_id: str) -> bool:
        """Return True if *peer_id* is explicitly BLOCKED.

        Args:
            peer_id: SHA-256 peer identifier.
        """
        with self._lock:
            rec = self._peers.get(peer_id)
            return bool(rec and rec.get("trust_state") == TrustState.BLOCKED)
