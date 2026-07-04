# pylint: disable=protected-access
# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring

import datetime
from typing import cast
import socket

from cryptography.fernet import Fernet

from network.node import (
    P2PNode,
    _MSG_RATE_LIMIT_MAX,
    _MAX_MESSAGE_AGE_SECONDS,
)
from message.protocol import PacketType
from security.rsa_utils import RSAUtils
from config import MAX_PACKET_SIZE, PROTOCOL_VERSION


class FakeSocket:
    """Minimal socket stand-in that records sendall() calls instead of
    touching a real network — mirrors test_node.py's FakeSocket, extended
    with sendall so send_message() can be exercised without a live peer."""

    def __init__(self):
        self.closed = False
        self.sent: list[bytes] = []

    def close(self):
        self.closed = True

    def sendall(self, data: bytes):
        self.sent.append(data)


def create_node():
    return P2PNode(host="127.0.0.1", port=5001, username="Tai")


def _iso_now(offset_seconds: float = 0.0) -> str:
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=offset_seconds)
    return when.isoformat()


# --------------------------------------------------------------------- #
# FLAW #6 — per-peer message rate limiting                              #
# --------------------------------------------------------------------- #

def test_rate_limit_allows_up_to_max():
    node = create_node()

    for _ in range(_MSG_RATE_LIMIT_MAX):
        assert node._check_rate_limit("1.2.3.4:5000") is True


def test_rate_limit_rejects_beyond_max():
    node = create_node()

    for _ in range(_MSG_RATE_LIMIT_MAX):
        node._check_rate_limit("1.2.3.4:5000")

    assert node._check_rate_limit("1.2.3.4:5000") is False


def test_rate_limit_is_per_peer():
    node = create_node()

    for _ in range(_MSG_RATE_LIMIT_MAX):
        node._check_rate_limit("1.2.3.4:5000")

    # A different peer must not be affected by another peer's burst.
    assert node._check_rate_limit("9.9.9.9:5000") is True


def test_remove_peer_clears_rate_limit_state():
    node = create_node()
    sock = cast(socket.socket, FakeSocket())

    node._register_peer("1.2.3.4:5000", sock, True)
    node._check_rate_limit("1.2.3.4:5000")
    assert "1.2.3.4:5000" in node._msg_timestamps

    node._remove_peer(sock)

    assert "1.2.3.4:5000" not in node._msg_timestamps


# --------------------------------------------------------------------- #
# FLAW #7 — replay-window staleness check                               #
# --------------------------------------------------------------------- #

def test_message_not_stale_when_fresh():
    node = create_node()
    packet = {"message_id": "abc123", "timestamp": _iso_now(0)}

    assert node._is_message_stale(packet) is False


def test_message_stale_when_old():
    node = create_node()
    packet = {
        "message_id": "abc123",
        "timestamp": _iso_now(_MAX_MESSAGE_AGE_SECONDS + 1),
    }

    assert node._is_message_stale(packet) is True


def test_message_stale_when_timestamp_malformed():
    node = create_node()
    packet = {"message_id": "abc123", "timestamp": "not-a-timestamp"}

    assert node._is_message_stale(packet) is True


# --------------------------------------------------------------------- #
# FLAW #1 — oversized outgoing message rejected instead of silently     #
# accepted-then-dropped by the receiver                                 #
# --------------------------------------------------------------------- #

def test_send_message_rejects_oversized_payload():
    node = create_node()
    sock = cast(socket.socket, FakeSocket())

    node._register_peer("1.2.3.4:5000", sock, True)
    node.peer_sessions["1.2.3.4:5000"]["state"] = "active"

    # No crypto session — plain-text payload, so its serialized size is
    # predictable and comfortably exceeds MAX_PACKET_SIZE on its own.
    huge_message = "x" * (MAX_PACKET_SIZE + 1000)

    result = node.send_message(huge_message, "1.2.3.4:5000")

    assert result is False
    assert not cast(FakeSocket, sock).sent  # never attempted on the wire


def test_send_message_accepts_normal_payload():
    node = create_node()
    sock = cast(socket.socket, FakeSocket())

    node._register_peer("1.2.3.4:5000", sock, True)
    node.peer_sessions["1.2.3.4:5000"]["state"] = "active"

    result = node.send_message("hello", "1.2.3.4:5000")

    assert result is True
    assert len(cast(FakeSocket, sock).sent) == 1


# --------------------------------------------------------------------- #
# FLAW #11 — handshake protocol version is now validated                #
# --------------------------------------------------------------------- #

def test_handshake_rejects_version_mismatch():
    node = create_node()
    sock = cast(socket.socket, FakeSocket())
    node._register_peer("1.2.3.4:5000", sock, True)

    _, public_key = RSAUtils.generate_key_pair()
    packet = {
        "type":        PacketType.HANDSHAKE,
        "username":    "Peer",
        "version":     "9.9",  # deliberately wrong
        "listen_port": 6000,
        "public_key":  RSAUtils.serialize_public_key(public_key),
    }

    node._handle_handshake(packet, sock)

    # Mismatched version must be treated like any other rejected handshake:
    # the pending session is torn down, not silently accepted.
    assert "1.2.3.4:5000" not in node.peer_sessions
    assert cast(FakeSocket, sock).closed is True


def test_handshake_accepts_matching_version():
    node = create_node()
    sock = cast(socket.socket, FakeSocket())
    node._register_peer("1.2.3.4:5000", sock, True)

    _, public_key = RSAUtils.generate_key_pair()
    packet = {
        "type":        PacketType.HANDSHAKE,
        "username":    "Peer",
        "version":     PROTOCOL_VERSION,
        "listen_port": 6000,
        "public_key":  RSAUtils.serialize_public_key(public_key),
    }

    node._handle_handshake(packet, sock)

    assert "1.2.3.4:5000" in node.peer_sessions
    assert node.peer_sessions["1.2.3.4:5000"]["username"] == "Peer"


# --------------------------------------------------------------------- #
# FLAW #3 — duplicate active session for the same peer_id is closed     #
# synchronously instead of handed off to a detached cleanup thread      #
# --------------------------------------------------------------------- #

def test_duplicate_session_closed_before_handler_returns():
    node = create_node()
    old_sock = cast(socket.socket, FakeSocket())
    new_sock = cast(socket.socket, FakeSocket())

    node._register_peer("1.1.1.1:5000", old_sock, True)
    node.peer_sessions["1.1.1.1:5000"]["peer_id"] = "peerX"
    node.peer_sessions["1.1.1.1:5000"]["state"] = "active"

    node._register_peer("2.2.2.2:5000", new_sock, True)
    node.peer_sessions["2.2.2.2:5000"]["peer_id"] = "peerX"

    private_key, public_key = RSAUtils.generate_key_pair()
    node.private_key = private_key
    node.public_key = public_key
    real_key = Fernet.generate_key()
    encrypted = RSAUtils.encrypt(public_key, real_key)

    packet = {
        "type":    PacketType.SESSION_KEY,
        "payload": encrypted.hex(),
    }

    node._handle_session_key(packet, new_sock)

    # By the time the handler returns, the OLDER session must already be
    # gone — not "eventually, whenever a background thread gets scheduled".
    assert "1.1.1.1:5000" not in node.peer_sessions
    assert cast(FakeSocket, old_sock).closed is True
    assert node.peer_sessions["2.2.2.2:5000"]["state"] == "active"
