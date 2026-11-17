"""v2.1 interface strings (Settings > Sources, the subtitle-area picker), English and Persian.

Merged into :data:`dualyrics.i18n.STRINGS` like the v2 table.
"""

from __future__ import annotations

S: dict[str, dict[str, str]] = {
    "sec_sources": {"en": "Sources", "fa": "منبع‌ها"},
    "sec_sources_caption": {
        "en": "Where the lines come from: the lyrics or subtitles the app already shows first, the audio when "
              "there are none.",
        "fa": "خط‌ها از کجا بیایند: اول متن یا زیرنویسی که خود برنامه نشان می‌دهد، و اگر نبود، از روی صدا."},
    "src_scope": {"en": "Settings for", "fa": "تنظیمات برای"},
    "src_scope_hint": {"en": "All apps, or one app profile with its own order and subtitle area.",
                       "fa": "همه‌ی برنامه‌ها، یا یک پروفایل با ترتیب و ناحیه‌ی زیرنویس مخصوص خودش."},
    "src_scope_global": {"en": "All apps", "fa": "همه‌ی برنامه‌ها"},
    "src_custom": {"en": "This app has its own order", "fa": "این برنامه ترتیب مخصوص خودش را دارد"},
    "src_custom_hint": {"en": "Off: this app follows the order for all apps.",
                        "fa": "خاموش: این برنامه از ترتیبِ همه‌ی برنامه‌ها پیروی می‌کند."},
    "src_order": {"en": "Order", "fa": "ترتیب"},
    "src_order_hint": {"en": "The first source that has something to show wins. Switch a source off to never "
                             "use it. A line and its translation are always shown together.",
                       "fa": "اولین منبعی که چیزی برای نمایش دارد انتخاب می‌شود. منبعی را خاموش کنید تا هرگز "
                             "استفاده نشود. هر خط همیشه با ترجمه‌اش زیر آن نمایش داده می‌شود."},
    "src_lyrics": {"en": "Synced lyrics", "fa": "متن هم‌زمان آهنگ"},
    "src_lyrics_desc": {"en": "Lyrics of the song that is playing (LRCLIB). In browsers only an exact match.",
                        "fa": "متن آهنگی که پخش می‌شود (LRCLIB). در مرورگرها فقط با تطابق دقیق."},
    "src_screen": {"en": "On-screen subtitles", "fa": "زیرنویس روی صفحه"},
    "src_screen_desc": {"en": "Captions the app already shows: web player captions, else text read from the "
                              "subtitle area of the window in front.",
                        "fa": "زیرنویسی که خود برنامه نشان می‌دهد: زیرنویس پخش‌کننده‌های وب، وگرنه متنِ خوانده‌شده "
                              "از ناحیه‌ی زیرنویسِ پنجره‌ی جلو."},
    "src_audio": {"en": "Audio (Live mode)", "fa": "صدا (زیرنویس زنده)"},
    "src_audio_desc": {"en": "Speech recognition of what the speakers play.",
                       "fa": "تشخیص گفتار از صدایی که پخش می‌شود."},
    "src_up": {"en": "Move up", "fa": "بالاتر"},
    "src_down": {"en": "Move down", "fa": "پایین‌تر"},
    "src_grp_lyrics": {"en": "Lyrics matching", "fa": "تطبیق متن آهنگ"},
    "src_strict": {"en": "Strict match in browsers", "fa": "تطابق دقیق در مرورگرها"},
    "src_strict_hint": {"en": "Title, artist and length must agree, so an ordinary video never gets the lyrics "
                              "of another song.",
                        "fa": "عنوان، خواننده و طول باید یکی باشند تا یک ویدیوی معمولی هرگز متن آهنگ دیگری را "
                              "نگیرد."},
    "src_tolerance": {"en": "Allowed length difference", "fa": "اختلاف مجاز طول"},
    "src_grp_screen": {"en": "On-screen subtitles", "fa": "زیرنویس روی صفحه"},
    "src_grp_screen_hint": {"en": "Only the window in front is read, and only for video players, browsers, games "
                                  "and fullscreen apps (or apps with their own subtitle area). Works offline.",
                            "fa": "فقط پنجره‌ی جلو خوانده می‌شود، و فقط برای پخش‌کننده‌های ویدیو، مرورگرها، بازی‌ها و "
                                  "برنامه‌های تمام‌صفحه (یا برنامه‌هایی که ناحیه‌ی زیرنویس خودشان را دارند). بدون اینترنت."},
    "src_uia": {"en": "Read web player captions (UI Automation)", "fa": "خواندن زیرنویس پخش‌کننده‌های وب (UI Automation)"},
    "src_uia_hint": {"en": "Exact text from YouTube and other web players in Chrome and Edge.",
                     "fa": "متن دقیق از یوتیوب و پخش‌کننده‌های وب دیگر در Chrome و Edge."},
    "src_ocr": {"en": "Read the subtitle area (OCR)", "fa": "خواندن ناحیه‌ی زیرنویس (OCR)"},
    "src_ocr_hint": {"en": "Windows' built-in text recognition, a few times a second and only when the area "
                           "changes.",
                     "fa": "تشخیص متن داخلی ویندوز، چند بار در ثانیه و فقط وقتی ناحیه تغییر کند."},
    "src_ocr_lang": {"en": "Text language", "fa": "زبان متن"},
    "src_ocr_lang_hint": {"en": "Add languages in Windows Settings > Time & language > Language > Optical "
                                "character recognition.",
                          "fa": "زبان‌های بیشتر را از تنظیمات ویندوز > زمان و زبان > زبان > تشخیص نوری نویسه‌ها "
                                "اضافه کنید."},
    "src_ocr_auto": {"en": "Automatic (Windows languages)", "fa": "خودکار (زبان‌های ویندوز)"},
    "src_fps": {"en": "Readings per second", "fa": "دفعات خواندن در ثانیه"},
    "src_region": {"en": "Subtitle area", "fa": "ناحیه‌ی زیرنویس"},
    "src_region_value": {"en": "{x0}–{x1} % across, {y0}–{y1} % down ({scope})",
                         "fa": "افقی {x0} تا {x1} درصد، عمودی {y0} تا {y1} درصد ({scope})"},
    "src_region_own": {"en": "own area", "fa": "ناحیه‌ی مخصوص"},
    "src_region_global": {"en": "area for all apps", "fa": "ناحیه‌ی همه‌ی برنامه‌ها"},
    "src_pick": {"en": "Pick on a window…", "fa": "انتخاب روی پنجره…"},
    "src_pick_hint": {"en": "Click the window that shows the subtitles (Esc cancels)…",
                      "fa": "روی پنجره‌ای که زیرنویس را نشان می‌دهد کلیک کنید (Esc برای لغو)…"},
    "src_reset_region": {"en": "Reset", "fa": "بازنشانی"},
    "src_test": {"en": "Test on a window…", "fa": "آزمایش روی پنجره…"},
    "src_test_running": {"en": "Reading…", "fa": "در حال خواندن…"},
    "src_test_result": {"en": "Web captions: {uia}\nOCR ({lang}): {ocr}", "fa": "زیرنویس وب: {uia}\nOCR ({lang}): {ocr}"},
    "src_test_none": {"en": "(nothing)", "fa": "(چیزی نیست)"},
    "src_test_na": {"en": "(not available)", "fa": "(در دسترس نیست)"},
    "src_uia_classes": {"en": "Caption element classes", "fa": "کلاس‌های عنصر زیرنویس"},
    "src_uia_classes_hint": {"en": "Class names (or parts of them) of caption elements in web players, comma "
                                   "separated.",
                             "fa": "نام کلاس (یا بخشی از آن) عنصرهای زیرنویس در پخش‌کننده‌های وب، با ویرگول جدا."},
    "src_grp_pivot": {"en": "Any language", "fa": "هر زبانی"},
    "src_grp_pivot_hint": {"en": "For speech and on-screen text in a language you do not know.",
                           "fa": "برای گفتار و متن روی صفحه به زبانی که نمی‌دانید."},
    "src_pivot": {"en": "Pivot through English", "fa": "ترجمه از راه انگلیسی"},
    "src_pivot_hint": {"en": "Speech or text in any language becomes English first, then your language. Speech "
                             "needs a multilingual model.",
                       "fa": "گفتار یا متن به هر زبانی اول به انگلیسی و بعد به زبان شما برگردانده می‌شود. برای "
                             "گفتار، مدل چندزبانه لازم است."},
    "src_show_original": {"en": "Also show the original-language line", "fa": "خط زبان اصلی هم نشان داده شود"},
    "src_show_original_hint": {"en": "The line in its own language on top, the translation under it and the "
                                     "English in a small third line. Speech is written down twice (slower).",
                               "fa": "خط به زبان خودش بالا، ترجمه زیر آن و انگلیسی در یک خط کوچک سوم. گفتار دو بار "
                                     "نوشته می‌شود (کندتر)."},
    "src_pivot_note": {"en": "With an English-only model Live mode understands English speech only.",
                       "fa": "با مدل فقط‌انگلیسی، زیرنویس زنده فقط گفتار انگلیسی را می‌فهمد."},
    "src_unavailable": {"en": "Not available on this PC: {what}", "fa": "روی این رایانه در دسترس نیست: {what}"},
    "rp_help": {"en": "Drag a rectangle around the subtitles.   Enter or Save: keep it   ·   Esc: cancel",
                "fa": "دور زیرنویس یک مستطیل بکشید.   Enter یا ذخیره: نگه‌داشتن   ·   Esc: لغو"},
    "rp_save": {"en": "Save", "fa": "ذخیره"},
    "rp_cancel": {"en": "Cancel", "fa": "لغو"},
    "rp_saved": {"en": "Subtitle area saved ({scope}).", "fa": "ناحیه‌ی زیرنویس ذخیره شد ({scope})."},
}
