"""Sidebar: peer list, search, and manual-connect panel."""
from __future__ import annotations

import time
from typing import Callable, Optional
import customtkinter as ctk

from gui import theme as T
from trust.trust_state import TrustState


_TRUST_LABELS: dict[str, str] = {
    "NEW":      "New peer",
    "TRUSTED":  "Trusted",
    "VERIFIED": "Verified",
    "MISMATCH": "Key changed",
    "BLOCKED":  "Blocked",
}


def _time_ago(ts: float) -> str:
    """Return a human-readable elapsed-time string for *ts*.

    Args:
        ts: Unix timestamp of the last-seen moment.

    Returns:
        Short relative string such as "Just now", "5m ago", "2h ago".
    """
    diff = time.time() - ts
    if diff < 5:
        return "Just now"
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


class PeerCard(ctk.CTkFrame):
    """One peer row — uses pack layout to avoid column-width conflicts."""

    def __init__(self, master, peer_id: str, peer_info: dict,
                 on_select=None, on_connect=None, **kw) -> None:
        super().__init__(master, corner_radius=12,
                         fg_color=T.BG_CARD, height=84, **kw)
        self.peer_id     = peer_id
        self.peer_info   = peer_info
        self._on_select  = on_select
        self._on_connect = on_connect
        self._selected   = False
        self.pack_propagate(False)
        self._build()
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))

    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        """Pack-only layout — no grid to avoid child overflow past card height."""
        for w in self.winfo_children():
            w.destroy()

        info      = self.peer_info
        username  = info.get("username") or "Unknown"
        status    = info.get("status", "offline")
        trust     = info.get("trust_state", TrustState.NEW) or ""
        connected = bool(info.get("connected"))
        ip        = info.get("ip", "")
        port_num  = int(info.get("port") or 0)
        last_seen = float(info.get("last_seen") or 0)
        unread    = int(info.get("unread") or 0)

        dot_col  = T.STATUS_DOT.get("connected" if connected else status,
                                    T.STATUS_DOT["offline"])
        av_col   = T.avatar_color(username)
        trust_fg = T.TRUST_FG.get(trust, T.TEXT_MUTED)
        trust_bg = T.TRUST_BG.get(trust, T.BG_CARD)

        # ── Outer horizontal row ──────────────────────────────────────
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="both", expand=True, padx=10, pady=8)

        # ── Avatar (left, fixed 50×50) ────────────────────────────────
        av_wrap = ctk.CTkFrame(row, fg_color="transparent", width=50, height=50)
        av_wrap.pack(side="left", padx=(0, 8), pady=7)
        av_wrap.pack_propagate(False)

        av = ctk.CTkFrame(av_wrap, width=44, height=44,
                          corner_radius=22, fg_color=av_col)
        av.pack(expand=True)
        av.pack_propagate(False)
        ctk.CTkLabel(av, text=username[0].upper(),
                     font=("Segoe UI", 15, "bold"), text_color="#fff",
                     ).place(relx=0.5, rely=0.5, anchor="center")

        # Status dot — placed at bottom-right of av_wrap
        dot = ctk.CTkFrame(av_wrap, width=13, height=13, corner_radius=7,
                           fg_color=dot_col, border_width=2,
                           border_color=T.BG_CARD)
        dot.place(relx=1.0, rely=1.0, x=-1, y=-1, anchor="se")

        # ── Text (right, flex) ────────────────────────────────────────
        txt = ctk.CTkFrame(row, fg_color="transparent")
        txt.pack(side="left", fill="both", expand=True)

        # Row 1: name (left) + action (right)
        name_row = ctk.CTkFrame(txt, fg_color="transparent")
        name_row.pack(fill="x")

        disp = username[:16] + ("…" if len(username) > 16 else "")
        ctk.CTkLabel(name_row, text=disp, anchor="w",
                     font=("Segoe UI", 12, "bold"), text_color=T.TEXT_PRI,
                     ).pack(side="left")

        if unread > 0:
            pill = ctk.CTkFrame(name_row, width=22, height=22,
                                corner_radius=11, fg_color=T.ACCENT)
            pill.pack(side="right")
            pill.pack_propagate(False)
            ctk.CTkLabel(pill,
                         text=str(unread) if unread < 10 else "9+",
                         font=("Segoe UI", 9, "bold"), text_color="#fff",
                         ).place(relx=0.5, rely=0.5, anchor="center")
        elif connected:
            ctk.CTkLabel(name_row, text="● live",
                         font=("Segoe UI", 9, "bold"), text_color=T.ACCENT,
                         ).pack(side="right")
        else:
            ctk.CTkButton(
                name_row, text="Connect", width=58, height=22,
                corner_radius=11,
                fg_color=T.ACCENT_DIM, hover_color=T.ACCENT,
                text_color=T.TEXT_LINK, font=("Segoe UI", 9),
                command=self._do_connect,
            ).pack(side="right")

        # Row 2: last message preview (or IP:port / last-seen as fallback)
        last_msg  = info.get("last_message", "")
        last_time = info.get("last_message_time", "")
        if last_msg:
            sub      = last_msg
            sub_font = (T.FONT, 10)
        elif ip and port_num:
            sub      = f"{ip}:{port_num}"
            sub_font = ("Consolas", 9)
        elif last_seen:
            sub      = _time_ago(last_seen)
            sub_font = (T.FONT, 10)
        else:
            sub      = "Scanning…"
            sub_font = (T.FONT, 10)

        sub_row = ctk.CTkFrame(txt, fg_color="transparent")
        sub_row.pack(fill="x")
        ctk.CTkLabel(sub_row, text=sub, anchor="w",
                     font=sub_font, text_color=T.TEXT_MUTED,
                     ).pack(side="left", fill="x", expand=True)
        if last_time:
            ctk.CTkLabel(sub_row, text=last_time,
                         font=(T.FONT, 9), text_color=T.TEXT_MUTED,
                         ).pack(side="right")

        # Row 3: trust badge
        badge = ctk.CTkFrame(txt, corner_radius=5, fg_color=trust_bg)
        badge.pack(anchor="w", pady=(3, 0))
        ctk.CTkLabel(badge, text=_TRUST_LABELS.get(trust, trust),
                     font=("Segoe UI", 9, "bold"), text_color=trust_fg,
                     ).pack(padx=6, pady=2)

        # Propagate click & hover to all children
        for w in self.winfo_children():
            self._bind_click(w)
        self.bind("<Button-1>", self._do_select)

    def _bind_click(self, widget) -> None:
        widget.bind("<Button-1>", self._do_select)
        widget.bind("<Enter>", lambda _e: self._hover(True))
        widget.bind("<Leave>", lambda _e: self._hover(False))
        for child in widget.winfo_children():
            self._bind_click(child)

    def _hover(self, on: bool) -> None:
        if not self._selected:
            self.configure(fg_color=T.BG_CARD_HOV if on else T.BG_CARD)

    def _do_select(self, _e=None) -> None:
        if self._on_select:
            self._on_select(self.peer_id, self.peer_info)

    def _do_connect(self) -> None:
        if self._on_connect:
            self._on_connect(self.peer_id, self.peer_info)

    def set_selected(self, sel: bool) -> None:
        """Highlight or un-highlight."""
        self._selected = sel
        self.configure(fg_color=T.BG_CARD_SEL if sel else T.BG_CARD)

    def update_info(self, peer_info: dict) -> None:
        """Refresh contents in-place."""
        self.peer_info = peer_info
        self._build()
        if self._selected:
            self.configure(fg_color=T.BG_CARD_SEL)


class Sidebar(ctk.CTkFrame):
    """Left sidebar: PEERS header, search, scrollable card list, manual-connect."""

    def __init__(self, master,
                 on_peer_select: Optional[Callable] = None,
                 on_peer_connect: Optional[Callable] = None,
                 on_manual_connect: Optional[Callable] = None, **kw) -> None:
        super().__init__(master, fg_color=T.BG_SIDEBAR, width=300, **kw)
        self.grid_propagate(False)
        self._on_peer_select    = on_peer_select
        self._on_peer_connect   = on_peer_connect
        self._on_manual_connect = on_manual_connect
        self.selected_peer_id: Optional[str]  = None
        self.peer_cards: dict[str, PeerCard]  = {}
        self._all_peers: dict[str, dict]      = {}
        self._manual_visible                  = False
        self._build()

    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        # ── Brand header ──────────────────────────────────────────────
        brand = ctk.CTkFrame(self, fg_color=T.BG_HEADER, corner_radius=0, height=62)
        brand.pack(fill="x")
        brand.pack_propagate(False)

        logo = ctk.CTkFrame(brand, width=36, height=36, corner_radius=10,
                            fg_color=T.ACCENT)
        logo.pack(side="left", padx=(14, 10), pady=13)
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="P²", font=(T.FONT, 12, "bold"),
                     text_color="#fff").place(relx=0.5, rely=0.5, anchor="center")

        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", fill="y", pady=12)
        ctk.CTkLabel(names, text="P2PChat", font=(T.FONT, 13, "bold"),
                     text_color=T.TEXT_PRI, anchor="w").pack(anchor="w")
        ctk.CTkLabel(names, text="Encrypted P2P Messaging",
                     font=(T.FONT, 8), text_color=T.TEXT_MUTED,
                     anchor="w").pack(anchor="w")

        ctk.CTkFrame(self, height=1, fg_color=T.BORDER).pack(fill="x")

        # ── Peers header ──────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(hdr, text="PEERS",
                     font=("Segoe UI", 10, "bold"),
                     text_color=T.TEXT_MUTED).pack(side="left")
        self._count_lbl = ctk.CTkLabel(hdr, text="",
                                       font=("Segoe UI", 9),
                                       text_color=T.TEXT_MUTED)
        self._count_lbl.pack(side="right")

        # ── Search ───────────────────────────────────────────────────
        sf = ctk.CTkFrame(self, fg_color=T.BG_HEADER, corner_radius=10)
        sf.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(sf, text="🔍", font=("Segoe UI", 11),
                     text_color=T.TEXT_MUTED).pack(side="left", padx=(10, 0))
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(
            sf, textvariable=self._search_var,
            placeholder_text="Search peers…",
            placeholder_text_color=T.TEXT_MUTED,
            fg_color="transparent", border_width=0,
            text_color=T.TEXT_PRI, font=("Segoe UI", 12), height=34,
        ).pack(side="left", fill="x", expand=True, padx=6)

        # ── Peer list ─────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.BORDER,
            scrollbar_button_hover_color=T.BORDER_LIGHT)
        self._scroll.pack(fill="both", expand=True, padx=6, pady=2)

        # ── Separator + bottom ────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color=T.BORDER).pack(fill="x", padx=10)

        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=12, pady=(6, 10))

        ctk.CTkButton(
            bot, text="＋   Add / Discover Peer",
            height=36, corner_radius=10,
            fg_color=T.BG_HEADER, hover_color=T.BG_FIELD,
            text_color=T.ACCENT, font=("Segoe UI", 12),
            command=self._toggle_manual,
        ).pack(fill="x")

        # Manual connect (hidden)
        self._manual_frame = ctk.CTkFrame(bot, fg_color=T.BG_HEADER, corner_radius=10)
        r = ctk.CTkFrame(self._manual_frame, fg_color="transparent")
        r.pack(fill="x", padx=8, pady=(8, 4))
        r.grid_columnconfigure(0, weight=2)
        r.grid_columnconfigure(1, weight=1)

        self._ip_entry = ctk.CTkEntry(
            r, placeholder_text="IP address", height=32,
            font=("Consolas", 11), fg_color=T.BG_FIELD,
            border_color=T.BORDER, text_color=T.TEXT_PRI)
        self._ip_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self._port_entry = ctk.CTkEntry(
            r, placeholder_text="Port", width=74, height=32,
            font=("Consolas", 11), fg_color=T.BG_FIELD,
            border_color=T.BORDER, text_color=T.TEXT_PRI)
        self._port_entry.grid(row=0, column=1, sticky="ew")

        self._ip_entry.bind("<Return>", lambda _: self._port_entry.focus_set())
        self._port_entry.bind("<Return>", lambda _: self._do_manual_connect())

        ctk.CTkButton(
            self._manual_frame, text="Connect",
            height=32, corner_radius=8,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOV,
            font=("Segoe UI", 12, "bold"),
            command=self._do_manual_connect,
        ).pack(fill="x", padx=8, pady=(0, 8))

    # ------------------------------------------------------------------ #
    # Public                                                               #
    # ------------------------------------------------------------------ #

    def update_peers(self, peers: dict) -> None:
        """Incremental peer list update — avoids full rebuild on heartbeat."""
        prev = set(self._all_peers)
        self._all_peers = peers
        curr = set(peers)

        for pid in prev - curr:
            if pid in self.peer_cards:
                self.peer_cards[pid].destroy()
                del self.peer_cards[pid]

        if prev != curr:
            self._apply_filter()
        else:
            for pid, info in peers.items():
                if pid in self.peer_cards:
                    self.peer_cards[pid].update_info(info)

        online = sum(1 for i in peers.values() if i.get("status") != "offline")
        self._count_lbl.configure(
            text=f"{online}/{len(peers)}" if peers else "")

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _apply_filter(self) -> None:
        q = self._search_var.get().lower().strip()
        filtered = {
            pid: info for pid, info in self._all_peers.items()
            if not q
            or q in (info.get("username") or "").lower()
            or q in (info.get("ip") or "")
        }

        for w in self._scroll.winfo_children():
            w.destroy()
        self.peer_cards.clear()

        if not filtered:
            ctk.CTkLabel(
                self._scroll,
                text="No peers discovered" if not q else "No match",
                text_color=T.TEXT_MUTED, font=("Segoe UI", 12),
            ).pack(pady=40)
            return

        def _key(item):
            s = item[1].get("status", "offline")
            c = item[1].get("connected", False)
            return 0 if (c or s == "connected") else (1 if s == "online" else 2)

        for pid, info in sorted(filtered.items(), key=_key):
            card = PeerCard(self._scroll, pid, info,
                            on_select=self._select_peer,
                            on_connect=self._connect_peer)
            card.pack(fill="x", padx=4, pady=3)
            card.set_selected(pid == self.selected_peer_id)
            self.peer_cards[pid] = card

    def _toggle_manual(self) -> None:
        self._manual_visible = not self._manual_visible
        if self._manual_visible:
            self._manual_frame.pack(fill="x", pady=(6, 0))
            self._ip_entry.focus_set()
        else:
            self._manual_frame.pack_forget()

    def _select_peer(self, peer_id: str, peer_info: dict) -> None:
        self.selected_peer_id = peer_id
        for pid, card in self.peer_cards.items():
            card.set_selected(pid == peer_id)
        if self._on_peer_select:
            self._on_peer_select(peer_id, peer_info)

    def _connect_peer(self, peer_id: str, peer_info: dict) -> None:
        if self._on_peer_connect:
            self._on_peer_connect(peer_id, peer_info)

    def _do_manual_connect(self) -> None:
        ip  = self._ip_entry.get().strip()
        p   = self._port_entry.get().strip()
        if self._on_manual_connect:
            self._on_manual_connect(ip, p)
        self._ip_entry.delete(0, "end")
        self._port_entry.delete(0, "end")
        if self._manual_visible:
            self._toggle_manual()
