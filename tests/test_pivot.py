"""Any-language pipeline: Whisper translate to English, then English to the target (with fakes)."""

from dualyrics import settings as S
from dualyrics.live import pivot
from dualyrics.live.livetranslate import LiveTranslator
from dualyrics.live.text import CaptionStream, Recognition


class FakeWhisper:
    """task=translate returns English, task=transcribe the original; both report the spoken language."""

    def __init__(self, original="Wohin gehst du?", english="Where are you going?", lang="de"):
        self.original, self.english, self.lang = original, english, lang
        self.calls = []

    def transcribe(self, audio, start, end, language=None, prompt="", task="transcribe"):
        self.calls.append(task)
        text = self.english if task == "translate" else self.original
        return [Recognition(text, start, end, -0.2, 0.05, 1.2, self.lang)]


def test_whisper_tasks():
    assert pivot.whisper_tasks(True, False, False, "auto", "fa") == ("translate",)
    assert pivot.whisper_tasks(True, True, False, "auto", "fa") == ("translate", "transcribe")
    assert pivot.whisper_tasks(False, True, False, "auto", "fa") == ("transcribe",)      # pivot off
    assert pivot.whisper_tasks(True, False, True, "auto", "fa") == ("transcribe",)       # English-only model
    assert pivot.whisper_tasks(True, False, False, "en", "fa") == ("transcribe",)        # English speech
    assert pivot.whisper_tasks(True, False, False, "fa", "fa") == ("transcribe",)        # already the target


def test_recognize_pivots_through_english():
    w = FakeWhisper()
    recs = pivot.recognize(w, None, 0, 1, None, "", ("translate",), "fa")
    assert w.calls == ["translate"]
    assert recs[0].text == "Where are you going?" and recs[0].pivot and recs[0].language == "de" and not recs[0].alt


def test_recognize_keeps_the_original_line_when_asked():
    w = FakeWhisper()
    recs = pivot.recognize(w, None, 0, 1, None, "", ("translate", "transcribe"), "fa")
    assert w.calls == ["translate", "transcribe"]
    assert recs[0].pivot and recs[0].alt == "Wohin gehst du?"


def test_recognize_english_speech_or_target_language_speech_is_not_pivoted():
    en = FakeWhisper(original="Hi there", english="Hi there", lang="en")
    assert not pivot.recognize(en, None, 0, 1, None, "", ("translate",), "fa")[0].pivot
    fa = FakeWhisper(original="کجا می‌روی؟", english="Where are you going?", lang="fa")
    recs = pivot.recognize(fa, None, 0, 1, None, "", ("translate",), "fa")
    assert fa.calls == ["translate", "transcribe"] and recs[0].text == "کجا می‌روی؟" and not recs[0].pivot


def test_display_lines():
    # pivot off: line and translation
    assert pivot.display_lines("Hola", "", False, "سلام", "fa", True) == ("Hola", "سلام", "")
    # pivot: English on top, translation under it
    assert pivot.display_lines("Hello", "Hola", True, "سلام", "fa", False) == ("Hello", "سلام", "")
    # also show the original: original, translation, English small
    assert pivot.display_lines("Hello", "Hola", True, "سلام", "fa", True) == ("Hola", "سلام", "Hello")
    # target English: the English line is the translation
    assert pivot.display_lines("Hello", "Hola", True, "", "en", True) == ("Hola", "Hello", "")
    assert pivot.display_lines("Hello", "Hola", True, "", "en", False) == ("Hello", "", "")


def test_caption_stream_keeps_pivot_and_merges_original_lines():
    cs = CaptionStream()
    cs.push(Recognition("Where are you", 0, 1, language="de", pivot=True, alt="Wohin"), now=1)
    kind, cap = cs.push(Recognition("going tonight?", 1.3, 2, language="de", pivot=True, alt="gehst du heute?"),
                        now=2)
    assert kind == "update" and cap.pivot and cap.text == "Where are you going tonight?"
    assert cap.alt == "Wohin gehst du heute?"


class FakeFree:
    def __init__(self):
        self.calls = []

    def translate(self, lines, target):
        self.calls.append((lines[0], target))
        table = {("こんにちは、元気？", "en"): ("Hello, how are you?", "ja"),
                 ("Hello, how are you?", "fa"): ("سلام، حالت چطوره؟", "en"),
                 ("Good morning everyone", "en"): ("Good morning everyone", "en"),
                 ("Good morning everyone", "fa"): ("صبح همگی بخیر", "en")}
        out, det = table.get((lines[0], target), (lines[0], ""))
        return [out], det


class FakeTranslator:
    def __init__(self):
        self.free = FakeFree()
        self.gemini = None


def cfg(**kw):
    return {**S.defaults(), "target_language": "fa", "provider": "free", **kw}


def test_screen_text_pivots_through_english():
    tr = FakeTranslator()
    res = pivot.pivot_text("こんにちは、元気？", LiveTranslator(tr), cfg(), [])
    assert (res.top, res.translation, res.source, res.english) == ("Hello, how are you?", "سلام، حالت چطوره؟", "ja",
                                                                    "Hello, how are you?")
    assert tr.free.calls == [("こんにちは、元気？", "en"), ("Hello, how are you?", "fa")]
    res2 = pivot.pivot_text("こんにちは、元気？", LiveTranslator(tr), cfg(pivot_show_original=True), [])
    assert (res2.top, res2.translation, res2.extra) == ("こんにちは、元気？", "سلام، حالت چطوره؟", "Hello, how are you?")


def test_screen_text_in_english_or_without_pivot_goes_straight_to_the_target():
    tr = FakeTranslator()
    res = pivot.pivot_text("Good morning everyone", LiveTranslator(tr), cfg(), [])
    assert (res.top, res.translation, res.english) == ("Good morning everyone", "صبح همگی بخیر", "")
    tr2 = FakeTranslator()
    pivot.pivot_text("こんにちは、元気？", LiveTranslator(tr2), cfg(pivot_english=False), [])
    assert tr2.free.calls == [("こんにちは、元気？", "fa")]


def test_screen_text_already_in_the_target_language_is_not_translated():
    tr = FakeTranslator()
    res = pivot.pivot_text("سلام، امروز کجا می‌ریم؟", LiveTranslator(tr), cfg(), [])
    assert res.state == "skipped" and res.top == "سلام، امروز کجا می‌ریم؟" and res.translation == ""
    assert tr.free.calls == []
