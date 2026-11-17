"""Design tokens of the settings window (docs/DESIGN.md, part 1). Colours are (light, dark) tuples, which is
what customtkinter accepts natively. Widgets never use raw hex values; they read them from here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# -- colour (light, dark)
BG_APP = ("#F4F6FA", "#0B1120")
BG_SIDEBAR = ("#EBEFF5", "#0F172A")
BG_SURFACE = ("#FFFFFF", "#141C2C")
BG_RAISED = ("#FFFFFF", "#1A2436")
BG_HOVER = ("#E6ECF4", "#212D42")
BG_SELECTED = ("#D5F3F9", "#123A4A")
BORDER_SUBTLE = ("#D3DBE6", "#2A3750")
BORDER_STRONG = ("#7F8CA0", "#64748B")
TEXT_PRIMARY = ("#0F172A", "#E6EDF7")
TEXT_SECONDARY = ("#3A475C", "#A9B6C9")
TEXT_MUTED = ("#56657B", "#8E9DB3")
TEXT_DISABLED = ("#9AA5B5", "#5B6A80")
ACCENT = ("#0E7490", "#22D3EE")
ACCENT_HOVER = ("#155E75", "#67E8F9")
ACCENT_PRESSED = ("#164E63", "#06B6D4")
ON_ACCENT = ("#FFFFFF", "#062330")
CORAL = ("#BE123C", "#FB7185")
SUCCESS = ("#047857", "#34D399")
WARNING = ("#A14A06", "#FBBF24")
DANGER = ("#B91C1C", "#F87171")
FOCUS = ("#0E7490", "#67E8F9")

# -- type scale: (size, weight)
TYPE = {
    "display": (28, 700),
    "title": (20, 600),
    "subtitle": (16, 600),
    "body": (14, 400),
    "body.strong": (14, 600),
    "caption": (12, 400),
    "mono": (13, 400),
}

# -- spacing (4 px base), radii, sizes
SPACE = (0, 2, 4, 8, 12, 16, 20, 24, 32, 40, 48)
RADIUS_SM, RADIUS_MD, RADIUS_LG, RADIUS_XL = 4, 6, 10, 14
CONTROL_H, CONTROL_LG_H, NAV_ITEM_H = 32, 36, 40
ICON, ICON_NAV = 16, 20
SIDEBAR_W, SIDEBAR_RAIL_W = 216, 60
PREVIEW_W, PREVIEW_MIN_W = 380, 320
CONTENT_MAX = 640
WINDOW = (1120, 720)
WINDOW_MIN = (760, 560)
RAIL_BELOW = 1100          # sidebar becomes an icon rail below this window width
DOCK_BELOW = 940           # preview docks under the content below this width

THEME_FILE = Path(__file__).resolve().parent.parent / "assets" / "theme" / "dualyrics.json"


def pick(token: tuple[str, str], dark: bool) -> str:
    return token[1] if dark else token[0]


def build_theme() -> dict[str, Any]:
    """The customtkinter theme generated from the tokens (written to assets/theme/dualyrics.json)."""
    L = list
    return {
        "CTk": {"fg_color": L(BG_APP)},
        "CTkToplevel": {"fg_color": L(BG_APP)},
        "CTkFrame": {"corner_radius": RADIUS_LG, "border_width": 0, "fg_color": L(BG_SURFACE),
                     "top_fg_color": L(BG_RAISED), "border_color": L(BORDER_SUBTLE)},
        "CTkButton": {"corner_radius": RADIUS_MD, "border_width": 0, "fg_color": L(ACCENT),
                      "hover_color": L(ACCENT_HOVER), "border_color": L(BORDER_STRONG), "text_color": L(ON_ACCENT),
                      "text_color_disabled": L(TEXT_DISABLED)},
        "CTkLabel": {"corner_radius": 0, "border_width": 0, "fg_color": "transparent",
                     "border_color": L(BORDER_SUBTLE), "text_color": L(TEXT_PRIMARY)},
        "CTkEntry": {"corner_radius": RADIUS_MD, "border_width": 1, "fg_color": L(BG_RAISED),
                     "border_color": L(BORDER_STRONG), "text_color": L(TEXT_PRIMARY),
                     "placeholder_text_color": L(TEXT_MUTED)},
        "CTkCheckBox": {"corner_radius": RADIUS_SM, "border_width": 2, "fg_color": L(ACCENT),
                        "border_color": L(BORDER_STRONG), "hover_color": L(ACCENT_HOVER),
                        "checkmark_color": L(ON_ACCENT), "text_color": L(TEXT_PRIMARY),
                        "text_color_disabled": L(TEXT_DISABLED)},
        "CTkSwitch": {"corner_radius": 1000, "border_width": 3, "button_length": 0, "fg_color": L(BORDER_STRONG),
                      "progress_color": L(ACCENT), "button_color": L(TEXT_PRIMARY),
                      "button_hover_color": L(TEXT_PRIMARY), "text_color": L(TEXT_PRIMARY),
                      "text_color_disabled": L(TEXT_DISABLED)},
        "CTkRadioButton": {"corner_radius": 1000, "border_width_checked": 6, "border_width_unchecked": 2,
                           "fg_color": L(ACCENT), "border_color": L(BORDER_STRONG), "hover_color": L(ACCENT_HOVER),
                           "text_color": L(TEXT_PRIMARY), "text_color_disabled": L(TEXT_DISABLED)},
        "CTkProgressBar": {"corner_radius": 1000, "border_width": 0, "fg_color": L(BORDER_SUBTLE),
                           "progress_color": L(ACCENT), "border_color": L(BORDER_SUBTLE)},
        "CTkSlider": {"corner_radius": 1000, "button_corner_radius": 1000, "border_width": 6, "button_length": 0,
                      "fg_color": L(BORDER_STRONG), "progress_color": L(ACCENT), "button_color": L(TEXT_PRIMARY),
                      "button_hover_color": L(ACCENT_HOVER)},
        "CTkOptionMenu": {"corner_radius": RADIUS_MD, "fg_color": L(BG_RAISED), "button_color": L(BG_RAISED),
                          "button_hover_color": L(BG_HOVER), "text_color": L(TEXT_PRIMARY),
                          "text_color_disabled": L(TEXT_DISABLED)},
        "CTkComboBox": {"corner_radius": RADIUS_MD, "border_width": 1, "fg_color": L(BG_RAISED),
                        "border_color": L(BORDER_STRONG), "button_color": L(BORDER_STRONG),
                        "button_hover_color": L(TEXT_SECONDARY), "text_color": L(TEXT_PRIMARY),
                        "text_color_disabled": L(TEXT_DISABLED)},
        "CTkScrollbar": {"corner_radius": 1000, "border_spacing": 4, "fg_color": "transparent",
                         "button_color": L(BORDER_SUBTLE), "button_hover_color": L(BORDER_STRONG)},
        "CTkSegmentedButton": {"corner_radius": RADIUS_MD, "border_width": 2, "fg_color": L(BG_RAISED),
                               "selected_color": L(ACCENT), "selected_hover_color": L(ACCENT_HOVER),
                               "unselected_color": L(BG_RAISED), "unselected_hover_color": L(BG_HOVER),
                               "text_color": L(TEXT_PRIMARY), "text_color_disabled": L(TEXT_DISABLED)},
        "CTkTextbox": {"corner_radius": RADIUS_MD, "border_width": 1, "fg_color": L(BG_RAISED),
                       "border_color": L(BORDER_STRONG), "text_color": L(TEXT_PRIMARY),
                       "scrollbar_button_color": L(BORDER_SUBTLE), "scrollbar_button_hover_color": L(BORDER_STRONG)},
        "CTkScrollableFrame": {"label_fg_color": L(BG_SURFACE)},
        "DropdownMenu": {"fg_color": L(BG_RAISED), "hover_color": L(BG_HOVER), "text_color": L(TEXT_PRIMARY)},
        "CTkFont": {"macOS": {"family": "SF Display", "size": 14, "weight": "normal"},
                    "Windows": {"family": "Segoe UI", "size": 14, "weight": "normal"},
                    "Linux": {"family": "Roboto", "size": 14, "weight": "normal"}},
    }


def write_theme(path: Path = THEME_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_theme(), indent=2) + "\n", encoding="utf-8")
    return path


def theme_path() -> Path:
    """The theme file, regenerated when it is missing (e.g. running from a fresh checkout)."""
    if not THEME_FILE.exists():
        try:
            write_theme()
        except OSError:
            pass
    return THEME_FILE
