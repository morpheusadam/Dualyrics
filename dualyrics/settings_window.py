"""Settings window (customtkinter): sidebar sections, design tokens from docs/DESIGN.md, live preview for
templates. Every change is applied live and saved automatically. The layout mirrors for Persian."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk

from dualyrics import __version__, langs, paths, settings as settings_mod, templates, winutil
from dualyrics.i18n import is_rtl, language, t
from dualyrics.ui import tokens as T
from dualyrics.ui.widgets import Kit, Toast, focus_ring

if TYPE_CHECKING:
    from dualyrics.app import App

log = logging.getLogger(__name__)

SECTIONS = (("general", "general"), ("profiles", "profiles"), ("templates", "templates"), ("lyrics", "lyrics"),
            ("live", "live"), ("translation", "translation"), ("monitors", "monitors"), ("sync", "sync"),
            ("network", "network"), ("about", "about"))
ALIASES = {"tab_general": "general", "tab_apps": "profiles", "tab_live": "live", "tab_translation": "translation",
           "tab_appearance": "templates", "tab_sync": "sync", "tab_network": "network", "tab_about": "about"}


def _init_theme(mode: str) -> None:
    try:
        ctk.set_default_color_theme(str(T.theme_path()))
    except Exception:
        ctk.set_default_color_theme("blue")
    ctk.set_appearance_mode({"system": "system", "light": "light"}.get(mode, "dark"))


def _has_nvidia() -> bool:
    """An NVIDIA driver is installed (nvcuda.dll), so the GPU option of the installer makes sense."""
    return os.path.exists(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvcuda.dll"))


class SettingsWindow:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.s = app.settings
        self.win: ctk.CTkToplevel | None = None
        self.kit: Kit | None = None
        self.section = self.s["settings_section"]
        self._setters: dict[str, list[Callable[[], None]]] = {}
        self._flushers: list[Callable[[], Any]] = []
        self._nav: dict[str, dict[str, Any]] = {}
        self._rail = False
        self._tpl_page: Any = None
        self._toast: Toast | None = None
        self._resize_job: str | None = None
        _init_theme(self.s["ui_theme"])

    # ------------------------------------------------------------------ lifecycle
    def open(self, section: str | None = None) -> None:
        section = ALIASES.get(section or "", section)
        if self.win is not None and self.win.winfo_exists():
            self._bring_to_front()
            if section:
                self.select(section)
            return
        if section:
            self.section = section
        self._build()

    def close(self) -> None:
        self._flush()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None
        self._setters = {}
        self._tpl_page = None

    def _flush(self) -> None:
        for fn in self._flushers:
            try:
                fn()
            except Exception:
                log.debug("flush failed", exc_info=True)
        self._flushers = []
        if self._tpl_page is not None:
            try:
                self._tpl_page.flush()
            except Exception:
                log.debug("template flush failed", exc_info=True)

    def rebuild(self) -> None:
        """Re-create the window (after the interface language changed)."""
        if self.win is None or not self.win.winfo_exists():
            return
        geo = self.win.geometry()
        self.close()
        self._build()
        try:
            self.win.geometry(geo)
        except tk.TclError:
            pass

    def on_settings_changed(self, keys: set[str]) -> None:
        if self.win is None or not self.win.winfo_exists():
            return
        if "ui_language" in keys:
            self.rebuild()
            return
        if "ui_theme" in keys:
            _init_theme(self.s["ui_theme"])
        if "template" in keys and self._tpl_page is not None:
            self._tpl_page.on_template_setting()
        for k in keys:
            for fn in self._setters.get(k, []):
                try:
                    fn()
                except Exception:
                    log.debug("setter %s failed", k, exc_info=True)
        if keys - {"positions", "settings_section", "preview_backdrop"}:
            self._saved()

    # ------------------------------------------------------------------ helpers
    def bind_key(self, key: str) -> tuple[Callable[[], Any], Callable[[Any], None]]:
        return (lambda: self.s[key]), (lambda v: self.s.update(**{key: v}))

    def watch(self, key: str, fn: Callable[[], None]) -> None:
        self._setters.setdefault(key, []).append(fn)

    def toast(self, text: str, kind: str = "info", action: tuple[str, Callable[[], None]] | None = None) -> None:
        if self._toast is not None:
            self._toast.show(text, kind, action)

    def effective_target(self) -> str:
        return self.app.effective_cfg()["target_language"]

    def _run_bg(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        """Run ``work`` in a thread and deliver its result to ``done`` on the Tk thread."""
        def runner() -> None:
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                res = e
            self.app.call_soon(lambda: done(res) if self.win is not None and self.win.winfo_exists() else None)

        threading.Thread(target=runner, daemon=True).start()

    def _saved(self) -> None:
        lbl = getattr(self, "_saved_lbl", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=t("saved_auto"))

    # ------------------------------------------------------------------ window
    def _build(self) -> None:
        self._setters = {}
        self._flushers = []
        _init_theme(self.s["ui_theme"])
        win = ctk.CTkToplevel(self.app.root)
        self.win = win
        self.kit = k = Kit(win)
        win.title(t("win_title"))
        win.configure(fg_color=T.BG_APP)
        try:
            win.iconbitmap(str(paths.ICON_ICO))
        except tk.TclError:
            pass
        win.geometry(f"{T.WINDOW[0]}x{T.WINDOW[1]}")
        win.minsize(*T.WINDOW_MIN)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<Control-Tab>", lambda _e: self._step_section(1))
        win.bind("<Control-Shift-Tab>", lambda _e: self._step_section(-1))
        win.grid_rowconfigure(0, weight=1)
        side_col, main_col = (1, 0) if k.rtl else (0, 1)
        win.grid_columnconfigure(main_col, weight=1)
        self.sidebar = ctk.CTkFrame(win, fg_color=T.BG_SIDEBAR, corner_radius=0, width=T.SIDEBAR_W)
        self.sidebar.grid(row=0, column=side_col, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.pack_propagate(False)   # children are packed: keep the token width (216 or 60 px)
        self.main = ctk.CTkFrame(win, fg_color=T.BG_APP, corner_radius=0)
        self.main.grid(row=0, column=main_col, sticky="nsew")
        self.footer = ctk.CTkFrame(win, fg_color=T.BG_SIDEBAR, corner_radius=0, height=56)
        self.footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._build_sidebar()
        self._build_footer()
        self.select(self.section if self.section in dict(SECTIONS) else "general")
        win.bind("<Configure>", self._on_resize, add="+")
        win.after(60, lambda: self._bring_to_front(center=True))
        win.after(120, lambda: winutil.set_dark_titlebar(winutil.toplevel_hwnd(win))
                  if self.s["ui_theme"] != "light" else None)

    def _bring_to_front(self, center: bool = False) -> None:
        """Center on the primary monitor and raise above other apps."""
        win = self.win
        if win is None or not win.winfo_exists():
            return
        if center:
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

    def _on_resize(self, e: Any) -> None:
        if self.win is None or e.widget is not self.win:
            return
        if self._resize_job is not None:
            self.win.after_cancel(self._resize_job)
        self._resize_job = self.win.after(120, self._apply_responsive)

    def _logical_width(self) -> float:
        """Window width in logical pixels (customtkinter scales everything by the Windows DPI)."""
        if self.win is None:
            return 0.0
        try:
            scale = self.win._get_window_scaling()  # noqa: SLF001
        except Exception:
            scale = 1.0
        return self.win.winfo_width() / max(0.1, scale)

    def _apply_responsive(self) -> None:
        self._resize_job = None
        if self.win is None or not self.win.winfo_exists():
            return
        width = self._logical_width()
        rail = width < T.RAIL_BELOW
        if rail != self._rail:
            self._rail = rail
            self._style_nav()
        if self._tpl_page is not None:
            self._layout_preview(width)

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> None:
        k = self.kit
        assert k is not None
        sb = self.sidebar
        for w in sb.winfo_children():
            w.destroy()
        head = ctk.CTkFrame(sb, fg_color="transparent")
        head.pack(fill="x", padx=T.SPACE[5], pady=(T.SPACE[6], T.SPACE[5]))
        try:
            from PIL import Image

            logo = ctk.CTkImage(Image.open(paths.ICON_PNG), size=(28, 28))
            ctk.CTkLabel(head, text="", image=logo, width=28).pack(side=k.start)
        except Exception:
            pass
        self._brand = k.label(head, "Dualyrics", "subtitle")
        self._brand.pack(side=k.start, padx=T.SPACE[3])
        nav = ctk.CTkFrame(sb, fg_color="transparent")
        nav.pack(fill="x", padx=T.SPACE[3])
        self._nav = {}
        for key, icon in SECTIONS:
            item = ctk.CTkFrame(nav, fg_color="transparent", height=T.NAV_ITEM_H)
            item.pack(fill="x", pady=1)
            bar = ctk.CTkFrame(item, width=3, height=24, corner_radius=2, fg_color="transparent")
            bar.pack(side=k.start, padx=(0, 2) if not k.rtl else (2, 0))
            btn = ctk.CTkButton(item, text=t(f"sec_{key}"), anchor=k.anchor, height=T.NAV_ITEM_H,
                                corner_radius=T.NAV_ITEM_H // 2, fg_color="transparent", hover_color=T.BG_HOVER,
                                text_color=T.TEXT_SECONDARY, font=k.font("body"), compound=k.start,
                                image=k.icon(icon, T.ICON_NAV, T.TEXT_SECONDARY),
                                command=lambda s=key: self.select(s))
            btn.pack(side=k.start, fill="x", expand=True)
            focus_ring(btn)
            btn._canvas.bind("<Up>", lambda _e: self._step_section(-1, focus=True))  # noqa: SLF001
            btn._canvas.bind("<Down>", lambda _e: self._step_section(1, focus=True))  # noqa: SLF001
            self._nav[key] = {"btn": btn, "bar": bar, "icon": icon}
            self._tooltip(btn, t(f"sec_{key}"))
        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=T.SPACE[5], pady=T.SPACE[5])
        ctk.CTkFrame(foot, height=1, fg_color=T.BORDER_SUBTLE).pack(fill="x", pady=(0, T.SPACE[4]))
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x")
        self._np_dot = ctk.CTkFrame(row, width=10, height=10, corner_radius=5, fg_color=T.TEXT_DISABLED)
        self._np_dot.pack(side=k.start)
        self._np_head = k.label(row, t("now_playing"), "caption", T.TEXT_MUTED)
        self._np_head.pack(side=k.start, padx=T.SPACE[3])
        self._np = k.label(foot, "", "caption", T.TEXT_SECONDARY, wrap=180)
        self._np.pack(fill="x", pady=(T.SPACE[2], 0))
        self._refresh_now_playing()

    def _tooltip(self, widget: Any, text: str) -> None:
        tip: dict[str, Any] = {}

        def show(_e: Any) -> None:
            if not self._rail or tip.get("w"):
                return
            w = tk.Toplevel(widget)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            tk.Label(w, text=text.replace("\u202b", "").replace("\u202c", ""), bg="#1A2436", fg="#E6EDF7",
                     font=("Segoe UI", 10), padx=8, pady=4).pack()
            x = widget.winfo_rootx() + (widget.winfo_width() + 6 if not is_rtl() else -w.winfo_reqwidth() - 6)
            w.geometry(f"+{x}+{widget.winfo_rooty() + 8}")
            tip["w"] = w

        def hide(_e: Any) -> None:
            w = tip.pop("w", None)
            if w is not None:
                w.destroy()

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _style_nav(self) -> None:
        k = self.kit
        assert k is not None
        self.sidebar.configure(width=T.SIDEBAR_RAIL_W if self._rail else T.SIDEBAR_W)
        for key, item in self._nav.items():
            sel = key == self.section
            btn = item["btn"]
            btn.configure(text="" if self._rail else t(f"sec_{key}"), width=44 if self._rail else 140,
                          corner_radius=10 if self._rail else T.NAV_ITEM_H // 2,
                          anchor="center" if self._rail else k.anchor,
                          fg_color=T.BG_SELECTED if sel else "transparent",
                          text_color=T.TEXT_PRIMARY if sel else T.TEXT_SECONDARY,
                          font=k.font("body.strong" if sel else "body"),
                          image=k.icon(item["icon"], T.ICON_NAV, T.ACCENT if sel else T.TEXT_SECONDARY))
            item["bar"].configure(fg_color=T.ACCENT if sel else "transparent")
        for w in (self._brand, self._np, self._np_head):
            if self._rail:
                w.pack_forget()
            elif not w.winfo_manager():
                w.pack(side=k.start, padx=T.SPACE[3]) if w is not self._np else w.pack(fill="x")

    def _refresh_now_playing(self) -> None:
        if self.win is None or not self._np.winfo_exists():
            return
        tr = self.app.media.track
        if tr is not None and self.app.media.has_session:
            app = tr.app.split("!")[-1].split(".")[0] if tr.app else ""
            self._np.configure(text=f"{tr.title} — {tr.artist}" + (f"\n{app}" if app else ""))
            self._np_dot.configure(fg_color=T.SUCCESS if self.app.media.playing else T.WARNING)
        else:
            self._np.configure(text=t("no_song"))
            self._np_dot.configure(fg_color=T.TEXT_DISABLED)
        self._np.after(1500, self._refresh_now_playing)

    def _step_section(self, d: int, focus: bool = False) -> str:
        keys = [k for k, _ in SECTIONS]
        i = (keys.index(self.section) + d) % len(keys)
        self.select(keys[i])
        if focus:
            try:
                self._nav[keys[i]]["btn"]._canvas.focus_set()  # noqa: SLF001
            except Exception:
                pass
        return "break"

    # ------------------------------------------------------------------ footer
    def _build_footer(self) -> None:
        k = self.kit
        assert k is not None
        f = self.footer
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=T.SPACE[6], pady=T.SPACE[3])
        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side=k.start)
        ctk.CTkLabel(left, text="", image=k.icon("ok", T.ICON, T.SUCCESS), width=18).pack(side=k.start)
        self._saved_lbl = k.label(left, t("saved_auto"), "caption", T.TEXT_SECONDARY)
        self._saved_lbl.pack(side=k.start, padx=T.SPACE[3])
        self._footer_actions = ctk.CTkFrame(inner, fg_color="transparent")
        self._footer_actions.pack(side=k.end)

    def _set_footer(self, actions: list[tuple[str, Callable[[], None], str]]) -> None:
        k = self.kit
        assert k is not None
        for w in self._footer_actions.winfo_children():
            w.destroy()
        has_primary = any(kind == "primary" for _l, _f, kind in actions)
        items = list(actions) + [(t("close"), self.close, "secondary" if has_primary else "primary")]
        for label, fn, kind in reversed(items):
            k.button(self._footer_actions, label, fn, kind=kind).pack(side=k.end, padx=(T.SPACE[3], 0)
                                                                    if not k.rtl else (0, T.SPACE[3]))

    # ------------------------------------------------------------------ sections
    def select(self, section: str) -> None:
        section = ALIASES.get(section, section)
        if section not in dict(SECTIONS) or self.win is None:
            return
        self._flush()
        self.section = section
        if self.s["settings_section"] != section:
            self.s.update(settings_section=section)
        k = self.kit
        assert k is not None
        for w in self.main.winfo_children():
            w.destroy()
        self._setters = {}
        self._tpl_page = None
        self._style_nav()
        head = k.section_header(self.main, t(f"sec_{section}"), t(f"sec_{section}_caption"))
        head.pack(fill="x", padx=T.SPACE[7], pady=(T.SPACE[7], T.SPACE[5]))
        body = ctk.CTkFrame(self.main, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self._body = body
        content_col, side_col = (1, 0) if k.rtl else (0, 1)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(content_col, weight=1)
        scroll = ctk.CTkScrollableFrame(body, fg_color="transparent", scrollbar_button_color=T.BORDER_SUBTLE,
                                        scrollbar_button_hover_color=T.BORDER_STRONG)
        scroll.grid(row=0, column=content_col, sticky="nsew", padx=(T.SPACE[7], T.SPACE[3]) if not k.rtl
                    else (T.SPACE[3], T.SPACE[7]))
        self._scroll = scroll
        content = ctk.CTkFrame(scroll, fg_color="transparent", width=T.CONTENT_MAX)
        content.pack(fill="x", anchor="ne" if k.rtl else "nw", padx=(0, T.SPACE[3]))
        self._toast = Toast(k, self.main)
        self._set_footer([])
        builder = getattr(self, f"_build_{section}")
        if section == "templates":
            side = ctk.CTkFrame(body, fg_color="transparent", width=T.PREVIEW_W)
            self._side = side
            self._side_col = side_col
            from dualyrics.ui.template_page import TemplatePage

            self._tpl_page = TemplatePage(self, k, content, side)
            self._layout_preview(self._logical_width())
            self._set_footer([(t("tpl_duplicate"), self._tpl_page.duplicate, "secondary")])
        else:
            builder(content)
        try:
            scroll._parent_canvas.yview_moveto(0)  # noqa: SLF001
        except Exception:
            pass

    def _layout_preview(self, width: float) -> None:
        """Preview column at the end; below 940 px it docks under the content; it can be collapsed."""
        side = getattr(self, "_side", None)
        if side is None or not side.winfo_exists():
            return
        k = self.kit
        assert k is not None
        side.grid_forget()
        if self.s["preview_collapsed"]:
            self._collapse_btn()
            return
        docked = bool(width) and width < T.DOCK_BELOW
        if self._tpl_page is not None:
            self._tpl_page.set_compact(docked)
        if docked:
            side.grid(row=1, column=0, columnspan=2, sticky="ew", padx=T.SPACE[7], pady=(0, T.SPACE[3]))
        else:
            side.grid(row=0, column=self._side_col, sticky="n", padx=(T.SPACE[3], T.SPACE[7]) if not k.rtl
                      else (T.SPACE[7], T.SPACE[3]))
        self._collapse_btn()

    def _collapse_btn(self) -> None:
        k = self.kit
        assert k is not None
        old = getattr(self, "_collapse", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        collapsed = self.s["preview_collapsed"]
        label = t("show_preview") if collapsed else t("collapse_preview")
        b = k.button(self.main, label, self._toggle_preview, kind="ghost",
                     icon="chevron_left" if (collapsed != k.rtl) else "chevron_right")
        b.place(relx=0.0 if k.rtl else 1.0, x=T.SPACE[7] if k.rtl else -T.SPACE[7], y=T.SPACE[7],
                anchor="nw" if k.rtl else "ne")
        self._collapse = b

    def _toggle_preview(self) -> None:
        self.s.update(preview_collapsed=not self.s["preview_collapsed"])
        if self.win is not None:
            self._layout_preview(self._logical_width())

    # -- General
    def _build_general(self, f: Any) -> None:
        k = self.kit
        assert k is not None
        g = k.group(f, t("grp_language_look"))
        self.watch("ui_language", k.option(g, t("ui_language"), [("en", "English"), ("fa", "فارسی (Persian)")],
                                           *self.bind_key("ui_language")))
        self.watch("ui_theme", k.segmented(g, t("ui_theme"), [("system", t("ui_theme_system")),
                                                              ("dark", t("ui_theme_dark")),
                                                              ("light", t("ui_theme_light"))],
                                           *self.bind_key("ui_theme")))
        g = k.group(f, t("grp_behaviour"))
        self.watch("mode", k.option(g, t("mode"), [("auto", t("mode_auto")), ("lyrics", t("mode_lyrics")),
                                                   ("live", t("mode_live"))], *self.bind_key("mode"),
                                    help=t("mode_hint"), width=300))
        self.watch("translation_only", k.switch(g, t("translation_only"), *self.bind_key("translation_only"),
                                                help=t("translation_only_hint")))
        self.watch("start_with_windows", k.switch(g, t("start_with_windows"), lambda: self.s["start_with_windows"],
                                                  self.app.set_startup))
        self.watch("visible", k.switch(g, t("show_lyrics"), *self.bind_key("visible"), help=t("visible_hint")))

    # -- Profiles
    def _profiles(self) -> list[dict[str, Any]]:
        return self.s["profiles"]

    def _set_profile(self, pid: str, **changes: Any) -> None:
        profs = self._profiles()
        for p in profs:
            if p["id"] == pid:
                p.update(changes)
        self.s.update(profiles=profs)

    def _build_profiles(self, f: Any) -> None:
        from dualyrics import procs, profiles as prof_mod

        k = self.kit
        assert k is not None
        g = k.group(f)
        self.watch("profiles_enabled", k.switch(g, t("ap_enabled"), *self.bind_key("profiles_enabled"),
                                                help=t("ap_enabled_hint")))
        self.watch("profile_activation", k.option(g, t("ap_activation"),
                                                  [("running", t("ap_act_running")),
                                                   ("foreground", t("ap_act_foreground"))],
                                                  *self.bind_key("profile_activation"), width=260))
        lst = k.group(f, t("ap_list"), t("ap_list_hint"))
        box = ctk.CTkFrame(lst, fg_color="transparent")
        box.pack(fill="x", pady=(T.SPACE[3], 0))
        icons: dict[str, Any] = {}
        mode_opts = [(m, t(f"pm_{m}")) for m in prof_mod.MODES]

        def icon_for(p: dict[str, Any]) -> Any:
            src = p.get("icon") or p.get("path") or ""
            if not src:
                return k.icon({"game": "target", "music": "lyrics", "video": "monitors", "browser": "network"}.get(
                    p.get("kind", "other"), "profiles"), 20, T.TEXT_SECONDARY)
            if src not in icons:
                img = procs.exe_icon(src, 32)
                icons[src] = ctk.CTkImage(img, size=(20, 20)) if img is not None else k.icon("profiles", 20)
            return icons[src]

        def render(*_a: Any) -> None:
            for w in box.winfo_children():
                w.destroy()
            for p in self._profiles():
                row = ctk.CTkFrame(box, fg_color=T.BG_SURFACE, corner_radius=T.RADIUS_MD, border_width=1,
                                   border_color=T.BORDER_SUBTLE)
                row.pack(fill="x", pady=3)
                pid = p["id"]
                name = t(f"ap_builtin_{pid}") if p.get("builtin") else p["name"]
                start = ctk.CTkFrame(row, fg_color="transparent")
                start.pack(side=k.start, fill="x", expand=True, padx=T.SPACE[4], pady=T.SPACE[3])
                ctk.CTkLabel(start, text="", image=icon_for(p), width=24).pack(side=k.start)
                txt = ctk.CTkFrame(start, fg_color="transparent")
                txt.pack(side=k.start, padx=T.SPACE[3], fill="x", expand=True)
                k.label(txt, name, "body", T.TEXT_PRIMARY if p.get("enabled") else T.TEXT_DISABLED).pack(fill="x")
                sub = t(f"kind_{p.get('kind', 'other')}")
                if p.get("exes") and not p.get("builtin"):
                    sub += "  ·  " + ", ".join(p["exes"][:2])
                k.label(txt, sub, "caption", T.TEXT_MUTED).pack(fill="x")
                end = ctk.CTkFrame(row, fg_color="transparent")
                end.pack(side=k.end, padx=T.SPACE[3])
                if not p.get("builtin"):
                    k.button(end, "", lambda pid=pid: remove(pid), kind="danger", icon="trash", width=36).pack(
                        side=k.end, padx=2)
                k.button(end, t("ap_edit"), lambda pid=pid: self._edit_profile(pid), icon="edit").pack(
                    side=k.end, padx=4)
                k.option_menu(end, mode_opts, lambda p=p: p.get("mode", "auto"),
                              lambda v, pid=pid: self._set_profile(pid, mode=v), width=140)
                en = tk.BooleanVar(value=bool(p.get("enabled")))
                ctk.CTkSwitch(end, text="", variable=en, width=44, switch_width=40, switch_height=20,
                              progress_color=T.ACCENT, fg_color=T.BORDER_STRONG,
                              command=lambda pid=pid, en=en: self._set_profile(pid, enabled=en.get())).pack(
                    side=k.end, padx=6)

        def remove(pid: str) -> None:
            old = self._profiles()
            self.s.update(profiles=[p for p in old if p["id"] != pid])
            self.toast(t("ap_removed"), "info", (t("undo"), lambda: self.s.update(profiles=old)))

        render()
        self.watch("profiles", render)

        add = k.group(f, t("ap_add"), t("ap_add_hint"))
        preset_opts = [("games", t("ap_preset_games")), ("video", t("ap_preset_video")),
                       ("music", t("ap_preset_music")), ("custom", t("ap_preset_custom"))]
        preset = {"v": "games"}
        k.option(add, t("ap_add_as"), preset_opts, lambda: preset["v"], lambda v: preset.update(v=v), width=300)
        msg = k.label(add, "", "caption", T.TEXT_SECONDARY, wrap=560)

        def add_app(app: Any) -> None:
            if app is None or isinstance(app, Exception):
                msg.configure(text=t("ap_pick_none"))
                return
            prof = prof_mod.new_profile(app.name, app.path or app.exe, preset["v"], self._profiles())
            before = len(self._profiles())
            self.s.update(profiles=prof_mod.add_profile(self._profiles(), prof))
            added = len(self._profiles()) > before
            msg.configure(text=t("ap_added", name=app.name) if added else t("ap_exists", name=app.name))

        def browse() -> None:
            from tkinter import filedialog

            path = filedialog.askopenfilename(parent=self.win, title=t("ap_browse"),
                                              filetypes=[(t("ap_programs"), "*.exe")])
            if path:
                add_app(procs.app_from_path(os.path.normpath(path)))

        def pick() -> None:
            msg.configure(text=t("ap_pick_hint"))
            self._run_bg(lambda: procs.pick_window(20.0, cancelled=lambda: self.win is None), add_app)

        acts = k.actions(add)
        k.button(acts, t("ap_running"), lambda: self._running_picker(add_app), kind="primary",
                 icon="add").pack(side=k.start)
        k.button(acts, t("ap_browse"), browse, icon="folder").pack(side=k.start, padx=8)
        k.button(acts, t("ap_pick"), pick, icon="target").pack(side=k.start)
        msg.pack(fill="x", pady=(T.SPACE[3], 0))
        k.note(add, t("ap_note"), "warning")

        io = k.group(f, t("ap_io"), t("ap_io_hint"))
        acts = k.actions(io)
        k.button(acts, t("ap_import"), self._import_profiles, icon="import").pack(side=k.start)
        k.button(acts, t("ap_export"), self._export_profiles, icon="export").pack(side=k.start, padx=8)

    def _export_profiles(self) -> None:
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(parent=self.win, title=t("ap_export"), defaultextension=".json",
                                            initialfile="dualyrics-profiles.json",
                                            filetypes=[(t("tpl_files"), "*.json")])
        if not path:
            return
        data = {"dualyrics_profiles": 1, "profiles": self._profiles()}
        try:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.toast(t("tpl_exported", name=Path(path).name), "success")
        except OSError as e:
            self.toast(t("tpl_save_failed", err=str(e)[:120]), "danger")

    def _import_profiles(self) -> None:
        from tkinter import filedialog

        from dualyrics import profiles as prof_mod

        path = filedialog.askopenfilename(parent=self.win, title=t("ap_import"),
                                          filetypes=[(t("tpl_files"), "*.json")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            items = raw.get("profiles") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                raise ValueError("no profiles in this file")
        except (OSError, ValueError) as e:
            self.toast(t("tpl_import_bad", err=str(e)[:160]), "danger")
            return
        current = self._profiles()
        by_id = {p["id"]: p for p in current}
        merged = list(current)
        for p in items:
            if not isinstance(p, dict):
                continue
            if p.get("id") in by_id:
                by_id[p["id"]].update({k: v for k, v in p.items() if k in prof_mod.BEHAVIOUR})
            else:
                merged = prof_mod.add_profile(merged, p)
        self.s.update(profiles=merged)
        self.toast(t("ap_imported", n=len(items)), "success")

    def _running_picker(self, on_pick: Callable[[Any], None]) -> None:
        """A small window listing running apps with their icons; a click adds one."""
        from dualyrics import procs

        k = self.kit
        assert k is not None
        top = ctk.CTkToplevel(self.win)
        top.title(t("ap_running"))
        top.geometry("440x500")
        top.transient(self.win)
        top.configure(fg_color=T.BG_APP)
        lst = ctk.CTkScrollableFrame(top, fg_color=T.BG_SURFACE, corner_radius=T.RADIUS_LG)
        lst.pack(fill="both", expand=True, padx=12, pady=12)
        k.label(lst, t("testing"), "body", T.TEXT_SECONDARY).pack()

        def fill(apps: Any) -> None:
            if not top.winfo_exists():
                return
            for w in lst.winfo_children():
                w.destroy()
            if isinstance(apps, Exception) or not apps:
                k.label(lst, t("ap_none_running"), "body", T.TEXT_SECONDARY).pack()
                return
            for app, img in apps:
                image = ctk.CTkImage(img, size=(20, 20)) if img is not None else k.icon("profiles", 20)

                def choose(a: Any = app) -> None:
                    top.destroy()
                    on_pick(a)

                ctk.CTkButton(lst, text=f"  {app.name}   ({app.exe})", image=image, anchor=k.anchor,
                              command=choose, fg_color="transparent", hover_color=T.BG_HOVER,
                              text_color=T.TEXT_PRIMARY, font=k.font("body"), height=36).pack(fill="x", pady=1)

        self._run_bg(lambda: [(a, procs.exe_icon(a.path, 32)) for a in procs.running_apps()], fill)
        top.after(80, lambda: top.winfo_exists() and (top.lift(), top.focus_force()))

    def _edit_profile(self, pid: str) -> None:
        """Per-app details: mode, kind, template, language, monitor, position, width, click-through..."""
        from dualyrics import profiles as prof_mod

        prof = next((p for p in self._profiles() if p["id"] == pid), None)
        k = self.kit
        if prof is None or k is None:
            return
        top = ctk.CTkToplevel(self.win)
        name = t(f"ap_builtin_{pid}") if prof.get("builtin") else prof["name"]
        top.title(t("ap_edit_title", name=name))
        top.geometry("600x640")
        top.transient(self.win)
        top.configure(fg_color=T.BG_APP)
        body = ctk.CTkScrollableFrame(top, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=12)
        g = k.group(body, t("ap_edit_title", name=name))
        glob = t("ap_global")
        tri = [(None, glob), (True, t("on")), (False, t("off"))]

        def get(field: str) -> Callable[[], Any]:
            return lambda: next((p for p in self._profiles() if p["id"] == pid), {}).get(field)

        def set_(field: str) -> Callable[[Any], None]:
            return lambda v: self._set_profile(pid, **{field: v})

        lang = language()
        tpl_opts = [("", glob)] + [(d["id"], templates.display_name(d, lang, t)) for d in self.app.templates.all()]
        k.option(g, t("ap_mode"), [(m, t(f"pm_{m}")) for m in prof_mod.MODES], get("mode"), set_("mode"))
        k.option(g, t("ap_kind"), [(x, t(f"kind_{x}")) for x in prof_mod.KINDS], get("kind"), set_("kind"))
        k.option(g, t("ap_template"), tpl_opts, get("template"), set_("template"))
        k.option(g, t("target_language"), [("", glob)] + [(lg.code, f"{lg.name} — {lg.native}")
                                                          for lg in langs.LANGUAGES], get("target_language"),
                 set_("target_language"), width=280)
        k.option(g, t("translation_only"), tri, get("translation_only"), set_("translation_only"))
        mons = winutil.monitors()
        k.option(g, t("ap_monitor"), [("", glob)] + [(m.name, self._monitor_label(i, m))
                                                     for i, m in enumerate(mons, 1)], get("monitor"), set_("monitor"),
                 width=280)
        k.option(g, t("preset"), [("", glob)] + [(p, t(f"pr_{p}")) for p in ("bottom", "top", "center")],
                 get("position"), set_("position"))
        k.option(g, t("ap_width"), [(0, glob)] + [(w, f"{w} px") for w in (600, 800, 900, 1100, 1300, 1600, 2000)],
                 get("width"), set_("width"))
        k.option(g, t("click_through"), tri, get("click_through"), set_("click_through"))
        k.option(g, t("ap_activation"), [("default", glob), ("running", t("ap_act_running")),
                                         ("foreground", t("ap_act_foreground"))], get("activation"),
                 set_("activation"), width=260)
        k.button(top, t("close"), top.destroy, kind="primary").pack(pady=(0, 12))
        top.after(80, lambda: top.winfo_exists() and (top.lift(), top.focus_force()))

    # -- Templates (see dualyrics/ui/template_page.py)
    def _build_templates(self, f: Any) -> None:  # pragma: no cover - built by select()
        pass

    # -- Lyrics
    def _build_lyrics(self, f: Any) -> None:
        k = self.kit
        assert k is not None
        g = k.group(f, t("grp_when"))
        self.watch("hide_when_idle", k.switch(g, t("hide_when_idle"), *self.bind_key("hide_when_idle")))
        self.watch("idle_hide_delay", k.slider(g, t("idle_delay"), 0, 60, *self.bind_key("idle_hide_delay"), 1, "s",
                                               indent=True))
        g = k.group(f, t("grp_lines"), t("grp_lines_hint"))
        for key in ("show_original", "show_translation", "show_next"):
            self.watch(key, k.switch(g, t(key), *self.bind_key(key)))
        self.watch("plain_height", k.slider(g, t("plain_height"), 120, 1200, *self.bind_key("plain_height"), 10,
                                            "px", help=t("plain_height_hint")))
        g = k.group(f, t("preferred_players"), t("players_hint"))
        refresh = k.entry(g, t("players"), lambda: ", ".join(self.s["preferred_players"]),
                          lambda v: self.s.update(preferred_players=v), width=320)
        self._flushers.append(getattr(refresh, "widget").flush)
        self.watch("preferred_players", refresh)

    # -- Live mode
    def _build_live(self, f: Any) -> None:
        from dualyrics.live import deps, models

        k = self.kit
        assert k is not None
        g = k.group(f, t("lv_install_title"))
        status = k.label(g, "", "body", wrap=560)
        status.pack(fill="x", pady=(T.SPACE[3], 0))
        gpu_var = tk.BooleanVar(value=_has_nvidia())
        install_log = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)
        busy = {"install": False, "download": False}
        acts = k.actions(g)

        def refresh() -> None:
            if not status.winfo_exists():
                return
            st = deps.status()
            cfg = self.s.snapshot()
            name = models.model_name(cfg["live_model"], cfg["live_english_only"])
            if not st.ready:
                text = t("lv_state_missing", pkgs=", ".join(p.split(">")[0].split("=")[0] for p in st.missing))
            else:
                eng = self.app.live
                dev = {"cuda": "GPU", "cpu": "CPU"}.get(eng.device, "")
                text = t("lv_state_ready", model=name, size=models.MODELS[name].size_mb,
                         have=t("yes") if models.is_installed(name) else t("lv_not_downloaded"),
                         device=dev or t("lv_device_unknown"))
            status.configure(text=text)
            install_btn.configure(state="disabled" if busy["install"] else "normal",
                                  text=t("lv_reinstall") if st.ready else t("lv_install"))

        def install() -> None:
            busy["install"] = True
            refresh()
            install_log.configure(text=t("lv_installing"))
            gpu = gpu_var.get()
            cfg = self.s.snapshot()

            def work() -> tuple[bool, str]:
                return deps.install(gpu, cfg["proxy_mode"], cfg["proxy_url"],
                                    on_line=lambda line: self.app.call_soon(
                                        lambda: install_log.winfo_exists() and install_log.configure(text=line)))

            def done(res: Any) -> None:
                busy["install"] = False
                self.app.recheck_live_deps()
                if isinstance(res, Exception):
                    res = (False, str(res))
                ok, msg = res
                install_log.configure(text=t("lv_install_ok") if ok else t("lv_install_fail", err=msg[:300]))
                refresh()

            self._run_bg(work, done)

        install_btn = k.button(acts, t("lv_install"), install, kind="primary", icon="import")
        install_btn.pack(side=k.start)
        ctk.CTkCheckBox(acts, text=t("lv_gpu"), variable=gpu_var, font=k.font("body"), fg_color=T.ACCENT,
                        hover_color=T.ACCENT_HOVER, border_color=T.BORDER_STRONG,
                        text_color=T.TEXT_PRIMARY).pack(side=k.start, padx=12)
        install_log.pack(fill="x", pady=(T.SPACE[3], 0))
        k.note(g, t("lv_install_hint"))

        g = k.group(f, t("lv_group_main"))
        self.watch("live_enabled", k.switch(g, t("lv_enabled"), *self.bind_key("live_enabled"),
                                            help=t("lv_enabled_hint")))
        self.watch("live_when_no_lyrics", k.switch(g, t("lv_no_lyrics"), *self.bind_key("live_when_no_lyrics")))
        g = k.group(f, t("lv_group_model"))
        self.watch("live_model", k.option(g, t("lv_model"),
                                          [(m, f"{m} — {models.MODELS[m].size_mb} MB · {t('lv_model_' + m)}")
                                           for m in models.SIZES], *self.bind_key("live_model"), width=320))
        self.watch("live_english_only", k.switch(g, t("lv_english_only"), *self.bind_key("live_english_only"),
                                                 help=t("lv_english_only_hint")))
        self.watch("live_source_language", k.option(g, t("lv_source"), [("auto", t("lv_detect"))] + [
            (lg.code, f"{lg.name} — {lg.native}") for lg in langs.LANGUAGES], *self.bind_key("live_source_language"),
            width=280))
        self.watch("live_device", k.segmented(g, t("lv_device"), [("auto", t("lv_dev_auto_short")), ("cpu", "CPU"),
                                                                 ("gpu", t("lv_dev_gpu"))],
                                              *self.bind_key("live_device")))
        dl = k.actions(g)
        dl_status = k.label(dl, "", "caption", T.TEXT_SECONDARY)

        def download() -> None:
            if busy["download"]:
                return
            cfg = self.s.snapshot()
            name = models.model_name(cfg["live_model"], cfg["live_english_only"])
            busy["download"] = True
            prog = {"pct": 0}

            def tick() -> None:
                if busy["download"] and dl_status.winfo_exists():
                    dl_status.configure(text=t("lv_downloading", model=name, pct=prog["pct"]))
                    dl_status.after(400, tick)

            def work() -> Any:
                return models.download(name, self.app.session(),
                                       lambda d, tot: prog.update(pct=int(d * 100 / tot) if tot else 0),
                                       cancelled=lambda: self.win is None)

            def done(res: Any) -> None:
                busy["download"] = False
                dl_status.configure(text=t("lv_download_fail", err=str(res)[:200]) if isinstance(res, Exception)
                                    else t("lv_downloaded", model=name))
                refresh()

            tick()
            self._run_bg(work, done)

        k.button(dl, t("lv_download"), download, icon="save").pack(side=k.start)
        dl_status.pack(side=k.start, padx=10)

        g = k.group(f, t("lv_tuning"), t("lv_tuning_hint"))
        self.watch("live_vad_sensitivity", k.slider(g, t("lv_vad"), 1, 10, *self.bind_key("live_vad_sensitivity"), 1,
                                                    help=t("lv_vad_hint")))
        self.watch("live_max_segment", k.slider(g, t("lv_max_segment"), 2, 15, *self.bind_key("live_max_segment"),
                                                0.5, "s"))
        self.watch("live_reading_cps", k.slider(g, t("lv_cps"), 8, 30, *self.bind_key("live_reading_cps"), 1))
        self.watch("live_min_display", k.slider(g, t("lv_min_display"), 0.5, 6, *self.bind_key("live_min_display"),
                                                0.5, "s"))
        self.watch("live_context_lines", k.slider(g, t("lv_context"), 0, 12, *self.bind_key("live_context_lines"), 1))
        g = k.group(f, t("lv_group_transcripts"))
        self.watch("live_save_transcripts", k.switch(g, t("lv_transcripts"), *self.bind_key("live_save_transcripts")))
        acts = k.actions(g)

        def open_transcripts() -> None:
            p = paths.data_dir() / "transcripts"
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(p)

        pause_btn = k.button(acts, "", lambda: (self.app.command("toggle_live_pause"),
                                                pause_btn.after(150, set_pause_text)), icon="pause")

        def set_pause_text() -> None:
            if pause_btn.winfo_exists():
                pause_btn.configure(text=t("lv_resume") if self.app.live.paused else t("lv_pause"))

        set_pause_text()
        pause_btn.pack(side=k.start)
        k.button(acts, t("lv_open_transcripts"), open_transcripts, icon="folder").pack(side=k.start, padx=8)
        self.watch("live_model", refresh)
        self.watch("live_english_only", refresh)
        refresh()

    # -- Translation
    def _build_translation(self, f: Any) -> None:
        k = self.kit
        assert k is not None
        g = k.group(f)
        self.watch("target_language", k.option(g, t("target_language"), [(lg.code, f"{lg.name} — {lg.native}")
                                                                        for lg in langs.LANGUAGES],
                                               *self.bind_key("target_language"), width=300))
        self.watch("provider", k.option(g, t("provider"), [("gemini", t("prov_gemini")), ("free", t("prov_free"))],
                                        *self.bind_key("provider"), width=300))
        self.watch("fallback_free", k.switch(g, t("fallback_free"), *self.bind_key("fallback_free")))
        self.watch("skip_same_language", k.switch(g, t("skip_same"), *self.bind_key("skip_same_language")))

        g = k.group(f, t("gemini_keys"), t("gemini_keys_hint"))
        keys = k.textbox(g, "\n".join(self.s["gemini_keys"]), height=70)
        k.label(g, t("gemini_models"), "body").pack(fill="x", pady=(T.SPACE[5], 0))
        models_tb = k.textbox(g, "\n".join(self.s["gemini_models"]), height=90)

        def save_keys(_e: Any = None) -> None:
            self.s.update(gemini_keys=[x.strip() for x in keys.get("1.0", "end").split() if x.strip()])

        def save_models(_e: Any = None) -> None:
            self.s.update(gemini_models=models_tb.get("1.0", "end"))

        keys.bind("<FocusOut>", save_keys)
        models_tb.bind("<FocusOut>", save_models)
        self._flushers += [save_keys, save_models]

        def set_models() -> None:
            models_tb.delete("1.0", "end")
            models_tb.insert("1.0", "\n".join(self.s["gemini_models"]))

        self.watch("gemini_models", set_models)
        result = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)

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
            result.configure(text="\n".join(f"{'OK' if ok else 'X'}  {kk}  {m}: {msg}" for kk, m, ok, msg in res))

        acts = k.actions(g)
        k.button(acts, t("test"), test, kind="primary").pack(side=k.start)
        k.button(acts, t("reset_models"), lambda: self.s.update(gemini_models=list(settings_mod.DEFAULT_MODELS))
                 ).pack(side=k.start, padx=8)
        result.pack(fill="x", pady=(T.SPACE[3], 0))

        g = k.group(f, t("style"), t("style_hint"))
        style = k.textbox(g, self.s["style"], height=110)

        def save_style(_e: Any = None) -> None:
            self.s.update(style=style.get("1.0", "end").strip())

        style.bind("<FocusOut>", save_style)
        self._flushers.append(save_style)

        def set_style() -> None:
            style.delete("1.0", "end")
            style.insert("1.0", self.s["style"])

        self.watch("style", set_style)
        acts = k.actions(g)
        status = k.label(acts, "", "caption", T.TEXT_SECONDARY)
        k.button(acts, t("reset_style"), lambda: self.s.update(style=settings_mod.DEFAULT_STYLE)).pack(side=k.start)

        def clear() -> None:
            self.app.clear_cache()
            status.configure(text=t("cache_cleared"))

        k.button(acts, t("clear_cache"), clear, kind="danger", icon="trash").pack(side=k.start, padx=8)
        status.pack(side=k.start, padx=8)

    # -- Monitors and placement
    def _monitor_label(self, i: int, m: winutil.Monitor) -> str:
        w = (m.screen[2] - m.screen[0]) or m.width
        return t("m_monitor_n", n=i, w=w, h=m.screen_height) + (f" ({t('primary')})" if m.primary else "")

    def _build_monitors(self, f: Any) -> None:
        k = self.kit
        assert k is not None
        mons = winutil.monitors()
        g = k.group(f, t("grp_monitors"), t("grp_monitors_hint"))

        def allowed(name: str) -> Callable[[], bool]:
            return lambda: name not in self.s["monitors_off"]

        def set_allowed(name: str) -> Callable[[bool], None]:
            def apply(on: bool) -> None:
                off = [n for n in self.s["monitors_off"] if n != name]
                if not on:
                    off.append(name)
                if len(off) >= len(mons):
                    self.toast(t("mon_keep_one"), "warning")
                    self._refresh("monitors_off")
                    return
                self.s.update(monitors_off=off)
            return apply

        for i, m in enumerate(mons, 1):
            self.watch("monitors_off", k.switch(g, self._monitor_label(i, m), allowed(m.name), set_allowed(m.name),
                                                help=m.name.replace("\\\\.\\", "")))
        opts = [("", t("mon_primary"))] + [(m.name, self._monitor_label(i, m)) for i, m in enumerate(mons, 1)]
        self.watch("monitor", k.option(g, t("mon_show_on"), opts, *self.bind_key("monitor"), width=280))
        self.watch("follow_active_monitor", k.switch(g, t("follow_active_monitor"),
                                                     *self.bind_key("follow_active_monitor"),
                                                     help=t("follow_active_monitor_hint")))
        acts = k.actions(g)
        k.button(acts, t("mon_identify"), self._identify_monitors, icon="monitors").pack(side=k.start)

        g = k.group(f, t("grp_place"), t("grp_place_hint"))
        self.watch("position_preset", k.segmented(g, t("preset"), [("bottom", t("pr_bottom")), ("top", t("pr_top")),
                                                                   ("center", t("pr_center")),
                                                                   ("custom", t("pr_custom_short"))],
                                                  *self.bind_key("position_preset")))
        self.watch("overlay_scale", k.slider(g, t("overlay_scale"), 50, 200,
                                             lambda: int(round(self.s["overlay_scale"] * 100)),
                                             lambda v: self.s.update(overlay_scale=v / 100), 5, "%",
                                             help=t("overlay_scale_hint")))
        self.watch("overlay_opacity", k.slider(g, t("overlay_opacity"), 20, 100,
                                               lambda: int(round(self.s["overlay_opacity"] * 100)),
                                               lambda v: self.s.update(overlay_opacity=v / 100), 5, "%"))
        self.watch("locked", k.switch(g, t("lock_position"), *self.bind_key("locked")))
        self.watch("click_through", k.switch(g, t("click_through"), *self.bind_key("click_through"),
                                             help=t("click_through_hint2", combo=self.s["hotkey_click_through"])))
        acts = k.actions(g)

        def forget() -> None:
            old = self.s["positions"]
            self.s.update(positions={}, position_preset="bottom")
            self.toast(t("mon_forgot"), "info", (t("undo"), lambda: self.s.update(positions=old)))

        k.button(acts, t("mon_forget"), forget, kind="danger", icon="trash").pack(side=k.start)

        g = k.group(f, t("grp_desktops"), t("grp_desktops_hint"))
        self.watch("virtual_desktops", k.segmented(g, t("vd_mode"), [("all", t("vd_all")),
                                                                    ("current", t("vd_current"))],
                                                   *self.bind_key("virtual_desktops")))
        from dualyrics import vdesk

        k.note(g, t("vd_note") if vdesk.manager().available else t("vd_unavailable"),
               "info" if vdesk.manager().available else "warning")
        k.note(f, t("tpl_fullscreen_note"), "warning")

    def _refresh(self, key: str) -> None:
        for fn in self._setters.get(key, []):
            try:
                fn()
            except Exception:
                pass

    def _identify_monitors(self) -> None:
        """Show each monitor's number big in its corner for two seconds."""
        for i, m in enumerate(winutil.monitors(), 1):
            w = tk.Toplevel(self.win)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg="#22D3EE")
            tk.Label(w, text=str(i), font=("Segoe UI", 64, "bold"), bg="#0B1120", fg="#22D3EE", padx=30).pack(
                padx=3, pady=3)
            w.geometry(f"+{m.left + 40}+{m.top + 40}")
            w.after(2000, w.destroy)

    # -- Sync & hotkeys
    def _build_sync(self, f: Any) -> None:
        k = self.kit
        assert k is not None
        g = k.group(f, t("global_offset"), t("global_offset_hint"))
        self.watch("offset_ms", k.slider(g, t("offset"), -5000, 5000, *self.bind_key("offset_ms"), 50, "ms"))
        acts = k.actions(g)
        k.button(acts, t("reset"), lambda: self.s.update(offset_ms=0)).pack(side=k.start)

        g = k.group(f, t("song_offset"))
        song_lbl = k.label(g, "", "body", T.TEXT_SECONDARY)
        song_lbl.pack(fill="x", pady=(T.SPACE[3], 0))
        row = k.actions(g)
        song_val = ctk.CTkLabel(row, text="0", width=100, font=k.font("mono"), text_color=T.TEXT_PRIMARY)
        k.button(row, "−", lambda: self.app.command("offset_down"), width=40).pack(side=k.start)
        song_val.pack(side=k.start, padx=6)
        k.button(row, "+", lambda: self.app.command("offset_up"), width=40).pack(side=k.start)
        k.button(row, t("reset"), lambda: self.app.command("offset_reset")).pack(side=k.start, padx=8)

        def refresh_song() -> None:
            if self.win is None or not song_lbl.winfo_exists():
                return
            label, ms = self.app.song_offset_info()
            song_lbl.configure(text=label or t("no_song"))
            song_val.configure(text=f"{ms:+d} ms")
            song_lbl.after(700, refresh_song)

        refresh_song()
        self.watch("offset_step_ms", k.slider(g, t("offset_step"), 50, 1000, *self.bind_key("offset_step_ms"), 50,
                                              "ms"))

        g = k.group(f, t("hotkeys"), t("hk_hint"))
        self.watch("hotkeys_enabled", k.switch(g, t("hotkeys_on"), *self.bind_key("hotkeys_enabled")))
        entries: dict[str, Any] = {}
        for key, label in (("hotkey_offset_up", t("hk_up")), ("hotkey_offset_down", t("hk_down")),
                           ("hotkey_toggle", t("hk_toggle")), ("hotkey_live_pause", t("hk_live_pause")),
                           ("hotkey_click_through", t("hk_click_through"))):
            box = {"v": self.s[key]}
            ref = k.entry(g, label, lambda key=key: self.s[key], lambda v, b=box: b.update(v=v), width=200, mono=True)
            entries[key] = getattr(ref, "widget")
            self.watch(key, ref)
        hk_status = k.label(g, "", "caption", T.TEXT_SECONDARY)

        def apply_hotkeys() -> None:
            self.s.update(**{kk: e.get() for kk, e in entries.items()})
            for kk, e in entries.items():
                if e.get().strip().lower().replace(" ", "") != self.s[kk]:
                    e.delete(0, "end")
                    e.insert(0, self.s[kk])
            failed = self.app.restart_hotkeys()
            hk_status.configure(text=t("hk_failed", keys=", ".join(failed)) if failed else t("hk_ok"))

        k.button(k.actions(g), t("apply"), apply_hotkeys, kind="primary").pack(side=k.start)
        hk_status.pack(fill="x", pady=(T.SPACE[3], 0))

    # -- Network
    def _build_network(self, f: Any) -> None:
        k = self.kit
        assert k is not None
        g = k.group(f)
        self.watch("proxy_mode", k.segmented(g, t("proxy_mode"), [("none", t("px_none")), ("system", t("px_system")),
                                                                  ("custom", t("px_custom"))],
                                             *self.bind_key("proxy_mode")))
        status = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)

        def save_url(v: str) -> bool:
            v = (v or "").strip()
            if v and not settings_mod.valid_proxy_url(v):
                status.configure(text=t("proxy_bad"), text_color=T.DANGER)
                return False
            changes: dict[str, Any] = {"proxy_url": v}
            if v and self.s["proxy_mode"] != "custom":
                changes["proxy_mode"] = "custom"
            self.s.update(**changes)
            status.configure(text="", text_color=T.TEXT_SECONDARY)
            return True

        ref = k.entry(g, t("proxy_url_short"), lambda: self.s["proxy_url"], save_url, help=t("proxy_url"),
                      width=280, placeholder="http://127.0.0.1:8080", mono=True)
        url_entry = getattr(ref, "widget")
        self._flushers.append(lambda: save_url(url_entry.get()))

        def test() -> None:
            if not save_url(url_entry.get()):
                return
            status.configure(text=t("testing"), text_color=T.TEXT_SECONDARY)

            def work() -> int:
                t0 = time.perf_counter()
                r = self.app.session().get("https://lrclib.net/api/search", params={"q": "hello adele"},
                                           timeout=(6, 15))
                r.raise_for_status()
                return int((time.perf_counter() - t0) * 1000)

            self._run_bg(work, lambda res: status.configure(
                text=t("conn_fail", err=str(res)[:200]) if isinstance(res, Exception) else t("conn_ok", ms=res),
                text_color=T.DANGER if isinstance(res, Exception) else T.SUCCESS))

        k.button(k.actions(g), t("test_connection"), test, kind="primary").pack(side=k.start)
        status.pack(fill="x", pady=(T.SPACE[3], 0))

    # -- About
    def _build_about(self, f: Any) -> None:
        k = self.kit
        assert k is not None
        g = k.group(f)
        head = ctk.CTkFrame(g, fg_color="transparent")
        head.pack(fill="x", pady=(T.SPACE[3], 0))
        try:
            from PIL import Image

            logo = ctk.CTkImage(Image.open(paths.ICON_PNG), size=(72, 72))
            ctk.CTkLabel(head, text="", image=logo).pack(side=k.start)
        except Exception:
            pass
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.pack(side=k.start, padx=T.SPACE[5])
        k.label(txt, "Dualyrics", "display").pack(fill="x")
        k.label(txt, t("about_version", v=__version__), "body", T.TEXT_SECONDARY).pack(fill="x")
        k.label(g, t("about_text", v=__version__, path=str(paths.data_dir())), "body", wrap=560).pack(
            fill="x", pady=(T.SPACE[5], 0))
        from dualyrics import REPO_URL

        if REPO_URL:
            link = k.label(g, REPO_URL, "body", T.ACCENT)
            link.configure(cursor="hand2")
            link.bind("<Button-1>", lambda _e: os.startfile(REPO_URL))
            link.pack(fill="x", pady=(T.SPACE[3], 0))
        acts = k.actions(g)
        k.button(acts, t("open_data"), lambda: os.startfile(paths.data_dir()), icon="folder").pack(side=k.start)
        k.button(acts, t("open_log"), lambda: os.startfile(paths.log_dir() / "dualyrics.log")).pack(side=k.start,
                                                                                                    padx=8)
        g = k.group(f, t("about_licences"))
        k.label(g, t("about_licences_text"), "caption", T.TEXT_SECONDARY, wrap=560).pack(fill="x")
        ofl = paths.ASSETS_DIR / "fonts" / "Vazirmatn-OFL.txt"
        if ofl.exists():
            k.button(k.actions(g), t("about_ofl"), lambda: os.startfile(ofl)).pack(side=k.start)
