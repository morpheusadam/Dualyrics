import json

from dualyrics import settings as S


def test_defaults_are_valid():
    d = S.defaults()
    assert S.validate(d) == d


def test_bad_values_reset_and_clamped():
    v = S.validate({"size_original": 500, "opacity": "high", "align": "diagonal", "color_bg": "red",
                    "width": -5, "show_next": "yes", "provider": "paid", "unknown_key": 1})
    d = S.defaults()
    assert v["size_original"] == 96
    assert v["opacity"] == d["opacity"]
    assert v["align"] == d["align"]
    assert v["color_bg"] == d["color_bg"]
    assert v["width"] == 300
    assert v["show_next"] is d["show_next"]
    assert v["provider"] == d["provider"]
    assert "unknown_key" not in v


def test_not_a_dict():
    assert S.validate(None) == S.defaults()
    assert S.validate([1, 2]) == S.defaults()


def test_lists_and_strings():
    v = S.validate({"preferred_players": "Spotify, chrome,, spotify", "gemini_models": "", "style": "   "})
    assert v["preferred_players"] == ["spotify", "chrome"]
    assert v["gemini_models"] == S.DEFAULT_MODELS
    assert v["style"] == S.DEFAULT_STYLE


def test_proxy_validation():
    assert S.validate({"proxy_mode": "custom", "proxy_url": "http://10.0.0.1:8080"})["proxy_mode"] == "custom"
    bad = S.validate({"proxy_mode": "custom", "proxy_url": "not a url"})
    assert bad["proxy_url"] == "" and bad["proxy_mode"] == "system"
    assert S.valid_proxy_url("socks5://host:1080")


def test_hotkeys_and_positions():
    mon = "\\\\.\\DISPLAY1"
    v = S.validate({"hotkey_toggle": "Ctrl + Alt + L", "hotkey_offset_up": "up",
                    "positions": {mon: {"x": 10, "y": 20, "anchor": "top"}, "bad": {"x": "1"}}})
    assert v["hotkey_toggle"] == "ctrl+alt+l"
    assert v["hotkey_offset_up"] == S.defaults()["hotkey_offset_up"]
    assert v["positions"] == {mon: {"x": 10, "y": 20, "anchor": "top"}}


def test_store_roundtrip_encrypts_keys(tmp_path):
    path = tmp_path / "settings.json"
    st = S.Settings(path)
    seen = []
    st.subscribe(seen.append)
    changed = st.update(gemini_keys=["AIzaTESTKEY1234567890"], size_original=30)
    assert changed == {"gemini_keys", "size_original"}
    assert seen == [changed]
    raw = path.read_text(encoding="utf-8")
    assert "AIzaTESTKEY1234567890" not in raw          # stored encrypted
    assert "gemini_keys" not in json.loads(raw)
    again = S.Settings(path)
    assert again["gemini_keys"] == ["AIzaTESTKEY1234567890"]
    assert again["size_original"] == 30
    assert st.update(size_original=30) == set()       # no change, no notification


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    st = S.Settings(path)
    assert st["width"] == S.defaults()["width"]
