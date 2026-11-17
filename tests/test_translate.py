from dualyrics import langs
from dualyrics.translate import _unique, build_prompt, fit_lines


def test_exact_count_strips_own_numbering_only():
    assert fit_lines(3, ["1. one", "2. two", "3. three"]) == ["one", "two", "three"]
    # a number that is not the position stays (lyrics may start with a number)
    assert fit_lines(2, ["99. problems", "two"]) == ["99. problems", "two"]


def test_numbered_answer_with_wrong_count_is_placed_by_number():
    out = fit_lines(4, ["1. a", "2. b", "3. c", "4. d", "5. extra"])
    assert out == ["a", "b", "c", "d"]


def test_numbered_objects():
    out = fit_lines(3, [{"n": 3, "t": "c"}, {"n": 1, "t": "a"}, {"n": 2, "t": "b"}])
    assert out == ["a", "b", "c"]


def test_blank_items_removed_when_that_fixes_count():
    assert fit_lines(2, ["a", "", "b", " "]) == ["a", "b"]


def test_wrong_count_untrusted_unless_forced():
    assert fit_lines(3, ["a", "b"]) is None
    assert fit_lines(3, ["a", "b"], force=True) == ["a", "b", ""]
    assert fit_lines(2, ["a", "b", "c", "d"], force=True) == ["a", "b"]
    assert fit_lines(2, None) is None
    assert fit_lines(0, []) == []


def test_unique_lines_mapping():
    uniq, mapping = _unique(["la", "", "chorus", "la", "♪"])
    assert uniq == ["la", "chorus"]
    assert mapping == [0, -1, 1, 0, -1]


def test_prompt_mentions_count_and_language():
    p = build_prompt(["a", "b"], "T", "A", "fa", "natural")
    assert "exactly 2 strings" in p and "Persian" in p and "1. a" in p


def test_language_guess_and_rtl():
    assert langs.guess_language("من تو را دوست دارم و این آهنگ برای توست") == "fa"
    assert langs.guess_language("사랑해요 정말로 당신을 사랑해요") == "ko"
    assert langs.guess_language("I love you more than words can say") is None
    assert langs.is_rtl_text("سلام دنیا") and not langs.is_rtl_text("hello")
    assert langs.same_language("zh-CN", "zh") and langs.base_code("iw") == "he"
