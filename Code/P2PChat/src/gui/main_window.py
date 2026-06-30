"""MainWindow: 3-column layout — Sidebar | Chat | PeerDetails."""
from __future__ import annotations

from typing import Callable
import customtkinter as ctk

from gui import theme as T
from gui.chatbox import ChatBox
from gui.sidebar import Sidebar
from gui.peer_details import PeerDetails
from gui.statusbar import StatusBar
from gui.transfer_panel import TransferPanel
from gui.trust_dialog import TrustDialog
from trust.trust_state import TrustState

_TRUST_FG = {
    TrustState.NEW:      T.TRUST_NEW,
    TrustState.TRUSTED:  T.TRUST_TRUSTED,
    TrustState.VERIFIED: T.TRUST_VERIFIED,
    TrustState.MISMATCH: T.TRUST_MISMATCH,
    TrustState.BLOCKED:  T.TRUST_BLOCKED,
}
_STATUS_ICON = {"online": "●", "connected": "●", "offline": "○"}
_TRUST_SHORT: dict[str, str] = {
    "NEW":      "New",
    "TRUSTED":  "✓ Trusted",
    "VERIFIED": "✔ Verified",
    "MISMATCH": "⚠ Key Changed",
    "BLOCKED":  "⊘ Blocked",
}


class MainWindow(ctk.CTkFrame):
    """Three-column root frame wired to ChatApp callbacks."""

    def __init__(self, master,
                 on_peer_select=None, on_peer_connect=None,
                 on_trust=None, on_block=None,
                 on_manual_connect=None, on_broadcast=None,
                 on_disconnect=None, on_add_contact=None) -> None:
        super().__init__(master, fg_color=T.BG_APP)
        self.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)

        self._build_sidebar(on_peer_select, on_peer_connect, on_manual_connect)
        self._build_chat(on_broadcast)
        self._build_details(on_trust, on_block, on_peer_connect, on_disconnect,
                             on_add_contact)
        self._build_statusbar()

    # ------------------------------------------------------------------ #
    # Construction                                                         #
    # ------------------------------------------------------------------ #

    def _build_sidebar(self, on_peer_select, on_peer_connect, on_manual_connect) -> None:
        self.sidebar = Sidebar(
            self,
            on_peer_select    = on_peer_select,
            on_peer_connect   = on_peer_connect,
            on_manual_connect = on_manual_connect)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

    def _build_chat(self, on_broadcast) -> None:
        col = ctk.CTkFrame(self, fg_color=T.BG_MAIN, corner_radius=0)
        col.grid(row=0, column=1, sticky="nsew")
        col.grid_rowconfigure(1, weight=1)
        col.grid_rowconfigure(2, weight=0)   # separator
        col.grid_rowconfigure(3, weight=0)   # input bar
        col.grid_columnconfigure(0, weight=1)

        # ── Header ───────────────────────────────────────────────────
        hdr = ctk.CTkFrame(col, height=68, fg_color=T.BG_HEADER, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)  # text column grows
        hdr.grid_columnconfigure(2, weight=0)  # security badge — fixed
        hdr.grid_propagate(False)

        # Avatar
        self._hdr_av = ctk.CTkFrame(
            hdr, width=42, height=42, corner_radius=21, fg_color=T.ACCENT)
        self._hdr_av.grid(row=0, column=0, rowspan=2, padx=(16, 10), pady=13)
        self._hdr_av.grid_propagate(False)
        self._hdr_av_lbl = ctk.CTkLabel(
            self._hdr_av, text="💬", font=("Segoe UI", 18))
        self._hdr_av_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self.chat_target_label = ctk.CTkLabel(
            hdr, text="P2PChat",
            font=(T.FONT, 14, "bold"), text_color=T.TEXT_PRI, anchor="w")
        self.chat_target_label.grid(row=0, column=1, sticky="sw", pady=(14, 0))

        self.chat_info_label = ctk.CTkLabel(
            hdr, text="Secure local network messaging",
            text_color=T.TEXT_MUTED, font=(T.FONT, 10), anchor="w")
        self.chat_info_label.grid(row=1, column=1, sticky="nw", pady=(0, 14))

        # Security badge — always visible in header (matches Lumina design)
        enc_badge = ctk.CTkFrame(hdr, fg_color=T.ACCENT_DIM, corner_radius=8)
        enc_badge.grid(row=0, column=2, rowspan=2, padx=(0, 14), pady=12)
        ctk.CTkLabel(enc_badge, text="🔒 End-to-End Encrypted",
                     font=(T.FONT, 10), text_color=T.TEXT_LINK,
                     ).pack(padx=10, pady=5)

        # Thin bottom border on header
        ctk.CTkFrame(col, height=1, fg_color=T.BORDER).grid(
            row=0, column=0, sticky="sew")

        # ── Chat area ────────────────────────────────────────────────
        self.chat_box = ChatBox(col)
        self.chat_box.grid(row=1, column=0, sticky="nsew")

        # Empty state — inspired by Lumina "Start a Secure Session" screen
        self._empty_frame = ctk.CTkFrame(col, fg_color="transparent")
        self._empty_frame.place(relx=0.5, rely=0.46, anchor="center")

        ctk.CTkLabel(self._empty_frame,
                     text="🔒", font=("Segoe UI Emoji", 48),
                     text_color=T.ACCENT_DIM).pack()
        ctk.CTkLabel(self._empty_frame,
                     text="Start a Secure Session",
                     font=(T.FONT, 18, "bold"), text_color=T.TEXT_PRI,
                     ).pack(pady=(12, 6))
        ctk.CTkLabel(self._empty_frame,
                     text="Select a peer from the sidebar to open an\n"
                          "end-to-end encrypted conversation.",
                     font=(T.FONT, 12), text_color=T.TEXT_MUTED,
                     justify="center").pack()

        badges_row = ctk.CTkFrame(self._empty_frame, fg_color="transparent")
        badges_row.pack(pady=(20, 0))
        for badge_text in ("🔐 End-to-End Encrypted",
                           "⚡ Direct P2P", "📡 LAN Discovery"):
            ctk.CTkFrame(badges_row,
                         fg_color=T.ACCENT_DIM, corner_radius=12,
                         ).pack(side="left", padx=4)
            ctk.CTkLabel(badges_row.winfo_children()[-1],
                         text=badge_text,
                         font=(T.FONT, 10), text_color=T.TEXT_LINK,
                         ).pack(padx=10, pady=5)

        # Keep _empty as alias for show/hide compatibility
        self._empty = self._empty_frame

        # ── Input bar ────────────────────────────────────────────────
        # Thin separator above input bar
        ctk.CTkFrame(col, height=1, fg_color=T.BORDER).grid(
            row=2, column=0, sticky="ew")

        input_bar = ctk.CTkFrame(col, fg_color=T.BG_INPUT, corner_radius=0, height=64)
        input_bar.grid(row=3, column=0, sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)   # entry fills row
        input_bar.grid_propagate(False)

        self.message_entry = ctk.CTkEntry(
            input_bar,
            placeholder_text="Type a message…",
            placeholder_text_color=T.TEXT_MUTED,
            height=40, corner_radius=20,
            fg_color=T.BG_ENTRY,
            border_color=T.BORDER_LIGHT,
            border_width=1,
            text_color=T.TEXT_PRI, font=("Segoe UI", 13))
        self.message_entry.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=12)

        self.send_button = ctk.CTkButton(
            input_bar, text="Send", width=74, height=40, corner_radius=20,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOV,
            text_color="#fff", font=("Segoe UI", 12, "bold"))
        self.send_button.grid(row=0, column=1, padx=(0, 8), pady=12)

        if on_broadcast:
            ctk.CTkButton(
                input_bar, text="📢", width=38, height=38, corner_radius=19,
                fg_color=T.BG_FIELD, hover_color=T.BORDER_LIGHT,
                font=("Segoe UI Emoji", 13), command=on_broadcast,
            ).grid(row=0, column=2, padx=(0, 10), pady=13)

    def _build_details(self, on_trust, on_block, on_connect,
                       on_disconnect=None, on_add_contact=None) -> None:
        # Right column: PeerDetails on top, TransferPanel below.
        right_col = ctk.CTkFrame(self, fg_color="transparent")
        right_col.grid(row=0, column=2, sticky="nsew")
        right_col.grid_rowconfigure(0, weight=1)
        right_col.grid_rowconfigure(1, weight=0)
        right_col.grid_columnconfigure(0, weight=1)

        self.details_panel = PeerDetails(
            right_col, on_trust=on_trust, on_block=on_block,
            on_connect=on_connect, on_disconnect=on_disconnect,
            on_add_contact=on_add_contact)
        self.details_panel.grid(row=0, column=0, sticky="nsew")

        self.transfer_panel = TransferPanel(right_col, controller=None)
        self.transfer_panel.grid(row=1, column=0, sticky="ew",
                                 padx=4, pady=(0, 10))

    def _build_statusbar(self) -> None:
        self.status_bar = StatusBar(self)
        self.status_bar.grid(row=1, column=0, columnspan=3, sticky="ew")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def set_send_callback(self, cb: Callable) -> None:
        """Wire send button and Return key."""
        self.send_button.configure(command=cb)
        self.message_entry.bind("<Return>", lambda _: cb())

    # Chat
    def add_system_message(self, msg: str) -> None:
        """Add a system notice bubble."""
        self.chat_box.add_system(msg)

    def add_received_message(self, sender: str, msg: str) -> None:
        """Add a received message bubble."""
        self.chat_box.add_received(sender, msg)

    def add_file_sent_message(self, sender: str, filename: str, size_str: str) -> None:
        """Show a right-aligned sent-file card (no download button).

        Args:
            sender: Local display name.
            filename: File name sent.
            size_str: Pre-formatted size string.
        """
        self.chat_box.add_file_sent(sender, filename, size_str)

    def add_file_message(self, sender: str, filename: str,
                         size_str: str, on_download=None) -> None:
        """Show a file-offer card in the chat area.

        Args:
            sender: Display name of the sender.
            filename: File name.
            size_str: Pre-formatted size string.
            on_download: Callback when user clicks Download.
        """
        self.chat_box.add_file_message(sender, filename, size_str, on_download)

    def add_sent_message(self, sender: str, recipient: str, msg: str) -> None:
        """Add a sent message bubble."""
        self.chat_box.add_sent(sender, recipient, msg)

    def clear_chat(self) -> None:
        """Clear all messages."""
        self.chat_box.clear()

    def show_empty_state(self) -> None:
        """Show the welcome / no-peer-selected placeholder in the chat area."""
        self._empty_frame.place(relx=0.5, rely=0.46, anchor="center")
        self.chat_target_label.configure(text="P2PChat")
        self.chat_info_label.configure(text="Secure local network messaging",
                                       text_color=T.TEXT_MUTED)
        self._hdr_av.configure(fg_color=T.ACCENT_DIM)
        self._hdr_av_lbl.configure(text="🔒", font=("Segoe UI Emoji", 18))

    def get_message_text(self) -> str:
        """Return text entry content."""
        return self.message_entry.get()

    def clear_message_text(self) -> None:
        """Clear text entry."""
        self.message_entry.delete(0, "end")

    def focus_message_box(self) -> None:
        """Focus text entry."""
        self.message_entry.focus_set()

    # Peer list
    def update_discovered_peers(self, peers: dict) -> None:
        """Rebuild the sidebar peer list."""
        self.sidebar.update_peers(peers)

    # Details panel
    def update_peer_details(self, peer_info: dict) -> None:
        """Refresh the right-hand details panel."""
        self.details_panel.update_peer(peer_info)

    # Chat header
    def set_active_chat(self, username: str,
                        status: str = "offline",
                        trust_state: str = "NEW") -> None:
        """Update the chat header for the active peer."""
        self._empty_frame.place_forget()

        icon     = _STATUS_ICON.get(status, "○")
        dot      = T.STATUS_DOT.get(status, T.STATUS_DOT["offline"])
        trust_fg = _TRUST_FG.get(trust_state, T.TEXT_MUTED)

        self._hdr_av.configure(fg_color=T.avatar_color(username))
        self._hdr_av_lbl.configure(
            text=username[0].upper() if username else "?",
            font=("Segoe UI", 15, "bold"))
        self.chat_target_label.configure(text=username)
        # Use trust colour for the info line so the trust state is visually
        # prominent (e.g. VERIFIED = green, MISMATCH = red).
        trust_short = _TRUST_SHORT.get(trust_state, trust_state)
        self.chat_info_label.configure(
            text=f"{icon} {status.capitalize()}  ·  {trust_short}",
            text_color=trust_fg if trust_state != "NEW" else dot)

    # Transfer Panel API
    def on_transfer_started(self, tid: str, filename: str,
                            peer: str, direction: str) -> None:
        """Notify the transfer panel that a transfer has begun.

        Args:
            tid: Unique transfer ID.
            filename: Name of the file being transferred.
            peer: Display name of the remote peer.
            direction: "out" or "in".
        """
        self.transfer_panel.add_transfer(tid, filename, peer, direction)

    def on_transfer_progress(self, tid: str, progress: float) -> None:
        """Update the progress bar for *tid*.

        Args:
            tid: Unique transfer ID.
            progress: Value in [0, 1].
        """
        self.transfer_panel.update_transfer(tid, progress)

    def on_transfer_complete(self, tid: str) -> None:
        """Remove the transfer card for *tid* (transfer finished or cancelled).

        Args:
            tid: Unique transfer ID.
        """
        self.transfer_panel.remove_transfer(tid)

    # Status bar wrappers
    def set_status(self, text: str, color: str = T.TEXT_MUTED) -> None:
        """Set main status bar segment 1."""
        self.status_bar.set_status(text, color)

    def set_identity(self, peer_id: str, fingerprint: str) -> None:
        """Show local identity in status bar."""
        self.status_bar.set_identity(peer_id, fingerprint)
        self.status_bar.set_discovery(True)

    def update_stats(self, peers: int = 0, connected: int = 0,
                     contacts: int = 0) -> None:
        """Update peer-count segment."""
        self.status_bar.set_stats(peers=peers, connected=connected,
                                  contacts=contacts)

    # Trust dialog
    def show_trust_dialog(self, mode: str, peer_info: dict,
                          callback: Callable) -> None:
        """Display the TOFU security verification dialog.

        Args:
            mode: "new_peer" or "warning".
            peer_info: PeerInfo dict passed to the dialog.
            callback: Called with the user's decision string.
        """
        TrustDialog(self.master, mode=mode, peer_info=peer_info,
                    callback=callback)

    # Compat shims
    def set_trust_callback(self, cb) -> None:
        """Compat shim."""
        self.details_panel.set_trust_callback(cb)

    def set_block_callback(self, cb) -> None:
        """Compat shim."""
        self.details_panel.set_block_callback(cb)
