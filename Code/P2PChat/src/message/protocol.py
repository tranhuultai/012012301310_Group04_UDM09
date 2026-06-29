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

    def __init__(self) -> None:
        """Initialise the protocol handler (stateless)."""

    def create_packet(
        self,
        msg_type: str,
        sender: str,
        payload_content: str,
        crypto: Optional[CryptoHandler] = None,
    ) -> dict[str, Any]:
        """Build a complete packet dict.

        Chat payloads are Fernet-encrypted when *crypto* is provided;
        handshake payloads are sent in plain text.

        Args:
            msg_type: One of the PacketType constants.
            sender: Username of the originating peer.
            payload_content: Plaintext message body.
            crypto: Active CryptoHandler for the session, or None.

        Returns:
            Dict ready for serialisation.
        """
        payload: Any = crypto.encrypt(payload_content) if crypto is not None else payload_content

        return {
            "type":       msg_type,
            "sender":     sender,
            "message_id": str(uuid.uuid4()),
            "payload":    payload,
            "timestamp":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def serialize(self, packet: dict[str, Any]) -> bytes:
        """Serialise *packet* into a 4-byte length-prefixed byte stream.

        Args:
            packet: Dict to serialise as JSON.

        Returns:
            Bytes in the format [4-byte big-endian length][JSON body].
        """
        json_data = json.dumps(packet).encode("utf-8")
        return struct.pack("!I", len(json_data)) + json_data

    def validate_packet(self, packet: dict[str, Any]) -> bool:
        """Return True only if *packet* carries all required fields with correct types.

        Every packet must pass this check before processing.
        Malformed packets are silently dropped — they must never crash the
        receive loop.

        Args:
            packet: Incoming packet dict to validate.

        Returns:
            True if the packet is structurally valid, False otherwise.
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
        """Decrypt packet payload.

        Args:
            packet: Packet dict containing a payload key.
            crypto: Active CryptoHandler, or None for plain payloads.

        Returns:
            Decrypted (or plain) payload string.

        Raises:
            cryptography.fernet.InvalidToken: If decryption fails.
        """
        if crypto is not None:
            return crypto.decrypt(packet["payload"])
        return packet["payload"]

    def validate_and_decrypt(
        self,
        packet: dict[str, Any],
        crypto: Optional[CryptoHandler] = None,
    ) -> Optional[str]:
        """Validate then decrypt a packet.

        Args:
            packet: Packet dict to validate and decrypt.
            crypto: Active CryptoHandler, or None.

        Returns:
            Decrypted payload string, or None on any failure.
        """
        if not self.validate_packet(packet):
            return None
        try:
            return self.decrypt_payload(packet, crypto)
        except (InvalidToken, ValueError) as exc:
            logger.warning("Decryption failed: %s", exc)
            return None

    def receive_exact(self, peer_socket: Any, size: int) -> bytes:
        """Read exactly *size* bytes from *peer_socket*.

        Args:
            peer_socket: Connected socket object.
            size: Number of bytes to read.

        Returns:
            Bytes read, or b"" when the connection is closed.
        """
        received_data = bytearray()
        while len(received_data) < size:
            data = peer_socket.recv(size - len(received_data))
            if not data:
                return b""
            received_data.extend(data)
        return bytes(received_data)

    def receive_packet(self, peer_socket: Any) -> Optional[dict[str, Any]]:
        """Read one framed packet from *peer_socket*.

        Returns the parsed dict, or None if the connection closed or the
        packet was oversized / malformed. Never raises.

        Args:
            peer_socket: Connected socket object.

        Returns:
            Parsed packet dict, or None.
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

            return json.loads(packet_data.decode("utf-8"))

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
