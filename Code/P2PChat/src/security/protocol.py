import datetime
import json
import struct
import uuid
from typing import Any, Optional
from cryptography.fernet import InvalidToken
from security.crypto import CryptoHandler

class PacketType:
    HANDSHAKE = "handshake"
    HANDSHAKE_ACK = "handshake_ack"
    SESSION_KEY = "session_key" # Sprint 3: RSA-wrapped Fernet key exchange
    MESSAGE = "message"
    SYSTEM = "system"
    ERROR = "error"

_HANDSHAKE_REQUIRED: frozenset[str] = frozenset({
    "type", "username", "version", "public_key",
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

_MESSAGE_STRING_FIELDS: frozenset[str] = frozenset({
    "type", "sender", "message_id", "timestamp",
})

class ProtocolHandler:
    """Central packet creation, framing, validation, and socket I/O."""

    HEADER_SIZE = 4  
    MAX_PACKET_SIZE = 1024 * 1024  # 1 MB max packet size to prevent abuse

    def __init__(self) -> None:
        # No state needed for now, but this is where we would store protocol version, supported features, etc.
        pass

    def create_packet(self, msg_type: str, sender: str, payload_content: str, crypto: Optional[CryptoHandler] = None) -> dict[str, Any]:
        """Build and return a complete packet dict. Chat payloads are Fernet-encrypted; handshake payloads are plain."""

        if crypto is not None:
            payload: Any = crypto.encrypt(payload_content)
        else:
            payload = payload_content
        
        packet = {
            "type": msg_type,
            "sender": sender,
            "message_id": str(uuid.uuid4()),
            "payload": payload,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        return packet

    #serialization and deserialization methods for framing packets with a length header
    def serialize(self, packet: dict[str, Any]) -> bytes:
        """Serialize *packet* into a 4-byte length-prefixed byte stream."""
        #Format: [Header][json_payload

        json_data = json.dumps(packet).encode('utf-8')
        return struct.pack("!I", len(json_data)) + json_data
    
    # Validation method to ensure incoming packets have the required structure and fields   
    def validate_packet(self, packet: dict[str, Any]) -> bool:
        """Return True only if *packet* carries all required fields with correct types.

        Every packet must pass this check before processing.
        Malformed packets are silently dropped — they must never crash the
        receive loop.
        """

        if not isinstance(packet, dict):
            print("[WARNING] validate_packet: not a dict")
            return False
        
        packet_type = packet.get("type")

        if packet_type == PacketType.HANDSHAKE:
            required = _HANDSHAKE_REQUIRED

        elif packet_type == PacketType.HANDSHAKE_ACK:
            required = _HANDSHAKE_ACK_REQUIRED
        
        elif packet_type == PacketType.MESSAGE:
            required = _MESSAGE_REQUIRED
            # Also enforce string types on MESSAGE fields.

            for field in _MESSAGE_STRING_FIELDS:

                if not isinstance(packet.get(field), str):
                    print(f"[WARNING] validate_packet: '{field}' must be a string")
                    return False

            if not isinstance(packet.get("payload"), str):
                print("[WARNING] validate_packet: 'payload' must be a string")
                return False

        elif packet_type == PacketType.SESSION_KEY:
            required = _SESSION_KEY_REQUIRED

            if not isinstance(packet.get("payload"), str):
                print("[WARNING] validate_packet: 'payload' must be a string")
                return False
                
        else:
            # Unknown or future packet type — drop it.
            print(f"[WARNING] validate_packet: unknown type '{packet_type}'")
            return False

        for field in required:

            if field not in packet:
                print(f"[WARNING] validate_packet: missing field '{field}'")
                return False

        return True
    
    def decrypt_payload(self, packet: dict[str, Any], crypto: Optional[CryptoHandler] = None) -> str:
        """Decrypt packet payload. Raises InvalidToken if decryption fails."""

        if crypto is not None:
            return crypto.decrypt(packet["payload"])
        
        return packet["payload"]
    
    def validate_and_decrypt(self, packet: dict[str, Any], crypto: Optional[CryptoHandler] = None) -> Optional[str]:
        """Convenience: validate then decrypt.  Returns None on any failure."""

        if not self.validate_packet(packet):
            return None
        try:
            decrypted_message = self.decrypt_payload(packet, crypto)
            return decrypted_message
        
        except (InvalidToken, ValueError) as exc:
            print(f"[WARNING] Decryption failed: {exc}")
            return None

    def receive_exact( self, peer_socket: Any, size: int) -> bytes:
        """Read exactly *size* bytes from *peer_socket*.

        Returns b"" when the connection is closed.
        """
        received_data = bytearray()

        while len(received_data) < size:
            data = peer_socket.recv(size - len(received_data))

            if not data:
                return b""
            
            received_data.extend(data)

        return bytes(received_data)
    
    def receive_packet( self, peer_socket: Any ) -> Optional[dict[str, Any]]:
        """Read one framed packet from *peer_socket*.

        Returns the parsed dict, or None if the connection closed or the
        packet was oversized / malformed.  Never raises.
        """
        try:
            header = self.receive_exact(peer_socket, self.HEADER_SIZE)
            if not header:
                return None

            (packet_length,) = struct.unpack("!I", header)

            if packet_length > self.MAX_PACKET_SIZE:
                print(
                    f"[WARNING] Oversized packet ({packet_length} bytes) — dropping"
                )
                return None

            packet_data = self.receive_exact(peer_socket, packet_length)

            if not packet_data:
                return None
            
            packet = json.loads(packet_data.decode("utf-8"))

            return packet

        except OSError as error:
            print(f"[ERROR] Socket receive failed: {error}")
            return None

        except (json.JSONDecodeError, UnicodeDecodeError, struct.error) as error:
            print(f"[WARNING] receive_packet: malformed data — {error}")
            return None
        
        
