r"""Offline translator: the last tier of the chain (Gemini, then free Google, then this), so lines are still
translated without a network or when every online translator refuses.

It reads Argos Translate language packages (``.argosmodel``, the package format and index of
https://github.com/argosopentech/argos-translate, MIT) directly with CTranslate2 and SentencePiece, without
the argostranslate package and its spaCy / Stanza dependencies:

- the package index (``index.json`` of argospm-index) lists one package per language pair with download links;
- a package is a zip of one folder with ``metadata.json`` (``from_code``, ``to_code``, optional
  ``target_prefix``), a CTranslate2 model in ``model/`` and ``sentencepiece.model`` (packages that use a BPE
  tokenizer instead are not supported and are shown as such);
- a line is split into sentences at sentence punctuation, every sentence is tokenized with SentencePiece and the
  sentences of all lines go through ``translate_batch`` together (beam 2, ``replace_unknowns``, length
  penalty 0.2 as in Argos), then are decoded and joined again;
- a pair without its own package goes through English (``fa -> en -> de``) when both halves are installed.

Packages download on demand (resumable, like the Whisper models) into ``%APPDATA%\Dualyrics\offline-translate``
and are unpacked there. The licence of every package is read from its metadata (``license``) or its README and
shown in Settings; the models come from different sources (OPUS-MT and others) under their own licences.

CTranslate2 comes with Live mode (faster-whisper); SentencePiece is installed with the same helper.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dualyrics import download as dl, langs, net, paths

log = logging.getLogger(__name__)

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
DEPS = {"ctranslate2": "ctranslate2>=4.0", "sentencepiece": "sentencepiece>=0.2"}
PIVOT = "en"
CHUNK = 1 << 16
MAX_LOADED = 3            # models kept in memory (each is ~100-200 MB)
BEAM = 2
_SENTENCE = re.compile(r"(?<=[.!?…。！？؟])\s+")
_CODES = {"zt": "zh-tw", "zh-tw": "zh-tw", "zh-hant": "zh-tw", "zh": "zh", "zh-cn": "zh", "zh-hans": "zh",
          "pb": "pt", "nb": "no", "iw": "he"}


DownloadCancelled = dl.DownloadCancelled   # resumable downloads with retries: dualyrics/download.py


def norm_code(code: str) -> str:
    """Language code of the app or of an Argos package, comparable ('zh-CN' = 'zh', 'zt' = 'zh-TW')."""
    c = (code or "").strip().lower().replace("_", "-")
    return _CODES.get(c) or langs.base_code(c)


def root_dir() -> Path:
    p = paths.data_dir() / "offline-translate"
    p.mkdir(parents=True, exist_ok=True)
    return p


def deps_missing() -> list[str]:
    """pip requirements of the offline translator that are not installed."""
    from dualyrics.live import deps

    return [req for mod, req in DEPS.items() if not deps.has(mod)]


# ---------------------------------------------------------------- the package index
@dataclass(frozen=True)
class PackageInfo:
    code: str                    # e.g. "translate-en_fa"
    from_code: str
    from_name: str
    to_code: str
    to_name: str
    version: str
    links: tuple[str, ...]
    license: str = ""            # when the index names it (else read from the package after download)

    @property
    def dir_name(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", self.code or f"translate-{self.from_code}_{self.to_code}")

    @staticmethod
    def from_entry(e: Any) -> "PackageInfo | None":
        if not isinstance(e, dict) or e.get("type", "translate") != "translate":
            return None
        links = tuple(str(x) for x in (e.get("links") or []) if isinstance(x, str) and x.startswith("https://"))
        fc, tc = str(e.get("from_code") or ""), str(e.get("to_code") or "")
        if not (links and fc and tc):
            return None
        return PackageInfo(str(e.get("code") or f"translate-{fc}_{tc}"), fc, str(e.get("from_name") or fc), tc,
                           str(e.get("to_name") or tc), str(e.get("package_version") or ""), links,
                           str(e.get("license") or e.get("licence") or ""))


def parse_index(data: Any) -> list[PackageInfo]:
    out = [p for p in (PackageInfo.from_entry(e) for e in (data if isinstance(data, list) else [])) if p]
    return sorted(out, key=lambda p: (p.from_name, p.to_name))


def cached_index() -> list[PackageInfo]:
    try:
        return parse_index(json.loads((root_dir() / "index.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def fetch_index(session: Any) -> list[PackageInfo]:
    """The package list from the internet (kept on disk for the next time); the saved copy when offline."""
    try:
        r = session.get(INDEX_URL, timeout=net.TIMEOUT_FAST)
        r.raise_for_status()
        data = r.json()
        got = parse_index(data)
        if got:
            (root_dir() / "index.json").write_text(json.dumps(data), encoding="utf-8")
            return got
    except Exception as e:  # noqa: BLE001
        log.info("offline package index not fetched: %s", e)
        got = cached_index()
        if got:
            return got
        raise
    return cached_index()


# ---------------------------------------------------------------- installed packages
_LICENSES = (
    (re.compile(r"\bCC[- ]?BY[- ]?SA[- ]?(\d(?:\.\d)?)?", re.I), "CC-BY-SA"),
    (re.compile(r"\bCC[- ]?BY[- ]?NC[- ]?(\d(?:\.\d)?)?", re.I), "CC-BY-NC"),
    (re.compile(r"\bCC[- ]?BY[- ]?(\d(?:\.\d)?)?", re.I), "CC-BY"),
    (re.compile(r"\bCC0\b|public domain", re.I), "CC0"),
    (re.compile(r"\bMIT\b"), "MIT"),
    (re.compile(r"\bApache(?:[- ]License)?[- ,]*(?:Version )?(2\.0)?", re.I), "Apache"),
    (re.compile(r"\bBSD\b"), "BSD"),
    (re.compile(r"\bGPL[- ]?v?(\d)?", re.I), "GPL"),
)


def license_from_text(text: str) -> str:
    """The licence a package README names ('' when it names none). Only the part after a 'licen[cs]e' word
    is searched, so model names such as 'OPUS-MT' never count."""
    m = re.search(r"licen[cs]", text or "", re.I)
    if not m:
        return ""
    tail = text[m.start():m.start() + 400]
    for rx, name in _LICENSES:
        hit = rx.search(tail)
        if hit:
            ver = next((g for g in hit.groups() if g), "") if hit.groups() else ""
            return f"{name}-{ver}" if ver and name not in ("CC0",) else name
    return ""


@dataclass
class Installed:
    path: Path
    code: str
    from_code: str
    to_code: str
    version: str
    target_prefix: str
    license: str
    supported: bool              # a SentencePiece package (BPE packages are not supported)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def pair(self) -> tuple[str, str]:
        return norm_code(self.from_code), norm_code(self.to_code)


def read_package(pkg_dir: Path) -> Installed | None:
    try:
        meta = json.loads((pkg_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or not meta.get("from_code") or not meta.get("to_code"):
        return None
    lic = str(meta.get("license") or meta.get("licence") or "")
    if not lic:
        for name in ("README.md", "README", "LICENSE", "LICENSE.md"):
            try:
                lic = license_from_text((pkg_dir / name).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if lic:
                break
    supported = (pkg_dir / "sentencepiece.model").is_file() and (pkg_dir / "model").is_dir()
    return Installed(pkg_dir, str(meta.get("code") or pkg_dir.name), str(meta["from_code"]), str(meta["to_code"]),
                     str(meta.get("package_version") or ""), str(meta.get("target_prefix") or ""), lic, supported,
                     meta)


def installed_packages(root: Path | None = None) -> list[Installed]:
    root = root or root_dir()
    out = []
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / ".complete").exists())
    except OSError:
        return []
    for d in dirs:
        pkg = read_package(d)
        if pkg is not None:
            out.append(pkg)
    return out


def remove(code_or_dir: str, root: Path | None = None) -> bool:
    root = root or root_dir()
    target = (root / code_or_dir).resolve()
    if target.parent != root.resolve() or not target.is_dir():
        return False
    try:
        (target / ".complete").unlink(missing_ok=True)   # first: a half-removed package never counts as installed
    except OSError as e:
        log.warning("offline package %s not removed: %s", target.name, e)
        return False
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


# ---------------------------------------------------------------- download (resumable)
_dl_locks: dict[str, threading.Lock] = {}
_dl_guard = threading.Lock()


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Unpack without letting a member escape ``dest`` (absolute paths, '..')."""
    base = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if base != target and base not in target.parents:
            raise OSError(f"unsafe path in package: {info.filename}")
    zf.extractall(dest)


def download(info: PackageInfo, session: Any, progress: Callable[[int, int], None] = lambda d, t: None,
             cancelled: Callable[[], bool] = lambda: False, root: Path | None = None) -> Installed:
    """Download and unpack one package (a broken download resumes where it stopped). Returns it installed."""
    root = root or root_dir()
    with _dl_guard:
        lock = _dl_locks.setdefault(info.dir_name, threading.Lock())
    while not lock.acquire(timeout=0.5):
        if cancelled():
            raise DownloadCancelled()
    try:
        return _download_locked(info, session, progress, cancelled, root)
    finally:
        lock.release()


def _download_locked(info: PackageInfo, session: Any, progress: Callable[[int, int], None],
                     cancelled: Callable[[], bool], root: Path) -> Installed:
    dest = root / info.dir_name
    if (dest / ".complete").exists():
        pkg = read_package(dest)
        if pkg is not None:
            return pkg
    part = root / f"{info.dir_name}.argosmodel"     # download.fetch writes <part>.part, renamed when complete
    last: Exception | None = None
    for url in info.links:
        try:
            dl.fetch(session, url, part, progress, cancelled)
            break
        except DownloadCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - try the next mirror
            last = e
            log.info("offline package %s: %s failed: %s", info.code, url, e)
    else:
        raise OSError(f"{info.code}: download failed ({last})")
    tmp = root / f"{info.dir_name}.unpack"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        if not zipfile.is_zipfile(part):
            part.unlink(missing_ok=True)       # damaged: never resume onto it
            raise OSError(f"{info.code}: the download is not a package")
        with zipfile.ZipFile(part) as zf:
            _safe_extract(zf, tmp)
        meta = next(iter(sorted(tmp.rglob("metadata.json"), key=lambda p: len(p.parts))), None)
        if meta is None:
            raise OSError(f"{info.code}: no metadata.json in the package")
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(meta.parent, dest)
        (dest / ".complete").write_text(json.dumps({"code": info.code, "version": info.version,
                                                    "at": time.time()}), encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    part.unlink(missing_ok=True)
    pkg = read_package(dest)
    if pkg is None:
        raise OSError(f"{info.code}: unreadable package")
    log.info("offline package %s installed (licence: %s)", info.code, pkg.license or "not stated")
    return pkg


# ---------------------------------------------------------------- translation
# common English words that are not common words of other Latin-script languages (so no "a", "in", "is", "to",
# "of", "for", "my", "he", "was", "will", "has", "so", "do", "i", "die" ...)
_EN_WORDS = frozenset((
    "the and you your it that this with are what when where who why how not but they them have had would "
    "could should just know like love we she his there their from out up if don't i'm it's can't won't i'll "
    "you're we're that's there's never ever been be".split()))
_WORD = re.compile(r"[a-z']+")


def looks_english(text: str) -> bool:
    """Latin-script text whose words are clearly English (at least 2 and ~20 % common English words)."""
    words = _WORD.findall((text or "").lower().replace("’", "'"))
    if not words:
        return False
    hits = sum(1 for w in words if w in _EN_WORDS)
    return hits >= min(2, len(words)) and hits / len(words) >= 0.2


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE.split((text or "").strip())]
    return [p for p in parts if p] or ([text.strip()] if text and text.strip() else [])


class _Model:
    def __init__(self, pkg: Installed, ct2: Any, spm: Any) -> None:
        threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        self.pkg = pkg
        self.translator = ct2.Translator(str(pkg.path / "model"), device="cpu", inter_threads=1,
                                         intra_threads=threads)
        self.sp = spm.SentencePieceProcessor(model_file=str(pkg.path / "sentencepiece.model"))
        self.used = time.monotonic()

    def translate(self, texts: list[str]) -> list[str]:
        """One output per input text (each split into sentences, all sentences in one batch)."""
        self.used = time.monotonic()
        sents: list[str] = []
        owner: list[int] = []
        for i, t in enumerate(texts):
            for s in split_sentences(t):
                sents.append(s)
                owner.append(i)
        if not sents:
            return ["" for _ in texts]
        tokens = [self.sp.encode(s, out_type=str) for s in sents]
        prefix = self.pkg.target_prefix
        res = self.translator.translate_batch(tokens, target_prefix=[[prefix]] * len(tokens) if prefix else None,
                                              replace_unknowns=True, beam_size=BEAM, num_hypotheses=1,
                                              length_penalty=0.2, max_batch_size=32)
        outs: list[list[str]] = [[] for _ in texts]
        for i, r in enumerate(res):
            value = self.sp.decode_pieces(r.hypotheses[0]).replace("▁", " ")
            if prefix and value.startswith(prefix):
                value = value[len(prefix):]
            outs[owner[i]].append(value.strip())
        return [" ".join(o).strip() for o in outs]


class OfflineTranslator:
    """Translate with the installed packages. ``ready`` and ``translate`` can be called from any thread."""

    def __init__(self, root: Path | None = None, loader: Callable[[Installed], Any] | None = None) -> None:
        self.root = root
        self._loader = loader                 # tests: a fake model per package
        self._lock = threading.Lock()
        self._models: dict[str, Any] = {}
        self._pkgs: list[Installed] | None = None
        self._deps_ok: bool | None = None if loader is None else True

    # -- packages
    def refresh(self) -> None:
        with self._lock:
            self._pkgs = None
            self._models.clear()
            if self._loader is None:
                self._deps_ok = None

    def packages(self) -> list[Installed]:
        with self._lock:
            if self._pkgs is None:
                self._pkgs = [p for p in installed_packages(self.root) if p.supported]
            return list(self._pkgs)

    def deps_ok(self) -> bool:
        if self._deps_ok is None:
            self._deps_ok = not deps_missing()
        return self._deps_ok

    def _by_pair(self) -> dict[tuple[str, str], Installed]:
        return {p.pair: p for p in self.packages()}

    def route(self, source: str, target: str) -> list[Installed]:
        """The packages that take ``source`` to ``target``: one, or two through English; [] when none."""
        s, t = norm_code(source), norm_code(target)
        pairs = self._by_pair()
        if (s, t) in pairs:
            return [pairs[(s, t)]]
        if s != PIVOT and t != PIVOT and (s, PIVOT) in pairs and (PIVOT, t) in pairs:
            return [pairs[(s, PIVOT)], pairs[(PIVOT, t)]]
        return []

    def sources_for(self, target: str) -> list[str]:
        t = norm_code(target)
        out = {s for (s, tt) in self._by_pair() if tt == t}
        if (PIVOT, t) in self._by_pair():
            out |= {s for (s, tt) in self._by_pair() if tt == PIVOT}
        return sorted(out)

    def ready(self, source: str, target: str) -> bool:
        if not self.deps_ok():
            return False
        if source:
            return bool(self.route(source, target))
        return bool(self.sources_for(target))

    # -- translation
    def _model(self, pkg: Installed) -> Any:
        key = str(pkg.path)
        with self._lock:
            m = self._models.get(key)
            if m is not None:
                return m
            if self._loader is not None:
                m = self._loader(pkg)
            else:
                from dualyrics.live import deps

                m = _Model(pkg, deps.import_module("ctranslate2"), deps.import_module("sentencepiece"))
            self._models[key] = m
            while len(self._models) > MAX_LOADED:
                oldest = min(self._models, key=lambda k: getattr(self._models[k], "used", 0.0))
                del self._models[oldest]
            return m

    def pick_source(self, lines: list[str], target: str, source: str = "") -> str:
        """The language the lines are in: the one given, the writing system's guess, English when the words
        look English, else the only other language that has a package to the target; '' when unknown (Latin or
        Cyrillic text in a language the script cannot tell, e.g. Spanish: never sent through the en model)."""
        if source:
            return source
        text = "\n".join(lines)
        guess = langs.guess_language(text)
        if guess:
            return guess
        if looks_english(text):
            return PIVOT
        others = [s for s in self.sources_for(target) if s != PIVOT]
        return others[0] if len(others) == 1 else ""

    def translate(self, lines: list[str], target: str, source: str = "") -> tuple[list[str], str]:
        """(translated lines, source language used). Raises RuntimeError without a route."""
        src = self.pick_source(lines, target, source)
        if not src:
            raise RuntimeError("the language of the text is not known (no offline language detection)")
        if langs.same_language(src, target):
            return list(lines), src
        route = self.route(src, target)
        if not route:
            raise RuntimeError(f"no offline package for {src} -> {target}")
        idx = [i for i, ln in enumerate(lines) if ln.strip()]
        texts = [lines[i] for i in idx]
        for pkg in route:
            texts = self._model(pkg).translate(texts)
        out = [""] * len(lines)
        for i, t in zip(idx, texts):
            out[i] = t
        return out, src
