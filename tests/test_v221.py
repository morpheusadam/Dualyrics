"""v2.2.1: fixes from the reverse-engineering review and the bug reports. Fakes only: no network, no sound."""

import hashlib
import json
import math
import threading
import time
import types

import pytest
import requests

from dualyrics import automode, backdrop, keypool, retry, settings as S, tksafe
from dualyrics.keypool import KeyPool
from dualyrics.live import engine as engine_mod, models
from dualyrics.live.livetranslate import LiveTranslator
from dualyrics.live.text import Recognition
from dualyrics.screen.engine import Readers, ScreenReader, Target
from dualyrics.translate import (ContentBlocked, Gemini, KeysResting, TranslationError, Translator, is_looping,
                                 live_generation)

MINUTE = json.dumps({"error": {"code": 429, "message": "Quota exceeded", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
     "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}})


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.ok = status == 200
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


def answer(text=None, finish="STOP", feedback=None):
    cand = {"finishReason": finish}
    if text is not None:
        cand["content"] = {"parts": [{"text": text}]}
    body = {"candidates": [cand]}
    if feedback:
        body = {"promptFeedback": {"blockReason": feedback}}
    return Resp(200, body)


class Api:
    """``reply(model, key, body) -> Resp``; records (model, key, body) of every request."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def post(self, url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        key = headers["x-goog-api-key"]
        self.calls.append((model, key, json, timeout))
        return self.reply(model, key, json)


def pool(clock=None):
    p = KeyPool(None, clock=clock or Clock())
    p.reset_fn = lambda now: 3600.0
    return p


CFG = {**S.defaults(), "provider": "gemini", "gemini_keys": ["k1", "k2"], "gemini_models": ["m1", "m2"],
       "target_language": "fa"}


# ---------------------------------------------------------------- 1. content blocks never rest keys
def test_a_safety_block_raises_content_blocked_after_one_request_and_rests_nothing():
    api = Api(lambda m, k, b: answer(finish="SAFETY"))
    p = pool()
    g = Gemini(lambda: api, p)
    with pytest.raises(ContentBlocked):
        g.ask(["k1", "k2"], ["m1", "m2"], "p")
    assert len(api.calls) == 1
    assert all(p.slot(m, k)[0] == keypool.OK for m in ("m1", "m2") for k in ("k1", "k2"))
    sent = api.calls[0][2]["safetySettings"]
    assert {s["category"] for s in sent} >= {"HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                                             "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"}
    assert all(s["threshold"] == "BLOCK_NONE" for s in sent)


def test_a_prompt_level_block_also_answers_from_the_next_tier():
    api = Api(lambda m, k, b: answer(feedback="PROHIBITED_CONTENT"))
    tr = Translator(lambda: api, pool())
    tr.free = types.SimpleNamespace(translate=lambda lines, target: ([f"F:{x}" for x in lines], "en"),
                                    resting=lambda: False)
    lt = LiveTranslator(tr)
    assert lt.translate("a line Gemini refuses", CFG, []) == ("F:a line Gemini refuses", "done")
    assert len(api.calls) == 1 and tr.gemini.pool.any_available(["k1"], ["m1"])


def test_an_unparsable_answer_still_cools_its_pair_but_an_empty_one_does_not():
    api = Api(lambda m, k, b: answer("this is not json"))
    p = pool()
    g = Gemini(lambda: api, p)
    with pytest.raises(TranslationError):
        g.ask(["k1"], ["m1"], "p")
    assert p.slot("m1", "k1")[2] == "bad JSON"
    api2 = Api(lambda m, k, b: answer(""))
    p2 = pool()
    with pytest.raises(ContentBlocked):
        Gemini(lambda: api2, p2).ask(["k1"], ["m1"], "p")
    assert p2.available("m1", "k1")


# ---------------------------------------------------------------- 2. live-line generation config
def test_live_lines_switch_thinking_off_cap_the_answer_and_use_a_short_timeout():
    api = Api(lambda m, k, b: answer('{"translation": "سلام"}'))
    g = Gemini(lambda: api, pool())
    g.ask(["k1"], ["gemini-2.5-flash"], "p", live=True, timeout=(4, 6))
    gen = api.calls[0][2]["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingBudget": 0} and gen["maxOutputTokens"] == 256
    assert api.calls[0][3] == (4, 6)
    g.ask(["k1"], ["gemini-2.5-flash"], "p")                       # a whole song keeps thinking
    assert "thinkingConfig" not in api.calls[1][2]["generationConfig"]
    assert live_generation("gemini-3-flash-preview")["thinkingConfig"] == {"thinkingLevel": "low"}
    assert live_generation("gemini-flash-lite-latest")["thinkingConfig"] == {"thinkingBudget": 0}
    assert "thinkingConfig" not in live_generation("gemini-2.5-pro")


def test_a_model_that_rejects_the_thinking_field_is_asked_again_without_it_and_remembered():
    def reply(m, k, body):
        if "thinkingConfig" in body["generationConfig"]:
            return Resp(400, {"error": {"message": "Thinking budget is not supported for this model."}})
        return answer('{"translation": "ok"}')

    api = Api(reply)
    p = pool()
    g = Gemini(lambda: api, p)
    assert g.ask(["k1"], ["gemini-2.5-flash-lite"], "p", live=True) == {"translation": "ok"}
    assert len(api.calls) == 2 and p.available("gemini-2.5-flash-lite", "k1")
    g.ask(["k1"], ["gemini-2.5-flash-lite"], "p", live=True)
    assert len(api.calls) == 3                                     # remembered: one request now


def test_a_live_line_past_its_deadline_asks_no_further_pair():
    api = Api(lambda m, k, b: answer('{"translation": "x"}'))
    g = Gemini(lambda: api, pool())
    with pytest.raises(TranslationError, match="out of time"):
        g.ask(["k1"], ["m1"], "p", live=True, deadline=time.monotonic() - 1)
    assert api.calls == []


def test_an_answer_cut_at_the_token_cap_moves_to_the_next_model_without_a_cooldown():
    api = Api(lambda m, k, b: answer('{"translation": "abc', finish="MAX_TOKENS") if m == "m1"
              else answer('{"translation": "done"}'))
    p = pool()
    g = Gemini(lambda: api, p)
    assert g.ask(["k1", "k2"], ["m1", "m2"], "p", live=True) == {"translation": "done"}
    assert [m for m, *_ in api.calls] == ["m1", "m2"] and p.available("m1", "k1") and p.available("m1", "k2")


def test_looping_answers_are_recognised():
    assert is_looping("ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha ha")
    assert is_looping("I love you I love you I love you I love you I love you I love you")
    assert not is_looping("short short short")
    assert not is_looping("This is a perfectly normal sentence that is long enough to check.")


def test_a_looping_live_answer_is_replaced_by_the_next_tier():
    api = Api(lambda m, k, b: answer(json.dumps({"translation": "بله " * 30})))
    tr = Translator(lambda: api, pool())
    tr.free = types.SimpleNamespace(translate=lambda lines, target: (["F"], "en"), resting=lambda: False)
    assert LiveTranslator(tr).translate("yes", CFG, []) == ("F", "done")


# ---------------------------------------------------------------- 3. partials while Gemini is busy
class SlowGemini:
    def __init__(self):
        self.prompts, self.release = [], threading.Event()

    def ask(self, keys, models, prompt, **kw):
        self.prompts.append(prompt)
        self.release.wait(2)
        return {"translation": "G"}

    def can_ask(self, keys, models):
        return True


class Store:
    def __init__(self):
        self.puts = []

    def get(self, *a):
        return None

    def put(self, text, target, value, provider, *a):
        self.puts.append((text, provider))


def test_a_partial_takes_the_free_tier_while_a_gemini_request_is_on_its_way():
    gem = SlowGemini()
    store = Store()
    tr = types.SimpleNamespace(gemini=gem, free=types.SimpleNamespace(
        translate=lambda lines, target: ([f"F:{x}" for x in lines], "en"), resting=lambda: False), offline=None)
    lt = LiveTranslator(tr, store)
    th = threading.Thread(target=lambda: lt.translate("the final sentence.", CFG, []))
    th.start()
    for _ in range(100):
        if lt.busy():
            break
        time.sleep(0.01)
    assert lt.translate("a sentence that is still", CFG, [], partial=True) == ("F:a sentence that is still", "done")
    gem.release.set()
    th.join(3)
    assert len(gem.prompts) == 1                                   # the partial cost no Gemini request
    assert ("a sentence that is still", "free") not in store.puts  # partials stay out of the disk cache
    assert lt.translate("another one", CFG, [], partial=True)[0] == "G"   # Gemini idle: it answers partials


# ---------------------------------------------------------------- 4. retries while the line is shown
def test_backoff_schedule():
    b = retry.Backoff()
    b.failed(100.0)
    assert b.due == 101.0 and not b.ready(100.5) and b.ready(101.0)
    b.taken()
    b.failed(101.2)
    assert b.due == pytest.approx(103.2)
    b.failed(103.5)
    assert b.due == 0.0                                            # three attempts: given up
    lim = retry.Backoff()
    lim.failed(0.0, limited=True)
    assert lim.due == 4.0
    lim.failed(4.0, limited=True)
    assert lim.due == 12.0
    late = retry.Backoff(attempts=1, first_at=0.0)
    late.failed(44.0)
    assert late.due == 0.0                                          # past the 45 s window
    assert retry.is_rate_limit(KeysResting("x")) and not retry.is_rate_limit(TranslationError("network"))


class FlakyTr:
    def __init__(self, fails):
        self.fails, self.calls = fails, 0

    def to_english(self, text, cfg):
        return text, "en"

    def translate(self, text, cfg, ctx, source="", where="", title=""):
        self.calls += 1
        if self.calls <= self.fails:
            raise TranslationError("network error: proxy dropped")
        return f"[fa] {text}", "done"


def test_the_screen_source_asks_again_for_a_failed_line_while_it_is_shown():
    cfg = {**S.defaults(), "target_language": "fa", "screen_ocr": False}
    tr = FlakyTr(fails=2)
    r = ScreenReader(lambda: cfg, tr, Readers(uia=lambda: None), translate_async=False)
    r._target, r._want = Target(hwnd=1, key="vlc", kind="video"), True
    r.read_once = lambda target, c, now: ("Where are you going?", True)
    for i in range(40):
        r.step(i * 0.25)
    assert tr.calls == 3 and r._cap.trans_state == "done" and r._cap.translation == "[fa] Where are you going?"


def test_live_mode_asks_again_for_the_newest_failed_caption():
    cfg = {**S.defaults(), "show_translation": True, "live_save_transcripts": False, "target_language": "fa"}
    eng = engine_mod.LiveEngine(lambda: cfg, lambda: None, types.SimpleNamespace(gemini=None, free=None))
    tr = FlakyTr(fails=1)
    eng.live_tr = types.SimpleNamespace(translate=lambda text, c, ctx, **kw: tr.translate(text, c, ctx))
    eng._tx.shutdown()
    eng._tx = types.SimpleNamespace(submit=lambda fn: (fn(), types.SimpleNamespace(cancel=lambda: False,
                                                                                    done=lambda: True))[1])
    now = time.time()
    eng._emit(Recognition("Where are we going?", now - 1, now, language="en"), cfg)
    cap = eng.stream.last
    assert cap.trans_state == "failed" and cap.retry.due
    eng._retry_failed(cap.retry.due + 0.01)
    assert cap.trans_state == "done" and cap.translation == "[fa] Where are we going?" and tr.calls == 2


# ---------------------------------------------------------------- 5. long captions keep their end
def test_a_long_live_caption_keeps_its_newest_words():
    from dualyrics import render

    r = render.Renderer()
    key = r.font_key({"font": {"latin": ["auto"], "rtl": ["auto"], "size": 20, "weight": 400, "italic": False}},
                     False, 1.0)
    words = " ".join(f"word{i}," if i == 12 else f"word{i}" for i in range(40))
    head = r._wrap(words, key, False, 220, 2, 0)
    tail = r._wrap(words, key, False, 220, 2, 0, keep="tail")
    assert len(head) == 2 and head[-1].endswith("…") and head[0].startswith("word0")
    assert len(tail) == 2 and tail[0].startswith("…") and tail[-1].endswith("word39")


# ---------------------------------------------------------------- 6. requests-per-minute pacing
def test_a_pair_that_hit_a_minute_limit_is_paced_afterwards():
    clock = Clock()
    p = pool(clock)
    for _ in range(9):
        p.note_sent("gemini-2.5-flash", "k1")
    assert p.available("gemini-2.5-flash", "k1")                  # no 429 yet: never slowed down (paid keys)
    p.mark_limited("gemini-2.5-flash", "k1", "Quota exceeded", MINUTE)
    clock.t += 8                                                    # the retry delay is over ...
    p.note_sent("gemini-2.5-flash", "k1")
    st, until, why = p.slot("gemini-2.5-flash", "k1")
    assert st == keypool.LIMITED and why == "requests per minute"  # ... but its minute is full (10 of 10)
    clock.t = until
    assert p.available("gemini-2.5-flash", "k1")
    assert p.order(["k1", "k2"], ["gemini-2.5-flash"])[0][0] == "gemini-2.5-flash"


# ---------------------------------------------------------------- bug 1: Liquid Glass corners
@pytest.mark.parametrize("radius,dpi", [(26, 1.0), (32.5, 1.25), (52, 2.0), (9, 1.0), (40, 1.5)])
def test_the_blur_windows_dwm_corner_lies_inside_the_box_corner(radius, dpi):
    pref, r = backdrop.corner_preference(radius, dpi)
    d = backdrop.blur_inset(radius, r)
    assert pref in (backdrop.DWMWCP_ROUND, backdrop.DWMWCP_ROUNDSMALL)
    # the blur corner circle (centre d + r) must be inside the box corner circle (centre R)
    gap = radius - (d + r)
    assert gap <= 0 or math.sqrt(2) * gap + r <= radius + 1e-9
    assert d <= max(1, radius * 0.3)                                # never a wide unblurred band
    assert backdrop.corner_preference(0, 1.0)[0] == backdrop.DWMWCP_DONOTROUND


# ---------------------------------------------------------------- bug 2: Settings froze
def test_tkinter_finalizers_from_other_threads_wait_for_the_tk_thread():
    ran = []

    class Fake:
        def __init__(self, name):
            self.name = name

        def __del__(self):
            ran.append((self.name, threading.get_ident()))

    tksafe._tk_thread = threading.get_ident()
    tksafe._wrap(Fake)
    me = threading.get_ident()
    Fake("here")
    assert ran == [("here", me)]

    def work():
        Fake("there")                     # collected on this thread: deferred, nothing runs here

    th = threading.Thread(target=work)
    th.start()
    th.join(2)
    assert not th.is_alive() and len(ran) == 1 and tksafe.pending() >= 1
    assert tksafe.drain() >= 1 and ran[-1] == ("there", me)


# ---------------------------------------------------------------- bug 3: flaky downloads, automode
class DropHub:
    """A Hub whose file downloads break off part-way (a flaky proxy) a few times, then work."""

    def __init__(self, files, drops):
        self.files, self.drops, self.ranges = files, list(drops), []

    def get(self, url, headers=None, stream=False, timeout=None, allow_redirects=True):
        if "/api/models/" in url:
            tree = [{"type": "file", "path": n, "size": len(d), "lfs": {"oid": hashlib.sha256(d).hexdigest(),
                                                                        "size": len(d)}}
                    for n, d in self.files.items()]
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: tree, status_code=200)
        name = url.rsplit("/", 1)[1]
        rng = (headers or {}).get("Range", "")
        self.ranges.append((name, rng))
        start = int(rng[6:-1]) if rng else 0
        data = self.files[name][start:]
        drop = self.drops.pop(0) if self.drops and name == "model.bin" else None
        hub = self

        class R:
            status_code = 206 if start else 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            def iter_content(self, n):
                for i in range(0, len(data), 8):
                    if drop is not None and i >= drop:
                        raise requests.exceptions.ChunkedEncodingError("proxy dropped the connection")
                    yield data[i:i + 8]

        del hub
        return R()


def test_a_model_download_resumes_by_itself_when_the_proxy_drops_it(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    monkeypatch.setattr(models, "_sleep", lambda s: None)
    monkeypatch.setattr(models, "RETRY_PAUSE", 0.0)
    files = {"config.json": b'{"a": 1}', "model.bin": bytes(range(256)) * 3, "tokenizer.json": b"{}",
             "vocabulary.txt": b"a\nb\n"}
    hub = DropHub(files, drops=(40, 200))
    models.download("tiny", hub)
    assert models.is_installed("tiny")
    assert (tmp_path / "faster-whisper-tiny" / "model.bin").read_bytes() == files["model.bin"]
    tries = [r for n, r in hub.ranges if n == "model.bin"]
    assert len(tries) == 3 and tries[0] == "" and tries[1].startswith("bytes=") and tries[2].startswith("bytes=")


def test_a_browser_video_is_decided_again_as_soon_as_the_profile_changes():
    sw = automode.AutoSwitch()
    music = automode.Inputs(media_playing=True, lyrics=automode.LYR_SEARCHING, live_ready=True)
    assert sw.update(automode.decide(music), 0.0).mode == automode.LYRICS
    video = automode.Inputs(media_playing=True, lyrics=automode.LYR_SEARCHING, live_ready=True,
                            browser_video=True)
    assert automode.decide(video).mode == automode.LIVE
    assert sw.update(automode.decide(video), 1.0).mode == automode.LYRICS        # hysteresis ...
    assert sw.update(automode.decide(video), 2.6).mode == automode.LIVE          # ... then Live


# ---------------------------------------------------------------- lyrics search and Whisper noise
def test_a_cover_of_the_same_length_loses_to_the_original():
    from dualyrics.lyrics import LyricsClient

    wanted = [("Shape of You (Official Video)", "Ed Sheeran")]
    orig = {"trackName": "Shape of You", "artistName": "Ed Sheeran", "duration": 234, "syncedLyrics": "x"}
    cover = {"trackName": "Shape of You", "artistName": "Karaoke Kings", "duration": 234, "syncedLyrics": "x"}
    other = {"trackName": "Perfect", "artistName": "Ed Sheeran", "duration": 234, "syncedLyrics": "x"}
    s = [LyricsClient._score(j, 234, wanted) for j in (orig, cover, other)]
    assert s[0] > s[1] and s[0] > s[2]
    duo = {"trackName": "Shape of You", "artistName": "Sheeran Ed", "duration": 234, "syncedLyrics": "x"}
    assert LyricsClient._score(duo, 234, wanted) == s[0]          # artists compared as sets of words


def test_seconds_of_sound_with_one_word_are_noise_but_not_from_live_captions():
    from dualyrics.live.text import hallucination_reason

    assert hallucination_reason(Recognition("Oi.", 0.0, 2.5)) == "noise"
    assert hallucination_reason(Recognition("Oi.", 0.0, 2.5, captions=True)) is None
    assert hallucination_reason(Recognition("Go!", 0.0, 0.8)) is None
    assert hallucination_reason(Recognition("你好", 0.0, 2.5)) is None
