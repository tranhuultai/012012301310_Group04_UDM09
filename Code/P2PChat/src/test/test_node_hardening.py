# pylint: disable=protected-access
# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# WHY changed: added regression tests for node.py's naive-timestamp crash,
# blocked-peer file-packet bypass, discovery spoofing, and lock-held-during-
# callback fixes.

import datetime
import threading
import time
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
from security.jwt_handler import JWTHandler
from identity.identity_manager import generate_peer_id, generate_fingerprint
from trust.trust_state import TrustState
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
# FLAW #1 — oversized outgoing message rejected, not silently dropped   #
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
# FLAW #3 — duplicate peer_id session closed synchronously, not detached #
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


# --------------------------------------------------------------------- #
# FLAW — _is_message_stale must not crash on an offset-less timestamp   #
# --------------------------------------------------------------------- #

def test_message_stale_when_timestamp_has_no_timezone():
    node = create_node()
    # Valid ISO 8601 but no UTC offset — used to raise an uncaught TypeError
    # (naive vs aware datetime), killing the receive thread.
    packet = {"message_id": "abc123", "timestamp": "2026-01-01T00:00:00"}

    assert node._is_message_stale(packet) is True


# --------------------------------------------------------------------- #
# FLAW — blocked peers must not reach TransferManager either            #
# --------------------------------------------------------------------- #

def test_handle_file_packet_dropped_for_blocked_peer():
    node = create_node()
    sock = cast(socket.socket, FakeSocket())
    received: list[dict] = []
    node.on_file_packet = lambda packet, crypto, sender_pid: received.append(packet)

    node._register_peer("1.2.3.4:5000", sock, True)
    node.peer_sessions["1.2.3.4:5000"]["peer_id"] = "peerX"
    node.tofu.store.add_peer("peerX", "aa:bb", TrustState.BLOCKED)

    node._handle_file_packet({"type": PacketType.FILE_META}, sock)

    assert not received


def test_handle_file_packet_delivered_for_normal_peer():
    node = create_node()
    sock = cast(socket.socket, FakeSocket())
    received: list[dict] = []
    node.on_file_packet = lambda packet, crypto, sender_pid: received.append(packet)

    node._register_peer("1.2.3.4:5000", sock, True)
    node.peer_sessions["1.2.3.4:5000"]["peer_id"] = "peerY"

    node._handle_file_packet({"type": PacketType.FILE_META}, sock)

    assert len(received) == 1


# --------------------------------------------------------------------- #
# FLAW — discovery must reject peer_id/fingerprint spoofing a public_key #
# --------------------------------------------------------------------- #

def _discovery_packet(priv_pem: str, pub_pem: str, peer_id: str, fingerprint: str) -> dict:
    token = JWTHandler.create_identity_token(
        peer_id=peer_id, username="Peer", fingerprint=fingerprint,
        private_key_pem=priv_pem,
    )
    return {
        "type":           "discovery",
        "instance_id":    "other-instance",
        "peer_id":        peer_id,
        "username":       "Peer",
        "fingerprint":    fingerprint,
        "public_key":     pub_pem,
        "identity_token": token,
        "port":           6000,
        "status":         "online",
    }


def test_discovered_peer_rejected_when_identity_spoofed():
    node = create_node()
    attacker_priv, attacker_pub = RSAUtils.generate_key_pair()
    attacker_priv_pem = RSAUtils.serialize_private_key(attacker_priv)
    attacker_pub_pem  = RSAUtils.serialize_public_key(attacker_pub)

    victim_peer_id     = "victim-peer-id"
    victim_fingerprint = "VI:CT:IM"

    # The JWT is validly signed (by the attacker's own key), and its claims
    # agree with the packet's own fields — but neither actually derives from
    # attacker_pub_pem, which is the whole point of the attack.
    packet = _discovery_packet(
        attacker_priv_pem, attacker_pub_pem, victim_peer_id, victim_fingerprint)

    node._handle_discovered_peer(packet, ("9.9.9.9", 6000))

    assert victim_peer_id not in node.discovered_peers


def test_discovered_peer_accepted_when_consistent():
    node = create_node()
    priv, pub = RSAUtils.generate_key_pair()
    priv_pem = RSAUtils.serialize_private_key(priv)
    pub_pem  = RSAUtils.serialize_public_key(pub)
    real_peer_id     = generate_peer_id(pub_pem)
    real_fingerprint = generate_fingerprint(pub_pem)

    packet = _discovery_packet(priv_pem, pub_pem, real_peer_id, real_fingerprint)

    node._handle_discovered_peer(packet, ("9.9.9.9", 6000))

    assert real_peer_id in node.discovered_peers


# FLAW — on_peer_discovered must fire after discovery_lock is released.
# RLock re-acquire can't detect this (reentrant), so these tests check
# RLock()._is_owned() instead: True only while still inside the `with`.

def test_new_peer_callback_fires_after_lock_released():
    node = create_node()
    lock_held: list[bool] = []
    node.on_peer_discovered = lambda peer_id, info: lock_held.append(
        node.discovery_lock._is_owned())  # pylint: disable=protected-access

    priv, pub = RSAUtils.generate_key_pair()
    priv_pem, pub_pem = RSAUtils.serialize_private_key(priv), RSAUtils.serialize_public_key(pub)
    peer_id, fingerprint = generate_peer_id(pub_pem), generate_fingerprint(pub_pem)
    packet = _discovery_packet(priv_pem, pub_pem, peer_id, fingerprint)

    node._handle_discovered_peer(packet, ("9.9.9.9", 6000))

    assert lock_held == [False]


def test_changed_peer_callback_fires_after_lock_released():
    node = create_node()
    priv, pub = RSAUtils.generate_key_pair()
    priv_pem, pub_pem = RSAUtils.serialize_private_key(priv), RSAUtils.serialize_public_key(pub)
    peer_id, fingerprint = generate_peer_id(pub_pem), generate_fingerprint(pub_pem)

    # First contact — registers the peer, doesn't yet exercise the
    # "existing entry changed" branch this test targets.
    node._handle_discovered_peer(
        _discovery_packet(priv_pem, pub_pem, peer_id, fingerprint), ("9.9.9.9", 6000))

    lock_held: list[bool] = []
    node.on_peer_discovered = lambda pid, info: lock_held.append(
        node.discovery_lock._is_owned())  # pylint: disable=protected-access

    # Same peer, different username — trips the dirty-check so `changed`
    # is True and the callback actually fires this time.
    packet2 = _discovery_packet(priv_pem, pub_pem, peer_id, fingerprint)
    packet2["username"] = "RenamedPeer"
    node._handle_discovered_peer(packet2, ("9.9.9.9", 6000))

    assert lock_held == [False]


def test_cleanup_expired_peers_callback_fires_after_lock_released():
    node = create_node()
    lock_held: list[bool] = []
    node.on_peer_discovered = lambda peer_id, info: lock_held.append(
        node.discovery_lock._is_owned())  # pylint: disable=protected-access

    node.discovered_peers["stale-peer"] = {
        "status": "online", "connected": True,
        "last_seen": time.time() - 9999, "username": "Ghost",
    }
    node.is_running = True
    t = threading.Thread(target=node._cleanup_expired_peers, daemon=True)
    t.start()
    t.join(timeout=2)
    node.is_running = False

    assert lock_held == [False]
