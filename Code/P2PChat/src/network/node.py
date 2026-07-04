"""P2PNode: TCP server, handshake, session management, and message routing."""
# WHY changed: hardening pass — fixed a receive-thread crash (naive timestamp),
# a blocked-peer file-transfer bypass, a discovery identity-spoofing gap, and
# callbacks firing while discovery_lock was still held.

import collections
import datetime
import logging
import socket
import threading
import time
from cryptography.fernet import Fernet, InvalidToken

from security.crypto import CryptoHandler
from security.jwt_handler import JWTHandler
from security.rsa_utils import RSAUtils
from identity.identity_manager import IdentityManager
from identity.identity_manager import generate_peer_id, generate_fingerprint
from message.protocol import PacketType, ProtocolHandler
from network.discovery import DiscoveryService, PEER_TIMEOUT
from trust.tofu_engine import TOFUEngine
from config import SOCKET_TIMEOUT, DEFAULT_LISTEN_PORT, MAX_PACKET_SIZE, PROTOCOL_VERSION

logger = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT = 10   # seconds before a pending handshake is dropped

# _send_heartbeats pings every _PING_INTERVAL secs so this only fires for
# a genuinely dead peer, not one that's just quietly reading/composing.
_RECV_TIMEOUT  = 45   # per-socket read timeout (catches stalled peers)
_PING_INTERVAL = 15   # heartbeat period; comfortably under _RECV_TIMEOUT
_AddressMap       = dict[int, str]  # id(socket) -> "IP:PORT"
_SEEN_MSG_MAX     = 4096  # max message IDs in the deque (FIFO eviction)

# Bounded retry for the initial connect() — a LAN hiccup or peer mid-restart
# looks identical to "not there" on one attempt. Runs off a background
# thread (gui/app.py), so the sleep()s here never block the GUI.
_CONNECT_RETRIES          = 3   # total attempts, including the first
_CONNECT_RETRY_BASE_DELAY = 1   # seconds; doubles each retry: 1s, 2s

# Generous per-peer cap, not a strict chat-pacing limiter — just enough to
# stop a buggy or malicious peer from flooding MESSAGE packets fast enough
# to overrun the GUI's bounded _msg_queue (see gui/app.py).
_MSG_RATE_LIMIT_WINDOW = 1.0   # seconds
_MSG_RATE_LIMIT_MAX    = 30    # max messages per peer per window

# Second line of defense against replay, independent of _seen_msg_deque's
# fixed size: a message evicted from that bounded FIFO after _SEEN_MSG_MAX
# newer messages would otherwise be accepted again if resent verbatim.
_MAX_MESSAGE_AGE_SECONDS = 300  # 5 min — generous enough to tolerate clock skew


class P2PNode:
    """TCP peer node: accepts/initiates connections, manages sessions, routes
    messages. peers/peer_sessions are keyed by "IP:PORT"; discovered_peers by
    peer_id (SHA-256 of public key) — tcp_address bridges the two."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str = "Anonymous",
        on_message=None,
        on_disconnect=None,
        on_connected=None,
        on_peer_discovered=None,
    ) -> None:
        """Construct the node (does not bind or start threads yet); see each
        callback parameter's name for its argument signature."""
        self.host     = host
        self.port     = port
        self.username = username

        self.on_message         = on_message
        self.on_disconnect      = on_disconnect
        self.on_connected       = on_connected
        self.on_peer_discovered = on_peer_discovered
        # Wired by TransferManager after construction (not passed in __init__
        # to avoid circular import).  Set to None so attribute always exists.
        self.on_file_packet     = None   # pylint: disable=invalid-name

        self.server_socket: socket.socket | None = None

        # Active sessions, keyed by "IP:PORT" TCP address.
        self.peers_lock    = threading.RLock()
        self.peers:         dict[str, socket.socket] = {}
        self.peer_sessions: dict[str, dict]          = {}
        self._sock_to_addr: _AddressMap              = {}

        self.protocol_handler = ProtocolHandler()

        # Per-port profile suffix so multiple local instances (`python main.py`,
        # `python main.py 1000`, ...) don't collide on shared identity/trust
        # files — see IdentityManager/TrustStore for why that matters on Windows.
        port_suffix = "" if port == DEFAULT_LISTEN_PORT else f"_{port}"

        self.identity_manager = IdentityManager(profile=f"identity{port_suffix}")
        self.identity_manager.load_identity()
        self.private_key = self.identity_manager.get_private_key()
        self.public_key  = self.identity_manager.get_public_key()

        self.tofu = TOFUEngine(profile=f"known_peers{port_suffix}")

        # Replay-attack mitigation: bounded FIFO of recently seen message IDs.
        # deque(maxlen=N) gives O(1) FIFO eviction — no "random eviction" like a plain set.
        self._seen_msg_deque: collections.deque = collections.deque(maxlen=_SEEN_MSG_MAX)
        self._seen_msg_set:   set[str]          = set()
        self._seen_lock = threading.Lock()

        # Per-peer message rate limiting: tcp_addr -> deque of recent send
        # timestamps (see _check_rate_limit / _MSG_RATE_LIMIT_*). A leaf
        # lock — never held while acquiring peers_lock or discovery_lock.
        self._msg_timestamps: dict[str, collections.deque] = {}
        self._msg_rate_lock = threading.Lock()

        # Discovery registry, keyed by peer_id.
        self.discovery_lock    = threading.RLock()
        self.discovered_peers: dict[str, dict] = {}

        self.discovery = DiscoveryService(
            username        = self.username,
            listen_port     = self.port,
            peer_id         = self.identity_manager.get_peer_id(),
            fingerprint     = self.identity_manager.get_fingerprint(),
            public_key_pem  = self.identity_manager.public_key_pem,
            private_key_pem = self.identity_manager.private_key_pem,
        )
        self.discovery.on_peer_found = self._handle_discovered_peer

        self.receive_threads:   list[threading.Thread]  = []
        self.expiration_thread: threading.Thread | None = None
        self.is_running = False

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start_server(self) -> None:
        """Bind the TCP server, start discovery, and begin accepting connections."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.settimeout(SOCKET_TIMEOUT)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(10)
        self.is_running = True

        self.discovery.start()

        self.expiration_thread = threading.Thread(
            target=self._cleanup_expired_peers,
            daemon=True, name="DiscoveryExpiration",
        )
        self.expiration_thread.start()

        threading.Thread(
            target=self._send_heartbeats,
            daemon=True, name="Heartbeat",
        ).start()

        threading.Thread(
            target=self._accept_connections,
            daemon=True, name="AcceptThread",
        ).start()

        logger.info("[NODE] Listening on %s:%d as %s",
                    self.host, self.port, self.identity_manager.get_peer_id()[:12])

    def stop_server(self) -> None:
        """Stop accepting connections, close all sessions, and shut down discovery."""
        self.is_running = False
        self.discovery.stop()

        with self.peers_lock:
            for sock in self.peers.values():
                try:
                    sock.close()
                except OSError:
                    pass
            self.peers.clear()
            self.peer_sessions.clear()
            self._sock_to_addr.clear()

        if self.server_socket is not None:
            try:
                self.server_socket.close()
            except OSError:
                pass

        for t in self.receive_threads:
            t.join(timeout=1)
        self.receive_threads.clear()
        logger.info("[NODE] Server stopped.")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def _dial_with_retry(self, host: str, port: int) -> socket.socket | None:
        """Connect to host:port with exponential backoff, up to _CONNECT_RETRIES
        times; return the socket or None. Split out to keep connect_to_peer()'s
        branch count down and make retry logic independently testable."""
        tcp_addr = f"{host}:{port}"
        sock: socket.socket | None = None
        last_exc: OSError | None = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)            # TCP connect timeout
                sock.connect((host, port))
                return sock
            except OSError as exc:
                last_exc = exc
                if sock is not None:
                    sock.close()
                sock = None
                if attempt < _CONNECT_RETRIES:
                    delay = _CONNECT_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "[NODE] Connect attempt %d/%d to %s failed (%s) — retrying in %ds",
                        attempt, _CONNECT_RETRIES, tcp_addr, exc, delay,
                    )
                    time.sleep(delay)

        logger.error("[NODE] Connect failed %s after %d attempts: %s",
                      tcp_addr, _CONNECT_RETRIES, last_exc)
        return None

    def connect_to_peer(self, host: str, port: int) -> bool:
        """Dial host:port and start the handshake; False if already connected."""
        tcp_addr = f"{host}:{port}"

        # Dedup 1: exact address already connected.
        with self.peers_lock:
            if tcp_addr in self.peers:
                logger.debug("[NODE] Already connected to %s", tcp_addr)
                return False

        # Dedup 2: same peer_id already active on a different (e.g.
        # ephemeral, if they connected to us) port — a plain tcp_addr
        # comparison would miss it, so resolve peer_id and scan by that.
        target_pid: str | None = None
        with self.discovery_lock:
            for pid, info in self.discovered_peers.items():
                announced = f"{info.get('ip')}:{info.get('port')}"
                if announced == tcp_addr:
                    target_pid = pid
                    break
        if target_pid is not None:
            with self.peers_lock:
                for sess in self.peer_sessions.values():
                    if (sess.get("peer_id") == target_pid
                            and sess.get("state") == "active"):
                        logger.info("[NODE] Active session already exists with %s",
                                    target_pid[:12])
                        return False

        # connect_to_peer() always runs off the GUI thread (see gui/app.py's
        # Connect handlers), so the backoff sleeps inside _dial_with_retry
        # never freeze the UI.
        sock = self._dial_with_retry(host, port)
        if sock is None:
            return False

        try:
            # Keep a modest per-recv timeout so a stalled peer cannot block
            # the receive thread indefinitely.
            sock.settimeout(_RECV_TIMEOUT)

            if not self._register_peer(tcp_addr, sock, is_initiator=True):
                sock.close()
                return False

            if not self._send_handshake(sock):
                self._remove_peer(sock)
                return False

            self._start_receive_thread(tcp_addr, sock)
            self._schedule_handshake_timeout(tcp_addr)
            return True

        except OSError as exc:
            logger.error("[NODE] Post-connect setup failed %s: %s", tcp_addr, exc)
            return False

    def send_message(self, message: str, tcp_addr: str) -> bool:
        """Encrypt and send message to the session at tcp_addr ("IP:PORT")."""
        with self.peers_lock:
            session = self.peer_sessions.get(tcp_addr)
            sock    = self.peers.get(tcp_addr)

        if session is None or session["state"] != "active" or sock is None:
            logger.warning("[NODE] Peer not active: %s", tcp_addr)
            return False

        crypto: CryptoHandler | None = session.get("crypto")
        try:
            packet = self.protocol_handler.create_packet(
                PacketType.MESSAGE, self.username, message, crypto=crypto,
            )
            data = self.protocol_handler.serialize(packet)

            # Mirror the receiver's own ceiling (protocol.py's
            # receive_packet) — sendall() would otherwise "succeed" locally
            # while the receiver silently drops the oversized packet.
            if len(data) > MAX_PACKET_SIZE:
                logger.warning(
                    "[NODE] Message too large to send (%d bytes > %d limit) to %s",
                    len(data), MAX_PACKET_SIZE, tcp_addr,
                )
                return False

            sock.sendall(data)
            return True
        except OSError as exc:
            logger.error("[NODE] Send failed: %s", exc)
            self._remove_peer(sock)
            return False

    def broadcast_message(self, message: str) -> tuple[int, int]:
        """Send message to every active session; returns (sent_count, failed_count)."""
        with self.peers_lock:
            active = [a for a, s in self.peer_sessions.items() if s["state"] == "active"]
        sent = failed = 0
        for addr in active:
            if self.send_message(message, addr):
                sent += 1
            else:
                failed += 1
        return sent, failed

    def _send_heartbeats(self) -> None:
        """Ping every active session every _PING_INTERVAL seconds, so
        _RECV_TIMEOUT never mistakes a quiet-but-alive peer for a dead one."""
        while self.is_running:
            time.sleep(_PING_INTERVAL)
            with self.peers_lock:
                targets = [(addr, self.peers.get(addr))
                          for addr, sess in self.peer_sessions.items()
                          if sess["state"] == "active"]
            if not targets:
                continue
            packet = self.protocol_handler.create_packet(
                PacketType.PING, self.username, "", crypto=None)
            data = self.protocol_handler.serialize(packet)
            for tcp_addr, sock in targets:
                if sock is None:
                    continue
                try:
                    sock.sendall(data)
                except OSError as exc:
                    logger.debug("[NODE] Heartbeat send failed for %s: %s", tcp_addr, exc)
                    self._remove_peer(sock)

    def disconnect_peer(self, peer_id: str) -> bool:
        """Close the active session for peer_id (user-initiated)."""
        with self.peers_lock:
            target_sock = None
            for addr, sess in self.peer_sessions.items():
                if sess.get("peer_id") == peer_id:
                    target_sock = self.peers.get(addr)
                    break

        if target_sock is None:
            return False

        logger.info("[NODE] Disconnecting %s (user request)", peer_id[:12])
        self._remove_peer(target_sock)
        return True

    def discover_peers(self) -> None:
        """Trigger an immediate UDP broadcast."""
        self.discovery.discover()

    def get_discovered_peers(self) -> dict[str, dict]:
        """Return a snapshot of discovered_peers (peer_id -> info dict)."""
        with self.discovery_lock:
            return dict(self.discovered_peers)

    def get_active_session_for_ip(self, ip: str) -> str | None:
        """Find an active session's "IP:port" key for ip (any port) — bridges
        discovery's listen-port to the OS-assigned ephemeral port."""
        with self.peers_lock:
            for tcp_addr, sess in self.peer_sessions.items():
                if tcp_addr.startswith(f"{ip}:") and sess.get("state") == "active":
                    return tcp_addr
        return None

    def get_trust_state(self, peer_id: str) -> str:
        """Return the TOFU trust state for *peer_id*."""
        return self.tofu.get_trust_state(peer_id)

    def trust_peer(self, peer_id: str) -> bool:
        """Mark *peer_id* as TRUSTED."""
        try:
            return self.tofu.trust_peer(peer_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("[NODE] Failed to trust peer %s", peer_id[:12])
            return False

    def block_peer(self, peer_id: str) -> bool:
        """Mark *peer_id* as BLOCKED and immediately disconnect their session."""
        try:
            ok = self.tofu.block_peer(peer_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("[NODE] Failed to block peer %s", peer_id[:12])
            return False
        if ok:
            self._disconnect_peer_id(peer_id)
        return ok

    def unblock_peer(self, peer_id: str) -> bool:
        """Remove BLOCKED state from *peer_id*, restoring TRUSTED."""
        try:
            return self.tofu.unblock_peer(peer_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("[NODE] Failed to unblock peer %s", peer_id[:12])
            return False

    # ------------------------------------------------------------------ #
    # Accept loop                                                          #
    # ------------------------------------------------------------------ #

    def _accept_connections(self) -> None:
        """Accept incoming TCP connections in a dedicated daemon thread."""
        if self.server_socket is None:
            return
        while self.is_running:
            try:
                client_socket, address = self.server_socket.accept()
                tcp_addr = f"{address[0]}:{address[1]}"
                logger.info("[NODE] Incoming connection from %s", tcp_addr)

                # Pre-handshake IP-level BLOCKED check (fast path).
                if self._is_ip_blocked(address[0]):
                    logger.info("[NODE] Rejected blocked IP %s", address[0])
                    client_socket.close()
                    continue

                # Apply the same recv timeout as outbound connections.
                client_socket.settimeout(_RECV_TIMEOUT)

                if not self._register_peer(tcp_addr, client_socket, is_initiator=False):
                    client_socket.close()
                    continue

                self._start_receive_thread(tcp_addr, client_socket)
                self._schedule_handshake_timeout(tcp_addr)

            except socket.timeout:
                continue
            except OSError:
                if self.is_running:
                    logger.error("[NODE] Accept error")
                break

    # ------------------------------------------------------------------ #
    # Receive loop                                                         #
    # ------------------------------------------------------------------ #

    def _receive_messages(self, peer_socket: socket.socket) -> None:
        """Read and dispatch packets from *peer_socket* (runs in its own thread)."""
        while self.is_running:
            try:
                packet = self.protocol_handler.receive_packet(peer_socket)
                if packet is None:
                    # None means the connection closed cleanly or timed out.
                    self._remove_peer(peer_socket)
                    break

                msg_type = packet.get("type", "")

                if   msg_type == PacketType.HANDSHAKE:
                    self._handle_handshake(packet, peer_socket)
                elif msg_type == PacketType.HANDSHAKE_ACK:
                    self._handle_handshake_ack(packet, peer_socket)
                elif msg_type == PacketType.SESSION_KEY:
                    self._handle_session_key(packet, peer_socket)
                elif msg_type == PacketType.MESSAGE:
                    self._handle_message(packet, peer_socket)
                elif msg_type in PacketType.FILE_TYPES:
                    self._handle_file_packet(packet, peer_socket)
                elif msg_type == PacketType.PING:
                    # No action needed — just receiving it already gave this
                    # loop's next receive_packet() call a fresh _RECV_TIMEOUT
                    # window, which is the entire point (see _send_heartbeats).
                    pass
                else:
                    logger.debug("[NODE] Unknown packet type '%s'", msg_type)

            except (ConnectionResetError, BrokenPipeError):
                self._remove_peer(peer_socket)
                break
            except socket.timeout:
                # Peer did not send data within _RECV_TIMEOUT — treat as stall.
                addr = self._get_tcp_addr(peer_socket)
                logger.warning("[NODE] Receive timeout — dropping stalled peer %s", addr)
                self._remove_peer(peer_socket)
                break
            except (OSError, ValueError, KeyError, InvalidToken) as exc:
                if self.is_running:
                    logger.error("[NODE] Receive error: %s", exc)
                self._remove_peer(peer_socket)
                break

    # ------------------------------------------------------------------ #
    # Packet handlers                                                      #
    # ------------------------------------------------------------------ #

    def _handle_handshake(self, packet: dict, peer_socket: socket.socket) -> None:
        if not self.protocol_handler.validate_packet(packet):
            self._remove_peer(peer_socket)
            return

        tcp_addr = self._get_tcp_addr(peer_socket)
        if tcp_addr is None:
            return

        # "version" was required but never actually compared — two diverged
        # builds could still "handshake" successfully.
        incoming_version = packet.get("version")
        if incoming_version != PROTOCOL_VERSION:
            logger.warning(
                "[NODE] Protocol version mismatch from %s: got %r, expected %r",
                tcp_addr, incoming_version, PROTOCOL_VERSION,
            )
            self._remove_peer(peer_socket)
            return

        raw_pub_key = packet.get("public_key", "")
        try:
            RSAUtils.load_public_key(raw_pub_key)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[NODE] Invalid public key in handshake — dropping %s", tcp_addr)
            self._remove_peer(peer_socket)
            return

        # Derive peer_id now so we can check BLOCKED before replying.
        incoming_peer_id = generate_peer_id(raw_pub_key)
        if self.tofu.is_blocked(incoming_peer_id):
            logger.info("[NODE] Handshake rejected — %s is BLOCKED", incoming_peer_id[:12])
            self._remove_peer(peer_socket)
            return

        # Update the session in a single lock acquisition (atomic).
        with self.peers_lock:
            session = self.peer_sessions.get(tcp_addr)
            if session is None:
                return
            session["peer_id"]     = incoming_peer_id
            session["public_key"]  = raw_pub_key
            session["username"]    = packet.get("username", "Unknown")
            session["listen_port"] = packet.get("listen_port")

        ack = {
            "type":       PacketType.HANDSHAKE_ACK,
            "status":     "ok",
            "public_key": RSAUtils.serialize_public_key(self.public_key),
        }
        try:
            peer_socket.sendall(self.protocol_handler.serialize(ack))
        except OSError as exc:
            logger.error("[NODE] ACK send failed: %s", exc)
            self._remove_peer(peer_socket)

    def _handle_handshake_ack(self, packet: dict, peer_socket: socket.socket) -> None:
        if not self.protocol_handler.validate_packet(packet):
            self._remove_peer(peer_socket)
            return

        tcp_addr = self._get_tcp_addr(peer_socket)
        if tcp_addr is None:
            return

        if packet.get("status") != "ok":
            logger.warning("[NODE] Handshake ACK status not ok from %s", tcp_addr)
            self._remove_peer(peer_socket)
            return

        raw_key = packet.get("public_key", "")
        try:
            RSAUtils.load_public_key(raw_key)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[NODE] Invalid public key in ACK — dropping %s", tcp_addr)
            self._remove_peer(peer_socket)
            return

        ack_peer_id = generate_peer_id(raw_key)
        if self.tofu.is_blocked(ack_peer_id):
            logger.info("[NODE] ACK rejected — %s is BLOCKED", ack_peer_id[:12])
            self._remove_peer(peer_socket)
            return

        # Update session atomically in one lock acquisition.
        with self.peers_lock:
            session = self.peer_sessions.get(tcp_addr)
            if session is None:
                return
            session["peer_id"]    = ack_peer_id
            session["public_key"] = raw_key
            is_initiator          = session["is_initiator"]

        if is_initiator:
            self._send_session_key(peer_socket, tcp_addr)

    def _handle_session_key(self, packet: dict, peer_socket: socket.socket) -> None:
        tcp_addr = self._get_tcp_addr(peer_socket)
        if tcp_addr is None:
            return

        if not self.protocol_handler.validate_packet(packet):
            self._remove_peer(peer_socket)
            return

        encrypted_key_hex = packet.get("payload", "")
        try:
            fernet_key = RSAUtils.decrypt(self.private_key, bytes.fromhex(encrypted_key_hex))
            crypto     = CryptoHandler(key=fernet_key)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[NODE] Session key decrypt failed from %s: %s", tcp_addr, exc)
            self._remove_peer(peer_socket)
            return

        peer_id = ""
        old_sock: socket.socket | None = None
        old_addr: str | None = None
        with self.peers_lock:
            session = self.peer_sessions.get(tcp_addr)
            if session is None:
                return
            peer_id = session.get("peer_id", "")

            # Both sides pressing Connect at once can leave two active
            # sessions for one peer_id — identify the older one here, close
            # it below (after releasing this lock).
            if peer_id:
                for addr, existing_sess in list(self.peer_sessions.items()):
                    if (addr != tcp_addr
                            and existing_sess.get("peer_id") == peer_id
                            and existing_sess.get("state") == "active"):
                        old_sock, old_addr = self.peers.get(addr), addr
                        break

            session["crypto"] = crypto
            session["state"]  = "active"

        # Closed after releasing peers_lock (not inside the "with" above) to
        # keep every lock in this file non-nested.
        if old_sock is not None:
            logger.info(
                "[NODE] Duplicate session for %s — closing older %s",
                peer_id[:12], old_addr,
            )
            self._remove_peer(old_sock)

        self._sync_discovery_on_connect(peer_id, tcp_addr)
        logger.info("[NODE] Session active: %s (peer: %s)",
                    tcp_addr, peer_id[:12] if peer_id else "?")
        self._fire_callback(self.on_connected, peer_id, tcp_addr)

    def _check_rate_limit(self, tcp_addr: str) -> bool:
        """Return False once tcp_addr exceeds _MSG_RATE_LIMIT_MAX messages in
        the trailing _MSG_RATE_LIMIT_WINDOW (sliding window via a per-peer
        deque of timestamps; dropped entirely in _remove_peer)."""
        now = time.time()
        with self._msg_rate_lock:
            timestamps = self._msg_timestamps.setdefault(tcp_addr, collections.deque())
            while timestamps and now - timestamps[0] > _MSG_RATE_LIMIT_WINDOW:
                timestamps.popleft()
            if len(timestamps) >= _MSG_RATE_LIMIT_MAX:
                return False
            timestamps.append(now)
            return True

    def _is_message_stale(self, packet: dict) -> bool:
        """True if packet's timestamp is missing/malformed or older than
        _MAX_MESSAGE_AGE_SECONDS (malformed counts as stale, not accepted)."""
        message_id = packet.get("message_id", "")[:8]
        try:
            msg_time = datetime.datetime.fromisoformat(packet["timestamp"])
            # A timezone-less string parses fine here but raises TypeError
            # when subtracted from aware "now" below — treat it as malformed.
            if msg_time.tzinfo is None:
                raise ValueError("timestamp has no timezone offset")
            age = (datetime.datetime.now(datetime.timezone.utc) - msg_time).total_seconds()
        except (ValueError, TypeError):
            logger.warning("[NODE] Malformed timestamp — dropping message %s", message_id)
            return True
        if age > _MAX_MESSAGE_AGE_SECONDS:
            logger.debug("[NODE] Stale message dropped (age=%.0fs): %s", age, message_id)
            return True
        return False

    def _handle_message(self, packet: dict, peer_socket: socket.socket) -> None:
        tcp_addr = self._get_tcp_addr(peer_socket)
        if tcp_addr is None:
            return

        # Snapshot all needed session fields in a single lock acquisition to
        # avoid TOCTOU: another thread could change state between the check
        # and the subsequent field reads.
        with self.peers_lock:
            session = self.peer_sessions.get(tcp_addr)
            if session is None:
                return
            session_state  = session["state"]
            sender_pid     = session.get("peer_id")
            session_crypto = session.get("crypto")

        if session_state != "active":
            return

        if not self._check_rate_limit(tcp_addr):
            logger.warning("[NODE] Rate limit exceeded for %s — message dropped", tcp_addr)
            return

        if not self.protocol_handler.validate_packet(packet):
            return

        message_id = packet["message_id"]

        if self._is_message_stale(packet):
            return

        # Replay-attack check using a bounded FIFO structure.
        # deque(maxlen=N) automatically discards the oldest entry when full,
        # so no explicit eviction code is needed — and eviction is truly FIFO.
        with self._seen_lock:
            if message_id in self._seen_msg_set:
                logger.debug("[NODE] Duplicate message dropped: %s", message_id[:8])
                return
            # Capture the item about to be evicted (deque[0]) BEFORE appending,
            # because after deque.append() when full, that item is already gone.
            evicted: str | None = (
                self._seen_msg_deque[0]
                if len(self._seen_msg_deque) == _SEEN_MSG_MAX
                else None
            )
            self._seen_msg_deque.append(message_id)
            self._seen_msg_set.add(message_id)
            if evicted is not None:
                self._seen_msg_set.discard(evicted)

        if sender_pid and self.tofu.is_blocked(sender_pid):
            logger.debug("[NODE] Message dropped — %s is BLOCKED", sender_pid[:12])
            return

        try:
            payload = self.protocol_handler.decrypt_payload(packet, crypto=session_crypto)
        except (InvalidToken, ValueError) as exc:
            logger.warning("[NODE] Decrypt failed from %s: %s", tcp_addr, exc)
            return

        self._fire_callback(
            self.on_message,
            sender_pid or "",
            packet.get("sender", "Unknown"),
            payload,
        )

    # ------------------------------------------------------------------ #
    # Discovery                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _discovered_identity_is_spoofed(peer_id: str, fingerprint, pub_key: str) -> bool:
        """True unless peer_id/fingerprint are actually SHA-256(pub_key) — the
        JWT alone only proves who signed it, not that these claimed values
        belong to that key, letting a sender impersonate someone else's peer_id."""
        return (peer_id != generate_peer_id(pub_key)
                or fingerprint != generate_fingerprint(pub_key))

    def _handle_discovered_peer(self, packet: dict, address: tuple[str, int]) -> None:
        """Called by DiscoveryService for every valid discovery/response packet."""
        peer_ip = address[0]
        peer_id = packet.get("peer_id")
        token   = packet.get("identity_token")
        pub_key = packet.get("public_key")

        if not token or not pub_key:
            logger.warning("[DISCOVERY] Missing JWT or public key from %s", peer_ip)
            return

        claims = JWTHandler.verify_identity_token(token, pub_key)
        if claims is None:
            logger.warning("[DISCOVERY] Invalid JWT from %s", peer_ip)
            return

        # Cross-check JWT claims against packet fields — prevents spoofing.
        if claims.get("fingerprint") != packet.get("fingerprint"):
            logger.warning("[DISCOVERY] Fingerprint mismatch JWT vs packet from %s", peer_ip)
            return
        if claims.get("peer_id") != peer_id:
            logger.warning("[DISCOVERY] Peer ID mismatch JWT vs packet from %s", peer_ip)
            return
        if not peer_id:
            return

        if self._discovered_identity_is_spoofed(peer_id, packet.get("fingerprint"), pub_key):
            logger.warning(
                "[DISCOVERY] peer_id/fingerprint do not match public_key from %s — dropping",
                peer_ip,
            )
            return

        peer_port = packet.get("port")
        if peer_port is None:
            return

        tcp_address = f"{peer_ip}:{peer_port}"
        trust_state = self.tofu.verify_peer(peer_id, packet.get("fingerprint", ""))
        now         = time.time()

        # Snapshot for the callback, fired after discovery_lock releases below
        # — holding a lock across a synchronous callback risks deadlock.
        notify_info: dict | None = None
        is_new = False

        with self.discovery_lock:
            existing = self.discovered_peers.get(peer_id)

            if existing is not None:
                # Dirty-check — only notify GUI when something meaningful changed.
                # Routine heartbeats (same fields) must NOT trigger UI redraws.
                changed = (
                    existing.get("status")       != "online"
                    or existing.get("trust_state") != trust_state
                    or existing.get("fingerprint") != packet.get("fingerprint")
                    or existing.get("username")    != packet.get("username")
                )
                existing["last_seen"]   = now
                existing["trust_state"] = trust_state
                existing["fingerprint"] = packet.get("fingerprint")
                # Only update stored listen-port address if the session is NOT
                # currently active (active sessions keep their ephemeral address).
                if not existing.get("connected"):
                    existing["tcp_address"] = tcp_address
                existing["status"] = "online"
                if changed:
                    notify_info = dict(existing)
            else:
                info = {
                    "username":    packet.get("username", "Unknown"),
                    "ip":          peer_ip,
                    "port":        peer_port,
                    "tcp_address": tcp_address,
                    "status":      "online",
                    "connected":   False,
                    "peer_id":     peer_id,
                    "last_seen":   now,
                    "fingerprint": packet.get("fingerprint"),
                    "trust_state": trust_state,
                }
                self.discovered_peers[peer_id] = info
                notify_info = dict(info)
                is_new = True

        if is_new and notify_info is not None:
            if peer_id == self.identity_manager.get_peer_id():
                logger.warning("[DISCOVERY] Peer %s has same identity — "
                               "two instances sharing identity.json?", peer_id[:12])
            logger.info("[DISCOVERY] New peer: %s (%s)", peer_id[:12], notify_info["username"])

        if notify_info is not None:
            self._fire_callback(self.on_peer_discovered, peer_id, notify_info)

    def _cleanup_expired_peers(self) -> None:
        """Mark peers offline when they stop broadcasting (runs every 5 s)."""
        while self.is_running:
            now = time.time()
            # Collect, then fire callbacks after releasing the lock — same
            # reasoning as _handle_discovered_peer above.
            to_notify: list[tuple[str, dict]] = []
            with self.discovery_lock:
                for peer_id, peer in list(self.discovered_peers.items()):
                    if (peer["status"] == "online"
                            and now - peer["last_seen"] > PEER_TIMEOUT):
                        # Transition fires once — no repeated callbacks.
                        peer["status"]    = "offline"
                        peer["connected"] = False
                        to_notify.append((peer_id, dict(peer)))
            for peer_id, info in to_notify:
                self._fire_callback(self.on_peer_discovered, peer_id, info)
            time.sleep(5)

    def _sync_discovery_on_connect(self, peer_id: str, tcp_addr: str) -> None:
        """Store the live (possibly ephemeral) tcp_addr into discovered_peers
        once a session goes active, so acceptor-side lookups by tcp_address
        keep working."""
        if not peer_id:
            return
        with self.discovery_lock:
            info = self.discovered_peers.get(peer_id)
            if info:
                info["tcp_address"] = tcp_addr
                info["connected"]   = True

    # ------------------------------------------------------------------ #
    # Peer registration / removal                                          #
    # ------------------------------------------------------------------ #

    def _register_peer(self, tcp_addr: str, sock: socket.socket,
                       is_initiator: bool) -> bool:
        """Register a new TCP connection; False if tcp_addr is already registered."""
        with self.peers_lock:
            if tcp_addr in self.peers:
                return False
            self.peers[tcp_addr]         = sock
            self._sock_to_addr[id(sock)] = tcp_addr
            # Provide a complete schema upfront; handlers fill in the rest.
            self.peer_sessions[tcp_addr] = {
                "state":        "pending",
                "peer_id":      None,   # filled by handshake handler
                "public_key":   None,
                "session_key":  None,
                "crypto":       None,
                "username":     None,
                "listen_port":  None,
                "is_initiator": is_initiator,
            }
        return True

    def _remove_peer(self, peer_socket: socket.socket) -> None:
        """Close *peer_socket* and clean up all associated state."""
        tcp_addr = self._get_tcp_addr(peer_socket)
        disconnected_pid: str | None = None

        with self.peers_lock:
            if tcp_addr is not None:
                sess             = self.peer_sessions.get(tcp_addr)
                disconnected_pid = sess.get("peer_id") if sess else None
                self.peers.pop(tcp_addr, None)
                self.peer_sessions.pop(tcp_addr, None)
                self._sock_to_addr.pop(id(peer_socket), None)

        try:
            peer_socket.close()
        except OSError:
            pass

        if tcp_addr is not None:
            with self._msg_rate_lock:
                self._msg_timestamps.pop(tcp_addr, None)

        if tcp_addr is not None:
            with self.discovery_lock:
                # Prefer peer_id lookup (reliable even when tcp_addr is ephemeral).
                target = (
                    self.discovered_peers.get(disconnected_pid)
                    if disconnected_pid else None
                )
                if target is None:
                    # Pre-handshake disconnect — fall back to tcp_address search.
                    for info in self.discovered_peers.values():
                        if info.get("tcp_address") == tcp_addr:
                            target = info
                            break
                if target is not None:
                    target["connected"] = False
                    # Per architecture spec: ONLINE ≠ CONNECTED.
                    # The peer may still be broadcasting UDP — keep "online"
                    # until _cleanup_expired_peers transitions them to "offline".
                    if target.get("status") == "connected":
                        target["status"] = "online"

            self._fire_callback(self.on_disconnect, tcp_addr)

    # ------------------------------------------------------------------ #
    # Handshake helpers                                                    #
    # ------------------------------------------------------------------ #

    def _send_handshake(self, peer_socket: socket.socket) -> bool:
        packet = {
            "type":        PacketType.HANDSHAKE,
            "username":    self.username,
            "version":     PROTOCOL_VERSION,
            "listen_port": self.port,
            "public_key":  RSAUtils.serialize_public_key(self.public_key),
        }
        try:
            peer_socket.sendall(self.protocol_handler.serialize(packet))
            return True
        except OSError as exc:
            logger.error("[NODE] Handshake send failed: %s", exc)
            return False

    def _send_session_key(self, peer_socket: socket.socket, tcp_addr: str) -> None:
        with self.peers_lock:
            session      = self.peer_sessions.get(tcp_addr)
            raw_peer_key = session.get("public_key") if session else None

        if not raw_peer_key:
            logger.warning("[NODE] No public key in session for %s", tcp_addr)
            self._remove_peer(peer_socket)
            return

        try:
            peer_pub = RSAUtils.load_public_key(raw_peer_key)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("[NODE] Cannot load peer public key: %s", exc)
            self._remove_peer(peer_socket)
            return

        fernet_key    = Fernet.generate_key()
        encrypted_key = RSAUtils.encrypt(peer_pub, fernet_key)

        pkt = {"type": PacketType.SESSION_KEY, "payload": encrypted_key.hex()}
        try:
            peer_socket.sendall(self.protocol_handler.serialize(pkt))
        except OSError as exc:
            logger.error("[NODE] Session key send failed: %s", exc)
            self._remove_peer(peer_socket)
            return

        crypto  = CryptoHandler(key=fernet_key)
        peer_id = ""
        with self.peers_lock:
            session = self.peer_sessions.get(tcp_addr)
            if session is not None:
                session["session_key"] = fernet_key
                session["crypto"]      = crypto
                session["state"]       = "active"
                peer_id                = session.get("peer_id", "")

        self._sync_discovery_on_connect(peer_id, tcp_addr)
        logger.info("[NODE] Session key sent → %s active (peer: %s)",
                    tcp_addr, peer_id[:12] if peer_id else "?")
        self._fire_callback(self.on_connected, peer_id, tcp_addr)

    def _schedule_handshake_timeout(self, tcp_addr: str) -> None:
        """Drop a peer that has not completed the handshake within HANDSHAKE_TIMEOUT."""
        def _check() -> None:
            with self.peers_lock:
                session = self.peer_sessions.get(tcp_addr)
                if session is None or session["state"] != "pending":
                    return
                sock = self.peers.get(tcp_addr)
            if sock is None:
                return
            logger.warning("[NODE] Handshake timeout — dropping %s", tcp_addr)
            self._remove_peer(sock)

        t = threading.Timer(HANDSHAKE_TIMEOUT, _check)
        t.daemon = True
        t.start()

    # ------------------------------------------------------------------ #
    # Block helpers                                                        #
    # ------------------------------------------------------------------ #

    def _is_ip_blocked(self, ip: str) -> bool:
        """Fast pre-handshake check: is any BLOCKED peer known at *ip*?"""
        with self.discovery_lock:
            for pid, info in self.discovered_peers.items():
                if info.get("ip") == ip and self.tofu.is_blocked(pid):
                    return True
        return False

    def _disconnect_peer_id(self, peer_id: str) -> None:
        """Force-close all TCP sessions for *peer_id*."""
        with self.discovery_lock:
            info = self.discovered_peers.get(peer_id)
        peer_ip = info.get("ip") if info else None
        if not peer_ip:
            return
        with self.peers_lock:
            to_close = [
                sock for addr, sock in self.peers.items()
                if addr.startswith(f"{peer_ip}:")
            ]
        for sock in to_close:
            logger.info("[NODE] Force-disconnecting blocked %s", peer_ip)
            self._remove_peer(sock)

    # ------------------------------------------------------------------ #
    # Utility                                                              #
    # ------------------------------------------------------------------ #

    def _get_tcp_addr(self, peer_socket: socket.socket) -> str | None:
        """Return the "IP:PORT" key for *peer_socket*, or None."""
        with self.peers_lock:
            return self._sock_to_addr.get(id(peer_socket))

    def _get_peer_id(self, peer_socket: socket.socket) -> str | None:
        """Compat alias for _get_tcp_addr (used by tests)."""
        return self._get_tcp_addr(peer_socket)

    def _start_receive_thread(self, tcp_addr: str, sock: socket.socket) -> None:
        # Prune dead threads before adding a new one (prevents unbounded growth).
        self.receive_threads = [t for t in self.receive_threads if t.is_alive()]
        t = threading.Thread(
            target=self._receive_messages, args=(sock,),
            daemon=True, name=f"Recv-{tcp_addr}",
        )
        t.start()
        self.receive_threads.append(t)

    def _handle_file_packet(self, packet: dict,
                             peer_socket: socket.socket) -> None:
        """Route an inbound file-transfer packet to TransferManager, passing
        the session's crypto so it can decrypt FILE_CHUNK payloads."""
        if self.on_file_packet is None:
            return
        tcp_addr = self._get_tcp_addr(peer_socket)
        with self.peers_lock:
            sess       = self.peer_sessions.get(tcp_addr) if tcp_addr else None
            crypto     = sess.get("crypto")    if sess else None
            sender_pid = sess.get("peer_id", "") if sess else ""

        # Mirrors the same check in _handle_message: a chunk already in the
        # OS receive buffer when a peer gets blocked could otherwise still
        # reach TransferManager and get written to disk.
        if sender_pid and self.tofu.is_blocked(sender_pid):
            logger.debug("[NODE] File packet dropped — %s is BLOCKED", sender_pid[:12])
            return

        self._fire_callback(self.on_file_packet, packet, crypto, sender_pid)

    def send_raw_packet(self, packet: dict, tcp_addr: str) -> bool:
        """Send a pre-built packet dict as-is — used by TransferManager for
        file-transfer packets, which don't fit create_packet's chat-message shape."""
        with self.peers_lock:
            sock = self.peers.get(tcp_addr)
        if sock is None:
            logger.warning("[NODE] send_raw_packet: no socket for %s", tcp_addr)
            return False
        try:
            sock.sendall(self.protocol_handler.serialize(packet))
            return True
        except OSError as exc:
            logger.error("[NODE] send_raw_packet failed: %s", exc)
            return False

    def _fire_callback(self, callback, *args) -> None:
        """Invoke *callback* safely, logging any exception it raises."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("[NODE] Callback raised: %s", exc)
