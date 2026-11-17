"""Settings: defaults, validation, JSON persistence and live change notifications.

Settings are stored in %APPDATA%\\Dualyrics\\settings.json. API keys are encrypted with
Windows DPAPI (bound to the current Windows user) before they are written to disk.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from dualyrics import langs, paths, profiles as profiles_mod

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

DEFAULT_STYLE = (
    "Translate like a gifted song translator, not word for word. Use natural, fluent, everyday language "
    "that a native speaker would actually say. Convey the meaning, feeling, idioms, slang, metaphors and "
    "wordplay with real equivalents in the target language. Keep every line short enough to read while it "
    "is being sung."
)

DEFAULT_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]
DEFAULT_PLAYERS = ["spotify", "spotube", "zunemusic", "chrome", "msedge", "firefox", "brave", "opera", "vlc"]

ALIGNMENTS = ("center", "left", "right")
PRESETS = ("bottom", "top", "center", "custom")
PROVIDERS = ("gemini", "free")
PROXY_MODES = ("none", "system", "custom")
UI_LANGUAGES = ("en", "fa")
MODES = ("auto", "lyrics", "live")
LIVE_DEVICES = ("auto", "cpu", "gpu")
LIVE_SIZES = ("tiny", "base", "small", "medium")
UI_THEMES = ("system", "dark", "light")
DESKTOP_MODES = ("all", "current")
BACKDROPS = ("dark", "bright", "game", "checker")
SECTIONS = ("general", "profiles", "templates", "lyrics", "live", "translation", "monitors", "sync", "network",
            "about")
_TEMPLATE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_PROXY = re.compile(r"^(https?|socks5h?|socks4a?)://[^\s/]+(:\d{1,5})?/?$", re.I)
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _default_ui_language() -> str:
    return "fa" if langs.windows_ui_language() == "fa" else "en"


def defaults() -> dict[str, Any]:
    """Fresh default settings (computed, because some depend on the Windows language)."""
    return {
        "version": SCHEMA_VERSION,
        # general
        "ui_language": _default_ui_language(),
        "target_language": langs.windows_ui_language(),
        "start_with_windows": False,
        "hide_when_idle": True,
        "idle_hide_delay": 3.0,
        "preferred_players": list(DEFAULT_PLAYERS),
        # translation
        "provider": "free",
        "gemini_keys": [],
        "gemini_models": list(DEFAULT_MODELS),
        "fallback_free": True,
        "style": DEFAULT_STYLE,
        "skip_same_language": True,
        # appearance
        "font_original": "auto",
        "font_translation": "auto",
        "size_original": 20,
        "size_translation": 22,
        "bold_original": True,
        "bold_translation": True,
        "color_original": "#FFFFFF",
        "color_translation": "#FFD54F",
        "color_next": "#9A9A9A",
        "color_bg": "#101014",
        "opacity": 0.88,
        "text_only": False,
        "outline": True,
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": True,
        "width": 900,
        "align": "center",
        "show_original": True,
        "show_translation": True,
        "show_next": True,
        "rounded": True,
        "plain_height": 320,
        # window state
        "position_preset": "bottom",
        "positions": {},
        "monitor": "",
        "locked": False,
        "click_through": False,
        "visible": True,
        # sync
        "offset_ms": 0,
        "offset_step_ms": 250,
        "hotkeys_enabled": True,
        "hotkey_offset_up": "ctrl+alt+up",
        "hotkey_offset_down": "ctrl+alt+down",
        "hotkey_toggle": "ctrl+alt+l",
        # network
        "proxy_mode": "system",
        "proxy_url": "",
        # v2: smart mode, per-app profiles, translation only
        "mode": "auto",
        "translation_only": False,
        "profiles_enabled": True,
        "profile_activation": "running",
        "profiles": profiles_mod.builtin_profiles(),
        # v2: Live mode (captions for what the speakers play)
        "live_enabled": True,
        "live_when_no_lyrics": True,
        "live_model": "small",
        "live_english_only": False,
        "live_source_language": "auto",
        "live_device": "auto",
        "live_vad_sensitivity": 5,
        "live_max_segment": 6.0,
        "live_reading_cps": 17.0,
        "live_min_display": 1.5,
        "live_context_lines": 4,
        "live_save_transcripts": False,
        "hotkey_live_pause": "ctrl+alt+p",
        # v2: templates, overlay window, monitors, settings window
        "template": "cinema",
        "ui_theme": "dark",
        "overlay_scale": 1.0,
        "overlay_opacity": 1.0,
        "monitors_off": [],
        "follow_active_monitor": False,
        "virtual_desktops": "all",
        "hotkey_click_through": "ctrl+alt+k",
        "preview_backdrop": "dark",
        "preview_collapsed": False,
        "settings_section": "general",
    }


# ---------------------------------------------------------------- validation helpers
def _bool(v: Any, d: bool) -> bool:
    return v if isinstance(v, bool) else d


def _num(v: Any, d: float, lo: float, hi: float, integer: bool = False) -> float | int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return d
    if v != v:  # NaN
        return d
    v = min(hi, max(lo, v))
    return int(round(v)) if integer else float(v)


def _choice(v: Any, d: str, options: tuple[str, ...] | list[str]) -> str:
    return v if isinstance(v, str) and v in options else d


def _color(v: Any, d: str) -> str:
    return v.upper() if isinstance(v, str) and _COLOR.match(v) else d


def _str(v: Any, d: str, max_len: int = 4000) -> str:
    return v[:max_len] if isinstance(v, str) else d


def _str_list(v: Any, d: list[str], max_items: int = 50, lower: bool = False) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,\n]", v)
    if not isinstance(v, list):
        return list(d)
    out: list[str] = []
    for item in v:
        if isinstance(item, str):
            s = item.strip().lower() if lower else item.strip()
            if s and s not in out:
                out.append(s[:200])
    return out[:max_items]


def _positions(v: Any) -> dict[str, dict[str, Any]]:
    """Saved overlay places, keyed by monitor name or ``<profile id>@<monitor>``: position (x, y relative to
    the monitor work area, anchored at the top or bottom edge) and/or size (box width ``w`` in px, text
    ``scale``)."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(v, dict):
        return out
    for mon, pos in list(v.items())[:64]:
        if not isinstance(mon, str) or not isinstance(pos, dict):
            continue
        entry: dict[str, Any] = {}
        x, y = pos.get("x"), pos.get("y")
        if isinstance(x, int) and isinstance(y, int) and not isinstance(x, bool) and not isinstance(y, bool):
            entry.update(x=max(-20000, min(20000, x)), y=max(-20000, min(20000, y)),
                         anchor="top" if pos.get("anchor") == "top" else "bottom")
        w = pos.get("w")
        if isinstance(w, int) and not isinstance(w, bool) and w > 0:
            entry["w"] = max(120, min(8000, w))
        sc = pos.get("scale")
        if isinstance(sc, (int, float)) and not isinstance(sc, bool) and sc == sc and sc > 0:
            entry["scale"] = round(max(0.5, min(2.0, float(sc))), 3)
        if entry:
            out[mon[:150]] = entry
    return out


_HOTKEY = re.compile(r"^(?:(?:ctrl|alt|shift|win)\+){1,4}[a-z0-9]+$")


def _hotkey(v: Any, d: str) -> str:
    if isinstance(v, str):
        s = v.strip().lower().replace(" ", "")
        if s == "" or _HOTKEY.match(s):
            return s
    return d


def validate(raw: Any) -> dict[str, Any]:
    """Return a complete, valid settings dict built from ``raw`` (unknown keys dropped, bad values reset)."""
    d = defaults()
    r = raw if isinstance(raw, dict) else {}
    lang_codes = [lang.code for lang in langs.LANGUAGES]
    s: dict[str, Any] = {"version": SCHEMA_VERSION}
    s["ui_language"] = _choice(r.get("ui_language"), d["ui_language"], UI_LANGUAGES)
    s["target_language"] = _choice(r.get("target_language"), d["target_language"], lang_codes)
    s["start_with_windows"] = _bool(r.get("start_with_windows"), d["start_with_windows"])
    s["hide_when_idle"] = _bool(r.get("hide_when_idle"), d["hide_when_idle"])
    s["idle_hide_delay"] = _num(r.get("idle_hide_delay"), d["idle_hide_delay"], 0, 60)
    s["preferred_players"] = _str_list(r.get("preferred_players"), d["preferred_players"], lower=True)

    s["provider"] = _choice(r.get("provider"), d["provider"], PROVIDERS)
    s["gemini_keys"] = _str_list(r.get("gemini_keys"), [], max_items=20)
    # model names go into the request URL path: only plain ids such as "gemini-2.5-flash"
    s["gemini_models"] = [m for m in _str_list(r.get("gemini_models"), d["gemini_models"], max_items=20)
                          if _MODEL.match(m)] or list(DEFAULT_MODELS)
    s["fallback_free"] = _bool(r.get("fallback_free"), d["fallback_free"])
    s["style"] = _str(r.get("style"), d["style"]).strip() or d["style"]
    s["skip_same_language"] = _bool(r.get("skip_same_language"), d["skip_same_language"])

    for k in ("font_original", "font_translation"):
        s[k] = _str(r.get(k), d[k], 100).strip() or "auto"
    s["size_original"] = _num(r.get("size_original"), d["size_original"], 8, 96, True)
    s["size_translation"] = _num(r.get("size_translation"), d["size_translation"], 8, 96, True)
    for k in ("bold_original", "bold_translation", "text_only", "outline", "shadow", "show_original",
              "show_translation", "show_next", "rounded", "locked", "click_through", "visible"):
        s[k] = _bool(r.get(k), d[k])
    for k in ("color_original", "color_translation", "color_next", "color_bg", "outline_color"):
        s[k] = _color(r.get(k), d[k])
    s["opacity"] = _num(r.get("opacity"), d["opacity"], 0.2, 1.0)
    s["outline_width"] = _num(r.get("outline_width"), d["outline_width"], 0, 4, True)
    s["width"] = _num(r.get("width"), d["width"], 300, 4000, True)
    s["align"] = _choice(r.get("align"), d["align"], ALIGNMENTS)
    s["plain_height"] = _num(r.get("plain_height"), d["plain_height"], 120, 1600, True)

    s["position_preset"] = _choice(r.get("position_preset"), d["position_preset"], PRESETS)
    s["positions"] = _positions(r.get("positions"))
    s["monitor"] = _str(r.get("monitor"), "", 100)

    s["offset_ms"] = _num(r.get("offset_ms"), d["offset_ms"], -15000, 15000, True)
    s["offset_step_ms"] = _num(r.get("offset_step_ms"), d["offset_step_ms"], 50, 2000, True)
    s["hotkeys_enabled"] = _bool(r.get("hotkeys_enabled"), d["hotkeys_enabled"])
    for k in ("hotkey_offset_up", "hotkey_offset_down", "hotkey_toggle", "hotkey_live_pause", "hotkey_click_through"):
        s[k] = _hotkey(r.get(k), d[k])

    s["proxy_mode"] = _choice(r.get("proxy_mode"), d["proxy_mode"], PROXY_MODES)
    url = _str(r.get("proxy_url"), "", 300).strip()
    s["proxy_url"] = url if url == "" or _PROXY.match(url) else ""
    if s["proxy_mode"] == "custom" and not s["proxy_url"]:
        s["proxy_mode"] = "system"

    s["mode"] = _choice(r.get("mode"), d["mode"], MODES)
    s["translation_only"] = _bool(r.get("translation_only"), d["translation_only"])
    s["profiles_enabled"] = _bool(r.get("profiles_enabled"), d["profiles_enabled"])
    s["profile_activation"] = _choice(r.get("profile_activation"), d["profile_activation"],
                                      profiles_mod.GLOBAL_ACTIVATIONS)
    s["profiles"] = profiles_mod.validate_profiles(r.get("profiles"), lang_codes)
    for k in ("live_enabled", "live_when_no_lyrics", "live_english_only", "live_save_transcripts"):
        s[k] = _bool(r.get(k), d[k])
    s["live_model"] = _choice(r.get("live_model"), d["live_model"], LIVE_SIZES)
    s["live_source_language"] = _choice(r.get("live_source_language"), d["live_source_language"],
                                        ["auto"] + lang_codes)
    s["live_device"] = _choice(r.get("live_device"), d["live_device"], LIVE_DEVICES)
    s["live_vad_sensitivity"] = _num(r.get("live_vad_sensitivity"), d["live_vad_sensitivity"], 1, 10, True)
    s["live_max_segment"] = _num(r.get("live_max_segment"), d["live_max_segment"], 2, 15)
    s["live_reading_cps"] = _num(r.get("live_reading_cps"), d["live_reading_cps"], 8, 30)
    s["live_min_display"] = _num(r.get("live_min_display"), d["live_min_display"], 0.5, 6)
    s["live_context_lines"] = _num(r.get("live_context_lines"), d["live_context_lines"], 0, 12, True)

    tpl = r.get("template")
    s["template"] = tpl if isinstance(tpl, str) and _TEMPLATE_ID.match(tpl) else d["template"]
    s["ui_theme"] = _choice(r.get("ui_theme"), d["ui_theme"], UI_THEMES)
    s["overlay_scale"] = round(_num(r.get("overlay_scale"), d["overlay_scale"], 0.5, 2.0), 3)
    s["overlay_opacity"] = round(_num(r.get("overlay_opacity"), d["overlay_opacity"], 0.2, 1.0), 3)
    s["monitors_off"] = _str_list(r.get("monitors_off"), [], max_items=16)
    s["follow_active_monitor"] = _bool(r.get("follow_active_monitor"), d["follow_active_monitor"])
    s["virtual_desktops"] = _choice(r.get("virtual_desktops"), d["virtual_desktops"], DESKTOP_MODES)
    s["preview_backdrop"] = _choice(r.get("preview_backdrop"), d["preview_backdrop"], BACKDROPS)
    s["preview_collapsed"] = _bool(r.get("preview_collapsed"), d["preview_collapsed"])
    s["settings_section"] = _choice(r.get("settings_section"), d["settings_section"], SECTIONS)
    return s


def valid_proxy_url(url: str) -> bool:
    return bool(_PROXY.match(url.strip()))


# ---------------------------------------------------------------- DPAPI key protection
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = Blob()
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):  # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(dst.pbData, ctypes.c_void_p))


def protect_keys(keys: list[str]) -> str:
    raw = json.dumps(keys).encode("utf-8")
    try:
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    except Exception as e:  # non-Windows or DPAPI unavailable
        log.warning("DPAPI unavailable (%s): API keys are stored only base64-encoded", type(e).__name__)
        return "b64:" + base64.b64encode(raw).decode("ascii")


def unprotect_keys(blob: Any) -> list[str]:
    if not isinstance(blob, str) or ":" not in blob:
        return []
    kind, data = blob.split(":", 1)
    try:
        raw = base64.b64decode(data)
        if kind == "dpapi":
            raw = _dpapi(raw, False)
        keys = json.loads(raw.decode("utf-8"))
        return [k for k in keys if isinstance(k, str)]
    except Exception as e:
        log.warning("could not decrypt stored API keys: %s", e)
        return []


# ---------------------------------------------------------------- store
Listener = Callable[[set[str]], None]


class Settings:
    """Thread-safe settings store. ``update()`` validates, saves and notifies listeners of changed keys."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.settings_file()
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._listeners: list[Listener] = []
        self.first_run = not self.path.exists()
        self.loaded_keys: set[str] = set()     # keys present in settings.json at start (v1 -> v2 migration)
        self.rev = 0                           # grows on every change (lets readers cache merged views)
        self._kept_keys_blob = ""              # encrypted keys this Windows user cannot decrypt: never dropped
        self._data = self._load()
        if self.first_run:
            self.save()

    # -- persistence
    def _load(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if self.path.exists():
            try:
                # utf-8-sig: a file saved by Notepad or PowerShell 5 starts with a BOM; it is still valid
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                log.error("settings.json is unreadable (%s); using defaults", e)
                try:
                    self.path.replace(self.path.with_suffix(".broken.json"))
                except OSError:
                    pass
                raw = {}
        if isinstance(raw, dict):
            self.loaded_keys = set(raw)
        if isinstance(raw, dict) and "gemini_keys_protected" in raw:
            blob = raw.pop("gemini_keys_protected")
            raw["gemini_keys"] = unprotect_keys(blob)
            if not raw["gemini_keys"] and isinstance(blob, str) and blob:
                # e.g. settings copied from another PC or Windows user: keep the blob instead of erasing the
                # keys on the next save; entering new keys replaces it
                self._kept_keys_blob = blob
        return validate(raw)

    def save(self) -> None:
        with self._save_lock:  # serialise writers so an older snapshot never replaces a newer one
            with self._lock:
                data = copy.deepcopy(self._data)
                kept = self._kept_keys_blob
            keys = data.pop("gemini_keys", [])
            data["gemini_keys_protected"] = protect_keys(keys) if keys else kept
            tmp = self.path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as e:
                log.error("could not save settings: %s", e)

    # -- access
    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data[key])

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def update(self, **changes: Any) -> set[str]:
        """Apply changes, validate, persist, notify. Returns the keys whose value really changed."""
        with self._lock:
            merged = copy.deepcopy(self._data)
            merged.update(changes)
            new = validate(merged)
            changed = {k for k in new if new[k] != self._data.get(k)}
            self._data = new
            if changed:
                self.rev += 1
            if "gemini_keys" in changed:
                self._kept_keys_blob = ""   # the user set the keys: the old undecryptable ones are replaced
        if changed:
            self.save()
            for fn in list(self._listeners):
                try:
                    fn(changed)
                except Exception:
                    log.exception("settings listener failed")
        return changed

    def reset(self, keys: list[str]) -> None:
        d = defaults()
        self.update(**{k: d[k] for k in keys if k in d})
