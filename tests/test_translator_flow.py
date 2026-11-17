"""Translator flow with a fake Gemini: retry on wrong line count, then chunking; same-language skip."""

from dualyrics import settings as S
from dualyrics.translate import CHUNK, Translator


class FakeGemini:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def ask(self, keys, models, prompt):
        self.prompts.append(prompt)
        ans = self.answers.pop(0)
        return ans(prompt) if callable(ans) else ans

    def reset_cooldowns(self):
        pass


def cfg(**kw):
    c = S.defaults()
    c.update(provider="gemini", gemini_keys=["k"], target_language="fa", fallback_free=False)
    c.update(kw)
    return c


def make(answers):
    tr = Translator(lambda: None)
    tr.gemini = FakeGemini(answers)
    return tr


def test_repeated_lines_sent_once_and_mapped_back():
    tr = make([{"source_language": "en", "lines": ["A", "B"]}])
    out = tr.translate(["a", "b", "", "a"], "t", "x", cfg())
    assert out.lines == ["A", "B", "", "A"] and out.provider == "gemini"
    assert "exactly 2 strings" in tr.gemini.prompts[0]


def test_wrong_count_retries_once():
    tr = make([{"lines": ["only one"]}, {"lines": ["A", "B", "C"]}])
    out = tr.translate(["a", "b", "c"], "t", "x", cfg())
    assert out.lines == ["A", "B", "C"]
    assert "previous answer had 1 lines" in tr.gemini.prompts[1]


def test_falls_back_to_chunks():
    n = CHUNK + 5
    src = [f"line {i}" for i in range(n)]

    def chunk_answer(prompt):
        count = int(prompt.split("has exactly ")[1].split()[0])
        return {"lines": [f"T{i}" for i in range(count)]}

    tr = make([{"lines": ["x"]}, {"lines": ["x"]}, chunk_answer, chunk_answer])
    out = tr.translate(src, "t", "x", cfg())
    assert len(out.lines) == n and all(out.lines)
    assert len(tr.gemini.prompts) == 4


def test_skip_when_model_says_same_language():
    tr = make([{"source_language": "fa", "lines": ["x", "y"]}])
    out = tr.translate(["hello there", "general kenobi"], "t", "x", cfg())
    assert out.skipped and out.lines == ["", ""]


def test_script_guess_skips_without_calling_provider():
    tr = make([])
    out = tr.translate(["من تو را دوست دارم", "این آهنگ برای توست"], "t", "x", cfg())
    assert out.skipped and tr.gemini.prompts == []
