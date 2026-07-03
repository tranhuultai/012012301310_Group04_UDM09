"""ChatBox: scrollable message area — canvas-rendered bubbles.

Rendering approach:
    Messages are drawn directly onto a tkinter.Canvas (rounded-rect + text
    items, see gui/chat_bubble.py) instead of being built as CTkFrame/
    CTkLabel widget trees. Every CTk widget independently redraws itself on
    <Configure> (confirmed by reading customtkinter's own installed source)
    — with one CTkFrame subtree per chat message, a resize drag or message
    flood triggered a redraw cascade across hundreds of live widgets,
    causing black flicker/tearing on Windows (Tk has no whole-window
    double buffer). Canvas items have no window handle and no <Configure>
    binding of their own, so that cascade cannot occur.

    File-transfer cards stay as CTkFrame/CTkButton, embedded into the same
    canvas via canvas.create_window() so everything still shares one
    scrollable area. They aren't converted to raw canvas drawing because
    they're rare (one per file transfer, not one per chat message) and
    aren't the high-volume case that caused the flicker — converting them
    too would mean reimplementing button click/hover as manual canvas tag
    bindings for no real benefit here.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import customtkinter as ctk

from gui import theme as T
from gui.chat_bubble import draw_bubble, draw_divider, draw_system

# Bubbles wrap at ~65% of the chat column's width — the proportion, not a
# fixed margin, is what makes them scale naturally with the window instead
# of always leaving/eating the same number of pixels (how Telegram/Discord
# size their message column). Still bounded on both ends: a floor so a
# narrow window doesn't crush bubbles unreadably thin, and a ceiling so an
# ultra-wide/maximized window doesn't stretch a one-line message absurdly wide.
_BUBBLE_MAX_PCT = 0.65
_BUBBLE_MIN_WRAP = 180
_BUBBLE_MAX_WRAP = 480

# Hard cap on rows kept in the chatbox. Without this, a spamming peer grows
# the canvas item count unboundedly. Date dividers are excluded from this
# cap — at most one is added per calendar day, and losing the day's only
# date header to eviction isn't worth the (negligible) memory it saves.
_MAX_ROWS = 150

_TOP_MARGIN = 8


@dataclass
class _MsgItem:
    """One rendered row: bubble, system pill, file card, or date divider."""
    kind: str                                    # "bubble" | "system" | "file" | "divider"
    tag: str
    is_me: bool = False
    sender: str = ""
    text: str = ""
    grouped: bool = False
    build_file_card: Optional[Callable] = None   # "file" kind only


class ChatBox(ctk.CTkFrame):
    """Scrollable area that holds message bubbles, date dividers, and notices."""

    def __init__(self, master, **kw) -> None:
        super().__init__(master, fg_color="transparent", **kw)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        # Reserve column 1's width (the scrollbar's natural width, 20px)
        # permanently, even while the scrollbar is hidden (grid_remove()
        # below). Without this, hiding it lets the canvas column reclaim
        # that space and grow — then showing it again later shrinks the
        # canvas back. Canvas items don't reposition themselves when the
        # canvas widget resizes, so a layout pass computed against one
        # width and then silently invalidated by a width change left
        # bubbles rendered at stale (wrong) x-coordinates until the next
        # unrelated relayout happened to fix it — reproduced as "message
        # bubbles show as thin slivers at the right edge; re-selecting the
        # peer a second time fixes it."
        self.grid_columnconfigure(1, minsize=20)

        self._canvas = tk.Canvas(self, bg=T.BG_MAIN, highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._vbar = ctk.CTkScrollbar(
            self, orientation="vertical", command=self._canvas.yview,
            button_color=T.BORDER, button_hover_color=T.BORDER_LIGHT)
        self._vbar.grid(row=0, column=1, sticky="ns")
        self._vbar.grid_remove()   # nothing to scroll yet — see _do_relayout
        self._canvas.configure(yscrollcommand=self._vbar.set)
        # CTkScrollableFrame handled mouse-wheel scrolling internally; a raw
        # Canvas needs it bound explicitly. Windows reports wheel delta in
        # multiples of 120.
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)

        self._last_date: str = ""

        # (is_me, sender) of the last text bubble added, or None. Anything
        # that isn't a plain text bubble (system notice, file card, date
        # divider) resets this to None, so grouping only ever spans an
        # unbroken run of the same sender's messages.
        self._last_msg_key: tuple[bool, str] | None = None

        # Every rendered row, in order. Replaces the old _bubble_labels +
        # _rows: relayout redraws from this list rather than reconfiguring
        # existing widgets, so there's no incremental-diff logic to get
        # wrong — simple, at the cost of redrawing everything on change
        # (cheap here: canvas items have no per-widget construction cost).
        self._items: list[_MsgItem] = []
        self._next_tag_id = 0

        # Embedded file-card CTkFrames, keyed by tag. Reused across
        # relayouts (only their canvas embedding is redone each time) so a
        # relayout doesn't rebuild the CTkButton/CTkLabel tree for every
        # file card on every new chat message — only genuinely-evicted or
        # cleared cards get destroyed (_track_item, clear()).
        self._file_frames: dict[str, ctk.CTkFrame] = {}

        self._current_wrap: int = 400
        self._canvas_width: int = 400

        # Debounces relayout so a burst of appends (message-spam batching,
        # up to 25 per Tk tick — see app.py's _drain_messages) triggers one
        # redraw pass, not one per message.
        self._relayout_pending: bool = False

        # Debounces scroll-to-bottom the same way.
        self._scroll_pending: bool = False

        # Debounces _on_resize: dragging a window edge fires <Configure>
        # continuously (every pixel), and relayouting on each one is what
        # caused visible tearing/flicker in the previous CTkFrame-based
        # renderer. Only the size after the drag settles is applied.
        self._resize_after_id: str | None = None

        self._canvas.bind("<Configure>", self._on_resize)

    # ── Resize handler ────────────────────────────────────────────────

    def _on_resize(self, event) -> None:
        """Schedule a debounced relayout on resize."""
        self._canvas_width = event.width
        raw_wrap = int(event.width * _BUBBLE_MAX_PCT)
        new_wrap = min(_BUBBLE_MAX_WRAP, max(_BUBBLE_MIN_WRAP, raw_wrap))
        if new_wrap == self._current_wrap:
            return
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:   # pylint: disable=broad-exception-caught
                pass
        self._resize_after_id = self.after(80, lambda w=new_wrap: self._apply_resize(w))

    def _apply_resize(self, new_wrap: int) -> None:
        """Reflow every row at the new wrap width once the resize has settled."""
        self._resize_after_id = None
        self._current_wrap = new_wrap
        self._request_relayout()

    # ── Public API ────────────────────────────────────────────────────

    def add_sent(self, sender: str, _recipient: str, message: str) -> None:
        """Append an outbound bubble (_recipient unused, kept for API compatibility)."""
        self._maybe_date_divider()
        key = (True, sender)
        item = _MsgItem(kind="bubble", tag=self._new_tag(), is_me=True,
                        sender=sender, text=message,
                        grouped=key == self._last_msg_key)
        self._track_item(item)
        self._last_msg_key = key
        self._request_relayout()

    def add_received(self, sender: str, message: str) -> None:
        """Append an inbound bubble."""
        self._maybe_date_divider()
        key = (False, sender)
        item = _MsgItem(kind="bubble", tag=self._new_tag(), is_me=False,
                        sender=sender, text=message,
                        grouped=key == self._last_msg_key)
        self._track_item(item)
        self._last_msg_key = key
        self._request_relayout()

    def add_system(self, text: str) -> None:
        """Add a centred system notice pill (e.g. "Connected")."""
        self._last_msg_key = None   # breaks bubble grouping across the notice
        item = _MsgItem(kind="system", tag=self._new_tag(), text=text)
        self._track_item(item)
        self._request_relayout()

    def add_file_sent(self, sender: str, filename: str, size_str: str) -> None:
        """Append a right-aligned sent-file card (no download button)."""
        self._maybe_date_divider()
        self._last_msg_key = None   # breaks bubble grouping across the file card

        def _build(parent) -> ctk.CTkFrame:
            card = ctk.CTkFrame(parent, fg_color=T.BG_BUBBLE_ME, corner_radius=14)
            ctk.CTkLabel(card, text=sender,
                        font=(T.FONT, 10, "bold"), text_color=T.TEXT_ON_ACCENT,
                        anchor="w").pack(fill="x", padx=12, pady=(8, 2))
            info_row = ctk.CTkFrame(card, fg_color="transparent")
            info_row.pack(fill="x", padx=12, pady=(0, 10))
            ctk.CTkLabel(info_row, text="📄",
                        font=("Segoe UI Emoji", 22)).pack(side="left", padx=(0, 8))
            detail = ctk.CTkFrame(info_row, fg_color="transparent")
            detail.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(detail, text=filename,
                        font=(T.FONT, 12, "bold"), text_color="#e2e8f0",
                        anchor="w").pack(anchor="w")
            ctk.CTkLabel(detail, text=f"{size_str}  ·  Sent",
                        font=(T.FONT, 10), text_color=T.TEXT_ON_ACCENT,
                        anchor="w").pack(anchor="w")
            return card

        item = _MsgItem(kind="file", tag=self._new_tag(), is_me=True,
                        build_file_card=_build)
        self._track_item(item)
        self._request_relayout()

    def add_file_message(self, sender: str, filename: str,
                         size_str: str, on_download=None) -> None:
        """Append a file-offer card with a Download button (calls on_download)."""
        self._maybe_date_divider()
        self._last_msg_key = None   # breaks bubble grouping across the file card

        def _build(parent) -> ctk.CTkFrame:
            card = ctk.CTkFrame(parent, fg_color=T.BG_CARD, corner_radius=14)
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
                fg_color=T.ACCENT_DIM, hover_color=T.ACCENT_GLOW,
                text_color=T.TEXT_LINK, font=(T.FONT, 11, "bold"),
                command=on_download,
            ).pack(fill="x", padx=12, pady=(4, 10))
            return card

        item = _MsgItem(kind="file", tag=self._new_tag(), is_me=False,
                        build_file_card=_build)
        self._track_item(item)
        self._request_relayout()

    def clear(self) -> None:
        """Remove all messages and reset divider state and item tracking."""
        self._canvas.delete("all")
        for frame in self._file_frames.values():
            frame.destroy()
        self._file_frames.clear()
        self._last_date = ""
        self._items = []
        self._last_msg_key = None
        self._canvas.configure(scrollregion=(0, 0, self._canvas_width, 0))
        self._vbar.grid_remove()   # empty conversation — nothing to scroll

    # ── Internal helpers ──────────────────────────────────────────────

    def _new_tag(self) -> str:
        self._next_tag_id += 1
        return f"row{self._next_tag_id}"

    def _track_item(self, item: _MsgItem) -> None:
        """Append *item* and evict the oldest evictable row past _MAX_ROWS.

        Only one item is ever appended per call, so at most one can be
        over the cap — an "if", not a "while", is enough here.
        """
        self._items.append(item)
        evictable = sum(1 for i in self._items if i.kind != "divider")
        if evictable > _MAX_ROWS:
            for i, old in enumerate(self._items):
                if old.kind != "divider":
                    self._items.pop(i)
                    self._canvas.delete(old.tag)
                    frame = self._file_frames.pop(old.tag, None)
                    if frame is not None:
                        frame.destroy()
                    break

    def _maybe_date_divider(self) -> None:
        today = datetime.now().strftime("%d %B %Y")
        if today != self._last_date:
            self._last_date = today
            self._last_msg_key = None   # new day breaks bubble grouping
            # Not passed through _track_item: dividers are excluded from
            # the row cap (see _MAX_ROWS comment above).
            self._items.append(_MsgItem(kind="divider", tag=self._new_tag(), text=today))

    # ── Relayout (debounced) ──────────────────────────────────────────

    def _request_relayout(self) -> None:
        """Coalesce a burst of appends/resizes within one Tk tick into one
        relayout pass — a message-spam batch (up to 25 per tick, handled
        upstream in app.py) would otherwise redraw the whole canvas 25x.
        """
        if self._relayout_pending:
            return
        self._relayout_pending = True
        self.after(0, self._do_relayout)

    def _do_relayout(self) -> None:
        self._relayout_pending = False
        # Unmaps every canvas item, including embedded file-card windows —
        # the CTkFrames themselves survive (see _draw_file_card) and are
        # only actually destroyed on eviction/clear().
        self._canvas.delete("all")

        y = _TOP_MARGIN
        for item in self._items:
            if item.kind == "bubble":
                y += draw_bubble(
                    self._canvas, y, self._canvas_width, self._current_wrap,
                    item.text, item.sender, item.is_me, item.grouped, item.tag)
            elif item.kind == "system":
                y += draw_system(self._canvas, y, self._canvas_width, item.text, item.tag)
            elif item.kind == "divider":
                y += draw_divider(self._canvas, y, self._canvas_width, item.text, item.tag)
            elif item.kind == "file":
                y += self._draw_file_card(item, y)

        content_h = y + _TOP_MARGIN
        self._canvas.configure(scrollregion=(0, 0, self._canvas_width, content_h))
        # CTkScrollbar always draws a full-length pill, even when there's
        # nothing to scroll (verified: it has no auto-hide of its own) —
        # left visible, a short chat looks like it has hidden content above/
        # below it, which is exactly what a user would (reasonably) find
        # confusing. Hide it whenever all content already fits on screen.
        if content_h > self._canvas.winfo_height():
            self._vbar.grid()
        else:
            self._vbar.grid_remove()
        self._request_scroll_bottom()

    def _draw_file_card(self, item: _MsgItem, top_y: int) -> int:
        """Embed a CTkFrame file card via create_window at the given y."""
        top_gap = 10
        y = top_y + top_gap
        frame = self._file_frames.get(item.tag)
        if frame is None:
            assert item.build_file_card is not None, "file item must carry a builder"
            frame = item.build_file_card(self._canvas)
            self._file_frames[item.tag] = frame
        frame.update_idletasks()
        card_w = frame.winfo_reqwidth()
        card_h = frame.winfo_reqheight()
        x = (self._canvas_width - 16 - card_w) if item.is_me else 16
        self._canvas.create_window(x, y, anchor="nw", window=frame,
                                    width=card_w, height=card_h, tags=(item.tag,))
        return card_h + top_gap

    # ── Scrolling ─────────────────────────────────────────────────────

    def _request_scroll_bottom(self) -> None:
        """Scroll to bottom after layout settles (debounced — a burst of
        relayouts within the settle delay only schedules one scroll)."""
        if self._scroll_pending:
            return
        self._scroll_pending = True
        self.after(100, self._do_scroll_bottom)

    def _do_scroll_bottom(self) -> None:
        self._scroll_pending = False
        try:
            self._canvas.yview_moveto(1.0)
        except Exception:   # pylint: disable=broad-exception-caught
            pass

    def _on_mousewheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")
