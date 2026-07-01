"""File transfer progress panel — collapsible, shows only when transfers active."""
from __future__ import annotations

import logging
from tkinter import filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

from gui import theme as T

logger = logging.getLogger(__name__)


class TransferPanel(ctk.CTkFrame):
    """Compact panel listing active file transfers with progress bars.

    Hidden when no transfer is active — expands automatically when one starts
    and collapses again 3 s after the last transfer completes.
    """

    def __init__(self, master, controller=None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.controller = controller
        self.on_file_sent: Optional[Callable[[str, str], None]] = None
        self._transfers: dict[str, dict] = {}
        self._visible = False
        self._build_ui()

    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # Collapsed header (always visible if transfers exist)
        self._header = ctk.CTkFrame(
            self, fg_color=T.BG_FIELD, corner_radius=10)

        ctk.CTkLabel(
            self._header,
            text="  📎  Active Transfers",
            font=(T.FONT, 11, "bold"), text_color=T.TEXT_SEC,
            anchor="w",
        ).pack(side="left", padx=(10, 0), pady=8)

        self._count_lbl = ctk.CTkLabel(
            self._header, text="",
            font=(T.FONT, 10), text_color=T.ACCENT,
        )
        self._count_lbl.pack(side="left", padx=4)

        # Scrollable card area (shown only when transfers exist)
        self._container = ctk.CTkScrollableFrame(
            self, fg_color=T.BG_CARD, corner_radius=10, height=140)

        # Send File button (always visible at bottom of right panel)
        self._send_btn = ctk.CTkButton(
            self, text="📎  Send File",
            height=38, corner_radius=10,
            fg_color=T.ACCENT_DIM, hover_color=T.ACCENT_GLOW,
            text_color=T.TEXT_LINK, font=(T.FONT, 12, "bold"),
            command=self._pick_and_send,
        )
        self._send_btn.pack(fill="x", padx=4, pady=(0, 6))

    # ------------------------------------------------------------------ #
    # Show/hide logic                                                      #
    # ------------------------------------------------------------------ #

    def _show_panel(self) -> None:
        if not self._visible:
            self._visible = True
            self._header.pack(fill="x", padx=4, pady=(6, 2), before=self._send_btn)
            self._container.pack(fill="x", padx=4, pady=(0, 4), before=self._send_btn)

    def _hide_panel(self) -> None:
        if self._visible:
            self._visible = False
            self._header.pack_forget()
            self._container.pack_forget()

    def _refresh_count(self) -> None:
        n = len(self._transfers)
        if n > 0:
            self._count_lbl.configure(text=f"({n})")
            self._show_panel()
        else:
            self._count_lbl.configure(text="")
            self.after(3000, self._hide_panel)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def add_transfer(self, transfer_id: str, filename: str,
                     peer_name: str, direction: str = "out") -> None:
        """Add a new transfer card and expand the panel."""
        if transfer_id in self._transfers:
            return

        icon  = "📤" if direction == "out" else "📥"
        label = f"{icon}  {filename}"

        card = ctk.CTkFrame(self._container, fg_color=T.BG_FIELD,
                            corner_radius=8)
        card.pack(fill="x", padx=4, pady=(4, 0))

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(top, text=label, anchor="w",
                     font=(T.FONT, 11, "bold"), text_color=T.TEXT_PRI,
                     ).pack(side="left")
        ctk.CTkLabel(top, text=peer_name, anchor="e",
                     font=(T.FONT, 10), text_color=T.TEXT_MUTED,
                     ).pack(side="right")

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 8))

        progress_bar = ctk.CTkProgressBar(row, height=6, corner_radius=3,
                                          fg_color=T.BG_CARD, progress_color=T.ACCENT)
        progress_bar.pack(side="left", fill="x", expand=True)
        progress_bar.set(0)

        pct = ctk.CTkLabel(row, text="0%", width=32,
                            font=(T.FONT, 10), text_color=T.TEXT_SEC)
        pct.pack(side="left", padx=(6, 4))

        ctk.CTkButton(
            row, text="✕", width=24, height=24, corner_radius=12,
            fg_color=T.DANGER_DIM, hover_color=T.BTN_DANGER_HOV,
            text_color=T.DANGER, font=(T.FONT, 11),
            command=lambda tid=transfer_id: self._cancel(tid),
        ).pack(side="right")

        self._transfers[transfer_id] = {
            "frame": card, "progress": progress_bar, "percent": pct}
        self._refresh_count()

    def update_transfer(self, transfer_id: str, progress: float) -> None:
        """Update progress bar."""
        entry = self._transfers.get(transfer_id)
        if not entry:
            return
        v = max(0.0, min(1.0, progress))
        entry["progress"].set(v)
        entry["percent"].configure(text=f"{int(v * 100)}%")

    def remove_transfer(self, transfer_id: str) -> None:
        """Remove and destroy a transfer card."""
        entry = self._transfers.pop(transfer_id, None)
        if entry:
            entry["frame"].destroy()
        self._refresh_count()

    # ------------------------------------------------------------------ #

    def _pick_and_send(self) -> None:
        """Open file picker, validate, and send.

        Shows allowed types and 10 MB limit in the dialog title so the user
        knows the constraints before choosing a file.
        """
        if self.controller is None:
            messagebox.showwarning("Not ready", "Node not started yet.")
            return

        peer_id = getattr(self.controller, "_current_peer_id", None)
        if not peer_id:
            messagebox.showwarning(
                "No peer selected",
                "Select a peer from the sidebar first, then try again.")
            return

        peer_info = self.controller.get_peer_info(peer_id) or {}
        if not peer_info.get("connected"):
            uname = peer_info.get("username", "the peer")
            messagebox.showwarning(
                "Not connected",
                f"Connect to {uname} first before sending files.")
            return

        filepath = filedialog.askopenfilename(
            title="Send File  (max 10 MB · PDF, DOCX, TXT, PNG, JPG, ZIP)",
            filetypes=[
                ("Supported files", "*.pdf *.docx *.txt *.png *.jpg *.jpeg *.zip"),
                ("All files",       "*.*"),
            ],
        )
        if not filepath:
            return  # user cancelled

        ok, result = self.controller.send_file(filepath, peer_id)
        if ok:
            logger.info("[TRANSFER] File offer sent: %s", result[:8])
            if self.on_file_sent is not None:
                self.on_file_sent(filepath, peer_id)
        else:
            logger.warning("[TRANSFER] send_file rejected: %s", result)
            messagebox.showerror("Cannot send file", result)

    def _cancel(self, transfer_id: str) -> None:
        if self.controller and hasattr(self.controller, "cancel_transfer"):
            self.controller.cancel_transfer(transfer_id)
        self.remove_transfer(transfer_id)
