"""Chat bubble drawing — raw Canvas items (polygon + text), not CTk widgets,
since one CTkFrame per message redrew on every <Configure> and caused a
flicker cascade under resize/flood. Each draw fn returns its total height."""
from __future__ import annotations

from datetime import datetime

from gui import theme as T

# Vertical gap above a row: tight within a same-sender group, wider when a
# new sender starts (or after a system message/divider breaks the group).
_GAP_GROUPED = 2
_GAP_NEW_GROUP = 10

_BUBBLE_RADIUS = 18
_BUBBLE_SIDE_MARGIN = 16   # gutter kept clear on the "own side" of a bubble
_BUBBLE_OPP_MARGIN = 72    # wider gutter on the opposite side (room to breathe)


def _now() -> str:
    return datetime.now().strftime("%H:%M")


def _round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """Draw a rounded rectangle as a smoothed 12-point polygon; returns its item id."""
    r = radius
    points = [
        x1 + r, y1,       x2 - r, y1,      x2, y1,      x2, y1 + r,
        x2, y2 - r,       x2, y2,          x2 - r, y2,  x1 + r, y2,
        x1, y2,           x1, y2 - r,      x1, y1 + r,  x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def draw_bubble(canvas, top_y: int, canvas_width: int, wraplength: int,
                 message: str, sender: str, is_me: bool, grouped: bool,
                 tag: str) -> int:
    """Draw one bubble at top_y; returns its total height including top gap.
    is_me: right-aligned indigo vs left-aligned card. tag: this row's canvas tag."""
    ts = _now()
    top_gap = _GAP_GROUPED if grouped else _GAP_NEW_GROUP
    y = top_y + top_gap

    # Drawn at the origin first so real wrapped size can be measured via
    # bbox, then repositioned — Tk's layout, not hand-rolled font metrics.
    name_id = None
    name_w = 0
    if not is_me and not grouped:
        name_id = canvas.create_text(
            0, 0, text=sender, anchor="nw",
            fill=T.TEXT_LINK, font=(T.FONT, 10, "bold"), tags=(tag,))
        nx1, _, nx2, _ = canvas.bbox(name_id)
        name_w = nx2 - nx1

    # Max width a bubble may occupy, leaving the opposite side's gutter
    # clear (mirrors the old padx=(72,0)/(0,72) reserved gutter).
    max_wrap = min(wraplength, canvas_width - _BUBBLE_SIDE_MARGIN - _BUBBLE_OPP_MARGIN - 32)
    msg_id = canvas.create_text(
        0, 0, text=message, anchor="nw", justify="left", width=max(max_wrap, 60),
        fill="#e0e7ff" if is_me else T.TEXT_PRI,
        font=(T.FONT, 13), tags=(tag,))
    mx1, my1, mx2, my2 = canvas.bbox(msg_id)
    msg_w, msg_h = mx2 - mx1, my2 - my1

    footer_text, footer_fill = (
        (f"{ts}    ✓✓", T.TEXT_ON_ACCENT) if is_me else (ts, T.TEXT_TIME))
    footer_id = canvas.create_text(
        0, 0, text=footer_text, anchor="nw",
        fill=footer_fill, font=(T.FONT, 9), tags=(tag,))
    fx1, fy1, fx2, fy2 = canvas.bbox(footer_id)
    footer_w, footer_h = fx2 - fx1, fy2 - fy1

    content_w = max(msg_w, footer_w, name_w)
    bubble_w = content_w + 32   # 16px padding each side
    top_pad = 12 if (is_me or grouped) else (10 + (18 if name_id is not None else 0))
    bubble_h = top_pad + msg_h + 6 + footer_h + 10

    if is_me:
        bx2 = canvas_width - _BUBBLE_SIDE_MARGIN
        bx1 = bx2 - bubble_w
    else:
        bx1 = _BUBBLE_SIDE_MARGIN
        bx2 = bx1 + bubble_w
    by1, by2 = y, y + bubble_h

    rect_fill = T.BG_BUBBLE_ME if is_me else T.BG_BUBBLE_IN
    _round_rect(canvas, bx1, by1, bx2, by2, _BUBBLE_RADIUS,
                fill=rect_fill, outline="", tags=(tag,))

    # Reposition text on top of the just-drawn rect (draw order alone put
    # the rect above the text; tag_raise fixes the stacking).
    if name_id is not None:
        canvas.coords(name_id, bx1 + 16, by1 + 10)
        canvas.tag_raise(name_id)
    canvas.coords(msg_id, bx1 + 16, by1 + top_pad)
    canvas.tag_raise(msg_id)
    canvas.coords(footer_id, bx2 - 14 - footer_w, by2 - 10 - footer_h)
    canvas.tag_raise(footer_id)

    return by2 - top_y


def draw_system(canvas, top_y: int, canvas_width: int, text: str, tag: str) -> int:
    """Draw a centred system notice pill (e.g. "Connected"). Returns height."""
    top_gap = 6
    label_id = canvas.create_text(
        0, 0, text=text, anchor="nw",
        fill=T.TEXT_MUTED, font=(T.FONT, 9), tags=(tag,))
    lx1, ly1, lx2, ly2 = canvas.bbox(label_id)
    label_w, label_h = lx2 - lx1, ly2 - ly1

    pad_x, pad_y = 12, 4
    pill_w, pill_h = label_w + pad_x * 2, label_h + pad_y * 2
    y = top_y + top_gap
    cx = canvas_width / 2
    bx1, bx2 = cx - pill_w / 2, cx + pill_w / 2
    by1, by2 = y, y + pill_h

    _round_rect(canvas, bx1, by1, bx2, by2, pill_h / 2,
                fill=T.BG_FIELD, outline="", tags=(tag,))
    canvas.coords(label_id, bx1 + pad_x, by1 + pad_y)
    canvas.tag_raise(label_id)

    return by2 - top_y


def draw_divider(canvas, top_y: int, canvas_width: int, label: str, tag: str) -> int:
    """Draw a centred date divider (label chip with rules on each side)."""
    top_gap = 10
    label_id = canvas.create_text(
        0, 0, text=label, anchor="nw",
        fill=T.TEXT_MUTED, font=(T.FONT, 9), tags=(tag,))
    lx1, ly1, lx2, ly2 = canvas.bbox(label_id)
    label_w, label_h = lx2 - lx1, ly2 - ly1

    pad_x, pad_y = 8, 4
    chip_w, chip_h = label_w + pad_x * 2, label_h + pad_y * 2
    y = top_y + top_gap
    line_y = y + chip_h / 2
    cx = canvas_width / 2
    bx1, bx2 = cx - chip_w / 2, cx + chip_w / 2

    canvas.create_line(16, line_y, bx1 - 6, line_y, fill=T.BORDER, tags=(tag,))
    canvas.create_line(bx2 + 6, line_y, canvas_width - 16, line_y,
                        fill=T.BORDER, tags=(tag,))
    _round_rect(canvas, bx1, y, bx2, y + chip_h, chip_h / 2,
                fill=T.BG_FIELD, outline="", tags=(tag,))
    canvas.coords(label_id, bx1 + pad_x, y + pad_y)
    canvas.tag_raise(label_id)

    return chip_h + top_gap * 2   # symmetric top/bottom breathing room
