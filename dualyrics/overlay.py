"""The floating lyrics bar: original line + translation under it, drawn on a Tk canvas.

Drag = move (unless locked) · mouse wheel = font size (scroll for unsynced lyrics, Ctrl+wheel = size)
· double-click = show/hide next line · right-click = menu.
"""

from __future__ import annotations

import logging
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from typing import Any, Callable

from dualyrics import langs, winutil
from dualyrics.i18n import t
from dualyrics.settings import Settings

log = logging.getLogger(__name__)

KEY_COLOR = "#010203"        # colour made fully transparent in "text only" mode
RTL_FONTS = ("Vazirmatn", "Vazirmatn UI", "Vazir", "Segoe UI", "Tahoma")
LTR_FONTS = ("Segoe UI", "Arial")
RLE, PDF = "‫", "‬"
BIDI_CONTROLS = str.maketrans("", "", "‎‏‪‫‬‭‮")


@dataclass(frozen=True)
class Content:
    mode: str = "synced"                       # 'synced' | 'plain'
    original: str = ""
    translation: str = ""
    next: str = ""
    status: str = ""
    rows: tuple[tuple[str, str], ...] = ()     # plain lyrics: (original, translation)
    progress: float = -1.0                     # plain lyrics: 0..1 estimated song progress
    song: str = ""                             # identity, resets plain-mode scrolling


class Overlay:
    def __init__(self, root: tk.Tk, settings: Settings, send: Callable[[str], None]) -> None:
        self.root = root
        self.settings = settings
        self.send = send
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.title("Dualyrics")
        self.cv = tk.Canvas(self.win, highlightthickness=0, bd=0, yscrollincrement=1)
        self.cv.pack(fill="both", expand=True)
        self.scale = max(1.0, self.win.winfo_fpixels("1i") / 96.0)
        self.families = set(tkfont.families(root))
        self.visible = False
        self._content: Content | None = None
        self._drawn: tuple[Any, ...] | None = None
        self._size = (0, 0)          # applied window size (px)
        self._draw_w = 0             # width used for the current drawing (px)
        self._drag: dict[str, int] = {}
        self._moved = False
        self._user_scrolled_at = 0.0
        self._plain_total = 0
        self._plain_song = ""
        self._font_cache: dict[tuple[Any, ...], tkfont.Font] = {}
        self._menu: tk.Menu | None = None
        self.cfg: dict[str, Any] = settings.snapshot()
        for ev, fn in (("<ButtonPress-1>", self._on_press), ("<B1-Motion>", self._on_drag),
                       ("<ButtonRelease-1>", self._on_release), ("<MouseWheel>", self._on_wheel),
                       ("<Double-Button-1>", lambda e: self.send("toggle_next")),
                       ("<Button-3>", self._on_menu)):
            self.cv.bind(ev, fn)
        self.apply_settings()

    # ------------------------------------------------------------------ settings
    def apply_settings(self) -> None:
        self.cfg = self.settings.snapshot()
        c = self.cfg
        bg = KEY_COLOR if c["text_only"] else c["color_bg"]
        self.cv.configure(bg=bg)
        self.win.configure(bg=bg)
        try:
            self.win.attributes("-transparentcolor", KEY_COLOR if c["text_only"] else "")
            # always < 1.0 so the window stays layered (needed for click-through)
            self.win.attributes("-alpha", 0.995 if c["text_only"] else min(0.995, c["opacity"]))
        except tk.TclError:
            pass
        self._drawn = None
        self._apply_window_styles()
        if self._content is not None:
            self.render(self._content, force=True)
        if self.visible:
            self._place()  # position preset / monitor may have changed

    def _apply_window_styles(self) -> None:
        if not self.win.winfo_ismapped():
            return
        hwnd = winutil.toplevel_hwnd(self.win)
        winutil.set_click_through(hwnd, self.cfg["click_through"])
        winutil.set_round_corners(hwnd, self.cfg["rounded"] and not self.cfg["text_only"])

    # ------------------------------------------------------------------ fonts
    def _family(self, choice: str, text: str) -> str:
        if choice and choice != "auto" and choice in self.families:
            return choice
        pool = RTL_FONTS if langs.is_rtl_text(text) else LTR_FONTS
        return next((f for f in pool if f in self.families), "TkDefaultFont")

    def _font(self, kind: str, text: str) -> tuple[Any, ...]:
        c = self.cfg
        if kind == "next":
            return (self._family(c["font_original"], text), max(8, int(c["size_original"] * 0.72)))
        if kind == "status":
            return (self._family("auto", text), 10)
        fam = self._family(c[f"font_{kind}"], text)
        size = c[f"size_{kind}"]
        return (fam, size, "bold") if c[f"bold_{kind}"] else (fam, size)

    # ------------------------------------------------------------------ drawing
    def _wrap_rtl(self, text: str, font: tuple[Any, ...], max_w: int) -> str:
        """Wrap right-to-left text ourselves and embed each line in RLE…PDF.

        Tk wraps lines on its own but draws every wrapped piece left-to-right, which scrambles
        mixed Persian/English lines. Pre-wrapping keeps each visual line correctly ordered.
        """
        f = self._font_cache.get(font)
        if f is None:
            f = self._font_cache[font] = tkfont.Font(root=self.win, font=font)
        out: list[str] = []
        for para in text.split("\n"):
            line = ""
            for word in para.split():
                cand = f"{line} {word}" if line else word
                if line and f.measure(cand) > max_w:
                    out.append(line)
                    line = word
                else:
                    line = cand
            out.append(line)
        return "\n".join(RLE + ln + PDF if ln else "" for ln in out)

    def _text(self, y: int, text: str, kind: str, color: str) -> int:
        """Draw one wrapped block (with outline/shadow) starting at ``y``; returns the bottom y."""
        c = self.cfg
        w = self._draw_w
        pad = int(16 * self.scale)
        text = text.translate(BIDI_CONTROLS)
        align = c["align"]
        x, anchor = {"left": (pad, "nw"), "right": (w - pad, "ne")}.get(align, (w // 2, "n"))
        font = self._font(kind, text)
        if langs.is_rtl_text(text):
            text = self._wrap_rtl(text, font, max(50, w - 2 * pad) - 4)
        opts = dict(text=text, font=font, anchor=anchor, justify=align, width=max(50, w - 2 * pad))
        if c["shadow"]:
            off = max(1, int(2 * self.scale))
            self.cv.create_text(x + off, y + off, fill="#000000", **opts)
        r = int(c["outline_width"] * self.scale) if c["outline"] else 0
        if r:
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if (dx or dy) and dx * dx + dy * dy <= r * r + 1:
                        self.cv.create_text(x + dx, y + dy, fill=c["outline_color"], **opts)
        item = self.cv.create_text(x, y, fill=color, **opts)
        box = self.cv.bbox(item)
        return box[3] if box else y

    def render(self, content: Content, force: bool = False) -> None:
        self._content = content
        c = self.cfg
        sig = (content.mode, content.original, content.translation, content.next, content.status,
               content.rows, content.song)
        width = int(c["width"] * self.scale)
        if content.mode == "plain":
            if force or sig != self._drawn:
                self._draw_plain(content, width)
                self._drawn = sig
            self._auto_scroll(content.progress)
            return
        if not force and sig == self._drawn:
            return
        self._drawn = sig
        self.cv.delete("all")
        self.cv.yview_moveto(0)
        self.cv.configure(scrollregion=(0, 0, 0, 0))
        self._draw_w = width
        gap = int(4 * self.scale)
        y = int(10 * self.scale)
        original = content.original if c["show_original"] else ""
        translation = content.translation if c["show_translation"] else ""
        if not original and not translation and not content.status:
            original = "♪"
        if original:
            y = self._text(y, original, "original", c["color_original"]) + gap
        if translation:
            y = self._text(y, translation, "translation", c["color_translation"]) + gap
        if c["show_next"] and content.next:
            y = self._text(y + gap, content.next, "next", c["color_next"]) + gap
        if content.status:
            y = self._text(y, content.status, "status", c["color_next"]) + gap
        height = max(int(40 * self.scale), y + int(8 * self.scale))
        self._resize(width, height)

    def _draw_plain(self, content: Content, width: int) -> None:
        c = self.cfg
        self._draw_w = width
        top = self.cv.canvasy(0)
        if content.song != self._plain_song:
            self._plain_song = content.song
            self._user_scrolled_at = 0.0
            top = 0.0
        self.cv.delete("all")
        y = int(10 * self.scale)
        if content.status:
            y = self._text(y, content.status, "status", c["color_next"]) + int(8 * self.scale)
        for orig, trans in content.rows:
            if not orig.strip():
                y += int(12 * self.scale)
                continue
            if c["show_original"]:
                y = self._text(y, orig, "original", c["color_original"])
            if c["show_translation"] and trans:
                y = self._text(y, trans, "translation", c["color_translation"])
            y += int(8 * self.scale)
        height = int(c["plain_height"] * self.scale)
        self._plain_total = max(y, height)
        self.cv.configure(scrollregion=(0, 0, width, self._plain_total))
        self._resize(width, height)
        if self._plain_total:
            self.cv.yview_moveto(top / self._plain_total)

    def _auto_scroll(self, progress: float) -> None:
        if progress < 0 or time.time() - self._user_scrolled_at < 8 or not self._plain_total:
            return
        view = self._size[1]
        target = max(0.0, progress * self._plain_total - view * 0.35)
        self.cv.yview_moveto(target / self._plain_total)

    def _resize(self, w: int, h: int) -> None:
        if (w, h) != self._size or not self.visible:
            self._size = (w, h)
            self._place()

    # ------------------------------------------------------------------ position
    def _monitor(self) -> winutil.Monitor:
        mons = winutil.monitors()
        name = self.cfg["monitor"]
        return next((m for m in mons if m.name == name), next((m for m in mons if m.primary), mons[0]))

    def _place(self) -> None:
        w, h = self._size
        if w <= 0 or h <= 0:
            return
        c = self.cfg
        mon = self._monitor()
        margin = int(56 * self.scale)
        pos = c["positions"].get(mon.name)
        x = mon.left + (mon.width - w) // 2
        if c["position_preset"] == "custom" and pos:
            x = mon.left + pos["x"]
            y = mon.top + pos["y"] - (h if pos["anchor"] == "bottom" else 0)
        elif c["position_preset"] == "top":
            y = mon.top + margin
        elif c["position_preset"] == "center":
            y = mon.top + (mon.height - h) // 2
        else:
            y = mon.bottom - margin - h
        # clamp into the visible work area of that monitor (multi-monitor safe)
        x = max(mon.left, min(x, mon.right - w)) if w <= mon.width else mon.left
        y = max(mon.top, min(y, mon.bottom - h)) if h <= mon.height else mon.top
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def _save_position(self) -> None:
        self.win.update_idletasks()
        x, y = self.win.winfo_x(), self.win.winfo_y()
        w, h = self._size
        mon = winutil.monitor_at(x + w // 2, y + h // 2)
        bottom = (y + h // 2) > mon.top + mon.height // 2
        positions = dict(self.cfg["positions"])
        positions[mon.name] = {"x": x - mon.left, "y": (y + h - mon.top) if bottom else (y - mon.top),
                               "anchor": "bottom" if bottom else "top"}
        self.settings.update(positions=positions, monitor=mon.name, position_preset="custom")

    # ------------------------------------------------------------------ visibility
    def show(self) -> None:
        if not self.visible:
            self.visible = True
            self._place()
            self.win.deiconify()
            self.win.lift()
            self.win.attributes("-topmost", True)
            self.win.update_idletasks()
            self._apply_window_styles()

    def hide(self) -> None:
        if self.visible:
            self.visible = False
            self.win.withdraw()

    # ------------------------------------------------------------------ mouse
    def _on_press(self, e: tk.Event) -> None:
        self._drag = {"x": e.x_root - self.win.winfo_x(), "y": e.y_root - self.win.winfo_y()}
        self._moved = False

    def _on_drag(self, e: tk.Event) -> None:
        if self.cfg["locked"] or not self._drag:
            return
        self._moved = True
        self.win.geometry(f"+{e.x_root - self._drag['x']}+{e.y_root - self._drag['y']}")

    def _on_release(self, _e: tk.Event) -> None:
        if self._moved and not self.cfg["locked"]:
            self._save_position()
        self._drag = {}
        self._moved = False

    def _on_wheel(self, e: tk.Event) -> None:
        step = 1 if e.delta > 0 else -1
        ctrl = bool(e.state & 0x0004)
        if self._content and self._content.mode == "plain" and not ctrl:
            self._user_scrolled_at = time.time()
            self.cv.yview_scroll(int(-step * 40 * self.scale), "units")
            return
        # read the live values: self.cfg refreshes only on the next tick, so fast wheel turns were lost
        s = self.settings
        s.update(size_original=s["size_original"] + step, size_translation=s["size_translation"] + step)

    def _on_menu(self, e: tk.Event) -> None:
        c = self.cfg
        if self._menu is not None:  # one menu at a time; a new widget per right-click leaked
            try:
                self._menu.destroy()
            except tk.TclError:
                pass
        m = self._menu = tk.Menu(self.win, tearoff=0)
        m.add_command(label=t("settings"), command=lambda: self.send("settings"))
        m.add_command(label=t("retranslate"), command=lambda: self.send("retranslate"))
        m.add_separator()
        step = c["offset_step_ms"]
        m.add_command(label=t("offset_earlier", ms=step), command=lambda: self.send("offset_down"))
        m.add_command(label=t("offset_later", ms=step), command=lambda: self.send("offset_up"))
        m.add_command(label=t("offset_reset"), command=lambda: self.send("offset_reset"))
        m.add_separator()
        for key, cmd in (("show_original", "toggle_original"), ("show_translation", "toggle_translation"),
                         ("show_next", "toggle_next"), ("text_only", "toggle_text_only"),
                         ("locked", "toggle_locked"), ("click_through", "toggle_click_through")):
            var = tk.BooleanVar(m, value=c[key])
            label = t("lock_position") if key == "locked" else t(key)
            m.add_checkbutton(label=label, variable=var, command=lambda cmd=cmd: self.send(cmd))
        pos = tk.Menu(m, tearoff=0)
        for preset in ("bottom", "top", "center"):
            pos.add_command(label=t(f"pr_{preset}"), command=lambda p=preset: self.send(f"preset_{p}"))
        m.add_cascade(label=t("position"), menu=pos)
        m.add_separator()
        m.add_command(label=t("hide"), command=lambda: self.send("toggle_visible"))
        m.add_command(label=t("quit"), command=lambda: self.send("quit"))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
