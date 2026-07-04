"""UIState: lightweight GUI-side state holder for discovered peers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIState:
    """Transient GUI state updated from network callbacks, read back when
    rebuilding the sidebar/chat header."""

    # Peers seen via discovery/handshake, keyed by peer_id (SHA-256 hex).
    discovered_peers: dict[str, dict[str, Any]] = field(default_factory=dict)

    # The peer_id the user has selected in the sidebar.
    active_peer_id: str | None = None

    # Overall connection status string ("offline" | "connected" | …)
    connection_status: str = "offline"

    # ------------------------------------------------------------------ #
    # Peer registry                                                        #
    # ------------------------------------------------------------------ #

    def update_discovered_peer(self, peer_id: str,
                                peer_info: dict[str, Any]) -> None:
        """Insert or replace peer_id in the registry."""
        self.discovered_peers[peer_id] = peer_info

    # ------------------------------------------------------------------ #
    # Selection                                                            #
    # ------------------------------------------------------------------ #

    def select_peer(self, peer_id: str | None) -> None:
        """Set the currently-selected peer (None to deselect)."""
        self.active_peer_id = peer_id

    # ------------------------------------------------------------------ #
    # Session status                                                       #
    # ------------------------------------------------------------------ #

    def set_connection_status(self, status: str) -> None:
        """Update the overall connection status string ("offline", "connected", ...)."""
        self.connection_status = status
