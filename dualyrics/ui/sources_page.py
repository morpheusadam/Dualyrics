"""Settings > Sources: the order of the sources (with up / down and on / off), globally or per app profile,
strict lyrics matching for browsers, the on-screen subtitle readers (UI Automation, OCR, the subtitle area
picker and a test), and the English pivot for any language."""

from __future__ import annotations

import threading
import tkinter as tk
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from dualyrics import profiles as prof_mod, sources as sources_mod
from dualyrics.i18n import t
from dualyrics.ui import tokens as T

if TYPE_CHECKING:
    from dualyrics.settings_window import SettingsWindow

ICONS = {"lyrics": "lyrics", "screen": "monitors", "audio": "live"}


def _ocr_languages() -> list[tuple[str, str]]:
    """Installed Windows OCR languages, asked on a short-lived thread (WinRT sets up its apartment there,
    not on the Tk thread)."""
    out: list[tuple[str, str]] = []

    def work() -> None:
        try:
            from dualyrics.screen import ocr

            out.extend(ocr.languages())
        except Exception:
            pass

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(3.0)
    return list(out)


def build(win: "SettingsWindow", f: Any) -> None:
    k = win.kit
    assert k is not None
    s = win.s
    scope = {"id": ""}           # "" = all apps, else a profile id

    def profile() -> dict[str, Any] | None:
        return next((p for p in s["profiles"] if p["id"] == scope["id"]), None) if scope["id"] else None

    def set_profile(**changes: Any) -> None:
        profs = s["profiles"]
        for p in profs:
            if p["id"] == scope["id"]:
                p.update(changes)
        s.update(profiles=profs)

    def prof_name(p: dict[str, Any]) -> str:
        return t(f"ap_builtin_{p['id']}") if p.get("builtin") else p.get("name", p["id"])

    # ---- scope
    g = k.group(f)
    scope_opts = [("", t("src_scope_global"))] + [(p["id"], prof_name(p)) for p in s["profiles"]
                                                   if p["id"] != prof_mod.DEFAULT_ID]
    custom_row: dict[str, Any] = {}

    def set_scope(v: str) -> None:
        scope["id"] = v
        render()

    k.option(g, t("src_scope"), scope_opts, lambda: scope["id"], set_scope, help=t("src_scope_hint"), width=280)
    custom_holder = ctk.CTkFrame(g, fg_color="transparent", height=1)   # empty for "All apps": no 200 px gap
    custom_holder.pack(fill="x")

    # ---- order
    og = k.group(f, t("src_order"), t("src_order_hint"))
    box = ctk.CTkFrame(og, fg_color="transparent")
    box.pack(fill="x", pady=(T.SPACE[3], 0))

    def current() -> tuple[list[str], list[str], bool]:
        """(order, off, editable) for the scope."""
        p = profile()
        if p is not None and p.get("sources_custom"):
            return sources_mod.validate_order(p.get("source_order") or s["source_order"]), list(
                p.get("sources_off") or []), True
        if p is not None:
            return list(s["source_order"]), list(s["sources_off"]), False
        return list(s["source_order"]), list(s["sources_off"]), True

    def store(order: list[str], off: list[str]) -> None:
        if scope["id"]:
            set_profile(source_order=order, sources_off=off, sources_custom=True)
        else:
            s.update(source_order=order, sources_off=off)

    def render(*_a: Any) -> None:
        if not box.winfo_exists():
            return
        for w in custom_holder.winfo_children():
            w.destroy()
        p = profile()
        if p is not None:
            custom_row["refresh"] = k.switch(custom_holder, t("src_custom"), lambda: bool(
                (profile() or {}).get("sources_custom")), lambda v: set_profile(
                    sources_custom=bool(v), source_order=(profile() or {}).get("source_order") or list(
                        s["source_order"]), sources_off=(profile() or {}).get("sources_off") or list(
                        s["sources_off"])), help=t("src_custom_hint"))
        for w in box.winfo_children():
            w.destroy()
        order, off, editable = current()
        for i, sid in enumerate(order):
            row = ctk.CTkFrame(box, fg_color=T.BG_SURFACE, corner_radius=T.RADIUS_MD, border_width=1,
                               border_color=T.BORDER_SUBTLE)
            row.pack(fill="x", pady=3)
            start = ctk.CTkFrame(row, fg_color="transparent")
            start.pack(side=k.start, fill="x", expand=True, padx=T.SPACE[4], pady=T.SPACE[3])
            k.label(start, f"{i + 1}", "subtitle", T.ACCENT).pack(side=k.start, padx=(0, 8) if not k.rtl else (8, 0))
            ctk.CTkLabel(start, text="", image=k.icon(ICONS[sid], 20, T.TEXT_SECONDARY), width=24).pack(side=k.start)
            txt = ctk.CTkFrame(start, fg_color="transparent")
            txt.pack(side=k.start, padx=T.SPACE[3], fill="x", expand=True)
            on = sid not in off
            k.label(txt, t(f"src_{sid}"), "body", T.TEXT_PRIMARY if on and editable else T.TEXT_DISABLED).pack(
                fill="x")
            k.label(txt, t(f"src_{sid}_desc"), "caption", T.TEXT_MUTED, wrap=380, reserve=4).pack(fill="x")
            end = ctk.CTkFrame(row, fg_color="transparent")
            end.pack(side=k.end, padx=T.SPACE[3])
            state = "normal" if editable else "disabled"

            def moved(sid: str = sid, d: int = 0) -> None:
                o, of, _e = current()
                store(sources_mod.move(o, sid, d), of)

            def toggled(sid: str = sid, var: Any = None) -> None:
                o, of, _e = current()
                of = [x for x in of if x != sid] if var.get() else of + [sid]
                store(o, of)

            var = tk.BooleanVar(value=on)
            ctk.CTkSwitch(end, text="", variable=var, width=44, switch_width=40, switch_height=20,
                          progress_color=T.ACCENT, fg_color=T.BORDER_STRONG, state=state,
                          command=lambda sid=sid, var=var: toggled(sid, var)).pack(side=k.end, padx=6)
            down = k.button(end, "▼", lambda sid=sid: moved(sid, 1), kind="ghost", width=36)
            down.configure(state=state if i < len(order) - 1 else "disabled")
            down.pack(side=k.end, padx=2)
            up = k.button(end, "▲", lambda sid=sid: moved(sid, -1), kind="ghost", width=36)
            up.configure(state=state if i > 0 else "disabled")
            up.pack(side=k.end, padx=2)
            win._tooltip(up, t("src_up"))      # noqa: SLF001
            win._tooltip(down, t("src_down"))  # noqa: SLF001
        refresh_region()

    # ---- lyrics matching
    lg = k.group(f, t("src_grp_lyrics"))
    win.watch("lyrics_strict_browsers", k.switch(lg, t("src_strict"), *win.bind_key("lyrics_strict_browsers"),
                                                 help=t("src_strict_hint")))
    win.watch("lyrics_strict_tolerance", k.slider(lg, t("src_tolerance"), 1, 20,
                                                  *win.bind_key("lyrics_strict_tolerance"), 1, "s", indent=True))

    # ---- on-screen subtitles
    sg = k.group(f, t("src_grp_screen"), t("src_grp_screen_hint"))
    win.watch("screen_uia", k.switch(sg, t("src_uia"), *win.bind_key("screen_uia"), help=t("src_uia_hint")))
    win.watch("screen_ocr", k.switch(sg, t("src_ocr"), *win.bind_key("screen_ocr"), help=t("src_ocr_hint")))
    lang_opts: list[tuple[str, str]] = [("auto", t("src_ocr_auto"))] + _ocr_languages()
    if s["ocr_language"] not in [v for v, _l in lang_opts]:
        lang_opts.append((s["ocr_language"], s["ocr_language"]))
    win.watch("ocr_language", k.option(sg, t("src_ocr_lang"), lang_opts, *win.bind_key("ocr_language"),
                                       help=t("src_ocr_lang_hint"), width=280, indent=True))
    win.watch("ocr_fps", k.slider(sg, t("src_fps"), 1, 6, *win.bind_key("ocr_fps"), 0.5, indent=True))

    holder = k.row(sg, t("src_region"), indent=True)
    region_lbl = k.label(holder, "", "caption", T.TEXT_SECONDARY)
    region_lbl.pack(side=k.start)

    def region_now() -> tuple[list[float], bool]:
        p = profile()
        if p is not None and p.get("ocr_region"):
            return sources_mod.validate_region(p["ocr_region"]), True
        return sources_mod.validate_region(s["ocr_region"]), False

    def refresh_region(*_a: Any) -> None:
        if not region_lbl.winfo_exists():
            return
        (x0, y0, x1, y1), own = region_now()
        region_lbl.configure(text=t("src_region_value", x0=round(x0 * 100), x1=round(x1 * 100), y0=round(y0 * 100),
                                    y1=round(y1 * 100), scope=t("src_region_own") if own else t("src_region_global")))

    acts = k.actions(sg)
    msg = k.label(sg, "", "caption", T.TEXT_SECONDARY, wrap=560)

    def picked_window(then: Any) -> None:
        from dualyrics import procs

        msg.configure(text=t("src_pick_hint"))
        win._run_bg(lambda: procs.pick_window(20.0, cancelled=lambda: win.win is None), then)  # noqa: SLF001

    def pick() -> None:
        def got(app: Any) -> None:
            if app is None or isinstance(app, Exception) or not getattr(app, "hwnd", 0):
                msg.configure(text=t("ap_pick_none"))
                return
            msg.configure(text="")
            from dualyrics.ui.region_picker import pick_region

            def done(region: list[float] | None) -> None:
                if region is None:
                    return
                if scope["id"]:
                    set_profile(ocr_region=region)
                    name = prof_name(profile() or {"id": scope["id"]})
                else:
                    s.update(ocr_region=region)
                    name = t("src_scope_global")
                refresh_region()
                win.toast(t("rp_saved", scope=name), "success")

            if not pick_region(win.win, app.hwnd, region_now()[0], done):
                msg.configure(text=t("ap_pick_none"))

        picked_window(got)

    def reset() -> None:
        if scope["id"]:
            set_profile(ocr_region=[])
        else:
            s.update(ocr_region=list(sources_mod.DEFAULT_OCR_REGION))
        refresh_region()

    def test() -> None:
        def got(app: Any) -> None:
            if app is None or isinstance(app, Exception) or not getattr(app, "hwnd", 0):
                msg.configure(text=t("ap_pick_none"))
                return
            msg.configure(text=t("src_test_running"))
            from dualyrics import profiles
            from dualyrics.screen.engine import probe_window

            cfg = s.snapshot()
            match = profiles.match_profile(cfg["profiles"], app.ref)
            region = sources_mod.region_of(cfg, profile() or match.profile)
            kind = match.profile.get("kind", "other")

            def show(res: Any) -> None:
                if isinstance(res, Exception):
                    msg.configure(text=str(res)[:300])
                    return

                def fmt(v: Any) -> str:
                    return t("src_test_na") if v is None else (v.strip() or t("src_test_none"))

                text = t("src_test_result", uia=fmt(res.get("uia")), ocr=fmt(res.get("ocr")),
                         lang=res.get("ocr_language") or "-")
                if res.get("error"):
                    text += f"\n{res['error']}"
                msg.configure(text=text)

            win._run_bg(lambda: probe_window(app.hwnd, region, cfg, kind), show)  # noqa: SLF001

        picked_window(got)

    k.button(acts, t("src_pick"), pick, kind="primary", icon="target").pack(side=k.start)
    k.button(acts, t("src_reset_region"), reset, icon="retranslate").pack(side=k.start, padx=8)
    k.button(acts, t("src_test"), test, icon="eye").pack(side=k.start)
    msg.pack(fill="x", pady=(T.SPACE[3], 0))
    refresh = k.entry(sg, t("src_uia_classes"), lambda: ", ".join(s["screen_uia_classes"]),
                      lambda v: s.update(screen_uia_classes=v), help=t("src_uia_classes_hint"), width=320)
    win._flushers.append(getattr(refresh, "widget").flush)  # noqa: SLF001
    win.watch("screen_uia_classes", refresh)

    # ---- any language
    pg = k.group(f, t("src_grp_pivot"), t("src_grp_pivot_hint"))
    win.watch("pivot_english", k.switch(pg, t("src_pivot"), *win.bind_key("pivot_english"), help=t("src_pivot_hint")))
    win.watch("pivot_show_original", k.switch(pg, t("src_show_original"), *win.bind_key("pivot_show_original"),
                                              help=t("src_show_original_hint")))
    k.note(pg, t("src_pivot_note"))

    for key in ("profiles", "source_order", "sources_off", "ocr_region"):
        win.watch(key, render)
    render()
