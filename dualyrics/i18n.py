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
    "tab_sync": {"en": "Sync & hotkeys", "fa": "همگام‌سازی و کلیدها"},
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
    "yes": {"en": "yes", "fa": "بله"},
    "on": {"en": "On", "fa": "روشن"},
    "off": {"en": "Off", "fa": "خاموش"},
    # v2: modes and translation only
    "mode": {"en": "Mode", "fa": "حالت"},
    "mode_auto": {"en": "Smart auto (lyrics or live captions)", "fa": "خودکار هوشمند (متن آهنگ یا زیرنویس زنده)"},
    "mode_lyrics": {"en": "Lyrics only", "fa": "فقط متن آهنگ"},
    "mode_live": {"en": "Live captions only", "fa": "فقط زیرنویس زنده"},
    "translation_only": {"en": "Translation only (hide the original)", "fa": "فقط ترجمه (متن اصلی پنهان شود)"},
    "translation_only_hint": {"en": "The original is still shown when a line has no translation.",
                              "fa": "اگر خطی ترجمه نداشته باشد، متن اصلی همچنان نشان داده می‌شود."},
    # v2: Live mode
    "tab_live": {"en": "Live", "fa": "زیرنویس زنده"},
    "live_pause": {"en": "Pause live captions", "fa": "توقف زیرنویس زنده"},
    "hk_live_pause": {"en": "Pause / resume live captions", "fa": "توقف / ادامه‌ی زیرنویس زنده"},
    "lv_missing": {"en": "Live captions are not installed — Settings › Live › Install.",
                   "fa": "زیرنویس زنده نصب نیست — تنظیمات › زیرنویس زنده › نصب."},
    "lv_downloading": {"en": "Downloading the speech model {model}: {pct}%",
                       "fa": "در حال دانلود مدل گفتار {model}: {pct}٪"},
    "lv_loading": {"en": "Loading the speech model {model}…", "fa": "در حال بارگذاری مدل گفتار {model}…"},
    "lv_error": {"en": "Live captions failed: {err}", "fa": "زیرنویس زنده کار نکرد: {err}"},
    "lv_listening": {"en": "Listening…", "fa": "در حال گوش دادن…"},
    "lv_paused": {"en": "Live captions paused", "fa": "زیرنویس زنده متوقف شد"},
    "lv_resumed": {"en": "Live captions on", "fa": "زیرنویس زنده روشن شد"},
    "lv_state_missing": {"en": "Live captions are not installed yet (missing: {pkgs}). They turn what your speakers "
                               "play into subtitles, offline, for games, films and videos.",
                         "fa": "زیرنویس زنده هنوز نصب نشده است (کم است: {pkgs}). این بخش هر صدایی را که از بلندگو "
                               "پخش می‌شود، بدون اینترنت، برای بازی و فیلم و ویدیو به زیرنویس تبدیل می‌کند."},
    "lv_state_ready": {"en": "Installed. Speech model: {model} ({size} MB, downloaded: {have}). Running on: {device}.",
                       "fa": "نصب است. مدل گفتار: {model} ({size} مگابایت، دانلود شده: {have}). اجرا روی: {device}."},
    "lv_not_downloaded": {"en": "not yet (downloads on first use)", "fa": "هنوز نه (بار اول خودکار دانلود می‌شود)"},
    "lv_device_unknown": {"en": "not started yet", "fa": "هنوز اجرا نشده"},
    "lv_install": {"en": "Install Live mode", "fa": "نصب زیرنویس زنده"},
    "lv_reinstall": {"en": "Update Live mode", "fa": "به‌روزرسانی زیرنویس زنده"},
    "lv_gpu": {"en": "Include NVIDIA GPU support (about 1 GB more)", "fa": "با پشتیبانی کارت گرافیک NVIDIA (حدود ۱ گیگابایت بیشتر)"},
    "lv_installing": {"en": "Installing… this can take a few minutes.", "fa": "در حال نصب… ممکن است چند دقیقه طول بکشد."},
    "lv_install_ok": {"en": "Live mode installed.", "fa": "زیرنویس زنده نصب شد."},
    "lv_install_fail": {"en": "Install failed: {err}", "fa": "نصب انجام نشد: {err}"},
    "lv_install_hint": {"en": "Downloads faster-whisper, PyAudioWPatch and numpy through your proxy setting. "
                              "Nothing is sent anywhere while you use it: speech is recognised on this PC.",
                        "fa": "faster-whisper و PyAudioWPatch و numpy را با تنظیم پراکسی شما دانلود می‌کند. موقع "
                              "استفاده چیزی جایی فرستاده نمی‌شود: گفتار روی همین کامپیوتر تشخیص داده می‌شود."},
    "lv_enabled": {"en": "Use live captions", "fa": "استفاده از زیرنویس زنده"},
    "lv_enabled_hint": {"en": "Smart auto switches to live captions for games, films and videos.",
                        "fa": "حالت خودکار هوشمند برای بازی، فیلم و ویدیو به زیرنویس زنده می‌رود."},
    "lv_no_lyrics": {"en": "Caption songs that have no lyrics", "fa": "زیرنویس زنده برای آهنگ‌هایی که متن ندارند"},
    "lv_model": {"en": "Speech model", "fa": "مدل گفتار"},
    "lv_model_tiny": {"en": "fastest", "fa": "سریع‌ترین"},
    "lv_model_base": {"en": "fast", "fa": "سریع"},
    "lv_model_small": {"en": "recommended", "fa": "پیشنهادی"},
    "lv_model_medium": {"en": "most accurate, needs a GPU", "fa": "دقیق‌ترین، کارت گرافیک لازم است"},
    "lv_english_only": {"en": "English-only model", "fa": "مدل فقط انگلیسی"},
    "lv_english_only_hint": {"en": "More accurate for English games and films; other languages are not recognised.",
                             "fa": "برای بازی و فیلم انگلیسی دقیق‌تر است؛ زبان‌های دیگر تشخیص داده نمی‌شوند."},
    "lv_source": {"en": "Spoken language", "fa": "زبان گفتار"},
    "lv_detect": {"en": "Detect automatically", "fa": "تشخیص خودکار"},
    "lv_device": {"en": "Run on", "fa": "اجرا روی"},
    "lv_dev_auto": {"en": "Automatic (GPU when available)", "fa": "خودکار (کارت گرافیک اگر باشد)"},
    "lv_dev_gpu": {"en": "GPU (NVIDIA)", "fa": "کارت گرافیک (NVIDIA)"},
    "lv_download": {"en": "Download model now", "fa": "دانلود مدل همین حالا"},
    "lv_downloaded": {"en": "Model {model} is ready.", "fa": "مدل {model} آماده است."},
    "lv_download_fail": {"en": "Download failed: {err}", "fa": "دانلود انجام نشد: {err}"},
    "lv_tuning": {"en": "Fine-tuning", "fa": "تنظیم دقیق"},
    "lv_vad": {"en": "Voice sensitivity", "fa": "حساسیت به صدا"},
    "lv_max_segment": {"en": "Longest piece before a cut", "fa": "بیشترین طول هر تکه"},
    "lv_cps": {"en": "Reading speed (characters per second)", "fa": "سرعت خواندن (حرف در ثانیه)"},
    "lv_min_display": {"en": "Shortest time on screen", "fa": "کمترین زمان نمایش"},
    "lv_context": {"en": "Earlier lines sent as context", "fa": "تعداد خط‌های قبلی برای فهم متن"},
    "lv_transcripts": {"en": "Save transcripts", "fa": "ذخیره‌ی متن گفت‌وگوها"},
    "lv_open_transcripts": {"en": "Open transcripts folder", "fa": "باز کردن پوشه‌ی متن‌ها"},
    "lv_pause": {"en": "Pause live captions", "fa": "توقف زیرنویس زنده"},
    "lv_resume": {"en": "Resume live captions", "fa": "ادامه‌ی زیرنویس زنده"},
    # v2: per-app profiles
    "tab_apps": {"en": "Apps", "fa": "برنامه‌ها"},
    "ap_enabled": {"en": "Per-app settings", "fa": "تنظیمات جدا برای هر برنامه"},
    "ap_enabled_hint": {"en": "The app in use decides the mode, look and place of the overlay.",
                        "fa": "برنامه‌ای که در حال استفاده است حالت، ظاهر و جای پنجره‌ی متن را تعیین می‌کند."},
    "ap_activation": {"en": "An app's settings apply when it is", "fa": "تنظیمات هر برنامه وقتی اعمال شود که"},
    "ap_act_running": {"en": "running or in front", "fa": "باز است یا جلوی صفحه است"},
    "ap_act_foreground": {"en": "the window in front only", "fa": "فقط پنجره‌ی جلوی صفحه باشد"},
    "ap_list": {"en": "Apps", "fa": "برنامه‌ها"},
    "ap_edit": {"en": "Edit", "fa": "ویرایش"},
    "ap_edit_title": {"en": "Settings for {name}", "fa": "تنظیمات {name}"},
    "ap_global": {"en": "Same as global", "fa": "مثل تنظیم کلی"},
    "ap_mode": {"en": "Mode", "fa": "حالت"},
    "ap_kind": {"en": "Kind of app", "fa": "نوع برنامه"},
    "ap_template": {"en": "Look (template)", "fa": "ظاهر (قالب)"},
    "ap_monitor": {"en": "Monitor", "fa": "نمایشگر"},
    "ap_width": {"en": "Width", "fa": "عرض"},
    "ap_add": {"en": "Add an app", "fa": "افزودن برنامه"},
    "ap_add_as": {"en": "Add as", "fa": "افزودن به‌عنوان"},
    "ap_preset_games": {"en": "Game (live captions, Game HUD, click-through)",
                        "fa": "بازی (زیرنویس زنده، حالت بازی، عبور کلیک)"},
    "ap_preset_video": {"en": "Video player", "fa": "پخش‌کننده‌ی ویدیو"},
    "ap_preset_music": {"en": "Music player", "fa": "پخش‌کننده‌ی موسیقی"},
    "ap_preset_custom": {"en": "Other app", "fa": "برنامه‌ی دیگر"},
    "ap_running": {"en": "From running apps…", "fa": "از برنامه‌های باز…"},
    "ap_browse": {"en": "Browse .exe…", "fa": "انتخاب فایل exe…"},
    "ap_programs": {"en": "Programs", "fa": "برنامه‌ها"},
    "ap_pick": {"en": "Pick a window", "fa": "انتخاب یک پنجره"},
    "ap_pick_hint": {"en": "Click any window within 20 seconds (Esc cancels).",
                     "fa": "تا ۲۰ ثانیه روی هر پنجره‌ای کلیک کنید (Esc برای لغو)."},
    "ap_pick_none": {"en": "No app picked.", "fa": "برنامه‌ای انتخاب نشد."},
    "ap_added": {"en": "Added: {name}", "fa": "اضافه شد: {name}"},
    "ap_exists": {"en": "{name} is already in the list.", "fa": "{name} از قبل در فهرست هست."},
    "ap_none_running": {"en": "No other app windows are open.", "fa": "پنجره‌ی برنامه‌ی دیگری باز نیست."},
    "ap_note": {"en": "Games show over the screen only in Borderless or Windowed mode, not in exclusive fullscreen.",
                "fa": "زیرنویس فقط روی بازی‌هایی دیده می‌شود که در حالت Borderless یا Windowed اجرا شوند، نه "
                      "تمام‌صفحه‌ی انحصاری."},
    "ap_builtin_spotify": {"en": "Spotify", "fa": "Spotify"},
    "ap_builtin_spotube": {"en": "Spotube", "fa": "Spotube"},
    "ap_builtin_music_apps": {"en": "Music apps", "fa": "برنامه‌های موسیقی"},
    "ap_builtin_browsers": {"en": "Web browsers", "fa": "مرورگرها"},
    "ap_builtin_vlc": {"en": "VLC", "fa": "VLC"},
    "ap_builtin_mpc": {"en": "MPC-HC / MPC-BE", "fa": "MPC-HC / MPC-BE"},
    "ap_builtin_potplayer": {"en": "PotPlayer", "fa": "PotPlayer"},
    "ap_builtin_media_any": {"en": "Other media players", "fa": "پخش‌کننده‌های دیگر"},
    "ap_builtin_fullscreen": {"en": "Fullscreen games and apps", "fa": "بازی‌ها و برنامه‌های تمام‌صفحه"},
    "ap_builtin_default": {"en": "Everything else", "fa": "همه‌ی برنامه‌های دیگر"},
    "pm_auto": {"en": "Smart auto", "fa": "خودکار هوشمند"},
    "pm_lyrics": {"en": "Lyrics", "fa": "متن آهنگ"},
    "pm_live": {"en": "Live captions", "fa": "زیرنویس زنده"},
    "pm_off": {"en": "Off", "fa": "خاموش"},
    "kind_music": {"en": "music", "fa": "موسیقی"},
    "kind_video": {"en": "video", "fa": "ویدیو"},
    "kind_browser": {"en": "browser", "fa": "مرورگر"},
    "kind_game": {"en": "game", "fa": "بازی"},
    "kind_other": {"en": "other", "fa": "دیگر"},
    # overlay templates (docs/DESIGN.md section 4)
    "tpl_cinema": {"en": "Cinema", "fa": "سینما"},
    "tpl_netflix": {"en": "Netflix", "fa": "نتفلیکس"},
    "tpl_karaoke": {"en": "Karaoke", "fa": "کارائوکه"},
    "tpl_minimal": {"en": "Minimal", "fa": "مینیمال"},
    "tpl_neon": {"en": "Neon", "fa": "نئون"},
    "tpl_game_hud": {"en": "Game HUD", "fa": "حالت بازی"},
    "tpl_glass": {"en": "Glass", "fa": "شیشه‌ای"},
    "tpl_persian_classic": {"en": "Persian Classic", "fa": "کلاسیک فارسی"},
}

from dualyrics.i18n_v2 import S as _V2  # noqa: E402 - v2 strings live in their own module

STRINGS.update(_V2)

from dualyrics.i18n_v21 import S as _V21  # noqa: E402 - v2.1 strings (Settings > Sources)

STRINGS.update(_V21)

from dualyrics.i18n_ai import S as _AI  # noqa: E402 - v2.1 strings (Settings > AI & API)

STRINGS.update(_AI)


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in ("en", "fa") else "en"


def language() -> str:
    return _lang


def is_rtl() -> bool:
    return _lang == "fa"


def t(key: str, /, **kw: object) -> str:   # positional-only: notices may carry their own "key" argument
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_lang) or entry["en"]
    text = text.format(**kw) if kw else text
    return rtl_embed(text) if _lang == "fa" else text

from dualyrics.i18n_dub import S as _DUB  # noqa: E402 - v2.1 strings (dubbing, usage, About links)

for _k, _v in _DUB.items():
    STRINGS.setdefault(_k, _v)       # never replaces a string defined above
