"""Tests for RSAUtils — key generation, encrypt/decrypt, serialization."""
from security.rsa_utils import RSAUtils


def test_generate_key_pair():
    """Generated key pair must return non-None private and public keys."""
    private_key, public_key = RSAUtils.generate_key_pair()
    assert private_key is not None
    assert public_key is not None


def test_rsa_encrypt_decrypt_round_trip():
    """Encrypting then decrypting must recover the original plaintext."""
    private_key, public_key = RSAUtils.generate_key_pair()
    plaintext = b"hello"
    encrypted = RSAUtils.encrypt(public_key, plaintext)
    decrypted = RSAUtils.decrypt(private_key, encrypted)
    assert decrypted == plaintext


def test_public_key_serialization():
    """Public key round-tripped through PEM must load without error."""
    _, public_key = RSAUtils.generate_key_pair()
    pem = RSAUtils.serialize_public_key(public_key)
    loaded = RSAUtils.load_public_key(pem)
    assert loaded is not None


def test_private_key_serialization():
    """Private key round-tripped through PEM must load without error."""
    private_key, _ = RSAUtils.generate_key_pair()
    pem = RSAUtils.serialize_private_key(private_key)
    loaded = RSAUtils.load_private_key(pem)
    assert loaded is not None
