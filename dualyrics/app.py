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

from dualyrics import APP_ID, __version__, cache as cache_mod, i18n, net, paths, startup, winutil
from dualyrics.hotkeys import HotkeyManager
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

TRANSLATION_KEYS = {"target_language", "provider", "show_translation", "skip_same_language"}
RETRY_KEYS = {"gemini_keys", "gemini_models", "fallback_free", "proxy_mode", "proxy_url"}
HOTKEY_KEYS = {"hotkeys_enabled", "hotkey_offset_up", "hotkey_offset_down", "hotkey_toggle"}
HOTKEY_COMMANDS = {"offset_up": "offset_up", "offset_down": "offset_down", "toggle": "toggle_visible"}


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
    for noisy in ("urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.excepthook = lambda *exc: log.critical("uncaught exception", exc_info=exc)
    threading.excepthook = lambda a: log.critical("uncaught exception in thread %s", a.thread,
                                                 exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


class App:
    def __init__(self, instance: winutil.SingleInstance, open_settings: bool = False) -> None:
        self.instance = instance
        self.settings = Settings()
        i18n.set_language(self.settings["ui_language"])
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

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Dualyrics")
        self.root.report_callback_exception = lambda *exc: log.error("Tk callback error", exc_info=exc)
        try:
            self.root.iconbitmap(default=str(paths.ICON_ICO))
        except tk.TclError:
            pass
        self.root.protocol("WM_SAVE_YOURSELF", self.quit)
        self.overlay = Overlay(self.root, self.settings, self.command)
        from dualyrics.settings_window import SettingsWindow  # imports customtkinter (slower), after Tk exists

        self.settings_window = SettingsWindow(self)
        from dualyrics.tray import Tray

        self.tray = Tray(self.command, lambda name: self.settings[name])
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

    def set_startup(self, enabled: bool) -> None:
        ok = startup.set_enabled(enabled)
        self.settings.update(start_with_windows=enabled if ok else startup.is_enabled())

    def restart_hotkeys(self) -> list[str]:
        if not self.settings["hotkeys_enabled"]:
            self.hotkeys.stop()
            return []
        return self.hotkeys.start({"offset_up": self.settings["hotkey_offset_up"],
                                   "offset_down": self.settings["hotkey_offset_down"],
                                   "toggle": self.settings["hotkey_toggle"]})

    def clear_cache(self) -> None:
        self.cache.clear()
        self._reload(keep=True)

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
        self.media.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
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

    # ------------------------------------------------------------------ settings changes
    def _on_settings(self, keys: set[str]) -> None:
        if self._stopping:
            return
        if "ui_language" in keys:
            i18n.set_language(self.settings["ui_language"])
        if keys & {"proxy_mode", "proxy_url"}:
            with self._session_lock:
                self._session = net.make_session(self.settings["proxy_mode"], self.settings["proxy_url"])
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
        if keys & {"visible", "click_through", "locked", "ui_language"}:
            self.tray.refresh()
        self.settings_window.on_settings_changed(keys)

    # ------------------------------------------------------------------ commands
    def _handle(self, cmd: str) -> None:
        s = self.settings
        toggles = {"toggle_visible": "visible", "toggle_click_through": "click_through", "toggle_locked": "locked",
                   "toggle_next": "show_next", "toggle_original": "show_original",
                   "toggle_translation": "show_translation", "toggle_text_only": "text_only"}
        if cmd in toggles:
            s.update(**{toggles[cmd]: not s[toggles[cmd]]})
        elif cmd == "settings":
            self.settings_window.open()
        elif cmd == "retranslate":
            if self.song.key:
                self.cache.drop_translation(self.song.key, s["target_language"])
                self.translator.gemini.reset_cooldowns()
                self.song.trans_retries = 0
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
        elif cmd.startswith("preset_"):
            s.update(position_preset=cmd[len("preset_"):])
        elif cmd == "quit":
            self.quit()

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
        st.times = [ln.time for ln in res.synced]
        st.loaded_at = time.time()
        st.status = "" if res.found else "st_not_found"
        self._translate(track, key, res, gen, st)

    def _translate(self, track: Track, key: str, res: LyricsResult, gen: int, st: SongState) -> None:
        cfg = self.settings.snapshot()
        lines = [ln.text for ln in res.synced] if res.synced else list(res.plain)
        if not cfg["show_translation"] or not any(x.strip() for x in lines):
            st.translation, st.trans_status = [], "off"
            return
        provider = "gemini" if cfg["provider"] == "gemini" and cfg["gemini_keys"] else "free"
        target = cfg["target_language"]
        hit = self.cache.get_translation(key, target, provider)
        if not self._current(gen):
            return
        if hit and len(hit["lines"]) == len(lines):
            st.translation = [str(x) for x in hit["lines"]]
            st.trans_status = "skipped" if hit.get("skipped") else "done"
            return
        st.translation, st.trans_status = [], "translating"
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
        self.cache.put_translation(key, target, tr.provider, {"lines": tr.lines, "skipped": tr.skipped,
                                                              "source": tr.source_lang})
        st.translation = tr.lines
        st.trans_status = "skipped" if tr.skipped else "done"
        log.info("translated with %s (source=%s, skipped=%s)", tr.provider, tr.source_lang or "?", tr.skipped)
        if provider == "gemini" and tr.provider == "free":
            self._notice = (i18n.t("st_free_used"), time.time() + 6)

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

        visible = bool(s["visible"]) and (media.has_session or bool(media.error)) and (track is not None or bool(media.error))
        if visible and s["hide_when_idle"] and not media.playing and now - media.last_playing_at > s["idle_hide_delay"]:
            visible = False
        content = self._content(now)
        if content is None:
            visible = False
        if visible and content is not None:
            self.overlay.render(content)
            self.overlay.show()
        else:
            self.overlay.hide()

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
        offset = (self.settings["offset_ms"] + self.cache.get_offset(st.key)) / 1000.0
        pos = self.media.clock.position() - offset + LEAD
        trans = st.translation
        if res.synced:
            lines = res.synced
            times = st.times
            if len(times) != len(lines):  # a worker is swapping lyrics/times right now
                times = [ln.time for ln in lines]
            i = bisect.bisect_right(times, pos) - 1
            original = lines[i].text if i >= 0 else ""
            translation = trans[i] if 0 <= i < len(trans) else ""
            nxt = next((ln.text for ln in lines[i + 1:] if ln.text.strip()), "")
            return Content(original=original, translation=translation, next=nxt, status=status, song=st.key)
        rows = tuple((ln, trans[k] if k < len(trans) else "") for k, ln in enumerate(res.plain))
        dur = st.track.duration if st.track else 0
        progress = min(1.0, max(0.0, pos / dur)) if dur > 0 else -1.0
        return Content(mode="plain", rows=rows, progress=progress, song=st.key,
                       status=status or i18n.t("st_unsynced"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dualyrics", description="Synced lyrics with translation for Windows.")
    parser.add_argument("--settings", action="store_true", help="open the settings window on start")
    parser.add_argument("--startup", action="store_true", help="launched by Windows at sign-in")
    parser.add_argument("--debug", action="store_true", help="verbose log")
    parser.add_argument("--version", action="version", version=f"Dualyrics {__version__}")
    args = parser.parse_args(argv)

    instance = winutil.SingleInstance()
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
