"""Identity management: key generation, persistence, and retrieval."""

import json
import logging
from pathlib import Path

from security.rsa_utils import RSAUtils

# ── Identity helpers ───────────────────────────────────────────────────────
# These two functions are module-level so node.py can import generate_peer_id
# directly from this module without pulling in an extra file.

def generate_peer_id(public_key_pem: str | bytes) -> str:
    """Return the SHA-256 peer ID derived from *public_key_pem*.

    The peer ID is the lowercase hex digest of the full PEM bytes (including
    headers).  This guarantees uniqueness across key sizes and formats.

    Args:
        public_key_pem: PEM-encoded RSA public key, as str or bytes.

    Returns:
        64-character lowercase hex string.
    """
    import hashlib  # pylint: disable=import-outside-toplevel
    if isinstance(public_key_pem, str):
        public_key_pem = public_key_pem.encode("utf-8")
    return hashlib.sha256(public_key_pem).hexdigest()


def generate_fingerprint(public_key_pem: str | bytes) -> str:
    """Return a human-readable colon-separated hex fingerprint.

    Format matches SSH-style fingerprints (e.g. AA:BB:CC:DD:…), making
    it easy for users to verify peer identity visually.

    Args:
        public_key_pem: PEM-encoded RSA public key, as str or bytes.

    Returns:
        Uppercase colon-separated hex string derived from SHA-256 of the PEM.
    """
    import hashlib  # pylint: disable=import-outside-toplevel
    if isinstance(public_key_pem, str):
        public_key_pem = public_key_pem.encode("utf-8")
    digest = hashlib.sha256(public_key_pem).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))

logger = logging.getLogger(__name__)


class IdentityManager:
    """Manages the local RSA identity (key pair, peer_id, fingerprint).

    On first run the key pair is generated and persisted to
    data/identity/<profile>.json.  On subsequent runs the existing
    key pair is loaded from disk, ensuring a stable peer_id across restarts.

    The *profile* parameter allows multiple instances on the same machine
    to maintain separate identities (useful for testing).  In production
    the default profile "identity" is used, which maps to
    data/identity/identity.json.
    """

    def __init__(self, profile: str = "identity") -> None:
        """Initialise with empty identity (call load_identity() to populate).

        Args:
            profile: Base filename (without .json) for the identity file.
                     Defaults to "identity" for the standard single-instance path.
        """
        self.identity_dir  = Path("data/identity")
        self.identity_file = self.identity_dir / f"{profile}.json"

        self.private_key     = None
        self.public_key      = None
        self.private_key_pem: str = ""
        self.public_key_pem:  str = ""
        self.peer_id:         str = ""
        self.fingerprint:     str = ""

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def load_identity(self) -> None:
        """Load existing identity from disk, or generate a fresh one.

        Generates and persists a new RSA-2048 key pair when no identity
        file exists.  Sets private_key_pem, public_key_pem,
        peer_id, and fingerprint as side-effects.
        """
        self.identity_dir.mkdir(parents=True, exist_ok=True)

        if self.identity_file.exists():
            try:
                self._load_from_disk()
            except ValueError as exc:
                logger.warning(
                    "[IDENTITY] Identity file invalid (%s) — regenerating.", exc
                )
                self._generate_and_save()
        else:
            self._generate_and_save()

        self.peer_id     = generate_peer_id(self.public_key_pem)
        self.fingerprint = generate_fingerprint(self.public_key_pem)
        logger.info(
            "[IDENTITY] Loaded peer_id=%s fp=%s",
            self.peer_id[:12], self.fingerprint[:23],
        )

    def save_identity(self) -> None:
        """Persist the current key pair to disk using an atomic write."""
        data = {
            "private_key": self.private_key_pem,
            "public_key":  self.public_key_pem,
        }
        temp = self.identity_file.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        temp.replace(self.identity_file)

    # ── Accessors ──────────────────────────────────────────────────────

    def get_peer_id(self) -> str:
        """Return the SHA-256 peer ID derived from the public key."""
        return self.peer_id

    def get_fingerprint(self) -> str:
        """Return the colon-separated hex fingerprint of the public key."""
        return self.fingerprint

    def get_public_key(self):
        """Return the loaded RSA public key object."""
        return self.public_key

    def get_private_key(self):
        """Return the loaded RSA private key object."""
        return self.private_key

    def get_public_key_pem(self) -> str:
        """Return the PEM-encoded public key string."""
        return self.public_key_pem

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _load_from_disk(self) -> None:
        """Read key PEMs from the identity file and deserialise them.

        If the file is missing required fields or the PEM data is corrupted,
        raises ValueError so load_identity can fall back to generating
        a fresh identity rather than crashing the entire node startup.

        Raises:
            ValueError: If the JSON is malformed or the keys cannot be loaded.
        """
        try:
            with open(self.identity_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Cannot read identity file: {exc}") from exc

        if "private_key" not in data or "public_key" not in data:
            raise ValueError("Identity file missing 'private_key' or 'public_key'.")

        try:
            self.private_key_pem = data["private_key"]
            self.public_key_pem  = data["public_key"]
            self.private_key     = RSAUtils.load_private_key(self.private_key_pem)
            self.public_key      = RSAUtils.load_public_key(self.public_key_pem)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise ValueError(f"Corrupted key material in identity file: {exc}") from exc

    def _generate_and_save(self) -> None:
        """Generate a new RSA-2048 key pair, serialise, and persist it.

        Both PEMs must be set before save_identity() so that discovery
        can sign JWTs immediately after startup.
        """
        self.private_key, self.public_key = RSAUtils.generate_key_pair()
        self.private_key_pem = RSAUtils.serialize_private_key(self.private_key)
        self.public_key_pem  = RSAUtils.serialize_public_key(self.public_key)
        self.save_identity()
        logger.info(
            "[IDENTITY] New identity generated → %s",
            self.identity_file,
        )
