"""Trust verification dialog — shown on first contact and fingerprint mismatch."""
from __future__ import annotations

import tkinter as tk
import customtkinter as ctk

from gui import theme as T
from gui.win_compat import enable_layered_window


class TrustDialog(ctk.CTkToplevel):
    """Modal dialog for TOFU trust decisions.

    Modes
    -----
    new_peer
        Shown on first contact.  Offers Trust, Block, Skip.
    warning
        Shown when a known peer's fingerprint changed (possible MITM).
        Offers Update & Trust, Block.

    Callback results: "trust" | "block" | "skip" | "update"

    Design note: grab_set() is called *after* all widgets are built.
    Calling it earlier (in _setup_window) causes the window to capture
    events before it is visible, making buttons unresponsive.
    """

    def __init__(
        self,
        master,
        mode: str,
        peer_info: dict,
        callback=None,
    ) -> None:
        """Show the dialog.

        Args:
            master: Parent Tk widget (root window or top-level).
            mode: "new_peer" or "warning".
            peer_info: Dict with peer identity fields.  For "warning" mode
                must also contain known_fingerprint and
                current_fingerprint.
            callback: Called with the result string when the user acts.
        """
        super().__init__(master)
        self.mode      = mode
        self.peer_info = peer_info
        self.callback  = callback

        self._configure_window()

        if mode == "new_peer":
            self._build_new_peer_ui()
        elif mode == "warning":
            self._build_warning_ui()
        else:
            raise ValueError(f"Unknown mode: {mode!r}")

        # grab_set AFTER widgets exist — prevents buttons from being unresponsive.
        self.after(50, self._activate)

    # ------------------------------------------------------------------ #
    # Window configuration                                                 #
    # ------------------------------------------------------------------ #

    def _configure_window(self) -> None:
        """Set window geometry and keep it centred above the parent."""
        self.title("Peer Trust Verification")
        # Deferred slightly: calling resizable() in the same tick as window
        # creation is a known source of an initial white/black flash on
        # Windows (the OS repaints once for the create, once for the
        # resizable-flag change) — letting the window finish its first
        # paint first avoids the double-repaint.
        self.after(10, lambda: self.resizable(False, False))
        enable_layered_window(self)
        self.configure(fg_color=T.BG_MAIN)
        self.grid_columnconfigure(0, weight=1)

        # Size: warning mode needs more height for two fingerprint lines.
        h = 420 if self.mode == "warning" else 360
        self.geometry(f"560x{h}")

        # Stay on top of the parent without stealing focus system-wide.
        # isinstance guard required by Pylance (master typed as Misc).
        if isinstance(self.master, tk.Wm):
            self.transient(self.master)

    def _activate(self) -> None:
        """Grab focus after the window is fully rendered."""
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass   # window was destroyed before the after() fired

    # ------------------------------------------------------------------ #
    # new_peer mode                                                        #
    # ------------------------------------------------------------------ #

    def _build_new_peer_ui(self) -> None:
        """Build UI for first-contact trust decision."""
        ctk.CTkLabel(
            self, text="🔑  New Peer",
            font=("Segoe UI", 20, "bold"), text_color=T.TEXT_PRI,
        ).pack(pady=(20, 10))

        card = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=12)
        card.pack(fill="x", padx=24, pady=(0, 4))

        for label, key in (
            ("Username",    "username"),
            ("Peer ID",     "peer_id"),
            ("Fingerprint", "fingerprint"),
        ):
            self._info_row(card, label, self.peer_info.get(key, "—"))

        ctk.CTkLabel(
            self,
            text="Verify the fingerprint with the peer before trusting.",
            text_color=T.TEXT_MUTED, font=("Segoe UI", 11),
            justify="center",
        ).pack(pady=14)

        self._button_row(
            ("✓  Trust & Connect", "trust",  T.SUCCESS,    T.BTN_SUCCESS_BG),
            ("⊘  Block",           "block",  T.DANGER,     T.DANGER_DIM),
            ("—  Skip",            "skip",   T.TEXT_MUTED, T.BG_FIELD),
        )

    # ------------------------------------------------------------------ #
    # warning mode                                                         #
    # ------------------------------------------------------------------ #

    def _build_warning_ui(self) -> None:
        """Build UI for fingerprint-mismatch security warning."""
        ctk.CTkLabel(
            self, text="⚠️  Fingerprint Changed",
            font=("Segoe UI", 18, "bold"), text_color=T.WARNING,
        ).pack(pady=(20, 6))

        ctk.CTkLabel(
            self,
            text=(
                "This peer's cryptographic fingerprint has changed.\n"
                "This may indicate a man-in-the-middle attack.\n"
                "Verify out-of-band before accepting."
            ),
            text_color=T.TEXT_SEC, font=("Segoe UI", 11),
            justify="center",
        ).pack(pady=(0, 10))

        card = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=12)
        card.pack(fill="x", padx=24, pady=(0, 4))

        self._info_row(card, "Peer",    self.peer_info.get("username", "—"))
        self._info_row(card, "Known",   self.peer_info.get("known_fingerprint", "—"))
        self._info_row(card, "Current", self.peer_info.get("current_fingerprint", "—"))

        self._button_row(
            ("↺  Update & Trust", "update", T.WARNING, T.BTN_WARN_BG),
            ("⊘  Block Peer",     "block",  T.DANGER,  T.DANGER_DIM),
        )

    # ------------------------------------------------------------------ #
    # Shared helpers                                                       #
    # ------------------------------------------------------------------ #

    def _info_row(self, parent, label: str, value: str) -> None:
        """Add a label : value row to *parent* card frame."""
        row = ctk.CTkFrame(parent, fg_color=T.BG_FIELD, corner_radius=8)
        row.pack(fill="x", padx=10, pady=2)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            row, text=label, anchor="w",
            font=("Segoe UI", 9, "bold"), text_color=T.TEXT_SEC, width=72,
        ).grid(row=0, column=0, sticky="nw", padx=(10, 0), pady=8)
        ctk.CTkLabel(
            row, text=value, anchor="w",
            font=("Consolas", 9), text_color=T.TEXT_PRI,
            wraplength=380, justify="left",
        ).grid(row=0, column=1, sticky="w", padx=(6, 10), pady=8)

    def _button_row(self, *buttons: tuple) -> None:
        """Render a row of action buttons.

        Args:
            buttons: Each item is (label, result, text_color, fg_color).
        """
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=16)
        for text, result, tc, fg in buttons:
            ctk.CTkButton(
                row, text=text, width=148, height=40, corner_radius=10,
                font=("Segoe UI", 12, "bold"),
                fg_color=fg, hover_color=self._darken(fg),
                text_color=tc,
                command=lambda r=result: self._fire(r),
            ).pack(side="left", padx=6)

    @staticmethod
    def _darken(hex_color: str) -> str:
        """Return a slightly darker version of *hex_color* for hover state."""
        # Cheap approximation: just use a nearby dark tone.
        return {
            T.BTN_SUCCESS_BG: T.BTN_SUCCESS_HOV,
            T.DANGER_DIM:     T.BTN_DANGER_HOV,
            T.BTN_WARN_BG:    T.BTN_WARN_HOV,
            T.BG_FIELD:       T.BG_CARD,
        }.get(hex_color, T.BG_CARD_HOV)

    # ------------------------------------------------------------------ #
    # Result dispatch                                                      #
    # ------------------------------------------------------------------ #

    def _fire(self, result: str) -> None:
        """Call the callback with *result* and close the dialog.

        Args:
            result: One of "trust", "block", "skip", "update".
        """
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self.callback:
            # Call after destroy so the callback can open other dialogs.
            self.callback(result)
