"""v2.2 Live mode: Windows Live Captions engine, Silero VAD segmenter, interim commits (fake audio and UIA)."""

import time
import types
from concurrent.futures import Future
from pathlib import Path

import pytest

from dualyrics import settings as S
from dualyrics.live import engine as engine_mod, interim, vad, wincaptions as W
from dualyrics.live.text import CaptionStream, LiveView, Recognition
from dualyrics.live.vad import Segmenter, SileroSegmenter


# ---------------------------------------------------------------- Silero segmenter (fake probabilities)
def feed(seg, probs, w="w"):
    out = []
    for p in probs:
        out.extend(seg.feed_window(w, p))
    return out


def test_silero_segment_has_pre_roll_and_ends_at_a_pause():
    seg = SileroSegmenter(None, max_segment=10, end_silence=0.32, min_speech=0.3)
    out = feed(seg, [0.0] * 10)
    assert out == [] and not seg.speaking
    pre = [f"p{i}" for i in range(10)]
    for i, w in enumerate(pre):
        seg.feed_window(w, 0.0)
    seg.feed_window("s0", 0.9)
    assert seg.speaking and seg._buf[:3] == ["p7", "p8", "p9"]    # ~96 ms of pre-roll before the onset
    out = feed(seg, [0.9] * 15 + [0.0] * 10)
    assert len(out) == 1 and out[0][:4] == ["p7", "p8", "p9", "s0"] and not seg.forced and not seg.speaking


def test_silero_end_pause_adapts_to_the_speaker():
    seg = SileroSegmenter(None, max_segment=60, end_silence=0.5)
    probs = []
    for _ in range(5):                       # pauses of ~0.25 s inside one piece
        probs += [0.9] * 10 + [0.0] * 8
    feed(seg, probs + [0.9] * 5)
    assert 0.3 <= seg.end_silence <= 0.31    # p75 0.256 x 1.2 = 0.31
    seg.pauses.extend([5.0] * 10)
    seg._adapt()
    assert seg.end_silence == 2.0            # clamped


def test_silero_end_pause_gets_shorter_for_long_pieces():
    seg = SileroSegmenter(None, max_segment=60, end_silence=1.0)
    base = seg.silence_limit()
    feed(seg, [0.9] * int(3.2 / 0.032))
    assert seg.silence_limit() < base
    feed(seg, [0.9] * int(3.0 / 0.032))
    l6 = seg.silence_limit()
    feed(seg, [0.9] * int(4.0 / 0.032))
    assert seg.silence_limit() < l6 and seg.silence_limit() >= 3


def test_silero_cuts_long_speech_at_the_quietest_point_and_keeps_the_rest():
    seg = SileroSegmenter(None, max_segment=3.2, end_silence=2.0)
    probs = [0.9] * 60 + [0.55, 0.52, 0.51, 0.52, 0.55] + [0.9] * 35      # a soft dip, never below 0.5
    wins = [f"w{i}" for i in range(len(probs))]
    out = []
    for w, p in zip(wins, probs):
        out.extend(seg.feed_window(w, p))
    assert len(out) == 1 and seg.forced and seg.speaking
    cut = len(out[0])
    assert 60 <= cut <= 65                                       # at the dip, not at the end
    assert seg._buf[0] == wins[cut]                              # the remainder starts the next piece


def test_short_piece_is_merged_with_the_next_speech_or_kept_after_waiting():
    seg = SileroSegmenter(None, max_segment=10, end_silence=0.32, min_speech=0.3, merge_wait=0.6)
    out = feed(seg, [0.9] * 5 + [0.0] * 10)           # 0.16 s of speech: too short alone
    assert out == [] and not seg.speaking and seg.pending
    out = feed(seg, [0.9] * 10 + [0.0] * 10)          # more speech soon after: one piece
    assert len(out) == 1 and sum(1 for _ in out[0]) >= 15
    seg2 = SileroSegmenter(None, max_segment=10, end_silence=0.32, min_speech=0.3, merge_wait=0.6)
    out = feed(seg2, [0.9] * 5 + [0.0] * 10 + [0.0] * 25)   # nothing follows: kept after the wait
    assert len(out) == 1
    seg3 = SileroSegmenter(None, max_segment=10, end_silence=0.32, min_speech=0.3, merge_wait=0.6)
    assert feed(seg3, [0.9] * 2 + [0.0] * 40) == []         # a click (64 ms): dropped


def test_mostly_not_speech_pieces_are_dropped():
    seg = SileroSegmenter(None, max_segment=10, end_silence=0.32, min_speech=0.05)
    probs = [0.9, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4] * 4 + [0.0] * 12
    assert feed(seg, probs) == []                     # 4 speech windows of 44: under 25 %


def test_trim_front_only_cuts_the_same_piece():
    seg = SileroSegmenter(None, max_segment=20, end_silence=1.0)
    feed(seg, [0.9] * 50)
    frames, gen = seg.peek()
    assert len(frames) == 50 and seg.buffered() == pytest.approx(1.6)
    assert seg.trim_front(0.64, gen) and len(seg._buf) == 30 and seg.trimmed
    assert not seg.trim_front(0.64, gen + 1)
    rms = Segmenter(0.03, 5, 20)
    for _ in range(40):
        rms.feed("f", 0.2)
    frames, g = rms.peek()
    assert rms.trim_front(0.3, g) and len(rms._seg) == len(frames) - 10
    out = []
    for _ in range(12):
        out += rms.feed("f", 0.0)
    assert len(out) == 1 and rms.gen == g + 1 and not rms.trim_front(0.3, g)


def test_sensitivity_sets_the_silero_threshold():
    assert vad.silero_threshold(5) == 0.5
    assert vad.silero_threshold(1) > vad.silero_threshold(10)
    seg = SileroSegmenter(None, sensitivity=5)
    seg.set_sensitivity(9)
    assert seg.threshold == pytest.approx(0.3)


def test_energy_segmenter_is_the_fallback(monkeypatch):
    from dualyrics.live import silero

    monkeypatch.setattr(silero, "available", lambda: False)
    assert vad.make_segmenter("auto", 0.03, 5, 6.0).kind == vad.ENERGY
    assert vad.make_segmenter("energy", 0.03, 5, 6.0).kind == vad.ENERGY


def test_silero_model_and_resampler():
    np = pytest.importorskip("numpy")
    from dualyrics.live import silero

    r = silero.To16k(np)
    ramp = np.arange(4800, dtype=np.float32)
    out = np.concatenate([r(ramp[i:i + 1440], 48000) for i in range(0, 4800, 1440)])
    assert len(out) == 1600 and np.allclose(np.diff(out), 3.0)       # 3:1, no gap at the frame edges
    r = silero.To16k(np)
    sig = np.sin(np.arange(44100 * 2) / 50.0).astype(np.float32)
    out = np.concatenate([r(sig[i:i + 1323], 44100) for i in range(0, len(sig), 1323)])
    assert abs(len(out) - 32000) <= 2
    if not silero.available():
        pytest.skip("onnxruntime or the Silero model is not installed")
    model = silero.SileroVad()
    probs = [model(np.zeros(512, dtype=np.float32)) for _ in range(10)]
    assert all(0.0 <= p < 0.3 for p in probs)
    seg = vad.make_segmenter("silero", 0.03, 5, 6.0, rate=lambda: 48000)
    assert seg.kind == vad.SILERO
    for _ in range(40):
        seg.feed(np.zeros(1440, dtype=np.float32), 0.0)
    assert not seg.speaking


# ---------------------------------------------------------------- interim commits (pure)
def test_sentences_split_without_breaking_abbreviations_and_merge_short_ones():
    assert interim.split_sentences("Mr. Smith paid 3.5 dollars in the U.S. today. Oh. Then he left! And") == [
        "Mr. Smith paid 3.5 dollars in the U.S. today.", "Oh. Then he left!", "And"]
    assert interim.split_sentences("你好。我们走吧！好", merge_short=False) == ["你好。", "我们走吧！", "好"]
    assert interim.split_sentences("کجا میری؟ خونه.", merge_short=False) == ["کجا میری؟", "خونه."]


def test_plan_commits_all_but_the_last_sentence():
    p = interim.plan("Hello there my friend. How are you doing today? And then")
    assert p.complete == ["Hello there my friend.", "How are you doing today?"] and p.rest == "And then"
    assert interim.plan("Just one unfinished sentence").complete == []
    assert interim.plan("...") is None


def test_committed_overlap_is_stripped():
    tail = "How are you doing today?"
    assert interim.strip_committed_overlap("are you doing today? And then we go", tail) == "And then we go"
    assert interim.strip_committed_overlap("today? Fine.", tail) == "Fine."
    assert interim.strip_committed_overlap("Something else", tail) == "Something else"
    assert interim.strip_committed_overlap("doing today?", tail) == ""


def test_trim_is_proportional_with_a_margin_and_keeps_half_a_second():
    assert interim.trim_seconds(50, 100, 4.0) == pytest.approx(2.3)
    assert interim.trim_seconds(99, 100, 2.0) == pytest.approx(1.5)          # 0.5 s kept
    assert interim.trim_seconds(0, 100, 4.0) == 0.0


def test_interim_pass_pacing():
    assert not interim.due(1.4, 0.0, 10.0, 0.0)
    assert interim.due(2.0, 0.0, 10.0, 0.0)
    assert not interim.due(3.0, 1.5, 10.0, 0.0)          # only 1.5 s new
    assert not interim.due(4.0, 1.5, 10.0, 9.5)          # too soon after the last pass


# ---------------------------------------------------------------- interim commits in the engine
class ScriptedWhisper:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def transcribe(self, audio, start, end, language=None, prompt="", task="transcribe"):
        self.calls.append((len(audio), task))
        return [Recognition(self.texts.pop(0), start, end, -0.2, 0.05, 1.2, "en")]


def live_cfg(**kw):
    cfg = S.defaults()
    cfg.update(show_translation=False, live_save_transcripts=False, target_language="fa", live_engine="whisper")
    cfg.update(kw)
    return cfg


def new_engine(cfg):
    return engine_mod.LiveEngine(lambda: cfg, lambda: None, types.SimpleNamespace(gemini=None, free=None))


def test_interim_commits_sentences_trims_audio_and_shows_the_rest_as_preview():
    np = pytest.importorskip("numpy")
    cfg = live_cfg()
    eng = new_engine(cfg)
    eng._transcriber = ScriptedWhisper("Hello there my friend. How are", "friend. How are you?")
    eng._seg = Segmenter(0.03, 5, 15.0)
    eng._interim_on = True
    loud, quiet = np.full(1440, 0.1, dtype=np.float32), np.zeros(1440, dtype=np.float32)
    for _ in range(75):
        eng._on_frame(loud, 0.1)
    assert eng._interim_due
    before = eng._seg.buffered()
    eng._do_interim(np, 48000, cfg)
    caps = list(eng.stream.captions)
    assert [c.text for c in caps] == ["Hello there my friend."]
    assert eng._seg.buffered() < before and eng._seg.trimmed
    assert eng._preview[0] == "How are"
    v = eng.view(time.time())
    assert v.preview == "How are" and v.original == "Hello there my friend."
    assert eng.view(time.time(), translation_only=True).preview == ""
    for _ in range(15):
        eng._on_frame(quiet, 0.0)
    frames, start, end, sgen, rate = eng._segq.get_nowait()
    eng._final_piece(eng._to16(np, frames, rate or 48000), start, end, sgen, cfg)
    assert [c.text for c in eng.stream.captions] == ["Hello there my friend.", "How are you?"]
    assert eng._preview == ("", 0.0) and eng._interim.gen == -1


def test_a_trim_that_comes_too_late_is_applied_to_the_finished_piece():
    np = pytest.importorskip("numpy")
    cfg = live_cfg()
    eng = new_engine(cfg)
    eng._transcriber = ScriptedWhisper("One sentence here. And", "And more.")
    eng._seg = Segmenter(0.03, 5, 15.0)
    loud, quiet = np.full(1440, 0.1, dtype=np.float32), np.zeros(1440, dtype=np.float32)
    for _ in range(80):
        eng._on_frame(loud, 0.1)
    real_peek = eng._seg.peek
    peeked = real_peek()

    def peek_then_end():               # the speaker pauses while the interim pass runs
        for _ in range(15):
            eng._seg.feed(quiet, 0.0)
        return peeked

    eng._seg.peek = peek_then_end
    eng._do_interim(np, 48000, cfg)
    assert eng._interim.late_trim > 0
    a16 = np.zeros(16000 * 3, dtype=np.float32)
    eng._final_piece(a16, 0.0, 3.0, peeked[1], cfg)
    assert eng._transcriber.calls[-1][0] < 16000 * 3
    assert [c.text for c in eng.stream.captions] == ["One sentence here.", "And more."]


def test_no_interim_pass_when_two_whisper_passes_are_needed():
    np = pytest.importorskip("numpy")
    cfg = live_cfg(pivot_show_original=True)
    eng = new_engine(cfg)
    eng._transcriber = ScriptedWhisper()
    eng._seg = Segmenter(0.03, 5, 15.0)
    for _ in range(80):
        eng._seg.feed(np.full(1440, 0.1, dtype=np.float32), 0.1)
    eng._do_interim(np, 48000, cfg)
    assert eng._transcriber.calls == []


def test_preview_line_goes_to_the_dim_line_of_the_template():
    from dualyrics import app as app_mod

    fake = types.SimpleNamespace(_notice=("", 0.0))
    c = app_mod.App._live_content(fake, time.time(), LiveView(preview="How are", visible=True))
    assert c.status == "How are" and c.original == ""
    c = app_mod.App._live_content(fake, time.time(), LiveView(original="Hi.", translation="سلام.", preview="How",
                                                              visible=True))
    assert c.next == "How"
    c = app_mod.App._live_content(fake, time.time(), LiveView(original="Hi.", extra="Hello.", preview="How",
                                                              visible=True))
    assert c.next == "Hello."


# ---------------------------------------------------------------- Live Captions: text
def test_latest_sentence_is_after_the_last_terminator():
    assert W.latest_sentence("Hello there. How are") == "How are"
    assert W.latest_sentence("Hello there. How are you?") == "How are you?"
    assert W.latest_sentence("Line one.\nLine two! Three") == "Three"
    assert W.latest_sentence("こんにちは。元気") == "元気"
    assert W.latest_sentence("") == ""


def test_tracker_emits_on_sentence_end_idle_and_rationed_partials():
    t = W.SentenceTracker()
    assert t.feed("Old text from before. Hello", 0.0) == []
    assert t.feed("Old text from before. Hello there", 0.03) == []
    assert t.feed("Old text from before. Hello there my", 0.06) == []      # three changes, but too early
    out = t.feed("Old text from before. Hello there my friend", 1.6)
    assert [(e.text, e.done) for e in out] == [("Hello there my friend", False)]   # 1.5 s after its start
    key = out[0].key
    assert t.feed("Old text from before. Hello there my friend how", 2.0) == []
    assert t.feed("Old text from before. Hello there my friend how are", 2.1) == []
    assert t.feed("Old text from before. Hello there my friend how are you", 2.2) == []   # next gap is 3 s
    assert t.feed("Old text from before. Hello there my friend how are you", 3.0) == []
    out = t.feed("Old text from before. Hello there my friend how are you", 3.5)
    assert [(e.text, e.done, e.key) for e in out] == [("Hello there my friend how are you", False, key)]  # idle
    assert t.feed("Old text from before. Hello there my friend how are you", 4.0) == []         # sent once
    out = t.feed("Old text from before. Hello there my friend how are you. And", 4.1)
    assert [(e.text, e.done, e.key) for e in out] == [("Hello there my friend how are you.", True, key)]
    out = t.feed("Old text from before. Hello there my friend how are you. And then? I", 4.2)
    assert [(e.text, e.done) for e in out] == [("And then?", True)] and out[0].key != key
    assert t.feed("", 4.3) == [] and not t.active


def test_tracker_ignores_text_that_was_there_before_and_after_reset():
    t = W.SentenceTracker()
    t.feed("First. Second.", 0.0)
    assert t.feed("First. Second.", 5.0) == []
    t.reset()
    assert t.feed("First. Second. Third.", 6.0) == []
    assert t.feed("First. Second. Third. Fourth.", 6.1) == []          # a full stop at the end: confirmed ...
    assert [e.text for e in t.feed("First. Second. Third. Fourth.", 6.25)] == ["Fourth."]   # ... 0.1 s later


def test_a_long_sentence_without_punctuation_costs_at_most_three_requests():
    words = ("so what we are going to do today is take a look at the new update and see how the changes "
             "affect us").split()
    assert len(words) == 23
    t = W.SentenceTracker()
    t.feed("Intro.", 0.0)
    emits, now = [], 0.0
    for i in range(len(words)):
        now = 0.1 + 0.3 * i
        for step in range(12):                  # Live Captions is read every 25 ms
            emits += t.feed("Intro. " + " ".join(words[:i + 1]), now + step * 0.025)
    for k in range(80):                          # the speaker pauses for 2 s
        emits += t.feed("Intro. " + " ".join(words), now + 0.3 + k * 0.025)
    assert 1 <= len(emits) <= 3 and all(not e.done for e in emits)
    assert emits[0].text.count(" ") >= 4                     # never the first word or two
    assert emits[-1].text == " ".join(words) and len({e.key for e in emits}) == 1


def test_a_cut_word_is_not_sent_right_after_the_sentence_starts():
    t = W.SentenceTracker()
    t.feed("Hi.", 0.0)
    got = []
    for i, text in enumerate(["Hi. I", "Hi. I am", "Hi. I am go", "Hi. I am going"]):
        got += t.feed(text, 0.025 * (i + 1))
    assert got == []


def test_an_early_full_stop_is_confirmed_or_keeps_its_caption():
    t = W.SentenceTracker()
    t.feed("Okay.", 0.0)
    got = t.feed("Okay. I think we.", 0.1) + t.feed("Okay. I think we.", 0.15)    # only 50 ms: not believed
    got += t.feed("Okay. I think we should go now.", 0.2) + t.feed("Okay. I think we should go now.", 0.35)
    assert [(e.text, e.done) for e in got] == [("I think we should go now.", True)]
    t2 = W.SentenceTracker()
    t2.feed("Okay.", 0.0)
    first = t2.feed("Okay. I think we.", 0.1) + t2.feed("Okay. I think we.", 0.3)   # confirmed: sent
    rest = t2.feed("Okay. I think we should", 0.5) + t2.feed("Okay. I think we should go now.", 0.6)
    rest += t2.feed("Okay. I think we should go now.", 0.8)
    assert [(e.text, e.done) for e in first] == [("I think we.", True)]
    assert [(e.text, e.done) for e in rest] == [("I think we should go now.", True)]
    assert rest[-1].key == first[0].key              # the same caption, updated in place


def test_cjk_line_breaks_end_sentences_and_spaces_before_marks_go():
    assert W.sentences("今日は天気がいいですね\n明日も晴れるでしょう\nでは") == [
        ("今日は天気がいいですね。", True), ("明日も晴れるでしょう。", True), ("では", False)]
    assert W.sentences("はい\nそうです") == [("はいそうです", False)]           # a short line: joined
    assert W.sentences("Line one\nand two") == [("Line one and two", False)]
    assert W.sentences("Hello there . How") == [("Hello there.", True), ("How", False)]


# ---------------------------------------------------------------- Live Captions: choice and availability
AV = W.Availability(True, "", ("en-US",))


def test_engine_choice():
    assert W.choose("auto", AV, "en") == (W.LIVE_CAPTIONS, "auto")
    assert W.choose("auto", AV, "auto") == (W.LIVE_CAPTIONS, "auto")
    assert W.choose("auto", AV, "de") == (W.WHISPER, "language")            # no German pack
    assert W.choose("auto", AV, "auto", whisper_pivot=True) == (W.WHISPER, "pivot")
    assert W.choose("auto", AV, "en", needs_audio=True) == (W.WHISPER, "dub_audio")
    assert W.choose("auto", AV, "en", failed_recently=True) == (W.WHISPER, "failed")
    assert W.choose("whisper", AV, "en") == (W.WHISPER, "chosen")
    assert W.choose("live_captions", AV, "de") == (W.LIVE_CAPTIONS, "chosen")
    old = W.Availability(False, "old_windows")
    assert W.choose("auto", old, "en") == (W.WHISPER, "old_windows")
    assert W.choose("live_captions", old, "en") == (W.WHISPER, "old_windows")
    nolang = W.Availability(False, "no_language")
    assert W.choose("live_captions", nolang, "en") == (W.LIVE_CAPTIONS, "chosen")   # tried anyway
    assert W.choose("auto", AV, "auto", whisper_pivot=True, whisper_installed=False) == (W.LIVE_CAPTIONS,
                                                                                         "no_whisper")
    assert AV.supports("en") and not AV.supports("fr") and W.caption_language(AV, "auto") == "en"


def test_speech_pack_names_are_recognised():
    m = W._SPEECH_PACK.match("MicrosoftWindows.Speech.en-US.1_1.0.0.0_x64__cw5n1h2txyewy")
    assert m and m.group(1) == "en-US"
    assert W._SPEECH_PACK.match("MicrosoftWindows.Speech.zh-Hans-CN.1_1.0.0.0_x64__cw5n1h2txyewy").group(1) == \
        "zh-Hans-CN"
    assert not W._SPEECH_PACK.match("Microsoft.XboxSpeechToTextOverlay_1.117.15001.0_x64__8wekyb3d8bbwe")


def test_availability_reports_why_not(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "windows_build", lambda: 19045)
    assert W.availability(force=True).reason in ("old_windows",)
    exe = tmp_path / "System32" / "LiveCaptions.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(W, "windows_build", lambda: 26100)
    monkeypatch.setattr(W, "exe_path", lambda: exe)
    monkeypatch.setattr(W, "installed_languages", lambda: ())
    if W.os.name == "nt":
        assert W.availability(force=True).reason in ("no_language", "no_uia")
        monkeypatch.setattr(W, "installed_languages", lambda: ("en-US",))
        a = W.availability(force=True)
        assert a.ok or a.reason == "no_uia"
    W.availability(force=True)


# ---------------------------------------------------------------- Live Captions: window and poll loop (fakes)
class FakeApi:
    def __init__(self, hwnd=0):
        self.hwnd, self.ex, self.iconic = hwnd, 0x100, False
        self.calls = []

    def find(self):
        return self.hwnd

    def launch(self, exe):
        self.calls.append("launch")
        self.hwnd = 77
        return types.SimpleNamespace(wait=lambda timeout=None: None, terminate=lambda: None)

    def ex_style(self, h):
        return self.ex

    def set_ex_style(self, h, v):
        self.calls.append(("ex", v))
        self.ex = v

    def placement(self, h):
        return "placement"

    def set_placement(self, h, wp):
        self.calls.append(("placement", wp))

    def hide(self, h):
        self.calls.append("hide")
        self.ex |= W.WS_EX_TOOLWINDOW
        self.iconic = True

    def is_iconic(self, h):
        return self.iconic

    def is_window(self, h):
        return h == self.hwnd

    def close(self, h):
        self.calls.append("close")
        self.hwnd = 0


def fake_exe(tmp_path):
    p = Path(tmp_path) / "LiveCaptions.exe"
    p.write_bytes(b"")
    return p


def test_live_captions_is_started_hidden_and_closed_with_its_style_back(tmp_path):
    api = FakeApi()
    app = W.LiveCaptionsApp(api, sleep=lambda s: None, exe=fake_exe(tmp_path))
    assert app.ensure() == 77 and app.ours
    assert api.calls[:2] == ["launch", "hide"]
    app.keep_hidden(77)
    assert api.calls.count("hide") == 1                 # still hidden: nothing to do
    api.iconic = False                                  # it showed itself again
    app.keep_hidden(77)
    assert api.calls.count("hide") == 2
    app.close()
    assert ("ex", 0x100) in api.calls and ("placement", "placement") in api.calls and "close" in api.calls


def test_a_live_captions_the_user_opened_is_left_alone(tmp_path):
    api = FakeApi(hwnd=5)
    app = W.LiveCaptionsApp(api, sleep=lambda s: None, exe=fake_exe(tmp_path))
    assert app.ensure() == 5 and not app.ours
    app.keep_hidden(5)
    app.close()
    assert api.calls == []


def test_a_window_hidden_by_an_earlier_run_is_adopted(tmp_path):
    api = FakeApi(hwnd=9)
    api.ex, api.iconic = 0x100 | W.WS_EX_TOOLWINDOW, True
    app = W.LiveCaptionsApp(api, sleep=lambda s: None, exe=fake_exe(tmp_path))
    app.ensure()
    assert app.ours
    app.close()
    assert ("ex", 0x100) in api.calls


def test_live_captions_that_never_opens_is_unavailable(tmp_path):
    api = FakeApi()
    api.launch = lambda exe: None
    clock = iter(range(0, 100))
    app = W.LiveCaptionsApp(api, sleep=lambda s: None, clock=lambda: next(clock), start_timeout=5,
                            exe=fake_exe(tmp_path))
    with pytest.raises(W.Unavailable):
        app.ensure()
    with pytest.raises(W.Unavailable):
        W.LiveCaptionsApp(FakeApi(), exe=Path(tmp_path) / "missing.exe").ensure()


class FakeApp:
    def __init__(self):
        self.hwnd, self.ensured, self.closes, self.hidden = 0, 0, 0, 0
        self.ours = True

    def ensure(self):
        self.ensured += 1
        self.hwnd = 10 + self.ensured
        return self.hwnd

    def alive(self, h):
        return h == self.hwnd

    def keep_hidden(self, h):
        self.hidden += 1

    def close(self, wait=True):
        self.closes += 1


class FakeReader:
    def __init__(self, texts):
        self.texts = list(texts)
        self.resets = 0

    def reset(self):
        self.resets += 1

    def read(self, hwnd):
        return self.texts.pop(0) if self.texts else None


def test_runner_reads_recovers_and_restarts():
    app = FakeApp()
    got = []
    reader = FakeReader(["Hi", None, "Hi there", "Hi there friend", "Hi there friend.", "Hi there friend."])
    r = W.CaptionsRunner(app, reader, got.append, box_timeout=5)
    r.start(0.0)
    for i in range(6):
        r.step(0.1 * (i + 1))                        # a None read (element not available) is just retried
    assert [(e.text, e.done) for e in got][-1] == ("Hi there friend.", True)
    app.hwnd = 0                                     # the user closed Live Captions
    r.step(1.0)
    assert app.ensured == 2 and reader.resets == 2


def test_runner_gives_up_without_a_caption_box_or_when_it_keeps_closing():
    r = W.CaptionsRunner(FakeApp(), FakeReader([]), lambda e: None, box_timeout=5)
    r.start(0.0)
    r.step(1.0)
    with pytest.raises(W.Unavailable):
        r.step(6.0)
    app = FakeApp()
    r = W.CaptionsRunner(app, FakeReader(["x"] * 10), lambda e: None, max_restarts=2)
    r.start(0.0)
    with pytest.raises(W.Unavailable):
        for i in range(5):
            app.hwnd = 0
            r.step(float(i))


# ---------------------------------------------------------------- Live Captions in the engine
def test_live_captions_sentences_update_one_caption_in_place():
    cfg = live_cfg(live_engine="live_captions")
    eng = new_engine(cfg)
    eng._want, eng._gen, eng.engine = True, 1, engine_mod.LIVE_CAPTIONS
    t0 = time.time()
    eng._on_caption(W.Emit("Hello there", False, 1, t0), 1)
    eng._on_caption(W.Emit("Hello there my friend", False, 1, t0), 1)
    caps = list(eng.stream.captions)
    assert len(caps) == 1 and caps[0].text == "Hello there my friend" and caps[0].version == 1
    eng._on_caption(W.Emit("Hello there my friend.", True, 1, t0), 1)
    assert caps[0].text == "Hello there my friend." and caps[0].final and caps[0].version == 1
    eng._on_caption(W.Emit("How are you?", True, 2, t0 + 2), 1)
    assert [c.text for c in eng.stream.captions] == ["Hello there my friend.", "How are you?"]
    eng._on_caption(W.Emit("Late event of a stopped run.", True, 3, t0 + 3), 0)
    assert len(eng.stream.captions) == 2


def test_stale_translations_are_cancelled():
    cfg = live_cfg(show_translation=True)
    eng = new_engine(cfg)
    eng.engine = engine_mod.LIVE_CAPTIONS
    jobs = []

    class Queued:
        def submit(self, fn):
            f = Future()
            jobs.append((fn, f))
            return f

        def shutdown(self, **kw):
            pass

    eng._tx = Queued()
    eng.live_tr = types.SimpleNamespace(translate=lambda text, cfg, ctx, **kw: ("T:" + text, "done"))
    a = eng.stream.push(Recognition("First sentence here.", 0.0, 1.0), now=1.0)[1]
    eng._translate(a, cfg)
    a.text, a.version = "First sentence here, longer.", 1
    eng._translate(a, cfg)
    assert jobs[0][1].cancelled()                       # an older version that never started
    b = eng.stream.push(Recognition("Second one.", 3.0, 4.0), now=4.0)[1]
    eng._translate(b, cfg)
    jobs[2][0]()                                        # the newer caption's translation finishes first
    assert b.translation == "T:Second one." and jobs[1][1].cancelled() and a.trans_state == "failed"


def test_live_captions_failure_falls_back_to_whisper(monkeypatch):
    cfg = live_cfg(live_engine="auto", live_source_language="en")
    eng = new_engine(cfg)
    monkeypatch.setattr(engine_mod.deps, "available", lambda: True)
    monkeypatch.setattr(W, "availability", lambda force=False, max_age=60.0: AV)
    assert eng.choose_engine(cfg) == (W.LIVE_CAPTIONS, "auto")

    def broken(gen):
        raise W.Unavailable("no caption box")

    ran = []
    eng._listen_captions = broken
    eng._prepare = lambda gen: None
    eng._listen = lambda gen: ran.append(eng.engine)
    eng._want, eng._gen = True, 1
    eng._run(1)
    assert ran == [W.WHISPER] and "caption box" in eng.lc_note and eng.engine_why == "failed"
    assert eng.choose_engine(cfg) == (W.WHISPER, "failed")


def test_live_captions_works_without_the_whisper_packages(monkeypatch):
    cfg = live_cfg(live_engine="auto")
    eng = new_engine(cfg)
    monkeypatch.setattr(engine_mod.deps, "available", lambda: False)
    monkeypatch.setattr(W, "availability", lambda force=False, max_age=60.0: AV)
    started = []
    monkeypatch.setattr(engine_mod.LiveEngine, "_run", lambda self, gen: started.append(gen))
    eng.start()
    assert eng.running and started
    eng.shutdown()
    monkeypatch.setattr(W, "availability", lambda force=False, max_age=60.0: W.Availability(False, "old_windows"))
    eng2 = new_engine(cfg)
    eng2.start()
    assert not eng2.running and eng2.state == engine_mod.MISSING


def test_caption_revise_keeps_translation_for_punctuation_only():
    st = CaptionStream()
    cap = st.push(Recognition("Hello there", 0.0, 1.0))[1]
    cap.translation, cap.trans_state = "سلام", "done"
    assert st.revise(cap, Recognition("Hello there.", 0.0, 1.2)) is None
    assert cap.text == "Hello there." and cap.translation == "سلام"
    assert st.revise(cap, Recognition("Hello there friend.", 0.0, 1.5)) is cap
    assert cap.trans_state == "pending" and cap.version == 1


def test_new_settings_are_validated():
    s = S.validate({"live_engine": "magic", "live_vad": "silero", "live_interim": "yes"})
    assert s["live_engine"] == "auto" and s["live_vad"] == "silero" and s["live_interim"] is True
    assert S.validate({"live_engine": "live_captions"})["live_engine"] == "live_captions"


def test_live_captions_loop_in_the_engine_with_fakes(monkeypatch):
    cfg = live_cfg(live_engine="live_captions", live_source_language="en")
    eng = new_engine(cfg)
    monkeypatch.setattr(W, "availability", lambda force=False, max_age=60.0: AV)
    texts = ["Earlier line.", "Earlier line. Where", "Earlier line. Where are", "Earlier line. Where are we",
             "Earlier line. Where are we going?"] + ["Earlier line. Where are we going?"] * 6

    class Reader(FakeReader):
        def read(self, hwnd):
            if not self.texts:
                eng._want = False
                return None
            return self.texts.pop(0)

    monkeypatch.setattr(W, "CaptionBoxReader", lambda: Reader(texts))
    monkeypatch.setattr(W, "LiveCaptionsApp", FakeApp)
    eng._want, eng._gen = True, 1
    eng._listen_captions(1)
    assert [(c.text, c.final) for c in eng.stream.captions] == [("Where are we going?", True)]
    assert eng.stream.captions[0].language == "en" and eng.state == engine_mod.OFF

    class Broken(FakeApp):
        def ensure(self):
            raise OSError("access denied")

    eng2 = new_engine(cfg)
    monkeypatch.setattr(W, "LiveCaptionsApp", Broken)
    eng2._want, eng2._gen = True, 1
    with pytest.raises(W.Unavailable):
        eng2._listen_captions(1)


# ---------------------------------------------------------------- review fixes (v2.2)
def test_interim_commits_never_caption_our_own_dubbed_voice():
    np = pytest.importorskip("numpy")
    cfg = live_cfg()
    eng = new_engine(cfg)
    eng._transcriber = ScriptedWhisper("Hello there my friend. How are")
    eng._seg = Segmenter(0.03, 5, 15.0)
    eng._interim_on = True
    for _ in range(75):
        eng._on_frame(np.full(1440, 0.1, dtype=np.float32), 0.1)
    eng.self_voice = lambda language, start, end: True
    before = eng._seg.buffered()
    eng._do_interim(np, 48000, cfg)
    assert list(eng.stream.captions) == [] and eng._seg.buffered() < before   # trimmed all the same


def test_an_interim_pass_that_commits_nothing_waits_for_new_audio():
    np = pytest.importorskip("numpy")
    cfg = live_cfg()
    eng = new_engine(cfg)
    eng._transcriber = ScriptedWhisper("...")
    eng._seg = Segmenter(0.03, 5, 15.0)
    eng._interim_on = True
    loud = np.full(1440, 0.1, dtype=np.float32)
    for _ in range(75):
        eng._on_frame(loud, 0.1)
    assert eng._interim_due
    eng._interim_due = False
    eng._do_interim(np, 48000, cfg)                   # nothing to commit
    eng._ipace[2] -= 5.0                              # the cooldown is long over
    for _ in range(10):                               # 0.3 s of new audio: not another pass yet
        eng._on_frame(loud, 0.1)
    assert not eng._interim_due
    for _ in range(60):
        eng._on_frame(loud, 0.1)
    assert eng._interim_due


def test_a_closed_live_captions_is_never_started_again(tmp_path):
    api = FakeApi()
    app = W.LiveCaptionsApp(api, sleep=lambda s: None, exe=fake_exe(tmp_path))
    app.ensure()
    app.close()
    assert api.hwnd == 0 and not app.ours
    with pytest.raises(W.Unavailable):                # a poll still running must not start an orphan
        app.ensure()
    assert api.calls.count("launch") == 1


def test_live_captions_without_a_window_is_not_left_running(tmp_path):
    killed = []
    api = FakeApi()
    api.launch = lambda exe: types.SimpleNamespace(wait=lambda timeout=None: None, terminate=lambda: killed.append(1))
    clock = iter(range(0, 100))
    app = W.LiveCaptionsApp(api, sleep=lambda s: None, clock=lambda: next(clock), start_timeout=5,
                            exe=fake_exe(tmp_path))
    with pytest.raises(W.Unavailable):
        app.ensure()
    assert killed and not app.ours and app.proc is None


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


def test_a_failed_live_captions_is_closed_and_a_stopped_run_is_no_failure(monkeypatch):
    cfg = live_cfg(live_engine="auto", live_source_language="en")
    monkeypatch.setattr(engine_mod.deps, "available", lambda: True)
    monkeypatch.setattr(W, "availability", lambda force=False, max_age=60.0: AV)
    eng = new_engine(cfg)
    app = FakeApp()
    eng._lc_app = app

    def broken(gen):
        raise W.Unavailable("no caption box")

    eng._listen_captions, eng._prepare, eng._listen = broken, (lambda gen: None), (lambda gen: None)
    eng._want, eng._gen = True, 1
    eng._run(1)
    assert eng._lc_app is None and _wait(lambda: app.closes == 1) and eng._lc_failed_at
    eng2 = new_engine(cfg)
    app2 = FakeApp()
    eng2._lc_app = app2

    def stopped(gen):                                 # unload closed it while the run was stopping
        eng2._gen += 1
        raise W.Unavailable("Live Captions was closed by Dualyrics")

    eng2._listen_captions = stopped
    eng2._want, eng2._gen = True, 1
    eng2._run(1)
    assert eng2._lc_failed_at == 0.0 and eng2._lc_app is app2 and app2.closes == 0


def test_a_live_captions_hidden_by_a_crashed_run_is_adopted_and_closed_later(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "availability", lambda force=False, max_age=60.0: AV)
    api = FakeApi(hwnd=9)
    api.ex, api.iconic = 0x100 | W.WS_EX_TOOLWINDOW, True
    exe = fake_exe(tmp_path)
    real = W.LiveCaptionsApp
    monkeypatch.setattr(W, "LiveCaptionsApp", lambda: real(api, sleep=lambda s: None, exe=exe))
    eng = new_engine(live_cfg())
    assert eng.adopt_orphan() and eng.loaded
    eng.unload()
    assert _wait(lambda: "close" in api.calls) and ("ex", 0x100) in api.calls
    user = FakeApi(hwnd=5)                            # a Live Captions the user opened is never taken
    monkeypatch.setattr(W, "LiveCaptionsApp", lambda: real(user, sleep=lambda s: None, exe=exe))
    eng2 = new_engine(live_cfg())
    assert not eng2.adopt_orphan() and not eng2.loaded and user.calls == []
