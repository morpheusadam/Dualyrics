"""Speech-to-speech dubbing with the Gemini Live API: the captured audio (what the speakers play) streams to
Gemini, which speaks the translation back as it goes, the lowest latency of all engines.

One WebSocket (``BidiGenerateContent``) per run: a setup message (model, voice, an interpreter instruction,
transcription of its own speech), then 16 kHz PCM chunks of the captured audio, and audio chunks back
(24 kHz PCM), which are played as they arrive. Usage reports (``usageMetadata``) and the seconds of audio
sent and received go to the usage meter (kind ``live``). The server ends a session after a while
(``goAway``); the session reconnects. A rate limit or quota error stops it and the dubber falls back to
speaking the Live captions with a text-to-speech engine.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time
from typing import Any, Callable

from dualyrics import langs, net

log = logging.getLogger(__name__)

URL = ("wss://generativelanguage.googleapis.com/ws/"
       "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
IN_RATE = 16000
OUT_RATE = 24000
CHUNK_SEC = 0.1          # audio sent in pieces of this length
QUEUE_MAX = 50           # 5 s of audio waiting to be sent; older pieces are dropped (stay live)
USAGE_EVERY = 30.0       # seconds between two usage rows of a running session
MIN_SESSION = 20.0       # a session the server closes sooner (without goAway) counts as failed


class LiveError(Exception):
    def __init__(self, msg: str, rate_limited: bool = False) -> None:
        super().__init__(msg)
        self.rate_limited = rate_limited


def instruction(target: str, where: str = "") -> str:
    lg = langs.BY_CODE.get(target)
    name = f"{lg.name} ({lg.native})" if lg else target
    ctx = f" The audio comes from {where}." if where else ""
    return (f"You are a simultaneous interpreter dubbing what plays on the user's computer: a game, a film or a "
            f"video.{ctx} Translate every sentence that is spoken into {name} and say only the translation, "
            f"naturally and with the same emotion, as a dubbing actor would. Never answer, comment, greet or "
            f"explain; never translate song lyrics, music or sound effects; stay silent when nobody speaks or "
            f"when the speech is already in {name}.")


def setup_message(model: str, voice: str, target: str, where: str = "") -> dict[str, Any]:
    return {"setup": {
        "model": f"models/{model}",
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
        "systemInstruction": {"parts": [{"text": instruction(target, where)}]},
        "outputAudioTranscription": {},
    }}


def audio_message(pcm16: bytes) -> str:
    return json.dumps({"realtimeInput": {"audio": {"data": base64.b64encode(pcm16).decode("ascii"),
                                                   "mimeType": f"audio/pcm;rate={IN_RATE}"}}})


def parse_server(msg: Any) -> dict[str, Any]:
    """What a server message carries: audio chunks, transcribed text, usage, turn end, setup done, goAway."""
    if isinstance(msg, (bytes, bytearray)):
        msg = msg.decode("utf-8", "replace")
    try:
        j = json.loads(msg)
    except (TypeError, ValueError):
        return {}
    out: dict[str, Any] = {"audio": [], "text": "", "usage": j.get("usageMetadata"),
                           "setup": "setupComplete" in j, "go_away": "goAway" in j, "error": j.get("error")}
    sc = j.get("serverContent") or {}
    for part in (sc.get("modelTurn") or {}).get("parts") or []:
        inline = part.get("inlineData") or {}
        if inline.get("data"):
            out["audio"].append(base64.b64decode(inline["data"]))
    tr = sc.get("outputTranscription") or {}
    out["text"] = str(tr.get("text") or "")
    out["turn_complete"] = bool(sc.get("turnComplete"))
    out["interrupted"] = bool(sc.get("interrupted"))
    return out


def _hide(text: str, key: str) -> str:
    return text.replace(key, "<key>") if key else text


def to_pcm16(frame: Any, rate: int) -> bytes:
    """A float32 mono numpy frame at ``rate`` as 16 kHz 16-bit PCM (numpy is there whenever Live mode runs)."""
    import numpy as np

    a = np.asarray(frame, dtype=np.float32)
    if rate != IN_RATE and len(a):
        n = max(1, int(len(a) * IN_RATE / rate))
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    return (np.clip(a, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class LiveDub:
    """One Gemini Live dubbing run. ``feed`` from the capture thread, everything else from any thread."""

    def __init__(self, session_factory: Callable[[], Any], gemini: Any, on_audio: Callable[[bytes, int], None],
                 on_text: Callable[[str], None] = lambda s: None) -> None:
        self.session_factory = session_factory
        self.gemini = gemini                 # dualyrics.translate.Gemini (usage meter, cooldowns, key index)
        self.on_audio = on_audio
        self.on_text = on_text
        self._q: queue.Queue[bytes] = queue.Queue()
        self._buf = bytearray()
        self._stop = threading.Event()
        self._closed = False
        self._thread: threading.Thread | None = None
        self.error: LiveError | None = None
        self.model = ""
        self.connected = False
        self.muted: Callable[[], bool] = lambda: False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, models: list[str], keys: list[str], voice: str, target: str, where: str = "") -> None:
        if self.running or self._closed:
            return              # stop() came first (the dubber switched mode while the start was queued)
        self._stop.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, args=(models, keys, voice, target, where),
                                        name="dub-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """End this run for good (its socket closes within a moment); a later start() does nothing."""
        self._closed = True
        self._stop.set()

    def feed(self, frame: Any, rate: int) -> None:
        if not self.connected or self._stop.is_set():
            return
        try:
            pcm = to_pcm16(frame, rate) if not self.muted() else bytes(2 * max(1, int(len(frame) * IN_RATE / rate)))
        except Exception:
            return
        self._buf += pcm
        need = int(IN_RATE * CHUNK_SEC) * 2
        while len(self._buf) >= need:
            chunk, self._buf = bytes(self._buf[:need]), self._buf[need:]
            if self._q.qsize() >= QUEUE_MAX:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass
            self._q.put(chunk)

    # -- worker
    def _run(self, models: list[str], keys: list[str], voice: str, target: str, where: str) -> None:
        while not self._stop.is_set():
            done, limited, last = False, False, "all Live models are cooling down"
            for model in models:
                for key in keys:
                    if self._stop.is_set():
                        return
                    if self.gemini.cooling(model, key):
                        limited = True
                        continue
                    try:
                        self._session(model, key, voice, target, where)
                        done = True              # ended by the server (goAway) or by stop(): reconnect
                        break
                    except LiveError as e:
                        last, limited = f"{model}: {_hide(str(e), key)}", limited or e.rate_limited
                        self.gemini._cooldown(model, key, 3600 if e.rate_limited else 120)
                        log.info("gemini live %s", last)
                    except Exception as e:  # noqa: BLE001 - the key is in the URL: never log it
                        last = f"{model}: {type(e).__name__}: {_hide(str(e), key)}"
                        self.gemini._cooldown(model, key, 60)
                        log.info("gemini live failed: %s", last)
                if done:
                    break
            if self._stop.is_set():
                return
            if not done:
                self.error = LiveError(last[:300], rate_limited=limited)
                return
            self._stop.wait(0.5)

    def _session(self, model: str, key: str, voice: str, target: str, where: str) -> None:
        import websocket

        self.model = model
        opts = net.ws_options(self.session_factory())
        try:
            ws = websocket.create_connection(f"{URL}?key={key}", timeout=15, **opts)
        except websocket.WebSocketBadStatusException as e:
            code = getattr(e, "status_code", 0)
            raise LiveError(f"HTTP {code}", rate_limited=code == 429) from e
        from dualyrics.usage import parse_usage

        sent = received = 0.0
        usage = (0, 0, 0)                     # tokens reported since the last usage row
        last_usage_at = time.monotonic()
        try:
            ws.send(json.dumps(setup_message(model, voice, target, where)))
            first = parse_server(ws.recv())
            if first.get("error") or not first.get("setup"):
                raise LiveError(str(first.get("error") or "no setupComplete")[:200])
            self.connected = True
            started = time.monotonic()
            ws.settimeout(0.05)
            log.info("gemini live dubbing with %s", model)
            while not self._stop.is_set():
                try:
                    while True:
                        chunk = self._q.get_nowait()
                        ws.send(audio_message(chunk))
                        sent += len(chunk) / 2 / IN_RATE
                except queue.Empty:
                    pass
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    raw = None
                except websocket.WebSocketConnectionClosedException as e:
                    reason = str(getattr(ws, "close_reason", "") or e)
                    if "quota" in reason.lower() or "exhausted" in reason.lower() or "429" in reason:
                        raise LiveError(reason[:200], rate_limited=True) from e
                    if time.monotonic() - started < MIN_SESSION:
                        # closed right after the setup without goAway (model refused, bad argument): an error,
                        # so the next key / model is tried and the dubber falls back, instead of reconnecting
                        # twice a second forever
                        raise LiveError(f"closed by the server: {reason}"[:200]) from e
                    return
                if raw:
                    m = parse_server(raw)
                    if m.get("error"):
                        err = str(m["error"])
                        raise LiveError(err[:200], rate_limited="429" in err or "quota" in err.lower())
                    for pcm in m.get("audio") or []:
                        received += len(pcm) / 2 / OUT_RATE
                        self.on_audio(pcm, OUT_RATE)
                    if m.get("text"):
                        self.on_text(m["text"])
                    if m.get("usage"):
                        usage = tuple(a + b for a, b in zip(usage, parse_usage(m["usage"])))  # type: ignore[assignment]
                    if m.get("go_away"):
                        return
                if time.monotonic() - last_usage_at > USAGE_EVERY and (sent or received):
                    self.gemini.record(model, key, "live", usage, True, 200, sent + received)
                    sent = received = 0.0
                    usage = (0, 0, 0)
                    last_usage_at = time.monotonic()
        finally:
            self.connected = False
            if sent or received or any(usage):
                self.gemini.record(model, key, "live", usage, True, 200, sent + received)
            try:
                ws.close()
            except Exception:
                pass
