from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class RSAUtils:
    """Utility class for RSA key generation, serialization, and en/decryption."""

    @staticmethod
    def generate_key_pair() -> tuple:
        """Generate and return a new (private_key, public_key) RSA-2048 pair."""

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        return private_key, private_key.public_key()

    @staticmethod
    def serialize_public_key(public_key) -> str:
        """Serialize *public_key* to a PEM-encoded string."""

        return public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    @staticmethod
    def load_public_key(public_key_string: str):
        """Deserialize a PEM-encoded public key string."""

        return serialization.load_pem_public_key(public_key_string.encode())

    @staticmethod
    def encrypt(public_key, data: bytes) -> bytes:
        """Encrypt *data* with *public_key* using OAEP/SHA-256."""

        return public_key.encrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    @staticmethod
    def decrypt(private_key, encrypted_data: bytes) -> bytes:
        """Decrypt *encrypted_data* with *private_key* using OAEP/SHA-256."""
        
        return private_key.decrypt(
            encrypted_data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
