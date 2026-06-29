"""UDP broadcast-based peer discovery service for P2PChat."""

import json
import logging
import socket
import threading
import time
from typing import Callable, Optional

from security.jwt_handler import JWTHandler
from config import (
    DISCOVERY_PORT,
    PEER_TIMEOUT,
    PRESENCE_INTERVAL,
)

logger = logging.getLogger(__name__)

DISCOVERY          = "discovery"
DISCOVERY_RESPONSE = "discovery_response"


class DiscoveryService:
    """UDP broadcast peer discovery.

    Optimisations vs naive implementation
    --------------------------------------
    * The JWT identity token is signed once and cached until it expires
      (every PRESENCE_INTERVAL seconds we would otherwise do an RSA sign op
      needlessly — JWT is valid for JWT_EXPIRE_HOURS).
    * The broadcast socket is created once and reused for all broadcasts
      (prevents the file-descriptor leak that occurred when a new socket was
      created per call and an OSError in the finally block silently ate it).
    * _send_response replies to (ip, DISCOVERY_PORT) — not to the
      sender's ephemeral UDP port — so the listener on the other side actually
      receives the response.

    Lifecycle: start() → (periodic discover()) → stop()
    """

    # How long (seconds) the cached JWT is considered fresh.
    # Slightly less than JWT_EXPIRE_HOURS to ensure we never send an expired one.
    _TOKEN_REFRESH_SECS = 600   # 10 minutes

    def __init__(
        self,
        username:        str,
        listen_port:     int,
        peer_id:         str,
        fingerprint:     str,
        public_key_pem:  str,
        private_key_pem: str,
    ) -> None:
        """Initialise the service (does not start yet).

        Args:
            username: Human-readable name to broadcast.
            listen_port: TCP port of the owning node.
            peer_id: SHA-256 peer identifier.
            fingerprint: Colon-separated hex fingerprint.
            public_key_pem: PEM public key (included in broadcast packets).
            private_key_pem: PEM private key (for JWT signing).
        """
        self.username        = username
        self.listen_port     = listen_port
        self.peer_id         = peer_id
        self.fingerprint     = fingerprint
        self.public_key_pem  = public_key_pem
        self.private_key_pem = private_key_pem

        # instance_id lets us discard our own broadcast echoes.
        self.instance_id = f"{username}-{listen_port}"

        self.running: bool = False

        # Listener socket (bound to DISCOVERY_PORT, shared across all operations).
        self._listen_sock: Optional[socket.socket] = None

        # Persistent broadcast socket (reused for all outgoing broadcasts).
        self._bcast_sock:  Optional[socket.socket] = None

        self._listener_thread:  Optional[threading.Thread] = None
        self._broadcast_thread: Optional[threading.Thread] = None

        # JWT cache — re-sign only when the token is about to expire.
        self._cached_token:    str   = ""
        self._token_created_at: float = 0.0

        # Local peer registry (used by unit tests; P2PNode has the authoritative copy).
        self.nearby_peers: dict[str, dict] = {}

        # Callback set by P2PNode.
        self.on_peer_found: Optional[Callable[[dict, tuple[str, int]], None]] = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Bind the UDP listener socket and start listener + broadcast threads."""
        if self.running:
            return

        # Listener socket: bound to DISCOVERY_PORT, receives broadcasts + responses.
        listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.settimeout(1.0)
        listen.bind(("", DISCOVERY_PORT))
        self._listen_sock = listen

        # Broadcast socket: reused for all outgoing packets (avoids FD churn).
        bcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bcast.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._bcast_sock = bcast

        self.running = True

        self._listener_thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="DiscoveryListener",
        )
        self._broadcast_thread = threading.Thread(
            target=self._broadcast_loop, daemon=True, name="DiscoveryBroadcast",
        )
        self._listener_thread.start()
        self._broadcast_thread.start()
        logger.info("[DISCOVERY] Service started on UDP port %d", DISCOVERY_PORT)

    def stop(self) -> None:
        """Signal threads to stop and release all sockets."""
        self.running = False

        for sock in (self._listen_sock, self._bcast_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._listen_sock = None
        self._bcast_sock  = None

        for t in (self._listener_thread, self._broadcast_thread):
            if t is not None and t.is_alive():
                t.join(timeout=2)

        logger.info("[DISCOVERY] Service stopped.")

    # ------------------------------------------------------------------ #
    # Public                                                               #
    # ------------------------------------------------------------------ #

    def discover(self) -> None:
        """Send a single broadcast discovery packet on the LAN."""
        sock = self._bcast_sock
        if sock is None:
            return
        packet = self._make_discovery_packet(DISCOVERY)
        try:
            sock.sendto(
                json.dumps(packet).encode("utf-8"),
                ("255.255.255.255", DISCOVERY_PORT),
            )
            logger.debug("[DISCOVERY] Broadcast sent: %s:%d", self.username, self.listen_port)
        except OSError as exc:
            logger.warning("[DISCOVERY] Broadcast failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Internal threads                                                     #
    # ------------------------------------------------------------------ #

    def _broadcast_loop(self) -> None:
        """Broadcast presence every PRESENCE_INTERVAL seconds."""
        while self.running:
            self.discover()
            for _ in range(PRESENCE_INTERVAL * 10):
                if not self.running:
                    break
                time.sleep(0.1)

    def _listen_loop(self) -> None:
        """Receive UDP packets and dispatch them."""
        while self.running:
            sock = self._listen_sock
            if sock is None:
                break
            try:
                data, address = sock.recvfrom(8192)
                packet = json.loads(data.decode("utf-8"))
                self._handle_packet(packet, address)
            except socket.timeout:
                continue
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            except OSError:
                if not self.running:
                    break

    # ------------------------------------------------------------------ #
    # Packet handling                                                      #
    # ------------------------------------------------------------------ #

    def _handle_packet(self, packet: dict, address: tuple[str, int]) -> None:
        """Dispatch one inbound discovery packet."""
        ptype = packet.get("type")

        if ptype == DISCOVERY:
            # Discard our own echoes.
            if packet.get("instance_id") == self.instance_id:
                return
            logger.debug("[DISCOVERY] Discovery from %s", packet.get("username"))
            # update_peer_registry is a no-op but kept so tests can mock it.
            self.update_peer_registry(packet, address)
            self._notify(packet, address)
            # Reply to (sender_ip, DISCOVERY_PORT), not the ephemeral source port.
            self._send_response(address[0])

        elif ptype == DISCOVERY_RESPONSE:
            # Discard our own responses (can happen on loopback or certain
            # switches that echo unicast packets back to the sender).
            if packet.get("instance_id") == self.instance_id:
                return
            logger.debug("[DISCOVERY] Response from %s", packet.get("username"))
            self.update_peer_registry(packet, address)
            self._notify(packet, address)

    def _notify(self, packet: dict, address: tuple[str, int]) -> None:
        """Forward a discovery packet to the node's callback."""
        if self.on_peer_found is not None:
            try:
                self.on_peer_found(packet, address)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception("[DISCOVERY] on_peer_found raised: %s", exc)

    def _send_response(self, peer_ip_or_addr) -> None:
        """Reply to a broadcast with our own identity info.

        Sends to (peer_ip, DISCOVERY_PORT) — the port the peer's listener
        is bound to, NOT the ephemeral UDP source port.

        Args:
            peer_ip_or_addr: Either an IP string or a (ip, port) tuple
                (the tuple form is the old API kept for test compatibility).
        """
        sock = self._bcast_sock
        if sock is None:
            return
        # Accept both old tuple form and new string form.
        if isinstance(peer_ip_or_addr, tuple):
            peer_ip = peer_ip_or_addr[0]
        else:
            peer_ip = peer_ip_or_addr
        response = self._make_discovery_packet(DISCOVERY_RESPONSE)
        try:
            sock.sendto(
                json.dumps(response).encode("utf-8"),
                (peer_ip, DISCOVERY_PORT),
            )
        except OSError as exc:
            logger.debug("[DISCOVERY] Send response error: %s", exc)

    # ------------------------------------------------------------------ #
    # Packet construction                                                  #
    # ------------------------------------------------------------------ #

    def _get_or_refresh_token(self) -> str:
        """Return a cached JWT, re-signing only when the cached one is stale.

        RSA signing is expensive (~1 ms); doing it every 5 s is wasteful when
        the JWT is valid for 12 hours.  We refresh every 10 minutes instead.

        Returns:
            Signed RS256 JWT string.
        """
        now = time.time()
        if not self._cached_token or now - self._token_created_at > self._TOKEN_REFRESH_SECS:
            self._cached_token    = JWTHandler.create_identity_token(
                peer_id         = self.peer_id,
                username        = self.username,
                fingerprint     = self.fingerprint,
                private_key_pem = self.private_key_pem,
            )
            self._token_created_at = now
            logger.debug("[DISCOVERY] JWT refreshed")
        return self._cached_token

    def _make_discovery_packet(self, packet_type: str) -> dict:
        """Build a complete discovery or discovery_response packet.

        Args:
            packet_type: One of the DISCOVERY / DISCOVERY_RESPONSE constants.

        Returns:
            JSON-serialisable dict ready for UDP transmission.
        """
        return {
            "type":           packet_type,
            "instance_id":    self.instance_id,
            "peer_id":        self.peer_id,
            "username":       self.username,
            "fingerprint":    self.fingerprint,
            "public_key":     self.public_key_pem,
            "identity_token": self._get_or_refresh_token(),
            "port":           self.listen_port,
            "status":         "online",
            "timestamp":      time.time(),
        }

    # ------------------------------------------------------------------ #
    # Registry (local copy for tests; P2PNode maintains authoritative one)#
    # ------------------------------------------------------------------ #

    def update_peer_registry(self, packet: dict, address: tuple) -> None:
        """Update the local nearby-peer registry from *packet*.

        This local copy is primarily used by unit tests.  The authoritative
        registry is maintained by P2PNode.discovered_peers.

        Args:
            packet: Discovery packet dict.
            address: (ip, port) of the packet sender.
        """
        peer_id = packet.get("peer_id")
        if not peer_id:
            return
        self.nearby_peers[peer_id] = {
            "peer_id":    peer_id,
            "username":   packet.get("username"),
            "fingerprint": packet.get("fingerprint"),
            "ip":         address[0],
            "tcp_port":   packet.get("port"),
            "status":     packet.get("status", "online"),
            "last_seen":  time.time(),
        }

    def get_nearby_peers(self) -> dict:
        """Return a shallow copy of the local nearby-peer registry.

        Returns:
            Dict mapping peer_id → peer info dict.
        """
        return dict(self.nearby_peers)

    def cleanup_expired_peers(self) -> None:
        """Remove peers from the local registry whose last-seen exceeds PEER_TIMEOUT."""
        now     = time.time()
        expired = [
            pid for pid, peer in self.nearby_peers.items()
            if now - peer["last_seen"] > PEER_TIMEOUT
        ]
        for pid in expired:
            del self.nearby_peers[pid]

    # ------------------------------------------------------------------ #
    # Socket compat shim                                                   #
    # ------------------------------------------------------------------ #

    @property
    def _socket(self) -> "socket.socket | None":
        """Compat: tests inject a mock socket via discovery._socket."""
        return self._bcast_sock

    @_socket.setter
    def _socket(self, value: "socket.socket | None") -> None:
        """Compat: allow tests to replace the broadcast socket with a mock."""
        self._bcast_sock = value
