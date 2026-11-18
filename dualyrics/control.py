"""AI control: the operations an AI assistant may run on Dualyrics (docs/AI.md).

One registry (:data:`TOOLS`) describes every operation with a JSON Schema for its arguments. It is served in two
ways:

* inside the running app, by the local control API (``dualyrics/api.py``, 127.0.0.1 only, bearer token), with the
  live state (what is playing, the lines on screen, the active app profile); the operations run on the Tk thread;
* by the MCP server (``dualyrics/mcp.py``) when the app is not running: the operations then work on settings.json,
  the templates folder and the usage database directly, and the few that need the running app say so.

Every change goes through the same validation as the settings window (``settings.validate``, the template
sanitizer, ``profiles.validate_profiles``); a patch with an invalid value changes nothing and says why. Gemini API
keys are never shown or changed here, and only the user can switch the control API itself on or off.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from dualyrics import __version__, langs, profiles as profiles_mod, settings as settings_mod, sources as sources_mod
from dualyrics import templates as templates_mod

log = logging.getLogger(__name__)

UI_TIMEOUT = 10.0           # seconds an operation may wait for the app's Tk thread
MAX_LINE = 500              # characters of a corrected line translation


class OpError(Exception):
    """A refusal or failure the assistant should read (the tool's error text)."""


class UnknownTool(OpError):
    pass


# ---------------------------------------------------------------- settings patches
BLOCKED_KEYS: dict[str, str] = {
    "version": "the settings schema version is managed by the app",
    "gemini_keys": "API keys are entered by the user in Settings > Translation; assistants never see or change them",
    "gemini_keys_off": "which API keys are switched on is set by the user in Settings > Translation",
    "profiles": "use list_profiles, upsert_profile and delete_profile to change app profiles",
    "api_enabled": "only the user switches the AI control API on or off (Settings > AI & API)",
}

_LANG_ALIASES = {"farsi": "fa", "parsi": "fa", "chinese": "zh-CN", "mandarin": "zh-CN", "simplified chinese": "zh-CN",
                 "traditional chinese": "zh-TW", "kurdish": "ckb", "sorani": "ckb", "kurmanji": "ku",
                 "tagalog": "tl", "brazilian portuguese": "pt", "persian (farsi)": "fa"}


def resolve_language(value: Any) -> str:
    """A language code from a code ("tr", "zh-cn"), an English name ("Turkish") or a native name ("Türkçe")."""
    if not isinstance(value, str) or not value.strip():
        raise OpError("a language is required (a code such as \"tr\" or a name such as \"Turkish\")")
    low = value.strip().lower()
    for lang in langs.LANGUAGES:
        if low in (lang.code.lower(), lang.name.lower(), lang.native.lower()):
            return lang.code
    if low in _LANG_ALIASES:
        return _LANG_ALIASES[low]
    base = re.split(r"[-_]", low)[0]
    for lang in langs.LANGUAGES:
        if lang.code.lower() == base:
            return lang.code
    for lang in langs.LANGUAGES:
        if lang.name.lower().startswith(low):
            return lang.code
    supported = ", ".join(f"{lang.code} ({lang.name})" for lang in langs.LANGUAGES)
    raise OpError(f"unsupported language {value!r}. Supported: {supported}")


def _kind(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "null"


def _norm(v: Any) -> Any:
    """What the validator's normalising leaves of a value (case, spaces, duplicates), to tell "normalised" from
    "invalid" when the result happens to equal the default."""
    if isinstance(v, str):
        return v.strip().lower().replace(" ", "")
    if isinstance(v, list):
        return list(dict.fromkeys(json_key(_norm(x)) for x in v if not (isinstance(x, str) and not x.strip())))
    return v


def json_key(v: Any) -> Any:
    return v if isinstance(v, (str, int, float, bool)) or v is None else repr(v)


def choices() -> dict[str, list[str]]:
    """Allowed values of the settings that take one of a few words."""
    from dualyrics.dub import voices as voices_mod

    codes = [lang.code for lang in langs.LANGUAGES]
    return {
        "mode": list(settings_mod.MODES), "provider": list(settings_mod.PROVIDERS),
        "proxy_mode": list(settings_mod.PROXY_MODES), "ui_language": list(settings_mod.UI_LANGUAGES),
        "ui_theme": list(settings_mod.UI_THEMES), "live_model": list(settings_mod.LIVE_SIZES),
        "live_device": list(settings_mod.LIVE_DEVICES), "virtual_desktops": list(settings_mod.DESKTOP_MODES),
        "position_preset": list(settings_mod.PRESETS), "align": list(settings_mod.ALIGNMENTS),
        "profile_activation": list(profiles_mod.GLOBAL_ACTIVATIONS), "preview_backdrop": list(settings_mod.BACKDROPS),
        "settings_section": list(settings_mod.SECTIONS), "target_language": codes,
        "live_source_language": ["auto"] + codes, "source_order (items)": list(sources_mod.SOURCE_IDS),
        "sources_off (items)": list(sources_mod.SOURCE_IDS), "dub_engine": list(voices_mod.ENGINES),
    }


_RANGES: dict[str, list[float]] = {}


def ranges() -> dict[str, list[float]]:
    """[lowest, highest] of every numeric setting (asked from the validator itself, so they never drift)."""
    if not _RANGES:
        d = settings_mod.defaults()
        for k, v in d.items():
            if _kind(v) == "number" and k != "version":
                lo = settings_mod.validate({k: -1e12}).get(k)
                hi = settings_mod.validate({k: 1e12}).get(k)
                _RANGES[k] = [lo, hi]
    return dict(_RANGES)


def check_patch(current: dict[str, Any], changes: Any,
                template_exists: Callable[[str], bool] | None = None) -> tuple[dict[str, Any], dict[str, Any],
                                                                               dict[str, str]]:
    """Check a settings patch against the validator. Returns ``(applied, adjusted, rejected)``:

    * ``applied``: keys whose value is valid as given,
    * ``adjusted``: ``{key: {"requested": ..., "applied": ...}}`` for values the validator clamped or normalised
      (a number out of range, a colour in lower case),
    * ``rejected``: ``{key: reason}`` for unknown or protected keys and invalid values.
    """
    if not isinstance(changes, dict) or not changes:
        raise OpError("changes must be a non-empty object such as {\"overlay_opacity\": 0.8}")
    d = settings_mod.defaults()
    rejected: dict[str, str] = {}
    cand: dict[str, Any] = {}
    for k, v in changes.items():
        if k in BLOCKED_KEYS:
            rejected[k] = BLOCKED_KEYS[k]
        elif k not in d:
            rejected[k] = "unknown setting (get_settings lists every key)"
        else:
            cand[k] = v
    for k in ("target_language", "live_source_language"):
        if k in cand and isinstance(cand[k], str) and not (k == "live_source_language" and cand[k] == "auto"):
            try:
                cand[k] = resolve_language(cand[k])
            except OpError as e:
                rejected[k] = str(e)
                cand.pop(k)
    if template_exists is not None and isinstance(cand.get("template"), str) and not template_exists(cand["template"]):
        rejected["template"] = f"no template {cand.pop('template')!r} (list_templates shows the ids)"
    merged = copy.deepcopy(current)
    merged.update(cand)
    new = settings_mod.validate(merged)
    hints = choices()
    rng = ranges()
    applied: dict[str, Any] = {}
    adjusted: dict[str, Any] = {}
    for k, v in cand.items():
        got = new.get(k)
        if got == v:
            applied[k] = got
            continue
        want = _kind(d[k])
        if _kind(v) != want:
            rejected[k] = f"expected a {want}, got {_kind(v)}"
        elif want == "number":
            adjusted[k] = {"requested": v, "applied": got}
        elif (got == d[k] or got == current.get(k)) and _norm(got) != _norm(v):
            why = f"invalid value {v!r}"
            allowed = hints.get(k) or hints.get(f"{k} (items)")
            if allowed:
                why += f"; allowed: {', '.join(map(str, allowed))}"
            elif k == "proxy_mode":
                why += "; \"custom\" needs a valid proxy_url"
            rejected[k] = why
        else:
            adjusted[k] = {"requested": v, "applied": got}
    for k, (lo, hi) in rng.items():
        if k in adjusted and isinstance(adjusted[k], dict):
            adjusted[k]["range"] = [lo, hi]
    return applied, adjusted, rejected


def public_settings(snap: dict[str, Any]) -> dict[str, Any]:
    """Settings as an assistant may see them: no API keys (only how many), no proxy password."""
    s = copy.deepcopy(snap)
    s["gemini_keys_count"] = len(s.pop("gemini_keys", []) or [])
    s.pop("gemini_keys_off", None)
    if s.get("proxy_url"):
        s["proxy_url"] = re.sub(r"//[^/@\s]*@", "//***@", s["proxy_url"])
    return s


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ---------------------------------------------------------------- argument checking (a small JSON Schema subset)
def check_args(schema: dict[str, Any], args: dict[str, Any]) -> None:
    props = schema.get("properties", {})
    for k in schema.get("required", []):
        if k not in args or args[k] is None:
            raise OpError(f"missing argument {k!r}")
    for k, v in args.items():
        if k not in props:
            if schema.get("additionalProperties", True) is False:
                raise OpError(f"unknown argument {k!r}; this tool takes: {', '.join(props) or 'nothing'}")
            continue
        if v is None:
            continue
        p = props[k]
        t = p.get("type")
        ok = {"string": isinstance(v, str), "boolean": isinstance(v, bool), "object": isinstance(v, dict),
              "array": isinstance(v, list),
              "integer": (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool)}.get(t, True)
        if not ok:
            raise OpError(f"argument {k!r} must be a {t}")
        if "enum" in p and v not in p["enum"]:
            raise OpError(f"argument {k!r} must be one of: {', '.join(map(str, p['enum']))}")


def coerce_query(schema: dict[str, Any], args: dict[str, str]) -> dict[str, Any]:
    """Arguments from a URL query (all strings) in the types the schema asks for (GET on the control API)."""
    props = schema.get("properties", {})
    out: dict[str, Any] = {}
    for k, v in args.items():
        t = props.get(k, {}).get("type")
        if t == "array":
            out[k] = [x.strip() for x in v.split(",") if x.strip()]
        elif t == "boolean":
            out[k] = v.lower() in ("1", "true", "yes", "on")
        elif t in ("integer", "number"):
            try:
                out[k] = int(v) if t == "integer" else float(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- the tool registry
def _obj(props: dict[str, Any] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    s: dict[str, Any] = {"type": "object", "properties": props or {}, "additionalProperties": False}
    if required:
        s["required"] = list(required)
    return s


_PROFILE_ARG = {"type": "string", "description": "An app profile: its id, its name or its exe (list_profiles)."}
_LANG_DESC = "A language code (\"tr\", \"fa\", \"zh-CN\") or name (\"Turkish\", \"Persian\")."

# name, title, read-only, needs the running app, description, input schema
_SPECS: list[tuple[str, str, bool, bool, str, dict[str, Any]]] = [
    ("get_status", "Status", True, False,
     "What Dualyrics is doing now: whether the app runs, the mode and the source in use (synced lyrics, on-screen "
     "subtitles or audio), the active app profile, what is playing, the lines on screen with their translation, "
     "the target language, template, sync offsets and dubbing.", _obj()),
    ("get_settings", "Get settings", True, False,
     "All settings (or only `keys`), with the allowed values of the word settings (`choices`) and the range of "
     "every number (`ranges`). Gemini API keys are never included (only their count).",
     _obj({"keys": {"type": "array", "items": {"type": "string"},
                    "description": "Only these settings (default: all)."}})),
    ("update_settings", "Change settings", False, False,
     "Change settings with a patch such as {\"overlay_opacity\": 0.8, \"show_next\": false}. Every value is "
     "validated like in the settings window: numbers out of range are clamped (reported in `adjusted`); an "
     "unknown key or invalid value changes nothing and says why. App profiles have their own tools; API keys "
     "cannot be changed here. Use dry_run to check a patch first.",
     _obj({"changes": {"type": "object", "description": "Setting keys and their new values."},
           "dry_run": {"type": "boolean", "description": "Only check the patch (default false)."}}, ("changes",))),
    ("list_templates", "List templates", True, False,
     "The subtitle templates (looks): built-in presets and the user's own, with which one is in use globally "
     "and per app profile.", _obj()),
    ("get_template", "Get a template", True, False,
     "One template as JSON (schema dualyrics.template/1, docs/DESIGN.md part 2): layout, box, lines (original, "
     "translation, next, speaker), karaoke, animation, timing, hints.",
     _obj({"id": {"type": "string", "description": "Template id or name, e.g. \"game_hud\" or \"Game HUD\"."}},
          ("id",))),
    ("apply_template", "Apply a template", False, False,
     "Make a template the look in use: for everything, or for one app profile (e.g. a game). With scope auto it "
     "goes where the settings window would put it: the active app's profile when that profile has its own "
     "template, else everything. apply_hints also applies the template's suggested placement, click-through and "
     "lock.",
     _obj({"id": {"type": "string", "description": "Template id or name."},
           "scope": {"type": "string", "enum": ["auto", "global", "profile"], "description": "Default auto."},
           "profile": _PROFILE_ARG,
           "apply_hints": {"type": "boolean", "description": "Also apply the suggested placement and behaviour."}},
          ("id",))),
    ("create_or_update_template", "Create or update a template", False, False,
     "Create a user template or change one. `template` may be partial: it is merged into the existing template "
     "(update), or into `based_on` (create; default Liquid Glass). Built-in presets are never changed: editing "
     "one creates a copy. Bad values are fixed and listed in `fixed`. Example: {\"based_on\": \"cinema\", "
     "\"name\": \"Big yellow\", \"template\": {\"lines\": {\"translation\": {\"color\": \"#FFD54F\"}}}}.",
     _obj({"id": {"type": "string", "description": "Template to update, or the id for a new one."},
           "template": {"type": "object", "description": "Template JSON (whole or partial)."},
           "based_on": {"type": "string", "description": "For a new template: the template to start from."},
           "name": {"type": "string", "description": "Display name."},
           "description": {"type": "string", "description": "Short description."}})),
    ("list_profiles", "List app profiles", True, False,
     "Per-app profiles: which apps they match (exe names), their kind, mode, template, target language, "
     "position, click-through, translation only, activation, sources and dubbing. `active` marks the profile in "
     "use now.", _obj()),
    ("upsert_profile", "Create or change an app profile", False, False,
     "Change an app profile (found by id, name or exe) or add one for an app. A new profile needs the app's exe "
     "file name (list_running_apps shows the open apps); preset games sets Live captions, the Game HUD "
     "template, click-through and top placement. Fields: name, exe or exes, kind (music, video, browser, game, "
     "other), enabled, mode (auto, lyrics, live, off), template, target_language, monitor, position ('', bottom, "
     "top, center), width, click_through, translation_only (true, false or null = follow the global setting), "
     "activation (default, foreground, running), sources_custom, source_order, sources_off, ocr_region, "
     "dub_custom, dub_enabled, dub_voice, dub_speed, dub_volume.",
     _obj({"profile": {"type": "object", "description": "Profile fields; include id, name or exe to find one."},
           "preset": {"type": "string", "enum": list(profiles_mod.PRESETS),
                      "description": "For a new profile (default custom)."}}, ("profile",))),
    ("delete_profile", "Delete an app profile", False, False,
     "Remove a user app profile (built-in ones can only be switched off: upsert_profile with enabled false). "
     "Returns the removed profile, so it can be added back.", _obj({"profile": _PROFILE_ARG}, ("profile",))),
    ("list_running_apps", "List open apps", True, False,
     "Apps with a window open now (name and exe), and the profile each one already has, to add a profile for a "
     "game or player.", _obj()),
    ("set_mode", "Set the mode", False, False,
     "auto: synced lyrics when they exist, else on-screen subtitles or live captions (smart auto); lyrics: lyrics "
     "only; live: captions (the app's own subtitles, else the audio); off: hide the overlay. With `profile` the "
     "mode is set for that app only.",
     _obj({"mode": {"type": "string", "enum": ["auto", "lyrics", "live", "off"]}, "profile": _PROFILE_ARG},
          ("mode",))),
    ("set_target_language", "Set the target language", False, False,
     "The language translations are made into. Without `profile` it applies everywhere (and the active app "
     "stops overriding it, like the overlay menu does); with `profile` only for that app ('' = follow the "
     "global language).",
     _obj({"language": {"type": "string", "description": _LANG_DESC}, "profile": _PROFILE_ARG}, ("language",))),
    ("sync_offset", "Shift the timing", False, False,
     "Shift lyrics timing. Positive values show lines later (use it when lyrics come too early), negative "
     "earlier. scope song (the song playing now, needs the app running) or global (all songs). Give ms "
     "(absolute), delta_ms (relative) or reset; with none of them it only reports the offset.",
     _obj({"scope": {"type": "string", "enum": ["song", "global"],
                     "description": "Default: song when one is loaded, else global."},
           "ms": {"type": "integer", "description": "New offset in milliseconds (-15000..15000)."},
           "delta_ms": {"type": "integer", "description": "Change the offset by this much."},
           "reset": {"type": "boolean", "description": "Back to 0."}})),
    ("retranslate", "Translate the song again", False, True,
     "Drop the cached translation of the song playing now and translate it again (needs the app running).",
     _obj()),
    ("edit_line_translation", "Correct a line's translation", False, True,
     "Replace the translation of one lyric line of the song playing now (default: the line on screen). It is "
     "shown at once and kept in the translation cache (needs the app running).",
     _obj({"text": {"type": "string", "description": "The corrected translation."},
           "index": {"type": "integer", "description": "Line number in the synced lyrics (0-based)."}}, ("text",))),
    ("get_usage", "Gemini usage", True, False,
     "What the Gemini API keys used: requests, tokens, audio, rate limits and cache hits for today, the last 7 "
     "days or this month; per model, per key (by position, never the key itself) and per song or video; today's "
     "share of each free-tier daily limit and when it resets.",
     _obj({"period": {"type": "string", "enum": ["today", "week", "month"], "description": "Default today."}})),
    ("toggle_dubbing", "Dubbing on or off", False, False,
     "Switch auto-dubbing (the translation spoken aloud) on or off; without `enabled` it flips. It follows the "
     "same rule as Ctrl+Alt+D: the active app's profile when that profile has its own dubbing settings.",
     _obj({"enabled": {"type": "boolean"}})),
    ("show", "Show the overlay", False, False, "Show the subtitle overlay again (after hide or Ctrl+Alt+L).",
     _obj()),
    ("hide", "Hide the overlay", False, False, "Hide the subtitle overlay (show brings it back).", _obj()),
]

TOOLS: list[dict[str, Any]] = []
READ_ONLY: set[str] = set()
NEEDS_APP: set[str] = set()
for _name, _title, _ro, _app, _desc, _schema in _SPECS:
    TOOLS.append({"name": _name, "title": _title, "description": _desc, "inputSchema": _schema,
                  "annotations": {"title": _title, "readOnlyHint": _ro, "destructiveHint": _name == "delete_profile",
                                  "idempotentHint": _ro or _name not in ("toggle_dubbing", "sync_offset"),
                                  "openWorldHint": False}})
    if _ro:
        READ_ONLY.add(_name)
    if _app:
        NEEDS_APP.add(_name)
TOOL_BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in TOOLS}
NEEDS_APP_TEXT = ("this needs Dualyrics running (start it from the Start menu); without it only settings, "
                  "templates, profiles and usage can be read and changed")


def tool_list() -> list[dict[str, Any]]:
    return copy.deepcopy(TOOLS)


# ---------------------------------------------------------------- the controller
class Controller:
    """Runs the operations on a settings store and a template store, with the running app (``app``) or without.

    With an app, every operation runs on the app's Tk thread (through ``app.call_soon``), so it sees and changes
    the same state as the overlay menu and the settings window."""

    def __init__(self, settings: Any, templates: Any, app: Any = None, usage_path: Path | None = None) -> None:
        self.s = settings
        self.tpl = templates
        self.app = app
        self.usage_path = usage_path
        self._ui_thread = threading.get_ident()

    @property
    def live(self) -> bool:
        return self.app is not None

    def close(self) -> None:
        pass

    def tools(self) -> list[dict[str, Any]]:
        return tool_list()

    # -- dispatch
    def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        spec = TOOL_BY_NAME.get(name)
        if spec is None:
            raise UnknownTool(f"unknown tool {name!r}")
        args = {} if args is None else args
        if not isinstance(args, dict):
            raise OpError("arguments must be an object")
        check_args(spec["inputSchema"], args)
        props = spec["inputSchema"].get("properties", {})
        args = {k: int(v) if props.get(k, {}).get("type") == "integer" else v for k, v in args.items() if v is not None}
        if name in NEEDS_APP and not self.live:
            raise OpError(NEEDS_APP_TEXT)
        fn = getattr(self, f"_op_{name}")
        return self._on_ui(lambda: fn(args))

    def _on_ui(self, fn: Callable[[], Any]) -> Any:
        if self.app is None or threading.get_ident() == self._ui_thread:
            return fn()
        done = threading.Event()
        box: dict[str, Any] = {}

        def job() -> None:
            try:
                box["r"] = fn()
            except BaseException as e:  # noqa: BLE001 - handed back to the API thread
                box["e"] = e
            finally:
                done.set()

        self.app.call_soon(job)
        if not done.wait(UI_TIMEOUT):
            raise TimeoutError("Dualyrics did not answer in time (is a dialog open?)")
        if "e" in box:
            raise box["e"]
        return box["r"]

    # -- lookups
    def _find_template(self, ref: Any) -> str:
        if not isinstance(ref, str) or not ref.strip():
            raise OpError("a template id is required (list_templates)")
        r = ref.strip()
        low = r.lower()
        for cand in (r, low, low.replace(" ", "_"), low.replace(" ", "-"), low.replace("-", "_"),
                     low.replace("_", "-"), templates_mod.slug(r)):
            if self.tpl.exists(cand):
                return cand
        squash = re.sub(r"[\s_-]+", "", low)
        for doc in self.tpl.all():
            names = [v.lower() for v in (doc.get("name") or {}).values() if isinstance(v, str)]
            if low in names or squash in (re.sub(r"[\s_-]+", "", n) for n in names):
                return doc["id"]
        raise OpError(f"no template {ref!r}; templates: {', '.join(self.tpl.ids())}")

    def _find_profile(self, ref: Any, profs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        p = self._match_profile(ref, profs)
        if p is None:
            ids = ", ".join(f"{q['id']} ({q.get('name', '')})" for q in (profs or self.s["profiles"]))
            raise OpError(f"no app profile {ref!r}; profiles: {ids}. upsert_profile adds one for an app")
        return p

    def _match_profile(self, ref: Any, profs: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        if not isinstance(ref, str) or not ref.strip():
            raise OpError("a profile id, name or exe is required (list_profiles)")
        profs = profs if profs is not None else self.s["profiles"]
        low = ref.strip().lower()
        exe = low if low.endswith(".exe") else low + ".exe"
        for test in (lambda p: p["id"] == ref.strip(), lambda p: p["id"] == low,
                     lambda p: (p.get("name") or "").lower() == low, lambda p: exe in p.get("exes", [])):
            for p in profs:
                if test(p):
                    return p
        return None

    def _set_profile(self, pid: str, **changes: Any) -> dict[str, Any]:
        profs = self.s["profiles"]
        for p in profs:
            if p["id"] == pid:
                p.update(changes)
        self.s.update(profiles=profs)
        return next(p for p in self.s["profiles"] if p["id"] == pid)

    def _active_profile(self) -> dict[str, Any]:
        """The app profile in use (the running app only; ``{}`` for the global catch-alls)."""
        if not self.live:
            return {}
        prof = self.app.match.profile
        return prof if prof.get("id") and prof["id"] not in profiles_mod.SPECIAL_IDS else {}

    def _profile_name(self, p: dict[str, Any]) -> str:
        return p.get("name") or p.get("id", "")

    # ---------------------------------------------------------------- operations
    def _op_get_status(self, a: dict[str, Any]) -> dict[str, Any]:
        s = self.s
        keys = len(s["gemini_keys"])
        out: dict[str, Any] = {
            "app_running": self.live, "version": __version__,
            "mode": s["mode"], "visible": s["visible"], "target_language": s["target_language"],
            "provider": "gemini" if s["provider"] == "gemini" and keys else "free",
            "gemini_keys": keys, "template": s["template"], "dubbing": s["dub_enabled"],
            "global_offset_ms": s["offset_ms"],
        }
        if not self.live:
            out["note"] = ("Dualyrics is not running: settings, templates, profiles and usage are read and changed "
                           "directly; they apply when it starts.")
            return out
        app = self.app
        from dualyrics import automode

        cfg = app.effective_cfg()
        d = app.decision
        prof = app.match.profile
        out["active"] = {"source": {automode.LYRICS: "lyrics", automode.SCREEN: "screen", automode.LIVE: "audio",
                                    automode.HIDE: "none"}.get(d.mode, d.mode), "reason": d.reason,
                         "overlay_on_screen": bool(getattr(app.overlay, "visible", False))}
        out["profile"] = {"id": prof.get("id", ""), "name": self._profile_name(prof), "matched_by": app.match.via}
        out["effective"] = {k: cfg[k] for k in ("target_language", "template", "translation_only", "click_through",
                                                "position_preset", "dub_enabled")}
        # the watcher keeps the last track after its player closed (no media session left): nothing plays then
        tr = app.media.track if getattr(app.media, "has_session", True) else None
        out["now_playing"] = None if tr is None else {
            "title": tr.title, "artist": tr.artist, "album": tr.album, "player": tr.app,
            "playing": bool(app.media.playing), "position_s": round(float(app.media.clock.position()), 1),
            "duration_s": round(float(tr.duration or 0), 1)}
        st = app.song
        res = st.lyrics
        if res is None:
            out["lyrics"] = {"status": "searching" if tr else "none"}
        else:
            out["lyrics"] = {"status": "found" if res.found else "not_found", "synced": bool(res.synced),
                             "lines": len(res.synced) or len(res.plain), "instrumental": bool(res.instrumental),
                             "translation": st.trans_status or "none",
                             "song_offset_ms": app.cache.get_offset(st.key) if st.key else 0}
        c = getattr(app.overlay, "_content", None)
        out["current_lines"] = None if c is None else {
            "original": c.original, "translation": c.translation, "next": c.next,
            "index": c.index if c.index >= 0 else None, "status": c.status}
        out["live_paused"] = bool(app.live.paused)
        return out

    def _op_get_settings(self, a: dict[str, Any]) -> dict[str, Any]:
        snap = public_settings(self.s.snapshot())
        keys = a.get("keys")
        if keys:
            missing = [k for k in keys if k not in snap]
            snap = {k: snap[k] for k in keys if k in snap}
            out: dict[str, Any] = {"settings": snap}
            if missing:
                out["unknown_keys"] = missing
            return out
        return {"settings": snap, "choices": choices(), "ranges": ranges(),
                "notes": {"profiles": "list_profiles / upsert_profile",
                          "templates": "list_templates; `template` is the global look (profiles can override it)",
                          "offset_ms": "positive = lines later"}}

    def _op_update_settings(self, a: dict[str, Any]) -> dict[str, Any]:
        applied, adjusted, rejected = check_patch(self.s.snapshot(), a["changes"], self.tpl.exists)
        if rejected:
            raise OpError("nothing was changed: " + "; ".join(f"{k}: {why}" for k, why in rejected.items()))
        out: dict[str, Any] = {"applied": applied, "adjusted": adjusted}
        to_set = {**applied, **{k: v["applied"] for k, v in adjusted.items()}}
        if a.get("dry_run"):
            out["dry_run"] = True
            return out
        out["changed"] = sorted(self.s.update(**to_set))
        prof = self._active_profile()
        over = sorted(set(profiles_mod.overrides(prof)) & set(to_set)) if prof else []
        if over:
            out["note"] = (f"the active app profile {self._profile_name(prof)!r} overrides {', '.join(over)} "
                           f"while it is in use; change it with upsert_profile")
        return out

    def _op_list_templates(self, a: dict[str, Any]) -> dict[str, Any]:
        profs = self.s["profiles"]
        current = self.s["template"]
        items = []
        for doc in self.tpl.all():
            tid = doc["id"]
            items.append({"id": tid, "name": templates_mod.display_name(doc, "en"),
                          "names": doc.get("name") or {}, "builtin": bool(doc.get("builtin")),
                          "based_on": doc.get("based_on"), "description": templates_mod.description(doc, "en"),
                          "in_use_globally": tid == current,
                          "used_by_profiles": [p["id"] for p in profs if p.get("template") == tid]})
        out: dict[str, Any] = {"templates": items, "global": current}
        if self.live:
            out["in_use_now"] = self.app.effective_cfg()["template"]
        return out

    def _op_get_template(self, a: dict[str, Any]) -> dict[str, Any]:
        tid = self._find_template(a["id"])
        doc = self.tpl.get(tid)
        return {"template": doc, "note": "built-in presets cannot be changed; create_or_update_template with this id "
                                         "makes a copy"} if doc and doc.get("builtin") else {"template": doc}

    def _op_apply_template(self, a: dict[str, Any]) -> dict[str, Any]:
        tid = self._find_template(a["id"])
        scope = "profile" if a.get("profile") else a.get("scope", "auto")
        target: dict[str, Any] | None = None
        if scope == "profile":
            target = self._find_profile(a["profile"]) if a.get("profile") else (self._active_profile() or None)
            if target is None:
                raise OpError("scope profile needs `profile` (no app profile is in use now)")
        elif scope == "auto":
            prof = self._active_profile()
            if prof.get("template"):
                target = prof
        if target is not None:
            self._set_profile(target["id"], template=tid)
        else:
            self.s.update(template=tid)
        hints = self.tpl.resolve(tid).get("hints") or {}
        out: dict[str, Any] = {"template": tid, "applied_to": f"profile {target['id']}" if target else "global",
                               "hints": hints}
        if a.get("apply_hints"):
            done: dict[str, Any] = {}
            place = hints.get("placement") if hints.get("placement") in ("bottom", "top", "center") else None
            if target is not None:
                ch: dict[str, Any] = {}
                if place:
                    ch["position"] = place
                if isinstance(hints.get("click_through"), bool):
                    ch["click_through"] = hints["click_through"]
                if ch:
                    self._set_profile(target["id"], **ch)
                done = ch
            else:
                ch = {k: hints[k] for k in ("click_through", "locked") if isinstance(hints.get(k), bool)}
                if place:
                    if self.live:
                        self.app._set_preset(place)          # also forgets a dragged place on this monitor
                    else:
                        ch["position_preset"] = place
                    done["position_preset"] = place
                if ch:
                    self.s.update(**ch)
                done.update(ch)
            out["hints_applied"] = done
        return out

    def _op_create_or_update_template(self, a: dict[str, Any]) -> dict[str, Any]:
        patch = {k: v for k, v in (a.get("template") or {}).items() if k not in ("builtin", "schema")}
        tid = a.get("id") or (patch.get("id") if isinstance(patch.get("id"), str) else None)
        patch.pop("id", None)
        existing = self.tpl.get(tid) if tid else None
        if tid and existing is None and not templates_mod.ID_RE.match(tid):
            existing = self.tpl.get(self._find_template(tid))
        created = existing is None or bool(existing.get("builtin"))
        if existing is not None:
            base = existing
        else:
            ref = a.get("based_on") or patch.get("based_on")
            base = self.tpl.resolve(self._find_template(ref) if ref else templates_mod.INSTALL_DEFAULT_ID)
        patch.pop("based_on", None)
        doc = deep_merge(base, patch)
        doc["schema"] = templates_mod.SCHEMA
        doc["builtin"] = False
        name = (a.get("name") or "").strip()[:80]
        if created:
            doc["based_on"] = base["id"] if base.get("builtin") else base.get("based_on")
            if name:
                doc["name"] = {"en": name}
            elif "name" not in patch:
                doc["name"] = {k: f"{v} (copy)" if k != "fa" else f"{v} (کپی)" for k, v in
                               (base.get("name") or {"en": base["id"]}).items()}
            wanted = tid if tid and existing is None and templates_mod.ID_RE.match(tid) else None
            doc["id"] = wanted or self.tpl.new_id(templates_mod.slug(name) if name else base["id"])
        else:
            if name:
                doc["name"] = {**(doc.get("name") or {}), "en": name}
        if a.get("description"):
            doc["description"] = {**(doc.get("description") or {}), "en": a["description"].strip()[:300]}
        fixed = templates_mod.schema_errors(doc)
        saved = self.tpl.save(doc)
        out: dict[str, Any] = {"id": saved["id"], "created": created, "template": saved}
        if existing is not None and existing.get("builtin"):
            out["note"] = f"built-in {existing['id']!r} is unchanged; the edit was saved as {saved['id']!r}"
        if fixed:
            out["fixed"] = fixed
        out["next"] = "apply_template makes it the look in use"
        return out

    def _op_list_profiles(self, a: dict[str, Any]) -> dict[str, Any]:
        active = self.app.match.profile.get("id") if self.live else None
        out = []
        for p in self.s["profiles"]:
            q = {k: v for k, v in p.items() if k != "icon"}
            q["active"] = p["id"] == active
            out.append(q)
        res: dict[str, Any] = {"profiles": out, "profiles_enabled": self.s["profiles_enabled"]}
        if not self.s["profiles_enabled"]:
            res["note"] = "per-app profiles are switched off (setting profiles_enabled)"
        return res

    _PROFILE_IDENTITY = ("id", "name", "exe", "exes", "path", "kind", "media_ids")

    def _op_upsert_profile(self, a: dict[str, Any]) -> dict[str, Any]:
        req = dict(a["profile"])
        allowed = set(profiles_mod.BEHAVIOUR) | set(self._PROFILE_IDENTITY)
        unknown = sorted(set(req) - allowed - {"builtin", "icon", "active"})
        if unknown:
            raise OpError(f"unknown profile fields: {', '.join(unknown)}; fields: {', '.join(sorted(allowed))}")
        for k in ("builtin", "icon", "active"):
            req.pop(k, None)
        for k in ("id", "name", "exe", "path", "kind"):
            if k in req and req[k] is not None and not isinstance(req[k], str):
                raise OpError(f"profile field {k!r} must be a string")
        for k in ("exes", "media_ids"):
            if k in req and not (isinstance(req[k], list) and all(isinstance(x, str) for x in req[k])):
                raise OpError(f"profile field {k!r} must be a list of strings")
        if req.get("template"):
            req["template"] = self._find_template(req["template"])
        if isinstance(req.get("target_language"), str) and req["target_language"]:
            req["target_language"] = resolve_language(req["target_language"])
        exe = req.pop("exe", None)
        if isinstance(exe, str) and exe and not exe.lower().endswith(".exe"):
            exe += ".exe"
        profs = self.s["profiles"]
        existing = None
        for ref in (req.get("id"), exe, req.get("name")):
            if isinstance(ref, str) and ref.strip():
                existing = self._match_profile(ref, profs)
                if existing is not None:
                    break
        if existing is not None:
            pid = existing["id"]
            changes = {k: v for k, v in req.items() if k != "id"}
            if existing.get("builtin"):
                fixed = [k for k in ("name", "exes", "path", "kind", "media_ids") if k in changes]
                if fixed:
                    raise OpError(f"the built-in profile {pid!r} keeps its {', '.join(fixed)}; add a new profile "
                                  f"for another app")
            if exe and exe.lower() not in existing.get("exes", []) and not existing.get("builtin"):
                changes["exes"] = list(dict.fromkeys(existing.get("exes", []) + [exe.lower()]))
            for p in profs:
                if p["id"] == pid:
                    p.update(changes)
            created = False
        else:
            first = exe or next(iter(req.get("exes") or []), None) or req.get("path")
            if not first:
                raise OpError("a new profile needs the app's exe file name, e.g. {\"name\": \"God of War "
                              "Ragnarok\", \"exe\": \"GoWR.exe\"} (list_running_apps shows the open apps)")
            try:
                new = profiles_mod.new_profile(req.get("name") or "", first, a.get("preset", "custom"), profs)
            except ValueError as e:
                raise OpError(str(e)) from None
            if isinstance(req.get("id"), str) and re.fullmatch(r"[a-z0-9_]{1,48}", req["id"]):
                if any(p["id"] == req["id"] for p in profs) or req["id"] in profiles_mod.BUILTIN_IDS:
                    raise OpError(f"the id {req['id']!r} is taken")
                new["id"] = req["id"]
            new.update({k: v for k, v in req.items() if k not in ("id", "path")})
            pid = new["id"]
            profs = profiles_mod.add_profile(profs, new)
            created = True
        codes = [lang.code for lang in langs.LANGUAGES]
        checked = profiles_mod.validate_profiles(profs, codes)
        saved = next((p for p in checked if p["id"] == pid), None)
        if saved is None:
            raise OpError("the profile is not valid (an app profile needs an .exe file name)")
        bad = {k: v for k, v in req.items() if k in profiles_mod.BEHAVIOUR and saved.get(k) != v
               and not (_kind(v) == _kind(saved.get(k)) and _kind(v) in ("number", "array"))}
        if bad:
            raise OpError("nothing was changed; invalid values: " + ", ".join(f"{k}={v!r}" for k, v in bad.items()))
        self.s.update(profiles=checked)
        saved = next(p for p in self.s["profiles"] if p["id"] == pid)
        out: dict[str, Any] = {"created": created, "profile": {k: v for k, v in saved.items() if k != "icon"}}
        if not self.s["profiles_enabled"]:
            out["note"] = "per-app profiles are switched off (setting profiles_enabled): it applies when they are on"
        return out

    def _op_delete_profile(self, a: dict[str, Any]) -> dict[str, Any]:
        p = self._find_profile(a["profile"])
        if p.get("builtin"):
            raise OpError(f"{p['id']!r} is built in and cannot be deleted; switch it off with upsert_profile "
                          f"{{\"profile\": {{\"id\": \"{p['id']}\", \"enabled\": false}}}}")
        self.s.update(profiles=[q for q in self.s["profiles"] if q["id"] != p["id"]])
        return {"deleted": {k: v for k, v in p.items() if k != "icon"}}

    def _op_list_running_apps(self, a: dict[str, Any]) -> dict[str, Any]:
        from dualyrics import procs

        profs = self.s["profiles"]
        out = []
        for info in procs.running_apps():
            owner = next((p["id"] for p in profs if info.exe in p.get("exes", [])), None)
            out.append({"name": info.name, "exe": info.exe, "profile": owner})
        return {"apps": out}

    def _op_set_mode(self, a: dict[str, Any]) -> dict[str, Any]:
        mode = a["mode"]
        if a.get("profile"):
            p = self._find_profile(a["profile"])
            saved = self._set_profile(p["id"], mode=mode)
            return {"mode": saved["mode"], "applied_to": f"profile {p['id']}"}
        if self.live:
            self.app._handle_v2(f"mode:{mode}")
        elif mode == "off":
            self.s.update(visible=False)
        else:
            self.s.update(mode=mode, visible=True)
        out: dict[str, Any] = {"mode": self.s["mode"], "visible": self.s["visible"], "applied_to": "global"}
        if mode == "live" and not self.s["live_enabled"]:
            out["note"] = "Live mode is switched off in Settings > Live mode (setting live_enabled)"
        return out

    def _op_set_target_language(self, a: dict[str, Any]) -> dict[str, Any]:
        if a.get("profile"):
            p = self._find_profile(a["profile"])
            code = "" if a["language"].strip() in ("", "default", "global") else resolve_language(a["language"])
            saved = self._set_profile(p["id"], target_language=code)
            return {"target_language": saved["target_language"] or "(global)", "applied_to": f"profile {p['id']}"}
        code = resolve_language(a["language"])
        if self.live:
            self.app._handle_v2(f"lang:{code}")
        else:
            self.s.update(target_language=code)
        return {"target_language": self.s["target_language"], "language": langs.BY_CODE[code].name,
                "applied_to": "global"}

    def _op_sync_offset(self, a: dict[str, Any]) -> dict[str, Any]:
        has_song = self.live and bool(self.app.song.key)
        scope = a.get("scope") or ("song" if has_song else "global")
        if scope == "song" and not self.live:
            raise OpError("scope song " + NEEDS_APP_TEXT + "; scope global works without it")
        if scope == "song" and not has_song:
            raise OpError("no song is loaded; use scope global")
        cur = self.app.cache.get_offset(self.app.song.key) if scope == "song" else self.s["offset_ms"]
        if a.get("reset"):
            new = 0
        elif "ms" in a:
            new = a["ms"]
        elif "delta_ms" in a:
            new = cur + a["delta_ms"]
        else:
            return {"scope": scope, "offset_ms": cur}
        new = int(max(-15000, min(15000, new)))
        if scope == "song":
            from dualyrics import i18n

            self.app.cache.set_offset(self.app.song.key, new)
            self.app._notice = (i18n.t("st_offset", ms=new), time.time() + 2.5)
            st = self.app.song
            return {"scope": "song", "song": f"{st.track.artist} — {st.track.title}" if st.track else st.key,
                    "offset_ms": new}
        self.s.update(offset_ms=new)
        return {"scope": "global", "offset_ms": self.s["offset_ms"]}

    def _op_retranslate(self, a: dict[str, Any]) -> dict[str, Any]:
        st = self.app.song
        if not st.key or st.lyrics is None or not st.lyrics.found:
            raise OpError("no song with lyrics is loaded (live and on-screen captions are translated line by line)")
        self.app._handle("retranslate")
        return {"song": f"{st.track.artist} — {st.track.title}" if st.track else st.key,
                "target_language": self.app.effective_cfg()["target_language"], "status": "translating"}

    def _op_edit_line_translation(self, a: dict[str, Any]) -> dict[str, Any]:
        app = self.app
        st = app.song
        res = st.lyrics
        if res is None or not res.found or not res.synced:
            raise OpError("no song with synced lyrics is loaded (only lyric lines can be corrected)")
        idx = a.get("index")
        if idx is None:
            c = getattr(app.overlay, "_content", None)
            idx = c.index if c is not None else -1
            if idx < 0:
                raise OpError("no line is on screen now; give `index`")
        if not 0 <= idx < len(res.synced):
            raise OpError(f"index must be 0..{len(res.synced) - 1}")
        text = " ".join(a["text"].split())      # one line: newlines and tabs become spaces
        if not text:
            raise OpError("text is empty")
        if len(text) > MAX_LINE:
            raise OpError(f"text is too long ({len(text)} characters, at most {MAX_LINE})")
        if not res.synced[idx].text.strip():
            raise OpError(f"line {idx} is an empty (instrumental) line")
        app.edit_translation(idx, text)
        return {"index": idx, "original": res.synced[idx].text, "translation": text}

    def _op_get_usage(self, a: dict[str, Any]) -> dict[str, Any]:
        from dualyrics import usage as usage_mod

        period = a.get("period", "today")
        meter = self.app.usage if self.live else None
        own = False
        if meter is None:
            path = self.usage_path
            if path is None or not path.exists():
                return {"period": period, "totals": {}, "note": "no Gemini request was made yet"}
            meter, own = usage_mod.UsageMeter(path), True
        try:
            out = {"period": period, "totals": meter.totals(period), "today_limits": meter.quota_today(),
                   "by_model": meter.by_model(period), "by_key": meter.by_key(period),
                   "by_media": meter.by_media(period, 10), "daily": meter.daily(7),
                   "quota_resets_at": usage_mod.quota_reset_local(time.time()),
                   "gemini_keys": len(self.s["gemini_keys"]), "provider": self.s["provider"],
                   "notes": "by_key names keys by their position in Settings (#1, #2...); limits are estimates of "
                            "the free tier (setting usage_limits)"}
        finally:
            if own:
                meter.close()
        return out

    def _op_toggle_dubbing(self, a: dict[str, Any]) -> dict[str, Any]:
        if self.live:
            cfg = self.app.effective_cfg()
            want = a.get("enabled", not cfg["dub_enabled"])
            if want != cfg["dub_enabled"]:
                self.app._handle_v2("toggle_dub")
            prof = self._active_profile()
            scope = f"profile {prof['id']}" if prof.get("dub_custom") else "global"
            return {"enabled": self.app.effective_cfg()["dub_enabled"], "applied_to": scope}
        want = a.get("enabled", not self.s["dub_enabled"])
        self.s.update(dub_enabled=want)
        return {"enabled": self.s["dub_enabled"], "applied_to": "global"}

    def _op_show(self, a: dict[str, Any]) -> dict[str, Any]:
        self.s.update(visible=True)
        out: dict[str, Any] = {"visible": True}
        if self.live:
            self.app._hidden_song = ""
            prof = self._active_profile()
            if prof.get("mode") == "off":
                out["note"] = (f"the overlay is off for {self._profile_name(prof)!r} (its profile's mode); "
                               f"set_mode with that profile turns it back on")
        return out

    def _op_hide(self, a: dict[str, Any]) -> dict[str, Any]:
        self.s.update(visible=False)
        return {"visible": False}


def offline_controller() -> Controller:
    """A controller on the files in %APPDATA%\\Dualyrics, for when the app is not running."""
    from dualyrics import paths
    from dualyrics.settings import Settings

    return Controller(Settings(), templates_mod.TemplateStore(), usage_path=paths.data_dir() / "usage.sqlite")
