import copy
import json
import sys

import pytest

from dualyrics import i18n, settings as S, templates as T


def test_builtins_are_valid_against_the_schema():
    assert [d["id"] for d in T.builtins()] == list(T.BUILTIN_IDS)
    for doc in T.builtins():
        assert T.schema_errors(doc) == [], doc["id"]
        assert T.sanitize(doc) == doc, doc["id"]
        assert doc["builtin"] is True and doc["schema"] == T.SCHEMA


def test_presets_are_readable_by_the_design_method():
    """DESIGN.md 3.3: every visible line passes over white, grey and black (box, outline or halo)."""
    for doc in T.builtins():
        for kind in T.LINE_KINDS:
            if doc["lines"][kind]["visible"]:
                c = T.line_contrast(doc, kind)
                assert c["pass"], (doc["id"], kind, c)
    assert T.line_contrast(T.builtin("netflix"), "original")["by"] == "box"
    assert T.line_contrast(T.builtin("cinema"), "original")["by"] == "outline"
    assert T.line_contrast(T.builtin("minimal"), "original")["by"] == "halo"


def test_schema_errors_explain_what_is_wrong():
    bad = T.builtin("cinema")
    bad["lines"]["original"]["font"]["size"] = 500
    bad["lines"]["original"]["font"]["weight"] = 450
    bad["container"]["align"] = "middle"
    bad["lines"]["next"]["color"] = "red"
    bad["lines"]["original"]["shadow"] = [T.DEFAULT_SHADOW] * 5
    bad["extra"] = 1
    del bad["timing"]
    errors = "\n".join(T.schema_errors(bad))
    assert "lines.original.font.size: 500 is above the maximum 120" in errors
    assert "multiple of 100" in errors
    assert '"middle" is not one of' in errors
    assert "lines.next.color" in errors
    assert "at most 4 items" in errors
    assert 'unknown key "extra"' in errors
    assert 'missing "timing"' in errors
    speaker = T.builtin("cinema")
    speaker["lines"]["speaker"]["format"] = "no placeholder"
    assert any("lines.speaker.format" in e for e in T.schema_errors(speaker))
    extra = T.builtin("cinema")
    extra["lines"]["original"]["placement"] = "above"   # only the speaker line has this
    assert any('unknown key "placement"' in e for e in T.schema_errors(extra))


def test_sanitize_clamps_drops_and_falls_back_to_the_preset():
    raw = T.builtin("neon")
    raw.update(id="my-neon", builtin=False, based_on="neon", junk={"a": 1})
    raw["lines"]["original"]["font"]["size"] = 999
    raw["lines"]["original"]["font"]["weight"] = 640
    raw["lines"]["original"]["color"] = "#abcdef"
    raw["lines"]["translation"]["color"] = "pink"
    raw["lines"]["original"]["opacity"] = -3
    raw["lines"]["original"]["shadow"] = [{"x": 99, "y": 0, "blur": 2, "color": "#000000", "opacity": 2}] * 6
    raw["container"]["width"]["unit"] = "em"
    raw["container"]["background"]["gradient"] = {"angle": 400, "stops": [{"offset": 0, "color": "#000000"}]}
    raw["animation"]["duration_ms"] = 10.6
    raw["karaoke"]["glow"] = None
    del raw["timing"]
    d = T.sanitize(raw)
    assert T.schema_errors(d) == []
    assert "junk" not in d
    o = d["lines"]["original"]
    assert o["font"]["size"] == 120 and o["font"]["weight"] == 600 and o["color"] == "#ABCDEF"
    assert o["opacity"] == 0 and len(o["shadow"]) == 4 and o["shadow"][0]["x"] == 20 and o["shadow"][0]["opacity"] == 1
    assert d["lines"]["translation"]["color"] == T.builtin("neon")["lines"]["translation"]["color"]
    assert d["container"]["width"]["unit"] == "percent"
    # a gradient with one stop is not usable: the preset value is kept
    assert d["container"]["background"]["gradient"] == T.builtin("neon")["container"]["background"]["gradient"]
    assert d["animation"]["duration_ms"] == 11 and d["karaoke"]["glow"] is None
    assert d["timing"] == T.builtin("neon")["timing"]
    assert d["builtin"] is False and d["based_on"] == "neon"


def test_sanitize_nullable_fields_get_defaults_when_switched_on():
    raw = T.builtin("cinema")
    raw["container"]["shadow"] = {"blur": 12}
    raw["lines"]["original"]["gradient"] = {"angle": 45, "stops": [{"offset": 0, "color": "#FF0000"},
                                                                   {"offset": 1, "color": "#0000FF"}]}
    d = T.sanitize(raw)
    assert d["container"]["shadow"] == dict(T.DEFAULT_SHADOW, blur=12)
    assert d["lines"]["original"]["gradient"]["angle"] == 45
    assert T.schema_errors(d) == []


def test_sanitize_garbage_gives_cinema():
    for junk in (None, [], "x", {"schema": "nope"}, {"id": "BAD ID!"}):
        d = T.sanitize(junk)
        assert T.schema_errors(d) == []
        assert d["lines"] == T.builtin("cinema")["lines"]
        assert T.ID_RE.match(d["id"])


def test_store_fork_rename_delete_restore_reset(tmp_path):
    store = T.TemplateStore(tmp_path)
    assert store.ids()[:len(T.BUILTIN_IDS)] == list(T.BUILTIN_IDS)
    assert store.resolve("does-not-exist")["id"] == "cinema"
    copy_ = store.fork("karaoke", names={"en": "Karaoke", "fa": "کارائوکه"})
    assert copy_["id"] != "karaoke" and copy_["based_on"] == "karaoke" and not copy_["builtin"]
    assert copy_["name"]["en"] == "Karaoke (copy)" and copy_["name"]["fa"] == "کارائوکه (کپی)"
    assert (tmp_path / f"{copy_['id']}.json").exists()
    # editing and saving never touches the preset
    edited = copy.deepcopy(copy_)
    edited["lines"]["original"]["color"] = "#123456"
    store.save(edited)
    assert store.get(copy_["id"])["lines"]["original"]["color"] == "#123456"
    assert T.builtin("karaoke")["lines"]["original"]["color"] == "#F1F5F9"
    assert store.rename(copy_["id"], "Party", "en")["name"]["en"] == "Party"
    assert store.reset_to_preset(copy_["id"])["lines"]["original"]["color"] == "#F1F5F9"
    assert store.get(copy_["id"])["name"]["en"] == "Party"
    rev = store.rev
    removed = store.delete(copy_["id"])
    assert removed is not None and not store.exists(copy_["id"]) and store.rev > rev
    assert store.delete("cinema") is None and store.exists("cinema")
    store.restore(removed)
    assert store.exists(copy_["id"])
    # saving under a built-in id makes a copy instead of shadowing the preset
    saved = store.save(T.builtin("glass"))
    assert saved["id"] != "glass" and saved["based_on"] == "glass"
    # files survive a restart
    again = T.TemplateStore(tmp_path)
    assert again.exists(copy_["id"]) and again.exists(saved["id"])


def test_import_export_round_trip(tmp_path):
    store = T.TemplateStore(tmp_path / "store")
    mine = store.fork("persian_classic", names={"en": "Gold"})
    out = tmp_path / "gold.json"
    store.export(mine["id"], out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == T.SCHEMA and "builtin" not in data
    assert T.schema_errors(data) == []
    other = T.TemplateStore(tmp_path / "other")
    doc, problems = other.import_file(out)
    assert problems == [] and doc["id"] == mine["id"]
    assert {k: v for k, v in doc.items() if k != "builtin"} == {k: v for k, v in mine.items() if k != "builtin"}
    # importing the same id again keeps both
    doc2, _ = other.import_file(out)
    assert doc2["id"] != doc["id"] and other.exists(doc["id"]) and other.exists(doc2["id"])
    # an exported built-in imports as a user copy
    store.export("game_hud", tmp_path / "hud.json")
    hud, _ = other.import_file(tmp_path / "hud.json")
    assert hud["id"] != "game_hud" and not hud["builtin"]


def test_import_fixes_and_reports_bad_values(tmp_path):
    store = T.TemplateStore(tmp_path / "s")
    raw = T.builtin("netflix")
    raw.update(id="shared-look", name={"en": "Shared", "de": "Geteilt"})
    raw["lines"]["original"]["font"]["size"] = 4
    raw["container"]["border"]["opacity"] = 5
    raw["unknown"] = True
    f = tmp_path / "shared.json"
    f.write_text("﻿" + json.dumps(raw), encoding="utf-8")   # a BOM from Notepad is fine
    doc, problems = store.import_file(f)
    assert len(problems) == 3
    assert doc["lines"]["original"]["font"]["size"] == 8 and doc["container"]["border"]["opacity"] == 1
    assert doc["name"] == {"en": "Shared", "de": "Geteilt"}
    assert T.schema_errors(doc) == []


@pytest.mark.parametrize("content", ["not json", "[]", json.dumps({"schema": "other/1"}), json.dumps({"id": "x"})])
def test_import_refuses_files_that_are_not_templates(tmp_path, content):
    store = T.TemplateStore(tmp_path / "s")
    f = tmp_path / "bad.json"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        store.import_file(f)
    assert store.ids() == list(T.BUILTIN_IDS)


def test_store_skips_broken_and_oversized_files(tmp_path):
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "huge.json").write_text(" " * (T.MAX_FILE_BYTES + 1), encoding="utf-8")
    (tmp_path / "cinema.json").write_text(json.dumps(T.builtin("cinema")), encoding="utf-8")  # cannot shadow
    store = T.TemplateStore(tmp_path)
    assert store.ids() == list(T.BUILTIN_IDS)
    assert store.get("cinema")["builtin"] is True


def test_v1_settings_become_my_style():
    v1 = S.defaults()
    v1.update(font_original="Tahoma", size_original=26, bold_translation=False, color_translation="#00FF00",
              text_only=True, outline=False, shadow=False, width=1200, align="left", show_next=False,
              rounded=False)
    d = T.from_v1(v1)
    assert T.schema_errors(d) == []
    assert d["id"] == T.MIGRATED_ID and d["scale"]["mode"] == "dpi"
    o, tr = d["lines"]["original"], d["lines"]["translation"]
    assert o["font"]["latin"][0] == "Tahoma" and o["font"]["size"] == 26 and o["font"]["weight"] == 700
    assert tr["font"]["weight"] == 400 and tr["color"] == "#00FF00" and tr["font"]["latin"] == ["auto"]
    assert o["outline"]["width"] == 0 and o["shadow"] == []
    assert d["container"]["background"]["opacity"] == 0 and d["container"]["corner_radius"] == 0
    assert d["container"]["width"] == {"unit": "px", "value": 1200, "fit_content": False, "min_px": 0}
    assert d["container"]["align"] == "left" and d["lines"]["next"]["visible"] is False


def test_settings_template_and_overlay_keys():
    v = S.validate({"template": "My Look!", "overlay_scale": 9, "overlay_opacity": 0.05, "monitors_off": "a, b",
                    "virtual_desktops": "everywhere", "ui_theme": "neon"})
    assert v["template"] == "liquid_glass" and v["overlay_scale"] == 2.0 and v["overlay_opacity"] == 0.2
    assert v["monitors_off"] == ["a", "b"] and v["virtual_desktops"] == "all" and v["ui_theme"] == "dark"
    assert S.validate({"template": "my-look_2"})["template"] == "my-look_2"
    pos = S.validate({"positions": {"m": {"w": 50, "scale": 7}, "p@m": {"x": 5, "y": 6, "anchor": "top",
                                                                       "w": 900, "scale": 1.25}, "e": {}}})["positions"]
    assert pos == {"m": {"w": 120, "scale": 2.0}, "p@m": {"x": 5, "y": 6, "anchor": "top", "w": 900, "scale": 1.25}}


def test_every_string_has_english_and_persian_and_presets_have_names():
    for key, val in i18n.STRINGS.items():
        assert val.get("en") and val.get("fa"), key
    for tid in T.BUILTIN_IDS:
        assert f"tpl_{tid}" in i18n.STRINGS and f"tpl_{tid}_desc" in i18n.STRINGS


def test_theme_file_matches_the_tokens():
    from dualyrics.ui import tokens

    assert json.loads(tokens.THEME_FILE.read_text(encoding="utf-8")) == json.loads(json.dumps(tokens.build_theme()))


@pytest.mark.skipif(sys.platform != "win32", reason="text shaping comes from Windows GDI")
def test_renderer_draws_every_preset_and_karaoke_moves():
    from dualyrics import render

    r = render.Renderer()
    lines = render.Lines("I still hear your voice", "هنوز صدایت را می‌شنوم", "Next line", "NARRATOR")
    for doc in T.builtins():
        f = r.render(doc, lines, 0.5, 600, hit=True)
        x0, y0, x1, y1 = f.box
        assert 0 <= x0 < x1 <= f.size[0] and 0 <= y0 < y1 <= f.size[1]
        alpha = f.image.getchannel("A")
        assert alpha.getbbox() is not None
        assert alpha.crop(f.box).getextrema()[0] >= 1   # the whole box catches the mouse
    k = T.builtin("karaoke")
    f = r.render(k, lines, 0.5, 600)
    assert f.karaoke and f.at(0.1).tobytes() != f.at(0.9).tobytes()
    assert f.at(-1) is f.image
    # wrapping and the ellipsis respect max_lines
    one = T.builtin("cinema")
    one["lines"]["original"]["max_lines"] = 1
    long = render.Lines(" ".join(["word"] * 80))
    f1 = r.render(one, long, 0.5, 400)
    f2 = r.render(T.builtin("cinema"), long, 0.5, 400)
    assert f1.box[3] - f1.box[1] < f2.box[3] - f2.box[1]
