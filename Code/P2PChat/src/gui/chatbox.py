"""ChatBox: scrollable message area with responsive bubbles.

Responsive design:
    ChatBox binds <Configure> on its scrollable frame and updates
    wraplength on all existing bubble labels when the chat column
    resizes.  This fixes the "broken layout on window resize" bug that
    occurs when wraplength is a hard-coded constant.

    _bubble_labels holds weak references to message labels.  When
    clear() is called, the list is reset so garbage-collected widgets
    don't accumulate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import customtkinter as ctk

from gui import theme as T
from gui.chat_bubble import add_chat_bubble

# Margins on each side of a bubble (bubble offset + internal padding).
# wraplength = chat_width - BUBBLE_MARGIN_PX
_BUBBLE_MARGIN_PX = 130


class ChatBox(ctk.CTkFrame):
    """Scrollable area that holds message bubbles, date dividers, and notices."""

    def __init__(self, master, **kw) -> None:
        super().__init__(master, fg_color="transparent", **kw)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color=T.BG_MAIN,
            scrollbar_button_color=T.BORDER,
            scrollbar_button_hover_color=T.BORDER_LIGHT)
        self._scroll.grid(row=0, column=0, sticky="nsew")
        self._last_date: str = ""

        # Tracks all message labels for wraplength updates on resize.
        # Cleared by clear() to avoid holding destroyed widgets.
        self._bubble_labels: list[ctk.CTkLabel] = []
        self._current_wrap: int = 400

        # Bind resize events on the scrollable frame to reflow bubbles.
        self._scroll.bind("<Configure>", self._on_resize)

    # ── Resize handler ────────────────────────────────────────────────

    def _on_resize(self, event) -> None:
        """Reflow all bubble message labels when the chat column resizes."""
        new_wrap = max(180, event.width - _BUBBLE_MARGIN_PX)
        if new_wrap == self._current_wrap:
            return
        self._current_wrap = new_wrap
        living = []
        for lbl in self._bubble_labels:
            try:
                lbl.configure(wraplength=new_wrap)
                living.append(lbl)
            except Exception:   # pylint: disable=broad-exception-caught
                pass  # widget was destroyed (e.g. after clear())
        self._bubble_labels = living

    # ── Public API ────────────────────────────────────────────────────

    def add_sent(self, sender: str, _recipient: str, message: str) -> None:
        """Append an outbound bubble.

        Args:
            sender: Local display name.
            _recipient: Unused — kept for API compatibility.
            message: Plaintext body.
        """
        self._maybe_date_divider()
        lbl = add_chat_bubble(
            self._scroll, message, sender=sender, is_me=True,
            wraplength=self._current_wrap)
        if lbl is not None:
            self._bubble_labels.append(lbl)
        self._scroll_bottom()

    def add_received(self, sender: str, message: str) -> None:
        """Append an inbound bubble.

        Args:
            sender: Remote peer display name.
            message: Plaintext body.
        """
        self._maybe_date_divider()
        lbl = add_chat_bubble(
            self._scroll, message, sender=sender, is_me=False,
            wraplength=self._current_wrap)
        if lbl is not None:
            self._bubble_labels.append(lbl)
        self._scroll_bottom()

    def add_system(self, text: str) -> None:
        """Add a centred system notice pill (e.g. "Connected")."""
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", pady=6)
        pill = ctk.CTkFrame(row, fg_color=T.BG_FIELD, corner_radius=10)
        pill.pack()
        ctk.CTkLabel(pill, text=text,
                     text_color=T.TEXT_MUTED, font=(T.FONT, 9),
                     ).pack(padx=12, pady=4)
        self._scroll_bottom()

    def add_file_message(self, sender: str, filename: str,
                         size_str: str, on_download=None) -> None:
        """Append a file-offer card with Download button.

        Args:
            sender: Display name of the file sender.
            filename: File name to display.
            size_str: Pre-formatted size string (e.g. "2.3 MB").
            on_download: Called when the user clicks Download.
        """
        self._maybe_date_divider()

        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", pady=(2, 6), padx=16)

        card = ctk.CTkFrame(row, fg_color=T.BG_CARD, corner_radius=14)
        card.pack(anchor="w", padx=(0, 72))

        ctk.CTkLabel(card, text=sender,
                     font=(T.FONT, 10, "bold"), text_color=T.ACCENT,
                     anchor="w").pack(fill="x", padx=12, pady=(8, 2))

        info_row = ctk.CTkFrame(card, fg_color="transparent")
        info_row.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(info_row, text="📄",
                     font=("Segoe UI Emoji", 22)).pack(side="left", padx=(0, 8))

        detail = ctk.CTkFrame(info_row, fg_color="transparent")
        detail.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(detail, text=filename,
                     font=(T.FONT, 12, "bold"), text_color=T.TEXT_PRI,
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(detail, text=size_str,
                     font=(T.FONT, 10), text_color=T.TEXT_MUTED,
                     anchor="w").pack(anchor="w")

        ctk.CTkButton(
            card, text="⬇  Download", height=32, corner_radius=8,
            fg_color=T.ACCENT_DIM, hover_color=T.ACCENT,
            text_color=T.TEXT_LINK, font=(T.FONT, 11, "bold"),
            command=on_download,
        ).pack(fill="x", padx=12, pady=(4, 10))

        self._scroll_bottom()

    def clear(self) -> None:
        """Remove all messages and reset divider state and label tracking."""
        for w in self._scroll.winfo_children():
            w.destroy()
        self._last_date = ""
        self._bubble_labels = []   # release references to destroyed widgets

    # ── Internal helpers ──────────────────────────────────────────────

    def _maybe_date_divider(self) -> None:
        today = datetime.now().strftime("%d %B %Y")
        if today != self._last_date:
            self._last_date = today
            self._date_divider(today)

    def _date_divider(self, label: str) -> None:
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", pady=10)
        ctk.CTkFrame(row, height=1, fg_color=T.BORDER).pack(
            side="left", fill="x", expand=True, padx=(16, 6))
        ctk.CTkLabel(row, text=label, fg_color=T.BG_FIELD,
                     corner_radius=8, text_color=T.TEXT_MUTED,
                     font=(T.FONT, 9)).pack(side="left", padx=4, pady=2)
        ctk.CTkFrame(row, height=1, fg_color=T.BORDER).pack(
            side="left", fill="x", expand=True, padx=(6, 16))

    def _scroll_bottom(self) -> None:
        """Scroll to bottom after layout settles."""
        def _do() -> None:
            try:
                # pylint: disable=protected-access
                canvas = self._scroll._parent_canvas
                canvas.update_idletasks()
                # Force scrollregion to include newly added content before scrolling.
                canvas.configure(scrollregion=canvas.bbox("all"))
                canvas.yview_moveto(1.0)
            except Exception:   # pylint: disable=broad-exception-caught
                pass
        self.after(100, _do)
