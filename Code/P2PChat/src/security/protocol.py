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
    MESSAGE = "message"
    SYSTEM = "system"
    ERROR = "error"

# Fields every non-handshake packet must carry.
REQUIRED_FIELDS = {"type", "sender", "payload", "timestamp", "message_id"}

# Fields that must be strings.
STRING_FIELDS = {"type", "sender", "message_id", "timestamp"}

class ProtocolHandler:
    """Central packet creation, framing, validation, and socket I/O."""

    HEADER_SIZE = 4  
    MAX_PACKET_SIZE = 1024 * 1024  # 1 MB max packet size to prevent abuse

    def __init__(self, crypto_handler: CryptoHandler) -> None:
        self.crypto_handler = crypto_handler

    def create_packet(self, msg_type: str, sender: str, payload_content: str) -> dict[str, str]:
        """Build and return a complete packet dict. Chat payloads are Fernet-encrypted; handshake payloads are plain."""

        if msg_type in [PacketType.HANDSHAKE, PacketType.HANDSHAKE_ACK]:
            payload = payload_content

        else:
            payload = self.crypto_handler.encrypt(payload_content)
        
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

    def deserialize(self, framed_data: bytes) -> dict[str, Any]:
        """Deserialize a length-prefixed byte stream. Raises ValueError on any structural problem."""

        if len(framed_data) > self.MAX_PACKET_SIZE:
            raise ValueError(f"Packet size exceeds maximum allowed size of {self.MAX_PACKET_SIZE} bytes.")
        
        if len(framed_data) < self.HEADER_SIZE:
            raise ValueError("Data too short to contain a header.")
            
        header = framed_data[:self.HEADER_SIZE]
        payload_length = struct.unpack('!I', header)[0]
        
        expected_length = (self.HEADER_SIZE + payload_length)

        if len(framed_data) < expected_length:
            raise ValueError(f"Incomplete packet. Expected {expected_length} bytes, got {len(framed_data)}.")
            
        json_data = framed_data[self.HEADER_SIZE:expected_length]
        
        try:
            return json.loads(json_data.decode('utf-8'))
        
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Malformed packet: {str(e)}")
    
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
        
        for field in REQUIRED_FIELDS:

            if field not in packet:
                print(f"[WARNING] validate_packet: Missing field '{field}'")
                return False
        
        for field in STRING_FIELDS:

            if not isinstance(packet.get(field), str):
                print(f"[WARNING] validate_packet: field '{field}' must be a string")
                return False

        return True  
    
    def decrypt_payload(self, packet: dict[str, Any]) -> str:
        """Decrypt packet payload. Raises InvalidToken if decryption fails."""

        encrypted_payload = packet["payload"]
        decrypted_message = self.crypto_handler.decrypt(encrypted_payload)

        return decrypted_message
    
    def validate_and_decrypt(self, packet: dict[str, Any]) -> Optional[str]:
        """Convenience: validate then decrypt.  Returns None on any failure."""

        if not self.validate_packet(packet):
            return None
        try:
            decrypted_message = self.decrypt_payload(packet)
            return decrypted_message
        
        except (InvalidToken, ValueError) as exc:
            print(f"[WARNING] Decryption failed: {exc}")
            return None

    def receive_exact( self, peer_socket, size) -> bytes:
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
    
    def receive_packet( self, peer_socket ) -> Optional[dict[str, Any]]:
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

            if not self.validate_packet(packet):
                print("[WARNING] Packet validation failed")
                return None                    

            return packet

        except (json.JSONDecodeError, UnicodeDecodeError, struct.error) as error:
            print(f"[WARNING] receive_packet: malformed data — {error}")
            return None
