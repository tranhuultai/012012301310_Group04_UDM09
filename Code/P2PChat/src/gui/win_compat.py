"""Windows-only Tk/CTk compositing tweaks — cosmetic, never required for correctness.

Kept separate from theme.py (colors/fonts only) so both ChatApp and
TrustDialog can import this without a circular import: app.py already
pulls in trust_dialog.py indirectly (via main_window.py), so trust_dialog.py
can't import anything from app.py back.
"""
from __future__ import annotations

import logging

try:
    import pywinstyles
except ImportError:
    pywinstyles = None

logger = logging.getLogger(__name__)


def enable_layered_window(win) -> None:
    """Opt *win* into DWM layered-window compositing (Windows only).

    Tk on Windows paints via immediate-mode GDI with no whole-window double
    buffer, so a fast resize drag can outrun the repaint and briefly show
    unpainted (black) canvas — worse for CustomTkinter windows specifically,
    since CTk redraws every widget independently on <Configure> (confirmed
    by reading customtkinter's own source; not fixable from the caller's
    side). Marking the window WS_EX_LAYERED hands compositing to DWM, which
    renders off-screen and blits atomically, closing that gap.

    No-op if pywinstyles isn't installed — this is a cosmetic mitigation,
    never required for the app to function.
    """
    if pywinstyles is None:
        return

    def _apply() -> None:
        try:
            # pywinstyles' type stub says `widget: int`, but it accepts a Tk
            # widget directly and resolves the HWND via widget.winfo_id()
            # internally (confirmed by reading its source).
            pywinstyles.set_opacity(win, value=1.0)  # type: ignore[arg-type]
        except Exception:   # pylint: disable=broad-exception-caught
            logger.warning("[WIN_COMPAT] Layered-window flicker fix unavailable", exc_info=True)

    # Deferred so win's HWND exists. The try/except is inside _apply, not
    # around this after() call, because after() only schedules _apply — it
    # can't fail with the error this is meant to guard against.
    win.after(100, _apply)
