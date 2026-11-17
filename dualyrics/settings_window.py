"""Settings window (customtkinter, dark, tabs). Every change is applied live."""

from __future__ import annotations

import logging
import os
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser
from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk

from dualyrics import __version__, langs, paths, settings as settings_mod
from dualyrics.i18n import is_rtl, t

if TYPE_CHECKING:
    from dualyrics.app import App

log = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
try:
    ctk.ThemeManager.theme["CTkFont"]["family"] = "Segoe UI"  # has Persian/Arabic glyphs, matches Windows
except Exception:
    pass

ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6A4BEB"
MUTED = "#9AA0AA"


class SettingsWindow:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.s = app.settings
        self.win: ctk.CTkToplevel | None = None
        self._setters: dict[str, Callable[[Any], None]] = {}
        self._flushers: list[Callable[[], Any]] = []  # save text fields that apply on focus-out
        self._fonts: list[str] = []

    # ------------------------------------------------------------------ lifecycle
    def open(self, tab: str | None = None) -> None:
        if self.win is not None and self.win.winfo_exists():
            self._bring_to_front()
            if tab:
                self.tabs.set(t(tab))
            return
        self._build()
        if tab:
            self.tabs.set(t(tab))

    def close(self) -> None:
        for fn in self._flushers:
            try:
                fn()
            except Exception:
                log.debug("flush failed", exc_info=True)
        self._flushers = []
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None
        self._setters = {}

    def rebuild(self) -> None:
        """Re-create the window (after the interface language changed)."""
        if self.win is None or not self.win.winfo_exists():
            return
        current = self.tabs.get()
        geo = self.win.geometry()
        self.close()
        self._build()
        self.win.geometry(geo)
        names = [t(k) for k in ("tab_general", "tab_translation", "tab_appearance", "tab_sync", "tab_network",
                                "tab_about")]
        self.tabs.set(names[0] if current not in names else current)

    def on_settings_changed(self, keys: set[str]) -> None:
        if self.win is None or not self.win.winfo_exists():
            return
        if "ui_language" in keys:
            self.rebuild()
            return
        for k in keys:
            fn = self._setters.get(k)
            if fn:
                try:
                    fn(self.s[k])
                except Exception:
                    log.debug("setter %s failed", k, exc_info=True)

    # ------------------------------------------------------------------ helpers
    def _row(self, parent: Any, label: str, widget: Any, hint: str = "") -> None:
        r = parent.grid_size()[1]
        rtl = is_rtl()
        lbl = ctk.CTkLabel(parent, text=label, anchor="e" if rtl else "w", justify="right" if rtl else "left")
        lbl.grid(row=r, column=1 if rtl else 0, sticky="e" if rtl else "w", padx=(12, 12), pady=(8, 0))
        widget.grid(row=r, column=0 if rtl else 1, sticky="w" if rtl else "e", padx=12, pady=(8, 0))
        if hint:
            h = ctk.CTkLabel(parent, text=hint, text_color=MUTED, font=ctk.CTkFont(size=11), wraplength=520,
                             anchor="e" if rtl else "w", justify="right" if rtl else "left")
            h.grid(row=r + 1, column=0, columnspan=2, sticky="ew", padx=12)

    def _full(self, parent: Any, widget: Any, pady: tuple[int, int] = (8, 0)) -> None:
        r = parent.grid_size()[1]
        widget.grid(row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=pady)

    def _heading(self, parent: Any, text: str) -> None:
        lbl = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=14, weight="bold"),
                           anchor="e" if is_rtl() else "w")
        self._full(parent, lbl, pady=(16, 0))

    def _switch(self, parent: Any, key: str, label: str, hint: str = "",
                on_change: Callable[[bool], None] | None = None) -> None:
        var = tk.BooleanVar(value=self.s[key])

        def changed() -> None:
            if on_change:
                on_change(var.get())
            else:
                self.s.update(**{key: var.get()})

        sw = ctk.CTkSwitch(parent, text="", variable=var, command=changed, progress_color=ACCENT, width=50)
        self._row(parent, label, sw, hint)
        self._setters[key] = lambda v: var.set(bool(v))

    def _option(self, parent: Any, key: str, label: str, options: list[tuple[str, str]], width: int = 260) -> None:
        """Dropdown of (value, label) pairs."""
        to_label = dict(options)
        to_value = {lbl: val for val, lbl in options}
        var = tk.StringVar(value=to_label.get(self.s[key], options[0][1]))

        def chosen(lbl: str) -> None:
            self.s.update(**{key: to_value[lbl]})
            # validation can refuse a choice (e.g. "custom" proxy without an address): show what is in effect
            var.set(to_label.get(self.s[key], lbl))

        om = ctk.CTkOptionMenu(parent, values=[lbl for _, lbl in options], variable=var, width=width,
                               fg_color="#2B2D31", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                               dynamic_resizing=False, command=chosen)
        self._row(parent, label, om)
        self._setters[key] = lambda v: var.set(to_label.get(v, var.get()))

    def _slider(self, parent: Any, key: str, label: str, lo: float, hi: float, steps: int,
                fmt: Callable[[float], str] = lambda v: str(int(v)), integer: bool = True) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        val = ctk.CTkLabel(frame, text=fmt(self.s[key]), width=48)

        def moved(v: float) -> None:
            v = int(round(v)) if integer else round(v, 2)
            val.configure(text=fmt(v))
            self.s.update(**{key: v})

        sl = ctk.CTkSlider(frame, from_=lo, to=hi, number_of_steps=steps, command=moved, width=220,
                           button_color=ACCENT, button_hover_color=ACCENT_HOVER, progress_color=ACCENT)
        sl.set(self.s[key])
        sl.pack(side="left")
        val.pack(side="left", padx=(8, 0))
        self._row(parent, label, frame)

        def setter(v: Any) -> None:
            sl.set(v)
            val.configure(text=fmt(v))

        self._setters[key] = setter

    def _color(self, parent: Any, key: str, label: str) -> None:
        def pick() -> None:
            res = colorchooser.askcolor(color=self.s[key], parent=self.win, title=label)
            if res and res[1]:
                self.s.update(**{key: res[1].upper()})

        btn = ctk.CTkButton(parent, text=self.s[key], width=110, fg_color=self.s[key], command=pick,
                            border_width=1, border_color="#555", text_color=_contrast(self.s[key]))
        self._row(parent, label, btn)
        self._setters[key] = lambda v: btn.configure(text=v, fg_color=v, hover_color=v, text_color=_contrast(v))

    def _textbox(self, parent: Any, value: str, height: int = 90) -> ctk.CTkTextbox:
        tb = ctk.CTkTextbox(parent, height=height, wrap="word", fg_color="#1E1F22", border_width=1,
                            border_color="#3A3B40")
        tb.insert("1.0", value)
        self._full(parent, tb)
        return tb

    def _button(self, parent: Any, text: str, cmd: Callable[[], None], width: int = 120, accent: bool = False) -> Any:
        return ctk.CTkButton(parent, text=text, command=cmd, width=width,
                             fg_color=ACCENT if accent else "#3A3B40",
                             hover_color=ACCENT_HOVER if accent else "#4A4B50")

    def _tab(self, name: str) -> ctk.CTkScrollableFrame:
        tab = self.tabs.add(t(name))
        frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0 if is_rtl() else 1, weight=1)
        return frame

    def _run_bg(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        """Run ``work`` in a thread and deliver its result to ``done`` on the Tk thread."""
        def runner() -> None:
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                res = e
            self.app.call_soon(lambda: done(res) if self.win is not None and self.win.winfo_exists() else None)

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self._setters = {}
        self._flushers = []
        win = ctk.CTkToplevel(self.app.root)
        self.win = win
        win.title(t("win_title"))
        try:
            win.iconbitmap(str(paths.ICON_ICO))
        except tk.TclError:
            pass
        win.geometry("700x680")
        win.minsize(560, 480)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.tabs = ctk.CTkTabview(win, segmented_button_selected_color=ACCENT,
                                   segmented_button_selected_hover_color=ACCENT_HOVER)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(4, 0))
        self._build_general(self._tab("tab_general"))
        self._build_translation(self._tab("tab_translation"))
        self._build_appearance(self._tab("tab_appearance"))
        self._build_sync(self._tab("tab_sync"))
        self._build_network(self._tab("tab_network"))
        self._build_about(self._tab("tab_about"))
        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=8)
        self._button(bottom, t("close"), self.close, accent=True).pack(side="left" if is_rtl() else "right")
        win.after(60, lambda: self._bring_to_front(center=True))

    def _bring_to_front(self, center: bool = False) -> None:
        """Center on the primary monitor and raise above other apps (focus_force alone is not
        enough when another process owns the foreground)."""
        win = self.win
        if win is None or not win.winfo_exists():
            return
        if center:
            from dualyrics import winutil

            win.update_idletasks()
            mon = winutil.primary_monitor()
            w, h = win.winfo_width(), win.winfo_height()
            x = mon.left + max(0, (mon.width - w) // 2)
            y = mon.top + max(0, (mon.height - h) // 2)
            tk.Toplevel.wm_geometry(win, f"+{x}+{y}")  # physical pixels, bypasses ctk scaling
        win.deiconify()
        win.attributes("-topmost", True)
        win.lift()
        win.focus_force()
        win.after(400, lambda: win.winfo_exists() and win.attributes("-topmost", False))

    def _build_general(self, f: Any) -> None:
        self._option(f, "target_language", t("target_language"),
                     [(lang.code, f"{lang.name} — {lang.native}") for lang in langs.LANGUAGES], width=300)
        self._option(f, "ui_language", t("ui_language"), [("en", "English"), ("fa", "فارسی (Persian)")])
        self._switch(f, "start_with_windows", t("start_with_windows"),
                     on_change=lambda v: self.app.set_startup(v))
        self._switch(f, "hide_when_idle", t("hide_when_idle"))
        self._heading(f, t("preferred_players"))
        entry = ctk.CTkEntry(f)
        entry.insert(0, ", ".join(self.s["preferred_players"]))
        self._full(f, entry)
        hint = ctk.CTkLabel(f, text=t("players_hint"), text_color=MUTED, font=ctk.CTkFont(size=11),
                            anchor="e" if is_rtl() else "w")
        self._full(f, hint, pady=(2, 0))

        def save_players(_e: Any = None) -> None:
            self.s.update(preferred_players=entry.get())

        entry.bind("<FocusOut>", save_players)
        entry.bind("<Return>", save_players)
        self._flushers.append(save_players)

        def set_players(v: list[str]) -> None:
            entry.delete(0, "end")
            entry.insert(0, ", ".join(v))

        self._setters["preferred_players"] = set_players

    def _build_translation(self, f: Any) -> None:
        self._option(f, "provider", t("provider"), [("gemini", t("prov_gemini")), ("free", t("prov_free"))],
                     width=340)
        self._heading(f, t("gemini_keys"))
        keys = self._textbox(f, "\n".join(self.s["gemini_keys"]), height=70)
        hint = ctk.CTkLabel(f, text=t("gemini_keys_hint"), text_color=MUTED, font=ctk.CTkFont(size=11),
                            wraplength=560, anchor="e" if is_rtl() else "w", justify="right" if is_rtl() else "left")
        self._full(f, hint, pady=(2, 0))
        self._heading(f, t("gemini_models"))
        models = self._textbox(f, "\n".join(self.s["gemini_models"]), height=90)

        def save_keys(_e: Any = None) -> None:
            self.s.update(gemini_keys=[k.strip() for k in keys.get("1.0", "end").split() if k.strip()])

        def save_models(_e: Any = None) -> None:
            self.s.update(gemini_models=models.get("1.0", "end"))

        keys.bind("<FocusOut>", save_keys)
        models.bind("<FocusOut>", save_models)
        self._flushers += [save_keys, save_models]

        def set_models(v: list[str]) -> None:
            models.delete("1.0", "end")
            models.insert("1.0", "\n".join(v))

        self._setters["gemini_models"] = set_models

        row = ctk.CTkFrame(f, fg_color="transparent")
        result = ctk.CTkLabel(f, text="", text_color=MUTED, justify="left", anchor="w", wraplength=560)

        def test() -> None:
            save_keys()
            save_models()
            ks, ms = self.s["gemini_keys"], self.s["gemini_models"]
            if not ks:
                result.configure(text=t("gemini_keys"))
                return
            result.configure(text=t("testing"))
            self._run_bg(lambda: self.app.translator.test_gemini(ks, ms), show)

        def show(res: Any) -> None:
            if isinstance(res, Exception):
                result.configure(text=str(res))
                return
            result.configure(text="\n".join(f"{'✔' if ok else '✘'} {k}  {m}: {msg}" for k, m, ok, msg in res))

        self._button(row, t("test"), test, accent=True).pack(side="left")
        self._button(row, t("reset_models"),
                     lambda: self.s.update(gemini_models=list(settings_mod.DEFAULT_MODELS)),
                     width=140).pack(side="left", padx=8)
        self._full(f, row)
        self._full(f, result, pady=(4, 0))
        self._switch(f, "fallback_free", t("fallback_free"))
        self._switch(f, "skip_same_language", t("skip_same"))

        self._heading(f, t("style"))
        style = self._textbox(f, self.s["style"], height=110)
        def save_style(_e: Any = None) -> None:
            self.s.update(style=style.get("1.0", "end").strip())

        style.bind("<FocusOut>", save_style)
        self._flushers.append(save_style)

        def set_style(v: str) -> None:
            style.delete("1.0", "end")
            style.insert("1.0", v)

        self._setters["style"] = set_style
        row2 = ctk.CTkFrame(f, fg_color="transparent")
        status = ctk.CTkLabel(row2, text="", text_color=MUTED)
        self._button(row2, t("reset_style"), lambda: self.s.update(style=settings_mod.DEFAULT_STYLE),
                     width=140).pack(side="left")

        def clear() -> None:
            self.app.clear_cache()
            status.configure(text=t("cache_cleared"))

        self._button(row2, t("clear_cache"), clear, width=140).pack(side="left", padx=8)
        status.pack(side="left", padx=8)
        self._full(f, row2, pady=(8, 12))

    def _font_combo(self, f: Any, key: str, label: str) -> None:
        if not self._fonts:
            fams = sorted({x for x in tkfont.families(self.app.root) if not x.startswith("@")}, key=str.lower)
            self._fonts = ["auto"] + fams
        var = tk.StringVar(value=self.s[key])
        cb = ctk.CTkComboBox(f, values=self._fonts, variable=var, width=260,
                             command=lambda v: self.s.update(**{key: v}), button_color=ACCENT,
                             button_hover_color=ACCENT_HOVER)
        cb.bind("<Return>", lambda _e: self.s.update(**{key: var.get()}))
        cb.bind("<FocusOut>", lambda _e: self.s.update(**{key: var.get()}))
        self._row(f, label, cb)
        self._setters[key] = lambda v: var.set(v)

    def _build_appearance(self, f: Any) -> None:
        self._heading(f, t("font_original"))
        self._font_combo(f, "font_original", t("font_original"))
        self._slider(f, "size_original", t("size"), 8, 72, 64)
        self._switch(f, "bold_original", t("bold"))
        self._color(f, "color_original", t("color_original"))
        self._switch(f, "show_original", t("show_original"))

        self._heading(f, t("font_translation"))
        self._font_combo(f, "font_translation", t("font_translation"))
        self._slider(f, "size_translation", t("size"), 8, 72, 64)
        self._switch(f, "bold_translation", t("bold"))
        self._color(f, "color_translation", t("color_translation"))
        self._switch(f, "show_translation", t("show_translation"))

        self._heading(f, t("tab_appearance"))
        self._switch(f, "show_next", t("show_next"))
        self._color(f, "color_next", t("color_next"))
        self._color(f, "color_bg", t("color_bg"))
        self._slider(f, "opacity", t("opacity"), 0.2, 1.0, 80, fmt=lambda v: f"{int(round(v * 100))}%",
                     integer=False)
        self._switch(f, "text_only", t("text_only"))
        self._switch(f, "outline", t("outline"))
        self._color(f, "outline_color", t("outline_color"))
        self._slider(f, "outline_width", t("outline_width"), 0, 4, 4)
        self._switch(f, "shadow", t("shadow"))
        self._switch(f, "rounded", t("rounded"))
        self._slider(f, "width", t("width"), 300, 2400, 210)
        self._slider(f, "plain_height", t("plain_height"), 120, 1200, 108)
        self._option(f, "align", t("align"), [(a, t(f"al_{a}")) for a in settings_mod.ALIGNMENTS])
        self._option(f, "position_preset", t("preset"), [(p, t(f"pr_{p}")) for p in settings_mod.PRESETS])
        self._switch(f, "locked", t("lock_position"))
        self._switch(f, "click_through", t("click_through"), hint=t("click_through_hint"))
        reset_keys = [k for k in settings_mod.defaults() if k.startswith(("font_", "size_", "bold_", "color_",
                                                                          "show_", "outline"))] + [
            "opacity", "text_only", "shadow", "width", "align", "rounded", "plain_height", "position_preset"]
        self._full(f, self._button(f, t("reset_appearance"), lambda: self.s.reset(reset_keys), width=180),
                   pady=(14, 12))

    def _build_sync(self, f: Any) -> None:
        self._heading(f, t("global_offset"))
        row = ctk.CTkFrame(f, fg_color="transparent")
        glob = ctk.CTkEntry(row, width=90, justify="center")
        glob.insert(0, str(self.s["offset_ms"]))

        def set_global(v: int) -> None:
            glob.delete(0, "end")
            glob.insert(0, str(v))

        def apply_global(_e: Any = None) -> None:
            try:
                self.s.update(offset_ms=int(float(glob.get().strip() or 0)))
            except ValueError:
                set_global(self.s["offset_ms"])

        def bump_global(sign: int) -> None:
            self.s.update(offset_ms=self.s["offset_ms"] + sign * self.s["offset_step_ms"])

        self._button(row, "−", lambda: bump_global(-1), width=40).pack(side="left")
        glob.pack(side="left", padx=6)
        self._button(row, "+", lambda: bump_global(1), width=40).pack(side="left")
        self._button(row, t("reset"), lambda: self.s.update(offset_ms=0), width=70).pack(side="left", padx=8)
        glob.bind("<Return>", apply_global)
        glob.bind("<FocusOut>", apply_global)
        self._flushers.append(apply_global)
        self._setters["offset_ms"] = set_global
        self._full(f, row)

        self._heading(f, t("song_offset"))
        song_lbl = ctk.CTkLabel(f, text="", text_color=MUTED, anchor="e" if is_rtl() else "w")
        self._full(f, song_lbl, pady=(2, 0))
        row2 = ctk.CTkFrame(f, fg_color="transparent")
        song_val = ctk.CTkLabel(row2, text="0", width=90)
        self._button(row2, "−", lambda: self.app.command("offset_down"), width=40).pack(side="left")
        song_val.pack(side="left", padx=6)
        self._button(row2, "+", lambda: self.app.command("offset_up"), width=40).pack(side="left")
        self._button(row2, t("reset"), lambda: self.app.command("offset_reset"), width=70).pack(side="left", padx=8)
        self._full(f, row2)

        def refresh_song() -> None:
            if self.win is None or not song_lbl.winfo_exists():
                return
            label, ms = self.app.song_offset_info()
            song_lbl.configure(text=label or t("no_song"))
            song_val.configure(text=f"{ms:+d} ms")
            song_lbl.after(700, refresh_song)

        refresh_song()
        self._slider(f, "offset_step_ms", t("offset_step"), 50, 1000, 19)

        self._heading(f, t("hotkeys"))
        self._switch(f, "hotkeys_enabled", t("hotkeys"))
        entries: dict[str, ctk.CTkEntry] = {}
        for key, label in (("hotkey_offset_up", t("hk_up")), ("hotkey_offset_down", t("hk_down")),
                           ("hotkey_toggle", t("hk_toggle"))):
            e = ctk.CTkEntry(f, width=200)
            e.insert(0, self.s[key])
            entries[key] = e
            self._row(f, label, e)

            def setter(v: str, e: ctk.CTkEntry = e) -> None:
                e.delete(0, "end")
                e.insert(0, v)

            self._setters[key] = setter
        hk_status = ctk.CTkLabel(f, text=t("hk_hint"), text_color=MUTED, anchor="e" if is_rtl() else "w")

        def apply_hotkeys() -> None:
            self.s.update(**{k: e.get() for k, e in entries.items()})
            for k, e in entries.items():
                if e.get().strip().lower().replace(" ", "") != self.s[k]:
                    e.delete(0, "end")
                    e.insert(0, self.s[k])
            failed = self.app.restart_hotkeys()
            hk_status.configure(text=t("hk_failed", keys=", ".join(failed)) if failed else t("hk_ok"))

        self._full(f, self._button(f, t("apply"), apply_hotkeys, accent=True))
        self._full(f, hk_status, pady=(4, 12))

    def _build_network(self, f: Any) -> None:
        self._option(f, "proxy_mode", t("proxy_mode"),
                     [("none", t("px_none")), ("system", t("px_system")), ("custom", t("px_custom"))])
        self._heading(f, t("proxy_url"))
        url = ctk.CTkEntry(f, placeholder_text="http://127.0.0.1:8080")
        if self.s["proxy_url"]:
            url.insert(0, self.s["proxy_url"])
        self._full(f, url)
        status = ctk.CTkLabel(f, text="", text_color=MUTED, anchor="e" if is_rtl() else "w", wraplength=560)

        def save_url(_e: Any = None) -> bool:
            v = url.get().strip()
            if v and not settings_mod.valid_proxy_url(v):
                status.configure(text=t("proxy_bad"))
                return False
            changes: dict[str, Any] = {"proxy_url": v}
            if v and self.s["proxy_mode"] != "custom":
                changes["proxy_mode"] = "custom"
            self.s.update(**changes)
            status.configure(text="")
            return True

        url.bind("<Return>", save_url)
        url.bind("<FocusOut>", save_url)
        self._flushers.append(save_url)

        def test() -> None:
            if not save_url():
                return
            status.configure(text=t("testing"))

            def work() -> int:
                t0 = time.perf_counter()
                r = self.app.session().get("https://lrclib.net/api/search", params={"q": "hello adele"},
                                           timeout=(6, 15))
                r.raise_for_status()
                return int((time.perf_counter() - t0) * 1000)

            self._run_bg(work, lambda res: status.configure(
                text=t("conn_fail", err=str(res)[:200]) if isinstance(res, Exception) else t("conn_ok", ms=res)))

        self._full(f, self._button(f, t("test_connection"), test, width=160, accent=True), pady=(10, 0))
        self._full(f, status, pady=(6, 12))

    def _build_about(self, f: Any) -> None:
        logo = None
        try:
            from PIL import Image

            logo = ctk.CTkImage(Image.open(paths.ICON_PNG), size=(96, 96))
        except Exception:
            pass
        if logo:
            self._full(f, ctk.CTkLabel(f, text="", image=logo), pady=(16, 0))
        title = ctk.CTkLabel(f, text="Dualyrics", font=ctk.CTkFont(size=24, weight="bold"))
        self._full(f, title, pady=(6, 0))
        body = ctk.CTkLabel(f, text=t("about_text", v=__version__, path=str(paths.data_dir())), wraplength=560,
                            justify="right" if is_rtl() else "left")
        self._full(f, body, pady=(10, 0))
        from dualyrics import REPO_URL

        if REPO_URL:
            link = ctk.CTkLabel(f, text=REPO_URL, text_color=ACCENT, cursor="hand2")
            link.bind("<Button-1>", lambda _e: os.startfile(REPO_URL))
            self._full(f, link, pady=(6, 0))
        row = ctk.CTkFrame(f, fg_color="transparent")
        self._button(row, t("open_data"), lambda: os.startfile(paths.data_dir()), width=160).pack(side="left")
        self._button(row, t("open_log"), lambda: os.startfile(paths.log_dir() / "dualyrics.log"),
                     width=120).pack(side="left", padx=8)
        self._full(f, row, pady=(14, 12))


def _contrast(hex_color: str) -> str:
    try:
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"
    except (ValueError, IndexError):
        return "#FFFFFF"
