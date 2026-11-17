from dualyrics.lyrics import parse_lrc


def test_basic_and_sorted():
    lines = parse_lrc("[00:12.50]Second\n[00:01.00]First\n")
    assert [(ln.time, ln.text) for ln in lines] == [(1.0, "First"), (12.5, "Second")]


def test_multiple_timestamps_and_metadata():
    lrc = "[ar:Someone]\n[ti:Song]\n[00:10.00][01:10.00]Chorus\n[00:20]Verse"
    lines = parse_lrc(lrc)
    assert [(ln.time, ln.text) for ln in lines] == [(10.0, "Chorus"), (20.0, "Verse"), (70.0, "Chorus")]


def test_three_digit_fraction_and_colon_separator():
    lines = parse_lrc("[00:01.123]a\n[00:02:50]b")
    assert lines[0].time == 1.123
    assert lines[1].time == 2.5


def test_offset_tag_moves_lyrics_earlier():
    lines = parse_lrc("[offset:+500]\n[00:10.00]x")
    assert lines[0].time == 9.5


def test_word_level_tags_and_empty_lines():
    lines = parse_lrc("[00:05.00]<00:05.00>Hello <00:05.50>world\n[00:07.00]\n")
    assert lines[0].text == "Hello world"
    assert lines[1].text == ""


def test_empty_input():
    assert parse_lrc("") == []
    assert parse_lrc("no timestamps here") == []
