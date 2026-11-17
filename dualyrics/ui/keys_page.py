"""Settings > Translation, v2.2 parts: the state of every Gemini key (round robin pool, dualyrics/keypool.py) with
its switch, Test button and today's use, the shared-project warning, and the offline translator (dualyrics/
offline.py) with its language packages and their licences."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from dualyrics import keypool, offline, usage as usage_mod
from dualyrics.i18n import t
from dualyrics.translate import mask_key
from dualyrics.ui import tokens as T

if TYPE_CHECKING:
    from dualyrics.settings_window import SettingsWindow

log = logging.getLogger(__name__)

REFRESH_MS = 4000
STATE_COLORS = {keypool.OK: T.SUCCESS, keypool.LIMITED: T.WARNING, keypool.EXHAUSTED: T.WARNING,
                keypool.COOLING: T.TEXT_SECONDARY, keypool.INVALID: T.DANGER, keypool.OFF: T.TEXT_MUTED}


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts and ts != float("inf") else "-"


def state_text(entry: dict[str, Any]) -> str:
    """The state of one key for its row: ok, rate limited until, daily quota used up until, rejected, off."""
    st, until = entry["state"], entry["until"]
    if st == keypool.OK:
        busy = [m for m, s in entry["models"].items() if s != keypool.OK]
        return t("kp_ok_some", n=len(busy)) if busy else t("kp_ok")
    if st == keypool.LIMITED:
        return t("kp_limited", at=_clock(until))
    if st == keypool.EXHAUSTED:
        return t("kp_exhausted", at=_clock(until))
    if st == keypool.COOLING:
        return t("kp_cooling", at=_clock(until))
    if st == keypool.INVALID:
        return t("kp_invalid")
    return t("kp_off")


def usage_text(row: dict[str, Any] | None) -> str:
    if not row:
        return t("kp_usage", requests=0, tokens="0")
    tok = row["tokens"]
    tokens = f"{tok / 1000:.1f}k" if tok >= 1000 else str(tok)
    return t("kp_usage", requests=row["requests"], tokens=tokens)


def shared_text(groups: list[list[int]]) -> str:
    return " ".join(t("kp_shared", keys=", ".join(f"#{i}" for i in g)) for g in groups)


# ---------------------------------------------------------------- Gemini keys
def build_keys(win: "SettingsWindow", g: Any) -> None:
    """The per-key list under the keys box (rebuilt when the keys change, states refreshed every few seconds)."""
    k = win.kit
    assert k is not None
    app = win.app
    pool: keypool.KeyPool | None = getattr(getattr(getattr(app, "translator", None), "gemini", None), "pool", None)
    if pool is None:
        return
    holder = ctk.CTkFrame(g, fg_color="transparent")
    holder.pack(fill="x", pady=(T.SPACE[4], 0))
    rows: list[dict[str, Any]] = []
    shared_lbl: list[Any] = []

    def rebuild() -> None:
        if not holder.winfo_exists():
            return
        for w in holder.winfo_children():
            w.destroy()
        rows.clear()
        shared_lbl.clear()
        keys = win.s["gemini_keys"]
        if not keys:
            return
        k.label(holder, t("kp_title"), "body.strong").pack(fill="x")
        k.label(holder, t("kp_hint", at=quota_reset_clock()), "caption", T.TEXT_SECONDARY, wrap=560).pack(fill="x", pady=(T.SPACE[1], 0))
        warn = k.label(holder, "", "caption", T.WARNING, wrap=560)
        warn.pack(fill="x", pady=(T.SPACE[2], 0))
        shared_lbl.append(warn)
        for i, key in enumerate(keys):
            fp = keypool.fingerprint(key)
            r = ctk.CTkFrame(holder, fg_color=T.BG_RAISED, corner_radius=T.RADIUS_MD, border_width=1,
                             border_color=T.BORDER_SUBTLE)
            r.pack(fill="x", pady=(T.SPACE[3], 0))
            top = ctk.CTkFrame(r, fg_color="transparent")
            top.pack(fill="x", padx=T.SPACE[4], pady=(T.SPACE[3], 0))
            k.label(top, f"#{i + 1}  {mask_key(key)}", "body").pack(side=k.start)
            var = tk_bool(fp not in win.s["gemini_keys_off"])

            def toggle(fp: str = fp, var: Any = var) -> None:
                off = [x for x in win.s["gemini_keys_off"] if x != fp]
                if not var.get():
                    off.append(fp)
                win.s.update(gemini_keys_off=off)

            sw = ctk.CTkSwitch(top, text="", variable=var, width=44, switch_width=40, switch_height=20,
                               progress_color=T.ACCENT, fg_color=T.BORDER_STRONG, button_color=T.TEXT_PRIMARY,
                               button_hover_color=T.TEXT_PRIMARY, command=toggle)
            sw.pack(side=k.end)
            result = k.label(r, "", "caption", T.TEXT_SECONDARY, wrap=520)

            def test(key: str = key, result: Any = result) -> None:
                result.configure(text=t("testing"))
                models = win.s["gemini_models"]

                def show(res: Any) -> None:
                    if not result.winfo_exists():
                        return
                    if isinstance(res, Exception):
                        result.configure(text=str(res)[:300])
                    else:
                        result.configure(text="\n".join(f"{'OK' if ok else 'X'}  {m}: {msg}" for m, ok, msg in res))
                    refresh()

                win._run_bg(lambda: app.translator.test_key(key, models), show)

            k.button(top, t("test"), test, width=72).pack(side=k.end, padx=T.SPACE[3])
            state = k.label(r, "", "caption", T.TEXT_SECONDARY, wrap=520)
            state.pack(fill="x", padx=T.SPACE[4], pady=(T.SPACE[1], 0))
            used = k.label(r, "", "caption", T.TEXT_MUTED, wrap=520)
            used.pack(fill="x", padx=T.SPACE[4])
            result.pack(fill="x", padx=T.SPACE[4], pady=(0, T.SPACE[3]))
            rows.append({"state": state, "used": used, "index": i + 1})
        refresh()

    def refresh() -> None:
        if not holder.winfo_exists() or not rows:
            return
        keys, models = win.s["gemini_keys"], win.s["gemini_models"]
        summary = pool.summary(keys, models)
        meter = getattr(app, "usage", None)
        per_key = {int(r["name"]): r for r in meter.by_key("today")} if meter is not None else {}
        for row, entry in zip(rows, summary):
            row["state"].configure(text=state_text(entry), text_color=STATE_COLORS.get(entry["state"], T.TEXT_SECONDARY))
            row["used"].configure(text=usage_text(per_key.get(entry["index"])))
        groups = pool.shared_groups(keys)
        if shared_lbl:
            shared_lbl[0].configure(text=shared_text(groups))

    def tick() -> None:
        if not holder.winfo_exists():
            return
        try:
            refresh()
        except Exception:  # noqa: BLE001 - a refresh never breaks the page
            log.debug("key list refresh failed", exc_info=True)
        holder.after(REFRESH_MS, tick)

    win.watch("gemini_keys", rebuild)
    win.watch("gemini_keys_off", refresh)
    win.watch("gemini_models", refresh)
    rebuild()
    holder.after(REFRESH_MS, tick)


def tk_bool(value: bool) -> Any:
    import tkinter as tk

    return tk.BooleanVar(value=value)


# ---------------------------------------------------------------- offline translator
def package_label(p: Any) -> str:
    return f"{p.from_name} → {p.to_name}" if hasattr(p, "from_name") else f"{p.from_code} → {p.to_code}"


def build_offline(win: "SettingsWindow", f: Any) -> None:
    k = win.kit
    assert k is not None
    s = win.s
    app = win.app
    tr = getattr(app, "offline", None)
    g = k.group(f, t("of_group"), t("of_hint"))
    win.watch("offline_enabled", k.switch(g, t("of_enabled"), *win.bind_key("offline_enabled"), help=t("of_enabled_hint")))
    log_lbl = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)
    busy = {"on": False}
    cancel = threading.Event()
    f.bind("<Destroy>", lambda _e: cancel.set(), add="+")    # leaving the page stops a download

    page = win.epoch

    def say(text: str) -> None:
        win.ui(lambda: log_lbl.configure(text=text), page)

    missing = offline.deps_missing()
    if missing:
        row = k.row(g, t("of_deps"), t("of_deps_hint", pkgs=", ".join(missing)))

        def install() -> None:
            if busy["on"]:
                return
            busy["on"] = True
            log_lbl.configure(text=t("dp_installing"))
            cfg = s.snapshot()

            def work() -> tuple[bool, str]:
                from dualyrics.live import deps

                return deps.install(False, cfg["proxy_mode"], cfg["proxy_url"], packages=dict(offline.DEPS),
                                    on_line=lambda line: say(line))

            def finished(res: Any) -> None:     # also when Settings was closed meanwhile
                if isinstance(res, tuple) and res[0] and tr is not None:
                    tr.refresh()

            def done(res: Any) -> None:
                busy["on"] = False
                ok, msg = res if isinstance(res, tuple) else (False, str(res))
                if ok:
                    win.select("translation")
                    win.toast(t("dp_install_ok"), "success")
                elif log_lbl.winfo_exists():
                    log_lbl.configure(text=t("dp_install_fail", err=msg[:300]))

            win._run_bg(work, done, after=finished)

        k.button(row, t("dp_install"), install, icon="import").pack(side=k.end)

    # installed packages, with their licences
    pkgs = offline.installed_packages()
    k.label(g, t("of_installed"), "body.strong").pack(fill="x", pady=(T.SPACE[5], 0))
    if not pkgs:
        k.label(g, t("of_none"), "caption", T.TEXT_SECONDARY, wrap=560).pack(fill="x", pady=(T.SPACE[1], 0))
    for p in pkgs:
        row = k.row(g, f"{p.from_code} → {p.to_code}  ·  v{p.version or '?'}",
                    t("of_license", lic=p.license or t("of_license_unknown"))
                    + ("" if p.supported else "  " + t("of_unsupported")))

        def remove(p: Any = p) -> None:
            if tr is not None:
                tr.refresh()               # drop the loaded model first, so its files are not in use
            offline.remove(p.path.name)
            if tr is not None:
                tr.refresh()
            win.select("translation")

        k.button(row, t("of_remove"), remove, kind="danger").pack(side=k.end)
        readme = p.path / "README.md"
        if readme.is_file():
            k.button(row, "README", lambda r=readme: os.startfile(r), kind="ghost").pack(side=k.end, padx=T.SPACE[2])

    # packages to download (from the Argos Translate index)
    k.label(g, t("of_get"), "body.strong").pack(fill="x", pady=(T.SPACE[5], 0))
    target = win.effective_target()
    pick_row = ctk.CTkFrame(g, fg_color="transparent")
    pick_row.pack(fill="x", pady=(T.SPACE[2], 0))
    lic_lbl = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)
    choice: dict[str, Any] = {"list": [], "pick": None}
    have = {p.path.name for p in pkgs}

    def fill(index: list[offline.PackageInfo]) -> None:
        if not pick_row.winfo_exists():
            return
        for w in pick_row.winfo_children():
            w.destroy()
        tn = offline.norm_code(target)
        # pairs with the target language (or English, for the route through English) first
        useful = [p for p in index if p.dir_name not in have
                  and (offline.norm_code(p.to_code) in (tn, "en") or offline.norm_code(p.from_code) == tn)]
        useful.sort(key=lambda p: (0 if offline.norm_code(p.to_code) == tn else 1 if offline.norm_code(p.from_code) == tn
                                   else 2, p.from_name, p.to_name))
        choice["list"] = useful
        if not useful:
            k.label(pick_row, t("of_none_left"), "caption", T.TEXT_SECONDARY).pack(side=k.start)
            return
        names = [package_label(p) for p in useful]
        var = ctk.StringVar(value=names[0])

        def picked(name: str) -> None:
            p = useful[names.index(name)] if name in names else None
            choice["pick"] = p
            lic_lbl.configure(text=t("of_license", lic=p.license) if p and p.license else t("of_license_after"))

        menu = ctk.CTkOptionMenu(pick_row, values=names, variable=var, command=picked, width=300,
                                 fg_color=T.BG_RAISED, button_color=T.BG_RAISED, button_hover_color=T.BG_HOVER,
                                 text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_RAISED,
                                 dropdown_text_color=T.TEXT_PRIMARY, dropdown_hover_color=T.BG_HOVER)
        menu.pack(side=k.start)
        picked(names[0])

        def download() -> None:
            p = choice["pick"]
            if p is None or busy["on"]:
                return
            busy["on"] = True
            log_lbl.configure(text=t("dp_installing"))

            def progress(done: int, total: int) -> None:
                if total:
                    say(t("dp_progress", done=f"{done / 1e6:.1f}", total=f"{total / 1e6:.1f}"))
                else:
                    say(t("of_progress", done=f"{done / 1e6:.1f}"))

            def work() -> Any:
                return offline.download(p, app.session(), progress, cancel.is_set)

            def finished(res: Any) -> None:     # also when Settings was closed meanwhile
                if not isinstance(res, Exception) and tr is not None:
                    tr.refresh()

            def done(res: Any) -> None:
                busy["on"] = False
                if isinstance(res, Exception):
                    if log_lbl.winfo_exists():
                        log_lbl.configure(text=t("dp_install_fail", err=str(res)[:300]))
                    return
                win.select("translation")
                win.toast(t("of_installed_ok", lic=res.license or t("of_license_unknown")), "success")

            win._run_bg(work, done, after=finished)

        k.button(pick_row, t("of_download"), download, icon="import").pack(side=k.start, padx=T.SPACE[3])

    def load() -> None:
        log_lbl.configure(text=t("of_loading"))

        def done(res: Any) -> None:
            if isinstance(res, Exception):
                if log_lbl.winfo_exists():
                    log_lbl.configure(text=t("of_index_fail", err=str(res)[:200]))
                return
            if log_lbl.winfo_exists():
                log_lbl.configure(text="")
            fill(res)

        win._run_bg(lambda: offline.fetch_index(app.session()), done)

    cached = offline.cached_index()
    if cached:
        fill(cached)
    else:
        k.button(pick_row, t("of_load"), load, icon="retranslate").pack(side=k.start)
    lic_lbl.pack(fill="x", pady=(T.SPACE[2], 0))
    log_lbl.pack(fill="x", pady=(T.SPACE[2], 0))
    k.label(g, t("of_credit"), "caption", T.TEXT_MUTED, wrap=560).pack(fill="x", pady=(T.SPACE[3], 0))


def quota_reset_clock() -> str:
    return usage_mod.quota_reset_local(time.time())
