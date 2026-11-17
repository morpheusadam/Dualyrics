"""v2.2 interface strings (Live mode: Windows Live Captions engine, Silero VAD, interim commits; Translation: the
Gemini key pool and the offline translator), English and Persian. Merged into :data:`dualyrics.i18n.STRINGS` like the other tables.
"""

from __future__ import annotations

S: dict[str, dict[str, str]] = {
    "lv_lc_starting": {"en": "Starting Windows Live Captions…", "fa": "در حال راه‌اندازی Live Captions ویندوز…"},
    "lv_engine": {"en": "Speech engine", "fa": "موتور تشخیص گفتار"},
    "lv_engine_hint": {
        "en": "Windows Live Captions (Windows 11 22H2+) is fast, accurate and light; Whisper understands every "
              "language and can translate speech to English. Automatic uses Live Captions when the spoken language "
              "is one it has installed and the English pivot does not need Whisper, and switches to Whisper by "
              "itself when Live Captions cannot run.",
        "fa": "Live Captions ویندوز (ویندوز ۱۱ نسخه‌ی 22H2 به بعد) سریع، دقیق و سبک است؛ Whisper همه‌ی زبان‌ها را "
              "می‌فهمد و می‌تواند گفتار را به انگلیسی ترجمه کند. حالت خودکار وقتی زبان گفتار در Live Captions نصب "
              "باشد و ترجمه از راه انگلیسی به Whisper نیاز نداشته باشد از Live Captions استفاده می‌کند، و اگر "
              "Live Captions کار نکند خودش به Whisper برمی‌گردد."},
    "lv_engine_auto": {"en": "Automatic", "fa": "خودکار"},
    "lv_engine_lc": {"en": "Windows Live Captions", "fa": "Live Captions ویندوز"},
    "lv_engine_whisper": {"en": "Whisper (offline model)", "fa": "Whisper (مدل آفلاین)"},
    "lv_engine_now": {"en": "Running now: {engine}.", "fa": "در حال اجرا: {engine}."},
    "lv_lc_ready": {"en": "Windows Live Captions can be used (languages: {langs}).",
                    "fa": "Live Captions ویندوز قابل استفاده است (زبان‌ها: {langs})."},
    "lv_lc_old_windows": {"en": "Windows Live Captions needs Windows 11 22H2 or later.",
                          "fa": "Live Captions ویندوز به ویندوز ۱۱ نسخه‌ی 22H2 یا جدیدتر نیاز دارد."},
    "lv_lc_no_exe": {"en": "Windows Live Captions is not installed on this PC.",
                     "fa": "Live Captions ویندوز روی این رایانه نصب نیست."},
    "lv_lc_no_uia": {"en": "Windows Live Captions cannot be read here (UI Automation is missing).",
                     "fa": "Live Captions ویندوز اینجا خوانده نمی‌شود (UI Automation در دسترس نیست)."},
    "lv_lc_no_language": {
        "en": "Windows Live Captions has no language yet: open it once (Win+Ctrl+L) and download a language.",
        "fa": "Live Captions ویندوز هنوز زبانی ندارد: یک بار بازش کنید (Win+Ctrl+L) و یک زبان دانلود کنید."},
    "lv_lc_failed": {"en": "Live Captions stopped working, Whisper is used for now: {err}",
                     "fa": "Live Captions کار نکرد و فعلاً Whisper استفاده می‌شود: {err}"},
    "lv_vad_kind": {"en": "Speech detection", "fa": "تشخیص گفتار در صدا"},
    "lv_vad_energy": {"en": "Loudness", "fa": "بلندی صدا"},
    "lv_vad_kind_hint": {
        "en": "Silero tells speech from music and noise and cuts at natural pauses; Loudness only compares the "
              "level with the background.",
        "fa": "Silero گفتار را از موسیقی و نویز جدا می‌کند و در مکث‌های طبیعی می‌بُرد؛ «بلندی صدا» فقط سطح صدا را "
              "با پس‌زمینه مقایسه می‌کند."},
    "lv_vad_no_silero": {"en": "Silero needs onnxruntime (part of Install Live mode); Loudness is used until then.",
                         "fa": "Silero به onnxruntime نیاز دارد (با نصب زیرنویس زنده می‌آید)؛ تا آن موقع «بلندی صدا» "
                               "استفاده می‌شود."},
    "lv_interim": {"en": "Caption long speech before the pause", "fa": "زیرنویس گفتار طولانی پیش از مکث"},
    "lv_interim_hint": {
        "en": "While someone keeps talking, every finished sentence is shown and translated at once (Whisper).",
        "fa": "وقتی کسی بی‌وقفه حرف می‌زند، هر جمله‌ی تمام‌شده همان لحظه نشان داده و ترجمه می‌شود (Whisper)."},
    "lv_interim_preview": {"en": "Show the unfinished sentence as a dim line",
                           "fa": "جمله‌ی ناتمام با یک خط کم‌رنگ نشان داده شود"},
    "lv_interim_preview_hint": {"en": "In the style of the template's small line; hidden with \"translation only\".",
                                "fa": "با سبک خط کوچک قالب؛ در حالت «فقط ترجمه» نشان داده نمی‌شود."},
    # Translation: the Gemini key pool (dualyrics/keypool.py)
    "kp_title": {"en": "Keys now", "fa": "وضعیت کلیدها"},
    "kp_hint": {
        "en": "Requests take turns over the keys that are on, so no key is used up first. Google counts the free "
              "quota per Cloud project: keys only add quota when they come from different projects or accounts. "
              "Daily quotas reset at {at} (midnight US Pacific).",
        "fa": "درخواست‌ها به نوبت بین کلیدهای روشن پخش می‌شوند تا هیچ کلیدی زودتر تمام نشود. گوگل سهمیه‌ی رایگان را "
              "برای هر پروژه‌ی Cloud حساب می‌کند: کلیدها فقط وقتی سهمیه اضافه می‌کنند که از پروژه‌ها یا حساب‌های "
              "جدا باشند. سهمیه‌ی روزانه ساعت {at} (نیمه‌شب به وقت اقیانوس آرام آمریکا) نو می‌شود."},
    "kp_ok": {"en": "Ready", "fa": "آماده"},
    "kp_ok_some": {"en": "Ready ({n} model(s) resting)", "fa": "آماده ({n} مدل در حال استراحت)"},
    "kp_limited": {"en": "Rate limited, back at {at}", "fa": "محدودیت سرعت؛ از {at} دوباره"},
    "kp_exhausted": {"en": "Daily quota used up, back at {at}", "fa": "سهمیه‌ی امروز تمام شد؛ از {at} دوباره"},
    "kp_cooling": {"en": "Paused after an error, back at {at}", "fa": "بعد از یک خطا متوقف شد؛ از {at} دوباره"},
    "kp_invalid": {"en": "Rejected by Google (check the key, then Test)",
                   "fa": "گوگل این کلید را نپذیرفت (کلید را بررسی و بعد آزمایش کنید)"},
    "kp_off": {"en": "Switched off", "fa": "خاموش"},
    "kp_usage": {"en": "Today: {requests} requests, {tokens} tokens", "fa": "امروز: {requests} درخواست، {tokens} توکن"},
    "kp_shared": {
        "en": "Keys {keys} seem to be in one Google project (they hit the limit together): they share one quota. "
              "Make keys in different projects or accounts to get more.",
        "fa": "کلیدهای {keys} ظاهراً در یک پروژه‌ی گوگل هستند (با هم به سقف می‌رسند) و یک سهمیه دارند. برای سهمیه‌ی "
              "بیشتر کلید را در پروژه‌ها یا حساب‌های جدا بسازید."},
    "us_keys_resting": {"en": "Every Gemini key is resting until {at}; the next translator is used meanwhile.",
                        "fa": "همه‌ی کلیدهای Gemini تا {at} استراحت می‌کنند؛ تا آن موقع مترجم بعدی استفاده می‌شود."},
    "us_fallback_offline": {"en": "Online translators are not available; translating offline for now.",
                            "fa": "مترجم‌های آنلاین در دسترس نیستند؛ فعلاً آفلاین ترجمه می‌شود."},
    # Translation: the offline translator (dualyrics/offline.py)
    "st_offline_used": {"en": "Online translators unavailable — translated offline.",
                        "fa": "مترجم‌های آنلاین در دسترس نبودند — آفلاین ترجمه شد."},
    "of_group": {"en": "Offline translator", "fa": "مترجم آفلاین"},
    "of_hint": {
        "en": "The last resort when Gemini and the free translator both fail (no internet, every limit reached): "
              "translation models that run on this PC. Lower quality, but subtitles never stop.",
        "fa": "آخرین راه وقتی Gemini و مترجم رایگان هر دو کار نکنند (اینترنت نیست یا همه به سقف رسیده‌اند): مدل‌های "
              "ترجمه که روی همین رایانه اجرا می‌شوند. کیفیت کمتر، ولی زیرنویس هیچ‌وقت قطع نمی‌شود."},
    "of_enabled": {"en": "Use the offline translator as the last resort", "fa": "مترجم آفلاین به‌عنوان آخرین راه استفاده شود"},
    "of_enabled_hint": {"en": "Only with a language package below.", "fa": "فقط وقتی یک بسته‌ی زبان در پایین نصب باشد."},
    "of_deps": {"en": "Translation engine", "fa": "موتور ترجمه"},
    "of_deps_hint": {"en": "Needs {pkgs} (installed once, about 40 MB).",
                     "fa": "به {pkgs} نیاز دارد (یک بار نصب می‌شود، حدود ۴۰ مگابایت)."},
    "of_installed": {"en": "Installed languages", "fa": "زبان‌های نصب‌شده"},
    "of_none": {"en": "None yet. Download the pair you need below (for example English → your language).",
                "fa": "هنوز هیچ. جفت زبان لازم را از پایین دانلود کنید (مثلاً انگلیسی ← زبان شما)."},
    "of_license": {"en": "Licence: {lic}", "fa": "مجوز: {lic}"},
    "of_license_unknown": {"en": "not stated (see the package README)", "fa": "ذکر نشده (README بسته را ببینید)"},
    "of_license_after": {"en": "The licence of the model is shown once it is downloaded (from its metadata).",
                         "fa": "مجوز مدل بعد از دانلود (از اطلاعات خود بسته) نشان داده می‌شود."},
    "of_unsupported": {"en": "(this package format is not supported)", "fa": "(قالب این بسته پشتیبانی نمی‌شود)"},
    "of_remove": {"en": "Remove", "fa": "حذف"},
    "of_get": {"en": "Get a language", "fa": "دریافت زبان"},
    "of_load": {"en": "Show available languages", "fa": "نمایش زبان‌های موجود"},
    "of_loading": {"en": "Loading the list…", "fa": "در حال دریافت فهرست…"},
    "of_index_fail": {"en": "The list could not be loaded: {err}", "fa": "فهرست دریافت نشد: {err}"},
    "of_none_left": {"en": "Every package for this language is installed.", "fa": "همه‌ی بسته‌های این زبان نصب شده‌اند."},
    "of_download": {"en": "Download", "fa": "دانلود"},
    "of_progress": {"en": "{done} MB downloaded…", "fa": "{done} مگابایت دانلود شد…"},
    "of_installed_ok": {"en": "Language installed (licence: {lic}).", "fa": "زبان نصب شد (مجوز: {lic})."},
    "of_credit": {"en": "Language packages of Argos Translate (MIT); each model keeps its own licence.",
                  "fa": "بسته‌های زبان Argos Translate (مجوز MIT)؛ هر مدل مجوز خودش را دارد."},
    # Live mode: what is recorded (live/capture.py)
    "lv_capture": {"en": "Listen to", "fa": "گوش دادن به"},
    "lv_cap_all_except_dualyrics": {"en": "All apps except Dualyrics", "fa": "همه‌ی برنامه‌ها جز Dualyrics"},
    "lv_cap_app": {"en": "Only the app in use", "fa": "فقط برنامه‌ی در حال استفاده"},
    "lv_cap_system": {"en": "Everything you hear", "fa": "هر چه می‌شنوید"},
    "lv_capture_hint": {"en": "\"Only the app in use\" records the app of the active profile (a game, a player, the "
                                "browser) and nothing else, so music or calls in other apps are not captioned. "
                                "\"All apps except Dualyrics\" never records the dubbed voice. Both need Windows 10 "
                                "build 20348 or Windows 11; older Windows records everything. Applies to Whisper: "
                                "Windows Live Captions always listens to everything.",
                        "fa": "«فقط برنامه‌ی در حال استفاده» فقط صدای برنامه‌ی پروفایل فعال (بازی، پخش‌کننده، "
                              "مرورگر) را ضبط می‌کند تا موسیقی یا تماس برنامه‌های دیگر زیرنویس نشود. «همه‌ی "
                              "برنامه‌ها جز Dualyrics» صدای دوبله را هرگز ضبط نمی‌کند. هر دو به ویندوز ۱۰ نسخه‌ی "
                              "20348 یا ویندوز ۱۱ نیاز دارند؛ ویندوز قدیمی‌تر همه‌چیز را ضبط می‌کند. این گزینه برای Whisper "
                              "است؛ Live Captions ویندوز همیشه همه‌چیز را می‌شنود."},
    "lv_cap_now_app": {"en": "Now recording: {what}", "fa": "در حال ضبط: {what}"},
    "lv_cap_fallback": {"en": "Per-app capture is not available ({err}); everything you hear is recorded.",
                        "fa": "ضبط جداگانه‌ی برنامه در دسترس نیست ({err})؛ هر چه می‌شنوید ضبط می‌شود."},
    # Dubbing: voice gender per target language (dub/voices.py)
    "dp_gender": {"en": "Voice for {lang}", "fa": "صدای {lang}"},
    "dp_gender_female": {"en": "Female", "fa": "زن"},
    "dp_gender_male": {"en": "Male", "fa": "مرد"},
    "dp_gender_hint": {"en": "Used while Voice is Automatic; remembered for each target language.",
                       "fa": "وقتی «صدا» خودکار است به کار می‌رود؛ برای هر زبان مقصد جدا ذخیره می‌شود."},
}
