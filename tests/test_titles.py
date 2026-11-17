import pytest

from dualyrics.lyrics import candidates, clean_artist, clean_title, split_artist_title


@pytest.mark.parametrize("raw,expected", [
    ("Let It Be - Remastered 2009", "Let It Be"),
    ("Bohemian Rhapsody (Remastered 2011)", "Bohemian Rhapsody"),
    ("Hotel California (Live at the Forum, 1976)", "Hotel California"),
    ("Old Town Road (feat. Billy Ray Cyrus)", "Old Town Road"),
    ("Old Town Road feat. Billy Ray Cyrus", "Old Town Road"),
    ("Song [Official Music Video]", "Song"),
    ("Song (Official Video) [HD]", "Song"),
    ("Song (Lyrics)", "Song"),
    ("Song | 90s Vibes", "Song"),
    ("Mr. Brightside - Radio Edit", "Mr. Brightside"),
    ("Yesterday", "Yesterday"),
    ("99 Problems", "99 Problems"),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_clean_title_never_empty():
    assert clean_title("(Live)") == "(Live)"


@pytest.mark.parametrize("raw,expected", [
    ("Adele - Topic", "Adele"),
    ("AdeleVEVO", "Adele"),
    ("Drake, Rihanna", "Drake"),
    ("Snoop Dogg feat. 2Pac", "Snoop Dogg"),
])
def test_clean_artist(raw, expected):
    assert clean_artist(raw) == expected


def test_split_artist_title_for_youtube():
    assert split_artist_title("Rick Astley - Never Gonna Give You Up (Official Music Video)") == (
        "Rick Astley", "Never Gonna Give You Up")
    assert split_artist_title("NoDashHere") is None


def test_candidates_prefer_split_for_channel_names():
    c = candidates("Rick Astley - Never Gonna Give You Up (Official Video)", "RickAstleyVEVO")
    assert c[0] == ("Never Gonna Give You Up", "Rick Astley")
    assert len(c) == len({(a.lower(), b.lower()) for a, b in c})
