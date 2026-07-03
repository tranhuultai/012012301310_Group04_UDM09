"""Protocol handler: packet creation, framing, validation, and socket I/O."""

import datetime
import json
import logging
import struct
import uuid
from typing import Any, Optional

from cryptography.fernet import InvalidToken

from security.crypto import CryptoHandler
from config import MAX_PACKET_SIZE

logger = logging.getLogger(__name__)


class PacketType:
    """Enumeration of all packet type string constants."""

    HANDSHAKE     = "handshake"
    HANDSHAKE_ACK = "handshake_ack"
    SESSION_KEY   = "session_key"
    MESSAGE       = "message"
    SYSTEM        = "system"
    ERROR         = "error"

    # Sent periodically on every active session, independent of chat
    # activity — see network/node.py's heartbeat thread. Its only purpose
    # is to keep data flowing so the receive loop's inactivity timeout
    # never misfires on a peer that's simply idle (not dead). No reply is
    # expected; receiving it is itself what resets the timeout.
    PING          = "ping"

    # ── File transfer ──────────────────────────────────────────────────
    # Workflow (Telegram-style):
    #   Sender   → FILE_META        (metadata only; no data yet)
    #   Receiver → DOWNLOAD_REQUEST (user clicked Download)
    #   Sender   → FILE_START       (begin transfer)
    #   Sender   → FILE_CHUNK × N   (encrypted + Base64 chunks)
    #   Sender   → FILE_COMPLETE    (SHA-256 for integrity check)
    #   Either   → FILE_CANCEL      (abort at any time)
    #   Either   → FILE_ERROR       (unrecoverable error)
    FILE_META         = "file_meta"
    DOWNLOAD_REQUEST  = "download_request"
    FILE_START        = "file_start"
    FILE_CHUNK        = "file_chunk"
    FILE_COMPLETE     = "file_complete"
    FILE_CANCEL       = "file_cancel"
    FILE_ERROR        = "file_error"

    # File packet types that the node passes through without validation.
    FILE_TYPES: frozenset[str] = frozenset({
        "file_meta", "download_request",
        "file_start", "file_chunk", "file_complete",
        "file_cancel", "file_error",
    })


_HANDSHAKE_REQUIRED: frozenset[str] = frozenset({
    "type", "username", "version", "listen_port", "public_key",
})

_HANDSHAKE_ACK_REQUIRED: frozenset[str] = frozenset({
    "type", "status", "public_key",
})

_MESSAGE_REQUIRED: frozenset[str] = frozenset({
    "type", "sender", "payload", "timestamp", "message_id",
})

_SESSION_KEY_REQUIRED: frozenset[str] = frozenset({
    "type", "payload",
})

# Fields in MESSAGE packets that must be strings.
_MESSAGE_STRING_FIELDS: frozenset[str] = frozenset({
    "type", "sender", "message_id", "timestamp",
})


class ProtocolHandler:
    """Central packet creation, framing, validation, and socket I/O."""

    HEADER_SIZE     = 4
    MAX_PACKET_SIZE = MAX_PACKET_SIZE

    def create_packet(
        self,
        msg_type: str,
        sender: str,
        payload_content: str,
        crypto: Optional[CryptoHandler] = None,
    ) -> dict[str, Any]:
        """Build a packet dict — payload is Fernet-encrypted when crypto is given,
        plain text otherwise (handshake packets have no session key yet)."""
        payload: Any = crypto.encrypt(payload_content) if crypto is not None else payload_content

        return {
            "type":       msg_type,
            "sender":     sender,
            "message_id": str(uuid.uuid4()),
            "payload":    payload,
            "timestamp":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def serialize(self, packet: dict[str, Any]) -> bytes:
        """Encode packet as JSON with a 4-byte big-endian length prefix."""
        json_data = json.dumps(packet).encode("utf-8")
        return struct.pack("!I", len(json_data)) + json_data

    def validate_packet(self, packet: dict[str, Any]) -> bool:
        """Check packet has the required fields for its type, with correct types.

        Every packet must pass this before processing. Malformed packets are
        silently dropped — they must never crash the receive loop.
        """
        if not isinstance(packet, dict):
            logger.warning("validate_packet: not a dict")
            return False

        packet_type = packet.get("type")

        if packet_type == PacketType.HANDSHAKE:
            required = _HANDSHAKE_REQUIRED

        elif packet_type == PacketType.HANDSHAKE_ACK:
            required = _HANDSHAKE_ACK_REQUIRED

        elif packet_type == PacketType.MESSAGE:
            required = _MESSAGE_REQUIRED
            for field in _MESSAGE_STRING_FIELDS:
                if not isinstance(packet.get(field), str):
                    logger.warning("validate_packet: '%s' must be a string", field)
                    return False

        elif packet_type == PacketType.SESSION_KEY:
            required = _SESSION_KEY_REQUIRED
            if not isinstance(packet.get("payload"), str):
                logger.warning("validate_packet: 'payload' must be a string")
                return False

        elif packet_type in PacketType.FILE_TYPES:
            # File-transfer packets are validated by TransferManager, not here.
            return True

        elif packet_type == PacketType.PING:
            return True   # no payload — the type field alone is the whole packet

        else:
            logger.warning("validate_packet: unknown type '%s'", packet_type)
            return False

        for field in required:
            if field not in packet:
                logger.warning("validate_packet: missing field '%s'", field)
                return False

        return True

    def decrypt_payload(
        self,
        packet: dict[str, Any],
        crypto: Optional[CryptoHandler] = None,
    ) -> str:
        """Decrypt packet["payload"], or return it as-is if crypto is None."""
        if crypto is not None:
            return crypto.decrypt(packet["payload"])
        return packet["payload"]

    def validate_and_decrypt(
        self,
        packet: dict[str, Any],
        crypto: Optional[CryptoHandler] = None,
    ) -> Optional[str]:
        """Validate then decrypt packet, returning None on any failure."""
        if not self.validate_packet(packet):
            return None
        try:
            return self.decrypt_payload(packet, crypto)
        except (InvalidToken, ValueError) as exc:
            logger.warning("Decryption failed: %s", exc)
            return None

    def receive_exact(self, peer_socket: Any, size: int) -> bytes:
        """Read exactly size bytes, or b"" if the connection closes first."""
        received_data = bytearray()
        while len(received_data) < size:
            data = peer_socket.recv(size - len(received_data))
            if not data:
                return b""
            received_data.extend(data)
        return bytes(received_data)

    def receive_packet(self, peer_socket: Any) -> Optional[dict[str, Any]]:
        """Read one framed packet, or None if closed / oversized / malformed.

        Never raises — the receive loop must survive a bad peer.
        """
        try:
            header = self.receive_exact(peer_socket, self.HEADER_SIZE)
            if not header:
                return None

            (packet_length,) = struct.unpack("!I", header)

            if packet_length > MAX_PACKET_SIZE:
                logger.warning("Oversized packet (%d bytes) — dropping", packet_length)
                return None

            packet_data = self.receive_exact(peer_socket, packet_length)
            if not packet_data:
                return None

            result = json.loads(packet_data.decode("utf-8"))
            if not isinstance(result, dict):
                logger.warning("receive_packet: expected JSON object, got %s", type(result).__name__)
                return None
            return result

        except OSError as exc:
            # WinError 10054 (connection reset) and 10053 (connection aborted)
            # are expected during node shutdown — log at DEBUG, not ERROR.
            if exc.winerror in (10054, 10053) if hasattr(exc, "winerror") else False:
                logger.debug("Socket closed by remote: %s", exc)
            else:
                logger.error("Socket receive failed: %s", exc)
            return None

        except (json.JSONDecodeError, UnicodeDecodeError, struct.error) as exc:
            logger.warning("receive_packet: malformed data — %s", exc)
            return None
