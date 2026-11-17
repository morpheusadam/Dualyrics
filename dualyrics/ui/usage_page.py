"""Settings > Usage: what the Gemini keys used (today, last 7 days, this month), per model and per key, a
daily chart, today's share of each free-tier daily limit (editable estimates), per song or video, cache hits,
CSV export and reset (dualyrics/usage.py)."""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from dualyrics import usage as usage_mod
from dualyrics.i18n import t
from dualyrics.ui import tokens as T

if TYPE_CHECKING:
    from dualyrics.settings_window import SettingsWindow

PERIODS = ("today", "week", "month")
CHART_DAYS = 14
CHART_H = 140


def fmt_num(n: float) -> str:
    """12 345 -> '12.3k', 2 500 000 -> '2.5M'."""
    n = float(n)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e4:
        return f"{n / 1e3:.1f}k"
    return f"{int(n)}"


def fmt_audio(sec: float) -> str:
    return f"{sec / 60:.1f} min" if sec >= 60 else f"{int(sec)} s"


def build(win: "SettingsWindow", f: Any) -> None:
    k = win.kit
    assert k is not None
    s = win.s
    meter: usage_mod.UsageMeter = win.app.usage
    state = {"period": "today", "metric": "requests"}

    # ---- totals: three tiles
    g = k.group(f, t("us_totals"), t("us_totals_hint"))
    tiles = ctk.CTkFrame(g, fg_color="transparent")
    tiles.pack(fill="x", pady=(T.SPACE[3], 0))
    for i in range(3):
        tiles.grid_columnconfigure(i, weight=1, uniform="tile")
    for i, period in enumerate(PERIODS):
        tot = meter.totals(period)
        col = 2 - i if k.rtl else i
        tile = ctk.CTkFrame(tiles, fg_color=T.BG_RAISED, corner_radius=T.RADIUS_MD, border_width=1,
                            border_color=T.BORDER_SUBTLE)
        tile.grid(row=0, column=col, sticky="nsew", padx=4)
        k.label(tile, t(f"us_{period}"), "caption", T.TEXT_SECONDARY).pack(fill="x", padx=T.SPACE[4],
                                                                          pady=(T.SPACE[3], 0))
        k.label(tile, fmt_num(tot["requests"]), "title").pack(fill="x", padx=T.SPACE[4])
        k.label(tile, t("us_requests"), "caption", T.TEXT_MUTED).pack(fill="x", padx=T.SPACE[4])
        detail = t("us_tile_detail", tokens=fmt_num(tot["total"]), audio=fmt_audio(tot["audio_sec"]),
                   cached=tot["cache_hits"], limited=tot["rate_limited"])
        k.label(tile, detail, "caption", T.TEXT_SECONDARY, wrap=170, reserve=4).pack(
            fill="x", padx=T.SPACE[4], pady=(T.SPACE[2], T.SPACE[3]))

    # ---- daily chart
    g = k.group(f, t("us_daily"), t("us_daily_hint", days=CHART_DAYS))
    metric_holder = ctk.CTkFrame(g, fg_color="transparent")
    metric_holder.pack(fill="x", pady=(T.SPACE[3], 0))
    canvas = tk.Canvas(g, height=CHART_H + 28, highlightthickness=0, bd=0)
    canvas.pack(fill="x", pady=(T.SPACE[3], 0))

    def draw_chart(*_a: Any) -> None:
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        dark = ctk.get_appearance_mode() == "Dark"
        bg = T.BG_SURFACE[1] if dark else T.BG_SURFACE[0]
        canvas.configure(bg=bg)
        days = meter.daily(CHART_DAYS)
        key = state["metric"]
        vals = [d[key] for d in days]
        top = max(vals) or 1
        w = max(200, canvas.winfo_width())
        gap = 6
        bw = (w - gap * (len(days) + 1)) / len(days)
        accent = T.ACCENT[1] if dark else T.ACCENT[0]
        muted = T.TEXT_MUTED[1] if dark else T.TEXT_MUTED[0]
        order = list(range(len(days)))
        if k.rtl:
            order.reverse()
        for slot, idx in enumerate(order):
            v = vals[idx]
            x0 = gap + slot * (bw + gap)
            h = 0 if v == 0 else max(2, v / top * (CHART_H - 16))    # room for the value above the tallest bar
            canvas.create_rectangle(x0, CHART_H - h + 4, x0 + bw, CHART_H + 4, fill=accent, width=0)
            if v:
                canvas.create_text(x0 + bw / 2, CHART_H - h - 4, text=fmt_num(v), fill=muted, font=("Segoe UI", 8))
            canvas.create_text(x0 + bw / 2, CHART_H + 16, text=days[idx]["day"][8:], fill=muted, font=("Segoe UI", 8))

    k.segmented_in(metric_holder, [("requests", t("us_requests")), ("tokens", t("us_tokens")),
                                   ("audio_sec", t("us_audio"))],
                   lambda: state["metric"], lambda v: (state.update(metric=v), draw_chart()))
    canvas.bind("<Configure>", draw_chart)

    # ---- free-tier limits for today
    reset_at = usage_mod.quota_reset_local(time.time())
    g = k.group(f, t("us_limits"), t("us_limits_hint", time=reset_at))
    rows = meter.quota_today()
    models = sorted({r["model"] for r in rows} | set(s["gemini_models"]))
    if not rows:
        k.label(g, t("us_none_today"), "caption", T.TEXT_SECONDARY).pack(fill="x", pady=(T.SPACE[3], 0))
    for r in rows:
        line = ctk.CTkFrame(g, fg_color="transparent")
        line.pack(fill="x", pady=(T.SPACE[3], 0))
        warn = r["limit"] and r["share"] >= usage_mod.WARN_AT
        label = t("us_limit_row", model=r["model"], key=r["key"], used=r["used"],
                  limit=r["limit"] if r["limit"] else "?")
        if r["limited"]:
            label += "  " + t("us_limited_n", n=r["limited"])
        k.label(line, label, "body", T.WARNING if warn else T.TEXT_PRIMARY).pack(fill="x")
        bar = ctk.CTkProgressBar(line, height=8, corner_radius=4, progress_color=T.WARNING if warn else T.ACCENT,
                                 fg_color=T.BORDER_SUBTLE)
        bar.set(min(1.0, r["share"]) if r["limit"] else 0)
        bar.pack(fill="x", pady=(T.SPACE[1], 0))

    lg = k.group(f, t("us_edit_limits"), t("us_edit_limits_hint"))
    entries: dict[str, Any] = {}
    for m in models:
        def getter(m: str = m) -> str:
            return str(usage_mod.limit_for(m, s["usage_limits"]))

        def setter(v: str, m: str = m) -> None:
            try:
                n = int(float(str(v).strip()))
            except ValueError:
                entries[m]()
                return
            lim = dict(s["usage_limits"])
            if n == usage_mod.default_limit(m):
                lim.pop(m, None)
            else:
                lim[m] = n
            s.update(usage_limits=lim)

        entries[m] = k.entry(lg, m, getter, setter, help=t("us_default_limit", n=usage_mod.default_limit(m)),
                             width=90, mono=True)
        win._flushers.append(lambda r=entries[m]: r.widget.flush() if hasattr(r, "widget") else None)
    acts = k.actions(lg)
    k.button(acts, t("us_reset_limits"), lambda: (s.update(usage_limits={}), win.select("usage"))).pack(side=k.start)
    win.watch("usage_warn", k.switch(lg, t("us_warn"), *win.bind_key("usage_warn"), help=t("us_warn_hint")))

    # ---- per model / per key / per song or video
    g = k.group(f, t("us_breakdown"))
    holder = ctk.CTkFrame(g, fg_color="transparent")
    holder.pack(fill="x", pady=(T.SPACE[3], 0))
    table = ctk.CTkFrame(g, fg_color="transparent")
    table.pack(fill="x")

    def render_tables(*_a: Any) -> None:
        if not table.winfo_exists():
            return
        for w in table.winfo_children():
            w.destroy()
        p = state["period"]
        sections = ((t("us_by_model"), meter.by_model(p), lambda n: str(n)),
                    (t("us_by_key"), meter.by_key(p), lambda n: t("us_key_n", n=n)),
                    (t("us_by_media"), meter.by_media(p), lambda n: str(n)))
        for title, items, name_of in sections:
            k.label(table, title, "body.strong").pack(fill="x", pady=(T.SPACE[4], 0))
            if not items:
                k.label(table, t("us_nothing"), "caption", T.TEXT_MUTED).pack(fill="x")
                continue
            for it in items:
                txt = t("us_row", name=name_of(it["name"]), requests=it["requests"], tokens=fmt_num(it["tokens"]),
                        audio=fmt_audio(it["audio_sec"]), cached=it["cache_hits"])
                if it["rate_limited"]:
                    txt += "  " + t("us_limited_n", n=it["rate_limited"])
                k.label(table, txt, "caption", T.TEXT_SECONDARY, wrap=560).pack(fill="x")

    k.segmented_in(holder, [(p, t(f"us_{p}")) for p in PERIODS], lambda: state["period"],
                   lambda v: (state.update(period=v), render_tables()))
    render_tables()

    # ---- export / reset
    g = k.group(f, t("us_data"), t("us_data_hint", path=str(meter.path)))
    acts = k.actions(g)
    status = k.label(g, "", "caption", T.TEXT_SECONDARY)

    def export() -> None:
        path = filedialog.asksaveasfilename(parent=win.win, defaultextension=".csv", initialfile="dualyrics-usage.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            n = meter.export_csv(Path(path))
            status.configure(text=t("us_exported", n=n, name=Path(path).name))
        except OSError as e:
            status.configure(text=str(e))

    def reset() -> None:
        if messagebox.askyesno("Dualyrics", t("us_reset_confirm"), parent=win.win):
            meter.reset()
            win.select("usage")
            win.toast(t("us_reset_done"), "success")

    k.button(acts, t("us_export"), export, kind="primary", icon="export").pack(side=k.start)
    k.button(acts, t("us_reset"), reset, kind="danger", icon="trash").pack(side=k.start, padx=8)
    status.pack(fill="x", pady=(T.SPACE[3], 0))
