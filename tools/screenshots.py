"""Take the README screenshots with the real app and the real overlay renderer.

    .venv\\Scripts\\python.exe tools\\screenshots.py

Runs Dualyrics in a temporary data folder (your settings are not touched), opens every settings section in
English and some in Persian and captures the window, renders four overlay templates over generated scenes
(no copyrighted images, nothing from your desktop) and captures the overlay's right-click menu.
Output: docs/screenshots/*.png
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))
os.environ["DUALYRICS_HOME"] = tempfile.mkdtemp(prefix="dualyrics-shots-")

from PIL import Image, ImageGrab  # noqa: E402

from dualyrics import app as appmod, render, settings as settings_mod, templates, winutil  # noqa: E402

SECTIONS = ["general", "profiles", "templates", "lyrics", "live", "translation", "monitors", "sync", "network",
            "about"]
FA_SECTIONS = ["templates", "general", "monitors"]
OVERLAY_TEMPLATES = [("cinema", "bright"), ("karaoke", "dark"), ("game_hud", "game"), ("persian_classic", "dark"),
                     ("neon", "dark"), ("glass", "bright")]


def overlay_shots() -> None:
    """Real renderer output over generated scenes, at 1080p scale."""
    r = render.Renderer()
    lines = render.Lines("I still hear your voice when the night is quiet",
                         "هنوز وقتی شب آرام است صدایت را می‌شنوم", "And every word is calling me home")
    for tid, scene in OVERLAY_TEMPLATES:
        t = templates.builtin(tid)
        s = r.scale(t, 1080)
        f = r.render(t, lines, s, r.max_width(t, 1920, s))
        img = f.at(0.55) if f.karaoke else f.image
        w, h = 1600, max(260, img.height + 60)
        back = render.backdrop(scene, (w, h))
        x = (w - img.width) // 2
        y = (h - img.height) // 2
        render.compose_on(back, img, (x, y)).save(OUT / f"overlay-{tid.replace('_', '-')}.png", optimize=True)
        print("overlay", tid)


def grab(win, name: str) -> None:
    for _attempt in range(10):
        win.lift()
        win.update()
        x, y = win.winfo_rootx(), win.winfo_rooty()
        w, h = win.winfo_width(), win.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        if img.convert("L").getextrema()[1] > 20:   # not a black frame caught while Windows repaints
            break
        time.sleep(0.8)
    img.save(OUT / name, optimize=True)
    print("shot", name, (w, h))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    overlay_shots()
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
    a.song_offset_info = lambda: ("Sample Artist — Sample Song", 0)
    sw._refresh_now_playing = lambda: (sw._np.configure(text="Sample Song — Sample Artist\nspotify"),
                                       sw._np_dot.configure(fg_color=("#047857", "#34D399")))
    queue: list[tuple[str, str]] = [("en", s) for s in SECTIONS] + [("fa", s) for s in FA_SECTIONS]

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
