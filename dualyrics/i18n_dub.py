"""v2.1 interface strings of dubbing (Settings > Dubbing, tray, overlay menu, notices), Settings > Usage, the
About page's GitHub links and the Liquid Glass preset, English and Persian.

Merged into :data:`dualyrics.i18n.STRINGS` like the v2 table.
"""

from __future__ import annotations

S: dict[str, dict[str, str]] = {
    # ---- sections
    "sec_failed": {"en": "This page could not be shown ({err}). The log has the details.",
                   "fa": "این صفحه نمایش داده نشد ({err}). جزئیات در فایل گزارش است."},
    "sec_dubbing": {"en": "Dubbing", "fa": "دوبله"},
    "sec_dubbing_caption": {
        "en": "Hear the translation spoken aloud: lyrics, on-screen subtitles and live captions, with other apps "
              "turned down while the voice speaks.",
        "fa": "ترجمه را با صدا بشنوید: متن آهنگ، زیرنویس روی صفحه و زیرنویس زنده؛ وقتی صدای دوبله پخش می‌شود "
              "صدای برنامه‌های دیگر کم می‌شود."},
    "sec_usage": {"en": "Usage", "fa": "مصرف"},
    "sec_usage_caption": {
        "en": "What your Gemini keys used: requests, tokens, audio and the free-tier daily limits.",
        "fa": "مصرف کلیدهای Gemini شما: درخواست‌ها، توکن‌ها، صدا و سقف روزانه‌ی رایگان."},

    # ---- dubbing: tray, overlay menu, hotkey, notices
    "dub_toggle": {"en": "Dubbing (speak the translation)", "fa": "دوبله (خواندن ترجمه با صدا)"},
    "dub_on": {"en": "Dubbing on", "fa": "دوبله روشن شد"},
    "dub_off": {"en": "Dubbing off", "fa": "دوبله خاموش شد"},
    "dub_fallback": {"en": "{frm} cannot speak now; using {to} instead.",
                     "fa": "{frm} الان نمی‌تواند بخواند؛ به‌جایش {to} استفاده می‌شود."},
    "dub_st_no_engine": {
        "en": "No voice is available: add a Gemini key, or install the Edge or Piper voices below.",
        "fa": "هیچ صدایی در دسترس نیست: یک کلید Gemini اضافه کنید یا صداهای Edge یا Piper را در پایین نصب کنید."},
    "dub_st_failed": {"en": "The last line could not be spoken (see the log).",
                      "fa": "خط آخر خوانده نشد (فایل گزارش را ببینید)."},
    "dub_st_live_limit": {
        "en": "Gemini Live reached its limit; captions are spoken with another voice for now.",
        "fa": "Gemini Live به سقف مصرف رسید؛ فعلاً زیرنویس‌ها با صدای دیگری خوانده می‌شوند."},
    "dub_st_live_failed": {
        "en": "Gemini Live is not available; captions are spoken with another voice for now.",
        "fa": "Gemini Live در دسترس نیست؛ فعلاً زیرنویس‌ها با صدای دیگری خوانده می‌شوند."},

    # ---- Settings > Dubbing
    "dp_main": {"en": "Dubbing", "fa": "دوبله"},
    "dp_enabled": {"en": "Speak the translation", "fa": "خواندن ترجمه با صدا"},
    "dp_enabled_hint": {
        "en": "Also Ctrl+Alt+D, the tray and the overlay menu. Off by default. An app profile can have its own "
              "dubbing settings (Settings > Profiles).",
        "fa": "با Ctrl+Alt+D، منوی نوار وظیفه و منوی زیرنویس هم می‌شود. پیش‌فرض خاموش است. هر پروفایل برنامه "
              "می‌تواند تنظیمات دوبله‌ی خودش را داشته باشد (تنظیمات › پروفایل‌ها)."},
    "dp_status": {"en": "Now: {what}", "fa": "الان: {what}"},
    "dp_status_ok": {"en": "ready", "fa": "آماده"},
    "dp_status_engine": {"en": "speaking with {engine}", "fa": "در حال خواندن با {engine}"},
    "dp_what": {"en": "What is spoken", "fa": "چه چیزی خوانده شود"},
    "dp_lyrics": {"en": "Song lyrics", "fa": "متن آهنگ"},
    "dp_lyrics_hint": {"en": "Each line is made ahead and starts with the line.",
                       "fa": "هر خط از قبل ساخته می‌شود و هم‌زمان با خود خط شروع می‌شود."},
    "dp_captions": {"en": "On-screen subtitles and live captions", "fa": "زیرنویس روی صفحه و زیرنویس زنده"},
    "dp_captions_hint": {"en": "A caption is spoken once it stops changing.",
                         "fa": "هر زیرنویس وقتی دیگر تغییر نکند خوانده می‌شود."},
    "dp_engine_group": {"en": "Voice", "fa": "صدا"},
    "dp_engine": {"en": "Engine", "fa": "موتور صدا"},
    "dp_engine_hint": {
        "en": "Automatic: Gemini Live for the audio of games and films, Gemini voices for text when you have a "
              "key, else Edge, else Piper. An engine that fails hands the line to the next one.",
        "fa": "خودکار: Gemini Live برای صدای بازی و فیلم، صداهای Gemini برای متن وقتی کلید دارید، وگرنه Edge و "
              "بعد Piper. موتوری که خطا بدهد خط را به موتور بعدی می‌دهد."},
    "dp_eng_auto": {"en": "Automatic", "fa": "خودکار"},
    "dp_eng_gemini_live": {"en": "Gemini Live (speech to speech)", "fa": "Gemini Live (صدا به صدا)"},
    "dp_eng_gemini_tts": {"en": "Gemini voices", "fa": "صداهای Gemini"},
    "dp_eng_edge": {"en": "Microsoft Edge voices (free, online)", "fa": "صداهای Microsoft Edge (رایگان، آنلاین)"},
    "dp_eng_piper": {"en": "Piper (offline)", "fa": "Piper (آفلاین)"},
    "dp_voice": {"en": "Voice", "fa": "صدا"},
    "dp_voice_hint": {"en": "For {lang}. Automatic picks a voice of the target language.",
                      "fa": "برای {lang}. حالت خودکار صدایی به زبان مقصد انتخاب می‌کند."},
    "dp_voice_auto": {"en": "Automatic", "fa": "خودکار"},
    "dp_speed": {"en": "Speed", "fa": "سرعت"},
    "dp_volume": {"en": "Voice volume", "fa": "بلندی صدای دوبله"},
    "dp_duck_group": {"en": "Other apps", "fa": "برنامه‌های دیگر"},
    "dp_duck": {"en": "Turn other apps down while the voice speaks", "fa": "کم کردن صدای برنامه‌های دیگر هنگام دوبله"},
    "dp_duck_hint": {
        "en": "Their volume comes back a moment after the voice stops, and when Dualyrics quits. A volume you "
              "change meanwhile is kept.",
        "fa": "صدایشان کمی بعد از تمام شدن دوبله و هنگام بستن Dualyrics برمی‌گردد. اگر خودتان در این فاصله "
              "صدا را تغییر دهید، همان می‌ماند."},
    "dp_duck_amount": {"en": "Lower by", "fa": "مقدار کاهش"},
    "dp_gemini_group": {"en": "Gemini models", "fa": "مدل‌های Gemini"},
    "dp_gemini_hint": {
        "en": "Automatic uses the models your key offers (asked from Google every few hours). Every request is "
              "counted in Settings > Usage.",
        "fa": "حالت خودکار از مدل‌هایی که کلید شما دارد استفاده می‌کند (هر چند ساعت از Google پرسیده می‌شود). "
              "هر درخواست در تنظیمات › مصرف شمرده می‌شود."},
    "dp_tts_model": {"en": "Text to speech", "fa": "متن به صدا"},
    "dp_live_model": {"en": "Gemini Live", "fa": "Gemini Live"},
    "dp_model_auto": {"en": "Automatic", "fa": "خودکار"},
    "dp_no_key": {"en": "Gemini voices need a Gemini key (Settings > Translation).",
                  "fa": "صداهای Gemini کلید Gemini لازم دارند (تنظیمات › ترجمه)."},
    "dp_install_group": {"en": "Free voices", "fa": "صداهای رایگان"},
    "dp_edge": {"en": "Microsoft Edge voices", "fa": "صداهای Microsoft Edge"},
    "dp_edge_hint": {"en": "The edge-tts package (a few hundred KB, installed with pip).",
                     "fa": "بسته‌ی edge-tts (چند صد کیلوبایت، با pip نصب می‌شود)."},
    "dp_piper": {"en": "Piper voice for {lang}", "fa": "صدای Piper برای {lang}"},
    "dp_piper_hint": {"en": "About 20 MB for the program and 20-60 MB per voice, into your Dualyrics folder.",
                      "fa": "حدود ۲۰ مگابایت برای برنامه و ۲۰ تا ۶۰ مگابایت برای هر صدا، در پوشه‌ی Dualyrics."},
    "dp_piper_none": {"en": "Piper has no voice for {lang}.", "fa": "Piper صدایی برای {lang} ندارد."},
    "dp_installed": {"en": "Installed", "fa": "نصب شده"},
    "dp_install": {"en": "Install", "fa": "نصب"},
    "dp_installing": {"en": "Installing…", "fa": "در حال نصب…"},
    "dp_install_ok": {"en": "Installed.", "fa": "نصب شد."},
    "dp_install_fail": {"en": "Could not install: {err}", "fa": "نصب نشد: {err}"},
    "dp_progress": {"en": "{done} of {total} MB", "fa": "{done} از {total} مگابایت"},
    "dp_test": {"en": "Test the voice", "fa": "آزمایش صدا"},
    "dp_test_fail": {"en": "No engine could speak the test line.", "fa": "هیچ موتوری نتوانست خط آزمایشی را بخواند."},

    # ---- notices from the translator and the usage meter
    "us_fallback_model": {"en": "Gemini {frm} reached its limit on key #{key}; using {to}.",
                          "fa": "Gemini {frm} روی کلید #{key} به سقف رسید؛ از {to} استفاده می‌شود."},
    "us_fallback_free": {"en": "Gemini reached its limit; using the free translator for now.",
                         "fa": "Gemini به سقف رسید؛ فعلاً از مترجم رایگان استفاده می‌شود."},
    "us_warn_toast": {"en": "{model} on key #{key}: {used} of {limit} requests today ({pct}%).",
                      "fa": "{model} روی کلید #{key}: {used} از {limit} درخواست امروز ({pct}٪)."},

    # ---- Settings > Usage
    "us_totals": {"en": "Totals", "fa": "جمع کل"},
    "us_totals_hint": {"en": "Requests to Gemini from translation, dubbing and Live, and the ones the cache saved.",
                       "fa": "درخواست‌های ترجمه، دوبله و حالت زنده به Gemini، و آن‌هایی که حافظه‌ی موقت صرفه‌جویی کرد."},
    "us_today": {"en": "Today", "fa": "امروز"},
    "us_week": {"en": "Last 7 days", "fa": "۷ روز اخیر"},
    "us_month": {"en": "This month", "fa": "این ماه"},
    "us_requests": {"en": "requests", "fa": "درخواست"},
    "us_tokens": {"en": "tokens", "fa": "توکن"},
    "us_audio": {"en": "audio", "fa": "صدا"},
    "us_tile_detail": {"en": "{tokens} tokens · {audio} audio\n{cached} from cache · {limited} rate-limited",
                       "fa": "{tokens} توکن · {audio} صدا\n{cached} از حافظه‌ی موقت · {limited} محدودشده"},
    "us_daily": {"en": "Day by day", "fa": "روزبه‌روز"},
    "us_daily_hint": {"en": "The last {days} days.", "fa": "{days} روز گذشته."},
    "us_limits": {"en": "Today's free-tier limits", "fa": "سقف رایگان امروز"},
    "us_limits_hint": {"en": "Per model and key. Google resets them at {time} (your time).",
                       "fa": "برای هر مدل و کلید. Google ساعت {time} (به وقت شما) آن‌ها را صفر می‌کند."},
    "us_none_today": {"en": "No Gemini request today.", "fa": "امروز درخواستی به Gemini نرفته."},
    "us_limit_row": {"en": "{model} · key #{key}: {used} / {limit}",
                     "fa": "{model} · کلید #{key}: {used} / {limit}"},
    "us_limited_n": {"en": "({n} rate-limited)", "fa": "({n} بار محدود شد)"},
    "us_edit_limits": {"en": "Daily limits", "fa": "سقف‌های روزانه"},
    "us_edit_limits_hint": {
        "en": "Requests per day per key. These are estimates of the free tier; set what your project really has.",
        "fa": "درخواست در روز برای هر کلید. این‌ها تخمین سقف رایگان‌اند؛ مقدار واقعی پروژه‌ی خودتان را بگذارید."},
    "us_default_limit": {"en": "Estimate: {n}", "fa": "تخمین: {n}"},
    "us_reset_limits": {"en": "Back to the estimates", "fa": "برگشت به تخمین‌ها"},
    "us_warn": {"en": "Warn at 80 % of a limit", "fa": "هشدار در ۸۰٪ سقف"},
    "us_warn_hint": {"en": "A short notice on the overlay, once per model and key a day.",
                     "fa": "یک پیام کوتاه روی زیرنویس، روزی یک بار برای هر مدل و کلید."},
    "us_breakdown": {"en": "Breakdown", "fa": "جزئیات"},
    "us_by_model": {"en": "By model", "fa": "بر اساس مدل"},
    "us_by_key": {"en": "By key", "fa": "بر اساس کلید"},
    "us_by_media": {"en": "By song or video", "fa": "بر اساس آهنگ یا ویدیو"},
    "us_key_n": {"en": "Key #{n}", "fa": "کلید #{n}"},
    "us_nothing": {"en": "Nothing yet.", "fa": "هنوز چیزی نیست."},
    "us_row": {"en": "{name}: {requests} requests · {tokens} tokens · {audio} · {cached} from cache",
               "fa": "{name}: {requests} درخواست · {tokens} توکن · {audio} · {cached} از حافظه‌ی موقت"},
    "us_data": {"en": "Data", "fa": "داده‌ها"},
    "us_data_hint": {"en": "Kept on this PC only, in {path}. Keys are named by their position, never stored.",
                     "fa": "فقط روی همین کامپیوتر، در {path}. کلیدها با شماره‌شان نام برده می‌شوند و خودشان ذخیره نمی‌شوند."},
    "us_export": {"en": "Export CSV…", "fa": "خروجی CSV…"},
    "us_exported": {"en": "{n} rows saved to {name}.", "fa": "{n} ردیف در {name} ذخیره شد."},
    "us_reset": {"en": "Reset usage…", "fa": "پاک کردن آمار…"},
    "us_reset_confirm": {"en": "Delete the whole usage history? This cannot be undone.",
                         "fa": "کل تاریخچه‌ی مصرف پاک شود؟ قابل برگشت نیست."},
    "us_reset_done": {"en": "Usage history deleted.", "fa": "تاریخچه‌ی مصرف پاک شد."},

    # ---- About: GitHub, tray
    "tray_github": {"en": "GitHub / Star", "fa": "GitHub / ستاره"},
    "about_github": {"en": "GitHub", "fa": "GitHub"},
    "about_github_hint": {"en": "Dualyrics is open source. A star helps other people find it.",
                          "fa": "Dualyrics متن‌باز است. یک ستاره کمک می‌کند دیگران هم پیدایش کنند."},
    "about_star": {"en": "Star on GitHub", "fa": "ستاره در GitHub"},
    "about_repo": {"en": "Source code", "fa": "کد منبع"},
    "about_issues": {"en": "Report a problem", "fa": "گزارش مشکل"},
    "about_releases": {"en": "Releases", "fa": "نسخه‌ها"},

    # ---- the Liquid Glass preset
    "tpl_liquid_glass": {"en": "Liquid Glass", "fa": "شیشه‌ی مایع"},
    "tpl_liquid_glass_desc": {"en": "Desktop, videos, any backdrop", "fa": "دسکتاپ، ویدیو، هر پس‌زمینه‌ای"},
}
