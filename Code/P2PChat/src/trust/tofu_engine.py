"""Trust-On-First-Use engine — peer identity verification and trust management."""

import logging

from trust.trust_state import TrustState
from trust.trust_store import TrustStore

logger = logging.getLogger(__name__)


class TOFUEngine:
    """TOFU trust engine.

    State machine (verify_peer)
    ───────────────────────────
    unknown peer          → store as TRUSTED, return NEW
    stored + BLOCKED      → return BLOCKED  (no state change)
    stored + fp match     → promote to VERIFIED, return VERIFIED
    stored + fp mismatch  → return MISMATCH (no auto-update; user must accept)
    """

    def __init__(self, profile: str = "known_peers") -> None:
        """Initialise the engine with a fresh TrustStore.

        Args:
            profile: Forwarded to TrustStore — see its docstring for why a
                per-port value matters when testing multiple local instances.
        """
        self.store = TrustStore(profile=profile)

    # ------------------------------------------------------------------ #
    # Core API                                                             #
    # ------------------------------------------------------------------ #

    def verify_peer(self, peer_id: str, fingerprint: str) -> str:
        """Verify *peer_id* against its stored fingerprint.

        Args:
            peer_id: SHA-256 peer identifier.
            fingerprint: Fingerprint received from the peer.

        Returns:
            One of the TrustState constants representing the outcome.
        """
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
        """Explicitly add *peer_id* as TRUSTED (used by tests and contacts).

        Args:
            peer_id: SHA-256 peer identifier.
            fingerprint: Fingerprint to store.
        """
        self.store.add_peer(peer_id, fingerprint, TrustState.TRUSTED)

    def update_peer(self, peer_id: str, fingerprint: str, trust_state: str) -> None:
        """Update the stored record for *peer_id*.

        Args:
            peer_id: SHA-256 peer identifier.
            fingerprint: New fingerprint value.
            trust_state: New trust state.
        """
        self.store.update_peer(peer_id, fingerprint, trust_state)

    def trust_peer(self, peer_id: str) -> bool:
        """Mark *peer_id* as TRUSTED (user-initiated).

        Args:
            peer_id: SHA-256 peer identifier.

        Returns:
            True if the record was found and updated.
        """
        rec = self.store.get_peer(peer_id)
        if not rec:
            logger.warning("[TOFU] trust_peer: unknown peer %s", peer_id[:12])
            return False
        self.store.update_peer(peer_id, rec["fingerprint"], TrustState.TRUSTED)
        logger.info("[TOFU] Peer %s manually trusted.", peer_id[:12])
        return True

    def block_peer(self, peer_id: str) -> bool:
        """Mark *peer_id* as BLOCKED (user-initiated).

        Args:
            peer_id: SHA-256 peer identifier.

        Returns:
            True if the record was found and updated.
        """
        rec = self.store.get_peer(peer_id)
        if not rec:
            logger.warning("[TOFU] block_peer: unknown peer %s", peer_id[:12])
            return False
        self.store.update_peer(peer_id, rec["fingerprint"], TrustState.BLOCKED)
        logger.info("[TOFU] Peer %s BLOCKED.", peer_id[:12])
        return True

    def unblock_peer(self, peer_id: str) -> bool:
        """Unblock *peer_id* by resetting trust to TRUSTED.

        Args:
            peer_id: SHA-256 peer identifier.

        Returns:
            True if the record was found and updated.
        """
        rec = self.store.get_peer(peer_id)
        if not rec:
            return False
        self.store.update_peer(peer_id, rec["fingerprint"], TrustState.TRUSTED)
        logger.info("[TOFU] Peer %s unblocked → TRUSTED.", peer_id[:12])
        return True

    def accept_mismatch(self, peer_id: str, new_fingerprint: str) -> None:
        """Accept a fingerprint change and re-trust *peer_id*.

        Args:
            peer_id: SHA-256 peer identifier.
            new_fingerprint: The new fingerprint to trust.
        """
        self.store.update_peer(peer_id, new_fingerprint, TrustState.TRUSTED)
        logger.info("[TOFU] Mismatch accepted for %s — new key trusted.", peer_id[:12])

    def get_trust_state(self, peer_id: str) -> str:
        """Return the current trust state string for *peer_id*.

        Args:
            peer_id: SHA-256 peer identifier.

        Returns:
            TrustState constant, or TrustState.NEW if peer is unknown.
        """
        rec = self.store.get_peer(peer_id)
        return rec["trust_state"] if rec else TrustState.NEW

    def is_blocked(self, peer_id: str) -> bool:
        """Return True if *peer_id* is BLOCKED.

        Args:
            peer_id: SHA-256 peer identifier.
        """
        return self.store.is_blocked(peer_id)
