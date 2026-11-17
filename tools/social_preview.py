"""Generate docs/social-preview.png (1280 x 640), the image GitHub shows when the repository link is shared.

    .venv\\Scripts\\python.exe tools\\social_preview.py

App icon, name and tagline on the deep navy of the design tokens (docs/DESIGN.md part 0), and the "any
language" overlay picture (docs/screenshots/overlay-any-language.png, made by tools/screenshots.py) on the right.
Upload it in the repository's Settings > General > Social preview.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "dualyrics" / "assets" / "fonts"
W, H = 1280, 640
NAVY_TOP, NAVY_BOTTOM = (11, 17, 32), (20, 28, 48)
CYAN, CORAL, WHITE, MUTED = (34, 211, 238), (251, 113, 133), (241, 245, 249), (148, 163, 184)


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Vazirmatn-{weight}.ttf"), size)


def main() -> None:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(NAVY_TOP, NAVY_BOTTOM)))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-220, -260, 520, 420), fill=CYAN + (38,))
    gd.ellipse((860, 330, 1500, 900), fill=CORAL + (30,))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img.paste(glow, (0, 0), glow)

    icon = Image.open(ROOT / "dualyrics" / "assets" / "icon.png").convert("RGBA").resize((132, 132), Image.LANCZOS)
    img.paste(icon, (72, 74), icon)
    d = ImageDraw.Draw(img)
    d.text((226, 82), "Dualyrics", font=font("Bold", 92), fill=WHITE)
    d.text((230, 192), "Live translated subtitles for anything you hear", font=font("Medium", 30), fill=CYAN)

    d.text((76, 290), "Any language you hear", font=font("SemiBold", 40), fill=WHITE)
    d.line([(80, 373), (110, 373)], fill=CORAL, width=5)                       # arrow (not in Vazirmatn)
    d.polygon([(108, 362), (124, 373), (108, 384)], fill=CORAL)
    d.text((136, 346), "English + your own language", font=font("SemiBold", 40), fill=CORAL)
    d.text((76, 402), "40+ target languages", font=font("SemiBold", 40), fill=WHITE)
    d.text((76, 490), "Spotify · YouTube · games · films", font=font("Medium", 28), fill=MUTED)
    d.text((76, 532), "Free · open source · Windows 10/11", font=font("Medium", 28), fill=MUTED)

    shot_path = ROOT / "docs" / "screenshots" / "overlay-any-language.png"
    if shot_path.exists():
        shot = Image.open(shot_path).convert("RGB")
        cx = shot.width // 2
        shot = shot.crop((cx - 380, 0, cx + 380, shot.height))    # the overlay box and some city around it
        tw = 500
        shot = shot.resize((tw, round(shot.height * tw / shot.width)), Image.LANCZOS)
        mask = Image.new("L", shot.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), 28, fill=255)
        x, y = W - tw - 56, 292
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((x + 6, y + 14, x + tw + 6, y + shot.height + 14), 28,
                                                 fill=(0, 0, 0, 150))
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        img.paste(shadow, (0, 0), shadow)
        img.paste(shot, (x, y), mask)
        ImageDraw.Draw(img).rounded_rectangle((x, y, x + tw - 1, y + shot.height - 1), 28,
                                              outline=(255, 255, 255), width=1)
    out = ROOT / "docs" / "social-preview.png"
    img.save(out, optimize=True)
    print("wrote", out, img.size)


if __name__ == "__main__":
    main()
