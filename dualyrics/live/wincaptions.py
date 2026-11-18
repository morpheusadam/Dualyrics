"""Windows 11 Live Captions as the speech engine of Live mode.

Windows 11 (22H2 and later) has an offline, low-latency captioner for whatever the PC plays: Live Captions
(``LiveCaptions.exe``). Dualyrics starts it hidden (minimized, a tool window that has no taskbar button, moved
off-screen), reads the text of its caption box (UI Automation element ``CaptionsTextBlock`` in the window of
class ``LiveCaptionsDesktopWindow``) every 25 ms, and turns the growing text into captions:

- the newest sentence is the text after the last sentence terminator (``. ? !`` and those of other scripts);
- a sentence is sent on when it ends, when it has not changed for ~1.25 s, or after 3 changes once 1.5 s
  passed since its start or its last partial (the gap doubles per partial), so a long sentence is translated
  while it is still being spoken without a request per word; the caption is then updated in place;
- Japanese and Chinese line breaks end sentences, and a full stop at the very end is believed after 0.1 s;
- a caption box that went away (ElementNotAvailable) is looked up again; a Live Captions that was closed is
  started again; when it cannot work (old Windows, no language pack, no caption box) Live mode falls back to
  faster-whisper by itself.

The idea and the window/element names come from SakiRinn/LiveCaptions-Translator (Apache-2.0), with a Python
proof in MuuCake/LiveCaptions-Translator-Buddy (MIT); see docs/ATTRIBUTION_NOTES.md. The text logic
(``latest_sentence``, ``SentenceTracker``) and the poll loop (``CaptionsRunner``) are pure and unit-tested
with fakes; ``LiveCaptionsApp`` and ``CaptionBoxReader`` are the thin Windows parts.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dualyrics import langs
from dualyrics.live.interim import TERMINATORS, split_sentences
from dualyrics.live.text import normalize

log = logging.getLogger(__name__)

LIVE_CAPTIONS, WHISPER, AUTO = "live_captions", "whisper", "auto"
ENGINES = (AUTO, LIVE_CAPTIONS, WHISPER)
WINDOW_CLASS = "LiveCaptionsDesktopWindow"
TEXT_ID = "CaptionsTextBlock"
MIN_BUILD = 22621            # Windows 11 22H2
POLL = 0.025                 # seconds between reads of the caption box
IDLE_EMIT = 1.25             # a sentence that stopped changing for this long is sent on
MAX_CHANGES = 3              # ... or after this many changes
START_TIMEOUT = 10.0         # seconds for Live Captions to show its window
BOX_TIMEOUT = 15.0           # no caption box for this long: give up (fallback to Whisper)
MAX_RESTARTS = 3             # Live Captions closed this many times in a row: give up
RETRY_AFTER = 600.0          # after a failure Live mode uses Whisper for this long before trying again
_PACKAGES = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
_SPEECH_PACK = re.compile(r"^MicrosoftWindows\.Speech\.([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+)\.\d+_", re.I)
_QUOTES = "\"'”»)]"


class Unavailable(RuntimeError):
    """Live Captions cannot be used now; Live mode falls back to Whisper."""


# ---------------------------------------------------------------- availability
@dataclass
class Availability:
    ok: bool
    reason: str = ""                       # i18n key suffix when not ok: old_windows, no_exe, no_uia, no_language
    languages: tuple[str, ...] = ()        # installed Live Captions languages (locales such as en-US)

    def supports(self, language: str) -> bool:
        """Live Captions has a language pack for ``language`` (``auto`` is always accepted)."""
        if not language or language == "auto":
            return True
        return any(langs.same_language(language, loc.split("-")[0]) for loc in self.languages)


def exe_path() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "LiveCaptions.exe"


def installed_languages() -> tuple[str, ...]:
    """Locales of the Live Captions speech packs installed for this user (``MicrosoftWindows.Speech.<locale>``
    packages in the per-user package repository of the registry)."""
    if os.name != "nt":
        return ()
    try:
        import winreg

        found = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PACKAGES) as key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                m = _SPEECH_PACK.match(name)
                if m and m.group(1) not in found:
                    found.append(m.group(1))
        return tuple(found)
    except OSError:
        return ()


def windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        return 0


_cache: tuple[float, Availability] | None = None


def availability(force: bool = False, max_age: float = 60.0) -> Availability:
    """Can Live Captions be used on this PC? Cheap (a file check and a registry listing), cached for a minute."""
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache[0] < max_age:
        return _cache[1]
    if os.name != "nt" or windows_build() < MIN_BUILD:
        a = Availability(False, "old_windows")
    elif not exe_path().is_file():
        a = Availability(False, "no_exe")
    elif importlib.util.find_spec("comtypes") is None:
        a = Availability(False, "no_uia")
    else:
        found = installed_languages()
        a = Availability(bool(found), "" if found else "no_language", found)
    _cache = (now, a)
    return a


def choose(pref: str, avail: Availability, source: str, whisper_pivot: bool = False,
           needs_audio: bool = False, failed_recently: bool = False,
           whisper_installed: bool = True) -> tuple[str, str]:
    """(engine, why) for the ``live_engine`` setting.

    ``auto`` prefers Live Captions when it is available, and uses Whisper when the spoken language has no
    Live Captions pack, when the English pivot needs Whisper's speech translation, when dubbing needs the
    captured audio (Gemini Live), or when Live Captions just failed. ``live_captions`` uses it whenever it can
    run (a missing language pack is tried anyway: the check may miss one); ``whisper`` never uses it.
    Without the Whisper packages, Live Captions is used whenever it can run.
    """
    if pref == WHISPER:
        return WHISPER, "chosen"
    if not whisper_installed:            # nothing to fall back to: keep trying Live Captions (with back-off)
        if avail.ok or (pref == LIVE_CAPTIONS and avail.reason == "no_language"):
            return LIVE_CAPTIONS, "no_whisper"
        return WHISPER, avail.reason
    if failed_recently:
        return WHISPER, "failed"
    if pref == LIVE_CAPTIONS:
        if avail.ok or avail.reason == "no_language":
            return LIVE_CAPTIONS, "chosen"
        return WHISPER, avail.reason
    if not avail.ok:
        return WHISPER, avail.reason
    if source and source != "auto" and not avail.supports(source):
        return WHISPER, "language"
    if whisper_pivot:
        return WHISPER, "pivot"
    if needs_audio:
        return WHISPER, "dub_audio"
    return LIVE_CAPTIONS, "auto"


def caption_language(avail: Availability, source: str) -> str:
    """The spoken language to report for Live Captions text: the setting, else the only installed pack."""
    if source and source != "auto":
        return source
    if len(avail.languages) == 1:
        return avail.languages[0].split("-")[0].lower()
    return ""


# ---------------------------------------------------------------- text
SHORT_BYTES = 10             # an unfinished sentence shorter than this (UTF-8 bytes) does not count as a change
MIN_PARTIAL_GAP = 1.5        # seconds from a sentence's start (or its last partial) to its next partial ...
MAX_PARTIAL_GAP = 6.0        # ... doubling per partial up to this (a long sentence is not asked 7 times)
CONFIRM = 0.1                # a full stop at the very end must stay this long (Live Captions takes some back)
CONTINUE_FOR = 4.0           # a finished sentence that goes on within this long keeps its caption
CJK_LINE_BYTES = 24          # a CJK caption line at least this long ends a sentence at its line break
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")
_SPACE_BEFORE = re.compile(r"\s+([.,!?;:…])(?=\s|$)")
_SPACE_AROUND_CJK = re.compile(r"\s*([。！？，、])\s*")


def _join_lines(full: str) -> str:
    """One line of caption text. Live Captions breaks Japanese and Chinese into lines without a full stop:
    a long CJK line ends a sentence (``。``), a short one is joined without a space; other scripts get a
    space (the idea of LiveCaptions-Translator's ``ReplaceNewlines``, written here)."""
    lines = [ln.strip() for ln in (full or "").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    out = ""
    for i, ln in enumerate(lines):
        out += ln
        if i == len(lines) - 1:
            break
        last = ln[-1]
        if _CJK.match(last) and _CJK.match(lines[i + 1][:1]):
            if len(ln.encode("utf-8")) >= CJK_LINE_BYTES:
                out += "。"
        else:
            out += " "
    text = " ".join(out.split())
    text = _SPACE_BEFORE.sub(r"\1", text)
    return _SPACE_AROUND_CJK.sub(r"\1", text)


def sentences(full: str) -> list[tuple[str, bool]]:
    """(sentence, complete) for the caption box text, oldest first; only the last one can be incomplete."""
    out = []
    for s in split_sentences(_join_lines(full), merge_short=False):
        out.append((s, s.rstrip(_QUOTES)[-1:] in TERMINATORS if s else False))
    return out


def latest_sentence(full: str) -> str:
    """The newest sentence: the text after the last terminator, or the last sentence when the text ends with one."""
    ss = sentences(full)
    return ss[-1][0] if ss else ""


def same_sentence(a: str, b: str) -> bool:
    """``b`` is the same sentence as ``a`` grown or corrected (their first words agree)."""
    wa, wb = normalize(a).split(), normalize(b).split()
    if not wa or not wb:
        return False
    k = max(1, min(len(wa), len(wb)) * 6 // 10)
    return wa[:k] == wb[:k]


def continues(done: str, text: str) -> bool:
    """``text`` is the finished sentence ``done`` going on (Live Captions took its full stop back)."""
    d, t = normalize(done), normalize(text)
    return bool(d) and len(t) > len(d) and t.startswith(d + " ")


@dataclass
class Emit:
    text: str
    done: bool                   # the sentence ended (the caption is final)
    key: int                     # same key = the same sentence (update the caption in place)
    start: float                 # when this sentence first appeared


@dataclass
class SentenceTracker:
    """Turns successive caption-box texts into sentence events (see the module docstring).

    Partials (unfinished sentences sent for translation) are rationed: a change counts only from
    ``SHORT_BYTES`` on, and a sentence gets its next partial no sooner than ``min_gap`` after its start or its
    previous partial, the gap doubling each time (a sentence that stopped changing is still sent at once). A
    full stop at the very end is believed only after ``confirm`` seconds; a finished sentence that goes on
    keeps its key, so its caption is updated in place.
    """

    idle: float = IDLE_EMIT
    max_changes: int = MAX_CHANGES
    min_gap: float = MIN_PARTIAL_GAP
    confirm: float = CONFIRM
    primed: bool = False
    cur: str = ""
    cur_key: int = 0
    started_at: float = 0.0
    changed_at: float = 0.0
    changes: int = 0
    emitted: str = ""
    partial_at: float = 0.0
    partials: int = 0
    last_done: tuple[str, int] = ("", 0)
    done_at: float = 0.0
    done_start: float = 0.0
    term: tuple[str, float] = ("", 0.0)
    done: deque = field(default_factory=lambda: deque(maxlen=40))
    _next: int = 1

    def reset(self) -> None:
        """Forget the current sentence; text already in the box is not sent again (after a pause or restart)."""
        self.primed = False
        self.cur, self.cur_key, self.changes, self.emitted = "", 0, 0, ""
        self.last_done, self.term = ("", 0), ("", 0.0)

    def _key(self) -> int:
        k = self._next
        self._next += 1
        return k

    def _finish(self, s: str, n: str, now: float, out: list[Emit]) -> None:
        if self.cur and same_sentence(self.cur, s):
            out.append(Emit(s, True, self.cur_key, self.started_at))
            self.last_done, self.done_start = (n, self.cur_key), self.started_at
            self.cur, self.cur_key, self.changes, self.emitted = "", 0, 0, ""
        elif self.last_done[1] and same_sentence(self.last_done[0], s):
            out.append(Emit(s, True, self.last_done[1], now))      # the finished sentence corrected
            self.last_done = (n, self.last_done[1])
        else:
            key = self._key()
            out.append(Emit(s, True, key, now))
            self.last_done, self.done_start = (n, key), now
        self.done_at = now

    def _gap(self) -> float:
        return min(MAX_PARTIAL_GAP, self.min_gap * (2 ** self.partials))

    def _partial(self, last: str, out: list[Emit], now: float) -> None:
        out.append(Emit(last, False, self.cur_key, self.started_at))
        self.changes, self.emitted, self.partial_at = 0, last, now
        self.partials += 1

    def feed(self, full: str, now: float) -> list[Emit]:
        ss = sentences(full)
        out: list[Emit] = []
        if not ss:
            self.cur, self.changes, self.emitted = "", 0, ""
            return out
        if not self.primed:              # text that was there before we looked is not captioned
            self.primed = True
            for s, complete in ss:
                if complete:
                    self.done.append(normalize(s))
        else:
            for idx, (s, complete) in enumerate(ss):
                n = normalize(s)
                if not complete or n in self.done:
                    continue
                if idx == len(ss) - 1 and self.confirm > 0:
                    if self.term[0] != n:
                        self.term = (n, now)
                    if now - self.term[1] < self.confirm:
                        continue         # a full stop at the very end: wait, it may be taken back
                self.done.append(n)
                if idx < len(ss) - 2:
                    continue             # an old line revised: not shown again
                self._finish(s, n, now, out)
        last, complete = ss[-1]
        if complete:
            return out
        if last != self.cur:
            if not (self.cur and same_sentence(self.cur, last)):
                if (not self.cur and self.last_done[1] and now - self.done_at <= CONTINUE_FOR
                        and continues(self.last_done[0], last)):
                    # "I think we." became "I think we should go": the same caption, updated in place
                    self.cur_key, self.started_at = self.last_done[1], self.done_start
                    self.last_done = ("", 0)
                else:
                    self.cur_key, self.started_at = self._key(), now
                self.changes, self.emitted, self.partial_at, self.partials = 0, "", now, 0
            self.cur = last
            self.changed_at = now
            if len(last.encode("utf-8")) >= SHORT_BYTES:
                self.changes += 1
            if self.changes >= self.max_changes and now - self.partial_at >= self._gap():
                self._partial(last, out, now)
        elif self.emitted != last and now - self.changed_at >= self.idle:
            self._partial(last, out, now)
        return out

    @property
    def active(self) -> bool:
        """A sentence is being spoken (for the caption's linger time)."""
        return bool(self.cur)


# ---------------------------------------------------------------- poll loop (pure, with fakes in tests)
class CaptionsRunner:
    """Keeps Live Captions running and its caption box read; ``step`` is one poll.

    ``app`` has ``ensure() -> hwnd``, ``alive(hwnd)``, ``keep_hidden(hwnd)`` and ``close()``; ``reader`` has
    ``read(hwnd) -> str | None`` (None: no caption box right now) and ``reset()``.
    """

    def __init__(self, app: Any, reader: Any, on_emit: Callable[[Emit], None], box_timeout: float = BOX_TIMEOUT,
                 max_restarts: int = MAX_RESTARTS) -> None:
        self.app, self.reader, self.on_emit = app, reader, on_emit
        self.tracker = SentenceTracker()
        self.box_timeout = box_timeout
        self.max_restarts = max_restarts
        self.hwnd = 0
        self.restarts = 0
        self.box_since = 0.0
        self.working_since = 0.0
        self._hide_at = 0.0

    def start(self, now: float) -> None:
        self.hwnd = self.app.ensure()
        self.reader.reset()
        self.box_since = now
        self._hide_at = now

    def step(self, now: float) -> None:
        if not self.hwnd or not self.app.alive(self.hwnd):
            self.restarts += 1
            if self.restarts > self.max_restarts:
                raise Unavailable("Live Captions keeps closing")
            log.info("Live Captions was closed: starting it again (%d)", self.restarts)
            self.tracker.reset()
            self.start(now)
            return
        if now - self._hide_at >= 1.0:   # it may show itself again after its start-up
            self._hide_at = now
            self.app.keep_hidden(self.hwnd)
        text = self.reader.read(self.hwnd)
        if text is None:
            if now - self.box_since > self.box_timeout:
                raise Unavailable("no caption text in Live Captions (is a language installed?)")
            return
        self.box_since = now
        if not self.working_since:
            self.working_since = now
        elif self.restarts and now - self.working_since > 60:
            self.restarts = 0
        for e in self.tracker.feed(text, now):
            self.on_emit(e)

    def close(self) -> None:
        try:
            self.app.close()
        except Exception:
            log.debug("closing Live Captions failed", exc_info=True)


# ---------------------------------------------------------------- Windows parts
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_SHOWMINNOACTIVE = 7
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0001, 0x0004, 0x0010, 0x0020
WM_CLOSE = 0x0010
OFF_SCREEN = -32000


class _Win32:
    """The few user32 calls needed to keep Live Captions out of sight (no focus is ever taken)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        self.ct, self.w = ctypes, w
        u = ctypes.windll.user32
        self.u = u
        u.FindWindowW.argtypes, u.FindWindowW.restype = [w.LPCWSTR, w.LPCWSTR], w.HWND
        u.IsWindow.argtypes, u.IsWindow.restype = [w.HWND], w.BOOL
        u.IsIconic.argtypes, u.IsIconic.restype = [w.HWND], w.BOOL
        u.ShowWindow.argtypes, u.ShowWindow.restype = [w.HWND, ctypes.c_int], w.BOOL
        u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        u.SetWindowPos.restype = w.BOOL
        u.PostMessageW.argtypes, u.PostMessageW.restype = [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL
        get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        put = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        get.argtypes, get.restype = [w.HWND, ctypes.c_int], ctypes.c_ssize_t
        put.argtypes, put.restype = [w.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
        self._get, self._put = get, put

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT), ("ptMinPosition", w.POINT),
                        ("ptMaxPosition", w.POINT), ("rcNormalPosition", w.RECT)]

        self.WP = WINDOWPLACEMENT
        u.GetWindowPlacement.argtypes = [w.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
        u.SetWindowPlacement.argtypes = [w.HWND, ctypes.POINTER(WINDOWPLACEMENT)]

    def find(self) -> int:
        return int(self.u.FindWindowW(WINDOW_CLASS, None) or 0)

    def is_window(self, hwnd: int) -> bool:
        return bool(self.u.IsWindow(hwnd))

    def is_iconic(self, hwnd: int) -> bool:
        return bool(self.u.IsIconic(hwnd))

    def ex_style(self, hwnd: int) -> int:
        return int(self._get(hwnd, GWL_EXSTYLE))

    def set_ex_style(self, hwnd: int, value: int) -> None:
        self._put(hwnd, GWL_EXSTYLE, value)

    def placement(self, hwnd: int) -> Any:
        wp = self.WP()
        wp.length = self.ct.sizeof(self.WP)
        self.u.GetWindowPlacement(hwnd, self.ct.byref(wp))
        return wp

    def set_placement(self, hwnd: int, wp: Any) -> None:
        wp.showCmd = SW_SHOWMINNOACTIVE
        self.u.SetWindowPlacement(hwnd, self.ct.byref(wp))

    def hide(self, hwnd: int) -> None:
        self.set_ex_style(hwnd, (self.ex_style(hwnd) | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        self.u.SetWindowPos(hwnd, 0, OFF_SCREEN, OFF_SCREEN, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        self.u.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)

    def close(self, hwnd: int) -> None:
        self.u.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def launch(self, exe: Path) -> Any:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWMINNOACTIVE
        return subprocess.Popen([str(exe)], startupinfo=si, close_fds=True)


class LiveCaptionsApp:
    """Starts Live Captions hidden, or uses the one the user already has open (left as it is). Only a Live
    Captions this app started is closed again, with its window style and position given back first."""

    def __init__(self, api: Any = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, start_timeout: float = START_TIMEOUT,
                 exe: Path | None = None) -> None:
        self.api = api if api is not None else _Win32()
        self.exe = exe or exe_path()
        self.sleep, self.clock = sleep, clock
        self.start_timeout = start_timeout
        self.ours = False
        self.closed = False                                 # closed by the app: never started again
        self.proc: Any = None
        self._saved: tuple[int, int, Any] | None = None    # (hwnd, ex style, placement) before we hid it

    def adopt(self) -> bool:
        """Take over a Live Captions hidden by an earlier run of this app that did not close it (minimized
        tool window). Returns True when there is one; a window the user opened is never taken."""
        hwnd = self.api.find()
        if not hwnd:
            return False
        ex = self.api.ex_style(hwnd)
        if not self.ours and self.api.is_iconic(hwnd) and ex & WS_EX_TOOLWINDOW:
            self.ours = True
            self._saved = (hwnd, ex & ~WS_EX_TOOLWINDOW, self.api.placement(hwnd))
        return self.ours

    def ensure(self) -> int:
        if self.closed:
            raise Unavailable("Live Captions was closed by Dualyrics")
        hwnd = self.api.find()
        if hwnd:
            if self.adopt():
                self._hide(hwnd)
            return hwnd
        if not self.exe.is_file():
            raise Unavailable("LiveCaptions.exe not found")
        try:
            self.proc = self.api.launch(self.exe)
        except OSError as e:
            raise Unavailable(f"Live Captions did not start: {e}") from e
        self.ours = True
        deadline = self.clock() + self.start_timeout
        while self.clock() < deadline:
            hwnd = self.api.find()
            if hwnd:
                self._hide(hwnd)
                if self.closed:            # closed while it was starting: do not leave it running
                    self._shut(wait=False)
                    raise Unavailable("Live Captions was closed by Dualyrics")
                return hwnd
            if self.closed:
                self._shut(wait=False)
                raise Unavailable("Live Captions was closed by Dualyrics")
            self.sleep(0.02)
        self._shut(wait=False)             # never left running without a window we keep hidden
        raise Unavailable("Live Captions did not open its window")

    def _hide(self, hwnd: int) -> None:
        if self._saved is None or self._saved[0] != hwnd:
            self._saved = (hwnd, self.api.ex_style(hwnd), self.api.placement(hwnd))
        self.api.hide(hwnd)

    def keep_hidden(self, hwnd: int) -> None:
        if self.ours and not (self.api.is_iconic(hwnd) and self.api.ex_style(hwnd) & WS_EX_TOOLWINDOW):
            self.api.hide(hwnd)

    def alive(self, hwnd: int) -> bool:
        return bool(hwnd) and self.api.is_window(hwnd)

    def close(self, wait: bool = True) -> None:
        self.closed = True
        if self.ours:
            self._shut(wait)

    def _shut(self, wait: bool) -> None:
        hwnd = self.api.find()
        if hwnd:
            if self._saved is not None and self._saved[0] == hwnd:
                _h, ex, wp = self._saved
                self.api.set_ex_style(hwnd, ex)
                self.api.set_placement(hwnd, wp)
            self.api.close(hwnd)
        proc = self.proc
        if proc is not None and (wait or not hwnd):
            try:
                if not hwnd:
                    raise TimeoutError     # no window to close (still starting, or it never opened one)
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        self.ours, self.proc, self._saved = False, None, None


class CaptionBoxReader:
    """Reads the caption box of Live Captions through UI Automation (use from one thread)."""

    SEARCH_EVERY = 0.25

    def __init__(self) -> None:
        from dualyrics.screen import uia

        self.m, self.uia = uia.create_automation()
        self.cond = self.uia.CreatePropertyCondition(self.m.UIA_AutomationIdPropertyId, TEXT_ID)
        self._hwnd = 0
        self._box: Any = None
        self._search_at = -1e9

    def reset(self) -> None:
        self._hwnd, self._box, self._search_at = 0, None, -1e9

    def read(self, hwnd: int) -> str | None:
        if hwnd != self._hwnd:
            self.reset()
            self._hwnd = hwnd
        try:
            if self._box is None:
                now = time.monotonic()
                if now - self._search_at < self.SEARCH_EVERY:
                    return None
                self._search_at = now
                root = self.uia.ElementFromHandle(hwnd)
                box = root.FindFirst(self.m.TreeScope_Descendants, self.cond)
                if not box:
                    return None
                self._box = box
            return self._box.CurrentName or ""
        except Exception as e:  # UIA_E_ELEMENTNOTAVAILABLE and friends: look the box up again
            log.debug("live captions read: %s", e)
            self._box = None
            return None
