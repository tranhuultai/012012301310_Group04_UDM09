import socket
import threading
from cryptography.fernet import Fernet, InvalidToken
from security.crypto import CryptoHandler
from security.protocol import PacketType, ProtocolHandler
from security.rsa_utils import RSAUtils

HANDSHAKE_TIMEOUT = 5 # Seconds before a pending peer is dropped

class P2PNode:
    def __init__(
        self,
        host: str,
        port: int,
        username: str = "Anonymous",
        on_message=None,
        on_disconnect=None,
        on_connected=None
    ) -> None:
        self.host = host
        self.port = port
        self.username = username

        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.on_connected = on_connected

        self.server_socket: socket.socket | None = None

        # All three dicts are protected by peers_lock.
        self.peers_lock = threading.RLock()
        self.peers: dict[str, socket.socket] = {}
        self.peer_sessions: dict[str, dict] = {}

        #self.crypto_handler = CryptoHandler()
        self.protocol_handler = ProtocolHandler()

        # RSA key pair — used only for session-key exchange.
        self.private_key, self.public_key = (RSAUtils.generate_key_pair())

        self.is_running = False

    def start_server(self) -> None:
        """Start the TCP server."""

        self.server_socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.server_socket.settimeout(1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen()
        self.is_running = True

        print(f"[INFO] Listening on {self.host}:{self.port}")
        
        threading.Thread(
            target=self.accept_connections,
            daemon=True,
            name="AcceptThread"
        ).start()

    def stop_server(self) -> None:
        """Stop the TCP server and close all connections."""
        self.is_running = False

        with self.peers_lock:

            for sock in self.peers.values():

                try:
                    sock.close()

                except OSError:
                    pass

            self.peers.clear()
            self.peer_sessions.clear()

        if self.server_socket is not None:

            try:
                self.server_socket.close()

            except OSError:
                pass

            print("[INFO] Server stopped.")

    # Connection lifecycle
    def accept_connections(self) -> None:
        """Accept incoming peer connections (runs on its own thread)."""
        if self.server_socket is None:
            return

        while self.is_running:
            try:
                client_socket, address = self.server_socket.accept()
                peer_address = f"{address[0]}:{address[1]}"

                print(f"[INFO] Incoming connection from {peer_address}")

                if not self.register_peer(peer_address, client_socket, is_initiator=False):
                    print(f"[INFO] Already connected: {peer_address}")
                    client_socket.close()
                    continue

                self.start_receive_thread(peer_address, client_socket)
                self.schedule_handshake_timeout(peer_address)

            except socket.timeout:
                continue

            except OSError:
                if self.is_running:
                    print("[ERROR] Accept connection failed")
                break

    # Networking interface
    # Used by GUI layer
    def connect_to_peer(self, host: str, port: int) -> bool:
        """Connect to another peer and initiate the handshake."""
        peer_address = f"{host}:{port}"

        with self.peers_lock:
            if peer_address in self.peers:
                print(f"[INFO] Already connected to {peer_address}")
                return False

        try:
            peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            peer_socket.settimeout(10)
            peer_socket.connect((host, port))
            peer_socket.settimeout(None)

            print(f"[INFO] Connected to {peer_address}")

            if not self.register_peer(peer_address, peer_socket, is_initiator=True):
                print(f"[INFO] Already connected to {peer_address}")
                peer_socket.close()
                return False

            if not self.send_handshake(peer_socket):
                self.remove_peer(peer_socket)
                return False

            self.start_receive_thread(peer_address, peer_socket)
            self.schedule_handshake_timeout(peer_address)

            return True

        except OSError as error:
            print(f"[ERROR] Failed to connect to {peer_address}: {error}")
            return False  

    # Message sending and receiving
    def send_message(self, message: str, peer_address: str) -> bool:
        """Send a message to a connected peer."""
        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)
            peer_socket = self.peers.get(peer_address)

        if session is None or session["state"] != "active":
            print(f"[WARNING] Peer not ready: {peer_address}")
            return False

        if peer_socket is None:
            return False

        crypto: CryptoHandler | None = session.get("crypto")

        try:

            packet = self.protocol_handler.create_packet(
                PacketType.MESSAGE,
                self.username,
                message,
                crypto=crypto,
            )
            peer_socket.sendall(self.protocol_handler.serialize(packet))
            return True

        except (BrokenPipeError, OSError) as error:
            print(f"[ERROR] Send failed: {error}")
            self.remove_peer(peer_socket)
            return False

    def receive_messages(self, peer_socket: socket.socket) -> None:
        """Receive loop — dispatches to handlers. Must never raise."""
        while self.is_running:

            try:
                packet = self.protocol_handler.receive_packet(peer_socket)

                if packet is None:
                    # Connection closed cleanly.
                    self.remove_peer(peer_socket)
                    break

                if not isinstance(packet, dict):
                    print("[WARNING] Non-dict packet received — dropping")
                    continue

                msg_type = packet.get("type", "")

                if msg_type == PacketType.HANDSHAKE:
                    self.handle_handshake(packet, peer_socket)

                elif msg_type == PacketType.HANDSHAKE_ACK:
                    self.handle_handshake_ack(packet, peer_socket)

                elif msg_type == PacketType.SESSION_KEY:
                    self.handle_session_key(packet, peer_socket)

                elif msg_type == PacketType.MESSAGE:
                    self.handle_message(packet, peer_socket)

                else:
                    print(f"[WARNING] Unknown packet type '{msg_type}' — dropping")

            except (ConnectionResetError, BrokenPipeError):
                print("[INFO] Connection reset by peer")
                self.remove_peer(peer_socket)
                break

            except (OSError, ValueError, KeyError, InvalidToken) as error:
                if self.is_running:
                    print(f"[ERROR] Receive error: {error}")
                self.remove_peer(peer_socket)
                break

    def broadcast_message(self, message: str) -> None:
        """Send *message* to every active peer."""
        with self.peers_lock:
            active = [
                addr
                for addr, session in self.peer_sessions.items()
                if session["state"] == "active"
            ]

        for peer_address in active:
            self.send_message(message, peer_address)

    # Internal handlers
    def handle_handshake(
        self, packet: dict, peer_socket: socket.socket
    ) -> None:
        
        if not self.protocol_handler.validate_packet(packet):
            print("[WARNING] Malformed handshake — disconnecting peer")
            self.remove_peer(peer_socket)
            return

        peer_address = self.get_peer_address(peer_socket)
        if peer_address is None:
            return

        print(f"[HANDSHAKE] Received from {peer_address} (user: {packet['username']})")

        # Validate the public key before proceeding with the handshake.
        try:
            RSAUtils.load_public_key(packet["public_key"])

        except Exception:
            print("[WARNING] Invalid public key")
            self.remove_peer(peer_socket)
            return
        
        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)

            if session is None:
                return
    
            session["public_key"] = packet["public_key"]
            session["username"] = packet.get("username", "Unknown")

        ack = {
            "type": PacketType.HANDSHAKE_ACK,
            "status": "ok",
            "public_key": RSAUtils.serialize_public_key(self.public_key),
        }
        try:
            peer_socket.sendall(self.protocol_handler.serialize(ack))

        except OSError as error:
            print(f"[ERROR] Failed to send handshake_ack: {error}")
            self.remove_peer(peer_socket)
            return

    def handle_handshake_ack(
        self, packet: dict, peer_socket: socket.socket
    ) -> None:
        
        if not self.protocol_handler.validate_packet(packet):
            print("[WARNING] Malformed handshake_ack — disconnecting peer")
            self.remove_peer(peer_socket)
            return
        
        peer_address = self.get_peer_address(peer_socket)
        if peer_address is None:
            return

        if packet.get("status") != "ok":
            print(f"[WARNING] Handshake_ack rejected by {peer_address}")
            self.remove_peer(peer_socket)
            return

        raw_key = packet.get("public_key", "")

        try:
            RSAUtils.load_public_key(raw_key)

        except Exception:
            print("[WARNING] Invalid RSA public key in handshake_ack — disconnecting")
            self.remove_peer(peer_socket)
            return

        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)
            if session is None:
                return
            
            session["public_key"] = raw_key

        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)

        if session is None:
            return
        
        if session["is_initiator"]:
            self._send_session_key(peer_socket, peer_address)




    def handle_session_key(
        self, packet: dict, peer_socket: socket.socket
    ) -> None:
        """Process the session key sent by the peer.  This is called by the 
        protocol handler after receiving a session_key packet."""

        peer_address = self.get_peer_address(peer_socket)

        if peer_address is None:
            return

        if not self.protocol_handler.validate_packet(packet):
            print(f"[WARNING] Invalid session_key packet from {peer_address}")
            self.remove_peer(peer_socket)
            return

        encrypted_key_hex = packet.get("payload", "")

        if not encrypted_key_hex:
            print(f"[WARNING] Empty session_key payload from {peer_address}")
            self.remove_peer(peer_socket)
            return

        try:
            encrypted_key = bytes.fromhex(encrypted_key_hex)
            fernet_key = RSAUtils.decrypt(self.private_key, encrypted_key)
            crypto = CryptoHandler(key=fernet_key)

        except Exception as exc:
            print(f"[WARNING] Failed to decrypt session key from {peer_address}: {exc}")
            self.remove_peer(peer_socket)
            return

        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)

            if session is None:
                return
            
            session["crypto"] = crypto
            session["state"] = "active"
            count = len(self.peers)

        print(f"[INFO] Session key received — peer active: {peer_address} "
              f"(peers: {count})")

        if self.on_connected is not None:
            self.on_connected(peer_address)


    def handle_message(
        self, packet: dict, peer_socket: socket.socket
    ) -> None:
        
        peer_address = self.get_peer_address(peer_socket)

        if peer_address is None:
            return
        
        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)
        
        if session is None:
            return

        if session["state"] != "active":
            print(
            f"[WARNING] Message from non-active peer {peer_address}"
        )
            return

        if not self.protocol_handler.validate_packet(packet):
            print(f"[WARNING] Invalid message packet from {peer_address} — dropping")
            return
        
        crypto: CryptoHandler | None = session.get("crypto")

        try:
            payload = self.protocol_handler.decrypt_payload(packet, crypto=crypto)

        except (InvalidToken, ValueError) as error:
            print(f"[WARNING] Decryption failed from {peer_address}: {error}")
            return

        if self.on_message is not None:
            self.on_message(payload)
            
        else:
            print(f"[MESSAGE] {peer_address}: {payload}")
    
    # Utility methods
    def get_peer_address(self, peer_socket: socket.socket) -> str | None:
        """Return the address string for *peer_socket*, or None."""

        with self.peers_lock:
            for address, sock in self.peers.items():

                if sock == peer_socket:
                    return address
                
        return None

    def register_peer(self, peer_address: str, sock: socket.socket, is_initiator: bool) -> bool:
        """Atomically register *peer_address*."""
        with self.peers_lock:

            if peer_address in self.peers:
                return False
            
            self.peers[peer_address] = sock
            self.peer_sessions[peer_address] = {
                "state": "pending",
                "public_key": None,
                "session_key": None,
                "crypto": None,
                "username": None,
                "is_initiator": is_initiator
            }

            count = len(self.peers)

        print(f"[INFO] Peer registered: {peer_address} — active peers: {count}")
        return True

    def set_peer_state(self, peer_address: str, state: str) -> None:
        with self.peers_lock:

            session = self.peer_sessions.get(peer_address)

            if session is not None:
                session["state"] = state

    def remove_peer(self, peer_socket: socket.socket) -> None:
        peer_address = self.get_peer_address(peer_socket)
        remaining_peers = 0

        with self.peers_lock:

            if peer_address is not None:
                self.peers.pop(peer_address, None)
                self.peer_sessions.pop(peer_address, None)
                remaining_peers = len(self.peers)

        try:
            peer_socket.close()

        except OSError:
            pass

        if peer_address is not None:

            print(f"[INFO] Peer disconnected: {peer_address} — remaining: {remaining_peers}")

            if self.on_disconnect is not None:
                self.on_disconnect(peer_address)

    def start_receive_thread(self, peer_address: str, sock: socket.socket) -> None:
        threading.Thread(
            target=self.receive_messages,
            args=(sock,),
            daemon=True,
            name=f"Recv-{peer_address}",
        ).start()

    def send_handshake(self, peer_socket: socket.socket) -> bool:

        handshake = {
            "type": PacketType.HANDSHAKE,
            "username": self.username,
            "version": "1.0",
            "public_key": RSAUtils.serialize_public_key(self.public_key),
        }

        try:
            peer_socket.sendall(self.protocol_handler.serialize(handshake))
            return True
        
        except (BrokenPipeError, OSError) as error:
            print(f"[ERROR] Failed to send handshake: {error}")
            return False   

    def schedule_handshake_timeout(self, peer_address: str) -> None:
        """Disconnect *peer_address* if still pending after the timeout."""
        def check_timeout() -> None:

            peer_socket = None

            with self.peers_lock:

                session = self.peer_sessions.get(peer_address)

                if session is None or session["state"] != "pending":
                    return
                
                peer_socket = self.peers.get(peer_address)
            
            if peer_socket is None:
                return

            print(f"[WARNING] Handshake timeout — disconnecting {peer_address}")
            self.remove_peer(peer_socket)

        timer = threading.Timer(HANDSHAKE_TIMEOUT, check_timeout)
        timer.daemon = True
        timer.start()

    def _send_session_key(self, peer_socket: socket.socket, peer_address: str) -> None:
        """Generate a session key, encrypt it with the peer's RSA public key, and send it."""

        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)

            if session is None:
                return
            
            raw_peer_key = session.get("public_key")

        if not raw_peer_key:
            print(f"[WARNING] No peer public key for {peer_address} — cannot send session key")
            return

        try:
            peer_pub = RSAUtils.load_public_key(raw_peer_key)

        except Exception as exc:
            print(f"[ERROR] Cannot load peer public key for {peer_address}: {exc}")
            self.remove_peer(peer_socket)
            return

        # Generate a fresh Fernet key for this direction.
        fernet_key = Fernet.generate_key()
        try:
            encrypted_key = RSAUtils.encrypt(peer_pub, fernet_key)

        except Exception as exc:
            print(f"[ERROR] RSA encrypt failed for {peer_address}: {exc}")
            self.remove_peer(peer_socket)
            return

        session_key_packet = {
            "type": PacketType.SESSION_KEY,
            "payload": encrypted_key.hex(),
        }

        try:
            peer_socket.sendall(self.protocol_handler.serialize(session_key_packet))

        except (BrokenPipeError, OSError) as exc:
            print(f"[ERROR] Failed to send session key to {peer_address}: {exc}")
            self.remove_peer(peer_socket)
            return

        crypto = CryptoHandler(key=fernet_key)

        became_active = False

        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)

            if session is not None:
                became_active = session.get("state") != "active"
                session["session_key"] = fernet_key
                session["crypto"] = crypto
                session["state"] = "active"

        print(f"[INFO] Session key sent to {peer_address}")

        if became_active and self.on_connected is not None:
            self.on_connected(peer_address)
