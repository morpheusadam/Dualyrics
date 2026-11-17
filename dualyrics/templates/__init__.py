"""Subtitle templates: one JSON document that describes how the overlay looks (docs/DESIGN.md, part 2).

- Built-in presets ship read-only in ``dualyrics/templates/builtin/<id>.json``.
- User templates live in ``%APPDATA%\\Dualyrics\\templates\\<id>.json``; import and export use the same format.
- :func:`sanitize` makes any document usable (unknown keys dropped, numbers clamped, bad values replaced by the
  preset or Cinema value). :func:`schema_errors` explains what is wrong, against ``schema.json`` (the reference
  JSON Schema in DESIGN.md 2.6), without needing the ``jsonschema`` package.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

SCHEMA = "dualyrics.template/1"
HERE = Path(__file__).resolve().parent
BUILTIN_DIR = HERE / "builtin"
SCHEMA_FILE = HERE / "schema.json"
BUILTIN_IDS = ("liquid_glass", "cinema", "netflix", "karaoke", "minimal", "neon", "game_hud", "glass",
               "persian_classic")
DEFAULT_ID = "cinema"            # base of validation: missing or bad values come from this preset
INSTALL_DEFAULT_ID = "liquid_glass"   # the look of new installs and of users who never chose one (v2.1)
OLD_DEFAULT_ID = "cinema"        # the default look of v2.0
MIGRATED_ID = "my-style"
LINE_KINDS = ("original", "translation", "next", "speaker")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
MAX_FILE_BYTES = 256 * 1024
DEFAULT_LATIN = ["Segoe UI Variable Display", "Segoe UI", "Arial"]
DEFAULT_RTL = ["Vazirmatn", "Segoe UI", "Tahoma"]

# used when a nullable field (gradient, shadow, glow) is switched on and there is nothing to copy
DEFAULT_SHADOW = {"x": 0, "y": 2, "blur": 6, "color": "#000000", "opacity": 0.6}
DEFAULT_GRADIENT = {"angle": 90, "stops": [{"offset": 0, "color": "#FFFFFF"}, {"offset": 1, "color": "#BFC7D5"}]}

_lock = threading.Lock()
_schema_cache: dict[str, Any] | None = None
_builtin_cache: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------- schema and built-ins
def schema() -> dict[str, Any]:
    global _schema_cache
    with _lock:
        if _schema_cache is None:
            _schema_cache = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        return _schema_cache


def builtin(tid: str) -> dict[str, Any] | None:
    """A fresh copy of a built-in preset (None for an unknown id)."""
    if tid not in BUILTIN_IDS:
        return None
    with _lock:
        if tid not in _builtin_cache:
            _builtin_cache[tid] = json.loads((BUILTIN_DIR / f"{tid}.json").read_text(encoding="utf-8"))
        return copy.deepcopy(_builtin_cache[tid])


def builtins() -> list[dict[str, Any]]:
    return [builtin(t) for t in BUILTIN_IDS]  # type: ignore[misc]


def _resolve(node: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: Any = schema()
        for part in ref[2:].split("/"):
            target = target[part]
        return _resolve(target)
    return node


def _props(node: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merged ``properties`` and ``required`` of a node and its ``allOf`` parts."""
    node = _resolve(node)
    props: dict[str, Any] = {}
    req: list[str] = []
    for part in node.get("allOf", []):
        p, r = _props(part)
        props.update(p)
        req += r
    props.update(node.get("properties", {}))
    req += node.get("required", [])
    return props, req


def _types(node: dict[str, Any]) -> list[str]:
    t = node.get("type")
    if t is None:
        return []
    return list(t) if isinstance(t, list) else [t]


def _is_type(v: Any, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer())
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return True


# ---------------------------------------------------------------- explaining errors (schema check)
def schema_errors(doc: Any, limit: int = 50) -> list[str]:
    """Human-readable problems of ``doc`` against the template JSON Schema ([] = valid)."""
    errors: list[str] = []
    _check(doc, schema(), "", errors)
    return errors[:limit]


def _where(path: str) -> str:
    return path or "(template)"


def _check(v: Any, node: dict[str, Any], path: str, errors: list[str]) -> set[str]:
    """Validate ``v``; returns the property names it evaluated (for ``unevaluatedProperties``)."""
    node = _resolve(node)
    evaluated: set[str] = set()
    if "oneOf" in node:
        passing = []
        for sub in node["oneOf"]:
            errs: list[str] = []
            _check(v, sub, path, errs)
            if not errs:
                passing.append(sub)
        if len(passing) != 1:
            nullable = any(_resolve(s).get("type") == "null" for s in node["oneOf"])
            what = " or null" if nullable else ""
            kinds = [s for s in node["oneOf"] if _resolve(s).get("type") != "null"]
            if v is not None and kinds:
                sub_errs: list[str] = []
                _check(v, kinds[0], path, sub_errs)
                errors.extend(sub_errs or [f"{_where(path)}: must be one value{what}"])
            else:
                errors.append(f"{_where(path)}: must be one value{what}")
        return evaluated
    for sub in node.get("allOf", []):
        evaluated |= _check(v, sub, path, errors)
    types = _types(node)
    if types and not any(_is_type(v, t) for t in types):
        errors.append(f"{_where(path)}: expected {' or '.join(types)}, got {_json_type(v)}")
        return evaluated
    if "const" in node and v != node["const"]:
        errors.append(f"{_where(path)}: must be {json.dumps(node['const'])}")
    if "enum" in node and v not in node["enum"]:
        errors.append(f"{_where(path)}: {json.dumps(v, ensure_ascii=False)} is not one of "
                      f"{', '.join(json.dumps(e) for e in node['enum'])}")
    if isinstance(v, str):
        if "pattern" in node and not re.search(node["pattern"], v):
            errors.append(f"{_where(path)}: {json.dumps(v, ensure_ascii=False)} has the wrong format")
        if "maxLength" in node and len(v) > node["maxLength"]:
            errors.append(f"{_where(path)}: longer than {node['maxLength']} characters")
        if "minLength" in node and len(v) < node["minLength"]:
            errors.append(f"{_where(path)}: must not be empty")
    if _is_type(v, "number"):
        if "minimum" in node and v < node["minimum"]:
            errors.append(f"{_where(path)}: {v} is below the minimum {node['minimum']}")
        if "maximum" in node and v > node["maximum"]:
            errors.append(f"{_where(path)}: {v} is above the maximum {node['maximum']}")
        if "multipleOf" in node and (v / node["multipleOf"]) % 1:
            errors.append(f"{_where(path)}: must be a multiple of {node['multipleOf']}")
    if isinstance(v, list):
        if "minItems" in node and len(v) < node["minItems"]:
            errors.append(f"{_where(path)}: needs at least {node['minItems']} items")
        if "maxItems" in node and len(v) > node["maxItems"]:
            errors.append(f"{_where(path)}: at most {node['maxItems']} items")
        if "items" in node:
            for i, item in enumerate(v):
                _check(item, node["items"], f"{path}[{i}]", errors)
    if isinstance(v, dict):
        props = node.get("properties", {})
        for k in node.get("required", []):
            if k not in v:
                errors.append(f"{_where(path)}: missing \"{k}\"")
        for k, sub in props.items():
            if k in v:
                _check(v[k], sub, f"{path}.{k}" if path else k, errors)
                evaluated.add(k)
        names = node.get("propertyNames")
        for k, item in v.items():
            if names and "pattern" in names and not re.search(names["pattern"], k):
                errors.append(f"{_where(path)}: bad key \"{k}\"")
            if k in props:
                continue
            extra = node.get("additionalProperties")
            if extra is False:
                errors.append(f"{_where(path)}: unknown key \"{k}\"")
            elif isinstance(extra, dict):
                _check(item, extra, f"{path}.{k}" if path else k, errors)
                evaluated.add(k)
        if node.get("unevaluatedProperties") is False:
            for k in v:
                if k not in evaluated:
                    errors.append(f"{_where(path)}: unknown key \"{k}\"")
    return evaluated


def _json_type(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


# ---------------------------------------------------------------- making documents valid (runtime)
def _clean(v: Any, node: dict[str, Any], fallback: Any) -> Any:
    node = _resolve(node)
    if "oneOf" in node:
        options = node["oneOf"]
        if v is None and any(_resolve(o).get("type") == "null" for o in options):
            return None
        real = next((o for o in options if _resolve(o).get("type") != "null"), options[0])
        if fallback is None:
            # switched on where the preset has none: missing keys are completed with defaults, but a wrong
            # value (one gradient stop, a colour name...) keeps the preset's "none"
            errs: list[str] = []
            _check(v, real, "", errs)
            if any("missing \"" not in e for e in errs):
                return None
            fallback = _default_for(real)
        return _clean(v, real, fallback)
    types = _types(node)
    if "null" in types and v is None:
        return None
    if "allOf" in node or "properties" in node or "object" in types:
        if not isinstance(v, dict):
            return copy.deepcopy(fallback)
        if "additionalProperties" in node and isinstance(node["additionalProperties"], dict):
            return _clean_localized(v, node, fallback)
        props, _req = _props(node)
        fb = fallback if isinstance(fallback, dict) else {}
        out: dict[str, Any] = {}
        for k, sub in props.items():
            if k in v:
                out[k] = _clean(v[k], sub, fb.get(k, _default_for(sub)))
            elif k in fb:
                out[k] = copy.deepcopy(fb[k])
        return out
    if "array" in types:
        fb = fallback if isinstance(fallback, list) else []
        if not isinstance(v, list):
            return copy.deepcopy(fb)
        item_node = node.get("items", {})
        items = []
        for i, item in enumerate(v[: node.get("maxItems", 64)]):
            item_fb = fb[i] if i < len(fb) else (fb[-1] if fb else _default_for(item_node))
            cleaned = _clean(item, item_node, item_fb)
            if cleaned is not None and not (isinstance(cleaned, str) and not cleaned):
                items.append(cleaned)
        if len(items) < node.get("minItems", 0):
            return copy.deepcopy(fb)
        return items
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return v if v in node["enum"] else copy.deepcopy(fallback)
    if "boolean" in types:
        return v if isinstance(v, bool) else fallback
    if "number" in types or "integer" in types:
        if not _is_type(v, "number"):
            return fallback
        lo, hi = node.get("minimum", -math.inf), node.get("maximum", math.inf)
        v = min(hi, max(lo, float(v)))
        if "multipleOf" in node:
            step = node["multipleOf"]
            v = min(hi, max(lo, round(v / step) * step))
        if "integer" in types:
            return int(round(v))
        return int(v) if float(v).is_integer() else round(v, 4)
    if "string" in types:
        if not isinstance(v, str):
            return fallback
        if "maxLength" in node:
            v = v[: node["maxLength"]]
        if "pattern" in node and not re.search(node["pattern"], v):
            return fallback
        if node.get("pattern") == "^#[0-9A-Fa-f]{6}$":
            v = v.upper()
        return v
    return copy.deepcopy(v if v is not None else fallback)


def _clean_localized(v: dict[str, Any], node: dict[str, Any], fallback: Any) -> dict[str, str]:
    pat = node.get("propertyNames", {}).get("pattern", ".*")
    out = {k: s.strip()[:200] for k, s in v.items() if isinstance(k, str) and re.search(pat, k)
           and isinstance(s, str) and s.strip()}
    if "en" not in out:
        fb = fallback if isinstance(fallback, dict) else {}
        out["en"] = fb.get("en") or next(iter(out.values()), "Template")
    return out


def _default_for(node: dict[str, Any]) -> Any:
    node = _resolve(node)
    props, _ = _props(node)
    if set(props) >= {"x", "y", "blur", "color", "opacity"}:
        return copy.deepcopy(DEFAULT_SHADOW)
    if set(props) >= {"angle", "stops"}:
        return copy.deepcopy(DEFAULT_GRADIENT)
    if set(props) >= {"offset", "color"}:
        return {"offset": 1, "color": "#FFFFFF"}
    if "string" in _types(node):
        return ""
    return None


def sanitize(raw: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """A complete, valid template built from ``raw``. Missing or bad values come from ``base``, else from the
    preset named in ``based_on``, else from Cinema."""
    r = raw if isinstance(raw, dict) else {}
    if base is None:
        based = r.get("based_on") if isinstance(r.get("based_on"), str) else None
        own = r.get("id") if isinstance(r.get("id"), str) else None
        base = builtin(based or "") or builtin(own or "") or builtin(DEFAULT_ID)
    assert base is not None
    out = _clean(r, schema(), base)
    out["schema"] = SCHEMA
    if not isinstance(out.get("id"), str) or not ID_RE.match(out["id"]):
        out["id"] = slug(_name_en(out) or "template")
    out["id"] = _not_device(out["id"])
    for k in ("name",):
        if k not in out:
            out[k] = copy.deepcopy(base[k])
    out.setdefault("description", {"en": ""} if "description" not in base else copy.deepcopy(base["description"]))
    out.setdefault("hints", {"placement": "keep", "click_through": None, "locked": None})
    out["builtin"] = bool(out.get("builtin")) and out["id"] in BUILTIN_IDS
    if out.get("based_on") is not None and out["based_on"] not in BUILTIN_IDS:
        out["based_on"] = None
    return out


def _name_en(doc: dict[str, Any]) -> str:
    n = doc.get("name")
    return n.get("en", "") if isinstance(n, dict) else ""


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", (text or "").lower()).strip("-_")
    s = s[:40]
    if len(s) < 2 or not s[0].isalnum():
        s = ("t-" + s).strip("-")[:40] if s else "template"
    return _not_device(s) if ID_RE.match(s) else "template"


_DEVICES = {"con", "prn", "aux", "nul"} | {f"{p}{n}" for p in ("com", "lpt") for n in range(10)}


def _not_device(tid: str) -> str:
    """Template ids become file names: "con.json" or "nul.json" would be a Windows device, not a file."""
    return f"{tid}-t" if tid in _DEVICES else tid


def display_name(doc: dict[str, Any], lang: str, translate: Callable[[str], str] | None = None) -> str:
    """Name to show: built-ins through i18n (``tpl_<id>``), user templates from their own ``name``."""
    if doc.get("builtin") and translate is not None:
        return translate(f"tpl_{doc['id']}")
    n = doc.get("name") or {}
    return n.get(lang) or n.get("en") or doc.get("id", "")


def description(doc: dict[str, Any], lang: str, translate: Callable[[str], str] | None = None) -> str:
    if doc.get("builtin") and translate is not None:
        return translate(f"tpl_{doc['id']}_desc")
    d = doc.get("description") or {}
    return d.get(lang) or d.get("en") or ""


def style_hash(obj: Any) -> str:
    import hashlib

    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- v1 migration (DESIGN.md 2.5)
def from_v1(s: dict[str, Any]) -> dict[str, Any]:
    """The v1 appearance settings as one user template (``my-style``)."""
    t = builtin(DEFAULT_ID)
    assert t is not None
    t.update(id=MIGRATED_ID, builtin=False, based_on=None,
             name={"en": "My style (v1)", "fa": "سبک من (نسخه ۱)"},
             description={"en": "Your Dualyrics 1.x look.", "fa": "ظاهر شما در Dualyrics نسخه‌ی ۱."})
    t["scale"] = {"mode": "dpi", "reference_height": 1080}
    c = t["container"]
    c["background"] = {"color": s.get("color_bg", "#101014"),
                       "opacity": 0.0 if s.get("text_only") else float(s.get("opacity", 0.88)), "gradient": None}
    c["corner_radius"] = 8 if s.get("rounded", True) else 0
    c["width"] = {"unit": "px", "value": int(s.get("width", 900)), "fit_content": False, "min_px": 0}
    c["align"] = s.get("align", "center") if s.get("align") in ("center", "left", "right") else "center"
    c["padding"] = {"x": 16, "y": 10}
    c["line_gap"] = 4
    c["group_gap"] = 4
    outline = bool(s.get("outline", True))
    for kind in ("original", "translation", "next"):
        ln = t["lines"][kind]
        src = "original" if kind == "next" else kind
        fam = s.get(f"font_{src}", "auto") or "auto"
        stack_l = ["auto"] if fam == "auto" else [fam] + DEFAULT_LATIN
        stack_r = ["auto"] if fam == "auto" else [fam] + DEFAULT_RTL
        ln["font"]["latin"], ln["font"]["rtl"] = stack_l[:6], stack_r[:6]
        size = int(s.get(f"size_{src}", 20))
        ln["font"]["size"] = max(8, int(size * 0.72)) if kind == "next" else size
        ln["font"]["weight"] = 400 if kind == "next" else (700 if s.get(f"bold_{src}", True) else 400)
        ln["color"] = s.get(f"color_{kind}", ln["color"])
        ln["opacity"] = 1.0
        ln["outline"] = {"width": int(s.get("outline_width", 2)) if outline else 0,
                         "color": s.get("outline_color", "#000000"), "opacity": 1.0}
        ln["shadow"] = [{"x": 2, "y": 2, "blur": 0, "color": "#000000", "opacity": 1.0}] if s.get("shadow", True) else []
        ln["visible"] = bool(s.get(f"show_{kind}", True))
    t["animation"].update(enter="none", exit="none", duration_ms=0, exit_duration_ms=0)
    t["timing"]["min_display_ms"] = 0
    return sanitize(t, base=builtin(DEFAULT_ID))


# ---------------------------------------------------------------- contrast (DESIGN.md 3.3)
def hex_rgb(h: str) -> tuple[float, float, float]:
    return int(h[1:3], 16) / 255, int(h[3:5], 16) / 255, int(h[5:7], 16) / 255


def luminance(rgb: tuple[float, float, float]) -> float:
    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def over(fg: tuple[float, float, float], alpha: float, bg: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))  # type: ignore[return-value]


def _colors(color: str, gradient: dict[str, Any] | None) -> list[str]:
    return [s["color"] for s in gradient["stops"]] if gradient else [color]


def is_large(style: dict[str, Any]) -> bool:
    size, weight = style["font"]["size"], style["font"]["weight"]
    return size >= 24 or (size >= 18.66 and weight >= 700)


def line_contrast(t: dict[str, Any], kind: str,
                  backdrops: Iterable[tuple[float, float, float]] = ((1, 1, 1), (0.5, 0.5, 0.5), (0, 0, 0)),
                  ) -> dict[str, Any]:
    """Worst text/background ratio of one line over ``backdrops`` (box composited over each), text vs
    outline, and whether the line passes by the DESIGN.md 3.3 method."""
    style = t["lines"][kind]
    bg = t["container"]["background"]
    need = 3.0 if is_large(style) else 4.5
    worst = math.inf
    for back in backdrops:
        for box_c in _colors(bg["color"], bg["gradient"]):
            eff = over(hex_rgb(box_c), bg["opacity"], back)
            for c in _colors(style["color"], style["gradient"]):
                worst = min(worst, ratio(over(hex_rgb(c), style["opacity"], eff), eff))
    outline_ratio = 0.0
    o = style["outline"]
    if o["width"] >= 1 and o["opacity"] > 0:
        outline_ratio = min(ratio(hex_rgb(c), hex_rgb(o["color"])) for c in _colors(style["color"], style["gradient"]))
    by = ""
    if worst >= need:
        by = "box"
    elif outline_ratio >= 7:
        by = "outline"
    elif bg["opacity"] == 0 and o["width"] < 1 and any(s["opacity"] >= 0.5 for s in style["shadow"]):
        by = "halo"
    return {"ratio": round(worst, 2), "outline": round(outline_ratio, 2), "need": need, "pass": bool(by), "by": by}


# ---------------------------------------------------------------- store
class TemplateStore:
    """Built-in and user templates; user files in ``folder``. Thread-safe; ``rev`` grows on every change."""

    def __init__(self, folder: Path | None = None) -> None:
        if folder is None:
            from dualyrics import paths

            folder = paths.data_dir() / "templates"
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._user: dict[str, dict[str, Any]] = {}
        self.rev = 0
        self._listeners: list[Callable[[], None]] = []
        self.reload()

    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _changed(self) -> None:
        self.rev += 1
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                log.exception("template listener failed")

    def reload(self) -> None:
        user: dict[str, dict[str, Any]] = {}
        for p in sorted(self.folder.glob("*.json")):
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
                doc = sanitize(json.loads(p.read_text(encoding="utf-8-sig")))
            except Exception as e:
                log.warning("template %s is unreadable: %s", p.name, e)
                continue
            doc["builtin"] = False
            if doc["id"] in BUILTIN_IDS or doc["id"] in user:
                continue
            user[doc["id"]] = doc
        with self._lock:
            self._user = user
        self._changed()

    # -- reading
    def ids(self) -> list[str]:
        with self._lock:
            users = sorted(self._user.values(), key=lambda d: _name_en(d).lower())
            return list(BUILTIN_IDS) + [d["id"] for d in users]

    def all(self) -> list[dict[str, Any]]:
        return [d for d in (self.get(i) for i in self.ids()) if d is not None]

    def exists(self, tid: str) -> bool:
        with self._lock:
            return tid in BUILTIN_IDS or tid in self._user

    def get(self, tid: str) -> dict[str, Any] | None:
        if tid in BUILTIN_IDS:
            return builtin(tid)
        with self._lock:
            d = self._user.get(tid)
            return copy.deepcopy(d) if d is not None else None

    def resolve(self, tid: str) -> dict[str, Any]:
        """The template for ``tid``, or Cinema when it does not exist (deleted, profile from another PC)."""
        return self.get(tid) or builtin(DEFAULT_ID)  # type: ignore[return-value]

    def is_builtin(self, tid: str) -> bool:
        return tid in BUILTIN_IDS

    def new_id(self, base: str) -> str:
        tid = slug(base)
        n = 2
        cand = tid
        while self.exists(cand):
            suffix = f"-{n}"
            cand = tid[: 40 - len(suffix)] + suffix
            n += 1
        return cand

    # -- writing
    def save(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Validate and store a user template (a built-in id gets a new id). Returns the stored document."""
        d = sanitize(doc)
        d["builtin"] = False
        if d["id"] in BUILTIN_IDS:
            d["based_on"] = d["based_on"] or d["id"]
            d["id"] = self.new_id(d["id"] + "-copy")
        self._write(d)
        with self._lock:
            self._user[d["id"]] = d
        self._changed()
        return copy.deepcopy(d)

    def _write(self, d: dict[str, Any]) -> None:
        path = self.folder / f"{d['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def fork(self, tid: str, lang: str = "en", names: dict[str, str] | None = None) -> dict[str, Any]:
        """Copy a template into a new user template (``based_on`` = the preset it came from)."""
        src = self.resolve(tid)
        d = copy.deepcopy(src)
        base_names = names or {k: v for k, v in (src.get("name") or {}).items()}
        suffix = {"en": " (copy)", "fa": " (کپی)"}
        d["name"] = {k: f"{v}{suffix.get(k, ' (copy)')}" for k, v in base_names.items()} or {"en": "Copy"}
        d["based_on"] = src["id"] if src.get("builtin") else src.get("based_on")
        d["builtin"] = False
        d["id"] = self.new_id(src["id"] if src.get("builtin") else (src.get("based_on") or src["id"]))
        return self.save(d)

    def rename(self, tid: str, name: str, lang: str = "en") -> dict[str, Any] | None:
        d = self.get(tid)
        name = (name or "").strip()[:80]
        if d is None or d.get("builtin") or not name:
            return d
        d["name"] = {**(d.get("name") or {}), lang: name}
        if lang != "en" and "en" not in d["name"]:
            d["name"]["en"] = name
        return self.save(d)

    def delete(self, tid: str) -> dict[str, Any] | None:
        """Remove a user template; returns it (for Undo). Built-ins cannot be deleted."""
        if tid in BUILTIN_IDS:
            return None
        with self._lock:
            d = self._user.pop(tid, None)
        if d is None:
            return None
        try:
            (self.folder / f"{tid}.json").unlink()
        except OSError as e:
            log.warning("could not delete template file %s: %s", tid, e)
        self._changed()
        return d

    def restore(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Undo a delete (keeps the id when it is still free)."""
        return self.save(doc)

    def reset_to_preset(self, tid: str) -> dict[str, Any] | None:
        d = self.get(tid)
        if d is None or d.get("builtin") or not d.get("based_on"):
            return d
        preset = builtin(d["based_on"])
        if preset is None:
            return d
        preset.update(id=d["id"], name=d["name"], description=d.get("description", preset["description"]),
                      builtin=False, based_on=d["based_on"])
        return self.save(preset)

    # -- import / export
    def export(self, tid: str, path: Path) -> None:
        d = self.resolve(tid)
        d.pop("builtin", None)
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def import_file(self, path: Path) -> tuple[dict[str, Any], list[str]]:
        """Import a template file. Returns (stored template, problems that were fixed). Raises ValueError when
        the file is not a template at all."""
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file is too large for a template")
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"not a JSON file: {e}") from e
        return self.import_doc(raw)

    def import_doc(self, raw: Any) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            raise ValueError("not a Dualyrics template (schema must be \"dualyrics.template/1\")")
        problems = schema_errors(raw)
        doc = sanitize(raw)
        doc["builtin"] = False
        if self.exists(doc["id"]):
            doc["id"] = self.new_id(doc["id"])
        return self.save(doc), problems
