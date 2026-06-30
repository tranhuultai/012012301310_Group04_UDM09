"""Chat bubbles — responsive, Lumina-inspired message rendering.

Design decisions:
    - add_chat_bubble returns the message label so ChatBox can track
      it for wraplength updates on window resize (CRITICAL for correctness).
    - Wraplength is passed in, not hardcoded, so resize events can update
      all existing bubbles uniformly.
    - Sent bubbles align right with anchor="e"; received align left.
    - Both sides have a timestamp footer — intentional per the Lumina spec.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import customtkinter as ctk

from gui import theme as T


def _now() -> str:
    return datetime.now().strftime("%H:%M")


def add_chat_bubble(
    parent,
    message: str,
    sender: str = "me",
    is_me: bool = False,
    wraplength: int = 400,
) -> Optional[ctk.CTkLabel]:
    """Append one message bubble to *parent* and return the message label.

    The caller should store the returned label in a list so that
    wraplength can be updated later when the chat column resizes.
    Returns None only if an unexpected error occurs.

    Args:
        parent: A CTkScrollableFrame (or any CTk container using pack).
        message: Plaintext message body.
        sender: Display name (shown on received bubbles).
        is_me: True → right-aligned indigo bubble; False → left-aligned card.
        wraplength: Initial wrap width in pixels — caller updates on resize.
    """
    ts = _now()

    outer = ctk.CTkFrame(parent, fg_color="transparent")
    outer.pack(fill="x", padx=16, pady=(2, 2))

    if is_me:
        bubble = ctk.CTkFrame(
            outer, fg_color=T.BG_BUBBLE_ME, corner_radius=16)
        bubble.pack(anchor="e", padx=(72, 0))

        msg_lbl = ctk.CTkLabel(
            bubble, text=message,
            justify="left", wraplength=wraplength,
            text_color="#e0e7ff",
            font=(T.FONT, 13),
        )
        msg_lbl.pack(anchor="w", padx=(14, 14), pady=(10, 4))

        ctk.CTkLabel(
            bubble, text=f"{ts}  ✓✓",
            text_color="#6366f1",
            font=(T.FONT, 9),
        ).pack(anchor="e", padx=(14, 12), pady=(0, 8))

    else:
        bubble = ctk.CTkFrame(
            outer, fg_color=T.BG_BUBBLE_IN, corner_radius=16)
        bubble.pack(anchor="w", padx=(0, 72))

        ctk.CTkLabel(
            bubble, text=sender, anchor="w",
            text_color=T.TEXT_LINK,
            font=(T.FONT, 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(10, 0))

        msg_lbl = ctk.CTkLabel(
            bubble, text=message,
            justify="left", wraplength=wraplength,
            text_color=T.TEXT_PRI,
            font=(T.FONT, 13),
        )
        msg_lbl.pack(anchor="w", padx=14, pady=(2, 4))

        ctk.CTkLabel(
            bubble, text=ts,
            text_color=T.TEXT_TIME,
            font=(T.FONT, 9),
        ).pack(anchor="e", padx=12, pady=(0, 8))

    return msg_lbl
