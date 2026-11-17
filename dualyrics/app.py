"""Application wiring: media watcher -> lyrics -> translation -> overlay, plus tray, hotkeys and settings."""

from __future__ import annotations

import argparse
import bisect
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from dualyrics import (APP_ID, __version__, automode, cache as cache_mod, fonts, i18n, langs, net, paths, procs,
                       profiles as profiles_mod, startup, sync, templates as templates_mod, winutil)
from dualyrics.hotkeys import HotkeyManager
from dualyrics.live import deps as live_deps
from dualyrics.live.engine import LiveEngine
from dualyrics.lyrics import LyricsClient, LyricsResult
from dualyrics.media import MediaWatcher, Track
from dualyrics.overlay import Content, Overlay
from dualyrics.settings import Settings
from dualyrics.translate import TranslationError, Translator

log = logging.getLogger("dualyrics")

LEAD = 0.20                 # show a line slightly before it is sung (seconds)
TICK_MS = 100
NETWORK_RETRY = 30          # seconds before retrying a lyrics lookup that failed on the network
TRANSLATE_RETRY = 90        # seconds before retrying a failed translation (max 3 times)
NOT_FOUND_SHOW = 8          # seconds the "no lyrics" notice stays visible
FG_POLL = 0.5               # seconds between foreground-window checks
RUN_POLL = 3.0              # seconds between running-process scans
PROFILE_HOLD = 1.0          # a new profile must stay matched this long before it applies (alt-tab)
LIVE_STOP_GRACE = 8.0       # keep capturing this long after Live is no longer wanted (brief flips)
LIVE_UNLOAD = 600.0         # free the Whisper model after this long without Live
DEPS_CHECK = 5.0            # seconds between checks whether the Live mode packages are installed

TRANSLATION_KEYS = {"target_language", "provider", "show_translation", "skip_same_language"}
RETRY_KEYS = {"gemini_keys", "gemini_models", "fallback_free", "proxy_mode", "proxy_url"}
HOTKEY_KEYS = {"hotkeys_enabled", "hotkey_offset_up", "hotkey_offset_down", "hotkey_toggle", "hotkey_live_pause",
               "hotkey_click_through"}
HOTKEY_COMMANDS = {"offset_up": "offset_up", "offset_down": "offset_down", "toggle": "toggle_visible",
                   "live_pause": "toggle_live_pause", "click_through": "toggle_click_through"}
LIVE_KEYS = {"live_model", "live_english_only", "live_device", "live_vad_sensitivity", "live_max_segment",
             "live_context_lines"}
PASS_THROUGH = {"enabled": True, "mode": "auto", "kind": "other", "id": "", "name": ""}  # profiles switched off
GLOBAL_SCOPE_IDS = ("", profiles_mod.DEFAULT_ID, profiles_mod.MEDIA_ANY_ID)  # positions saved globally
SEEK_JUMP = 3               # more lines than this between ticks = a seek: min display time does not apply


@dataclass
class SongState:
    track: Track | None = None
    key: str = ""
    gen: int = 0
    status: str = ""                  # i18n key: st_searching / st_not_found / st_network / ''
    loaded_at: float = 0.0
    error_at: float = 0.0
    lyrics: LyricsResult | None = None
    times: list[float] = field(default_factory=list)
    translation: list[str] = field(default_factory=list)
    trans_status: str = ""            # '' | translating | failed | skipped | done | off
    trans_error_at: float = 0.0
    trans_retries: int = 0
    preview: dict[int, str] = field(default_factory=dict)   # look-ahead translations while the full one runs
    preview_busy: bool = False
    preview_tried: set[int] = field(default_factory=set)    # lines the look-ahead already sent (or skipped)
    edits: dict[int, str] = field(default_factory=dict)      # lines the user corrected (overlay menu) ...
    edits_lang: str = ""                                      # ... for this target language
    shown: int = -2                  # line index on screen (min display time can hold a line a bit longer)
    shown_at: float = 0.0


def setup_logging(debug: bool = False) -> None:
    handler = logging.handlers.RotatingFileHandler(paths.log_dir() / "dualyrics.log", maxBytes=1_000_000,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:  # running from a console
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)
    for noisy in ("urllib3", "PIL", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.excepthook = lambda *exc: log.critical("uncaught exception", exc_info=exc)
    threading.excepthook = lambda a: log.critical("uncaught exception in thread %s", a.thread,
                                                 exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


class App:
    def __init__(self, instance: winutil.SingleInstance, open_settings: bool = False) -> None:
        self.instance = instance
        self.settings = Settings()
        i18n.set_language(self.settings["ui_language"])
        fonts.load_bundled()
        self.templates = templates_mod.TemplateStore()
        self._migrate_v1()
        self._hidden_song = ""            # "hide for this song": song key until the song changes
        self.cache = cache_mod.Cache()
        threading.Thread(target=self.cache.prune, daemon=True).start()
        self._session_lock = threading.Lock()
        self._session = net.make_session(self.settings["proxy_mode"], self.settings["proxy_url"])
        self.translator = Translator(self.session)
        self.media = MediaWatcher(lambda: self.settings["preferred_players"])
        self.q: queue.Queue[Any] = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="song")
        self.song = SongState()
        self._song_lock = threading.Lock()
        self._notice: tuple[str, float] = ("", 0.0)
        self._stopping = False
        # v2: smart auto mode, per-app profiles, Live mode
        live_deps.activate_runtime()
        self.live = LiveEngine(self.effective_cfg, self.session, self.translator)
        self.auto = automode.AutoSwitch()
        self.decision = automode.Decision(automode.LYRICS, "start")
        self._fg = procs.Foreground(None)
        self._fg_at = -FG_POLL
        self._running: list[profiles_mod.AppRef] = []
        self._run_at = -RUN_POLL
        self.match = profiles_mod.Match(dict(PASS_THROUGH), "default")
        self._pending_match: tuple[str, float] = ("", 0.0)
        self._overrides: dict[str, Any] = {}
        self._cfg_cache: tuple[int, dict[str, Any], dict[str, Any]] | None = None
        self._scan = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan")   # process list, off the Tk thread
        self._scan_busy = False
        self._live_avail = (False, -DEPS_CHECK)
        self._live_off_since = 0.0

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Dualyrics")
        self.root.report_callback_exception = lambda *exc: log.error("Tk callback error", exc_info=exc)
        try:
            self.root.iconbitmap(default=str(paths.ICON_ICO))
        except tk.TclError:
            pass
        self.root.protocol("WM_SAVE_YOURSELF", self.quit)
        self.overlay = Overlay(self.root, self.settings, self.command, self.templates, host=self)
        from dualyrics.settings_window import SettingsWindow  # imports customtkinter (slower), after Tk exists

        self.settings_window = SettingsWindow(self)
        from dualyrics.tray import Tray

        self.tray = Tray(self.command, self._tray_state)
        self.hotkeys = HotkeyManager(lambda action: self.command(HOTKEY_COMMANDS.get(action, "")))
        self.settings.subscribe(lambda keys: self.call_soon(lambda: self._on_settings(keys)))

        startup.refresh()
        if self.settings["start_with_windows"] != startup.is_enabled():
            self.settings.update(start_with_windows=startup.is_enabled())
        self._open_settings_on_start = open_settings or self.settings.first_run

    # ------------------------------------------------------------------ public helpers (used by UI)
    def session(self) -> Any:
        with self._session_lock:
            return self._session

    def call_soon(self, fn: Callable[[], Any]) -> None:
        """Run ``fn`` on the Tk thread (thread-safe)."""
        self.q.put(fn)

    def command(self, cmd: str) -> None:
        if cmd:
            self.q.put(cmd)

    def _migrate_v1(self) -> None:
        """First start of v2 over v1 settings: the v1 look becomes the user template "My style (v1)", and saved
        positions (left edge in v1) become box centers."""
        s = self.settings
        # nothing to migrate on a first run, over v2 settings, or when settings.json was unreadable (defaults)
        if s.first_run or "template" in s.loaded_keys or not s.loaded_keys:
            return
        snap = s.snapshot()
        try:
            doc = templates_mod.from_v1(snap)
            if not self.templates.exists(doc["id"]):
                self.templates.save(doc)
            # v1 saved the left edge of a bar ``width`` px wide times the system DPI scale (at least 1.0)
            half = int(int(snap["width"]) * max(1.0, winutil.primary_monitor().scale)) // 2
            positions = {k: dict(v, x=v["x"] + half) if "x" in v else v for k, v in snap["positions"].items()}
            s.update(template=doc["id"], positions=positions)
            log.info("migrated the v1 look into the template %s", doc["id"])
        except Exception:
            log.exception("v1 look migration failed")
            s.update(template=templates_mod.DEFAULT_ID)

    def set_startup(self, enabled: bool) -> None:
        ok = startup.set_enabled(enabled)
        self.settings.update(start_with_windows=enabled if ok else startup.is_enabled())

    def restart_hotkeys(self) -> list[str]:
        if not self.settings["hotkeys_enabled"]:
            self.hotkeys.stop()
            return []
        return self.hotkeys.start({"offset_up": self.settings["hotkey_offset_up"],
                                   "offset_down": self.settings["hotkey_offset_down"],
                                   "toggle": self.settings["hotkey_toggle"],
                                   "live_pause": self.settings["hotkey_live_pause"],
                                   "click_through": self.settings["hotkey_click_through"]})

    def clear_cache(self) -> None:
        self.cache.clear()
        self._reload(keep=True)

    def _tray_state(self, name: str) -> Any:
        if name == "live_paused":
            return self.live.paused
        return self.effective_cfg()[name]   # check marks show what applies now (profile overrides included)

    def effective_cfg(self) -> dict[str, Any]:
        """Settings with the active profile's overrides (target language, translation only, placement...).

        Called many times per tick and from worker threads: the merged dict is cached until the settings or
        the overrides change, and every caller gets its own shallow copy (callers only read it)."""
        overrides = self._overrides
        rev = self.settings.rev
        cached = self._cfg_cache
        if cached is None or cached[0] != rev or cached[1] is not overrides:
            cfg = self.settings.snapshot()
            cfg.update(overrides)
            if self.settings.rev == rev:      # not changed while copying
                self._cfg_cache = (rev, overrides, cfg)
            return dict(cfg)
        return dict(cached[2])

    def live_ready(self) -> bool:
        """Live mode is switched on and its packages are installed (checked every few seconds)."""
        if not self.settings["live_enabled"]:
            return False
        ok, at = self._live_avail
        now = time.monotonic()
        if now - at > DEPS_CHECK:
            ok = live_deps.available()
            self._live_avail = (ok, now)
        return ok

    def recheck_live_deps(self) -> None:
        self._live_avail = (False, -DEPS_CHECK)
        self.live.clear_error()

    def song_offset_info(self) -> tuple[str, int]:
        tr = self.song.track
        if not tr:
            return "", 0
        return f"{tr.artist} — {tr.title}", self.cache.get_offset(self.song.key)

    # ------------------------------------------------------------------ run / quit
    def run(self) -> None:
        log.info("Dualyrics %s starting (Python %s)", __version__, sys.version.split()[0])
        self.media.start()
        self.tray.start()
        failed = self.restart_hotkeys()
        if failed:
            log.warning("hotkeys unavailable: %s", failed)
        threading.Thread(target=self._instance_listener, name="instance", daemon=True).start()
        if self._open_settings_on_start:
            self.root.after(400, self.settings_window.open)
        self.root.after(TICK_MS, self._tick)
        self.root.mainloop()
        log.info("Dualyrics stopped")

    def quit(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        log.info("shutting down")
        try:
            self.settings_window.close()
        except Exception:
            pass
        self.hotkeys.stop()
        self.tray.stop()
        try:
            self.overlay.close()
        except Exception:
            pass
        self.media.stop()
        try:
            self.live.shutdown()
        except Exception:
            log.debug("live shutdown failed", exc_info=True)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self._scan.shutdown(wait=False, cancel_futures=True)
        self.settings.save()
        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass

    def _instance_listener(self) -> None:
        while not self._stopping:
            if self.instance.wait_for_signal(500):
                self.call_soon(self.settings_window.open)
            if self.instance.quit_requested():
                log.info("quit requested by another process (installer or --quit)")
                self.call_soon(self.quit)
                return

    # ------------------------------------------------------------------ settings changes
    def _on_settings(self, keys: set[str]) -> None:
        if self._stopping:
            return
        if "ui_language" in keys:
            i18n.set_language(self.settings["ui_language"])
        if keys & {"proxy_mode", "proxy_url"}:
            with self._session_lock:
                self._session = net.make_session(self.settings["proxy_mode"], self.settings["proxy_url"])
            self.live.clear_error()      # a model download that failed may work through the new proxy
        self.overlay.apply_settings()
        if keys & TRANSLATION_KEYS or ("gemini_keys" in keys and self.settings["provider"] == "gemini"):
            # adding/removing Gemini keys switches the effective provider (free <-> gemini)
            self.song.trans_retries = 0
            self._reload(keep=True)
        elif keys & RETRY_KEYS and (self.song.trans_status == "failed" or self.song.status == "st_network"):
            self.song.trans_retries = 0
            self._reload(keep=True)
        if keys & HOTKEY_KEYS:
            self.restart_hotkeys()
        if keys & LIVE_KEYS:
            self.live.reload_if_changed()
        if "live_enabled" in keys:
            self.recheck_live_deps()
            if not self.settings["live_enabled"]:
                self.live.stop()
        if keys & {"profiles", "profiles_enabled", "profile_activation"}:
            self._pending_match = ("", 0.0)
        if keys & {"visible", "click_through", "locked", "ui_language", "translation_only"}:
            self.tray.refresh()
        self.settings_window.on_settings_changed(keys)

    # ------------------------------------------------------------------ commands
    def _handle(self, cmd: str) -> None:
        s = self.settings
        toggles = {"toggle_visible": "visible", "toggle_click_through": "click_through", "toggle_locked": "locked",
                   "toggle_next": "show_next", "toggle_original": "show_original",
                   "toggle_translation": "show_translation", "toggle_text_only": "text_only",
                   "toggle_translation_only": "translation_only"}
        if cmd in toggles:
            key = toggles[cmd]
            new = not self.effective_cfg()[key]
            prof = self.match.profile
            if key in ("click_through", "translation_only") and prof.get("id") and isinstance(prof.get(key), bool):
                # the active app's profile sets this: flip it there, or the toggle (Ctrl+Alt+K) would do nothing
                self._set_profile_field(prof["id"], **{key: new})
            else:
                s.update(**{key: new})
        elif cmd == "settings":
            self.settings_window.open()
        elif cmd.startswith("settings:"):
            self.settings_window.open(cmd.split(":", 1)[1])
        elif cmd == "retranslate":
            if self.song.key:
                self.cache.drop_translation(self.song.key, s["target_language"])
                self.translator.gemini.reset_cooldowns()
                self.song.trans_retries = 0
                self.song.edits = {}         # a fresh translation was asked for
                self._reload(keep=True)
        elif cmd == "open_cache":
            os.startfile(self.cache.root)
        elif cmd in ("offset_up", "offset_down", "offset_reset"):
            if self.song.key:
                cur = self.cache.get_offset(self.song.key)
                step = s["offset_step_ms"]
                new = 0 if cmd == "offset_reset" else cur + (step if cmd == "offset_up" else -step)
                self.cache.set_offset(self.song.key, new)
                self._notice = (i18n.t("st_offset", ms=new), time.time() + 2.5)
        elif cmd == "toggle_live_pause":
            self.live.set_paused(not self.live.paused)
            self._notice = (i18n.t("lv_paused" if self.live.paused else "lv_resumed"), time.time() + 2.5)
            self.tray.refresh()
        elif cmd.startswith("preset_"):
            self._set_preset(cmd[len("preset_"):])
        elif cmd == "quit":
            self.quit()
        else:
            self._handle_v2(cmd)

    # ------------------------------------------------------------------ v2 commands (overlay menu, hotkeys)
    def _handle_v2(self, cmd: str) -> None:
        s = self.settings
        name, _, arg = cmd.partition(":")
        if name == "mode":
            if arg == "off":
                s.update(visible=False)
            elif arg in ("auto", "lyrics", "live"):
                s.update(mode=arg, visible=True)
                self._clear_profile_field("mode")
        elif name == "show":
            if arg == "both":
                s.update(translation_only=False, show_original=True, show_translation=True)
            elif arg == "translation":
                s.update(translation_only=True, show_translation=True)
            elif arg == "original":
                s.update(translation_only=False, show_original=True, show_translation=False)
            self._clear_profile_field("translation_only")
        elif name == "template":
            self.apply_template(arg)
        elif cmd in ("size_up", "size_down", "size_reset"):
            self.overlay.change_scale({"size_up": 1.1, "size_down": 1 / 1.1}.get(cmd))
        elif name == "opacity":
            try:
                s.update(overlay_opacity=float(arg))
            except ValueError:
                pass
        elif name == "monitor":
            s.update(monitor=arg, follow_active_monitor=False)
            self._clear_profile_field("monitor")
        elif cmd == "toggle_follow_monitor":
            s.update(follow_active_monitor=not s["follow_active_monitor"])
        elif name == "lang":
            s.update(target_language=arg)
            self._clear_profile_field("target_language")
        elif cmd in ("global_offset_up", "global_offset_down", "global_offset_reset"):
            step = s["offset_step_ms"]
            new = 0 if cmd.endswith("reset") else s["offset_ms"] + (step if cmd.endswith("up") else -step)
            s.update(offset_ms=new)
            self._notice = (i18n.t("st_global_offset", ms=s["offset_ms"]), time.time() + 2.5)
        elif cmd == "copy_line":
            info = self.menu_info()
            self._copy("\n".join(x for x in (info.get("line_original"), info.get("line_translation")) if x))
        elif cmd == "copy_lyrics":
            self._copy(self.lyrics_text())
        elif cmd == "hide_song":
            if self.song.key:
                self._hidden_song = self.song.key
        elif cmd == "hide_app":
            self.hide_for_app()
        elif cmd == "save_transcript":
            path = self.live.save_transcript()
            self._notice = (i18n.t("m_transcript_saved", name=path.name) if path else i18n.t("m_transcript_empty"),
                            time.time() + 4)
        else:
            log.debug("unknown command %r", cmd)

    def _set_preset(self, preset: str) -> None:
        """Position preset from the menu: forget the dragged place on this monitor (for this profile too)."""
        s = self.settings
        mon = self.overlay.monitor()
        positions = dict(s["positions"])
        keys = [mon.name] + ([f"{self.overlay.profile_id}@{mon.name}"] if self.overlay.profile_id else [])
        for key in keys:
            if key in positions:
                entry = {k: v for k, v in positions[key].items() if k in ("w", "scale")}
                if entry:
                    positions[key] = entry
                else:
                    positions.pop(key)
        s.update(position_preset=preset, positions=positions)
        prof = self.match.profile
        if prof.get("position") and prof.get("id"):
            self._set_profile_field(prof["id"], position=preset)

    def _set_profile_field(self, pid: str, **changes: Any) -> None:
        profs = self.settings["profiles"]
        for p in profs:
            if p["id"] == pid:
                p.update(changes)
        self.settings.update(profiles=profs)

    def _clear_profile_field(self, field_name: str) -> None:
        """A quick change from the overlay menu wins over the active profile's own value for that field."""
        prof = self.match.profile
        unset: Any = {"translation_only": None, "click_through": None, "mode": "auto"}.get(field_name, "")
        value = prof.get(field_name)
        if not prof.get("id") or value is None or value == unset or (value in ("", 0) and not isinstance(value, bool)):
            return
        if field_name == "mode" and value == "off":
            return   # "Hide for this app" stays until it is undone in Settings > Profiles
        self._set_profile_field(prof["id"], **{field_name: unset})

    def apply_template(self, tid: str, ask: bool = True, parent: Any = None) -> None:
        """Make a template the active look (for the active profile when it has its own template). Offers the
        template's suggested placement and behaviour (never forced)."""
        if not self.templates.exists(tid):
            return
        prof = self.match.profile
        if prof.get("id") and prof.get("template"):
            self._set_profile_field(prof["id"], template=tid)
        else:
            self.settings.update(template=tid)
        hints = self.templates.resolve(tid).get("hints") or {}
        want: dict[str, Any] = {}
        if hints.get("placement") in ("bottom", "top", "center"):
            want["position_preset"] = hints["placement"]
        for k in ("click_through", "locked"):
            if isinstance(hints.get(k), bool):
                want[k] = hints[k]
        s = self.settings
        if not want or all(s[k] == v for k, v in want.items()):
            return
        if ask:
            from tkinter import messagebox

            if not messagebox.askyesno("Dualyrics", i18n.t("tpl_hints_offer"), parent=parent or self.root):
                return
        if "position_preset" in want:
            self._set_preset(want.pop("position_preset"))
        if want:
            s.update(**want)

    def hide_for_app(self) -> None:
        """Turn the overlay off for the app in use (its profile's mode becomes Off; undo in Settings > Profiles)."""
        prof = self.match.profile
        if not prof.get("id") or prof["id"] in GLOBAL_SCOPE_IDS:
            return
        self._set_profile_field(prof["id"], mode="off")
        log.info("overlay switched off for %s", prof.get("name") or prof["id"])

    def _copy(self, text: str) -> None:
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._notice = (i18n.t("m_copied"), time.time() + 2)
        except tk.TclError:
            pass

    def lyrics_text(self) -> str:
        """The whole song with its translation under each line (for "Copy full lyrics")."""
        st = self.song
        res = st.lyrics
        if res is None or not res.found:
            return ""
        lines = [ln.text for ln in res.synced] if res.synced else list(res.plain)
        out: list[str] = []
        if st.track:
            out += [f"{st.track.artist} - {st.track.title}", ""]
        for i, ln in enumerate(lines):
            tr = st.translation[i] if i < len(st.translation) else st.preview.get(i, "")
            out.append(ln)
            if tr and tr != ln:
                out.append(tr)
        return "\n".join(out).strip()

    # ------------------------------------------------------------------ overlay host interface
    def position(self) -> float:
        """Current song position (s) with offsets, in the same time base as the lyric timestamps."""
        offset = (self.settings["offset_ms"] + self.cache.get_offset(self.song.key)) / 1000.0
        return self.media.clock.position() - offset + LEAD

    def menu_info(self) -> dict[str, Any]:
        st = self.song
        live = self.decision.mode == automode.LIVE
        info: dict[str, Any] = {
            "mode": self.decision.mode, "global_mode": self.settings["mode"],
            "song": st.key if st.lyrics is not None and st.lyrics.found and not live else "",
            "song_offset": self.cache.get_offset(st.key) if st.key else 0,
            "live_available": self.live_ready(), "live_paused": self.live.paused,
            "live_has_text": bool(self.live.stream.captions),
        }
        prof = self.match.profile
        if prof.get("id") and prof["id"] not in GLOBAL_SCOPE_IDS:
            info["profile_name"] = i18n.t(f"ap_builtin_{prof['id']}") if prof.get("builtin") else prof.get("name")
            info["profile_hideable"] = True
        c = self.overlay._content
        if c is not None:
            if live:
                cap = self.live.stream.last
                if cap is not None:
                    info.update(line_original=cap.text, line_translation=cap.translation)
            elif c.index >= 0 and st.lyrics is not None and st.lyrics.synced and c.index < len(st.lyrics.synced):
                i = c.index
                info.update(line_index=i, line_original=st.lyrics.synced[i].text,
                            line_translation=st.translation[i] if i < len(st.translation) else st.preview.get(i, ""))
                info["editable"] = bool(info["line_original"].strip()) and self.effective_cfg()["show_translation"]
        return info

    def edit_translation(self, index: int, text: str) -> None:
        """The user corrected one line's translation: show it now and keep it in the translation cache."""
        st = self.song
        res = st.lyrics
        if res is None or not res.synced or not (0 <= index < len(res.synced)) or not text:
            return
        n = len(res.synced)
        lang = self.effective_cfg()["target_language"]
        if st.edits_lang != lang:
            st.edits, st.edits_lang = {}, lang
        st.edits[index] = text
        if st.trans_status not in ("done", "skipped") or len(st.translation) != n:
            # the full translation is still running (or failed): show the edit now and merge it into the
            # translation when it arrives; caching the gaps as a finished translation would hide them for good
            st.preview[index] = text
            self._notice = (i18n.t("m_edit_saved"), time.time() + 2.5)
            return
        trans = list(st.translation)
        trans[index] = text
        st.translation = trans
        st.trans_status = "done"
        cfg = self.effective_cfg()
        provider = "gemini" if cfg["provider"] == "gemini" and cfg["gemini_keys"] else "free"
        self.cache.put_translation(st.key, cfg["target_language"], provider,
                                   {"lines": trans, "skipped": False, "source": "", "edited": True})
        self._notice = (i18n.t("m_edit_saved"), time.time() + 2.5)

    # ------------------------------------------------------------------ song loading
    def _reload(self, keep: bool) -> None:
        tr = self.media.track
        if tr:
            self._start_load(tr, keep=keep)

    def _start_load(self, track: Track, keep: bool = False) -> None:
        with self._song_lock:
            st = self.song
            st.gen += 1
            gen = st.gen
            same = st.track is not None and st.track.key == track.key
            if not (keep and same):
                self.song = SongState(track=track, key=cache_mod.track_key(track.title, track.artist), gen=gen,
                                      loaded_at=time.time())
            else:
                st.track = track
            st = self.song
        self.executor.submit(self._load, track, gen, st)

    def _current(self, gen: int) -> bool:
        return self.song.gen == gen and not self._stopping

    def _load(self, track: Track, gen: int, st: SongState) -> None:
        try:
            if self._current(gen):  # skip loads that went stale while queued (fast track skipping)
                self._load_inner(track, gen, st)
        except Exception:
            log.exception("loading %s failed", track.key)

    def _load_inner(self, track: Track, gen: int, st: SongState) -> None:
        # ``st`` is the state object of *this* load: writing to self.song instead could hit the next song
        key = cache_mod.track_key(track.title, track.artist)
        cached = self.cache.get_lyrics(key)
        if cached is not None:
            res = LyricsResult.from_dict(cached)
        else:
            if self._current(gen) and st.lyrics is None:
                st.status = "st_searching"
            res = LyricsClient(self.session()).fetch(track.title, track.artist, track.album, track.duration)
            if not self._current(gen):
                return
            if res.network_error:
                st.status, st.error_at = "st_network", time.time()
                log.warning("lyrics lookup failed (network): %s", track.key)
                return
            self.cache.put_lyrics(key, res.to_dict())
            log.info("lyrics %s: synced=%d plain=%d instrumental=%s", "found" if res.found else "not found",
                     len(res.synced), len(res.plain), res.instrumental)
        if not self._current(gen):
            return
        st.times = [ln.time for ln in res.synced]
        st.lyrics = res
        st.loaded_at = time.time()
        st.status = "" if res.found else "st_not_found"
        self._translate(track, key, res, gen, st)

    def _translate(self, track: Track, key: str, res: LyricsResult, gen: int, st: SongState) -> None:
        cfg = self.effective_cfg()
        lines = [ln.text for ln in res.synced] if res.synced else list(res.plain)
        if not cfg["show_translation"] or not any(x.strip() for x in lines):
            st.translation, st.trans_status = [], "off"
            return
        provider = "gemini" if cfg["provider"] == "gemini" and cfg["gemini_keys"] else "free"
        target = cfg["target_language"]
        hit = self.cache.get_translation(key, target, provider)
        if not self._current(gen):
            return
        edits = dict(st.edits) if st.edits_lang == target else {}
        if hit and len(hit["lines"]) == len(lines):
            out = [str(x) for x in hit["lines"]]
            if edits and any(out[i] != v for i, v in edits.items() if i < len(out)):
                self._store_translation(st, key, target, provider, out, False, "", edits)
                return
            st.translation = out
            st.trans_status = "skipped" if hit.get("skipped") else "done"
            return
        st.translation, st.trans_status = [], "translating"
        st.preview, st.preview_tried, st.preview_busy = dict(edits), set(), False
        try:
            tr = self.translator.translate(lines, track.title, track.artist, cfg,
                                           cancelled=lambda: not self._current(gen))
        except TranslationError as e:
            if self._current(gen):
                st.trans_status, st.trans_error_at = "failed", time.time()
                log.warning("translation failed: %s", e)
            return
        if not self._current(gen):
            return
        # edits the user made while this translation ran (same language) win over the new lines
        edits = dict(st.edits) if st.edits_lang == target else {}
        self._store_translation(st, key, target, tr.provider, list(tr.lines), tr.skipped, tr.source_lang, edits)
        log.info("translated with %s (source=%s, skipped=%s)", tr.provider, tr.source_lang or "?", tr.skipped)
        if provider == "gemini" and tr.provider == "free":
            self._notice = (i18n.t("st_free_used"), time.time() + 6)

    def _store_translation(self, st: SongState, key: str, target: str, provider: str, lines: list[str],
                           skipped: bool, source: str, edits: dict[int, str]) -> None:
        """Show a finished translation and cache it, with the user's corrections of this song applied."""
        data: dict[str, Any] = {"lines": lines, "skipped": skipped, "source": source}
        if edits:
            for i, text in edits.items():
                if 0 <= i < len(lines):
                    lines[i] = text
            data.update(lines=lines, skipped=False, edited=True)
        self.cache.put_translation(key, target, provider, data)
        st.translation = lines
        st.trans_status = "skipped" if data["skipped"] else "done"

    # ------------------------------------------------------------------ main loop
    def _tick(self) -> None:
        if self._stopping:
            return
        try:
            self._pump()
            self._update()
        except Exception:
            log.exception("tick failed")
        if not self._stopping:
            self.root.after(TICK_MS, self._tick)

    def _pump(self) -> None:
        for _ in range(50):
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                item() if callable(item) else self._handle(item)
            except Exception:
                log.exception("command %r failed", item)
            if self._stopping:
                return

    def _update(self) -> None:
        s = self.settings
        media, st = self.media, self.song
        track = media.track
        now = time.time()
        if track and track.key != (st.track.key if st.track else None):
            self._start_load(track)
            st = self.song
        elif track and st.status == "st_network" and now - st.error_at > NETWORK_RETRY:
            st.error_at = now
            self._start_load(track, keep=True)
        elif track and st.trans_status == "failed" and st.trans_retries < 3 and now - st.trans_error_at > TRANSLATE_RETRY:
            st.trans_retries += 1
            st.trans_error_at = now
            self._start_load(track, keep=True)

        self._sample_system(now)
        self._update_profile(now)
        self.overlay.tick()
        if self._hidden_song and (track is None or cache_mod.track_key(track.title, track.artist) != self._hidden_song):
            self._hidden_song = ""
        d = self._decide(now)
        if d.mode == automode.HIDE and media.error and track is None:
            d = automode.Decision(automode.LYRICS, "media_error")  # keep v1's "media controls unavailable" notice
        if d.mode == automode.LIVE:
            self._live_off_since = 0.0
            self._start_live()
            content = self._live_content(now)
            visible = bool(s["visible"]) and content is not None
        else:
            self._idle_live(now)
            visible = d.mode == automode.LYRICS and bool(s["visible"]) and (
                media.has_session or bool(media.error)) and (track is not None or bool(media.error)) and (
                not self._hidden_song or self._hidden_song != st.key)
            if visible and s["hide_when_idle"] and not media.playing and now - media.last_playing_at > s["idle_hide_delay"]:
                visible = False
            content = self._content(now) if visible else None
            self._lookahead(self.song)
        if content is None:
            visible = False
        if visible and content is not None:
            self.overlay.render(content)
            self.overlay.show()
        else:
            self.overlay.hide()

    # ------------------------------------------------------------------ v2: profiles, smart auto, Live
    def _sample_system(self, now: float) -> None:
        """Foreground window (twice a second) and running processes (every few seconds)."""
        if not self.settings["profiles_enabled"] and not self.settings["follow_active_monitor"]:
            return
        if now - self._fg_at >= FG_POLL:
            self._fg_at = now
            try:
                fg = procs.foreground()
                if not fg.own:          # our settings window or overlay: keep the app the user came from
                    self._fg = fg
                    if self.settings["follow_active_monitor"] and fg.hwnd and not fg.shell:
                        mon = winutil.monitor_of_window(fg.hwnd)
                        if mon is not None:
                            self.overlay.set_active_monitor(mon.name)
            except Exception:
                log.debug("foreground check failed", exc_info=True)
        if now - self._run_at >= RUN_POLL and not self._scan_busy:
            self._run_at = now
            self._scan_busy = True

            def scan() -> None:
                try:
                    self._running = procs.running()
                except Exception:
                    log.debug("process scan failed", exc_info=True)
                finally:
                    self._scan_busy = False

            try:
                self._scan.submit(scan)
            except RuntimeError:   # shutting down
                self._scan_busy = False

    def _update_profile(self, now: float) -> None:
        s = self.settings
        if s["profiles_enabled"]:
            media = self.media
            m = profiles_mod.match_profile(
                s["profiles"], self._fg.app, self._fg.fullscreen, self._running,
                media.track.app if media.track else "", media.playing and media.has_session,
                s["profile_activation"])
        else:
            m = profiles_mod.Match(dict(PASS_THROUGH), "default")
        key = f"{m.profile.get('id')}|{m.via}"
        if key == f"{self.match.profile.get('id')}|{self.match.via}":
            self.match = m           # same profile: take its current values (the user may have edited it)
            self._pending_match = ("", 0.0)
        else:
            pending, since = self._pending_match
            if pending != key:
                self._pending_match = (key, now)
            if (pending == key and now - since >= PROFILE_HOLD) or not self.match.profile.get("id"):
                log.info("profile: %s (via %s)", m.profile.get("name") or m.profile.get("id"), m.via)
                self.match = m
                self._pending_match = ("", 0.0)
        over = profiles_mod.overrides(self.match.profile) if s["profiles_enabled"] else {}
        pid = self.match.profile.get("id") or ""
        scope = pid if s["profiles_enabled"] and pid not in GLOBAL_SCOPE_IDS else ""
        if over != self._overrides or scope != self.overlay.profile_id:
            old_target = self.effective_cfg()["target_language"]
            self._overrides = over
            self.overlay.set_overrides(over, scope)
            self.tray.refresh()
            if self.effective_cfg()["target_language"] != old_target:
                self.song.trans_retries = 0
                self._reload(keep=True)

    def _lyrics_state(self) -> str:
        st = self.song
        if self.media.track is None or st.track is None:
            return automode.LYR_NONE
        if st.status == "st_network":
            return automode.LYR_NETWORK
        if st.lyrics is None:
            return automode.LYR_SEARCHING
        return automode.LYR_FOUND if st.lyrics.found else automode.LYR_NOT_FOUND

    def _decide(self, now: float) -> automode.Decision:
        s, media = self.settings, self.media
        prof, via = self.match.profile, self.match.via
        has_track = media.track is not None and media.has_session
        recent = has_track and not media.playing and (
            not s["hide_when_idle"] or now - media.last_playing_at <= s["idle_hide_delay"])
        inp = automode.Inputs(
            mode=profiles_mod.effective_mode(prof, s["mode"]),
            profile_enabled=bool(prof.get("enabled", True)),
            media_playing=has_track and media.playing,
            media_recent=recent,
            lyrics=self._lyrics_state(),
            live_ready=self.live_ready(),
            live_when_no_lyrics=s["live_when_no_lyrics"],
            live_context=profiles_mod.is_live_context(prof, via),
        )
        d = self.auto.update(automode.decide(inp), now)
        if d.mode != self.decision.mode:
            log.info("mode: %s (%s)", d.mode, d.reason)
        self.decision = d
        return d

    def translation_only(self) -> bool:
        return bool(self.effective_cfg()["translation_only"])

    def _start_live(self) -> None:
        m = self.match
        where = ""
        if m.via == "fullscreen":
            where = self._fg.title[:80]
        elif m.profile.get("id") and m.profile.get("id") not in profiles_mod.SPECIAL_IDS:
            where = m.profile.get("name", "")
        tr = self.media.track
        title = f"{tr.artist} — {tr.title}" if tr and self.media.playing else ""
        self.live.start(where, title)

    def _idle_live(self, now: float) -> None:
        if self.live.running:
            if not self._live_off_since:
                self._live_off_since = now
            elif now - self._live_off_since > LIVE_STOP_GRACE:
                log.info("live: stopped listening")
                self.live.stop()
        elif self.live.loaded and not self._live_off_since:
            self._live_off_since = now   # stopped elsewhere (Live switched off, engine error): unload later too
        elif self._live_off_since and self.live.loaded and now - self._live_off_since > LIVE_UNLOAD:
            log.info("live: speech model unloaded after %d s without Live", LIVE_UNLOAD)
            self.live.unload()
            self._live_off_since = 0.0

    def _live_content(self, now: float) -> Content | None:
        v = self.live.view(now, self.translation_only())
        notice, until = self._notice
        has_notice = bool(notice) and now < until
        status = notice if has_notice else (i18n.t(v.status, **v.status_kw) if v.status else "")
        if not v.visible:
            return Content(status=status, song="live") if has_notice else None
        return Content(original=v.original, translation=v.translation, status=status, song="live")

    def _lookahead(self, st: SongState) -> None:
        """While Gemini translates the whole song, translate the lines about to be sung with the free
        translator, so they already have a translation (replaced by Gemini's when it arrives)."""
        res = st.lyrics
        if st.preview_busy or st.trans_status != "translating" or res is None or not res.synced:
            return
        cfg = self.effective_cfg()
        if not (cfg["provider"] == "gemini" and cfg["gemini_keys"] and cfg["fallback_free"]):
            return
        times = st.times if len(st.times) == len(res.synced) else [ln.time for ln in res.synced]
        # lines already sent (or found to need nothing) are skipped, so this works after a seek backwards too
        todo = [i for i in sync.lookahead_window(times, self.position(), done=st.preview_tried | set(st.preview))
                if res.synced[i].text.strip()]
        if not todo:
            return
        st.preview_tried.update(todo)
        lines = [res.synced[i].text for i in todo]
        target, gen = cfg["target_language"], st.gen
        guess = langs.guess_language("\n".join(lines))
        if cfg["skip_same_language"] and guess and langs.same_language(guess, target):
            return
        st.preview_busy = True

        def work() -> None:
            try:
                out, detected = self.translator.free.translate(lines, target)
                if not (cfg["skip_same_language"] and detected and langs.same_language(detected, target)):
                    if self._current(gen):
                        for i, text in zip(todo, out):
                            st.preview.setdefault(i, text)   # a line the user corrected meanwhile stays
            except Exception as e:
                log.info("look-ahead translation failed: %s", e)
            finally:
                if st.gen == gen:
                    st.preview_busy = False

        self.executor.submit(work)

    def _status_text(self, now: float) -> str:
        st = self.song
        notice, until = self._notice
        if notice and now < until:
            return notice
        if st.status:
            return i18n.t(st.status)
        if st.trans_status == "translating":
            return i18n.t("st_translating")
        if st.trans_status == "failed":
            return i18n.t("st_trans_failed")
        return ""

    def _hold(self, st: SongState, i: int) -> int:
        """The template's minimum display time: a line stays at least that long even when the next timestamp
        comes sooner (fast rap). Lines are never skipped; a jump of several lines (a seek) applies at once."""
        now = time.monotonic()
        min_s = self.overlay.template["timing"]["min_display_ms"] / 1000.0
        if st.shown == -2 or min_s <= 0 or i < st.shown or i - st.shown > SEEK_JUMP:
            if i != st.shown:
                st.shown, st.shown_at = i, now
            return i
        if i == st.shown:
            return i
        if now - st.shown_at >= min_s:
            st.shown += 1
            st.shown_at = now
        return st.shown

    def _content(self, now: float) -> Content | None:
        if self.media.error and self.media.track is None:
            return Content(status=i18n.t("st_no_media"))
        st = self.song
        res = st.lyrics
        status = self._status_text(now)
        if res is None:
            return Content(status=status or i18n.t("st_searching"), song=st.key)
        if not res.found:
            return Content(status=status, song=st.key) if now - st.loaded_at < NOT_FOUND_SHOW else None
        if res.instrumental and not res.synced and not res.plain:
            return Content(original=i18n.t("st_instrumental"), status=status, song=st.key)
        pos = self.position()
        trans = st.translation
        only = self.translation_only()
        if res.synced:
            lines = res.synced
            times = st.times
            if len(times) != len(lines):  # a worker is swapping lyrics/times right now
                times = [ln.time for ln in lines]
            i = self._hold(st, bisect.bisect_right(times, pos) - 1)
            original = lines[i].text if i >= 0 else ""
            translation = trans[i] if 0 <= i < len(trans) else st.preview.get(i, "")
            nxt_i = next((k for k in range(i + 1, len(lines)) if lines[k].text.strip()), -1)
            nxt = lines[nxt_i].text if nxt_i >= 0 else ""
            if only and translation:
                original = ""   # translation only: the original stays only when there is no translation
                nxt = (trans[nxt_i] if 0 <= nxt_i < len(trans) else st.preview.get(nxt_i, "")) or nxt
            t0 = times[i] if i >= 0 else -1.0
            dur = st.track.duration if st.track and st.track.duration else 0.0
            t1 = times[i + 1] if 0 <= i < len(times) - 1 else (dur if dur > t0 + 0.5 else t0 + 5.0)
            return Content(original=original, translation=translation, next=nxt, status=status, song=st.key,
                           index=i, line_start=t0, line_end=t1)
        rows = tuple((ln, trans[k] if k < len(trans) else "") for k, ln in enumerate(res.plain))
        if only:
            rows = tuple((tr or ln, "") for ln, tr in rows)
        dur = st.track.duration if st.track else 0
        progress = min(1.0, max(0.0, pos / dur)) if dur > 0 else -1.0
        return Content(mode="plain", rows=rows, progress=progress, song=st.key,
                       status=status or i18n.t("st_unsynced"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dualyrics", description="Synced lyrics with translation for Windows.")
    parser.add_argument("--settings", action="store_true", help="open the settings window on start")
    parser.add_argument("--startup", action="store_true", help="launched by Windows at sign-in")
    parser.add_argument("--debug", action="store_true", help="verbose log")
    parser.add_argument("--quit", action="store_true", help="close the running copy (used by the installer)")
    parser.add_argument("--version", action="version", version=f"Dualyrics {__version__}")
    args = parser.parse_args(argv)

    instance = winutil.SingleInstance()
    if args.quit:
        asked = instance.already_running and instance.signal_quit()
        instance.release()
        if asked:
            winutil.SingleInstance.wait_until_gone(15)
        return
    if instance.already_running:
        if not args.startup:  # a manual second launch opens the running copy's settings; sign-in stays silent
            instance.signal_running_instance()
        instance.release()
        return
    setup_logging(args.debug)
    winutil.set_dpi_awareness()
    winutil.set_app_user_model_id(APP_ID)
    try:
        app = App(instance, open_settings=args.settings)
        app.run()
    except Exception:
        log.exception("fatal error")
        raise
    finally:
        instance.release()
        logging.shutdown()
    # Settings are saved and the tray/hotkeys/media threads are stopped. Exit now: Python would otherwise
    # wait at shutdown for a lyrics/translation request still running in the worker pool (up to ~75 s).
    os._exit(0)
