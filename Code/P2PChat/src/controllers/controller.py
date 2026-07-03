"""ChatController: thin adapter between P2PNode and the GUI layer."""

from __future__ import annotations

import logging
import time
import threading
from typing import Callable, Optional, Tuple

from network.node import P2PNode
from network.transfer_manager import TransferManager
from storage.contact_book import ContactBook
from storage.message_history import MessageHistory
from trust.trust_state import TrustState

logger = logging.getLogger(__name__)


class ChatController:
    """Thin wrapper around P2PNode that exposes a clean interface for the GUI.

    Key design decision: the GUI works with **peer_id** (SHA-256 hash) for
    display and selection, but all networking calls (send_message, connect)
    use **tcp_address** ("IP:PORT"). This controller translates between them.
    """

    def __init__(
        self,
        on_system:          Callable[[str], None],
        on_message:         Callable[[str, str, str], None],
        on_connected:       Callable[[str, str], None],
        on_disconnect:      Callable[[str], None],
        on_peers_update:    Callable[[], None],
        on_peer_discovered: Optional[Callable[[str, dict], None]] = None,
        on_transfer_started:  Optional[Callable] = None,
        on_transfer_progress: Optional[Callable] = None,
        on_transfer_complete: Optional[Callable] = None,
        on_file_meta:         Optional[Callable] = None,
        schedule_gui:         Optional[Callable] = None,
    ) -> None:
        """Wire GUI callback references. Signatures:

        on_system(message), on_message(peer_id, sender, payload),
        on_connected(peer_id, tcp_addr), on_disconnect(tcp_addr), on_peers_update(),
        on_peer_discovered(peer_id, info), on_transfer_started(tid, filename, peer_name, direction),
        on_transfer_progress(tid, fraction), on_transfer_complete(tid, success, message),
        on_file_meta(meta, peer_name), schedule_gui(fn) — runs fn on the Tk thread.
        """
        self.on_system          = on_system
        self.on_message         = on_message
        self.on_connected       = on_connected
        self.on_disconnect      = on_disconnect
        self.on_peers_update    = on_peers_update
        self.on_peer_discovered = on_peer_discovered

        self.node: Optional[P2PNode]           = None
        self.transfer_manager: Optional[TransferManager] = None

        # GUI-thread scheduler — provided by ChatApp.
        self._schedule_gui = schedule_gui or (lambda fn: fn())

        # File-transfer GUI callbacks
        self._on_transfer_started  = on_transfer_started
        self._on_transfer_progress = on_transfer_progress
        self._on_transfer_complete = on_transfer_complete
        self._on_file_meta         = on_file_meta

        self.contact_book    = ContactBook()
        self.message_history = MessageHistory()

        # Bidirectional mapping between peer_id and tcp_address.
        self._peer_to_tcp: dict[str, str] = {}
        self._tcp_to_peer: dict[str, str] = {}
        self._map_lock = threading.Lock()

        # The currently-selected peer (set by ChatApp when user clicks a peer).
        # TransferPanel reads this to know which peer to send a file to.
        self._current_peer_id: str | None = None

    # ------------------------------------------------------------------ #
    # Node lifecycle                                                       #
    # ------------------------------------------------------------------ #

    def start_node(self, host: str, port: int, username: str) -> Tuple[bool, str]:
        """Start the P2PNode on host:port. Returns (ok, info_or_error_message)."""
        if self.node is not None:
            return False, "Node already started."

        self.node = P2PNode(
            host               = host,
            port               = port,
            username           = username,
            on_message         = self._on_message,
            on_disconnect      = self._on_disconnect,
            on_connected       = self._on_connected,
            on_peer_discovered = self._on_peer_discovered,
        )

        try:
            self.node.start_server()
        except OSError:
            logger.exception("Failed to start node")
            self.node = None
            return False, f"Could not bind to port {port}."

        # Wire file transfer after node is running.
        self.transfer_manager = TransferManager(
            node                = self.node,
            controller          = self,
            schedule_gui        = self._schedule_gui,
            on_transfer_started  = self._on_transfer_started,
            on_transfer_progress = self._on_transfer_progress,
            on_transfer_complete = self._on_transfer_complete,
            on_file_meta         = self._on_file_meta,
        )

        peer_id = self.get_local_peer_id()
        fp      = self.get_local_fingerprint()
        return True, (
            f"Started as '{username}' on port {port}. "
            f"ID: {peer_id[:8]}… | FP: {fp[:11]}…"
        )

    def stop(self) -> None:
        """Stop the node and release all resources."""
        if self.node is not None:
            self.node.stop_server()
            self.node = None

    # ------------------------------------------------------------------ #
    # Networking actions                                                   #
    # ------------------------------------------------------------------ #

    def connect_to_peer(self, ip: str, port: int) -> bool:
        """Initiate a TCP connection to ip:port."""
        if self.node is None:
            self.on_system("Start the node first.")
            return False

        if ip in ("127.0.0.1", "localhost") and port == self.node.port:
            self.on_system("Cannot connect to yourself.")
            return False

        return self.node.connect_to_peer(ip, port)

    def send_message(self, payload: str, peer_id: str) -> bool:
        """Translate peer_id to tcp_address and send payload through the node."""
        if self.node is None:
            self.on_system("Start the node first.")
            return False

        tcp_addr = self._peer_id_to_tcp(peer_id)
        if tcp_addr is None:
            logger.warning("No tcp_address for peer_id %s — cannot send", peer_id[:12])
            self.on_system(f"Peer not connected: {peer_id[:8]}…")
            return False

        ok = self.node.send_message(payload, tcp_addr)
        if ok:
            try:
                self.message_history.append_message(peer_id, {
                    "message_id": f"sent-{time.time_ns()}",
                    "peer_id":    peer_id,
                    "direction":  "sent",
                    "content":    payload,
                    "timestamp":  time.time(),
                })
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to persist sent message for %s", peer_id[:12])
        return ok

    def broadcast_message(self, payload: str) -> Tuple[int, int]:
        """Send payload to all active peers; returns (sent_count, failed_count)."""
        if self.node is None:
            self.on_system("Start the node first.")
            return 0, 0
        return self.node.broadcast_message(payload)

    def send_file(self, filepath: str, peer_id: str) -> tuple[bool, str]:
        """Validate filepath, send FILE_META to peer_id, and wait for DOWNLOAD_REQUEST.

        Returns (True, transfer_id) or (False, error_message).
        """
        if self.transfer_manager is None:
            return False, "Node not started."
        return self.transfer_manager.send_file(filepath, peer_id)

    def cancel_transfer(self, transfer_id: str) -> None:
        """Cancel an in-progress transfer."""
        if self.transfer_manager is not None:
            self.transfer_manager.cancel_transfer(transfer_id)

    def request_download(self, transfer_id: str) -> bool:
        """Request download of a received FILE_META (on receiver's Download click)."""
        if self.transfer_manager is None:
            return False
        return self.transfer_manager.request_download(transfer_id)

    def disconnect_peer(self, peer_id: str) -> bool:
        """Close the active session with peer_id."""
        if self.node is None:
            return False
        return self.node.disconnect_peer(peer_id)

    def discover_peers(self) -> None:
        """Trigger an immediate UDP discovery broadcast."""
        if self.node is not None:
            self.node.discover_peers()

    def get_discovered_peers(self) -> dict[str, dict]:
        """Return a snapshot of all currently discovered peers."""
        if self.node is not None:
            return self.node.get_discovered_peers()
        return {}

    # ------------------------------------------------------------------ #
    # Identity / trust                                                     #
    # ------------------------------------------------------------------ #

    def get_local_peer_id(self) -> str:
        """Return this node's SHA-256 peer ID, or empty string if not started."""
        if self.node is None:
            return ""
        return self.node.identity_manager.get_peer_id()

    def get_local_fingerprint(self) -> str:
        """Return this node's fingerprint, or empty string if not started."""
        if self.node is None:
            return ""
        return self.node.identity_manager.get_fingerprint()

    def get_trust_state(self, peer_id: str) -> str:
        """Return the current TOFU trust state for peer_id."""
        if self.node is None:
            return TrustState.NEW
        return self.node.get_trust_state(peer_id)

    def trust_peer(self, peer_id: str) -> None:
        """Mark peer_id as TRUSTED in the trust store."""
        if self.node is not None:
            self._apply_trust_action(self.node.trust_peer, peer_id)

    def block_peer(self, peer_id: str) -> None:
        """Mark peer_id as BLOCKED and disconnect any active session."""
        if self.node is not None:
            self._apply_trust_action(self.node.block_peer, peer_id)

    def unblock_peer(self, peer_id: str) -> None:
        """Remove BLOCKED state from peer_id (restore to TRUSTED)."""
        if self.node is not None:
            self._apply_trust_action(self.node.unblock_peer, peer_id)

    def _apply_trust_action(self, action: Callable[[str], bool], peer_id: str) -> None:
        """Run a node trust action and report to on_system if peer_id was unknown."""
        if not action(peer_id):
            self.on_system(f"Peer {peer_id[:8]}… not found in trust store.")

    def get_peer_info(self, peer_id: str) -> Optional[dict]:
        """Return the peer info dict for peer_id, or None if unknown."""
        return self.get_discovered_peers().get(peer_id)

    # ------------------------------------------------------------------ #
    # Address translation helpers                                          #
    # ------------------------------------------------------------------ #

    def register_peer_tcp(self, peer_id: str, tcp_addr: str) -> None:
        """Register a bidirectional peer_id <-> tcp_address mapping."""
        with self._map_lock:
            self._peer_to_tcp[peer_id]  = tcp_addr
            self._tcp_to_peer[tcp_addr] = peer_id

    def _peer_id_to_tcp(self, peer_id: str) -> Optional[str]:
        """Resolve peer_id to the active TCP session address.

        Resolution order:
        1. Explicit mapping registered at handshake time.
        2. Search node.peers for an active session with the peer's IP
           (bridges discovery listen-port vs OS-assigned ephemeral port).
        3. Discovery tcp_address as last resort (initiator side only).
        """
        with self._map_lock:
            tcp = self._peer_to_tcp.get(peer_id)
        if tcp:
            return tcp

        if self.node is None:
            return None

        # Discovery stores the listen port; the actual connection may use
        # an ephemeral port on the remote side (when we are the acceptor).
        info = self.get_discovered_peers().get(peer_id)
        if info:
            ip = info.get("ip")
            if ip:
                # Search all active sessions for this IP.
                active = self.node.get_active_session_for_ip(ip)
                if active:
                    self.register_peer_tcp(peer_id, active)
                    return active
            # Only return the stored tcp_address if the peer is still
            # marked connected — otherwise it's stale from a previous session.
            if info.get("connected"):
                return info.get("tcp_address")
        return None

    def _tcp_to_peer_id(self, tcp_addr: str) -> Optional[str]:
        with self._map_lock:
            return self._tcp_to_peer.get(tcp_addr)

    def set_current_peer(self, peer_id: Optional[str]) -> None:
        """Set the currently-selected peer (called by GUI when user clicks a peer)."""
        self._current_peer_id = peer_id

    def get_peer_id_for_tcp(self, tcp_addr: str) -> Optional[str]:
        """Return the peer_id registered for tcp_addr, or None.

        Public accessor so the GUI can resolve tcp_addr -> peer_id on
        disconnect events without reaching into the internal mapping.
        """
        with self._map_lock:
            return self._tcp_to_peer.get(tcp_addr)

    # ------------------------------------------------------------------ #
    # Internal callbacks                                                   #
    # ------------------------------------------------------------------ #

    def _safe_fire(self, cb: Optional[Callable], *args) -> None:
        """Call *cb* with *args*, swallowing and logging any exceptions."""
        if cb is None:
            return
        try:
            cb(*args)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Controller callback raised an exception")

    def _on_message(self, peer_id: str, sender: str, payload: str) -> None:
        """Forward an inbound message (routed by peer_id) to the app callback."""
        self._safe_fire(self.on_message, peer_id, sender, payload)

    def _on_connected(self, peer_id: str, tcp_addr: str) -> None:
        """Register the peer_id/tcp_addr mapping and notify the GUI."""
        if peer_id and tcp_addr:
            self.register_peer_tcp(peer_id, tcp_addr)
        self._safe_fire(self.on_connected, peer_id, tcp_addr)
        self._safe_fire(self.on_peers_update)

    def _on_disconnect(self, tcp_addr: str) -> None:
        with self._map_lock:
            peer_id = self._tcp_to_peer.pop(tcp_addr, None)
            if peer_id:
                self._peer_to_tcp.pop(peer_id, None)
        if self.transfer_manager:
            self.transfer_manager.cleanup_peer(tcp_addr)
        self._safe_fire(self.on_disconnect, tcp_addr)
        self._safe_fire(self.on_peers_update)

    def _on_peer_discovered(self, peer_id: str, info: dict) -> None:
        logger.debug("[CTRL] peer discovered: %s (%s)", peer_id[:12], info.get("username"))
        tcp_addr = info.get("tcp_address")
        if tcp_addr:
            self.register_peer_tcp(peer_id, tcp_addr)
        self._safe_fire(self.on_peer_discovered, peer_id, info)
        self._safe_fire(self.on_peers_update)
