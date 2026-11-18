"""v2.2 translation that never stops: the Gemini key pool (round robin, per-key state, daily quota until the reset,
shared projects), the offline last tier, the line cache on disk and the OCR/UIA dedupe (Translumo's rules).
Everything runs with fakes: no network, no window, no sound."""

import io
import json
import types
import zipfile

import pytest

from dualyrics import keypool, linecache, offline, settings as S
from dualyrics.keypool import KeyPool, fingerprint
from dualyrics.live.livetranslate import LiveTranslator
from dualyrics.screen import dedupe
from dualyrics.translate import Gemini, KeysResting, TranslationError, Translator

DAILY = json.dumps({"error": {"code": 429, "message": "Quota exceeded", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
     "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
MINUTE = json.dumps({"error": {"code": 429, "message": "Quota exceeded", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
     "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}})


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_pool(clock=None, path=None):
    pool = KeyPool(path, clock=clock or Clock())
    pool.reset_fn = lambda now: 3600.0          # the daily reset is an hour away in these tests
    return pool


# ---------------------------------------------------------------- a fake generateContent endpoint
class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status == 200
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class FakeApi:
    """Answers per key: 'ok', 'daily', 'minute' or 'bad'; records (model, key) of every request."""

    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def post(self, url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        key = headers["x-goog-api-key"]
        self.calls.append((model, key))
        what = self.plan.get((model, key), self.plan.get(key, "ok"))
        if what == "daily":
            return Resp(429, DAILY)
        if what == "minute":
            return Resp(429, MINUTE)
        if what == "bad":
            return Resp(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        return Resp(200, {"candidates": [{"content": {"parts": [{"text": '{"translation": "سلام"}'}]}}]})


def gemini_with(api, pool=None):
    g = Gemini(lambda: api, pool or make_pool())
    return g


# ---------------------------------------------------------------- round robin
def test_requests_take_turns_over_the_keys_instead_of_draining_key_1():
    api = FakeApi({})
    g = gemini_with(api)
    for _ in range(6):
        g.ask(["k1", "k2", "k3"], ["m1", "m2"], "p")
    assert [k for _m, k in api.calls] == ["k1", "k2", "k3", "k1", "k2", "k3"]
    assert {m for m, _k in api.calls} == {"m1"}          # the first model as long as it answers


def test_order_keeps_the_model_order_and_rotates_the_keys():
    pool = make_pool()
    assert pool.order(["a", "b"], ["m1", "m2"]) == [("m1", "a"), ("m1", "b"), ("m2", "a"), ("m2", "b")]
    assert pool.order(["a", "b"], ["m1", "m2"]) == [("m1", "b"), ("m1", "a"), ("m2", "b"), ("m2", "a")]


def test_switched_off_keys_are_never_asked():
    api = FakeApi({})
    pool = make_pool()
    pool.disabled_fn = lambda: [fingerprint("k2")]
    g = gemini_with(api, pool)
    for _ in range(4):
        g.ask(["k1", "k2", "k3"], ["m"], "p")
    assert "k2" not in [k for _m, k in api.calls]
    assert pool.summary(["k1", "k2"], ["m"])[1]["state"] == keypool.OFF


# ---------------------------------------------------------------- per-key state, exhaustion and the reset
def test_daily_quota_skips_the_key_until_the_reset_and_survives_a_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "state.json"
    api = FakeApi({"k1": "daily"})
    pool = make_pool(clock, path)
    g = gemini_with(api, pool)
    assert g.ask(["k1", "k2"], ["m"], "p") == {"translation": "سلام"}      # k1 429 (day) -> k2
    assert api.calls == [("m", "k1"), ("m", "k2")]
    st, until, _why = pool.slot("m", "k1")
    assert st == keypool.EXHAUSTED and until == clock.t + 3600
    api.calls.clear()
    for _ in range(3):
        g.ask(["k1", "k2"], ["m"], "p")
    assert [k for _m, k in api.calls] == ["k2", "k2", "k2"]                # skipped, no request
    again = make_pool(clock, path)                                         # restart: still exhausted
    assert again.slot("m", "k1")[0] == keypool.EXHAUSTED
    clock.t += 3601                                                        # after the reset
    assert again.available("m", "k1") and pool.available("m", "k1")


def test_a_per_minute_limit_rests_for_the_retry_delay_only():
    clock = Clock()
    pool = make_pool(clock)
    wait = pool.mark_limited("m", "k", "Quota exceeded", MINUTE)
    assert wait == 8.0 and pool.slot("m", "k")[0] == keypool.LIMITED
    clock.t += 9
    assert pool.available("m", "k")


def test_a_rejected_key_is_marked_invalid_and_a_test_that_works_clears_it():
    api = FakeApi({"k1": "bad"})
    tr = Translator(lambda: api, make_pool())
    tr.gemini.ask(["k1", "k2"], ["m"], "p")
    pool = tr.gemini.pool
    assert pool.slot("m", "k1")[0] == keypool.INVALID
    api.plan = {}
    assert tr.test_key("k1", ["m"]) == [("m", True, "OK")]
    assert pool.available("m", "k1")


def test_when_every_key_is_exhausted_the_next_tier_is_used_without_requests():
    api = FakeApi({"k1": "daily", "k2": "daily"})
    tr = Translator(lambda: api, make_pool())
    notices = []
    tr.gemini.on_notice = lambda what, kw: notices.append(what)
    with pytest.raises(KeysResting):
        tr.gemini.ask(["k1", "k2"], ["m"], "p")
    assert len(api.calls) == 2 and "keys_resting" in notices
    api.calls.clear()
    with pytest.raises(KeysResting):
        tr.gemini.ask(["k1", "k2"], ["m"], "p")
    assert api.calls == []                                   # straight away, no per-line retries
    free_calls = []
    tr.free = types.SimpleNamespace(translate=lambda lines, target: (free_calls.append(lines) or
                                                                     [f"F:{x}" for x in lines], "en"))
    cfg = {**S.defaults(), "provider": "gemini", "gemini_keys": ["k1", "k2"], "gemini_models": ["m"],
           "target_language": "fa"}
    out = tr.translate(["hello there", "good night"], "t", "a", cfg)
    assert out.provider == "free" and out.lines == ["F:hello there", "F:good night"] and api.calls == []
    lt = LiveTranslator(tr)
    assert lt.translate("where are you", cfg, []) == ("F:where are you", "done") and api.calls == []


# ---------------------------------------------------------------- shared projects
def test_keys_that_hit_429_together_are_reported_as_one_project():
    clock = Clock()
    pool = make_pool(clock)
    for _ in range(2):
        pool.mark_limited("m", "k1", "", MINUTE)
        clock.t += 1.0
        pool.mark_limited("m", "k2", "", MINUTE)
        clock.t += 60
    assert pool.shared_groups(["k1", "k2", "k3"]) == [[1, 2]]
    s = pool.summary(["k1", "k2", "k3"], ["m"])
    assert s[0]["shared_with"] == [2] and s[2]["shared_with"] == []
    # one of them runs out of the day's quota: its partner has none left either (not asked in vain)
    clock.t += 100
    pool.mark_limited("m", "k1", "", DAILY)
    assert pool.slot("m", "k2")[0] == keypool.EXHAUSTED and pool.available("m", "k3")


def test_a_key_that_answers_right_after_another_one_hit_429_has_its_own_quota():
    clock = Clock()
    pool = make_pool(clock)
    pool.mark_limited("m", "k1", "", MINUTE)
    clock.t += 1
    pool.mark_limited("m", "k2", "", MINUTE)          # one point
    clock.t += 30
    pool.mark_limited("m", "k1", "", MINUTE)
    clock.t += 1
    pool.mark_ok("m", "k2")                          # k2 works while k1 is limited: separate
    clock.t += 30
    pool.mark_limited("m", "k1", "", MINUTE)
    clock.t += 1
    pool.mark_limited("m", "k2", "", MINUTE)
    assert pool.shared_groups(["k1", "k2"]) == []


def test_usage_tells_a_shared_project_when_two_keys_run_out_of_the_day():
    clock = Clock()
    pool = make_pool(clock)
    pool.limit_fn = lambda model: 250
    pool.used_fn = lambda model, key: {"k1": 130, "k2": 120, "k3": 250}[key]
    pool.mark_limited("m", "k1", "", DAILY)
    pool.mark_limited("m", "k2", "", DAILY)
    pool.evidence_daily("m", ["k1", "k2"])
    assert pool.shared_groups(["k1", "k2"]) == [[1, 2]]
    pool.forget_pairs()
    pool.mark_limited("m", "k3", "", DAILY)
    pool.evidence_daily("m", ["k1", "k3"])          # 130 + 250 = about two limits: separate projects
    assert pool.shared_groups(["k1", "k3"]) == []


def test_the_state_file_holds_fingerprints_only(tmp_path):
    path = tmp_path / "state.json"
    pool = make_pool(path=path)
    pool.mark_limited("m", "AIzaSySECRETSECRET1", "", DAILY)
    pool.mark_invalid("AIzaSySECRETSECRET2", "bad")
    text = path.read_text(encoding="utf-8")
    assert "SECRET" not in text and fingerprint("AIzaSySECRETSECRET1") in text


# ---------------------------------------------------------------- the offline tier
class FakeOffline:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def ready(self, source, target):
        return self.ok

    def translate(self, lines, target, source=""):
        self.calls.append(list(lines))
        return [f"O:{x}" for x in lines], source or "en"


def broken_free():
    def fail(lines, target):
        raise ConnectionError("no network")

    return types.SimpleNamespace(translate=fail)


def test_offline_tier_is_used_when_gemini_and_free_google_fail():
    api = FakeApi({"k1": "daily"})
    off = FakeOffline()
    tr = Translator(lambda: api, make_pool(), off)
    tr.free = broken_free()
    cfg = {**S.defaults(), "provider": "gemini", "gemini_keys": ["k1"], "gemini_models": ["m"], "target_language": "fa"}
    out = tr.translate(["hello", "world"], "t", "a", cfg)
    assert out.provider == "offline" and out.lines == ["O:hello", "O:world"]
    lt = LiveTranslator(tr)
    assert lt.translate("see you", cfg, []) == ("O:see you", "done")
    cfg_off = {**cfg, "offline_enabled": False}
    with pytest.raises(TranslationError):
        Translator(lambda: api, make_pool(), off).translate(["x y z"], "t", "a", {**cfg_off, "fallback_free": False})


def test_free_google_rests_after_a_failure_so_the_offline_tier_answers_at_once():
    from dualyrics.translate import FreeGoogle, fallback_chain

    calls = []

    class Session:
        def get(self, *a, **kw):
            calls.append(1)
            raise ConnectionError("down")

    free = FreeGoogle(lambda: Session())
    tr = types.SimpleNamespace(free=free, offline=FakeOffline())
    cfg = S.defaults()
    assert fallback_chain(tr, ["a b"], "fa", cfg)[2] == "offline" and len(calls) == 1
    assert free.resting()
    assert fallback_chain(tr, ["c d"], "fa", cfg)[2] == "offline" and len(calls) == 1   # not asked again yet


def test_offline_translator_routes_directly_or_through_english(tmp_path):
    for code, fc, tc in (("translate-en_fa", "en", "fa"), ("translate-de_en", "de", "en")):
        d = tmp_path / code
        (d / "model").mkdir(parents=True)
        (d / "sentencepiece.model").write_bytes(b"x")
        (d / "metadata.json").write_text(json.dumps({"code": code, "from_code": fc, "to_code": tc,
                                                     "package_version": "1.9"}), encoding="utf-8")
        (d / "README.md").write_text("Model from OPUS-MT. License: CC-BY-4.0", encoding="utf-8")
        (d / ".complete").write_text("{}", encoding="utf-8")

    class Model:
        def __init__(self, pkg):
            self.pkg = pkg

        def translate(self, texts):
            return [f"{self.pkg.to_code}({x})" for x in texts]

    tr = offline.OfflineTranslator(tmp_path, loader=Model)
    assert tr.ready("", "fa") and tr.ready("de", "fa") and not tr.ready("fr", "fa")
    assert tr.translate(["Where are you", "", "I love you"], "fa") == (["fa(Where are you)", "", "fa(I love you)"],
                                                                       "en")
    assert tr.translate(["hallo"], "fa", "de") == (["fa(en(hallo))"], "de")
    assert tr.translate(["Wo bist du heute"], "fa") == (["fa(en(Wo bist du heute))"], "de")   # the only other one
    with pytest.raises(RuntimeError):     # Latin text that is not English: never through the en model, ...
        tr.translate(["Dónde estás mi amor"], "de")
    import shutil

    only = tmp_path / "only-en-fa"
    shutil.copytree(tmp_path / "translate-en_fa", only / "translate-en_fa")
    with pytest.raises(RuntimeError):     # ... and never passed through as "already English"
        offline.OfflineTranslator(only, loader=Model).translate(["Dónde estás mi amor", "te quiero"], "en")
    assert offline.looks_english("I don't know what you want") and not offline.looks_english("Je ne sais pas")
    pkgs = offline.installed_packages(tmp_path)
    assert {p.license for p in pkgs} == {"CC-BY-4.0"} and all(p.supported for p in pkgs)


def test_package_licences_come_from_metadata_or_the_readme():
    assert offline.license_from_text("Trained on OPUS. Licensed under CC BY 4.0.") == "CC-BY-4.0"
    assert offline.license_from_text("License: MIT") == "MIT"
    assert offline.license_from_text("OPUS-MT model, no words about it") == ""
    p = offline.PackageInfo.from_entry({"code": "translate-en_fa", "from_code": "en", "to_code": "fa",
                                        "links": ["https://example.org/a.argosmodel"], "license": "CC0"})
    assert p is not None and p.license == "CC0"
    assert offline.PackageInfo.from_entry({"from_code": "en", "to_code": "fa", "links": ["http://x"]}) is None


def _package_zip(code="translate-en_fa", evil=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{code}/metadata.json", json.dumps({"code": code, "from_code": "en", "to_code": "fa",
                                                         "license": "CC-BY-4.0"}))
        zf.writestr(f"{code}/sentencepiece.model", b"sp")
        zf.writestr(f"{code}/model/model.bin", b"bin")
        if evil:
            zf.writestr("../escape.txt", b"no")
    return buf.getvalue()


class DlSession:
    """Serves ``data``; the first response breaks off halfway, the second honours the Range header."""

    def __init__(self, data):
        self.data, self.ranges = data, []

    def get(self, url, headers=None, stream=False, timeout=None, allow_redirects=True):
        rng = (headers or {}).get("Range")
        self.ranges.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        body = self.data[start:]
        cut = not rng
        outer = self

        class R:
            status_code = 206 if rng else 200
            headers = {"Content-Length": str(len(body))}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            def iter_content(self, n):
                part = body[: len(body) // 2] if cut else body
                for i in range(0, len(part), 7):
                    yield part[i:i + 7]

        assert outer is self
        return R()


def test_packages_download_resumably_and_unpack_safely(tmp_path, monkeypatch):
    from dualyrics import download

    monkeypatch.setattr(download, "PAUSE", 0.0)
    info = offline.PackageInfo("translate-en_fa", "en", "English", "fa", "Persian", "1.9",
                               ("https://example.org/en_fa.argosmodel",))
    sess = DlSession(_package_zip())
    pkg = offline.download(info, sess, root=tmp_path)          # broken off halfway, resumed with a Range request
    assert sess.ranges[0] is None and sess.ranges[1] is not None and pkg.license == "CC-BY-4.0" and pkg.supported
    assert (tmp_path / "translate-en_fa" / ".complete").exists()
    assert not list(tmp_path.glob("*.part"))
    bad = offline.PackageInfo("translate-x_y", "x", "X", "y", "Y", "1", ("https://example.org/x.argosmodel",))
    sess2 = DlSession(_package_zip("translate-x_y", evil=True))
    sess2.get = lambda url, headers=None, **kw: DlSession.get(sess2, url, {"Range": "bytes=0-"}, **kw)
    with pytest.raises(OSError):
        offline.download(bad, sess2, root=tmp_path)
    assert not (tmp_path.parent / "escape.txt").exists() and not (tmp_path / "translate-x_y").exists()
    assert offline.remove("translate-en_fa", tmp_path) and not offline.remove("..", tmp_path)


# ---------------------------------------------------------------- line cache on disk
class CountingGemini:
    def __init__(self, can=True):
        self.prompts, self.can = [], can
        self.meter = None

    def ask(self, keys, models, prompt, timeout=None, **kw):
        self.prompts.append(prompt)
        return {"translation": "ترجمه"}

    def can_ask(self, keys, models):
        return self.can


def test_line_translations_persist_so_a_replay_costs_nothing(tmp_path):
    store = linecache.LineStore(tmp_path / "lines.sqlite")
    cfg = {**S.defaults(), "provider": "gemini", "gemini_keys": ["k"], "gemini_models": ["m"], "target_language": "fa"}
    g = CountingGemini()
    LiveTranslator(types.SimpleNamespace(gemini=g, free=None), store).translate("Where is the ship?", cfg, [])
    g2 = CountingGemini()
    again = LiveTranslator(types.SimpleNamespace(gemini=g2, free=None), linecache.LineStore(tmp_path / "lines.sqlite"))
    assert again.translate("where is the ship", cfg, []) == ("ترجمه", "done") and g2.prompts == []


def test_a_lower_tier_line_is_upgraded_by_gemini_only_when_gemini_can_be_asked(tmp_path):
    store = linecache.LineStore(tmp_path / "lines.sqlite")
    store.put("hello friend", "fa", "سلام رفیق (free)", "free")
    store.put("hello friend", "fa", "worse", "offline")             # never replaces a better translation
    assert store.get("hello friend", "fa")[:2] == ("سلام رفیق (free)", "free")
    cfg = {**S.defaults(), "provider": "gemini", "gemini_keys": ["k"], "gemini_models": ["m"], "target_language": "fa"}
    resting = CountingGemini(can=False)
    lt = LiveTranslator(types.SimpleNamespace(gemini=resting, free=None), store)
    assert lt.translate("Hello friend", cfg, [])[0] == "سلام رفیق (free)" and resting.prompts == []
    ready = CountingGemini(can=True)
    lt2 = LiveTranslator(types.SimpleNamespace(gemini=ready, free=None), store)
    assert lt2.translate("Hello friend", cfg, [])[0] == "ترجمه" and len(ready.prompts) == 1
    assert store.get("hello friend", "fa")[1] == "gemini"


# ---------------------------------------------------------------- OCR / UIA dedupe (Translumo's rules)
def test_jaro_and_dice_match_the_upstream_formulas():
    assert dedupe.jaro("MARTHA", "MARHTA") == pytest.approx(0.9444, abs=1e-3)
    assert dedupe.jaro("abc", "") == 0.0 and dedupe.jaro("Hello", "hello") == 1.0   # case-insensitive
    assert dedupe.dice("night", "nacht") == pytest.approx(0.25)
    s = dedupe.similarity("Where are you going?", "Where are y0u going?")
    assert s > dedupe.UPPER_SIMILARITY_THRESHOLD
    assert dedupe.similarity("Where are you going?", "I saw a boat over there.") < dedupe.SIMILARITY_THRESHOLD


def test_text_cache_thresholds_scores_and_lifetime():
    clock = Clock(100.0)
    c = dedupe.TextResultCache(clock=clock)
    assert c.is_cached("Where are you going?", 18)[0] is False
    c.end_iteration()
    first_it = c.iteration
    clock.t += 1
    assert c.is_cached("where are you going?", 18)[0] is True          # same text, ignoring case
    assert c.is_cached("Where are y0u going?", 17)[0] is True          # similar, not better: a repeat
    c.end_iteration()
    assert c.is_cached("Where are you going now?", 22)[0] is False     # similar but more complete
    c.end_iteration()
    assert c.iteration == first_it                                      # still the same subtitle
    assert c.is_cached("I saw a boat over there.", 19)[0] is False and c.iteration != first_it
    c.end_iteration()
    clock.t += dedupe.LIFETIME + 0.1                                    # nothing read for 3 s: cache empty
    assert c.is_cached("I saw a boat over there.", 19)[0] is False


def test_a_trusted_reading_covers_ocr_only_when_very_similar():
    c = dedupe.TextResultCache(clock=Clock())
    c.is_cached("Mimir, how many stories", dedupe.TRUSTED)
    c.end_iteration()
    assert c.is_cached("Mimir, how many storles", 22)[0] is True        # > 0.9
    assert c.is_cached("Mimir, many", 9)[0] is False                    # similar, but not 0.9


def test_near_identical_translations_are_repeats():
    c = dedupe.TextResultCache(clock=Clock())
    it = c.iteration
    assert c.is_translated_cached("کجا می‌روی؟ امروز صبح زود", it) is False
    assert c.is_translated_cached("کجا می‌روی؟ امروز صبح زود.", it) is True
    assert c.is_translated_cached("کجا می‌روی؟ امروز صبح زود.", it + 1) is False
    assert dedupe.same_translation("Where are you going today?", "Where are you going today?!")
    assert not dedupe.same_translation("Where are you going?", "Who are you?")


def test_screen_reader_reuses_the_translation_of_a_line_read_again():
    from tests.test_screen import frame, make_reader

    a, a2, b = "Where are you going?", "Where are y0u going?", "I saw a boat over there."
    frames = [frame(b)] * 3 + [frame(a)] * 3 + [frame("")] * 8 + [frame(a2, noise=3)] * 3   # back after 2 s
    table = {frame(b).tobytes(): b, frame(a).tobytes(): a, frame(a2, noise=3).tobytes(): a2}
    r, state, _ocr, tr = make_reader(frames, table)
    seen = []
    # a translation that does not look like the original (else it counts as our own overlay read back)
    tr.translate = lambda text, cfg, ctx, **kw: (seen.append(text) or f"ترجمه {len(seen)}", "done")
    for i in range(len(frames)):
        state["i"] = i
        r.step(i * 0.25)
    v = r.view(now=len(frames) * 0.25)
    assert seen == [b, a] and r.reused == 1                             # the OCR variant cost no translation
    assert v.visible and v.original == a and v.translation == "ترجمه 2"   # the earlier, cleaner reading


# ---------------------------------------------------------------- settings
def test_new_settings_are_validated_and_hidden_from_assistants():
    v = S.validate({"gemini_keys_off": ["ABCDEF012345", "not-a-fingerprint", 3], "offline_enabled": "yes"})
    assert v["gemini_keys_off"] == ["abcdef012345"] and v["offline_enabled"] is True
    from dualyrics import control

    assert "gemini_keys_off" in control.BLOCKED_KEYS
    assert "gemini_keys_off" not in control.public_settings(S.defaults())


def test_a_half_removed_package_never_counts_and_cache_hits_rarely_write(tmp_path, monkeypatch):
    d = tmp_path / "translate-en_fa"
    (d / "model").mkdir(parents=True)
    (d / "sentencepiece.model").write_bytes(b"x")
    (d / "metadata.json").write_text(json.dumps({"from_code": "en", "to_code": "fa"}), encoding="utf-8")
    (d / ".complete").write_text("{}", encoding="utf-8")
    assert len(offline.installed_packages(tmp_path)) == 1
    monkeypatch.setattr(offline.shutil, "rmtree", lambda *a, **kw: None)     # files still in use
    assert not offline.remove("translate-en_fa", tmp_path)
    assert offline.installed_packages(tmp_path) == []
    now = [1000.0]
    store = linecache.LineStore(tmp_path / "l.sqlite", clock=lambda: now[0])
    store.put("hi", "fa", "سلام", "gemini")
    writes = []
    store._db.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith("UPDATE") else None)
    now[0] += 60
    assert store.get("hi", "fa")[0] == "سلام" and writes == []
    now[0] += linecache.TOUCH_EVERY
    assert store.get("hi", "fa") and len(writes) == 1
