import socket
import threading
from cryptography.fernet import InvalidToken
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
        on_disconnect=None
    ) -> None:
        self.host = host
        self.port = port
        self.username = username

        self.on_message = on_message
        self.on_disconnect = on_disconnect

        self.server_socket: socket.socket | None = None

        # All three dicts are protected by peers_lock.
        self.peers_lock = threading.Lock()
        self.peers: dict[str, socket.socket] = {}
        self.peer_sessions: dict[str, dict] = {}

        self.crypto_handler = CryptoHandler()
        self.protocol_handler = ProtocolHandler(self.crypto_handler)

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
            daemon=True
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

                with self.peers_lock:
                    already = peer_address in self.peers

                if already:
                    print(f"[INFO] Already connected: {peer_address}")
                    client_socket.close()
                    continue

                self.register_peer(peer_address, client_socket)
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

            self.register_peer(peer_address, peer_socket)
            self.send_handshake(peer_socket)
            self.start_receive_thread(peer_address, peer_socket)
            self.schedule_handshake_timeout(peer_address)

            return True

        except OSError as error:
            print(f"[ERROR] Failed to connect to {peer_address}: {error}")
            return False  

    # Message sending and receiving
    def send_message(self, message: str | dict, peer_address: str) -> None:
        """Send a message to a connected peer."""
        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)
            peer_socket = self.peers.get(peer_address)

        if session is None or session["state"] != "active":
            print(f"[WARNING] Peer not ready: {peer_address}")
            return

        if peer_socket is None:
            return

        try:

            if isinstance(message, str):
                packet = self.protocol_handler.create_packet(
                    PacketType.MESSAGE,
                    self.username,
                    message,
                )

            else:
                packet = message

            peer_socket.sendall(self.protocol_handler.serialize(packet))

        except (BrokenPipeError, OSError) as error:
            print(f"[ERROR] Send failed: {error}")
            self.remove_peer(peer_socket)

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
        required = {"type", "username", "version", "public_key"}
        if not required.issubset(packet):
            print("[WARNING] Malformed handshake — disconnecting peer")
            self.remove_peer(peer_socket)
            return

        peer_address = self.get_peer_address(peer_socket)
        if peer_address is None:
            return

        print(f"[HANDSHAKE] Received from {peer_address} (user: {packet['username']})")

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

        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)

            if session is not None:
                session["handshake_complete"] = True

        self.set_peer_state(peer_address, "active")
        print(f"[INFO] Peer active: {peer_address}")

    def handle_handshake_ack(
        self, packet: dict, peer_socket: socket.socket
    ) -> None:
        peer_address = self.get_peer_address(peer_socket)
        if peer_address is None:
            return

        if packet.get("status") != "ok":
            print(f"[WARNING] Handshake_ack rejected by {peer_address}")
            self.remove_peer(peer_socket)
            return

        with self.peers_lock:
            session = self.peer_sessions.get(peer_address)
            if session is None:
                return
            
            if "public_key" in packet:
                session["public_key"] = packet["public_key"]

            session["handshake_complete"] = True

        self.set_peer_state(peer_address, "active")
        print(f"[INFO] Handshake complete — peer active: {peer_address}")

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
            f"[WARNING] Message from "
            f"non-active peer {peer_address}"
        )
            return

        payload = (self.protocol_handler.validate_and_decrypt(packet))

        if payload is None:
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

    def register_peer(self, peer_address: str, sock: socket.socket) -> None:
        with self.peers_lock:

            self.peers[peer_address] = sock
            peer_count = len(self.peers)
            self.peer_sessions[peer_address] = {
                "state": "pending",
                "public_key": None,
                "session_key": None,
                "handshake_complete": False,
                "username": None,
            }

        print(f"[INFO] Peer registered: {peer_address} — active peers: {peer_count}")

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

    def send_handshake(self, peer_socket: socket.socket) -> None:

        handshake = {
            "type": PacketType.HANDSHAKE,
            "username": self.username,
            "version": "1.0",
            "public_key": RSAUtils.serialize_public_key(self.public_key),
        }

        peer_socket.sendall(self.protocol_handler.serialize(handshake))   

    def schedule_handshake_timeout(self, peer_address: str) -> None:
        """Disconnect *peer_address* if still pending after the timeout."""
        def check_timeout() -> None:

            peer_socket = None

            with self.peers_lock:

                session = self.peer_sessions.get(peer_address)

                if session is None:
                    return
                
                if session["state"] != "pending":
                    return
                
                peer_socket = self.peers.get(peer_address)
            
            if peer_socket is None:
                return

            print(f"[WARNING] Handshake timeout — disconnecting {peer_address}")

            self.remove_peer(peer_socket)

        timer = threading.Timer(HANDSHAKE_TIMEOUT, check_timeout)
        timer.daemon = True
        timer.start()
