from security.crypto import CryptoHandler
from security.protocol import ProtocolHandler, PacketType


def test_round_trip() -> None:
    """Message survives create → serialize → receive_packet → decrypt."""
    crypto = CryptoHandler()
    protocol = ProtocolHandler(crypto)

    packet = protocol.create_packet(PacketType.MESSAGE, "Tai", "Hello World")
    data = protocol.serialize(packet)

    # Simulate socket by wrapping bytes in a minimal file-like object.
    import io
    fake_socket = io.BytesIO(data)
    fake_socket.recv = lambda n: fake_socket.read(n)  # type: ignore[attr-defined]

    received = protocol.receive_packet(fake_socket)
    assert received is not None, "receive_packet returned None"
    assert protocol.validate_packet(received), "validate_packet failed"

    plaintext = protocol.decrypt_payload(received)
    assert plaintext == "Hello World", f"Expected 'Hello World', got {plaintext!r}"
    print(f"[OK] round_trip: {plaintext!r}")


def test_validate_rejects_missing_fields() -> None:
    """validate_packet rejects packets with missing required fields."""
    crypto = CryptoHandler()
    protocol = ProtocolHandler(crypto)

    bad = {"type": "message"}
    assert not protocol.validate_packet(bad), "Expected False for incomplete packet"
    print("[OK] validate_packet correctly rejected incomplete packet")


def test_oversized_packet_dropped() -> None:
    """receive_packet returns None for packets exceeding MAX_PACKET_SIZE."""
    import struct, io
    crypto = CryptoHandler()
    protocol = ProtocolHandler(crypto)

    oversized_length = protocol.MAX_PACKET_SIZE + 1
    fake_data = struct.pack("!I", oversized_length) + b"x" * 100
    fake_socket = io.BytesIO(fake_data)
    fake_socket.recv = lambda n: fake_socket.read(n)  # type: ignore[attr-defined]

    result = protocol.receive_packet(fake_socket)
    assert result is None, "Expected None for oversized packet"
    print("[OK] oversized packet correctly dropped")


if __name__ == "__main__":
    test_round_trip()
    test_validate_rejects_missing_fields()
    test_oversized_packet_dropped()
