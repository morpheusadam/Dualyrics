"""User-interface strings (English and Persian)."""

from __future__ import annotations

_lang = "en"

RLE, PDF = "‫", "‬"   # right-to-left embedding: keeps "Gemini" etc. in place inside Persian text


def rtl_embed(text: str) -> str:
    """Wrap every line in RLE…PDF so Windows lays mixed Persian/English text out right-to-left."""
    return "\n".join(RLE + ln + PDF if ln else ln for ln in text.split("\n"))


STRINGS: dict[str, dict[str, str]] = {
    # tray / menus
    "show_lyrics": {"en": "Show lyrics", "fa": "نمایش متن آهنگ"},
    "settings": {"en": "Settings…", "fa": "تنظیمات…"},
    "retranslate": {"en": "Retranslate this song", "fa": "ترجمه‌ی دوباره‌ی این آهنگ"},
    "click_through": {"en": "Click-through", "fa": "عبور کلیک از روی پنجره"},
    "lock_position": {"en": "Lock position", "fa": "قفل جای پنجره"},
    "open_cache": {"en": "Open cache folder", "fa": "باز کردن پوشه‌ی کش"},
    "quit": {"en": "Quit", "fa": "خروج"},
    "hide": {"en": "Hide", "fa": "پنهان کردن"},
    "show_next": {"en": "Show next line", "fa": "نمایش خط بعدی"},
    "show_original": {"en": "Show original", "fa": "نمایش متن اصلی"},
    "show_translation": {"en": "Show translation", "fa": "نمایش ترجمه"},
    "text_only": {"en": "Text only (transparent background)", "fa": "فقط متن (پس‌زمینه‌ی شفاف)"},
    "offset_earlier": {"en": "Lyrics earlier (−{ms} ms)", "fa": "متن زودتر (−{ms} میلی‌ثانیه)"},
    "offset_later": {"en": "Lyrics later (+{ms} ms)", "fa": "متن دیرتر (+{ms} میلی‌ثانیه)"},
    "offset_reset": {"en": "Reset song offset", "fa": "صفر کردن تأخیر این آهنگ"},
    "position": {"en": "Position", "fa": "جای پنجره"},
    "tray_tip": {"en": "Dualyrics — lyrics + translation", "fa": "Dualyrics — متن آهنگ + ترجمه"},
    # status
    "st_searching": {"en": "Looking for lyrics…", "fa": "در حال پیدا کردن متن آهنگ…"},
    "st_translating": {"en": "Translating…", "fa": "در حال ترجمه…"},
    "st_not_found": {"en": "No lyrics found for this song.", "fa": "برای این آهنگ متنی پیدا نشد."},
    "st_instrumental": {"en": "♪ Instrumental", "fa": "♪ بی‌کلام"},
    "st_network": {"en": "Network error — retrying soon. Check Settings › Network.",
                   "fa": "خطای اینترنت — به‌زودی دوباره تلاش می‌کنم. تنظیمات › شبکه را ببینید."},
    "st_trans_failed": {"en": "Translation failed (quota, key or network).",
                        "fa": "ترجمه انجام نشد (سهمیه، کلید یا اینترنت)."},
    "st_unsynced": {"en": "These lyrics have no timing — scroll to read.",
                    "fa": "این متن زمان‌بندی ندارد — برای خواندن اسکرول کنید."},
    "st_no_media": {"en": "Windows media controls are not available.", "fa": "کنترل‌های رسانه‌ی ویندوز در دسترس نیست."},
    "st_free_used": {"en": "Gemini unavailable — used free translation.", "fa": "Gemini در دسترس نبود — ترجمه‌ی رایگان استفاده شد."},
    "st_offset": {"en": "Song offset: {ms:+d} ms", "fa": "تأخیر این آهنگ: {ms:+d} میلی‌ثانیه"},
    # settings window
    "win_title": {"en": "Dualyrics settings", "fa": "تنظیمات Dualyrics"},
    "tab_general": {"en": "General", "fa": "عمومی"},
    "tab_translation": {"en": "Translation", "fa": "ترجمه"},
    "tab_appearance": {"en": "Appearance", "fa": "ظاهر"},
    "tab_sync": {"en": "Sync", "fa": "همگام‌سازی"},
    "tab_network": {"en": "Network", "fa": "شبکه"},
    "tab_about": {"en": "About", "fa": "درباره"},
    "target_language": {"en": "Translate into", "fa": "ترجمه به زبان"},
    "ui_language": {"en": "Interface language", "fa": "زبان برنامه"},
    "start_with_windows": {"en": "Start with Windows", "fa": "اجرا همراه با ویندوز"},
    "hide_when_idle": {"en": "Hide when nothing is playing", "fa": "پنهان شدن وقتی چیزی پخش نمی‌شود"},
    "preferred_players": {"en": "Preferred players (in order, comma separated)",
                          "fa": "پخش‌کننده‌های ترجیحی (به ترتیب، با ویرگول)"},
    "players_hint": {"en": "Matched against the app id, e.g. spotify, spotube, chrome, msedge, firefox.",
                     "fa": "با شناسه‌ی برنامه مقایسه می‌شود، مثل spotify، spotube، chrome، msedge، firefox."},
    "provider": {"en": "Translation provider", "fa": "سرویس ترجمه"},
    "prov_gemini": {"en": "Gemini — best quality (API key)", "fa": "Gemini — بهترین کیفیت (کلید API)"},
    "prov_free": {"en": "Free — Google Translate (no key)", "fa": "رایگان — Google Translate (بدون کلید)"},
    "gemini_keys": {"en": "Gemini API keys (one per line)", "fa": "کلیدهای API جمینای (هر خط یک کلید)"},
    "gemini_keys_hint": {"en": "Free keys: aistudio.google.com › Get API key. Stored encrypted for your Windows user.",
                         "fa": "کلید رایگان: aistudio.google.com › Get API key. رمزگذاری‌شده برای کاربر ویندوز شما ذخیره می‌شود."},
    "gemini_models": {"en": "Models (tried in order, one per line)", "fa": "مدل‌ها (به ترتیب امتحان می‌شوند، هر خط یکی)"},
    "test": {"en": "Test", "fa": "آزمایش"},
    "testing": {"en": "Testing…", "fa": "در حال آزمایش…"},
    "reset_models": {"en": "Default models", "fa": "مدل‌های پیش‌فرض"},
    "fallback_free": {"en": "Use the free translator when Gemini fails", "fa": "اگر Gemini کار نکرد از مترجم رایگان استفاده شود"},
    "style": {"en": "Translation style (instruction for Gemini)", "fa": "سبک ترجمه (دستور برای Gemini)"},
    "reset_style": {"en": "Default style", "fa": "سبک پیش‌فرض"},
    "skip_same": {"en": "Translate only when the lyrics are not already in the target language",
                  "fa": "فقط وقتی ترجمه شود که متن آهنگ به زبان مقصد نباشد"},
    "clear_cache": {"en": "Clear cache", "fa": "پاک کردن کش"},
    "cache_cleared": {"en": "Cache cleared.", "fa": "کش پاک شد."},
    "font_original": {"en": "Original font", "fa": "فونت متن اصلی"},
    "font_translation": {"en": "Translation font", "fa": "فونت ترجمه"},
    "size": {"en": "Size", "fa": "اندازه"},
    "bold": {"en": "Bold", "fa": "ضخیم"},
    "color_original": {"en": "Original color", "fa": "رنگ متن اصلی"},
    "color_translation": {"en": "Translation color", "fa": "رنگ ترجمه"},
    "color_next": {"en": "Next line color", "fa": "رنگ خط بعدی"},
    "color_bg": {"en": "Background color", "fa": "رنگ پس‌زمینه"},
    "opacity": {"en": "Opacity", "fa": "میزان شفافیت"},
    "outline": {"en": "Text outline", "fa": "حاشیه‌ی دور متن"},
    "outline_color": {"en": "Outline color", "fa": "رنگ حاشیه"},
    "outline_width": {"en": "Outline width", "fa": "ضخامت حاشیه"},
    "shadow": {"en": "Text shadow", "fa": "سایه‌ی متن"},
    "width": {"en": "Width (px)", "fa": "عرض (پیکسل)"},
    "plain_height": {"en": "Height for unsynced lyrics (px)", "fa": "ارتفاع برای متن بدون زمان‌بندی (پیکسل)"},
    "align": {"en": "Alignment", "fa": "چینش"},
    "al_center": {"en": "Center", "fa": "وسط"},
    "al_left": {"en": "Left", "fa": "چپ"},
    "al_right": {"en": "Right", "fa": "راست"},
    "rounded": {"en": "Rounded corners (Windows 11)", "fa": "گوشه‌های گرد (ویندوز ۱۱)"},
    "preset": {"en": "Position", "fa": "جای پنجره"},
    "pr_bottom": {"en": "Bottom", "fa": "پایین"},
    "pr_top": {"en": "Top", "fa": "بالا"},
    "pr_center": {"en": "Center", "fa": "وسط"},
    "pr_custom": {"en": "Custom (dragged)", "fa": "دلخواه (جابه‌جا شده)"},
    "click_through_hint": {"en": "While on, use the tray icon to turn it off.",
                           "fa": "وقتی روشن است، برای خاموش کردن از آیکن کنار ساعت استفاده کنید."},
    "auto": {"en": "auto", "fa": "خودکار"},
    "reset_appearance": {"en": "Reset appearance", "fa": "بازگرداندن ظاهر پیش‌فرض"},
    "global_offset": {"en": "Global offset (ms, + = lyrics later)", "fa": "تأخیر کلی (میلی‌ثانیه، + یعنی متن دیرتر)"},
    "song_offset": {"en": "This song's offset (ms)", "fa": "تأخیر همین آهنگ (میلی‌ثانیه)"},
    "no_song": {"en": "Nothing playing", "fa": "چیزی پخش نمی‌شود"},
    "offset_step": {"en": "Step (ms)", "fa": "گام (میلی‌ثانیه)"},
    "reset": {"en": "Reset", "fa": "صفر"},
    "hotkeys": {"en": "Global hotkeys", "fa": "کلیدهای میان‌بر سراسری"},
    "hk_up": {"en": "Lyrics later", "fa": "متن دیرتر"},
    "hk_down": {"en": "Lyrics earlier", "fa": "متن زودتر"},
    "hk_toggle": {"en": "Show / hide", "fa": "نمایش / پنهان"},
    "apply": {"en": "Apply", "fa": "اعمال"},
    "hk_hint": {"en": "Format: ctrl+alt+up, ctrl+shift+l, alt+f9 …", "fa": "قالب: ctrl+alt+up، ctrl+shift+l، alt+f9 …"},
    "hk_failed": {"en": "Could not register: {keys} (used by another app?)", "fa": "ثبت نشد: {keys} (برنامه‌ی دیگری استفاده می‌کند؟)"},
    "hk_ok": {"en": "Hotkeys active.", "fa": "کلیدهای میان‌بر فعال‌اند."},
    "proxy_mode": {"en": "Proxy", "fa": "پراکسی"},
    "px_none": {"en": "No proxy", "fa": "بدون پراکسی"},
    "px_system": {"en": "System proxy", "fa": "پراکسی سیستم"},
    "px_custom": {"en": "Custom", "fa": "دلخواه"},
    "proxy_url": {"en": "Proxy URL (http://host:port or socks5://host:port)",
                  "fa": "آدرس پراکسی (http://host:port یا socks5://host:port)"},
    "proxy_bad": {"en": "Invalid proxy URL.", "fa": "آدرس پراکسی معتبر نیست."},
    "test_connection": {"en": "Test connection", "fa": "آزمایش اتصال"},
    "conn_ok": {"en": "LRCLIB reachable ({ms} ms).", "fa": "اتصال به LRCLIB برقرار است ({ms} میلی‌ثانیه)."},
    "conn_fail": {"en": "Connection failed: {err}", "fa": "اتصال برقرار نشد: {err}"},
    "about_text": {
        "en": "Dualyrics {v}\nSynced lyrics with a translation under every line, for any player that "
              "reports to Windows media controls.\n\nLyrics: LRCLIB (lrclib.net). Translation: Google Gemini "
              "or Google Translate.\nOpen source, MIT license.\n\nYour settings, keys and cache: {path}",
        "fa": "Dualyrics {v}\nمتن هم‌زمان آهنگ با ترجمه زیر هر خط، برای هر پخش‌کننده‌ای که به کنترل‌های "
              "رسانه‌ی ویندوز گزارش می‌دهد.\n\nمتن آهنگ: LRCLIB (lrclib.net). ترجمه: Google Gemini یا Google Translate.\n"
              "متن‌باز، مجوز MIT.\n\nتنظیمات، کلیدها و کش شما: {path}",
    },
    "open_data": {"en": "Open data folder", "fa": "باز کردن پوشه‌ی داده‌ها"},
    "open_log": {"en": "Open log", "fa": "باز کردن لاگ"},
    "close": {"en": "Close", "fa": "بستن"},
}


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in ("en", "fa") else "en"


def language() -> str:
    return _lang


def is_rtl() -> bool:
    return _lang == "fa"


def t(key: str, **kw: object) -> str:
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_lang) or entry["en"]
    text = text.format(**kw) if kw else text
    return rtl_embed(text) if _lang == "fa" else text
