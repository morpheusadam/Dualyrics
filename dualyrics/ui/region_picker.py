"""Subtitle-area picker: a frozen picture of the target window, laid exactly over it; the user drags a
rectangle around the subtitles. The area is stored as fractions of the window's client area, so it keeps
fitting when the window is resized or goes fullscreen."""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Any, Callable

from dualyrics import sources as sources_mod
from dualyrics.i18n import t

log = logging.getLogger(__name__)

ACCENT = "#22D3EE"
SHADE = "#000000"


def pick_region(root: Any, hwnd: int, region: list[float], on_done: Callable[[list[float] | None], None]) -> bool:
    """Open the picker over ``hwnd`` (Tk thread). ``on_done`` gets the new region, or None when cancelled.
    Returns False when the window cannot be captured."""
    from PIL import ImageEnhance, ImageTk

    from dualyrics.screen import capture

    box = capture.client_box(hwnd)
    if box is None:
        return False
    try:
        shot = capture.grab(box)
    except Exception:
        log.exception("could not capture the window for the region picker")
        return False
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    dim = ImageEnhance.Brightness(shot).enhance(0.55)

    top_win = tk.Toplevel(root)
    top_win.overrideredirect(True)
    top_win.attributes("-topmost", True)
    top_win.geometry(f"{w}x{h}+{left}+{top}")   # the process is DPI aware: these are physical pixels
    canvas = tk.Canvas(top_win, width=w, height=h, highlightthickness=0, cursor="crosshair", bg="black")
    canvas.pack(fill="both", expand=True)
    photo_dim = ImageTk.PhotoImage(dim)
    canvas.create_image(0, 0, image=photo_dim, anchor="nw")
    keep: dict[str, Any] = {"dim": photo_dim, "bright": None, "done": False}
    x0, y0, x1, y1 = sources_mod.validate_region(region)
    rect = [x0 * w, y0 * h, x1 * w, y1 * h]
    bright_item = canvas.create_image(0, 0, anchor="nw")
    outline = canvas.create_rectangle(*rect, outline=ACCENT, width=2)
    help_bg = canvas.create_rectangle(0, 0, 0, 0, fill="#0B1220", outline="")
    help_txt = canvas.create_text(w // 2, 26, text=t("rp_help").replace("‫", "").replace("‬", ""),
                                  fill="#E6EDF7", font=("Segoe UI", 12))
    bx0, by0, bx1, by1 = canvas.bbox(help_txt)
    canvas.coords(help_bg, bx0 - 14, by0 - 8, bx1 + 14, by1 + 8)

    def button(text: str, x: int, cmd: Callable[[], None], primary: bool) -> None:
        tag = f"btn_{text}"
        label = text.replace("‫", "").replace("‬", "")
        tid = canvas.create_text(x, by1 + 34, text=label, fill="#0B1220" if primary else "#E6EDF7",
                                 font=("Segoe UI", 11, "bold"), tags=("btn", tag))
        a0, b0, a1, b1 = canvas.bbox(tid)
        bg = canvas.create_rectangle(a0 - 16, b0 - 7, a1 + 16, b1 + 7, fill=ACCENT if primary else "#1A2436",
                                     outline=ACCENT, tags=("btn", tag))
        canvas.tag_lower(bg, tid)
        canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e: cmd())
        canvas.tag_bind(tag, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind(tag, "<Leave>", lambda _e: canvas.configure(cursor="crosshair"))

    def show() -> None:
        a, b, c, d = (int(v) for v in rect)
        a, c = sorted((max(0, a), min(w, c)))
        b, d = sorted((max(0, b), min(h, d)))
        canvas.coords(outline, a, b, c, d)
        if c - a > 1 and d - b > 1:
            keep["bright"] = ImageTk.PhotoImage(shot.crop((a, b, c, d)))
            canvas.itemconfigure(bright_item, image=keep["bright"])
            canvas.coords(bright_item, a, b)
        canvas.tag_raise(outline)
        for item in (help_bg, help_txt):
            canvas.tag_raise(item)
        canvas.tag_raise("btn")
        for tid in canvas.find_withtag("btn"):
            if canvas.type(tid) == "text":
                canvas.tag_raise(tid)

    def finish(result: list[float] | None) -> None:
        if keep["done"]:
            return
        keep["done"] = True
        try:
            top_win.destroy()
        except tk.TclError:
            pass
        on_done(result)

    def save() -> None:
        a, b, c, d = rect
        finish(sources_mod.validate_region([min(a, c) / w, min(b, d) / h, max(a, c) / w, max(b, d) / h], region))

    drag: dict[str, Any] = {"start": None}

    def press(e: Any) -> None:
        if canvas.find_withtag("current") and any(tg.startswith("btn_") for tg in canvas.gettags("current")):
            return
        drag["start"] = (e.x, e.y)
        rect[:] = [e.x, e.y, e.x, e.y]
        show()

    def move(e: Any) -> None:
        if drag["start"] is not None:
            rect[2], rect[3] = e.x, e.y
            show()

    def release(_e: Any) -> None:
        if drag["start"] is not None:
            drag["start"] = None
            if abs(rect[2] - rect[0]) < 8 or abs(rect[3] - rect[1]) < 8:   # a click, not a drag: keep the old one
                rect[:] = [x0 * w, y0 * h, x1 * w, y1 * h]
            show()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", move)
    canvas.bind("<ButtonRelease-1>", release)
    canvas.bind("<Double-Button-1>", lambda _e: save())
    top_win.bind("<Return>", lambda _e: save())
    top_win.bind("<KP_Enter>", lambda _e: save())
    top_win.bind("<Escape>", lambda _e: finish(None))
    button(t("rp_save"), w // 2 - 60, save, True)
    button(t("rp_cancel"), w // 2 + 60, lambda: finish(None), False)
    show()
    top_win.after(60, lambda: top_win.winfo_exists() and (top_win.lift(), top_win.focus_force()))
    return True
