"""Live mode text logic: hallucination filter, dedupe, fragment merging, reading time, context, VAD."""

from types import SimpleNamespace

import pytest

from dualyrics.live.livetranslate import LiveTranslator, build_live_prompt
from dualyrics.live.text import (CaptionStream, ContextWindow, Recognition, display_duration, hallucination_reason,
                                 strip_overlap)
from dualyrics.live.vad import Segmenter


def rec(text, start=0.0, end=None, **kw):
    return Recognition(text, start, start + 1.0 if end is None else end, **kw)


# ---------------------------------------------------------------- hallucinations
@pytest.mark.parametrize("text", ["Thank you for watching!", "Thanks for watching.", "[Music]", "(applause)",
                                  "♪♪", "...", "Please subscribe to my channel", "Subtitles by the Amara.org community",
                                  "Um.", "[MUSIC PLAYING]"])
def test_junk_is_dropped(text):
    assert hallucination_reason(rec(text)) is not None


def test_suspect_short_phrases_need_confidence():
    assert hallucination_reason(rec("Thank you.", avg_logprob=-0.2, no_speech_prob=0.05)) is None
    assert hallucination_reason(rec("Thank you.", avg_logprob=-0.7, no_speech_prob=0.5)) == "junk_phrase"
    assert hallucination_reason(rec("you", avg_logprob=-0.9, no_speech_prob=0.7)) == "junk_phrase"


def test_low_confidence_and_repetition_are_dropped():
    assert hallucination_reason(rec("We need to go", avg_logprob=-1.2, no_speech_prob=0.8)) == "low_confidence"
    assert hallucination_reason(rec("We need to go", avg_logprob=-1.8)) == "low_confidence"
    assert hallucination_reason(rec("We need to go", avg_logprob=-1.2, no_speech_prob=0.1)) is None
    assert hallucination_reason(rec("the the the the the the the")) == "repetition"
    assert hallucination_reason(rec("Boy, listen to me.", compression_ratio=2.9)) == "repetition"


def test_sung_words_are_kept_without_the_notes():
    s = CaptionStream()
    kind, cap = s.push(rec("♪ I will always love you ♪"))
    assert kind == "new" and cap.text == "I will always love you"


# ---------------------------------------------------------------- dedupe, overlap, merging
def test_repeated_recognition_is_dropped():
    s = CaptionStream()
    s.push(rec("Kratos, we have to go.", 0, 1))
    assert s.push(rec("Kratos, we have to go!", 1.5, 2.5)) == (None, "duplicate")
    assert s.push(rec("we have to go", 3, 4)) == (None, "duplicate")   # a piece of the line we already have
    assert len(s.captions) == 1
    assert s.dropped["duplicate"] == 2


def test_longer_rerecognition_replaces_the_shorter():
    s = CaptionStream()
    _, cap = s.push(rec("Where is", 0, 1))
    kind, cap2 = s.push(rec("Where is the boy?", 1.1, 2))
    assert kind == "update" and cap2.id == cap.id and cap2.text == "Where is the boy?"


def test_overlap_from_kept_tail_is_removed():
    assert strip_overlap("I told you we would", "we would go home") == "go home"
    assert strip_overlap("Hello there", "general Kenobi") == "general Kenobi"


def test_fragment_ending_mid_sentence_is_merged():
    s = CaptionStream(merge_gap=1.2)
    _, first = s.push(rec("When the storm comes", 0.0, 2.0))
    first.translation, first.trans_state = "وقتی طوفان بیاید", "done"
    kind, cap = s.push(rec("we stand together.", 2.5, 4.0))
    assert kind == "update" and cap.id == first.id
    assert cap.text == "When the storm comes we stand together."
    assert cap.version == 1 and cap.trans_state == "pending" and cap.translation == ""   # translate again


def test_no_merge_after_a_full_sentence_a_long_gap_or_too_long_text():
    s = CaptionStream(merge_gap=1.2, max_chars=40)
    s.push(rec("It is done.", 0, 1))
    assert s.push(rec("Now we rest", 1.2, 2))[0] == "new"
    assert s.push(rec("and then", 5.0, 6))[0] == "new"                 # 3 s gap
    assert s.push(rec("a very long continuation that cannot fit", 6.1, 8))[0] == "new"
    assert [c.final for c in s.captions] == [True, True, True, False]


def test_unpunctuated_speech_still_splits_after_max_span():
    s = CaptionStream(merge_gap=1.2, max_span=7.0)
    s.push(rec("we must reach Midgard", 0, 2.5))
    assert s.push(rec("before the winter ends", 2.6, 5.0))[0] == "update"
    assert s.push(rec("keep your bow ready", 5.1, 7.5))[0] == "new"        # would span 7.5 s


# ---------------------------------------------------------------- reading time
def test_reading_time_has_minimum_and_maximum():
    assert display_duration("Go!", cps=17, min_s=1.5) == 1.5
    assert display_duration("x" * 500, cps=17, max_s=8) == 8
    assert display_duration("x" * 34, cps=17, min_s=0.5) == pytest.approx(2.0)


def test_reading_time_counts_both_lines_and_translation_only():
    both = display_duration("x" * 34, "y" * 34, cps=17, min_s=0)
    assert both == pytest.approx(3.0)                                   # 34 + 17 chars at 17 cps
    assert display_duration("x" * 34, "y" * 17, cps=17, min_s=0, translation_only=True) == pytest.approx(1.0)
    assert display_duration("x" * 34, "", cps=17, min_s=0, translation_only=True) == pytest.approx(2.0)


# ---------------------------------------------------------------- context
def test_context_window_keeps_last_n_and_replaces_merged_caption():
    c = ContextWindow(3)
    for i in range(5):
        c.add(f"line {i}", f"t{i}", key=i)
    assert c.pairs() == [("line 2", "t2"), ("line 3", "t3"), ("line 4", "t4")]
    c.add("line 4 and more", "t4+", key=4)
    assert c.pairs()[-1] == ("line 4 and more", "t4+") and len(c.pairs()) == 3
    c.resize(1)
    assert c.pairs() == [("line 4 and more", "t4+")]


def test_prompt_contains_context_and_where():
    p = build_live_prompt("Boy!", [("We must go to Midgard.", "باید به میدگارد برویم.")], "fa", "en",
                          where="God of War Ragnarök")
    assert "Midgard" in p and "میدگارد" in p and "God of War Ragnarök" in p and p.rstrip().endswith("Boy!")
    assert "Persian" in p


class _FakeGemini:
    def __init__(self):
        self.prompts = []

    def ask(self, keys, models, prompt, timeout=None, **kw):
        self.prompts.append(prompt)
        return {"translation": "پسر!"}


def test_live_translator_uses_context_and_cache():
    g = _FakeGemini()
    lt = LiveTranslator(SimpleNamespace(gemini=g, free=None))
    cfg = {"target_language": "fa", "provider": "gemini", "gemini_keys": ["k"], "gemini_models": ["m"],
           "fallback_free": False, "skip_same_language": True}
    assert lt.translate("Boy!", cfg, [("Hello.", "سلام.")], source="en") == ("پسر!", "done")
    assert lt.translate("boy", cfg, [], source="en") == ("پسر!", "done")       # cache (normalized)
    assert len(g.prompts) == 1 and "سلام." in g.prompts[0]
    assert lt.translate("سلام دوست من", cfg, [], source="fa") == ("", "skipped")


# ---------------------------------------------------------------- voice activity segmentation
def feed(seg, levels):
    out = []
    for i, lv in enumerate(levels):
        out += seg.feed(i, lv)
    return out


def test_segment_ends_at_a_pause():
    seg = Segmenter(frame_sec=0.03, sensitivity=5, max_segment=6, end_silence=0.3, min_speech=0.4)
    quiet, loud = [0.001] * 20, [0.2] * 30          # 0.9 s of speech
    out = feed(seg, quiet + loud + quiet)
    assert len(out) == 1
    frames = out[0]
    assert 30 <= len(frames) <= 30 + seg.pre_frames + 11 and 20 in frames   # speech + pre-roll + pause


def test_short_blip_is_ignored_and_long_speech_is_cut_with_a_tail():
    seg = Segmenter(frame_sec=0.03, sensitivity=5, max_segment=1.5, end_silence=0.3, min_speech=0.4)
    assert feed(seg, [0.001] * 10 + [0.2] * 5 + [0.001] * 20) == []          # 0.15 s click
    out = feed(seg, [0.2] * 120)                                               # 3.6 s without a pause
    assert len(out) >= 2 and seg.forced and seg.speaking


def test_sensitivity_changes_the_threshold():
    low, high = Segmenter(sensitivity=1), Segmenter(sensitivity=10)
    assert not low.is_voice(0.01) and high.is_voice(0.01)
