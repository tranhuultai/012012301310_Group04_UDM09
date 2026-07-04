"""Trust-On-First-Use engine — peer identity verification and trust management."""

import logging

from trust.trust_state import TrustState
from trust.trust_store import TrustStore

logger = logging.getLogger(__name__)


class TOFUEngine:
    """TOFU trust engine. verify_peer: unknown -> TRUSTED/NEW, BLOCKED stays
    BLOCKED, fingerprint match -> VERIFIED, mismatch -> MISMATCH (no
    auto-update; user must accept)."""

    def __init__(self, profile: str = "known_peers") -> None:
        """profile is forwarded to TrustStore (per-port value for multi-instance testing)."""
        self.store = TrustStore(profile=profile)

    # ------------------------------------------------------------------ #
    # Core API                                                             #
    # ------------------------------------------------------------------ #

    def verify_peer(self, peer_id: str, fingerprint: str) -> str:
        """Check peer_id against its stored fingerprint, returning the resulting TrustState."""
        rec = self.store.get_peer(peer_id)

        if rec is None:
            # First contact — TOFU: trust immediately, signal NEW to caller.
            self.store.add_peer(peer_id, fingerprint, TrustState.TRUSTED)
            logger.info("[TOFU] New peer %s — auto-trusted.", peer_id[:12])
            return TrustState.NEW

        stored_fp   = rec["fingerprint"]
        trust_state = rec["trust_state"]

        # BLOCKED peers are always rejected regardless of fingerprint.
        if trust_state == TrustState.BLOCKED:
            logger.debug("[TOFU] Peer %s is BLOCKED.", peer_id[:12])
            return TrustState.BLOCKED

        if stored_fp == fingerprint:
            # Fingerprint matches — confirm as VERIFIED.
            if trust_state != TrustState.VERIFIED:
                self.store.update_peer(peer_id, fingerprint, TrustState.VERIFIED)
                logger.debug("[TOFU] Peer %s verified.", peer_id[:12])
            return TrustState.VERIFIED

        # Fingerprint changed — possible key rotation or MITM attack.
        logger.warning(
            "[TOFU] Fingerprint MISMATCH for %s (stored=%s, received=%s)",
            peer_id[:12], stored_fp[:23], fingerprint[:23],
        )
        return TrustState.MISMATCH

    def add_peer(self, peer_id: str, fingerprint: str) -> None:
        """Explicitly add peer_id as TRUSTED (used by tests and contacts)."""
        self.store.add_peer(peer_id, fingerprint, TrustState.TRUSTED)

    def update_peer(self, peer_id: str, fingerprint: str, trust_state: str) -> None:
        """Overwrite the stored record for peer_id."""
        self.store.update_peer(peer_id, fingerprint, trust_state)

    def _set_trust_state(self, peer_id: str, trust_state: str) -> bool:
        """Move an existing peer to trust_state; False if peer is unknown."""
        rec = self.store.get_peer(peer_id)
        if not rec:
            return False
        self.store.update_peer(peer_id, rec["fingerprint"], trust_state)
        return True

    def trust_peer(self, peer_id: str) -> bool:
        """Mark peer_id as TRUSTED (user-initiated)."""
        ok = self._set_trust_state(peer_id, TrustState.TRUSTED)
        if ok:
            logger.info("[TOFU] Peer %s manually trusted.", peer_id[:12])
        else:
            logger.warning("[TOFU] trust_peer: unknown peer %s", peer_id[:12])
        return ok

    def block_peer(self, peer_id: str) -> bool:
        """Mark peer_id as BLOCKED (user-initiated)."""
        ok = self._set_trust_state(peer_id, TrustState.BLOCKED)
        if ok:
            logger.info("[TOFU] Peer %s BLOCKED.", peer_id[:12])
        else:
            logger.warning("[TOFU] block_peer: unknown peer %s", peer_id[:12])
        return ok

    def unblock_peer(self, peer_id: str) -> bool:
        """Unblock peer_id by resetting trust to TRUSTED."""
        ok = self._set_trust_state(peer_id, TrustState.TRUSTED)
        if ok:
            logger.info("[TOFU] Peer %s unblocked → TRUSTED.", peer_id[:12])
        return ok

    def accept_mismatch(self, peer_id: str, new_fingerprint: str) -> None:
        """Accept a fingerprint change and re-trust peer_id under the new key."""
        self.store.update_peer(peer_id, new_fingerprint, TrustState.TRUSTED)
        logger.info("[TOFU] Mismatch accepted for %s — new key trusted.", peer_id[:12])

    def get_trust_state(self, peer_id: str) -> str:
        """Return peer_id's trust state, or TrustState.NEW if unknown."""
        rec = self.store.get_peer(peer_id)
        return rec["trust_state"] if rec else TrustState.NEW

    def is_blocked(self, peer_id: str) -> bool:
        """Return True if peer_id is BLOCKED."""
        return self.store.is_blocked(peer_id)
