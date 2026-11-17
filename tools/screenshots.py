"""Take the README screenshots and the hero GIF with the real app and the real overlay renderer.

    .venv\\Scripts\\python.exe tools\\screenshots.py              everything
    .venv\\Scripts\\python.exe tools\\screenshots.py --overlays   only the overlay pictures and docs/hero.gif
    .venv\\Scripts\\python.exe tools\\screenshots.py --only=ai,fa-general   only these settings sections

Runs Dualyrics in a temporary data folder (your settings are not touched), opens every settings section in
English and some in Persian and captures the window, renders overlay templates over generated scenes (no
copyrighted images, nothing from your desktop), makes docs/hero.gif from renderer frames and captures the
overlay's right-click menu. The Usage page shows two weeks of sample requests written into the temporary
folder. For templates with real blur (Liquid Glass, Glass) the scene under the box is blurred and tinted the way
the overlay's acrylic backdrop window does it on screen (dualyrics/backdrop.py).
Output: docs/screenshots/*.png, docs/hero.gif
"""

from __future__ import annotations

import ctypes
import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))
os.environ["DUALYRICS_HOME"] = tempfile.mkdtemp(prefix="dualyrics-shots-")

from PIL import Image, ImageDraw, ImageFilter, ImageGrab  # noqa: E402

from dualyrics import app as appmod, backdrop, render, settings as settings_mod, templates, winutil  # noqa: E402

SECTIONS = ["general", "profiles", "sources", "templates", "lyrics", "live", "translation", "dubbing", "usage",
            "monitors", "sync", "network", "ai", "about"]
FA_SECTIONS = ["templates", "general", "monitors"]
EN = "I still hear your voice when the night is quiet"
FA = "هنوز وقتی شب آرام است صدایت را می‌شنوم"
NEXT = "And every word is calling me home"
# (template, scene, file name, lines)
OVERLAYS = [
    ("liquid_glass", "city", "overlay-liquid-glass", (EN, FA, NEXT)),
    ("cinema", "city", "overlay-cinema", (EN, FA, NEXT)),
    ("karaoke", "city", "overlay-karaoke", (EN, FA, NEXT)),
    ("liquid_glass", "city", "overlay-any-language",
     ("もう一度だけ、信じてみよう", "بیا فقط یک بار دیگر باور کنیم", "Let's believe, just one more time")),
    ("game_hud", "game", "overlay-game-hud", (EN, FA, NEXT)),
    ("persian_classic", "dark", "overlay-persian-classic", (EN, FA, NEXT)),
    ("neon", "dark", "overlay-neon", (EN, FA, NEXT)),
    ("glass", "bright", "overlay-glass", (EN, FA, NEXT)),
]
# hero GIF scenes: (template, scene, original, translation, third line); the third line is the next lyric line,
# or the English pivot under a caption in another language
HERO = [
    ("liquid_glass", "city", EN, FA, NEXT),
    ("karaoke", "city", NEXT, "Y cada palabra me llama a casa", "I follow the light until the morning"),
    ("cinema", "city", "もう一度だけ、信じてみよう", "بیا فقط یک بار دیگر باور کنیم", "Let's believe, just one more time"),
    ("neon", "city", "Wir müssen die Brücke vor Sonnenaufgang erreichen.",
     "Gün doğmadan köprüye ulaşmalıyız.", "We have to reach the bridge before sunrise."),
]


def city(size: tuple[int, int]) -> Image.Image:
    """A busy generated scene: a city skyline at dusk with lit windows and out-of-focus lights."""
    w, h = size
    rnd = random.Random(11)
    img = render.gradient_image((w, h), {"angle": 90, "stops": [{"offset": 0, "color": "#0B1026"},
                                                                {"offset": 0.45, "color": "#40204F"},
                                                                {"offset": 0.8, "color": "#C2563F"},
                                                                {"offset": 1, "color": "#F2A65A"}]}).convert("RGB")
    bokeh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(bokeh)
    for _ in range(int(w * h / 9000)):
        x, y, r = rnd.randrange(w), rnd.randrange(h), rnd.choice((8, 12, 18, 26, 40))
        col = rnd.choice(((255, 196, 120), (255, 120, 90), (120, 200, 255), (255, 240, 200), (200, 120, 255)))
        d.ellipse((x - r, y - r, x + r, y + r), fill=col + (rnd.randrange(40, 110),))
    bokeh = bokeh.filter(ImageFilter.GaussianBlur(6))
    img.paste(bokeh, (0, 0), bokeh)
    town = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(town)
    x = 0
    while x < w:
        bw = rnd.randrange(40, 130)
        top = int(h * rnd.uniform(0.25, 0.7))
        shade = rnd.randrange(10, 28)
        d.rectangle((x, top, x + bw, h), fill=(shade, shade, shade + 14, 255))
        for wy in range(top + 8, h, 14):
            for wx in range(x + 6, x + bw - 6, 12):
                if rnd.random() < 0.38:
                    c = rnd.choice(((255, 214, 130), (255, 236, 190), (150, 210, 255)))
                    d.rectangle((wx, wy, wx + 5, wy + 7), fill=c + (rnd.randrange(150, 255),))
        x += bw + rnd.randrange(0, 10)
    img.paste(town, (0, 0), town)
    return img.filter(ImageFilter.GaussianBlur(0.8))


def scene(kind: str, size: tuple[int, int]) -> Image.Image:
    return city(size) if kind == "city" else render.backdrop(kind, size).convert("RGB")


def glass_under(back: Image.Image, t: dict, s: float, f: render.Frame, xy: tuple[int, int]) -> Image.Image:
    """What the acrylic backdrop window shows under the box: the scene blurred and lightly tinted."""
    if not backdrop.wanted(t):
        return back
    c = t["container"]
    x0, y0, x1, y1 = f.box
    rect = (xy[0] + x0, xy[1] + y0, xy[0] + x1, xy[1] + y1)
    region = back.crop(rect).filter(ImageFilter.GaussianBlur(max(8.0, c["blur"]["strength"] * s)))
    col = c["background"]["color"]
    tint = Image.new("RGB", region.size, tuple(int(col[i:i + 2], 16) for i in (1, 3, 5)))
    region = Image.blend(region, tint, backdrop.TINT_ALPHA / 255)
    out = back.copy()
    out.paste(region, rect[:2], render.round_mask(region.width, region.height, c["corner_radius"] * s))
    return out


def place(r: render.Renderer, tid: str, lines: tuple[str, str, str], size: tuple[int, int], screen_h: int = 1080):
    """Render one template at the scale of a ``screen_h`` screen; (template, scale, frame, top-left)."""
    t = templates.builtin(tid)
    s = r.scale(t, screen_h)
    f = r.render(t, render.Lines(*lines), s, r.max_width(t, size[0] - 40, s))
    xy = ((size[0] - f.image.width) // 2, (size[1] - f.image.height) // 2)
    return t, s, f, xy


def overlay_shots() -> None:
    """Real renderer output over generated scenes, at 1080p scale."""
    r = render.Renderer()
    for tid, kind, name, lines in OVERLAYS:
        _t, _s, f, _xy = place(r, tid, lines, (1600, 400))
        size = (1600, max(300, f.image.height + 60))
        t, s, f, xy = place(r, tid, lines, size)
        back = glass_under(scene(kind, size), t, s, f, xy)
        img = f.at(0.55) if f.karaoke else f.image
        render.compose_on(back, img, xy).save(OUT / f"{name}.png", optimize=True)
        print("overlay", name)


def hero_gif(path: Path = ROOT / "docs" / "hero.gif") -> None:
    """docs/hero.gif: four looks and languages, faded in, karaoke sweeping, from the overlay renderer."""
    r = render.Renderer()
    size, out_w = (1600, 440), 1120
    out_size = (out_w, round(size[1] * out_w / size[0]))
    scenes = []
    for tid, kind, orig, tr, third in HERO:
        t, s, f, xy = place(r, tid, (orig, tr, third), size)
        scenes.append((glass_under(scene(kind, size), t, s, f, xy), f, xy))
    # one palette for every frame, and dithered backgrounds that stay identical from frame to frame (small file)
    sample = Image.new("RGB", (out_size[0], out_size[1] * len(scenes)))
    for i, (back, f, xy) in enumerate(scenes):
        shot = render.compose_on(back, f.at(0.5) if f.karaoke else f.image, xy)
        sample.paste(shot.resize(out_size, Image.LANCZOS), (0, i * out_size[1]))
    pal = sample.quantize(255, method=Image.Quantize.MEDIANCUT)
    frames: list[Image.Image] = []
    durations: list[int] = []
    for back, f, xy in scenes:
        bg = back.resize(out_size, Image.LANCZOS).quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
        bg = bg.convert("RGBA")
        steps = ([("in", a / 6) for a in range(1, 7)] + [("hold", p / 26) for p in range(27)]
                 + [("out", a / 4) for a in (3, 2, 1, 0)])
        for kind, v in steps:
            if f.karaoke:
                img = f.at(0.0 if kind == "in" else 1.0 if kind == "out" else v)
            else:
                img = f.image
            if kind != "hold":
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda px, v=v: int(px * v)))
            big = Image.new("RGBA", size, (0, 0, 0, 0))
            big.alpha_composite(img, (max(0, xy[0]), max(0, xy[1])))
            fr = bg.copy()
            fr.alpha_composite(big.resize(out_size, Image.LANCZOS))
            frames.append(fr.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE))
            durations.append(70 if kind == "hold" else 60)
        durations[-5] = 1100    # rest on the finished line before it fades out
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True,
                   disposal=1)
    print("hero", path.name, len(frames), "frames", path.stat().st_size // 1024, "KB")


def seed_usage() -> None:
    """Two weeks of sample Gemini requests in the temporary data folder, so the Usage page is not empty."""
    from dualyrics import paths, usage

    rnd = random.Random(5)
    now = time.time()
    media = ["Sample Artist - Sample Song", "Another Artist - Night Drive", "Film trailer (live captions)",
             "Game session (on-screen subtitles)"]
    clock = {"t": now}
    lt = time.localtime(now)
    today = max(60, lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)     # seconds since midnight
    meter = usage.UsageMeter(paths.data_dir() / "usage.sqlite", clock=lambda: clock["t"])
    for day in range(13, -1, -1):
        for _ in range(rnd.randrange(6, 30)):
            clock["t"] = now - day * 86400 - rnd.randrange(0, min(20000, today))
            m = rnd.choice(media)
            model = rnd.choice(("gemini-2.5-flash", "gemini-2.5-flash", "gemini-flash-lite-latest"))
            p, o = rnd.randrange(600, 2400), rnd.randrange(300, 1500)
            meter.record(model, rnd.choice((1, 1, 2)), "text", (p, o, p + o), media=m)
            if rnd.random() < 0.5:
                meter.cache_hit(media=m)
        if day < 4:
            clock["t"] = now - day * 86400 - min(3600, today // 2)
            meter.record("gemini-2.5-flash-preview-tts", 1, "tts", (120, 0, 120), audio_sec=4.2, media=media[0])
    meter.close()


def print_window(win) -> Image.Image | None:
    """The window's client area drawn by the window itself (PrintWindow), so a notification or another window
    on top of it never ends up in the picture."""
    from ctypes import wintypes

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    for fn in (user32.GetDC, gdi32.CreateCompatibleDC, gdi32.CreateCompatibleBitmap, gdi32.SelectObject):
        fn.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_uint]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    hwnd = int(win.wm_frame(), 16)
    w, h = win.winfo_width(), win.winfo_height()
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, 0x1 | 0x2):    # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            return None
        header = (ctypes.c_uint32 * 10)(40, w, ctypes.c_uint32(-h).value, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, header, 0):
            return None
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def grab(win, name: str) -> None:
    for _attempt in range(10):
        win.lift()
        win.update()
        x, y = win.winfo_rootx(), win.winfo_rooty()
        w, h = win.winfo_width(), win.winfo_height()
        img = print_window(win) or ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        if img.convert("L").getextrema()[1] > 20:   # not a black frame caught while Windows repaints
            break
        time.sleep(0.8)
    img.save(OUT / name, optimize=True)
    print("shot", name, (w, h))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    overlay_shots()
    hero_gif()
    if "--overlays" in sys.argv:
        return
    seed_usage()
    store = settings_mod.Settings()
    store.update(ui_language="en", target_language="fa", ui_theme="dark", settings_section="general")
    appmod.setup_logging(False)
    winutil.set_dpi_awareness()
    inst = winutil.SingleInstance()
    a = appmod.App(inst, open_settings=False)
    sw = a.settings_window
    # keep the shots neutral: no overlay over the window, and not the song that happens to be playing
    a.overlay.show = lambda: None
    import types

    from dualyrics import paths, settings_window as sw_mod

    shown = types.SimpleNamespace(**{k: getattr(paths, k) for k in dir(paths) if not k.startswith("_")})
    shown.data_dir = lambda: Path(r"C:\Users\you\AppData\Roaming\Dualyrics")   # no real user name in the shots
    sw_mod.paths = shown
    a.usage.path = shown.data_dir() / "usage.sqlite"   # shown on the Usage page
    from dualyrics import mcp   # AI & API page: the installed app's path, not this checkout's

    mcp.launch_command = lambda: (r"C:\Users\you\AppData\Local\Programs\Dualyrics\Dualyrics.exe", ["--mcp"], {})
    a.song_offset_info = lambda: ("Sample Artist — Sample Song", 0)
    sw._refresh_now_playing = lambda: (sw._np.configure(text="Sample Song — Sample Artist\nspotify"),
                                       sw._np_dot.configure(fg_color=("#047857", "#34D399")))
    queue: list[tuple[str, str]] = [("en", s) for s in SECTIONS] + [("fa", s) for s in FA_SECTIONS]
    only = [a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--only=")]
    if only:     # --only=network,ai,fa-general: retake these sections (and the overlay menu)
        queue = [(lang, sec) for lang, sec in queue if (sec if lang == "en" else "fa-" + sec) in only[0]]

    def step(i: int = 0) -> None:
        if i >= len(queue):
            a.root.after(300, menu_shot)
            return
        lang, section = queue[i]
        if a.settings["ui_language"] != lang:
            a.settings.update(ui_language=lang)
        a.root.after(400, lambda: open_and_grab(i, lang, section))

    def open_and_grab(i: int, lang: str, section: str) -> None:
        sw.open(section)
        sw.win.attributes("-topmost", True)
        name = f"settings-{section}.png" if lang == "en" else f"settings-fa-{section}.png"
        if section == "templates":
            a.root.after(1600, lambda: (grab(sw.win, name), editor_shot(i, lang)))
        else:
            a.root.after(900 if lang == "en" else 1600, lambda: (grab(sw.win, name), step(i + 1)))

    def editor_shot(i: int, lang: str) -> None:
        """The template editor itself: scroll down to the text-line properties."""
        sw._scroll._parent_canvas.yview_moveto(0.36)  # noqa: SLF001
        name = "settings-template-editor.png" if lang == "en" else "settings-fa-template-editor.png"
        a.root.after(500, lambda: (grab(sw.win, name), step(i + 1)))

    def menu_shot() -> None:
        sw.close()
        a.settings.update(ui_language="en")
        ov = a.overlay
        from dualyrics.overlay import Content

        ov.render(Content(original="I still hear your voice when the night is quiet",
                          translation="هنوز وقتی شب آرام است صدایت را می‌شنوم", song="shot", index=0), force=True)
        a.root.after(600, lambda: popup(ov))

    def popup(ov) -> None:
        mon = winutil.primary_monitor()
        x, y = mon.left + mon.width // 2 - 160, mon.top + 120

        def shoot() -> None:
            time.sleep(1.0)
            img = ImageGrab.grab(bbox=(x - 2, y - 2, x + 700, y + 760), all_screens=True)
            img = trim(img)
            img.save(OUT / "overlay-menu.png", optimize=True)
            print("shot overlay-menu.png", img.size)
            ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
            a.call_soon(lambda: a.root.after(500, a.quit))
            time.sleep(4)          # the native menu loop can outlive Tk on exit: leave anyway
            sys.stdout.flush()
            os._exit(0)

        threading.Thread(target=shoot, daemon=True).start()
        menu = ov.build_menu()
        menu.tk_popup(x, y)
        menu.grab_release()

    a.root.after(800, step)
    a.run()
    inst.release()
    sys.stdout.flush()
    os._exit(0)


def trim(img: Image.Image) -> Image.Image:
    """Crop to the menu (the area that has the menu background colour)."""
    px = img.convert("RGB")
    w, h = px.size
    bg = px.getpixel((4, 4))

    def run(get, n: int) -> int:
        """Last index of the menu along a line (separators and text are short interruptions)."""
        last, miss = 0, 0
        for i in range(4, n):
            if _close(get(i), bg):
                last, miss = i, 0
            else:
                miss += 1
                if miss > 14:
                    break
        return last

    x1 = run(lambda i: px.getpixel((i, 4)), w)
    y1 = run(lambda i: px.getpixel((3, i)), h)
    return img.crop((0, 0, min(w, x1 + 3), min(h, y1 + 3)))


def _close(a, b) -> bool:
    return all(abs(int(p) - int(q)) < 24 for p, q in zip(a[:3], b[:3]))


if __name__ == "__main__":
    main()
