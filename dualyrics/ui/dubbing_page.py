"""Settings > Dubbing: speak the translation (on / off, what is spoken), the engine and voice, speed and volume,
ducking of other apps, the Gemini speech models, and installing the free voices (Edge, Piper)
(dualyrics/dub/, docs/ALGORITHM.md section 9)."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from dualyrics import langs
from dualyrics.dub import edge, piper, voices
from dualyrics.i18n import t
from dualyrics.ui import tokens as T

if TYPE_CHECKING:
    from dualyrics.settings_window import SettingsWindow

log = logging.getLogger(__name__)

ENGINE_LABELS = {"gemini_tts": "Gemini", "gemini_live": "Gemini Live", "edge": "Edge", "piper": "Piper"}
# the test line, in the target language (it is spoken with a voice of that language)
SAMPLES = {"en": "This is how the translation will sound.", "fa": "ترجمه این‌طور خوانده می‌شود.",
           "ar": "هكذا ستبدو الترجمة.", "tr": "Çeviri böyle duyulacak.", "de": "So klingt die Übersetzung.",
           "fr": "Voici comment la traduction sonnera.", "es": "Así sonará la traducción.",
           "it": "Ecco come suonerà la traduzione.", "pt": "É assim que a tradução vai soar.",
           "ru": "Так будет звучать перевод."}


def _lang_name(code: str) -> str:
    lg = langs.BY_CODE.get(code)
    return lg.name if lg else code


def build(win: "SettingsWindow", f: Any) -> None:
    k = win.kit
    assert k is not None
    s = win.s
    dub = getattr(win.app, "dub", None)
    target = win.effective_target()
    busy = {"edge": False, "piper": False, "test": False}

    # ---- on / off and what is spoken
    g = k.group(f, t("dp_main"))
    win.watch("dub_enabled", k.switch(g, t("dp_enabled"), *win.bind_key("dub_enabled"), help=t("dp_enabled_hint")))
    status = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)
    status.pack(fill="x", pady=(T.SPACE[3], 0))

    def show_status() -> None:
        if not status.winfo_exists():
            return
        if dub is not None and dub.status:
            status.configure(text=t("dp_status", what=t(dub.status)), text_color=T.WARNING)
        elif dub is not None and dub.last_engine:
            status.configure(text=t("dp_status", what=t("dp_status_engine", engine=ENGINE_LABELS.get(
                dub.last_engine, dub.last_engine))), text_color=T.TEXT_SECONDARY)
        else:
            status.configure(text=t("dp_status", what=t("dp_status_ok")), text_color=T.TEXT_SECONDARY)

    show_status()
    g = k.group(f, t("dp_what"))
    win.watch("dub_lyrics", k.switch(g, t("dp_lyrics"), *win.bind_key("dub_lyrics"), help=t("dp_lyrics_hint")))
    win.watch("dub_captions", k.switch(g, t("dp_captions"), *win.bind_key("dub_captions"),
                                       help=t("dp_captions_hint")))

    # ---- engine and voice
    g = k.group(f, t("dp_engine_group"))

    def set_engine(v: str) -> None:
        s.update(dub_engine=v)
        if engine_of_voice(s["dub_voice"]) not in ("", v) and v != "auto":
            s.update(dub_voice="auto")       # a voice of another engine would be ignored anyway
        win.select("dubbing")

    def engine_of_voice(v: str) -> str:
        e = voices.engine_of(v)
        return {"gemini": "gemini_tts"}.get(e, e)

    k.option(g, t("dp_engine"), [(e, t(f"dp_eng_{e}")) for e in voices.ENGINES], lambda: s["dub_engine"],
             set_engine, help=t("dp_engine_hint"), width=300)
    eng = s["dub_engine"]
    if eng == "auto":
        eng = "gemini_tts" if s["gemini_keys"] else ("edge" if edge.available() else "piper")
    names = voices.choices(eng, target)
    if s["dub_voice"] not in names:
        names.append(s["dub_voice"])
    win.watch("dub_voice", k.option(g, t("dp_voice"), [(v, t("dp_voice_auto") if v == "auto" else v) for v in names],
                                    *win.bind_key("dub_voice"), help=t("dp_voice_hint", lang=_lang_name(target)),
                                    width=300))
    lg = voices.base_lang(target)

    def cur_gender() -> str:
        return voices.gender_for({"dub_gender": s["dub_gender"]}, target)

    def gender_label(gender: str) -> str:
        name = voices.short_name(voices.auto_voice(eng, target, gender))
        word = t(f"dp_gender_{gender}")
        return f"{word} · {name}" if name else word

    def set_gender(v: str) -> None:
        before = voices.piper_voice(s["dub_voice"], target, cur_gender())
        genders = dict(s["dub_gender"])
        genders[lg] = v
        s.update(dub_gender=genders)
        if voices.piper_voice(s["dub_voice"], target, v) != before:
            win.select("dubbing")            # the Piper download row below is for the other voice now

    win.watch("dub_gender", k.segmented(g, t("dp_gender", lang=_lang_name(target)),
                                        [(gd, gender_label(gd)) for gd in voices.GENDERS],
                                        cur_gender, set_gender,
                                        help=t("dp_gender_hint")))
    win.watch("dub_speed", k.slider(g, t("dp_speed"), 0.5, 2.0, *win.bind_key("dub_speed"), 0.05, "×",
                                    fmt=lambda v: f"{v:.2f}"))
    win.watch("dub_volume", k.slider(g, t("dp_volume"), 0, 100, *win.bind_key("dub_volume"), 1, "%"))
    if eng.startswith("gemini") and not s["gemini_keys"]:
        k.note(g, t("dp_no_key"), "warning")
    acts = k.actions(g)
    test_btn = k.button(acts, t("dp_test"), lambda: test(), icon="play")
    test_btn.pack(side=k.start)

    def test() -> None:
        if dub is None or busy["test"]:
            return
        busy["test"] = True
        cfg = win.app.effective_cfg()
        text = SAMPLES.get(voices.base_lang(target), SAMPLES["en"])

        def work() -> bool:
            return bool(dub.test(text, target, cfg))

        def done(res: Any) -> None:
            busy["test"] = False
            if res is not True:
                win.toast(t("dp_test_fail"), "warning")
            show_status()

        win._run_bg(work, done)

    # ---- ducking
    g = k.group(f, t("dp_duck_group"))
    win.watch("dub_duck", k.switch(g, t("dp_duck"), *win.bind_key("dub_duck"), help=t("dp_duck_hint")))
    win.watch("dub_duck_amount", k.slider(g, t("dp_duck_amount"), 0, 100, *win.bind_key("dub_duck_amount"), 5, "%",
                                          indent=True))

    # ---- Gemini models
    g = k.group(f, t("dp_gemini_group"), t("dp_gemini_hint"))
    disc = getattr(dub, "discovery", None)
    for key, label, found in (("dub_gemini_tts_model", t("dp_tts_model"), list(getattr(disc, "tts", []) or [])),
                              ("dub_gemini_live_model", t("dp_live_model"), list(getattr(disc, "live", []) or []))):
        opts = ["auto"] + found
        if s[key] not in opts:
            opts.append(s[key])
        win.watch(key, k.option(g, label, [(m, t("dp_model_auto") if m == "auto" else m) for m in opts],
                                *win.bind_key(key), width=320))

    # ---- free voices: Edge (pip package) and Piper (download)
    g = k.group(f, t("dp_install_group"))
    row = k.row(g, t("dp_edge"), t("dp_edge_hint"))
    edge_log = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)
    if edge.available():
        k.label(row, t("dp_installed"), "body", T.SUCCESS).pack(side=k.end)
    else:
        def install_edge() -> None:
            if busy["edge"]:
                return
            busy["edge"] = True
            edge_log.configure(text=t("dp_installing"))
            cfg = s.snapshot()
            ep = win.epoch

            def work() -> tuple[bool, str]:
                from dualyrics.live import deps

                return deps.install(False, cfg["proxy_mode"], cfg["proxy_url"], packages={
                    edge.PACKAGE: edge.REQUIREMENT}, on_line=lambda line: win.ui(
                        lambda: edge_log.configure(text=line), ep))

            def done(res: Any) -> None:
                busy["edge"] = False
                ok, msg = res if isinstance(res, tuple) else (False, str(res))
                if ok:
                    win.select("dubbing")
                    win.toast(t("dp_install_ok"), "success")
                elif edge_log.winfo_exists():
                    edge_log.configure(text=t("dp_install_fail", err=msg[:300]))

            win._run_bg(work, done)

        k.button(row, t("dp_install"), install_edge, icon="import").pack(side=k.end)
    edge_log.pack(fill="x")

    voice_path = voices.piper_voice(s["dub_voice"], target, cur_gender())
    if voice_path is None:
        k.label(g, t("dp_piper_none", lang=_lang_name(target)), "caption", T.TEXT_SECONDARY).pack(
            fill="x", pady=(T.SPACE[3], 0))
        return
    row = k.row(g, t("dp_piper", lang=_lang_name(target)), t("dp_piper_hint"))
    piper_log = k.label(g, "", "caption", T.TEXT_SECONDARY, wrap=560)
    if piper.installed() and piper.voice_installed(voice_path):
        k.label(row, t("dp_installed"), "body", T.SUCCESS).pack(side=k.end)
    else:
        cancel = threading.Event()
        f.bind("<Destroy>", lambda _e: cancel.set(), add="+")    # leaving the page stops the download

        def install_piper() -> None:
            if busy["piper"]:
                return
            busy["piper"] = True
            piper_log.configure(text=t("dp_installing"))
            ep = win.epoch

            def progress(done: int, total: int) -> None:
                if total:
                    txt = t("dp_progress", done=f"{done / 1e6:.1f}", total=f"{total / 1e6:.1f}")
                    win.ui(lambda: piper_log.configure(text=txt), ep)

            def work() -> tuple[bool, str]:
                try:
                    piper.install(win.app.session(), voice_path, progress, cancel.is_set)
                except Exception as e:  # noqa: BLE001 - shown to the user
                    log.info("piper install failed: %s", e)
                    return False, str(e)
                return True, ""

            def done(res: Any) -> None:
                busy["piper"] = False
                ok, msg = res if isinstance(res, tuple) else (False, str(res))
                if ok:
                    win.select("dubbing")
                    win.toast(t("dp_install_ok"), "success")
                elif piper_log.winfo_exists():
                    piper_log.configure(text=t("dp_install_fail", err=msg[:300]))

            win._run_bg(work, done)

        k.button(row, t("dp_install"), install_piper, icon="import").pack(side=k.end)
    piper_log.pack(fill="x")
