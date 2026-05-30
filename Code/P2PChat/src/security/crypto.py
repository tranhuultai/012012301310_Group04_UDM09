from cryptography.fernet import Fernet


class CryptoHandler:
    """Handles Fernet symmetric encryption and decryption."""

    def __init__(self, key: bytes | None = None) -> None:
        self.key = key or Fernet.generate_key()
        self.fernet = Fernet(self.key)

    @staticmethod
    def generate_key() -> bytes:
        """Generate and return a new Fernet key."""
        return Fernet.generate_key()

    def encrypt(self, data: str) -> str:
        """Encrypt *data* and return the result as a base64-encoded string."""
        if not isinstance(data, str):
            raise ValueError("Data to encrypt must be a string.")
        return self.fernet.encrypt(data.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt *encrypted_data* and return the original string.

        Raises:
            cryptography.fernet.InvalidToken: if decryption fails.
        """
        if not isinstance(encrypted_data, str):
            raise ValueError("Data to decrypt must be a string.")
        return self.fernet.decrypt(encrypted_data.encode("utf-8")).decode("utf-8")

    def get_key(self) -> bytes:
        """Return the current encryption key."""
        return self.key
