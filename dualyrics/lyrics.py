"""Lyrics lookup on LRCLIB (free, no key) with LRC parsing and title/artist cleaning."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from dualyrics import net

log = logging.getLogger(__name__)

API = "https://lrclib.net/api"


@dataclass
class LyricLine:
    time: float   # seconds from song start
    text: str


@dataclass
class LyricsResult:
    synced: list[LyricLine] = field(default_factory=list)
    plain: list[str] = field(default_factory=list)
    instrumental: bool = False
    found: bool = False
    network_error: bool = False
    # what LRCLIB matched (track, artist, duration): checked by the strict match for browsers
    match_title: str = ""
    match_artist: str = ""
    match_duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"synced": [[ln.time, ln.text] for ln in self.synced], "plain": self.plain,
                             "instrumental": self.instrumental, "found": self.found}
        if self.match_title or self.match_artist:
            d["match"] = [self.match_title, self.match_artist, self.match_duration]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LyricsResult":
        m = d.get("match")
        mt, ma, md = ("", "", 0.0)
        if isinstance(m, list) and len(m) == 3:
            try:
                mt, ma, md = str(m[0] or ""), str(m[1] or ""), float(m[2] or 0)
            except (TypeError, ValueError):
                mt, ma, md = ("", "", 0.0)
        return cls(synced=[LyricLine(float(t), str(x)) for t, x in d.get("synced", [])],
                   plain=[str(x) for x in d.get("plain", [])],
                   instrumental=bool(d.get("instrumental")), found=bool(d.get("found")),
                   match_title=mt, match_artist=ma, match_duration=md)

    @property
    def has_match_info(self) -> bool:
        return bool(self.match_title)


# ---------------------------------------------------------------- LRC parsing
_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_ANY_TAG = re.compile(r"\[[^\]]*\]")
_WORD_TS = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.I)


def parse_lrc(text: str) -> list[LyricLine]:
    """Parse LRC text into time-sorted lines.

    Handles ``[mm:ss.xx]``, ``[mm:ss:xx]``, ``[mm:ss]``, several timestamps on one line,
    the ``[offset:±ms]`` tag, metadata tags (``[ar:...]``) and word-level ``<mm:ss.xx>`` tags.
    """
    if not text:
        return []
    offset = 0.0
    m = _OFFSET.search(text)
    if m:
        offset = int(m.group(1)) / 1000.0  # positive offset = lyrics appear sooner
    out: list[tuple[float, int, str]] = []
    for n, row in enumerate(text.splitlines()):
        stamps = _TS.findall(row)
        if not stamps:
            continue
        words = _WORD_TS.sub("", _ANY_TAG.sub("", row)).strip()
        words = re.sub(r"\s{2,}", " ", words)
        for mm, ss, frac in stamps:
            t = int(mm) * 60 + int(ss) + (int(frac) / (10 ** len(frac)) if frac else 0.0)
            out.append((max(0.0, t - offset), n, words))
    out.sort(key=lambda x: (x[0], x[1]))
    return [LyricLine(round(t, 3), w) for t, _, w in out]


# ---------------------------------------------------------------- title / artist cleaning
_JUNK_WORDS = (r"official|video|audio|lyrics?|visuali[sz]er|remaster(?:ed)?|live|version|edit|mix|"
               r"feat\.?|ft\.?|featuring|with|prod\.?|explicit|clean|hd|hq|4k|8k|m/?v|color coded|"
               r"from|ost|soundtrack|mono|stereo|deluxe|bonus|demo|acoustic|karaoke|sped up|slowed|reverb|"
               r"extended|radio|single|anniversary|music")
_BRACKETS = re.compile(r"\s*[\(\[【「（][^\)\]】」）]*?\b(?:" + _JUNK_WORDS + r")\b[^\)\]】」）]*[\)\]】」）]", re.I)
_DASH_SUFFIX = re.compile(r"\s+[-–—]\s+[^-–—]*\b(?:remaster(?:ed)?|live|version|edit|mix|mono|stereo|demo|"
                          r"acoustic|single|radio|from|recorded|session|bonus|anniversary)\b.*$", re.I)
_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)
_PIPE = re.compile(r"\s*[|｜•].*$")
_OFFICIAL_TAIL = re.compile(r"\s*\b(?:official\s+)?(?:music\s+|lyric\s+)?(?:video|audio|visualizer)\s*$", re.I)
_DASHES = re.compile(r"\s+[-–—~]\s+")


def clean_title(title: str) -> str:
    """Remove 'Remastered', '(Live at…)', 'feat. X', '(Official Video)', '| channel' and similar noise."""
    t = title or ""
    for _ in range(3):  # nested / repeated decorations
        t2 = _BRACKETS.sub("", t)
        if t2 == t:
            break
        t = t2
    t = _PIPE.sub("", t)
    t = _DASH_SUFFIX.sub("", t)
    t = _FEAT.sub("", t)
    t = _OFFICIAL_TAIL.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—\"'“”")
    return t or (title or "").strip()


def clean_artist(artist: str) -> str:
    """Primary artist: drop ' - Topic', 'VEVO', featured artists and co-artists after a comma."""
    a = (artist or "").strip()
    a = re.sub(r"\s*-\s*Topic$", "", a, flags=re.I)
    a = re.sub(r"(?<=[a-z0-9])VEVO$", "", a)
    a = re.sub(r"\s*\b(?:VEVO|Official(?: Channel)?)$", "", a, flags=re.I)
    a = re.split(r"\s*(?:,|;|/|\bfeat\.?|\bft\.?|\bfeaturing\b| x | X | & )\s*", a)[0]
    return a.strip()


def split_artist_title(title: str) -> tuple[str, str] | None:
    """'Artist - Song (Official Video)' -> ('Artist', 'Song'); used for YouTube where artist = channel."""
    parts = _DASHES.split(title or "", maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return clean_artist(parts[0]), clean_title(parts[1])
    return None


def candidates(title: str, artist: str) -> list[tuple[str, str]]:
    """Ordered, de-duplicated (track, artist) pairs to try after the exact lookup failed."""
    out: list[tuple[str, str]] = []

    def add(t: str, a: str) -> None:
        t, a = t.strip(), a.strip()
        if t and (t.lower(), a.lower()) not in {(x.lower(), y.lower()) for x, y in out}:
            out.append((t, a))

    ct, ca = clean_title(title), clean_artist(artist)
    split = split_artist_title(title)
    channel_like = bool(re.search(r"vevo|topic|official|records|music|tv|channel|nation|vibes", artist or "", re.I))
    if split and channel_like:
        add(split[1], split[0])
    add(ct, ca)
    if ca != (artist or "").strip():
        add(ct, (artist or "").strip())
    if split:
        add(split[1], split[0])
    return out


# ---------------------------------------------------------------- LRCLIB client
class LyricsClient:
    def __init__(self, session: requests.Session) -> None:
        self.session = session

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        r = self.session.get(f"{API}/{path}", params={k: v for k, v in params.items() if v not in (None, "")},
                             timeout=net.TIMEOUT_FAST)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _to_result(j: dict[str, Any]) -> LyricsResult:
        res = LyricsResult(found=True, instrumental=bool(j.get("instrumental")),
                           match_title=str(j.get("trackName") or j.get("name") or ""),
                           match_artist=str(j.get("artistName") or ""))
        try:
            res.match_duration = float(j.get("duration") or 0)
        except (TypeError, ValueError):
            res.match_duration = 0.0
        if j.get("syncedLyrics"):
            res.synced = parse_lrc(j["syncedLyrics"])
        if j.get("plainLyrics"):
            res.plain = [ln.rstrip() for ln in j["plainLyrics"].splitlines()]
        if not res.synced and not res.plain and not res.instrumental:
            res.found = False
        return res

    @staticmethod
    def _score(j: dict[str, Any], duration: float) -> float:
        sc = 0.0
        if j.get("syncedLyrics"):
            sc += 4
        elif j.get("plainLyrics"):
            sc += 1
        if j.get("instrumental"):
            sc -= 3
        if duration and j.get("duration"):
            diff = abs(float(j["duration"]) - duration)
            sc += 3 if diff <= 2 else 1 if diff <= 6 else -4 if diff > 20 else 0
        return sc

    def fetch(self, title: str, artist: str, album: str, duration: float,
              accept: Callable[[dict[str, Any]], bool] | None = None) -> LyricsResult:
        """get(exact) -> get(cleaned) -> search(cleaned) -> search(free text). Prefers synced lyrics.

        ``accept`` (strict match for browsers) rejects LRCLIB answers that are not this exact track; a
        search then looks at every result instead of only the best scored one."""
        dur = round(duration) if duration and duration > 0 else None
        plain_fallback: LyricsResult | None = None
        net_errors = 0
        attempts = 0

        def consider(j: Any) -> LyricsResult | None:
            nonlocal plain_fallback
            if not isinstance(j, dict):
                return None
            if accept is not None and not accept(j):
                return None
            res = self._to_result(j)
            if res.synced:
                return res
            if res.found and plain_fallback is None:
                plain_fallback = res
            return None

        gets = [(title, artist, album, dur)] + [(t, a, None, dur) for t, a in candidates(title, artist)]
        for t, a, al, d in gets:
            attempts += 1
            try:
                hit = consider(self._get("get", {"track_name": t, "artist_name": a, "album_name": al, "duration": d}))
                if hit:
                    return hit
            except Exception as e:
                net_errors += net.is_network_error(e)
                log.info("lrclib get failed (%s / %s): %s", t, a, e)
                if net.is_network_error(e) and attempts == 1:
                    return LyricsResult(network_error=True)

        searches: list[dict[str, Any]] = [{"track_name": t, "artist_name": a} for t, a in candidates(title, artist)[:2]]
        searches.append({"q": f"{clean_artist(artist)} {clean_title(title)}"})
        for params in searches:
            try:
                arr = self._get("search", params) or []
            except Exception as e:
                net_errors += net.is_network_error(e)
                log.info("lrclib search failed (%s): %s", params, e)
                continue
            if not isinstance(arr, list) or not arr:
                continue
            if accept is not None:
                ok = [j for j in arr[:20] if isinstance(j, dict) and accept(j)]
                if not ok:
                    continue
                hit = consider(max(ok, key=lambda j: self._score(j, duration or 0)))
                if hit:
                    return hit
                continue
            best = max(arr[:20], key=lambda j: self._score(j, duration or 0))
            if duration and best.get("duration") and abs(float(best["duration"]) - duration) > 25:
                continue  # probably a different song
            hit = consider(best)
            if hit:
                return hit
        if plain_fallback:
            return plain_fallback
        # any failed request may have hidden the right match: retry later instead of caching "not found"
        return LyricsResult(network_error=net_errors > 0)
