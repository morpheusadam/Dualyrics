"""Microsoft Edge "Read aloud" neural voices: free and online, no key, good Persian voices.

Spoken through the open-source ``edge-tts`` package (LGPL-3.0), which follows the service's protocol as it
changes. It is not bundled: Settings > Dubbing installs it on demand (``dualyrics.live.deps``), like Live
mode's packages, so the app keeps its own licence and the package can be updated on its own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dualyrics.live import deps

log = logging.getLogger(__name__)

PACKAGE = "edge_tts"
REQUIREMENT = "edge-tts>=7.3"
BITRATE = 48000          # the package's output: audio-24khz-48kbitrate-mono-mp3
TIMEOUT = 25.0


class EdgeError(Exception):
    pass


def available() -> bool:
    return deps.has(PACKAGE)


def rate_text(rate: float) -> str:
    """Speaking rate as the service wants it: 1.25 -> '+25%'."""
    pct = int(round((max(0.5, min(2.0, rate)) - 1.0) * 100))
    return f"{pct:+d}%"


def mp3_seconds(data: bytes) -> float:
    return len(data) * 8 / BITRATE


def http_proxy(url: str) -> str | None:
    """The package (aiohttp) takes HTTP proxies only; a SOCKS proxy is not passed (then it goes direct)."""
    return url if url.lower().startswith(("http://", "https://")) else None


def synthesize(text: str, voice: str, rate: float = 1.0, proxy: str = "", timeout: float = TIMEOUT) -> bytes:
    """MP3 audio (24 kHz mono) of ``text``. Raises EdgeError (also when the package is missing)."""
    try:
        edge_tts = deps.import_module(PACKAGE)
    except Exception as e:  # noqa: BLE001
        raise EdgeError(f"edge-tts is not installed ({e})") from e

    async def run() -> bytes:
        comm = edge_tts.Communicate(text, voice, rate=rate_text(rate), proxy=http_proxy(proxy) if proxy else None)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                buf += chunk.get("data") or b""
        return bytes(buf)

    try:
        audio = asyncio.run(asyncio.wait_for(run(), timeout))
    except Exception as e:  # noqa: BLE001 - network, service and package errors alike
        raise EdgeError(f"{type(e).__name__}: {e}"[:300]) from e
    if not audio:
        raise EdgeError("no audio (voice not available?)")
    return audio


def list_voices(proxy: str = "") -> list[dict[str, Any]]:
    """Every Edge voice: [{"name": "fa-IR-DilaraNeural", "locale": "fa-IR", "gender": "Female"}, ...]."""
    edge_tts = deps.import_module(PACKAGE)

    async def run() -> Any:
        return await edge_tts.list_voices(proxy=http_proxy(proxy) if proxy else None)

    out = []
    for v in asyncio.run(asyncio.wait_for(run(), TIMEOUT)) or []:
        if isinstance(v, dict) and v.get("ShortName"):
            out.append({"name": v["ShortName"], "locale": v.get("Locale", ""), "gender": v.get("Gender", "")})
    return out
