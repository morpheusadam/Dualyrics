"""Per-app profiles: which app is in use decides the mode, look and placement of the overlay.

A profile is a plain dict (stored in settings.json under ``profiles``). Fields:

- ``id``, ``name``, ``exes`` (exe file names, lower case), ``path`` (optional full exe path: then only that
  exact file matches), ``icon`` (file to take the icon from; default the exe), ``media_ids`` (substrings of the
  Windows media session app id, e.g. ``spotify``), ``kind`` (music / video / browser / game / other),
  ``builtin``.
- behaviour: ``enabled``, ``mode`` (auto / lyrics / live / off), ``template`` (template id, '' = global),
  ``target_language`` ('' = global), ``monitor`` ('' = global), ``position`` ('' = global or a preset),
  ``width`` (0 = global), ``click_through`` / ``translation_only`` (None = global), ``activation``
  (default / foreground / running).
- sources (v2.1): ``sources_custom`` (False = the global order), ``source_order`` and ``sources_off`` (see
  ``dualyrics.sources``), ``ocr_region`` ([] = the global subtitle region, else [x0, y0, x1, y1] fractions).
- dubbing (v2.1): ``dub_custom`` (False = the global dubbing settings), ``dub_enabled``, ``dub_voice``,
  ``dub_speed``, ``dub_volume``.

Two special built-ins have no exe: ``fullscreen`` (any fullscreen app without its own profile, the Games
preset) and ``default`` (everything else, off out of the box). ``media_any`` catches media sessions of
players that have no profile, so every player that worked in v1 keeps working.
"""

from __future__ import annotations

import copy
import ntpath
import re
from dataclasses import dataclass
from typing import Any, Iterable

MODES = ("auto", "lyrics", "live", "off")
KINDS = ("music", "video", "browser", "game", "other")
ACTIVATIONS = ("default", "foreground", "running")
GLOBAL_ACTIVATIONS = ("foreground", "running")
POSITIONS = ("", "bottom", "top", "center")
DEFAULT_ID = "default"
FULLSCREEN_ID = "fullscreen"
MEDIA_ANY_ID = "media_any"
MAX_PROFILES = 100
LIVE_KINDS = ("game", "video")      # kinds whose foreground use means "captions for what is heard"

BEHAVIOUR: dict[str, Any] = {
    "enabled": True,
    "mode": "auto",
    "template": "",
    "target_language": "",
    "monitor": "",
    "position": "",
    "width": 0,
    "click_through": None,
    "translation_only": None,
    "activation": "default",
    "sources_custom": False,
    "source_order": [],
    "sources_off": [],
    "ocr_region": [],
    # v2.1 dubbing: with dub_custom the profile has its own on/off, voice, speed and volume
    "dub_custom": False,
    "dub_enabled": False,
    "dub_voice": "auto",
    "dub_speed": 1.0,
    "dub_volume": 90,
}

# presets used when the user adds an app
PRESETS: dict[str, dict[str, Any]] = {
    "games": {"kind": "game", "mode": "live", "template": "game_hud", "click_through": True,
              "position": "top", "activation": "foreground"},
    "video": {"kind": "video", "mode": "auto"},
    "music": {"kind": "music", "mode": "lyrics"},
    "custom": {"kind": "other", "mode": "auto"},
}


def _builtin(pid: str, name: str, exes: list[str], kind: str, media_ids: list[str] | None = None,
             **behaviour: Any) -> dict[str, Any]:
    p = {"id": pid, "name": name, "exes": exes, "path": "", "icon": "", "kind": kind, "builtin": True,
         "media_ids": media_ids if media_ids is not None else [ntpath.splitext(e)[0] for e in exes]}
    p.update(copy.deepcopy(BEHAVIOUR))
    p.update(behaviour)
    return p


def builtin_profiles() -> list[dict[str, Any]]:
    """Fresh built-in profiles, in matching order (the two catch-alls last)."""
    return [
        _builtin("spotify", "Spotify", ["spotify.exe"], "music"),
        _builtin("spotube", "Spotube", ["spotube.exe"], "music"),
        _builtin("music_apps", "Music apps", [
            "applemusic.exe", "itunes.exe", "aimp.exe", "foobar2000.exe", "musicbee.exe", "tidal.exe",
            "deezer.exe", "youtube music.exe", "youtube-music.exe", "winamp.exe", "microsoft.media.player.exe",
        ], "music", media_ids=["applemusic", "itunes", "aimp", "foobar2000", "musicbee", "tidal", "deezer",
                               "youtube music", "youtube-music", "winamp", "zunemusic", "microsoft.media.player"]),
        _builtin("browsers", "Web browsers", [
            "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe",
            "zen.exe", "yandex.exe", "comet.exe",
        ], "browser", media_ids=["chrome", "msedge", "firefox", "308046b0af4a39cb", "brave", "opera", "vivaldi",
                                 "thebrowsercompany.arc", "yandex", "comet"]),
        _builtin("vlc", "VLC", ["vlc.exe"], "video"),
        _builtin("mpc", "MPC-HC / MPC-BE", ["mpc-hc64.exe", "mpc-hc.exe", "mpc-be64.exe", "mpc-be.exe"], "video",
                 media_ids=["mpc-hc", "mpc-be"]),
        _builtin("potplayer", "PotPlayer", ["potplayermini64.exe", "potplayermini.exe", "potplayer64.exe",
                                            "potplayer.exe"], "video", media_ids=["potplayer"]),
        _builtin(MEDIA_ANY_ID, "Other media players", [], "music", media_ids=[]),
        _builtin(FULLSCREEN_ID, "Fullscreen games and apps", [], "game", media_ids=[],
                 **{k: v for k, v in PRESETS["games"].items() if k != "kind"}),
        _builtin(DEFAULT_ID, "Everything else", [], "other", media_ids=[], enabled=False),
    ]


BUILTIN_IDS = tuple(p["id"] for p in builtin_profiles())
SPECIAL_IDS = (MEDIA_ANY_ID, FULLSCREEN_ID, DEFAULT_ID)


# ---------------------------------------------------------------- validation
_ID = re.compile(r"[^a-z0-9_]+")


def slug(text: str) -> str:
    s = _ID.sub("_", (text or "").lower()).strip("_")
    return s[:40] or "app"


def _exe_name(v: Any) -> str:
    if not isinstance(v, str):
        return ""
    s = ntpath.basename(v.strip().strip('"')).lower()
    return s[:120] if s.endswith(".exe") else ""


def _opt_bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _validate_behaviour(raw: dict[str, Any], base: dict[str, Any], lang_codes: Iterable[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["enabled"] = raw["enabled"] if isinstance(raw.get("enabled"), bool) else base["enabled"]
    out["mode"] = raw["mode"] if raw.get("mode") in MODES else base["mode"]
    tpl = raw.get("template")
    out["template"] = tpl if isinstance(tpl, str) and re.fullmatch(r"[A-Za-z0-9_\-]{0,60}", tpl) else base["template"]
    tl = raw.get("target_language")
    out["target_language"] = tl if isinstance(tl, str) and (tl == "" or tl in lang_codes) else base["target_language"]
    mon = raw.get("monitor")
    out["monitor"] = mon[:100] if isinstance(mon, str) else base["monitor"]
    out["position"] = raw["position"] if raw.get("position") in POSITIONS else base["position"]
    w = raw.get("width")
    out["width"] = (0 if w == 0 else max(300, min(4000, int(w)))) if (
        isinstance(w, (int, float)) and not isinstance(w, bool) and w == w) else base["width"]
    out["click_through"] = _opt_bool(raw.get("click_through")) if "click_through" in raw else base["click_through"]
    out["translation_only"] = (_opt_bool(raw.get("translation_only")) if "translation_only" in raw
                               else base["translation_only"])
    out["activation"] = raw["activation"] if raw.get("activation") in ACTIVATIONS else base["activation"]
    from dualyrics import sources as src_mod

    out["sources_custom"] = raw["sources_custom"] if isinstance(raw.get("sources_custom"), bool) else bool(
        base.get("sources_custom", False))
    order = raw.get("source_order", base.get("source_order", []))
    out["source_order"] = src_mod.validate_order(order) if order else []
    out["sources_off"] = src_mod.validate_off(raw.get("sources_off", base.get("sources_off", [])))
    out["ocr_region"] = src_mod.validate_region(raw.get("ocr_region", base.get("ocr_region", [])), ())
    from dualyrics.dub import voices as voices_mod

    for k in ("dub_custom", "dub_enabled"):
        out[k] = raw[k] if isinstance(raw.get(k), bool) else bool(base.get(k, BEHAVIOUR[k]))
    v = raw.get("dub_voice")
    out["dub_voice"] = v if isinstance(v, str) and voices_mod.VOICE_RE.match(v) else base.get("dub_voice", "auto")
    sp = raw.get("dub_speed")
    out["dub_speed"] = round(max(0.5, min(2.0, float(sp))), 2) if isinstance(sp, (int, float)) and not isinstance(
        sp, bool) and sp == sp else base.get("dub_speed", 1.0)
    vol = raw.get("dub_volume")
    out["dub_volume"] = int(max(0, min(100, round(vol)))) if isinstance(vol, (int, float)) and not isinstance(
        vol, bool) and vol == vol else base.get("dub_volume", 90)
    return out


def validate_profiles(raw: Any, lang_codes: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Complete, valid profile list: built-ins always present (identity from code, behaviour from ``raw``),
    user profiles cleaned, ids unique, catch-alls at the end."""
    codes = set(lang_codes)
    items = raw if isinstance(raw, list) else []
    stored = {p.get("id"): p for p in items if isinstance(p, dict) and isinstance(p.get("id"), str)}
    builtins = {p["id"]: p for p in builtin_profiles()}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_builtin(pid: str) -> None:
        b = builtins[pid]
        b.update(_validate_behaviour(stored.get(pid, {}), b, codes))
        out.append(b)
        seen.add(pid)

    order = [p.get("id") for p in items if isinstance(p, dict)]
    # keep the user's order for ordinary profiles; built-ins missing from the stored list go first
    for pid in [b for b in BUILTIN_IDS if b not in SPECIAL_IDS and b not in order]:
        add_builtin(pid)
    for p in items:
        if not isinstance(p, dict) or len(out) >= MAX_PROFILES - len(SPECIAL_IDS):
            continue
        pid = p.get("id")
        if pid in SPECIAL_IDS or (pid in builtins and pid in seen):
            continue
        if pid in builtins:
            add_builtin(pid)
            continue
        exes = [e for e in (_exe_name(x) for x in (p.get("exes") or [])) if e]
        path = p.get("path") if isinstance(p.get("path"), str) else ""
        if path and not exes:
            exes = [e for e in [_exe_name(path)] if e]
        if not exes:
            continue
        base = {**copy.deepcopy(BEHAVIOUR), **PRESETS["custom"]}
        name = p.get("name") if isinstance(p.get("name"), str) and p.get("name").strip() else exes[0][:-4]
        prof = {
            "id": pid if isinstance(pid, str) and re.fullmatch(r"[a-z0-9_]{1,48}", pid) else slug(name),
            "name": name.strip()[:80],
            "exes": list(dict.fromkeys(exes))[:16],
            "path": path[:400],
            "icon": p.get("icon")[:400] if isinstance(p.get("icon"), str) else "",
            "kind": p.get("kind") if p.get("kind") in KINDS else "other",
            "builtin": False,
            "media_ids": [m.strip().lower()[:80] for m in (p.get("media_ids") or []) if isinstance(m, str)
                          and m.strip()][:16] or [e[:-4] for e in exes],
        }
        while prof["id"] in seen or prof["id"] in builtins:
            prof["id"] = _bump(prof["id"])
        prof.update(_validate_behaviour(p, base, codes))
        out.append(prof)
        seen.add(prof["id"])
    for pid in SPECIAL_IDS:
        add_builtin(pid)
    return out


def _bump(pid: str) -> str:
    m = re.match(r"^(.*?)_(\d+)$", pid)
    return f"{m.group(1)}_{int(m.group(2)) + 1}" if m else f"{pid}_2"


# ---------------------------------------------------------------- creating profiles (used by the UI)
def new_profile(name: str, exe_path: str, preset: str = "custom", existing: Iterable[dict[str, Any]] = (),
                icon: str = "") -> dict[str, Any]:
    """A profile for one exe, from a preset (games / video / music / custom). Not yet validated."""
    exe = _exe_name(exe_path)
    if not exe:
        raise ValueError(f"not an .exe: {exe_path!r}")
    ids = {p.get("id") for p in existing} | set(BUILTIN_IDS)
    pid = slug(name or exe[:-4])
    while pid in ids:
        pid = _bump(pid)
    p: dict[str, Any] = {"id": pid, "name": (name or exe[:-4]).strip()[:80], "exes": [exe],
                         "path": exe_path if ntpath.isabs(exe_path) else "", "icon": icon or "",
                         "builtin": False, "media_ids": [exe[:-4]]}
    p.update(copy.deepcopy(BEHAVIOUR))
    p.update(copy.deepcopy(PRESETS.get(preset, PRESETS["custom"])))
    return p


def add_profile(profiles: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Insert before the catch-alls. An app that already has a user profile is not added twice."""
    exe = (profile.get("exes") or [""])[0]
    for p in profiles:
        if not p.get("builtin") and exe in p.get("exes", []) and (p.get("path") or "").lower() == (
                profile.get("path") or "").lower():
            return list(profiles)
    out = [p for p in profiles if p.get("id") not in SPECIAL_IDS]
    out.append(profile)
    out += [p for p in profiles if p.get("id") in SPECIAL_IDS]
    return out


# ---------------------------------------------------------------- matching
@dataclass(frozen=True)
class AppRef:
    """A process seen on the system: exe file name (lower case) and full path (may be empty)."""
    exe: str
    path: str = ""

    @staticmethod
    def of(path_or_exe: str) -> "AppRef":
        s = (path_or_exe or "").strip()
        return AppRef(ntpath.basename(s).lower(), s if ntpath.isabs(s) else "")


@dataclass(frozen=True)
class Match:
    profile: dict[str, Any]
    via: str            # foreground | fullscreen | media | running | default


def matches_app(p: dict[str, Any], app: AppRef | None) -> bool:
    if app is None or not app.exe:
        return False
    if p.get("path"):
        return bool(app.path) and app.path.lower() == p["path"].lower()
    return app.exe in p.get("exes", ())


def matches_media(p: dict[str, Any], media_app: str) -> bool:
    m = (media_app or "").lower()
    return bool(m) and any(tok and tok in m for tok in p.get("media_ids", ()))


def activation(p: dict[str, Any], global_activation: str) -> str:
    a = p.get("activation", "default")
    return global_activation if a == "default" else a


def match_profile(profiles: list[dict[str, Any]], foreground: AppRef | None = None, fullscreen: bool = False,
                  running: Iterable[AppRef] = (), media_app: str = "", media_playing: bool = False,
                  global_activation: str = "running") -> Match:
    """Pick the profile that rules right now.

    1. an enabled profile of the foreground app (any activation);
    2. the foreground app is fullscreen and has no profile: the ``fullscreen`` profile;
    3. the app that owns the playing media session (its profile, else ``media_any``), if its activation
       allows background use;
    4. an enabled profile of a running app with "running" activation (explicit Live/Lyrics modes first);
    5. the ``default`` profile.
    """
    by_id = {p["id"]: p for p in profiles}
    regular = [p for p in profiles if p["id"] not in SPECIAL_IDS]
    enabled = [p for p in regular if p.get("enabled")]

    fg = next((p for p in enabled if matches_app(p, foreground)), None)
    if fg is not None:
        return Match(fg, "foreground")
    fg_known = any(matches_app(p, foreground) for p in regular)   # disabled profile: fall through, no fullscreen
    fs = by_id.get(FULLSCREEN_ID)
    if fullscreen and not fg_known and fs is not None and fs.get("enabled"):
        return Match(fs, "fullscreen")

    if media_playing and media_app:
        owner = next((p for p in regular if matches_media(p, media_app)), None)
        if owner is None:
            owner = by_id.get(MEDIA_ANY_ID)
        if owner is not None and owner.get("enabled") and activation(owner, global_activation) == "running":
            return Match(owner, "media")

    run = list(running)
    cands = [p for p in enabled if activation(p, global_activation) == "running"
             and any(matches_app(p, a) for a in run)]
    if cands:
        cands.sort(key=lambda p: 0 if p.get("mode") in ("live", "lyrics") else 1)  # stable: list order kept
        return Match(cands[0], "running")
    return Match(by_id.get(DEFAULT_ID) or builtin_profiles()[-1], "default")


# ---------------------------------------------------------------- effect on settings
OVERRIDE_KEYS = ("target_language", "monitor", "position_preset", "width", "click_through", "translation_only",
                 "template")


def overrides(p: dict[str, Any]) -> dict[str, Any]:
    """Settings keys this profile overrides while it is active (empty / None fields follow the global value)."""
    o: dict[str, Any] = {}
    if p.get("target_language"):
        o["target_language"] = p["target_language"]
    if p.get("monitor"):
        o["monitor"] = p["monitor"]
    if p.get("position"):
        o["position_preset"] = p["position"]
    if p.get("width"):
        o["width"] = p["width"]
    if isinstance(p.get("click_through"), bool):
        o["click_through"] = p["click_through"]
    if isinstance(p.get("translation_only"), bool):
        o["translation_only"] = p["translation_only"]
    if p.get("template"):
        o["template"] = p["template"]
    if p.get("dub_custom"):
        o.update(dub_enabled=bool(p.get("dub_enabled")), dub_voice=p.get("dub_voice", "auto"),
                 dub_speed=p.get("dub_speed", 1.0), dub_volume=p.get("dub_volume", 90))
    return o


def effective_mode(p: dict[str, Any], global_mode: str) -> str:
    m = p.get("mode", "auto")
    return global_mode if m == "auto" else m


def is_live_context(p: dict[str, Any], via: str) -> bool:
    """Foreground use of a game or video player (or any fullscreen app) means: caption what is heard."""
    return via == "fullscreen" or (via == "foreground" and p.get("kind") in LIVE_KINDS)
