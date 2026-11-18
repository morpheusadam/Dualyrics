"""The floating subtitle window: renders the active template with per-pixel alpha.

Mouse: drag = move (unless locked) · drag an edge or corner = resize (left/right edges reflow the text,
top/bottom edges and corners scale it) · wheel = text size (scrolls unsynced lyrics; Ctrl+wheel = size) ·
double-click = show/hide the next line · right-click = menu.

Position and size are remembered per monitor and per app profile (``positions`` in settings.json, keys
``<monitor>`` and ``<profile id>@<monitor>``).
"""

from __future__ import annotations

import logging
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from PIL import Image, ImageTk

from dualyrics import backdrop as backdrop_mod, langs, render, templates, vdesk, winutil
from dualyrics.backdrop import Backdrop
from dualyrics.i18n import is_rtl, t
from dualyrics.layered import LayeredSurface
from dualyrics.settings import Settings
from dualyrics.ui import icons

log = logging.getLogger(__name__)

MARGIN = 56                # px from the screen edge for the bottom/top presets (at 100 %)
MIN_BOX_W = 160
SCALE_MIN, SCALE_MAX = 0.5, 2.0
FRAME_MS = 16
KARAOKE_MS = 33
MONITOR_POLL = 2.0
DESKTOP_POLL = 1.0
EASINGS: dict[str, Callable[[float], float]] = {
    "linear": lambda x: x,
    "ease_out": lambda x: 1 - (1 - x) ** 3,
    "ease_in_out": lambda x: 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2,
    "ease_out_back": lambda x: 1 + 2.70158 * (x - 1) ** 3 + 1.70158 * (x - 1) ** 2,
}
QUICK_LANGS = ("en", "fa", "ar", "tr", "es", "fr", "de", "ru", "zh-CN", "ja", "ko", "hi", "it", "pt")
MENU_DIM = "#5B6A80"
MENU_COLORS = dict(bg="#1A2436", fg="#E6EDF7", activebackground="#123A4A", activeforeground="#E6EDF7",
                   selectcolor="#22D3EE", disabledforeground="#8E9DB3")


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
    speaker: str = ""
    index: int = -1                            # synced line index (for "edit this line")
    line_start: float = -1.0                   # karaoke: position (s) where the line starts ...
    line_end: float = -1.0                     # ... and where the next one starts
    tail: bool = False                         # Live / on-screen captions: a long line keeps its newest words


class Host(Protocol):
    """What the overlay asks the app (menu state, clock, actions that need app data)."""

    def menu_info(self) -> dict[str, Any]: ...
    def position(self) -> float: ...
    def edit_translation(self, index: int, text: str) -> None: ...


class Overlay:
    def __init__(self, root: tk.Tk, settings: Settings, send: Callable[[str], None],
                 store: templates.TemplateStore, host: Host | None = None) -> None:
        self.root = root
        self.settings = settings
        self.send = send
        self.store = store
        self.host = host
        self.renderer = render.Renderer()
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.title("Dualyrics overlay")
        self.area = tk.Frame(self.win, bg="#000000", highlightthickness=0, bd=0)
        self.area.pack(fill="both", expand=True)
        self.surface = LayeredSurface()
        self.win.update_idletasks()
        self.hwnd = winutil.toplevel_hwnd(self.win)
        self.surface.attach(self.hwnd)
        self.backdrop = Backdrop(root, self.hwnd)   # real blur under the box (frost mode "acrylic")
        self._scale = 1.0                            # scale of the current frame (corner radius of the blur)
        self.visible = False
        self.overrides: dict[str, Any] = {}
        self.profile_id = ""                 # active app profile (positions are remembered per profile)
        self.active_monitor = ""             # monitor of the foreground app ("follow the active app")
        self.cfg: dict[str, Any] = settings.snapshot()
        self.template: dict[str, Any] = store.resolve(self.cfg["template"])
        self._tpl_key: tuple[str, int] = ("", -1)
        self._content: Content | None = None
        self._sig: tuple[Any, ...] | None = None
        self._frame: render.Frame | None = None
        self._shown: Image.Image | None = None
        self._pos = (0, 0)                     # screen position of the current frame image
        self._alpha = 1.0
        self._anim: dict[str, Any] | None = None
        self._anim_job: str | None = None
        self._kara_job: str | None = None
        self._kara_timing: tuple[float, float] = (-1.0, -1.0)
        self._fade: dict[str, Any] | None = None
        self._fade_job: str | None = None
        self._drag: dict[str, Any] = {}
        self._resize: dict[str, Any] = {}
        self._plain_scroll = 0
        self._plain_total = 0
        self._plain_song = ""
        self._user_scrolled_at = 0.0
        self._menu: tk.Menu | None = None
        self._menu_images: list[Any] = []
        self._editor: tk.Toplevel | None = None
        self._mon_sig: tuple[Any, ...] = ()
        self._mon_at = 0.0
        self._vd_at = 0.0
        self._home_desktop = ""
        self.desktop_hidden = False
        for ev, fn in (("<ButtonPress-1>", self._on_press), ("<B1-Motion>", self._on_drag),
                       ("<ButtonRelease-1>", self._on_release), ("<MouseWheel>", self._on_wheel),
                       ("<Double-Button-1>", lambda e: self.send("toggle_next")),
                       ("<Button-3>", self._on_menu), ("<Motion>", self._on_motion),
                       ("<Leave>", lambda e: self._set_cursor(""))):
            self.area.bind(ev, fn)
        store.subscribe(lambda: root.after(0, self._template_changed))
        self.apply_settings()

    # ------------------------------------------------------------------ settings and template
    def set_overrides(self, overrides: dict[str, Any], profile_id: str = "") -> None:
        """Settings the active app profile overrides (template, monitor, position, width, click-through...)."""
        self.overrides = dict(overrides)
        self.profile_id = profile_id
        self.apply_settings()

    def set_active_monitor(self, name: str) -> None:
        if name and name != self.active_monitor:
            self.active_monitor = name
            if self.cfg.get("follow_active_monitor") and self._content is not None:
                self._sig = None
                self.render(self._content, force=True)

    def apply_settings(self) -> None:
        self.cfg = {**self.settings.snapshot(), **self.overrides}
        self._load_template()
        self._sig = None
        self._apply_window_styles()
        if self._content is not None:
            self.render(self._content, force=True)

    def _template_changed(self) -> None:
        self._load_template()
        if self._content is not None:
            self.render(self._content, force=True)

    def _load_template(self) -> None:
        key = (self.cfg["template"], self.store.rev)
        if key != self._tpl_key:
            self._tpl_key = key
            self.template = self.store.resolve(self.cfg["template"])
            self._sig = None

    def _apply_window_styles(self) -> None:
        winutil.set_click_through(self.hwnd, bool(self.cfg["click_through"]))

    # ------------------------------------------------------------------ geometry
    def monitor(self) -> winutil.Monitor:
        """Where the overlay goes: the active app's monitor (when following), the chosen one, else primary,
        always among the monitors the user allows."""
        mons = winutil.monitors()
        off = set(self.cfg.get("monitors_off") or [])
        allowed = [m for m in mons if m.name not in off] or mons
        names = {m.name: m for m in allowed}
        if self.cfg.get("follow_active_monitor") and self.active_monitor in names:
            return names[self.active_monitor]
        if self.cfg.get("monitor") in names:
            return names[self.cfg["monitor"]]
        return next((m for m in allowed if m.primary), allowed[0])

    def _entry(self, mon: winutil.Monitor) -> tuple[str, dict[str, Any]]:
        """Saved place/size for this profile and monitor: the profile's own values win, the rest comes from the
        monitor's global entry (a profile entry may hold only a size, from Ctrl+wheel). The scope is "profile"
        when the profile has its own place (x/y)."""
        pos = self.cfg.get("positions") or {}
        glob = pos.get(mon.name) or {}
        prof = pos.get(f"{self.profile_id}@{mon.name}") if self.profile_id else None
        if prof:
            return ("profile" if "x" in prof else "global"), {**glob, **prof}
        if glob:
            return "global", dict(glob)
        return "", {}

    def _save_key(self, mon: winutil.Monitor) -> str:
        return f"{self.profile_id}@{mon.name}" if self.profile_id else mon.name

    def user_scale(self, mon: winutil.Monitor | None = None) -> float:
        mon = mon or self.monitor()
        _k, e = self._entry(mon)
        return float(e.get("scale", self.cfg.get("overlay_scale", 1.0)))

    def _metrics(self, mon: winutil.Monitor) -> tuple[float, int]:
        """(scale, max box width in px)."""
        tpl = self.template
        s = self.renderer.scale(tpl, mon.screen_height, mon.scale, self.user_scale(mon))
        _k, e = self._entry(mon)
        max_w = self.renderer.max_width(tpl, mon.width, s)
        if self.overrides.get("width"):
            max_w = int(self.overrides["width"] * mon.scale)
        if e.get("w"):
            max_w = int(e["w"])
        return s, max(MIN_BOX_W, min(max_w, mon.width))

    # ------------------------------------------------------------------ rendering
    def _show_flags(self) -> dict[str, bool]:
        c = self.cfg
        return {"original": bool(c["show_original"]), "translation": bool(c["show_translation"]),
                "next": bool(c["show_next"])}

    def render(self, content: Content, force: bool = False) -> None:
        self._content = content
        if self._resize.get("active"):
            return
        mon = self.monitor()
        s, max_w = self._metrics(mon)
        self._scale = s
        hit = not self.cfg["click_through"]
        if content.mode == "plain":
            if content.song != self._plain_song:
                self._plain_song = content.song
                self._plain_scroll = 0
                self._user_scrolled_at = 0.0
            sig = ("plain", content.rows, content.status, self._tpl_key, s, max_w, hit, self._plain_scroll,
                   tuple(self._show_flags().items()), mon.name)
            if not force and sig == self._sig:
                self._auto_scroll(content.progress)
                return
            self._sig = sig
            view_h = int(self.cfg["plain_height"] * mon.scale)
            frame, total = self.renderer.render_list(self.template, list(content.rows), content.status, s, max_w,
                                                     view_h, self._plain_scroll, show=self._show_flags(), hit=hit)
            self._plain_total = total
            self._present(frame, animate=False)
            self._auto_scroll(content.progress)
            return
        sig = ("synced", content.original, content.translation, content.next, content.status, content.speaker,
               self._tpl_key, s, max_w, hit, tuple(self._show_flags().items()), mon.name, content.tail)
        self._kara_timing = (content.line_start, content.line_end)
        if not force and sig == self._sig:
            return
        text_changed = self._sig is None or self._sig[0] != "synced" or self._sig[1:3] != sig[1:3]
        self._sig = sig
        lines = render.Lines(content.original, content.translation, content.next, content.speaker, content.status,
                             content.tail)
        try:
            frame = self.renderer.render(self.template, lines, s, max_w, show=self._show_flags(), hit=hit)
        except Exception:
            log.exception("render failed")
            return
        self._present(frame, animate=text_changed and not force)

    def _frame_pos(self, frame: render.Frame, mon: winutil.Monitor) -> tuple[int, int]:
        """Screen position of a frame image so that its box sits at the saved place or preset."""
        bx0, by0, bx1, by1 = frame.box
        bw, bh = bx1 - bx0, by1 - by0
        scope, e = self._entry(mon)
        preset = self.cfg["position_preset"]
        if self.overrides.get("position_preset") and scope != "profile":
            preset = self.overrides["position_preset"]
        margin = int(MARGIN * mon.scale)
        cx = mon.left + mon.width // 2
        if "x" in e and (scope == "profile" or preset == "custom"):
            cx = mon.left + e["x"]
            y = mon.top + e["y"] - (bh if e["anchor"] == "bottom" else 0)
        elif preset == "top":
            y = mon.top + margin
        elif preset == "center":
            y = mon.top + (mon.height - bh) // 2
        else:
            y = mon.bottom - margin - bh
        x = cx - bw // 2
        # keep the box inside the work area of that monitor (multi-monitor safe, survives unplugging)
        x = max(mon.left, min(x, mon.right - bw)) if bw <= mon.width else mon.left
        y = max(mon.top, min(y, mon.bottom - bh)) if bh <= mon.height else mon.top
        return x - bx0, y - by0

    @staticmethod
    def _anchored_pos(frame: render.Frame, anchor: dict[str, Any]) -> tuple[int, int]:
        bx0, by0, bx1, by1 = frame.box
        bw, bh = bx1 - bx0, by1 - by0
        if anchor["h"] == "left":
            x = anchor["x"]
        elif anchor["h"] == "right":
            x = anchor["x"] - bw
        else:
            x = anchor["x"] - bw // 2
        y = anchor["y"] if anchor["v"] == "top" else anchor["y"] - bh
        return x - bx0, y - by0

    def _motion(self) -> tuple[str, str, int, int]:
        a = self.template["animation"]
        enter, exit_, dur, exit_dur = a["enter"], a["exit"], a["duration_ms"], a["exit_duration_ms"]
        if a["reduced_motion"] == "respect" and render.reduced_motion():
            enter = "fade" if enter != "none" else "none"
            exit_ = "fade" if exit_ != "none" else "none"
            dur, exit_dur = min(dur, 120), min(exit_dur, 120)
        return enter, exit_, dur, exit_dur

    def _present(self, frame: render.Frame, animate: bool) -> None:
        mon = self.monitor()
        pos = self._frame_pos(frame, mon)
        old_img, old_pos = self._shown, self._pos
        self._frame = frame
        self._stop_karaoke()
        enter, exit_, dur, exit_dur = self._motion()
        if (animate and self.visible and self._fade is None and old_img is not None
                and (enter != "none" or exit_ != "none") and max(dur, exit_dur) > 0):
            s = self.renderer.scale(self.template, mon.screen_height, mon.scale, self.user_scale(mon))
            self._anim = {"t0": time.perf_counter(), "old": old_img, "old_pos": old_pos, "new": frame, "pos": pos,
                          "enter": enter, "exit": exit_, "dur": max(1, dur), "exit_dur": max(1, exit_dur),
                          "slide": self.template["animation"]["slide_px"] * s,
                          "ease": EASINGS.get(self.template["animation"]["easing"], EASINGS["ease_out"])}
            if self._anim_job is None:
                self._anim_step()
            return
        self._anim = None
        self._blit(self._current_image(), pos)
        self._start_karaoke()

    def _current_image(self) -> Image.Image:
        f = self._frame
        if f is None:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        return f.at(self._karaoke_progress()) if f.karaoke else f.image

    def _out_alpha(self) -> float:
        return self._alpha * float(self.cfg.get("overlay_opacity", 1.0))

    def _blit(self, img: Image.Image, pos: tuple[int, int], current: bool = True) -> None:
        if current:
            self._shown, self._pos = img, pos
        w, h = img.size
        try:
            self.win.geometry(f"{w}x{h}+{pos[0]}+{pos[1]}")
        except tk.TclError:
            return
        if self.visible and not self.desktop_hidden:
            self.surface.blit(img, pos[0], pos[1], self._out_alpha())
            if current:
                self._sync_backdrop()

    def _sync_backdrop(self) -> None:
        """Real blur under the box when the template asks for it (Liquid Glass, Glass); else none."""
        f = self._frame
        if f is None or not self.visible or self.desktop_hidden or not backdrop_mod.wanted(self.template):
            self.backdrop.hide()
            return
        c = self.template["container"]
        rect = backdrop_mod.box_rect(self._pos, f.box)
        self.backdrop.show(rect, int(round(c["corner_radius"] * self._scale)), c["background"]["color"])

    def set_capture_excluded(self, excluded: bool) -> None:
        """Leave the overlay (and its blur window) out of screen grabs while the screen source reads."""
        from dualyrics.screen import capture

        capture.set_capture_excluded(self.hwnd, excluded)
        self.backdrop.set_capture_excluded(excluded)

    # -- line change animation (crossfade old -> new with optional slide)
    def _anim_step(self) -> None:
        self._anim_job = None
        an = self._anim
        if an is None:
            return
        ms = (time.perf_counter() - an["t0"]) * 1000
        pe, pn = min(1.0, ms / an["exit_dur"]), min(1.0, ms / an["dur"])
        ease = an["ease"]
        if pe >= 1 and pn >= 1:
            self._anim = None
            self._blit(self._current_image(), an["pos"])
            self._start_karaoke()
            return
        old_a = 0.0 if an["exit"] == "none" else (1.0 - ease(pe) if "fade" in an["exit"] else
                                                   (1.0 if pe < 1 else 0.0))
        new_a = ease(pn) if "fade" in an["enter"] else 1.0
        slide = an["slide"]
        dy_new = int(round((1 - ease(pn)) * slide)) * (1 if "slide_up" in an["enter"] else
                                                       -1 if "slide_down" in an["enter"] else 0)
        dy_old = int(round(ease(pe) * slide)) * (-1 if "slide_up" in an["exit"] else
                                                 1 if "slide_down" in an["exit"] else 0)
        new_img = an["new"].at(self._karaoke_progress()) if an["new"].karaoke else an["new"].image
        old = an["old"]
        ox, oy = an["old_pos"][0], an["old_pos"][1] + dy_old
        nx, ny = an["pos"][0], an["pos"][1] + dy_new
        x0, y0 = min(ox, nx), min(oy, ny)
        x1, y1 = max(ox + old.width, nx + new_img.width), max(oy + old.height, ny + new_img.height)
        canvas = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        if old_a > 0.01:
            canvas.alpha_composite(_fade(old, old_a), (ox - x0, oy - y0))
        if new_a > 0.01:
            canvas.alpha_composite(_fade(new_img, new_a), (nx - x0, ny - y0))
        self._blit(canvas, (x0, y0), current=False)
        self._anim_job = self.win.after(FRAME_MS, self._anim_step)

    # -- karaoke sweep
    def _karaoke_progress(self) -> float:
        t0, t1 = self._kara_timing
        if t0 < 0 or t1 <= t0 or self.host is None:
            return -1.0
        try:
            pos = self.host.position()
        except Exception:
            return -1.0
        return max(0.0, min(1.0, (pos - t0) / (t1 - t0)))

    def _start_karaoke(self) -> None:
        if self._frame is not None and self._frame.karaoke and self._kara_job is None:
            self._kara_job = self.win.after(KARAOKE_MS, self._karaoke_step)

    def _stop_karaoke(self) -> None:
        if self._kara_job is not None:
            try:
                self.win.after_cancel(self._kara_job)
            except tk.TclError:
                pass
            self._kara_job = None

    def _karaoke_step(self) -> None:
        self._kara_job = None
        f = self._frame
        if f is None or not f.karaoke or self._anim is not None:
            return
        if self.visible and not self._drag.get("moved"):
            self._blit(f.at(self._karaoke_progress()), self._pos)
        self._kara_job = self.win.after(KARAOKE_MS, self._karaoke_step)

    # -- unsynced lyrics scrolling
    def _auto_scroll(self, progress: float) -> None:
        if progress < 0 or time.time() - self._user_scrolled_at < 8 or not self._plain_total:
            return
        view = int(self.cfg["plain_height"] * self.monitor().scale)
        target = int(max(0.0, progress * self._plain_total - view * 0.35))
        if abs(target - self._plain_scroll) > 2 and self._content is not None:
            self._plain_scroll = target
            self.render(self._content, force=True)

    # ------------------------------------------------------------------ placement upkeep
    def tick(self) -> None:
        """Called by the app every tick: follow monitor changes (unplug, resolution) and virtual desktops."""
        now = time.monotonic()
        if now - self._mon_at >= MONITOR_POLL:
            self._mon_at = now
            mons = winutil.monitors()
            sig = tuple((m.name, m.left, m.top, m.right, m.bottom, m.dpi) for m in mons)
            if sig != self._mon_sig:
                if self._mon_sig:
                    log.info("monitors changed: %s", [m.name for m in mons])
                self._mon_sig = sig
                self._sig = None
                if self._content is not None:
                    self.render(self._content, force=True)
        if now - self._vd_at >= DESKTOP_POLL and (self.visible or self.desktop_hidden):
            self._vd_at = now
            try:
                self._follow_desktops()
            except Exception:
                log.debug("virtual desktop check failed", exc_info=True)

    def _follow_desktops(self) -> None:
        vd = vdesk.manager()
        if not vd.available:
            return
        fg = vdesk.foreground_hwnd()
        from dualyrics import procs

        if not fg or procs._pid(fg) == procs.OWN_PID:
            return
        fg_desk = vd.desktop_of(fg)
        if self.cfg.get("virtual_desktops", "all") == "all":
            if self.desktop_hidden:
                self.desktop_hidden = False
                if self.visible:
                    self.win.deiconify()
                    self._blit(self._shown or self._current_image(), self._pos)
            if vd.on_current(self.hwnd) is False and fg_desk:
                vd.move(self.hwnd, fg_desk)
            return
        if not self._home_desktop:
            self._home_desktop = fg_desk
        hidden = bool(fg_desk and self._home_desktop and fg_desk != self._home_desktop)
        if hidden != self.desktop_hidden:
            self.desktop_hidden = hidden
            if hidden:
                self.win.withdraw()
                self.backdrop.hide()
            elif self.visible:
                self.win.deiconify()
                self._blit(self._shown or self._current_image(), self._pos)

    def reset_home_desktop(self) -> None:
        self._home_desktop = ""

    # ------------------------------------------------------------------ visibility
    def show(self) -> None:
        if self.visible and (self._fade is None or self._fade["to"] == 1.0):
            return
        if not self.visible:
            self.visible = True
            self._alpha = 0.0
            if not self.desktop_hidden:
                self.win.deiconify()
                self.win.attributes("-topmost", True)
            self._apply_window_styles()
            if self._frame is not None:
                self._blit(self._current_image(), self._frame_pos(self._frame, self.monitor()))
            self._start_karaoke()
        self._fade_to(1.0, self._motion()[2])

    def hide(self) -> None:
        if self.visible and not (self._fade and self._fade["to"] == 0.0):
            self.backdrop.hide()
            self._fade_to(0.0, self._motion()[3], then_hide=True)

    def _fade_to(self, target: float, ms: int, then_hide: bool = False) -> None:
        if self._fade_job is not None:
            try:
                self.win.after_cancel(self._fade_job)
            except tk.TclError:
                pass
            self._fade_job = None
        self._fade = {"from": self._alpha, "to": target, "t0": time.perf_counter(), "ms": max(1, ms),
                      "hide": then_hide}
        self._fade_step()

    def _fade_step(self) -> None:
        self._fade_job = None
        f = self._fade
        if f is None:
            return
        k = min(1.0, (time.perf_counter() - f["t0"]) * 1000 / f["ms"])
        self._alpha = f["from"] + (f["to"] - f["from"]) * k
        if self._shown is not None and self.visible and not self.desktop_hidden:
            self.surface.blit(self._shown, self._pos[0], self._pos[1], self._out_alpha())
        if k >= 1.0:
            self._fade = None
            if f["hide"]:
                self.visible = False
                self._stop_karaoke()
                self.win.withdraw()
                self.backdrop.hide()
            return
        self._fade_job = self.win.after(FRAME_MS, self._fade_step)

    # ------------------------------------------------------------------ mouse: move, resize, wheel
    def _set_cursor(self, cursor: str) -> None:
        try:
            self.area.configure(cursor=cursor)
        except tk.TclError:
            pass

    def _zone(self, x: int, y: int) -> str:
        """Which edge of the box the pointer is on ('' = inside / move). x, y are window coordinates."""
        if self._frame is None or self.cfg["locked"]:
            return ""
        bx0, by0, bx1, by1 = self._frame.box
        g = render.GRIP
        if not (bx0 - g <= x <= bx1 + g and by0 - g <= y <= by1 + g):
            return ""
        h = "l" if abs(x - bx0) <= g else ("r" if abs(x - bx1) <= g else "")
        v = "t" if abs(y - by0) <= g else ("b" if abs(y - by1) <= g else "")
        return v + h

    def _on_motion(self, e: tk.Event) -> None:
        if self._drag or self._resize:
            return
        cursor = {"l": "sb_h_double_arrow", "r": "sb_h_double_arrow", "t": "sb_v_double_arrow",
                  "b": "sb_v_double_arrow", "tl": "size_nw_se", "br": "size_nw_se", "tr": "size_ne_sw",
                  "bl": "size_ne_sw"}.get(self._zone(e.x, e.y), "fleur" if not self.cfg["locked"] else "")
        self._set_cursor(cursor)

    def _box_on_screen(self) -> tuple[int, int, int, int]:
        assert self._frame is not None
        bx0, by0, bx1, by1 = self._frame.box
        px, py = self._pos
        return px + bx0, py + by0, px + bx1, py + by1

    def _on_press(self, e: tk.Event) -> None:
        self._close_editor()
        if self._frame is None or self._anim is not None:
            return
        zone = self._zone(e.x, e.y)
        box = self._box_on_screen()
        if zone:
            self._resize = {"zone": zone, "x": e.x_root, "y": e.y_root, "box": box, "active": False,
                            "scale": self.user_scale(), "w": box[2] - box[0], "h": box[3] - box[1],
                            "anchor": None, "at": 0.0}
            return
        self._drag = {"dx": e.x_root - box[0], "dy": e.y_root - box[1], "box": box, "moved": False}

    def _on_drag(self, e: tk.Event) -> None:
        if self.cfg["locked"]:
            return
        if self._resize:
            self._do_resize(e)
            return
        d = self._drag
        if not d or self._frame is None or self._shown is None:
            return
        nx, ny = e.x_root - d["dx"], e.y_root - d["dy"]
        if not d["moved"] and abs(nx - d["box"][0]) + abs(ny - d["box"][1]) < 3:
            return
        d["moved"] = True
        bx0, by0 = self._frame.box[:2]
        self._blit(self._shown, (nx - bx0, ny - by0))

    def _do_resize(self, e: tk.Event) -> None:
        r = self._resize
        zone = r["zone"]
        dx, dy = e.x_root - r["x"], e.y_root - r["y"]
        x0, y0, x1, y1 = r["box"]
        mon = self.monitor()
        w = r["w"] - dx if "l" in zone else (r["w"] + dx if "r" in zone else r["w"])
        w = max(MIN_BOX_W, min(mon.width, w))
        scale = r["scale"]
        if "t" in zone or "b" in zone:
            h = r["h"] - dy if "t" in zone else r["h"] + dy
            scale = max(SCALE_MIN, min(SCALE_MAX, r["scale"] * max(20, h) / max(20, r["h"])))
        r["active"] = True
        r["new_w"], r["new_scale"] = int(w), round(scale, 3)
        r["anchor"] = {"h": "right" if "l" in zone else ("left" if "r" in zone else "center"),
                       "x": x1 if "l" in zone else (x0 if "r" in zone else (x0 + x1) // 2),
                       "v": "bottom" if "t" in zone else "top", "y": y1 if "t" in zone else y0}
        now = time.perf_counter()
        if now - r["at"] < 0.03:
            return
        r["at"] = now
        self._preview_size(r["new_w"], r["new_scale"])

    def _preview_size(self, w: int, scale: float) -> None:
        """Re-render at a size being dragged (saved when the mouse is released)."""
        c = self._content
        if c is None:
            return
        mon = self.monitor()
        s = self.renderer.scale(self.template, mon.screen_height, mon.scale, scale)
        self._scale = s
        hit = not self.cfg["click_through"]
        if c.mode == "plain":
            frame, _ = self.renderer.render_list(self.template, list(c.rows), c.status, s, w,
                                                 int(self.cfg["plain_height"] * mon.scale), self._plain_scroll,
                                                 show=self._show_flags(), hit=hit)
        else:
            lines = render.Lines(c.original, c.translation, c.next, c.speaker, c.status, c.tail)
            frame = self.renderer.render(self.template, lines, s, w, show=self._show_flags(), hit=hit)
        self._frame = frame
        self._sig = None
        self._blit(self._current_image(), self._anchored_pos(frame, self._resize["anchor"]))

    def _on_release(self, _e: tk.Event) -> None:
        if self._resize:
            r = self._resize
            if r.get("active"):
                # the preview is throttled: draw the final size first, so the saved place matches it
                self._preview_size(r["new_w"], r["new_scale"])
                self._resize = {}
                self._save_place(size=(r["new_w"], r["new_scale"]))
            self._resize = {}
            self._sig = None
            if self._content is not None:
                self.render(self._content, force=True)
            return
        moved = self._drag.get("moved")
        self._drag = {}
        if moved and not self.cfg["locked"]:
            self._save_place()

    def _save_place(self, size: tuple[int, float] | None = None) -> None:
        """Remember where the box is (center x, top or bottom edge) and its size, for this monitor and
        profile."""
        if self._frame is None:
            return
        x0, y0, x1, y1 = self._box_on_screen()
        w, h = x1 - x0, y1 - y0
        mon = winutil.monitor_at(x0 + w // 2, y0 + h // 2)
        bottom = (y0 + h // 2) > mon.top + mon.height // 2
        positions = dict(self.settings["positions"])
        key = f"{self.profile_id}@{mon.name}" if self.profile_id else mon.name
        entry = dict(positions.get(key, {}))
        entry.update(x=x0 + w // 2 - mon.left, y=(y1 - mon.top) if bottom else (y0 - mon.top),
                     anchor="bottom" if bottom else "top")
        if size is not None:
            entry["w"], entry["scale"] = int(size[0]), float(size[1])
        positions[key] = entry
        changes: dict[str, Any] = {"positions": positions}
        if not self.profile_id:
            changes["position_preset"] = "custom"
        if self.profile_id:
            # remember the monitor for this app too, or its own monitor setting would pull it back
            profs = self.settings["profiles"]
            for p in profs:
                if p.get("id") == self.profile_id:
                    p["monitor"] = mon.name
            changes["profiles"] = profs
            self.overrides["monitor"] = mon.name
        elif not self.cfg.get("follow_active_monitor"):
            changes["monitor"] = mon.name
            self.overrides.pop("monitor", None)
        # a dragged overlay stays where it was dropped, even while a profile suggests another preset
        self.overrides.pop("position_preset", None)
        self.settings.update(**changes)
        self.reset_home_desktop()

    def change_scale(self, factor: float | None) -> None:
        """Multiply the text size (None = back to 100 % and the template's width), remembered for this monitor
        and profile."""
        mon = self.monitor()
        positions = dict(self.settings["positions"])
        key = self._save_key(mon)
        cur = self.user_scale(mon)        # what is on screen now (profile size, else the monitor's)
        if factor is None:
            keys = {key, mon.name}        # back to 100 %: the monitor's own size must not show through
        else:
            keys = {key}
        for k in keys:
            entry = dict(positions.get(k, {}))
            if factor is None:
                entry.pop("scale", None)
                entry.pop("w", None)
            else:
                entry["scale"] = round(max(SCALE_MIN, min(SCALE_MAX, cur * factor)), 3)
            if entry:
                positions[k] = entry
            else:
                positions.pop(k, None)
        self.settings.update(positions=positions)

    def _on_wheel(self, e: tk.Event) -> None:
        step = 1 if e.delta > 0 else -1
        ctrl = bool(e.state & 0x0004)
        if self._content and self._content.mode == "plain" and not ctrl:
            self._user_scrolled_at = time.time()
            self._plain_scroll = max(0, min(self._plain_total,
                                            self._plain_scroll - int(step * 48 * self.monitor().scale)))
            self.render(self._content, force=True)
            return
        self.change_scale(1.06 if step > 0 else 1 / 1.06)

    # ------------------------------------------------------------------ right-click menu
    def _img(self, name: str, enabled: bool = True) -> Any:
        img = ImageTk.PhotoImage(icons.render(name, 16, "#E6EDF7" if enabled else MENU_DIM), master=self.win)
        self._menu_images.append(img)
        return img

    def _on_menu(self, e: tk.Event) -> None:
        self._close_editor()
        if self._menu is not None:  # one menu at a time; a new widget per right-click leaked
            try:
                self._menu.destroy()
            except tk.TclError:
                pass
        self._menu_images = []
        m = self._menu = self.build_menu()
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def build_menu(self) -> tk.Menu:
        info = self.host.menu_info() if self.host else {}
        c = self.cfg
        opts = dict(tearoff=0, bd=0, relief="flat", **MENU_COLORS)
        m = tk.Menu(self.win, **opts)

        def sub(label: str, icon: str) -> tk.Menu:
            s = tk.Menu(m, **opts)
            m.add_cascade(label=label, menu=s, image=self._img(icon), compound="left")
            return s

        def cmd(menu: tk.Menu, label: str, command: str | Callable[[], None], icon: str | None = None,
                accel: str = "", enabled: bool = True) -> None:
            fn = command if callable(command) else (lambda c=command: self.send(c))
            kw: dict[str, Any] = dict(label=label, command=fn, accelerator=_accel(accel))
            if not enabled:
                # Windows embosses disabled entries that have an image (double text): draw them dimmed
                # instead, and make them do nothing
                kw.update(command=lambda: None, foreground=MENU_DIM, activeforeground=MENU_DIM,
                          activebackground=MENU_COLORS["bg"])
            if icon:
                kw.update(image=self._img(icon, enabled), compound="left")
            menu.add_command(**kw)

        def check(menu: tk.Menu, label: str, value: bool, command: str, icon: str | None = None,
                  accel: str = "") -> None:
            var = tk.BooleanVar(menu, value=value)
            kw: dict[str, Any] = dict(label=label, variable=var, command=lambda: self.send(command),
                                      accelerator=_accel(accel))
            if icon:
                kw.update(image=self._img(icon), compound="left")
            menu.add_checkbutton(**kw)

        def radio(menu: tk.Menu, var: tk.StringVar, label: str, value: str, command: str,
                  enabled: bool = True) -> None:
            menu.add_radiobutton(label=label, variable=var, value=value, command=lambda: self.send(command),
                                 state="normal" if enabled else "disabled")

        live = info.get("mode") == "live"
        has_song = bool(info.get("song"))
        has_line = bool(info.get("line_original") or info.get("line_translation"))

        sm = sub(t("m_mode"), "mode")
        mode_var = tk.StringVar(sm, value=info.get("global_mode", c["mode"]) if c["visible"] else "off")
        for key in ("auto", "lyrics", "live"):
            radio(sm, mode_var, t(f"m_mode_{key}"), key, f"mode:{key}")
        radio(sm, mode_var, t("m_mode_off"), "off", "mode:off")
        if info.get("profile_name"):
            sm.add_separator()
            sm.add_command(label=t("m_profile_active", name=info["profile_name"]), state="disabled")

        ss = sub(t("m_show"), "eye")
        show_var = tk.StringVar(ss, value=("translation" if c["translation_only"] else
                                           "original" if not c["show_translation"] else "both"))
        radio(ss, show_var, t("m_show_both"), "both", "show:both")
        radio(ss, show_var, t("m_show_translation"), "translation", "show:translation")
        radio(ss, show_var, t("m_show_original"), "original", "show:original")
        ss.add_separator()
        nv = tk.BooleanVar(ss, value=bool(c["show_next"]))
        ss.add_checkbutton(label=t("show_next"), variable=nv, command=lambda: self.send("toggle_next"),
                           accelerator=t("acc_double_click"))

        st = sub(t("m_template"), "templates")
        tvar = tk.StringVar(st, value=c["template"])
        lang = "fa" if is_rtl() else "en"
        for doc in self.store.all():
            st.add_radiobutton(label=templates.display_name(doc, lang, t), variable=tvar, value=doc["id"],
                               command=lambda i=doc["id"]: self.send(f"template:{i}"))
        st.add_separator()
        st.add_command(label=t("m_edit_templates"), command=lambda: self.send("settings:templates"))

        sz = sub(t("m_text_size"), "text_size")
        cmd(sz, t("m_larger"), "size_up", "plus", t("acc_wheel_up"))
        cmd(sz, t("m_smaller"), "size_down", "minus", t("acc_wheel_down"))
        cmd(sz, t("m_size_reset", pct=int(round(self.user_scale() * 100))), "size_reset")

        so = sub(t("m_opacity"), "opacity")
        ovar = tk.StringVar(so, value=str(int(round(float(c.get("overlay_opacity", 1.0)) * 10)) * 10))
        for p in (100, 90, 80, 70, 60, 50, 40, 30):
            radio(so, ovar, f"{p}%", str(p), f"opacity:{p / 100:.2f}")

        sp = sub(t("m_position"), "position")
        pvar = tk.StringVar(sp, value=c["position_preset"])
        for preset in ("bottom", "top", "center"):
            radio(sp, pvar, t(f"pr_{preset}"), preset, f"preset_{preset}")
        sp.add_separator()
        mvar = tk.StringVar(sp, value=self.monitor().name)
        off = set(c.get("monitors_off") or [])
        for i, mon in enumerate(winutil.monitors(), start=1):
            label = t("m_monitor_n", n=i, w=(mon.screen[2] - mon.screen[0]) or mon.width, h=mon.screen_height)
            if mon.primary:
                label += f"  ({t('primary')})"
            radio(sp, mvar, label, mon.name, f"monitor:{mon.name}", enabled=mon.name not in off)
        sp.add_separator()
        fv = tk.BooleanVar(sp, value=bool(c.get("follow_active_monitor")))
        sp.add_checkbutton(label=t("follow_active_monitor"), variable=fv,
                           command=lambda: self.send("toggle_follow_monitor"))

        sl = sub(t("m_language"), "language")
        lvar = tk.StringVar(sl, value=c["target_language"])
        codes = [c["target_language"]] + [x for x in QUICK_LANGS if x != c["target_language"]]
        for code in codes:
            lg = langs.BY_CODE.get(code)
            if lg is not None:
                radio(sl, lvar, f"{lg.native}  ·  {lg.name}", code, f"lang:{code}")
        sl.add_separator()
        sl.add_command(label=t("m_more_languages"), command=lambda: self.send("settings:translation"))

        sy = sub(t("m_sync"), "sync")
        step = c["offset_step_ms"]
        sy.add_command(label=t("m_sync_song", ms=int(info.get("song_offset", 0))), state="disabled")
        cmd(sy, t("offset_earlier", ms=step), "offset_down", None, c.get("hotkey_offset_down", ""), has_song)
        cmd(sy, t("offset_later", ms=step), "offset_up", None, c.get("hotkey_offset_up", ""), has_song)
        cmd(sy, t("offset_reset"), "offset_reset", None, "", has_song)
        sy.add_separator()
        sy.add_command(label=t("m_sync_global", ms=int(c["offset_ms"])), state="disabled")
        cmd(sy, t("offset_earlier", ms=step), "global_offset_down")
        cmd(sy, t("offset_later", ms=step), "global_offset_up")
        cmd(sy, t("m_global_reset"), "global_offset_reset")

        m.add_separator()
        cmd(m, t("m_copy_line"), "copy_line", "copy", "", has_line)
        cmd(m, t("m_copy_lyrics"), "copy_lyrics", "copy", "", has_song and not live)
        cmd(m, t("m_edit_line"), self.open_editor, "edit", "", bool(info.get("editable")))
        cmd(m, t("retranslate"), "retranslate", "retranslate", "", has_song and not live)
        m.add_separator()
        check(m, t("lock_position"), bool(c["locked"]), "toggle_locked", "lock")
        check(m, t("click_through"), bool(c["click_through"]), "toggle_click_through", "click",
              c.get("hotkey_click_through", ""))
        cmd(m, t("m_hide_song"), "hide_song", "eye_off", "", has_song and not live)
        name = info.get("profile_name") or ""
        cmd(m, t("m_hide_app", name=name) if name else t("m_hide_app_none"), "hide_app", "eye_off", "",
            bool(info.get("profile_hideable")))
        m.add_separator()
        paused = bool(info.get("live_paused"))
        cmd(m, t("lv_resume") if paused else t("lv_pause"), "toggle_live_pause", "play" if paused else "pause",
            c.get("hotkey_live_pause", ""), bool(info.get("live_available")))
        cmd(m, t("m_save_transcript"), "save_transcript", "save", "", bool(info.get("live_has_text")))
        check(m, t("dub_toggle"), bool(c.get("dub_enabled")), "toggle_dub", "dubbing", c.get("hotkey_dub", ""))
        m.add_separator()
        cmd(m, t("settings"), "settings", "settings")
        cmd(m, t("hide"), "toggle_visible", "eye_off", c.get("hotkey_toggle", ""))
        cmd(m, t("quit"), "quit", "quit")
        return m

    # ------------------------------------------------------------------ inline translation editor
    def open_editor(self) -> None:
        info = self.host.menu_info() if self.host else {}
        if not info.get("editable"):
            return
        self._close_editor()
        idx = int(info.get("line_index", -1))
        top = tk.Toplevel(self.win)
        self._editor = top
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg="#22D3EE")
        body = tk.Frame(top, bg="#141C2C", padx=12, pady=10)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        rtl_target = langs.is_rtl_language(self.cfg["target_language"])
        tk.Label(body, text=info.get("line_original", ""), bg="#141C2C", fg="#A9B6C9", font=("Segoe UI", 10),
                 anchor="w", justify="left", wraplength=560).pack(fill="x")
        var = tk.StringVar(top, value=info.get("line_translation", ""))
        fam = "Vazirmatn" if rtl_target else "Segoe UI"
        entry = tk.Entry(body, textvariable=var, font=(fam, 14), bg="#1A2436", fg="#E6EDF7",
                         insertbackground="#E6EDF7", relief="flat", justify="right" if rtl_target else "left",
                         highlightthickness=2, highlightcolor="#67E8F9", highlightbackground="#64748B")
        entry.pack(fill="x", pady=(6, 6), ipady=4)
        tk.Label(body, text=t("m_edit_hint"), bg="#141C2C", fg="#8E9DB3", font=("Segoe UI", 9),
                 anchor="w").pack(fill="x")

        def save(_e: Any = None) -> None:
            text = var.get().strip()
            self._close_editor()
            if self.host is not None and text:
                self.host.edit_translation(idx, text)

        entry.bind("<Return>", save)
        entry.bind("<Escape>", lambda _e: self._close_editor())
        top.update_idletasks()
        bx0, by0, bx1, by1 = self._box_on_screen() if self._frame else (0, 0, 600, 80)
        w = max(440, min(900, bx1 - bx0))
        h = top.winfo_reqheight()
        mon = self.monitor()
        x = max(mon.left, min((bx0 + bx1) // 2 - w // 2, mon.right - w))
        y = by0 - h - 8 if by0 - h - 8 >= mon.top else by1 + 8
        top.geometry(f"{w}x{h}+{x}+{y}")
        top.after(30, lambda: (top.focus_force(), entry.focus_set(), entry.select_range(0, "end")))

    def _close_editor(self) -> None:
        if self._editor is not None:
            try:
                self._editor.destroy()
            except tk.TclError:
                pass
            self._editor = None

    def close(self) -> None:
        self._stop_karaoke()
        self.backdrop.destroy()
        self.surface.close()


def _fade(img: Image.Image, k: float) -> Image.Image:
    if k >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point([int(v * k + 0.5) for v in range(256)]))
    return out


def _accel(combo: str) -> str:
    """'ctrl+alt+up' -> 'Ctrl+Alt+Up' for the menu."""
    if not combo or "+" not in combo:
        return combo or ""
    return "+".join(p[:1].upper() + p[1:] for p in combo.split("+"))
