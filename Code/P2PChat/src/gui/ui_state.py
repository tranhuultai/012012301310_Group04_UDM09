"""UIState: lightweight GUI-side state holder for discovered peers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIState:
    """Holds transient GUI state in one place.

    Rationale
    ---------
    Network callbacks arrive on background threads.  The ChatApp schedules
    state updates here via after(0, ...), then reads from this object
    when rebuilding the sidebar or resolving peer info for the chat header.

    Keeping a single discovered_peers dict here avoids passing raw dicts
    through multiple callback hops and gives one authoritative read path for
    all GUI components.

    Only the fields and methods actually used by ChatApp are included.
    """

    # Registry of all peers seen via UDP discovery or TCP handshake.
    # Keys: peer_id (SHA-256 hex).  Values: plain peer info dicts.
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
        """Insert or replace *peer_id* in the registry.

        Args:
            peer_id: SHA-256 peer identifier.
            peer_info: Peer info dict from discovery or handshake.
        """
        self.discovered_peers[peer_id] = peer_info

    # ------------------------------------------------------------------ #
    # Selection                                                            #
    # ------------------------------------------------------------------ #

    def select_peer(self, peer_id: str | None) -> None:
        """Set the currently-selected peer.

        Args:
            peer_id: SHA-256 peer identifier, or None to deselect.
        """
        self.active_peer_id = peer_id

    # ------------------------------------------------------------------ #
    # Session status                                                       #
    # ------------------------------------------------------------------ #

    def set_connection_status(self, status: str) -> None:
        """Update the overall connection status string.

        Args:
            status: One of "offline", "connected", etc.
        """
        self.connection_status = status
