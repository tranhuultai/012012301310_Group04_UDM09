"""Fernet symmetric encryption wrapper for P2PChat session messages."""

from cryptography.fernet import Fernet


class CryptoHandler:
    """Fernet symmetric encryption/decryption for one session."""

    def __init__(self, key: bytes | None = None) -> None:
        self.key    = key or Fernet.generate_key()
        self.fernet = Fernet(self.key)

    def encrypt(self, data: str) -> str:
        """Encrypt a plaintext string, returning base64 ciphertext."""
        if not isinstance(data, str):
            raise ValueError("Data to encrypt must be a string.")
        return self.fernet.encrypt(data.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_data: str, ttl: int | None = None) -> str:
        """Decrypt base64 ciphertext back to the original string. No TTL by
        default — node-level dedup handles replay; TTL would false-reject
        on clock skew."""
        if not isinstance(encrypted_data, str):
            raise ValueError("Data to decrypt must be a string.")
        return self.fernet.decrypt(encrypted_data.encode("utf-8"), ttl=ttl).decode("utf-8")

    def get_key(self) -> bytes:
        """Return the raw Fernet key bytes."""
        return self.key
