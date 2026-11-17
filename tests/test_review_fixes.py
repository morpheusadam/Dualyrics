"""Regression tests for bugs found in review: session flapping, transient HTTP errors, cache temp files."""

import asyncio
import datetime as dt
import threading
from types import SimpleNamespace

import requests

from dualyrics import net
from dualyrics.cache import Cache
from dualyrics.lyrics import LyricsClient
from dualyrics.media import MediaWatcher


# ---------------------------------------------------------------- media: same app id, two sessions
class _Session:
    def __init__(self, app: str, title: str, playing: bool) -> None:
        self.source_app_user_model_id = app
        self._title, self._playing = title, playing

    def get_playback_info(self):
        return SimpleNamespace(playback_status=4 if self._playing else 5)

    async def try_get_media_properties_async(self):
        return SimpleNamespace(title=self._title, artist="Artist", album_title="")

    def get_timeline_properties(self):
        return SimpleNamespace(position=dt.timedelta(seconds=10), end_time=dt.timedelta(seconds=200),
                               last_updated_time=dt.datetime.now(dt.timezone.utc))


def test_two_sessions_of_one_app_do_not_flap():
    paused = _Session("chrome", "Paused tab", False)
    playing = _Session("chrome", "Playing tab", True)
    mgr = SimpleNamespace(get_sessions=lambda: [paused, playing])  # paused one listed first
    w = MediaWatcher(lambda: ["chrome"])
    seen = []
    for _ in range(12):
        asyncio.run(w._poll(mgr))
        seen.append(w.track.title)
    assert set(seen[1:]) == {"Playing tab"}, seen


# ---------------------------------------------------------------- network error classification
def _http_error(status: int) -> requests.HTTPError:
    r = requests.Response()
    r.status_code = status
    return requests.HTTPError(response=r)


def test_server_errors_are_transient():
    assert net.is_network_error(_http_error(503))
    assert net.is_network_error(_http_error(429))
    assert not net.is_network_error(_http_error(400))
    assert net.is_network_error(requests.ConnectionError())


class _FlakySession:
    """LRCLIB answering 404 to every get and 503 to every search."""

    def get(self, url, params=None, timeout=None):
        r = requests.Response()
        r.status_code = 404 if url.endswith("/get") else 503
        r.url = url
        return r


def test_lrclib_outage_is_not_cached_as_not_found():
    res = LyricsClient(_FlakySession()).fetch("Song", "Artist", "", 200)
    assert not res.found
    assert res.network_error  # the app retries later instead of caching "no lyrics" for a day


# ---------------------------------------------------------------- cache: concurrent writers
def test_concurrent_cache_writes(tmp_path):
    c = Cache(tmp_path)
    errors = []

    def work(n: int) -> None:
        try:
            for i in range(30):
                c.put_lyrics("a|b", {"found": True, "plain": [str(n), str(i)]})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert c.get_lyrics("a|b")["found"]
    assert not list((tmp_path / "lyrics").glob("*.tmp"))


# ---------------------------------------------------------------- media: SMTC not ready at sign-in
def test_media_watcher_retries_after_failure(monkeypatch):
    import dualyrics.media as media

    monkeypatch.setattr(media, "RESTART_DELAY", 0.01)
    w = MediaWatcher(lambda: [])
    calls = []

    async def loop():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("SMTC not ready")

    w._loop = loop
    w._run()
    assert len(calls) == 3  # failed twice, then ran
