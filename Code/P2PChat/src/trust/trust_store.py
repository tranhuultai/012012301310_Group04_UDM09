"""Persistent trust store: maps peer_id → {fingerprint, trust_state}."""
# WHY changed: concurrent background saves had no ordering guarantee, so an
# older snapshot's write could land after (and silently overwrite) a newer one.

import json
import logging
import threading
from pathlib import Path

from trust.trust_state import TrustState

logger = logging.getLogger(__name__)


class TrustStore:
    """Thread-safe JSON-backed store for peer trust records, in
    data/trust/<profile>.json as {peer_id: {fingerprint, trust_state}}."""

    def __init__(self, profile: str = "known_peers") -> None:
        """Load the trust store; pass a per-port profile for multiple local
        instances, or their writes race on the same file."""
        self._data_dir   = Path("data/trust")
        self._store_file = self._data_dir / f"{profile}.json"
        self._lock       = threading.RLock()
        # Serializes disk writes across the background threads _save() spawns
        # — without it, two saves fired close together race on the same .tmp
        # file, which Windows rejects with "Access is denied".
        self._write_lock = threading.Lock()
        # Monotonic counter so a slow write for an older snapshot can't land
        # after (and silently regress) a newer one — each _save() spawns its
        # own thread, and scheduling, not call order, decides which writes first.
        self._save_seq         = 0
        self._last_written_seq = 0
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
        """Schedule an async atomic write, so slow storage can't stall the
        discovery/receive threads calling this."""
        # Snapshot under the existing lock (caller holds it).
        snapshot = dict(self._peers)
        self._save_seq += 1
        seq = self._save_seq
        threading.Thread(
            target=self._write_snapshot,
            args=(snapshot, seq),
            daemon=True,
            name="TrustStoreSave",
        ).start()

    def _write_snapshot(self, snapshot: dict, seq: int) -> None:
        """Atomically write snapshot to disk; skipped if a higher seq
        already landed first (see _save_seq)."""
        tmp = self._store_file.with_suffix(".tmp")
        with self._write_lock:
            if seq < self._last_written_seq:
                logger.debug("[TRUST] Skipping stale write (seq=%d < %d)",
                             seq, self._last_written_seq)
                return
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(snapshot, fh, indent=4)
                tmp.replace(self._store_file)
                self._last_written_seq = seq
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
        """Insert a new peer record (overwrites if already exists)."""
        with self._lock:
            self._peers[peer_id] = {
                "fingerprint": fingerprint,
                "trust_state": trust_state,
            }
            self._save()

    def update_peer(self, peer_id: str, fingerprint: str, trust_state: str) -> None:
        """Update an existing peer record."""
        with self._lock:
            self._peers[peer_id] = {
                "fingerprint": fingerprint,
                "trust_state": trust_state,
            }
            self._save()

    def remove_peer(self, peer_id: str) -> None:
        """Remove a peer from the store, if present."""
        with self._lock:
            if peer_id in self._peers:
                del self._peers[peer_id]
                self._save()

    def get_all_peers(self) -> dict[str, dict]:
        """Return a shallow copy of all peer records."""
        with self._lock:
            return {pid: dict(rec) for pid, rec in self._peers.items()}

    def is_blocked(self, peer_id: str) -> bool:
        """Return True if peer_id is explicitly BLOCKED."""
        with self._lock:
            rec = self._peers.get(peer_id)
            return bool(rec and rec.get("trust_state") == TrustState.BLOCKED)
