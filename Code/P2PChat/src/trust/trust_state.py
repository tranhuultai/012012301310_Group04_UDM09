"""Trust state constants for TOFU model."""


class TrustState:
    """Peer trust states: NEW (first discovery), TRUSTED (user-trusted),
    VERIFIED (fingerprint confirmed), MISMATCH (fingerprint changed,
    possible MITM), BLOCKED (all traffic rejected)."""

    NEW      = "NEW"
    TRUSTED  = "TRUSTED"
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    BLOCKED  = "BLOCKED"

    _ALL: frozenset[str] = frozenset({NEW, TRUSTED, VERIFIED, MISMATCH, BLOCKED})

    @classmethod
    def is_valid(cls, state: str) -> bool:
        """Return True if *state* is a known trust state string."""
        return state in cls._ALL
