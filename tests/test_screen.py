"""On-screen subtitles: change detection, cleaning, debounce, dedupe and the reader loop with fake frames."""

from PIL import Image, ImageDraw

from dualyrics import settings as S
from dualyrics.screen.engine import Readers, ScreenReader, Target
from dualyrics.screen.logic import (ChangeDetector, StaticLines, SubtitleTracker, clean_text, garbage, similar,
                                    ui_label)


def frame(text: str = "", bg: int = 30, noise: int = 0) -> Image.Image:
    """A fake subtitle region: dark picture (``noise`` moves a grey shape, like a film), white text blocks
    standing in for the letters of ``text``."""
    img = Image.new("RGB", (640, 120), (bg, bg, bg))
    d = ImageDraw.Draw(img)
    if noise:
        d.ellipse((noise * 7 % 500, 10, noise * 7 % 500 + 90, 100), fill=(90, 80, 70))
    x = 40
    for i, ch in enumerate(text):
        if ch != " ":
            h = 20 + (ord(ch) * 7 % 16)
            d.rectangle((x, 90 - h, x + 9, 90), fill=(255, 255, 255))
        x += 13
    return img


# ---------------------------------------------------------------- change detection
def test_same_frame_or_moving_film_is_not_a_change():
    det = ChangeDetector(max_interval=99)
    assert det.check(frame("Hello there"), 0.0)                 # the first frame is always read
    assert not det.check(frame("Hello there"), 0.3)
    assert not det.check(frame("Hello there", noise=5), 0.6)    # the film behind moved, the subtitle did not


def test_new_subtitle_is_a_change_and_the_safety_interval_reads_again():
    det = ChangeDetector(max_interval=1.5)
    det.check(frame("Hello there"), 0.0)
    assert det.check(frame("Where are you going"), 0.3)
    assert not det.check(frame("Where are you going"), 0.6)
    assert det.check(frame("Where are you going"), 2.0)        # at least every 1.5 s
    det.reset()
    assert det.check(frame("Where are you going"), 2.1)


# ---------------------------------------------------------------- text cleaning
def test_clean_text_and_similarity():
    assert clean_text("  - Where are\n you going?  |") == "Where are you going?"
    assert clean_text("1:23 / 4:56") == "" and clean_text("··") == ""
    assert similar("Where are you going?", "Where are yau going")       # OCR changed a letter
    assert not similar("Where are you going?", "I saw a boat over there")


def test_ui_labels_and_ocr_noise_are_not_subtitles():
    assert ui_label("ACTIVE MARKERS") and ui_label("@ MOVE  ZOOM") and ui_label("THE PATH")
    assert not ui_label("I saw a dock back there, maybe there's a boat.")
    assert not ui_label("NO, WAIT! DON'T GO IN THERE, BOY!")   # a shouted line is long enough
    assert garbage("A p æ-j9i:l CIS b GJ , pan 14-i91 a-15-uJl")
    assert not garbage("Atreus: I saw a dock back there, maybe there's a boat we can use.")
    assert not garbage("你好，我们走吧") and not garbage("کجا داری می‌ری؟")


def test_static_lines_drop_hud_text_that_never_changes():
    st = StaticLines(static_after=10, forget_after=30)
    hud = "Press any key to continue the journey"
    talk = ["Where are you going?", "I saw a boat back there.", "We should rest now, boy.",
            "The mountain is close.", "Do not touch that!", "Father, look up there."]
    for t in range(0, 12):
        out = st.feed([hud, talk[t // 2]], float(t))
    assert out == ["Father, look up there."]           # the HUD line stayed 11 s: dropped
    assert st.feed(["Kratos looks at the boat"], 12.0) == ["Kratos looks at the boat"]


# ---------------------------------------------------------------- debounce, dedupe, activity
def feed_all(tr, readings, start=0.0, step=0.3, trusted=False):
    events = []
    t = start
    for r in readings:
        ev = tr.feed(r, t, trusted)
        if ev:
            events.append((round(t, 1), ev.kind, ev.text))
        t += step
    return events, t


def test_text_must_be_stable_before_it_shows():
    tr = SubtitleTracker(stable=2)
    events, _ = feed_all(tr, ["Where ar", "Where are you going?", "Where are you going?"])
    assert [e[1:] for e in events] == [("show", "Where are you going?")]


def test_ocr_variations_of_the_same_line_are_one_caption_and_clear_needs_two_empty_frames():
    tr = SubtitleTracker(stable=2, clear=2)
    readings = ["Where are you going?", "Where are you going?", "Where are yau going?", "Where are you going",
                "", "Where are you going?", "", ""]
    events, _ = feed_all(tr, readings)
    kinds = [e[1] for e in events]
    assert kinds == ["show", "clear"]                  # one flicker frame did not clear it


def test_a_line_that_flickers_off_and_on_resumes_instead_of_a_new_caption():
    tr = SubtitleTracker(stable=2, clear=2)
    events, _ = feed_all(tr, ["Hello Kratos", "Hello Kratos", "", "", "Hello Kratos", "Hello Kratos"])
    assert [e[1] for e in events] == ["show", "clear", "resume"]


def test_activation_needs_two_different_lines_or_a_trusted_reader():
    tr = SubtitleTracker(stable=2)
    feed_all(tr, ["The first line here", "The first line here"])
    assert not tr.active(0.6) and not tr.has_text(0.6)
    feed_all(tr, ["", "", "The second line now", "The second line now"], start=0.6)
    assert tr.active(1.8) and tr.has_text(1.8)
    feed_all(tr, ["", ""], start=1.8)                             # the line left the screen at 2.1
    assert tr.has_text(2.1 + 5) and not tr.has_text(2.1 + 9)     # held 8 s between lines
    trusted = SubtitleTracker()
    feed_all(trusted, ["Captions from the page"], trusted=True)
    assert trusted.active(0.1)


def test_text_that_stays_too_long_is_a_label_and_is_ignored_afterwards():
    tr = SubtitleTracker(stable=2, static_after=15)
    feed_all(tr, ["Subscribe to the channel"] * 60, step=0.3)    # 18 s
    assert tr.current == ""
    events, _ = feed_all(tr, ["Subscribe to the channel"] * 4, start=20)
    assert events == []


# ---------------------------------------------------------------- the reader loop with fake frames
class FakeOcr:
    """Reads the fake frame's text from a table (like a perfect OCR), counting how often it was asked."""

    def __init__(self, table):
        self.table, self.calls = table, 0

    def recognize_lines(self, img):
        self.calls += 1
        key = img.tobytes()
        return [self.table.get(key, "")] if self.table.get(key) else []

    def close(self):
        pass


class FakeTr:
    def __init__(self):
        self.seen = []

    def to_english(self, text, cfg):
        return text, "en"

    def translate(self, text, cfg, ctx, source="", where="", title=""):
        self.seen.append(text)
        return f"[fa] {text}", "done"


def make_reader(frames, table, cfg=None):
    cfg = cfg or {**S.defaults(), "target_language": "fa"}
    state = {"i": 0}
    ocr = FakeOcr(table)
    readers = Readers(uia=lambda: None, ocr=lambda lang: ocr, client_box=lambda hwnd: (0, 0, 1000, 500),
                      grab=lambda box: frames[min(state["i"], len(frames) - 1)])
    tr = FakeTr()
    r = ScreenReader(lambda: cfg, tr, readers, translate_async=False)
    r._target, r._want = Target(hwnd=1, key="vlc", kind="video"), True
    return r, state, ocr, tr


def test_reader_shows_translated_subtitles_and_reads_only_changed_frames():
    lines = ["Where are you going?", "I saw a boat over there."]
    frames = [frame("")] * 2 + [frame(lines[0])] * 6 + [frame(lines[1], noise=k) for k in range(6)] + [frame("")] * 4
    table = {f.tobytes(): text for f, text in ((frame(lines[0]), lines[0]),)}
    table.update({frame(lines[1], noise=k).tobytes(): lines[1] for k in range(6)})
    r, state, ocr, tr = make_reader(frames, table)
    views = []
    for i in range(len(frames)):
        state["i"] = i
        now = i * 0.3
        r.step(now)
        views.append(r.view(now=now))
    shown = [(v.original, v.translation) for v in views if v.visible]
    assert (lines[1], f"[fa] {lines[1]}") in shown          # active after the second line, translated
    assert tr.seen == lines                                  # each line translated once (dedupe)
    assert ocr.calls < len(frames)                           # unchanged frames were not read again
    assert r.has_text(now) and not views[-1].visible         # gone from the screen, still "has subtitles"


def test_reader_skips_translation_when_translations_are_off():
    cfg = {**S.defaults(), "target_language": "fa", "show_translation": False}
    lines = ["First subtitle line", "Second subtitle line"]
    frames = [frame(lines[0])] * 3 + [frame(lines[1])] * 3
    table = {frame(t).tobytes(): t for t in lines}
    r, state, _ocr, tr = make_reader(frames, table, cfg)
    for i in range(len(frames)):
        state["i"] = i
        r.step(i * 0.3)
    v = r.view(now=1.6)
    assert v.visible and v.original == lines[1] and v.translation == "" and tr.seen == []


def test_a_more_complete_reading_updates_the_caption_instead_of_a_new_one():
    tr = SubtitleTracker(stable=2)
    events, _ = feed_all(tr, ["Mimir, how many stories", "Mimir, how many stories",
                              "Mimir, how many stories have you told about Odin and Thor?",
                              "Mimir, how many stories have you told about Odin and Thor?"])
    assert [e[1] for e in events] == ["show", "update"]
    assert tr.current.endswith("Odin and Thor?")


def test_reader_retranslates_an_updated_caption_and_keeps_its_id():
    a, b, c = "The first subtitle", "Mimir, how many stories", "Mimir, how many stories have you told?"
    frames = [frame(a)] * 3 + [frame("")] * 2 + [frame(b)] * 3 + [frame(c)] * 3
    table = {frame(x).tobytes(): x for x in (a, b, c)}
    r, state, _ocr, tr = make_reader(frames, table)
    ids = set()
    for i in range(len(frames)):
        state["i"] = i
        r.step(i * 0.3)
        v = r.view(now=i * 0.3)
        if v.visible and v.original in (b, c):
            ids.add(v.caption_id)
    assert tr.seen == [a, b, c] and len(ids) == 1
    assert r.view(now=3.0).translation == f"[fa] {c}"
