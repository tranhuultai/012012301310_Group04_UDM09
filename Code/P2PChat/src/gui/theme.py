"""Design tokens — single source of truth for all GUI colours, fonts, and sizes.

Implements the Lumina P2P design system (light variant):
- surface: #fcf8ff base with indigo-primary (#4648d4)
- Teal secondary for security / verified indicators
- Soft rounding: 8px standard, 12px cards, circular avatars
- Typography: Inter rhythm via Segoe UI
"""
from __future__ import annotations

# ── Background layers (light hierarchy) ───────────────────────────────────
BG_APP      = "#fcf8ff"   # surface — root window
BG_SIDEBAR  = "#f5f2fe"   # surface-container-low — left panel
BG_MAIN     = "#ffffff"   # pure white — chat column
BG_HEADER   = "#efecf8"   # surface-container — headers
BG_INPUT    = "#f5f2fe"   # bottom input bar
BG_CARD     = "#ffffff"   # peer card / info card
BG_CARD_HOV = "#efecf8"   # peer card hover
BG_CARD_SEL = "#e1e0ff"   # peer card selected (primary-fixed)
BG_BUBBLE_ME= "#4648d4"   # sent bubble — Lumina primary
BG_BUBBLE_IN= "#efecf8"   # received bubble — surface-container
BG_FIELD    = "#e9e6f3"   # info rows — surface-container-high
BG_DETAILS  = "#f5f2fe"   # right panel
BG_TOAST    = "#1b1b23"   # toast — dark on-surface for contrast
BG_ENTRY    = "#ffffff"   # text entry
BG_BADGE    = "#4648d4"   # unread badge

# ── Borders / separators ──────────────────────────────────────────────────
BORDER       = "#c7c4d7"   # outline-variant
BORDER_LIGHT = "#e4e1ed"   # surface-container-highest

# ── Text ──────────────────────────────────────────────────────────────────
TEXT_PRI    = "#1b1b23"   # on-surface — primary text
TEXT_SEC    = "#464554"   # on-surface-variant — secondary text
TEXT_MUTED  = "#767586"   # outline — muted / placeholder
TEXT_TIME   = "#767586"   # timestamps (same as muted)
TEXT_LINK   = "#4648d4"   # primary — links / accent labels
TEXT_ON_ACCENT = "#a5b4fc"   # muted captions/timestamps on dark indigo surfaces
                              # (sent bubbles, sent-file cards) — needs more
                              # contrast against BG_BUBBLE_ME than TEXT_MUTED gives

# ── Accents — Lumina primary palette ──────────────────────────────────────
ACCENT      = "#4648d4"   # primary
ACCENT_HOV  = "#2f2ebe"   # on-primary-fixed-variant
ACCENT_DIM  = "#e1e0ff"   # primary-fixed — subtle tint
ACCENT_GLOW = "#c0c1ff"   # primary-fixed-dim — focus ring

# ── Semantic colours ──────────────────────────────────────────────────────
SUCCESS     = "#006b5f"   # secondary (teal) — verified / connected
WARNING     = "#904900"   # tertiary (amber/sienna)
DANGER      = "#ba1a1a"   # error
DANGER_DIM  = "#ffdad6"   # error-container

# ── Button-specific backgrounds (light theme needs explicit bg colours) ───
BTN_SUCCESS_BG  = "#dcfce7"   # light green tint for trust button
BTN_SUCCESS_HOV = "#bbf7d0"
BTN_WARN_BG     = "#fef3c7"   # light amber for "Accept new key"
BTN_WARN_HOV    = "#fde68a"
BTN_DANGER_HOV  = "#fecaca"   # hover on block / disconnect

# ── Trust palette ─────────────────────────────────────────────────────────
TRUST_NEW      = "#904900"   # tertiary
TRUST_TRUSTED  = "#4648d4"   # primary
TRUST_VERIFIED = "#006b5f"   # secondary
TRUST_MISMATCH = "#ba1a1a"   # error
TRUST_BLOCKED  = "#767586"   # outline

TRUST_BG: dict[str, str] = {
    "NEW":      "#fef3c7",   # warm amber tint
    "TRUSTED":  "#e1e0ff",   # primary-fixed
    "VERIFIED": "#d1f5ed",   # light teal
    "MISMATCH": "#ffdad6",   # error-container
    "BLOCKED":  "#e9e6f3",   # surface-container-high
}

TRUST_FG: dict[str, str] = {
    "NEW":      TRUST_NEW,
    "TRUSTED":  TRUST_TRUSTED,
    "VERIFIED": TRUST_VERIFIED,
    "MISMATCH": TRUST_MISMATCH,
    "BLOCKED":  TRUST_BLOCKED,
}

# ── Status dot colours ────────────────────────────────────────────────────
STATUS_DOT: dict[str, str] = {
    "online":     SUCCESS,
    "connected":  ACCENT,
    "offline":    "#c7c4d7",
    "connecting": WARNING,
}

# ── Avatar palette ────────────────────────────────────────────────────────
AVATAR_PALETTE: list[str] = [
    "#4648d4",  # indigo (primary)
    "#7c3aed",  # violet
    "#0891b2",  # cyan
    "#006b5f",  # teal (secondary)
    "#d97706",  # amber
    "#dc2626",  # red
    "#db2777",  # pink
    "#0284c7",  # sky
]

# ── Typography — Inter rhythm via Segoe UI ────────────────────────────────
FONT        = "Segoe UI"
FONT_MONO   = "Consolas"
FONT_TITLE  = (FONT, 14, "bold")
FONT_BODY   = (FONT, 13)
FONT_SMALL  = (FONT, 11)
FONT_BADGE  = (FONT, 9, "bold")
FONT_MONO_S = (FONT_MONO, 9)


def avatar_color(name: str) -> str:
    """Return a stable avatar colour for *name*."""
    return AVATAR_PALETTE[sum(ord(c) for c in name) % len(AVATAR_PALETTE)]
