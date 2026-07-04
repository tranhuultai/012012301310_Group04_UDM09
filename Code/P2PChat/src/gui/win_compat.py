"""Windows-only Tk/CTk compositing tweaks — cosmetic, never required for
correctness. Kept separate from theme.py to avoid a circular import between
ChatApp and TrustDialog."""
from __future__ import annotations

import logging

try:
    import pywinstyles
except ImportError:
    pywinstyles = None

logger = logging.getLogger(__name__)


def enable_layered_window(win) -> None:
    """Opt *win* into DWM layered-window compositing (Windows only) to stop a
    fast resize from briefly showing unpainted black canvas. No-op if
    pywinstyles isn't installed."""
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
