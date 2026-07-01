"""PeerDetails: right-hand panel — peer info, trust, and action buttons."""
from __future__ import annotations

from typing import Callable, Optional
import customtkinter as ctk
from gui import theme as T
from trust.trust_state import TrustState

_TRUST_LABELS: dict[str, str] = {
    "NEW":      "New peer",
    "TRUSTED":  "Trusted",
    "VERIFIED": "Verified",
    "MISMATCH": "Key changed ⚠",
    "BLOCKED":  "Blocked",
}


class PeerDetails(ctk.CTkFrame):
    """Right panel: avatar, metadata rows, and action buttons."""

    def __init__(self, master,
                 on_trust=None, on_block=None,
                 on_connect=None, on_disconnect=None,
                 on_add_contact=None, **kw) -> None:
        super().__init__(master, fg_color=T.BG_DETAILS, width=272,
                         corner_radius=0, **kw)
        self.grid_propagate(False)
        self._on_trust       = on_trust
        self._on_block       = on_block
        self._on_connect     = on_connect
        self._on_disconnect  = on_disconnect
        self._on_add_contact = on_add_contact
        self.current_peer_id:    Optional[str]  = None
        self._current_peer_info: Optional[dict] = None
        self._is_connected:      bool           = False
        self._build()

    # ------------------------------------------------------------------ #
    # Build                                                                #
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        # ── Section header ────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=T.BG_HEADER, corner_radius=0, height=42)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Peer Details",
                     font=(T.FONT, 11, "bold"), text_color=T.TEXT_PRI,
                     anchor="w").pack(side="left", padx=16, fill="y")

        # Spacer below header
        ctk.CTkFrame(self, height=1, fg_color=T.BORDER).pack(fill="x")
        ctk.CTkFrame(self, height=10, fg_color="transparent").pack(fill="x")

        # ── Avatar card ──────────────────────────────────────────────
        av_card = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=14)
        av_card.pack(fill="x", padx=12, pady=(0, 8))

        av_wrap = ctk.CTkFrame(av_card, fg_color="transparent")
        av_wrap.pack(pady=(16, 6))

        self._av_ring = ctk.CTkFrame(av_wrap, width=64, height=64,
                                     corner_radius=32, fg_color=T.ACCENT)
        self._av_ring.pack()
        self._av_ring.pack_propagate(False)
        self._av_lbl = ctk.CTkLabel(self._av_ring, text="?",
                                    font=("Segoe UI", 24, "bold"), text_color="#fff")
        self._av_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self._name_lbl = ctk.CTkLabel(av_card, text="No peer selected",
                                      font=("Segoe UI", 14, "bold"),
                                      text_color=T.TEXT_PRI)
        self._name_lbl.pack()

        self._status_lbl = ctk.CTkLabel(av_card, text="● Offline",
                                        font=("Segoe UI", 10),
                                        text_color=T.STATUS_DOT["offline"])
        self._status_lbl.pack(pady=(2, 14))

        # ── Info rows ────────────────────────────────────────────────
        info_card = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=14)
        info_card.pack(fill="x", padx=12, pady=(0, 8))

        _, self._pid_val  = self._row(info_card, "Peer ID",     "—")
        _, self._ip_val   = self._row(info_card, "IP",          "—")
        _, self._port_val = self._row(info_card, "Port",        "—")
        _, self._fp_val   = self._row(info_card, "Fingerprint", "—", mono=True, wrap=True)
        self._trust_row, self._trust_val = self._row(info_card, "Trust", "—")

        # ── Action buttons ───────────────────────────────────────────
        btn_card = ctk.CTkFrame(self, fg_color="transparent")
        btn_card.pack(fill="x", padx=12, pady=(0, 12))

        # Icons chosen to match widely-recognized equivalents from
        # Messenger/Zalo/Telegram (link/plug/plus/no-entry) instead of the
        # previous Unicode Technical-block glyphs (⎋ ⊘ ⭐), which aren't in
        # the emoji set and render as flat, hard-to-read symbols.
        self._connect_btn = self._btn(
            btn_card, "🔗  Connect",
            fg=T.ACCENT_DIM, hover=T.ACCENT_GLOW, tc=T.TEXT_LINK,
            cmd=self._do_connect)

        self._trust_btn = self._btn(
            btn_card, "✓  Trust / Verify",
            fg=T.BTN_SUCCESS_BG, hover=T.BTN_SUCCESS_HOV, tc=T.SUCCESS,
            cmd=self._do_trust)

        self._contact_btn = self._btn(
            btn_card, "➕  Add Contact",
            fg=T.BG_FIELD, hover=T.BG_CARD, tc=T.TEXT_SEC,
            cmd=self._do_add_contact)

        self._block_btn = self._btn(
            btn_card, "🚫  Block Peer",
            fg=T.DANGER_DIM, hover=T.BTN_DANGER_HOV, tc=T.DANGER,
            cmd=self._do_block)

    @staticmethod
    def _btn(parent, label, fg, hover, tc, cmd) -> ctk.CTkButton:
        b = ctk.CTkButton(parent, text=label, height=38, corner_radius=10,
                          fg_color=fg, hover_color=hover, text_color=tc,
                          font=("Segoe UI", 12, "bold"), command=cmd)
        b.pack(fill="x", pady=(0, 5))
        return b

    def _row(self, parent, label: str, value: str,
             mono: bool = False, wrap: bool = False) -> tuple:
        """Create a label+value row. Always returns (row_frame, value_label)."""
        row = ctk.CTkFrame(parent, fg_color=T.BG_FIELD, corner_radius=8)
        row.pack(fill="x", padx=10, pady=2)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label, font=("Segoe UI", 9, "bold"),
                     text_color=T.TEXT_SEC, anchor="w", width=88,
                     ).grid(row=0, column=0, sticky="nw", padx=(10, 0), pady=8)
        val = ctk.CTkLabel(row, text=value,
                           font=("Consolas", 9) if mono else ("Segoe UI", 10),
                           text_color=T.TEXT_PRI, anchor="w",
                           wraplength=130 if wrap else 0, justify="left")
        val.grid(row=0, column=1, sticky="w", padx=(4, 10), pady=8)
        return row, val

    # ------------------------------------------------------------------ #
    # Public                                                               #
    # ------------------------------------------------------------------ #

    def update_peer(self, peer_info: dict) -> None:
        """Refresh all widgets from *peer_info*."""
        self._current_peer_info = peer_info
        self.current_peer_id    = peer_info.get("peer_id")
        self._is_connected      = bool(peer_info.get("connected"))

        username  = peer_info.get("username") or "Unknown"
        status    = peer_info.get("status", "offline")
        connected = bool(peer_info.get("connected"))
        trust     = peer_info.get("trust_state", TrustState.NEW)
        ip        = peer_info.get("ip") or "—"
        port      = str(peer_info.get("port") or "—")
        fp        = peer_info.get("fingerprint") or "—"
        pid       = peer_info.get("peer_id") or "—"

        av_col    = T.avatar_color(username)
        dot_s     = "connected" if connected else status
        dot_col   = T.STATUS_DOT.get(dot_s, T.STATUS_DOT["offline"])
        trust_fg  = T.TRUST_FG.get(trust, T.TEXT_MUTED)
        trust_bg  = T.TRUST_BG.get(trust, T.BG_FIELD)
        status_str = "Connected" if connected else status.capitalize()

        self._av_ring.configure(fg_color=av_col)
        self._av_lbl.configure(text=username[0].upper())
        self._name_lbl.configure(text=username)
        self._status_lbl.configure(
            text=f"● {status_str}", text_color=dot_col)

        # Peer ID — truncated
        pid_disp = f"{pid[:16]}…" if len(pid) > 18 else pid
        self._pid_val.configure(text=pid_disp)
        self._ip_val.configure(text=ip)
        self._port_val.configure(text=port)

        # Fingerprint — wrap at 8 pairs
        if ":" in fp:
            parts = fp.split(":")
            fp = "\n".join(":".join(parts[i:i+8]) for i in range(0, len(parts), 8))
        self._fp_val.configure(text=fp)

        # Trust row
        self._trust_val.configure(text=_TRUST_LABELS.get(trust, trust),
                                   text_color=trust_fg)
        self._trust_row.configure(fg_color=trust_bg)

        # Connect button doubles as Disconnect when a session is active.
        if connected:
            self._connect_btn.configure(
                text="🔌  Disconnect", state="normal",
                fg_color=T.DANGER_DIM, hover_color=T.BTN_DANGER_HOV,
                text_color=T.DANGER)
        else:
            self._connect_btn.configure(
                text="🔗  Connect", state="normal",
                fg_color=T.ACCENT_DIM, hover_color=T.ACCENT_GLOW,
                text_color=T.TEXT_LINK)

        # Trust/Block button states — mutually exclusive actions
        if trust == TrustState.BLOCKED:
            self._trust_btn.configure(
                text="🔓  Unblock Peer",
                fg_color=T.BTN_SUCCESS_BG, text_color=T.SUCCESS)
            self._block_btn.configure(
                text="🚫  Blocked",
                state="disabled", fg_color=T.BG_FIELD, text_color=T.TEXT_MUTED)
        elif trust == TrustState.MISMATCH:
            self._trust_btn.configure(
                text="⚠  Accept New Key",
                fg_color=T.BTN_WARN_BG, text_color=T.WARNING)
            self._block_btn.configure(
                text="🚫  Block Peer",
                state="normal", fg_color=T.DANGER_DIM, text_color=T.DANGER)
        else:
            self._trust_btn.configure(
                text="✓  Trust / Verify",
                fg_color=T.BTN_SUCCESS_BG, text_color=T.SUCCESS)
            self._block_btn.configure(
                text="🚫  Block Peer",
                state="normal", fg_color=T.DANGER_DIM, text_color=T.DANGER)

    # Compat shims
    def set_trust_callback(self, cb: Callable) -> None:
        """Set the trust callback (compat shim)."""
        self._on_trust = cb

    def set_block_callback(self, cb: Callable) -> None:
        """Set the block callback (compat shim)."""
        self._on_block = cb

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _do_connect(self) -> None:
        """Connect, or disconnect if a session is already active."""
        if not self.current_peer_id:
            return
        if self._is_connected:
            if self._on_disconnect:
                self._on_disconnect(self.current_peer_id)
        elif self._on_connect and self._current_peer_info:
            self._on_connect(self.current_peer_id, self._current_peer_info)

    def _do_trust(self) -> None:
        if self.current_peer_id and self._on_trust:
            self._on_trust(self.current_peer_id)

    def _do_block(self) -> None:
        if self.current_peer_id and self._on_block:
            self._on_block(self.current_peer_id)

    def _do_add_contact(self) -> None:
        if self.current_peer_id and self._on_add_contact:
            self._on_add_contact(self.current_peer_id)
