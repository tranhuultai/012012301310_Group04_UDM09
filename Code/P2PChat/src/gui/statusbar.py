"""StatusBar: bottom status bar with 5 segments."""
from __future__ import annotations
import customtkinter as ctk
from gui import theme as T


class StatusBar(ctk.CTkFrame):
    """5-segment bottom bar.  Attributes _status_label / _stats_label for tests."""

    def __init__(self, master, **kw) -> None:
        super().__init__(master, height=32, corner_radius=0,
                         fg_color=T.BG_APP, **kw)
        self.grid_columnconfigure(1, weight=1)

        self._status_label = ctk.CTkLabel(
            self, text="🔄 Initializing...",
            font=("Segoe UI", 9), text_color=T.TEXT_MUTED, anchor="w")
        self._status_label.grid(row=0, column=0, sticky="w", padx=14, pady=6)

        self._disc_label = ctk.CTkLabel(
            self, text="◎  Discovery: —",
            font=("Segoe UI", 9), text_color=T.TEXT_MUTED, anchor="w")
        self._disc_label.grid(row=0, column=1, sticky="w", padx=8)

        self._conn_label = ctk.CTkLabel(
            self, text="⎋  Not connected",
            font=("Segoe UI", 9), text_color=T.TEXT_MUTED, anchor="w")
        self._conn_label.grid(row=0, column=2, sticky="w", padx=8)

        self._enc_label = ctk.CTkLabel(
            self, text="🔓  —",
            font=("Segoe UI", 9), text_color=T.TEXT_MUTED, anchor="w")
        self._enc_label.grid(row=0, column=3, sticky="w", padx=8)

        self._stats_label = ctk.CTkLabel(
            self, text="Peers: 0",
            font=("Segoe UI", 9), text_color=T.TEXT_MUTED, anchor="e")
        self._stats_label.grid(row=0, column=4, sticky="e", padx=14)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def set_status(self, text: str, color: str = T.TEXT_MUTED) -> None:
        """Set the primary status segment (also used by tests)."""
        self._status_label.configure(text=text, text_color=color)

    def set_identity(self, peer_id: str, _fingerprint: str) -> None:
        """Show local identity hash in segment 1."""
        # Shorten for status bar — full values visible in peer details
        self._status_label.configure(
            text=f"🟢 Online  ·  ID {peer_id[:12]}…",
            text_color=T.SUCCESS)

    def set_discovery(self, active: bool) -> None:
        """Toggle the discovery segment."""
        if active:
            self._disc_label.configure(
                text="◉  Discovery: Active", text_color=T.SUCCESS)
        else:
            self._disc_label.configure(
                text="◎  Discovery: —", text_color=T.TEXT_MUTED)

    def set_connected_peer(self, label: str) -> None:
        """Show connected peer in segment 3."""
        self._conn_label.configure(
            text=f"⎋  {label}", text_color=T.ACCENT)

    def set_disconnected(self) -> None:
        """Reset segment 3 to disconnected state."""
        self._conn_label.configure(
            text="⎋  Not connected", text_color=T.TEXT_MUTED)
        self._enc_label.configure(
            text="🔓  —", text_color=T.TEXT_MUTED)

    def set_encrypted(self, active: bool) -> None:
        """Toggle the encryption segment."""
        if active:
            self._enc_label.configure(
                text="🔒  AES-256", text_color=T.SUCCESS)
        else:
            self._enc_label.configure(
                text="🔓  —", text_color=T.TEXT_MUTED)

    def set_stats(self, peers: int = 0, connected: int = 0,
                  contacts: int = 0) -> None:
        """Update the right-hand stats segment. Exact format tested by test_statusbar."""
        self._stats_label.configure(
            text=f"Peers:{peers} | Connected:{connected} | Contacts:{contacts}  ")
        self.set_encrypted(connected > 0)
        if peers > 0:
            self.set_discovery(True)

    # Compat shims
    def set_initializing(self)          -> None:
        """Show initialising state."""
        self.set_status("🔄 Initializing...", T.TEXT_MUTED)

    def set_discovery_running(self)     -> None:
        """Show discovery running."""
        self.set_discovery(True)

    def set_connected(self, peer: str)  -> None:
        """Show connected state in segment 1."""
        self.set_status(f"Connected: {peer}", T.ACCENT)

    def set_handshake(self)             -> None:
        """Show handshake state."""
        self.set_status("Handshake…", T.WARNING)

    def set_error(self, msg: str)       -> None:
        """Show error in segment 1."""
        self.set_status(f"⚠ {msg}", T.DANGER)
