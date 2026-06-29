"""Fernet symmetric encryption wrapper for P2PChat session messages."""

from cryptography.fernet import Fernet


class CryptoHandler:
    """Handles Fernet symmetric encryption and decryption for a single session."""

    def __init__(self, key: bytes | None = None) -> None:
        """Initialise with an existing key or generate a fresh one.

        Args:
            key: 32-byte URL-safe base64 Fernet key, or None to generate one.
        """
        self.key    = key or Fernet.generate_key()
        self.fernet = Fernet(self.key)

    def encrypt(self, data: str) -> str:
        """Encrypt *data* and return the result as a base64-encoded string.

        Args:
            data: Plaintext string to encrypt.

        Returns:
            URL-safe base64 ciphertext string.

        Raises:
            ValueError: If *data* is not a string.
        """
        if not isinstance(data, str):
            raise ValueError("Data to encrypt must be a string.")
        return self.fernet.encrypt(data.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_data: str, ttl: int | None = None) -> str:
        """Decrypt *encrypted_data* and return the original string.

        Args:
            encrypted_data: Base64-encoded Fernet ciphertext.
            ttl: Maximum token age in seconds.  Defaults to None (no age
                 check).  For direct P2P sessions, replay protection is already
                 provided by the node-level seen_messages deque; Fernet TTL
                 would cause false rejections if clocks are skewed between peers.

        Returns:
            Decrypted plaintext string.

        Raises:
            cryptography.fernet.InvalidToken: If decryption fails.
            ValueError: If *encrypted_data* is not a string.
        """
        if not isinstance(encrypted_data, str):
            raise ValueError("Data to decrypt must be a string.")
        return self.fernet.decrypt(encrypted_data.encode("utf-8"), ttl=ttl).decode("utf-8")

    def get_key(self) -> bytes:
        """Return the current Fernet key bytes."""
        return self.key
