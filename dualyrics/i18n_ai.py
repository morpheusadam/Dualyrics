"""v2.1 interface strings of Settings > AI & API (the control API and the MCP server), English and Persian.

Merged into :data:`dualyrics.i18n.STRINGS` like the v2 table.
"""

from __future__ import annotations

S: dict[str, dict[str, str]] = {
    "sec_ai": {"en": "AI & API", "fa": "هوش مصنوعی و API"},
    "sec_ai_caption": {
        "en": "Let an AI assistant such as Claude read and set up Dualyrics: what is playing, settings, templates, "
              "app profiles, the target language, timing and usage.",
        "fa": "به دستیاری مثل Claude اجازه دهید Dualyrics را بخواند و تنظیم کند: چیزی که پخش می‌شود، تنظیمات، "
              "قالب‌ها، پروفایل برنامه‌ها، زبان ترجمه، زمان‌بندی و مصرف."},
    "ai_api": {"en": "Control API", "fa": "API کنترل"},
    "ai_api_hint": {
        "en": "Reachable only from this PC (127.0.0.1) and only with the token in api.json, in your Dualyrics "
              "folder. Assistants never see your Gemini keys.",
        "fa": "فقط از همین کامپیوتر (127.0.0.1) و فقط با توکنِ داخل api.json در پوشه‌ی Dualyrics در دسترس است. "
              "دستیارها هرگز کلیدهای Gemini شما را نمی‌بینند."},
    "ai_enable": {"en": "Let AI assistants control Dualyrics", "fa": "اجازه به دستیارهای هوش مصنوعی برای کنترل"},
    "ai_enable_hint": {
        "en": "Off: assistants can only change the settings while Dualyrics is closed.",
        "fa": "خاموش: دستیارها فقط وقتی Dualyrics بسته است می‌توانند تنظیمات را تغییر دهند."},
    "ai_status_on": {"en": "Running on 127.0.0.1:{port}", "fa": "فعال روی 127.0.0.1:{port}"},
    "ai_status_off": {"en": "Off", "fa": "خاموش"},
    "ai_status_error": {"en": "Could not start: {err}", "fa": "اجرا نشد: {err}"},
    "ai_regen": {"en": "Regenerate token", "fa": "ساخت توکن تازه"},
    "ai_regen_done": {
        "en": "New token saved. Connected assistants pick it up by themselves; the old one no longer works.",
        "fa": "توکن تازه ذخیره شد. دستیارهای وصل خودشان آن را برمی‌دارند؛ توکن قبلی دیگر کار نمی‌کند."},
    "ai_copy_token": {"en": "Copy token", "fa": "کپی توکن"},
    "ai_copy": {"en": "Copy", "fa": "کپی"},
    "ai_copied": {"en": "Copied to the clipboard.", "fa": "در کلیپ‌بورد کپی شد."},
    "ai_desktop": {"en": "Claude Desktop", "fa": "Claude Desktop"},
    "ai_desktop_hint": {
        "en": "In Claude Desktop: Settings > Developer > Edit Config, add this to claude_desktop_config.json "
              "(merge it with servers you already have), save and restart Claude Desktop.",
        "fa": "در Claude Desktop: Settings > Developer > Edit Config، این را به claude_desktop_config.json اضافه "
              "کنید (کنار سرورهایی که از قبل دارید)، ذخیره کنید و Claude Desktop را دوباره باز کنید."},
    "ai_open_config": {"en": "Open the config file", "fa": "باز کردن فایل تنظیمات"},
    "ai_code": {"en": "Claude Code", "fa": "Claude Code"},
    "ai_code_hint": {"en": "Run this once in a terminal; Dualyrics is then available in every project.",
                     "fa": "یک بار در ترمینال اجرا کنید؛ از آن به بعد Dualyrics در همه‌ی پروژه‌ها در دسترس است."},
    "ai_other": {"en": "Other assistants", "fa": "دستیارهای دیگر"},
    "ai_other_hint": {
        "en": "Any MCP client: a stdio server with this command. Scripts can also call the HTTP API with the "
              "token in api.json (see the AI guide).",
        "fa": "هر کلاینت MCP: یک سرور stdio با این فرمان. اسکریپت‌ها هم می‌توانند با توکنِ داخل api.json مستقیم "
              "API را صدا بزنند (راهنمای هوش مصنوعی را ببینید)."},
    "ai_examples": {"en": "Try asking", "fa": "امتحان کنید"},
    "ai_examples_text": {
        "en": "“Set the Game HUD template for God of War.”\n“Translate to Turkish.”\n“Show my Gemini usage.”\n"
              "“The lyrics are half a second early.”\n“Make the translation bigger and yellow.”",
        "fa": "«برای God of War قالب Game HUD را بگذار.»\n«به ترکی ترجمه کن.»\n«مصرف Gemini من را نشان بده.»\n"
              "«متن آهنگ نیم ثانیه زودتر می‌آید.»\n«ترجمه را بزرگ‌تر و زرد کن.»"},
    "ai_docs": {"en": "AI guide", "fa": "راهنمای هوش مصنوعی"},
}
