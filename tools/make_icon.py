"""Generate the Dualyrics app icon (concept B: a sound wave flowing into two subtitle lines,
white = original, coral = translation). Writes dualyrics/assets/icon.png (512) and icon.ico (16-256).
Run:  python tools/make_icon.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image  # noqa: E402

from icon_concepts import concept_b  # noqa: E402

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dualyrics", "assets")
os.makedirs(ASSETS, exist_ok=True)
big = concept_b()
big.resize((512, 512), Image.LANCZOS).save(os.path.join(ASSETS, "icon.png"))
big.resize((256, 256), Image.LANCZOS).save(
    os.path.join(ASSETS, "icon.ico"),
    sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("icon written to", ASSETS)
