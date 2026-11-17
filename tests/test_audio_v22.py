"""v2.2 audio, dubbing and leftovers: per-process capture (fake streams), voice gender, MP3 decoding without
Media Foundation, robust Live installs (fake pip and HTTP), half-finished downloads, the Liquid Glass default,
GitHub links, and Settings callbacks after the window closed. Nothing here plays sound or opens a window."""

import json
import threading
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from dualyrics import REPO_URL, download, settings as S
from dualyrics.dub import audio, engines as eng_mod, piper, voices
from dualyrics.live import capture, deps, models, procloop

np = pytest.importorskip("numpy")
ROOT = Path(__file__).resolve().parent.parent
TONE = Path(__file__).parent / "data" / "tone.mp3"


# ---------------------------------------------------------------- resampler
def test_resampler_streams_without_drift_or_clicks():
    rate = 48000
    t = np.arange(rate) / rate
    x = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    r = procloop.Resampler(np, rate)
    parts = [r(x[i:i + 441]) for i in range(0, len(x), 441)]        # odd packet sizes, like WASAPI
    out = np.concatenate(parts)
    assert abs(len(out) - 16000) <= 2
    ref = np.sin(2 * np.pi * 440 * (np.arange(len(out)) * 3 / rate))
    assert float(np.max(np.abs(out[50:-50] - ref[50:-50]))) < 0.06   # smooth across packet edges
    one = procloop.Resampler(np, rate)(x)
    assert abs(len(one) - len(out)) <= 1


def test_resampler_passes_16k_through():
    x = np.ones(160, dtype=np.float32)
    assert procloop.Resampler(np, 16000)(x) is not None and len(procloop.Resampler(np, 16000)(x)) == 160


# ---------------------------------------------------------------- per-process capture (fake streams)
class FakeStream:
    opened: list = []

    def __init__(self, pid, include, np_=None, chunks=None, alive=True):
        self.pid, self.include = pid, include
        self.chunks = list(chunks or [np.full(800, 0.1, dtype=np.float32)] * 5)
        self.alive = alive
        self.closed = False
        FakeStream.opened.append((pid, include))

    def target_alive(self):
        return self.alive

    def read(self):
        if self.chunks:
            return self.chunks.pop(0), True
        return np.zeros(0, dtype=np.float32), False

    def close(self):
        self.closed = True


def run_capture(cap, seconds=0.35):
    cap.start()
    time.sleep(seconds)
    cap.stop()


def test_app_capture_records_the_target_tree_in_30ms_frames(monkeypatch):
    monkeypatch.setattr(capture.deps, "import_module", lambda name: np)
    FakeStream.opened = []
    frames = []
    cap = capture.ProcessCapture(lambda f, r: frames.append((len(f), r)), want=capture.APP, target=lambda: 4242,
                                 own_pid=7, opener=lambda pid, inc, n: FakeStream(pid, inc))
    run_capture(cap)
    assert FakeStream.opened[0] == (4242, True)
    assert cap.mode == capture.APP and cap.rate == 16000 and not cap.fallback
    loud = [f for f in frames if f[1] > 0.05]
    assert loud and all(n == 480 for n, _ in frames)           # 4000 samples -> eight 30 ms frames
    assert len(loud) == 4000 // 480
    assert any(r == 0.0 for _n, r in frames)                    # silence keeps flowing after the audio


def test_app_capture_without_an_app_excludes_our_own_process(monkeypatch):
    monkeypatch.setattr(capture.deps, "import_module", lambda name: np)
    FakeStream.opened = []
    cap = capture.ProcessCapture(lambda f, r: None, want=capture.APP, target=lambda: 0, own_pid=7,
                                 opener=lambda pid, inc, n: FakeStream(pid, inc))
    run_capture(cap, 0.15)
    assert FakeStream.opened[0] == (7, False) and cap.mode == capture.ALL_EXCEPT_OWN


def test_all_except_dualyrics_excludes_our_tree(monkeypatch):
    monkeypatch.setattr(capture.deps, "import_module", lambda name: np)
    FakeStream.opened = []
    cap = capture.make_capture(capture.ALL_EXCEPT_OWN, lambda f, r: None)
    assert isinstance(cap, capture.ProcessCapture)
    cap._opener = lambda pid, inc, n: FakeStream(pid, inc)
    run_capture(cap, 0.15)
    assert FakeStream.opened[0] == (cap.own_pid, False)


def test_capture_follows_a_new_app_and_skips_one_it_cannot_record(monkeypatch):
    monkeypatch.setattr(capture.deps, "import_module", lambda name: np)
    monkeypatch.setattr(capture, "RETARGET_EVERY", 0.02)
    FakeStream.opened = []
    target = {"pid": 100}

    def opener(pid, inc, n):
        if pid == 200:
            raise procloop.ProcessLoopbackError("access denied")
        return FakeStream(pid, inc)

    cap = capture.ProcessCapture(lambda f, r: None, want=capture.APP, target=lambda: target["pid"], own_pid=7,
                                 opener=opener)
    cap.start()
    time.sleep(0.1)
    target["pid"] = 200                       # e.g. a protected game: everything except Dualyrics instead
    time.sleep(0.2)
    cap.stop()
    assert FakeStream.opened[0] == (100, True)
    assert (7, False) in FakeStream.opened and cap._bad_pid == 200


def test_process_capture_falls_back_to_system_loopback(monkeypatch):
    monkeypatch.setattr(capture.deps, "import_module", lambda name: np)
    ran = []
    monkeypatch.setattr(capture.LoopbackCapture, "_run", lambda self: ran.append(True))

    def opener(pid, inc, n):
        raise procloop.ProcessLoopbackError("Windows build 19045 has no process loopback")

    cap = capture.ProcessCapture(lambda f, r: None, want=capture.ALL_EXCEPT_OWN, opener=opener)
    cap._run()
    assert ran == [True] and cap.mode == capture.SYSTEM and "19045" in cap.fallback


def test_make_capture_system_is_the_old_loopback():
    cap = capture.make_capture("system", lambda f, r: None)
    assert type(cap) is capture.LoopbackCapture and cap.want == capture.SYSTEM
    assert type(capture.make_capture("bogus", lambda f, r: None)) is capture.LoopbackCapture


def test_process_tree_root_and_app_pid():
    from dualyrics import procs

    table = {1: (0, "explorer.exe"), 10: (1, "chrome.exe"), 11: (10, "chrome.exe"), 12: (11, "chrome.exe"),
             20: (1, "game.exe"), procs.OWN_PID: (1, "python.exe")}
    assert procs.tree_root(12, table) == 10                  # tab / audio process -> the browser
    assert procs.tree_root(20, table) == 20
    assert procs.app_root_pid(12, table=table) == 10
    assert procs.app_root_pid(0, ["game.exe"], table=table) == 20
    assert procs.app_root_pid(0, ["missing.exe"], table=table) == 0
    assert procs.app_root_pid(procs.OWN_PID, table=table) == 0


def test_capture_setting_is_validated():
    d = S.validate({})
    assert d["live_capture"] == "all_except_dualyrics"
    assert S.validate({"live_capture": "app"})["live_capture"] == "app"
    assert S.validate({"live_capture": "mic"})["live_capture"] == "all_except_dualyrics"


@pytest.mark.skipif(not procloop.supported(), reason="needs Windows build 20348+")
def test_process_loopback_declarations_load():
    c = procloop._declare()
    assert {"IAudioClient", "IAudioCaptureClient", "Handler", "activate"} <= set(c)


# ---------------------------------------------------------------- dubbing: voice gender
def test_gender_picks_the_persian_voices():
    cfg = {"dub_gender": {"fa": "male"}}
    assert voices.gender_for(cfg, "fa") == "male" and voices.gender_for(cfg, "en") == "female"
    assert voices.edge_voice("auto", "fa", "male") == "fa-IR-FaridNeural"
    assert voices.edge_voice("auto", "fa", "female") == "fa-IR-DilaraNeural"
    assert voices.edge_voice("auto", "fa") == "fa-IR-DilaraNeural"               # default: female
    assert voices.edge_voice("fa-IR-DilaraNeural", "fa", "male") == "fa-IR-DilaraNeural"   # a chosen voice wins
    assert voices.gemini_voice("auto", "male") == "Charon" and voices.gemini_voice("auto", "female") == "Kore"
    assert voices.gemini_gender("Charon") == "male" and voices.gemini_gender("Kore") == "female"
    assert voices.short_name(voices.auto_voice("edge", "fa", "male")) == "Farid"
    assert voices.piper_voice("auto", "en", "male").endswith("en_US-ryan-medium")
    assert voices.piper_voice("auto", "fa", "female").endswith("fa_IR-amir-medium")   # only a male Persian voice


def test_every_edge_language_has_a_female_and_a_male_voice():
    for lang, names in voices.EDGE_VOICES.items():
        assert len(names) >= 2, lang
        assert all(voices.engine_of(n) == "edge" for n in names)
    assert set(voices.GEMINI_MALE) <= set(voices.GEMINI_VOICES)
    for by in voices.PIPER_BY_GENDER.values():
        assert set(by) == set(voices.GENDERS)
        assert all(voices.engine_of(p.rsplit("/", 1)[1]) == "piper" for p in by.values())


def test_gender_setting_is_validated():
    v = S.validate({"dub_gender": {"fa": "male", "en": "robot", "toolong": "male", 3: "male"}})
    assert v["dub_gender"] == {"fa": "male"}
    assert S.validate({"dub_gender": "male"})["dub_gender"] == {}


def test_engines_use_the_gender(monkeypatch):
    seen = {}
    monkeypatch.setattr(eng_mod.edge, "synthesize", lambda text, v, rate, proxy: seen.setdefault("v", v) and b"x")
    monkeypatch.setattr(eng_mod.edge, "mp3_seconds", lambda b: 1.0)
    tts = eng_mod.EdgeTTS(lambda: SimpleNamespace(trust_env=False, proxies={}))
    tts.speak("سلام", "fa", "auto", 1.0, {"dub_gender": {"fa": "male"}})
    assert seen["v"] == "fa-IR-FaridNeural"


def test_piper_uses_the_other_downloaded_voice_when_the_gender_has_none(monkeypatch):
    have = {voices.PIPER_VOICES["en"]}                      # lessac (female) only
    monkeypatch.setattr(eng_mod.piper, "voice_installed", lambda p: p in have)
    used = {}
    monkeypatch.setattr(eng_mod.piper, "synthesize", lambda text, path, lang, rate: used.setdefault("p", path)
                        and _wav())
    eng_mod.PiperTTS().speak("hi", "en", "auto", 1.0, {"dub_gender": {"en": "male"}})
    assert used["p"] == voices.PIPER_VOICES["en"]


def _wav() -> bytes:
    import io
    import wave

    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 100)
    return b.getvalue()


# ---------------------------------------------------------------- MP3 without Media Foundation
def test_mp3_decodes_with_dr_mp3():
    pytest.importorskip("miniaudio")
    pcm, rate = audio.decode_mp3(TONE.read_bytes())
    assert rate == 24000 and 0.3 < audio.pcm_seconds(pcm, rate) < 0.5


def test_mp3_falls_back_to_pyav_then_mci(monkeypatch):
    monkeypatch.setattr(audio, "_decode_miniaudio", lambda data, rate=24000: None)
    monkeypatch.setattr(audio, "_decode_mp3", lambda data, rate=24000: (b"\x00\x00" * 10, rate))
    assert audio.decode_mp3(b"mp3") == (b"\x00\x00" * 10, 24000)
    monkeypatch.setattr(audio, "_decode_mp3", lambda data, rate=24000: None)
    assert audio.decode_mp3(b"not an mp3") is None


def test_player_plays_decoded_mp3_through_waveout(monkeypatch):
    played = []
    monkeypatch.setattr(audio, "decode_mp3", lambda data: (b"\x01\x00" * 4, 24000))
    monkeypatch.setattr(audio.Player, "_play_pcm", lambda self, pcm, rate, vol: played.append((pcm, rate)) or True)
    monkeypatch.setattr(audio, "_mci", lambda cmd: pytest.fail("MCI must not be used"))
    assert audio.Player().play(audio.Clip("x", mp3=b"mp3", seconds=0.1)) is True
    assert played == [(b"\x01\x00" * 4, 24000)]


def test_miniaudio_is_a_bundled_dependency():
    assert "miniaudio" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "miniaudio" in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    spec = (ROOT / "Dualyrics.spec").read_text(encoding="utf-8")
    assert '"miniaudio"' in spec and '"_cffi_backend"' in spec


# ---------------------------------------------------------------- Liquid Glass and GitHub links
def test_liquid_glass_is_the_default_look():
    from dualyrics import templates

    assert templates.INSTALL_DEFAULT_ID == S.DEFAULT_TEMPLATE == "liquid_glass"
    assert templates.builtin("liquid_glass") is not None
    assert S.validate({})["template"] == "liquid_glass" and S.validate({})["template_chosen"] is False


def test_users_who_never_chose_a_look_get_liquid_glass():
    assert S.migrate_template_default({"template": "cinema"})["template"] == "liquid_glass"
    assert S.migrate_template_default({"template": "neon"})["template"] == "neon"
    assert S.migrate_template_default({"template": "neon", "template_chosen": True})["template"] == "neon"
    assert S.migrate_template_default({"template": "cinema", "template_chosen": False})["template"] == "liquid_glass"


def test_github_links_exist():
    from dualyrics import i18n

    assert REPO_URL == "https://github.com/morpheusadam/Dualyrics"
    tray = (ROOT / "dualyrics" / "tray.py").read_text(encoding="utf-8")
    assert '"open_github"' in tray and "GitHub" in i18n.t("tray_github")
    assert "os.startfile(REPO_URL)" in (ROOT / "dualyrics" / "app.py").read_text(encoding="utf-8")
    about = (ROOT / "dualyrics" / "settings_window.py").read_text(encoding="utf-8")
    assert 't("about_star")' in about and "ISSUES_URL" in about and "RELEASES_URL" in about
    iss = (ROOT / "installer" / "Dualyrics.iss").read_text(encoding="utf-8")
    assert f'#define RepoUrl "{REPO_URL}"' in iss
    for key in ("AppPublisherURL={#RepoUrl}", "AppSupportURL={#RepoUrl}/issues", "AppUpdatesURL={#RepoUrl}/releases"):
        assert key in iss


# ---------------------------------------------------------------- resumable downloads
class FlakyResponse:
    def __init__(self, body: bytes, status: int = 200, drop_after: int | None = None):
        self.body, self.status_code, self.drop = body, status, drop_after
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 416:
            import requests

            raise requests.HTTPError(response=SimpleNamespace(status_code=self.status_code))

    def iter_content(self, n):
        import requests

        data = self.body if self.drop is None else self.body[:self.drop]
        for i in range(0, len(data), 4):
            yield data[i:i + 4]
        if self.drop is not None:
            raise requests.exceptions.ChunkedEncodingError("connection reset by the proxy")


class FlakySession:
    def __init__(self, data: bytes, drops=(10,)):
        self.data, self.drops, self.ranges = data, list(drops), []

    def get(self, url, headers=None, **kw):
        rng = (headers or {}).get("Range", "")
        self.ranges.append(rng)
        start = int(rng[6:-1]) if rng else 0
        if start >= len(self.data):
            return FlakyResponse(b"", 416)
        drop = self.drops.pop(0) if self.drops else None
        r = FlakyResponse(self.data[start:], 206 if start else 200, drop)
        return r


def test_download_resumes_after_the_proxy_drops_the_connection(tmp_path):
    import hashlib

    data = bytes(range(256)) * 4
    s = FlakySession(data, drops=(10, 300))
    out = download.fetch(s, "https://x/f.whl", tmp_path / "f.whl", sha256=hashlib.sha256(data).hexdigest(),
                         sleep=lambda s: None)
    assert out.read_bytes() == data and not (tmp_path / "f.whl.part").exists()
    assert s.ranges[0] == "" and s.ranges[1].startswith("bytes=") and len(s.ranges) == 3


def test_download_with_a_wrong_checksum_is_never_kept(tmp_path):
    s = FlakySession(b"abcdefgh" * 10, drops=())
    with pytest.raises(download.DownloadError):
        download.fetch(s, "https://x/f.whl", tmp_path / "f.whl", sha256="0" * 64, sleep=lambda s: None)
    assert not (tmp_path / "f.whl").exists() and not (tmp_path / "f.whl.part").exists()


# ---------------------------------------------------------------- robust heavy installs (fake pip)
def test_pip_flags_depend_on_the_pip_version():
    assert deps.robust_flags((25, 0, 1)) == ["--retries", "10", "--timeout", "60"]
    assert "--resume-retries" in deps.robust_flags((25, 1))
    cmd = deps.install_command(True, "http://proxy:1", pip_ver=(25, 3), py=["py"])
    assert cmd[:4] == ["py", "-m", "pip", "install"] and "--resume-retries" in cmd
    assert cmd[-2:] == list(deps.GPU.values()) and cmd[cmd.index("--proxy") + 1] == "http://proxy:1"


class FakePip:
    def __init__(self, report: dict | None, local_ok=True, online_ok=True):
        self.report, self.local_ok, self.online_ok, self.calls = report, local_ok, online_ok, []
        self.pending_seen: list[set] = []

    def __call__(self, cmd, env, on_line, cancelled):
        self.calls.append(cmd)
        self.pending_seen.append(deps.pending())
        if "--dry-run" in cmd:
            if self.report is None:
                return 1, "ERROR: no such option: --report"
            Path(cmd[cmd.index("--report") + 1]).write_text(json.dumps(self.report), encoding="utf-8")
            return 0, ""
        if "--no-index" in cmd:
            return (0, "Successfully installed") if self.local_ok else (1, "ERROR: no matching distribution")
        return (0, "Successfully installed") if self.online_ok else (1, "ERROR: read timed out")


@pytest.fixture
def fake_install(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "_python", lambda: ["py"])
    monkeypatch.setattr(deps, "pip_version", lambda py: (25, 0))
    monkeypatch.setattr(deps, "_verify_imports", lambda py, mods: "")
    monkeypatch.setattr(deps, "wheels_dir", lambda: tmp_path / "wheels")
    monkeypatch.setattr(deps, "_pending_file", lambda: tmp_path / "install-pending.json")
    monkeypatch.setattr(deps, "_has", lambda mod: True)
    fetched = []

    def fetch(session, url, dest, progress, cancelled, sha256=""):
        fetched.append((url, sha256))
        Path(dest).write_bytes(b"wheel")
        progress(5, 5)
        return Path(dest)

    monkeypatch.setattr(download, "fetch", fetch)
    return fetched


REPORT = {"install": [
    {"download_info": {"url": "https://files.example/packages/nvidia_cudnn_cu12-9.1-py3-none-win_amd64.whl",
                       "archive_info": {"hashes": {"sha256": "a" * 64}}}},
    {"download_info": {"url": "https://files.example/packages/numpy-2.1-cp312-cp312-win_amd64.whl",
                       "archive_info": {"hash": "sha256=" + "b" * 64}}},
]}


def test_install_downloads_big_wheels_itself_then_installs_offline(monkeypatch, fake_install, tmp_path):
    pip = FakePip(REPORT)
    monkeypatch.setattr(deps, "_run_pip", pip)
    lines = []
    ok, _msg = deps.install(True, "none", "", on_line=lines.append)
    assert ok
    assert [u.rsplit("/", 1)[1] for u, _ in fake_install] == ["nvidia_cudnn_cu12-9.1-py3-none-win_amd64.whl",
                                                              "numpy-2.1-cp312-cp312-win_amd64.whl"]
    assert fake_install[1][1] == "b" * 64
    assert "--dry-run" in pip.calls[0] and "--no-index" in pip.calls[1] and len(pip.calls) == 2
    assert pip.calls[1][pip.calls[1].index("--find-links") + 1] == str(tmp_path / "wheels")
    assert "--retries" in pip.calls[1]
    assert set(deps.REQUIRED) <= pip.pending_seen[1]          # pending while pip runs ...
    assert deps.pending() == set()                            # ... cleared once it worked
    assert not list((tmp_path / "wheels").glob("*.whl"))      # the downloaded wheels are removed
    assert any("Downloading nvidia_cudnn" in ln for ln in lines)


def test_install_falls_back_to_pip_downloads(monkeypatch, fake_install):
    pip = FakePip(None)                                       # old pip: no --report
    monkeypatch.setattr(deps, "_run_pip", pip)
    ok, _ = deps.install(False, "none", "")
    assert ok and not fake_install and "--no-index" not in pip.calls[-1] and len(pip.calls) == 2
    pip = FakePip(REPORT, local_ok=False)                     # local files rejected: online with retries
    monkeypatch.setattr(deps, "_run_pip", pip)
    ok, _ = deps.install(False, "none", "")
    assert ok and len(pip.calls) == 3 and "--no-index" not in pip.calls[-1]


def test_a_failed_or_cancelled_reinstall_keeps_a_working_install(monkeypatch, fake_install):
    pip = FakePip(REPORT, local_ok=False, online_ok=False)
    monkeypatch.setattr(deps, "_run_pip", pip)
    ok, msg = deps.install(False, "none", "")                  # pip fails, but the old copy still loads
    assert not ok and "timed out" in msg and deps.pending() == set() and deps.status().ready
    assert set(deps.REQUIRED) <= pip.pending_seen[1]          # it was pending while pip installed
    seen = []

    def cancel_while_resolving(cmd, env, on_line, cancelled):
        seen.append(deps.pending())
        return -1, "cancelled"

    monkeypatch.setattr(deps, "_run_pip", cancel_while_resolving)
    assert deps.install(False, "none", "") == (False, "cancelled")
    assert seen == [set()] and deps.pending() == set() and deps.status().ready


def test_a_half_finished_install_never_counts(monkeypatch, fake_install):
    monkeypatch.setattr(deps, "_run_pip", FakePip(REPORT, local_ok=False, online_ok=False))
    monkeypatch.setattr(deps, "_verify_imports", lambda py, mods: "ModuleNotFoundError: numpy")
    ok, msg = deps.install(False, "none", "")
    assert not ok and "timed out" in msg
    assert set(deps.REQUIRED) <= deps.pending()
    assert not deps.status().ready and not deps.has("numpy")   # files may exist, but they do not count
    monkeypatch.setattr(deps, "_run_pip", FakePip(REPORT))
    monkeypatch.setattr(deps, "_verify_imports", lambda py, mods: "ImportError: DLL load failed")
    ok, msg = deps.install(False, "none", "")
    assert not ok and "does not load" in msg and not deps.status().ready
    monkeypatch.setattr(deps, "_verify_imports", lambda py, mods: "")
    assert deps.install(False, "none", "")[0] and deps.status().ready


def test_report_files_refuse_what_the_app_cannot_fetch():
    assert deps._report_files({"install": [{"download_info": {"url": "file:///C:/x/pkg-1.whl"}}]}) is None
    assert deps._report_files({"install": [{"download_info": {"url": "https://h/../evil.exe"}}]}) is None
    assert deps._report_files({}) == []


def test_half_downloaded_model_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    d = tmp_path / "faster-whisper-tiny"
    d.mkdir()
    for f, n in (("config.json", 10), ("model.bin", 40), ("tokenizer.json", 10), ("vocabulary.txt", 10)):
        (d / f).write_bytes(b"x" * n)
    sizes = {"config.json": 10, "model.bin": 40, "tokenizer.json": 10, "vocabulary.txt": 10}
    (d / ".complete").write_text(json.dumps({"files": list(sizes), "sizes": sizes}), encoding="utf-8")
    assert models.is_installed("tiny")
    (d / "model.bin").write_bytes(b"x" * 20)                 # truncated after the fact
    assert not models.is_installed("tiny")
    (d / ".complete").write_text(json.dumps({"files": list(sizes)}), encoding="utf-8")   # v2.1 marker
    assert not models.is_installed("tiny")                   # a 20-byte model.bin is no model
    (d / ".complete").write_text("not json", encoding="utf-8")
    assert not models.is_installed("tiny")


def test_piper_with_a_stopped_unpack_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(piper, "root", lambda: tmp_path)
    (tmp_path / "piper" / "espeak-ng-data").mkdir(parents=True)
    (tmp_path / "piper" / "piper.exe").write_bytes(b"MZ")
    assert piper.installed()
    (tmp_path / ".unpacking").write_text("unpacking", encoding="utf-8")
    assert not piper.installed()


# ---------------------------------------------------------------- Settings callbacks after close
class FakeWin:
    def __init__(self):
        self.exists = True

    def winfo_exists(self):
        return self.exists


def make_settings_window():
    from dualyrics.settings_window import SettingsWindow

    sw = SettingsWindow.__new__(SettingsWindow)
    q: list = []
    sw.app = SimpleNamespace(call_soon=q.append)
    sw.win, sw.epoch, sw.jobs = FakeWin(), 0, set()
    return sw, q


def drain(q, n, timeout=2.0):
    """Run the first ``n`` queued Tk-thread calls once they arrived."""
    end = time.time() + timeout
    while len(q) < n and time.time() < end:
        time.sleep(0.01)
    for fn in list(q):
        fn()
    q.clear()


def test_background_result_after_close_touches_no_widget():
    sw, q = make_settings_window()
    gate = threading.Event()
    got, after = [], []
    sw._run_bg(lambda: gate.wait(2) and "ok", got.append, after=after.append)
    sw.epoch += 1                       # the window closed (or another page was shown) meanwhile
    sw.win = None
    gate.set()
    drain(q, 2)
    assert got == [] and after == ["ok"]


def test_ui_updates_drop_tclerror_and_stale_pages():
    sw, q = make_settings_window()

    def boom():
        raise tk.TclError('invalid command name ".!ctklabel"')

    sw.ui(boom)
    q.pop(0)()                          # swallowed, not raised
    seen = []
    sw.ui(lambda: seen.append(1), epoch=0)
    sw.epoch = 1
    q.pop(0)()
    assert seen == []
    sw.ui(lambda: seen.append(2))
    sw.win.exists = False
    q.pop(0)()
    assert seen == []
