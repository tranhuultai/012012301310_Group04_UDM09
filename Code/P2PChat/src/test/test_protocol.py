import io
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from security.crypto import CryptoHandler
from security.protocol import PacketType, ProtocolHandler
from security.rsa_utils import RSAUtils


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_socket(data: bytes):
    """Wrap bytes in a minimal file-like object that acts like a socket."""
    buf = io.BytesIO(data)
    buf.recv = lambda n: buf.read(n)  # type: ignore[attr-defined]
    return buf


def _make_protocol() -> ProtocolHandler:
    return ProtocolHandler()


def _make_crypto() -> CryptoHandler:
    return CryptoHandler()


# ── Protocol framing ──────────────────────────────────────────────────────────

def test_round_trip_plaintext() -> None:
    """Message survives create → serialize → receive_packet → plaintext read."""
    protocol = _make_protocol()
    packet = protocol.create_packet(PacketType.MESSAGE, "Tai", "Hello World")
    data = protocol.serialize(packet)
    received = protocol.receive_packet(_fake_socket(data))

    assert received is not None
    assert received["payload"] == "Hello World"
    print("[OK] test_round_trip_plaintext")


def test_round_trip_encrypted() -> None:
    """Message survives create (encrypted) → serialize → receive → decrypt."""
    protocol = _make_protocol()
    crypto = _make_crypto()
    packet = protocol.create_packet(PacketType.MESSAGE, "Tai", "Secret", crypto=crypto)
    data = protocol.serialize(packet)
    received = protocol.receive_packet(_fake_socket(data))

    assert received is not None
    plaintext = protocol.decrypt_payload(received, crypto=crypto)
    assert plaintext == "Secret"
    print("[OK] test_round_trip_encrypted")


def test_wrong_key_raises() -> None:
    """Decrypting with the wrong key raises InvalidToken — not silently wrong."""
    from cryptography.fernet import InvalidToken
    protocol = _make_protocol()
    sender_crypto = _make_crypto()
    wrong_crypto = _make_crypto()

    packet = protocol.create_packet(PacketType.MESSAGE, "A", "Hi", crypto=sender_crypto)
    data = protocol.serialize(packet)
    received = protocol.receive_packet(_fake_socket(data))

    assert received is not None

    try:
        protocol.decrypt_payload(received, crypto=wrong_crypto)
        assert False, "Expected InvalidToken"
    except InvalidToken:
        pass
    print("[OK] test_wrong_key_raises")


def test_validate_rejects_missing_fields() -> None:
    """validate_packet rejects an incomplete MESSAGE packet."""
    protocol = _make_protocol()
    assert not protocol.validate_packet({"type": "message"})
    print("[OK] test_validate_rejects_missing_fields")


def test_validate_accepts_valid_message() -> None:
    protocol = _make_protocol()
    crypto = _make_crypto()
    packet = protocol.create_packet(PacketType.MESSAGE, "A", "hello", crypto=crypto)
    assert protocol.validate_packet(packet)
    print("[OK] test_validate_accepts_valid_message")


def test_validate_accepts_valid_handshake() -> None:
    protocol = _make_protocol()
    _, pub = RSAUtils.generate_key_pair()
    packet = {
        "type": PacketType.HANDSHAKE,
        "username": "Alice",
        "version": "1.0",
        "public_key": RSAUtils.serialize_public_key(pub),
    }
    assert protocol.validate_packet(packet)
    print("[OK] test_validate_accepts_valid_handshake")


def test_validate_accepts_valid_handshake_ack() -> None:
    protocol = _make_protocol()
    _, pub = RSAUtils.generate_key_pair()
    packet = {
        "type": PacketType.HANDSHAKE_ACK,
        "status": "ok",
        "public_key": RSAUtils.serialize_public_key(pub),
    }
    assert protocol.validate_packet(packet)
    print("[OK] test_validate_accepts_valid_handshake_ack")


def test_validate_accepts_valid_session_key() -> None:
    protocol = _make_protocol()
    packet = {
        "type": PacketType.SESSION_KEY,
        "payload": "00ff",
    }
    assert protocol.validate_packet(packet)
    print("[OK] test_validate_accepts_valid_session_key")


def test_validate_rejects_bad_session_key_payload() -> None:
    protocol = _make_protocol()
    assert not protocol.validate_packet({"type": PacketType.SESSION_KEY})
    assert not protocol.validate_packet({"type": PacketType.SESSION_KEY, "payload": b"bad"})
    print("[OK] test_validate_rejects_bad_session_key_payload")

def test_oversized_packet_dropped() -> None:
    """receive_packet returns None for declared size > MAX_PACKET_SIZE."""
    protocol = _make_protocol()
    fake_data = struct.pack("!I", protocol.MAX_PACKET_SIZE + 1) + b"x" * 100
    result = protocol.receive_packet(_fake_socket(fake_data))
    assert result is None
    print("[OK] test_oversized_packet_dropped")


def test_receive_packet_does_not_validate() -> None:
    """Fix C2: receive_packet returns the packet even if validate_packet would fail."""
    protocol = _make_protocol()
    # Build a structurally valid frame but semantically invalid packet.
    bad = {"type": "message"}  # missing required MESSAGE fields
    data = protocol.serialize(bad)
    result = protocol.receive_packet(_fake_socket(data))
    # receive_packet should return the dict; validation is the handler's job.
    assert result == bad
    print("[OK] test_receive_packet_does_not_validate")


def test_receive_packet_handles_oserror() -> None:
    """Fix C3: receive_packet returns None instead of raising on socket error."""
    protocol = _make_protocol()

    class BrokenSocket:
        def recv(self, n):
            raise OSError("connection reset")

    result = protocol.receive_packet(BrokenSocket())
    assert result is None
    print("[OK] test_receive_packet_handles_oserror")


# ── RSA key exchange ──────────────────────────────────────────────────────────

def test_rsa_encrypt_decrypt_round_trip() -> None:
    """RSAUtils encrypt/decrypt round-trip for a Fernet key."""
    from cryptography.fernet import Fernet
    priv, pub = RSAUtils.generate_key_pair()
    fernet_key = Fernet.generate_key()
    encrypted = RSAUtils.encrypt(pub, fernet_key)
    decrypted = RSAUtils.decrypt(priv, encrypted)
    assert decrypted == fernet_key
    print("[OK] test_rsa_encrypt_decrypt_round_trip")


def test_load_public_key_rejects_garbage() -> None:
    """RSAUtils.load_public_key raises on invalid input."""
    try:
        RSAUtils.load_public_key("not a PEM key")
        assert False, "Expected exception"
    except Exception:
        pass
    print("[OK] test_load_public_key_rejects_garbage")


# ── Node integration ──────────────────────────────────────────────────────────

def test_register_peer_atomic() -> None:
    """Fix C4: register_peer returns False on second call for same address."""
    from node.core import P2PNode
    import socket as _socket

    node = P2PNode(host="127.0.0.1", port=19999)

    # Use a dummy socket — we never send on it.
    a, b = _socket.socketpair()
    try:
        assert node.register_peer("127.0.0.1:9000", a, True) is True
        assert node.register_peer("127.0.0.1:9000", b, False) is False
        # Only the first socket should be in the registry.
        with node.peers_lock:
            assert node.peers["127.0.0.1:9000"] is a
    finally:
        a.close()
        b.close()

    print("[OK] test_register_peer_atomic")


def test_send_message_returns_false_when_not_active() -> None:
    """Fix G1: send_message returns False when peer state is not active."""
    from node.core import P2PNode
    import socket as _socket

    node = P2PNode(host="127.0.0.1", port=19998)
    a, b = _socket.socketpair()
    try:
        node.register_peer("127.0.0.1:9001", a, True)
        # State is "pending" — not active.
        result = node.send_message("hello", "127.0.0.1:9001")
        assert result is False
    finally:
        a.close()
        b.close()

    print("[OK] test_send_message_returns_false_when_not_active")


def test_send_session_key_marks_initiator_connected() -> None:
    """Initiator must trigger on_connected after sending a valid session key."""
    from node.core import P2PNode
    import socket as _socket

    connected = []
    node = P2PNode(
        host="127.0.0.1",
        port=19996,
        on_connected=connected.append,
    )
    _peer_priv, peer_pub = RSAUtils.generate_key_pair()
    a, b = _socket.socketpair()
    try:
        node.register_peer("127.0.0.1:9003", a, True)
        with node.peers_lock:
            node.peer_sessions["127.0.0.1:9003"]["public_key"] = (
                RSAUtils.serialize_public_key(peer_pub)
            )

        node._send_session_key(a, "127.0.0.1:9003")

        with node.peers_lock:
            session = node.peer_sessions["127.0.0.1:9003"]
            assert session["state"] == "active"
            assert session["crypto"] is not None

        assert connected == ["127.0.0.1:9003"]
    finally:
        node.remove_peer(a)
        b.close()

    print("[OK] test_send_session_key_marks_initiator_connected")

def test_handshake_timeout_disconnects_pending_peer() -> None:
    """Pending peers are disconnected after HANDSHAKE_TIMEOUT seconds."""
    import socket as _socket
    from node import core as _core

    original_timeout = _core.HANDSHAKE_TIMEOUT
    _core.HANDSHAKE_TIMEOUT = 0.1  # speed up the test

    disconnected = threading.Event()

    def on_disconnect(_addr):
        disconnected.set()

    node = _core.P2PNode(
        host="127.0.0.1",
        port=19997,
        on_disconnect=on_disconnect,
    )
    a, b = _socket.socketpair()
    try:
        node.register_peer("127.0.0.1:9002", a, True)
        node.schedule_handshake_timeout("127.0.0.1:9002")
        assert disconnected.wait(timeout=2.0), "Timeout did not fire"
    finally:
        _core.HANDSHAKE_TIMEOUT = original_timeout
        b.close()

    print("[OK] test_handshake_timeout_disconnects_pending_peer")


# ── Validation edge cases ─────────────────────────────────────────────────────

def test_validate_ip_rejects_reserved() -> None:
    """Fix W3: validate_ip rejects 0.0.0.0 and 255.255.255.255."""
    from gui.validation import validate_ip
    assert not validate_ip("0.0.0.0")
    assert not validate_ip("255.255.255.255")
    assert validate_ip("192.168.1.1")
    assert not validate_ip("not_an_ip")
    assert not validate_ip("127.1")
    assert not validate_ip("1")
    print("[OK] test_validate_ip_rejects_reserved")


if __name__ == "__main__":
    test_round_trip_plaintext()
    test_round_trip_encrypted()
    test_wrong_key_raises()
    test_validate_rejects_missing_fields()
    test_validate_accepts_valid_message()
    test_validate_accepts_valid_handshake()
    test_validate_accepts_valid_handshake_ack()
    test_validate_accepts_valid_session_key()
    test_validate_rejects_bad_session_key_payload()
    test_oversized_packet_dropped()
    test_receive_packet_does_not_validate()
    test_receive_packet_handles_oserror()
    test_rsa_encrypt_decrypt_round_trip()
    test_load_public_key_rejects_garbage()
    test_register_peer_atomic()
    test_send_message_returns_false_when_not_active()
    test_send_session_key_marks_initiator_connected()
    test_handshake_timeout_disconnects_pending_peer()
    test_validate_ip_rejects_reserved()
    print("\nAll tests passed.")
