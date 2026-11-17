# Promotion kit for Dualyrics 2.1.0

Ready-to-post drafts. The owner posts them from his own accounts; nothing here is posted automatically. Replace `<release link>` with `https://github.com/morpheusadam/Dualyrics/releases/latest` and attach `docs/hero.gif` (or a screen recording) wherever images are allowed.

## Plan

Everything points back to one owned place, the GitHub repository: the release page for downloads, the star button for the Linux vote (review on 2026-12-17), and issues for feedback. Rented channels (Reddit, Hacker News, Product Hunt, Dev.to, Telegram) bring people in; borrowed channels (awesome lists, YouTubers, newsletters) keep bringing them after the launch week.

| When | Where | Goal |
|---|---|---|
| Day 0 | GitHub release v2.1.0, social preview, topics, pinned "Linux vote" issue | The page every post links to is ready |
| Day 1 (Tue or Wed, 15:00 UTC) | Show HN | Developers, MCP and Whisper crowd |
| Day 2 | r/software | General Windows users |
| Day 3 | r/languagelearning | Learners who use songs and videos |
| Day 4 | r/pcgaming | Players of untranslated games |
| Day 5 | r/spotify (only if the rules allow it that week; otherwise skip or use the weekly thread) | Spotify listeners |
| Day 5 | Persian Telegram channels and Virgool | Persian-speaking users, the first target language |
| Week 2 | Dev.to article | Long-tail search, developers |
| Week 2 (Tue to Thu, 00:01 PT) | Product Hunt | Early adopters, a launch badge for the README |
| Weeks 2 to 4 | awesome-list pull requests, two or three YouTubers who cover free Windows tools or language learning | Lasting links and new viewers |
| 2026-12-17 | Pinned issue update with the star count and the Linux decision | A second launch moment |

Rules for every channel: read the community rules first, post as the developer ("I made this"), answer every comment for the first few hours, never ask for upvotes, and never post the same text to several subreddits on the same day. Reddit and HN readers are allergic to marketing language, so the drafts below stay plain.

Before posting, check that the release has the Setup, exe, zip and `SHA256SUMS.txt`, and that the README GIF loads.

---

## Show HN

**Title** (80 characters max):

```
Show HN: Dualyrics, translated subtitles for anything you hear on Windows
```

**Text:**

```
I made Dualyrics because I wanted to understand the songs, YouTube videos and games I play in languages I don't speak, without copying lines into a translator.

It is a small always-on-top subtitle bar for Windows 10/11. For the app in front it tries three sources in order:

1. Synced lyrics from LRCLIB, read from Windows media controls (Spotify, YouTube Music, any player that reports there). In a browser it only accepts an exact title/artist/length match, so a random video never gets the lyrics of a song with a similar name.
2. Subtitles the app already shows: YouTube captions through UI Automation in Chrome/Edge, otherwise Windows' built-in offline OCR on a subtitle area you draw once (games, film players). It only reads when the area changes and ignores text that never changes, like HUD labels.
3. The audio: WASAPI loopback, cut at pauses, offline faster-whisper on GPU or CPU.

Speech or text in any language goes through English (Whisper's translate task), then into one of 46 target languages, with Gemini (your own free key) or the free Google Translate endpoint. Each caption is translated together with the previous few lines so names stay consistent.

Other parts: a renderer with per-pixel alpha so shadows and glow look right over games, a Liquid Glass look that uses the Windows acrylic blur behind the box, optional dubbing (off by default) with ducking of other apps, a Gemini usage meter against the free-tier limits, and an MCP server (`Dualyrics.exe --mcp`) so Claude or another assistant can change templates, profiles and timing. The MCP protocol is implemented in about 300 lines instead of pulling in the SDK.

Python, CustomTkinter, Pillow, PyWinRT. MIT licensed, no account, no telemetry.

https://github.com/morpheusadam/Dualyrics

Windows only for now. A Linux port depends on interest: I'll decide on 2026-12-17 based on stars. Feedback on the source-priority logic (docs/ALGORITHM.md) is very welcome.
```

---

## Reddit: r/software

Check the subreddit's self-promotion rules on the day. Flair: "Self-Promotion" or "Release" if available.

**Title:**

```
I made a free Windows app that puts translated subtitles under songs, YouTube videos and games (Dualyrics, open source)
```

**Body:**

```
Dualyrics is a small subtitle bar that floats over any app and shows a translation under whatever you're listening to.

- Songs (Spotify, YouTube Music, Spotube, anything in the Windows media flyout): synced lyrics line by line, translation under each line.
- YouTube and films: it reads the captions already on screen. No captions? It listens to the sound and makes them with offline Whisper.
- Games: draw a box around the game's subtitles once and it reads them with Windows' offline OCR.

Any language in, English plus your language out. 46 target languages. Translation is free Google Translate by default, or Gemini with your own free key (setup steps are in the README).

No account, no telemetry, MIT licensed. Windows 10/11, installer or portable exe.

Download and screenshots: https://github.com/morpheusadam/Dualyrics

I'm the developer, happy to answer questions or take bug reports.
```

---

## Reddit: r/languagelearning

Many language subreddits only allow tools in a weekly thread. Post there if so.

**Title:**

```
I built a free tool that shows a translation under song lyrics and video subtitles while you watch (Windows)
```

**Body:**

```
I learn a lot from songs and YouTube, and I kept pausing to look lines up. So I made Dualyrics, a free, open-source Windows app that floats a small subtitle bar over whatever you're playing:

- the original line (lyrics in sync with the music, or the video's captions),
- your language under it,
- optionally the English in a small third line, when the original is in another language (e.g. Japanese → English → Spanish).

It works with Spotify, YouTube and YouTube Music in the browser, film players, and even games. You can turn on "translation only" or hide the next line, and copy the whole song with its translation for later study.

No account and no ads. Translation uses free Google Translate, or Gemini if you add a free key, which handles slang and idioms better.

https://github.com/morpheusadam/Dualyrics

Would love to hear which languages you use it with, and what would make it better for studying.
```

---

## Reddit: r/pcgaming

**Title:**

```
Free overlay that translates game subtitles (or the voices, if there are no subtitles) in real time
```

**Body:**

```
I play a lot of games that aren't translated into my language, so I built Dualyrics, a free, open-source overlay for Windows.

How it works in games:
- If the game shows subtitles, you draw a rectangle around them once. It reads that area with Windows' built-in offline OCR whenever it changes and shows the translation in a small bar (it ignores HUD text that never changes).
- If the game has no subtitles, it listens to the game audio and transcribes it offline with Whisper (NVIDIA GPU or CPU), then translates.
- A Games profile makes the overlay click-through, so it never steals a click.

Notes: the game has to run in Borderless or Windowed mode. No game files are touched, it only looks at the screen and the sound like a capture app would. Audio recognition stays on your PC; only the recognised text goes to the translator.

46 target languages, any spoken language through English. MIT licensed, no telemetry.

https://github.com/morpheusadam/Dualyrics

If you try it with a specific game, I'd like to know how the OCR does with its font.
```

---

## Reddit: r/spotify

r/spotify often removes third-party tools outside its designated threads. Read the rules and the pinned posts first; if tools are not allowed, skip this one.

**Title:**

```
Made a free Windows app that shows Spotify lyrics with a translation under every line
```

**Body:**

```
Spotify's lyrics panel doesn't translate, so I made Dualyrics, a free, open-source desktop app. Play a song in the Spotify desktop app and a small bar on top of your screen shows the synced line, its translation under it, and the next line.

- 46 languages (Persian, Arabic, Turkish, Spanish, Japanese, Korean...).
- No Spotify login: it reads the song from Windows media controls and gets lyrics from LRCLIB.
- Fix lyrics that are early or late with Ctrl+Alt+Up/Down, remembered per song.
- Also works with YouTube Music in the browser.

Windows 10/11 only. https://github.com/morpheusadam/Dualyrics
```

---

## Product Hunt

**Name:** Dualyrics

**Tagline** (60 characters max):

```
Translated subtitles for anything you hear on Windows
```

**Description** (260 characters max):

```
Songs, YouTube, films and games: Dualyrics shows the line and your translation in a floating subtitle bar. Synced lyrics, on-screen subtitles via OCR, or offline Whisper captions. Any language through English into 46 others. Free, open source, no account.
```

**Topics:** Windows, Languages, Music, Open Source, Artificial Intelligence

**Gallery:** `docs/social-preview.png` first, then `docs/hero.gif`, `docs/screenshots/overlay-liquid-glass.png`, `overlay-any-language.png`, `settings-sources.png`, `settings-usage.png`, `settings-ai.png`.

**Maker's first comment:**

```
Hi Product Hunt! I'm the developer of Dualyrics.

I wanted to understand songs, videos and games in languages I don't speak without alt-tabbing to a translator. Dualyrics takes the lines from wherever they already exist: synced lyrics for music, the subtitles on screen for YouTube, films and games, and only when there are none, the audio itself through offline speech recognition. Everything goes through English into your language.

It's free and MIT licensed. Translation works without any key, and you can add a free Gemini key for better results. There's also optional dubbing and an MCP server if you like to set things up through Claude.

It's Windows-only today. If you'd like a Linux version, a star on GitHub counts as a vote: I'm reviewing them on 2026-12-17.

I'll be here all day to answer questions.
```

---

## Dev.to article

**Title:** `How I made a subtitle overlay that reads lyrics, the screen or the audio, whichever exists first`

**Tags:** `python`, `windows`, `opensource`, `ai`

```markdown
I wanted one thing: a translation under whatever I'm listening to on my PC. Songs, YouTube, a film, a game. The obvious approach is speech recognition on everything, but that is slow, heavy and wrong more often than it needs to be, because most of the time the words already exist somewhere.

So Dualyrics, the Windows app I built, asks three sources in order and shows the first one that has something.

## 1. Synced lyrics

Windows exposes the playing track through the Global System Media Transport Controls (the media flyout). Spotify, browsers and most players report there. Dualyrics reads title, artist, album and duration, asks LRCLIB for time-synced LRC lyrics, and keeps its own playback clock, because players report their position rarely.

Browsers were the hard part. A YouTube video called "Pasta" will happily match a song called "Pasta". In browsers Dualyrics only accepts a match when title, artist and duration agree within four seconds.

## 2. Subtitles already on screen

In Chrome and Edge, YouTube's caption text is in the accessibility tree, so UI Automation reads it exactly. Everywhere else it uses Windows.Media.Ocr, the offline OCR that ships with Windows, on a rectangle the user draws over the subtitle area once.

OCR at 30 fps would waste CPU, so it samples 1 to 6 times a second and only runs OCR when a bright-pixel mask of the area changes. Lines must stay stable across two reads before they show, and lines that never change (HUD labels, logos) are ignored. The overlay excludes itself from capture so OCR never reads its own subtitles.

## 3. The audio

Only when there are no lyrics and no subtitles does it capture the speakers (WASAPI loopback), cut at pauses with a small energy-based voice detector, and run faster-whisper on the GPU or CPU. A filter drops Whisper's favourite hallucinations ("Thank you for watching" over silence, music tags, repetition loops).

## Any language, through English

Whisper can translate speech to English directly (`task=translate`). Dualyrics uses that as a pivot: Japanese audio becomes English offline, then English is translated into the user's language with Gemini or the free Google Translate endpoint. Each line goes out with the previous few lines and the app name, so names stay consistent across a film.

## Bits I enjoyed building

- A renderer with per-pixel alpha (`UpdateLayeredWindow`), so soft shadows look right over a game, and a second window with the Windows acrylic accent behind the box for a real blur.
- A tiny MCP server (stdio JSON-RPC, about 300 lines) so Claude can change templates or timing by asking. No SDK, which would have pulled pydantic, starlette and uvicorn into a 25 MB exe.
- A usage meter in SQLite that tracks Gemini requests against the free-tier daily limits, with the quota day in Pacific time.

The code is MIT licensed: https://github.com/morpheusadam/Dualyrics. The decision rules are written down in `docs/ALGORITHM.md` if you want the details. It's Windows-only for now; stars decide whether a Linux port comes next.
```

---

## Persian: Telegram post

```
Dualyrics 2.1 منتشر شد

برنامه‌ی رایگان و متن‌باز برای ویندوز که زیر هر چیزی که گوش می‌دهید ترجمه می‌گذارد:

• آهنگ (اسپاتیفای، یوتیوب موزیک): متن هم‌زمان با موسیقی و ترجمه‌ی فارسی زیر هر خط
• یوتیوب و فیلم: زیرنویس روی صفحه را می‌خواند؛ اگر زیرنویس نبود، از روی صدا زیرنویس می‌سازد
• بازی: دور زیرنویس بازی یک مستطیل بکشید تا با OCR آفلاین ویندوز خوانده و ترجمه شود

هر زبانی (ژاپنی، کره‌ای، آلمانی...) اول به انگلیسی و بعد به فارسی ترجمه می‌شود. بدون حساب کاربری و بدون کلید هم کار می‌کند؛ با کلید رایگان Gemini ترجمه بهتر می‌شود. رابط برنامه و نصب‌کننده فارسی است.

دانلود: https://github.com/morpheusadam/Dualyrics/releases/latest

اگر دوستش داشتید به مخزن ستاره بدهید؛ ستاره‌ها رأی نسخه‌ی لینوکس هستند (بررسی: ۱۷ دسامبر).
```

## Persian: Virgool article

**Title:** `Dualyrics: ترجمه‌ی زنده‌ی آهنگ، یوتیوب و بازی روی ویندوز، رایگان و متن‌باز`

```markdown
وقتی آهنگی به زبانی گوش می‌دهم که بلد نیستم، یا بازی‌ای بازی می‌کنم که فارسی ندارد، مدام باید متن را کپی کنم و در مترجم بچسبانم. Dualyrics را ساختم که این کار را خودش انجام دهد: یک نوار زیرنویس کوچک که روی همه‌ی پنجره‌ها می‌ماند و زیر هر خط، ترجمه‌اش را نشان می‌دهد.

## از کجا متن را پیدا می‌کند؟

برای برنامه‌ای که جلوی شماست، سه منبع را به ترتیب امتحان می‌کند:

1. **متن هم‌زمان آهنگ** از LRCLIB، برای اسپاتیفای، یوتیوب موزیک و هر پخش‌کننده‌ای که در کنترل‌های رسانه‌ی ویندوز دیده می‌شود.
2. **زیرنویس روی صفحه**: زیرنویس یوتیوب در Chrome و Edge، و برای فیلم و بازی، OCR آفلاینِ خود ویندوز روی ناحیه‌ای که یک بار مشخص می‌کنید.
3. **صدا**: اگر هیچ‌کدام نبود، صدای بلندگو با Whisper روی کامپیوتر خودتان به متن تبدیل می‌شود.

## هر زبانی به فارسی

گفتار یا متن به هر زبانی، اول به انگلیسی و بعد به فارسی (یا هر کدام از ۴۶ زبان) ترجمه می‌شود. می‌توانید خط اصلی، ترجمه‌ی فارسی و انگلیسی را با هم ببینید که برای یادگیری زبان هم به کار می‌آید.

## چیزهای دیگر

- نُه قالب ظاهری، از جمله «شیشه‌ی مایع» که پشت زیرنویس را واقعاً تار می‌کند.
- پروفایل جدا برای هر برنامه و بازی.
- دوبله (پیش‌فرض خاموش): ترجمه با صدا خوانده می‌شود و صدای برنامه‌های دیگر کم می‌شود.
- شمارنده‌ی مصرف Gemini تا از سقف رایگان جلوتر نروید.
- رابط برنامه و نصب‌کننده‌ی فارسی، با فونت وزیرمتن.

## شروع

نصب‌کننده را از [صفحه‌ی دانلود](https://github.com/morpheusadam/Dualyrics/releases/latest) بگیرید و در تنظیمات، زبان «فارسی» را برای ترجمه انتخاب کنید. بدون کلید هم کار می‌کند؛ برای ترجمه‌ی بهتر، راهنمای گرفتن کلید رایگان Gemini در [README فارسی](https://github.com/morpheusadam/Dualyrics/blob/main/README.fa.md) هست.

برنامه رایگان و با مجوز MIT است و هیچ داده‌ای جمع نمی‌کند. اگر به کارتان آمد، در گیت‌هاب ستاره بدهید؛ برای نسخه‌ی لینوکس هم رأی حساب می‌شود.
```

---

## Awesome-list pull requests

Open one pull request per list, follow each list's CONTRIBUTING file (alphabetical order, line format, sometimes a minimum star count), and do not open them all on the same day.

### Awesome-Windows (`Awesome-Windows/Awesome`)

Section: **Audio** or **Utilities** (whichever fits the current layout).

```
* [Dualyrics](https://github.com/morpheusadam/Dualyrics) - Translated subtitles over any app: synced lyrics, on-screen subtitles (OCR) and offline Whisper captions. ![Open-Source Software][oss]
```

PR title: `Add Dualyrics`

PR body:

```
Dualyrics is a free, MIT-licensed Windows 10/11 app that shows a floating subtitle bar with a translation of whatever you are listening to: synced lyrics for Spotify and other players, subtitles read from the screen (UI Automation or Windows OCR) and offline Whisper captions from the audio, in 46 languages. Installer and portable exe on the releases page.
```

### awesome-mcp-servers (`punkpeye/awesome-mcp-servers`)

Section: **Multimedia Process** (or the closest current section). Legend: 🐍 Python, 🏠 local, 🪟 Windows.

```
- [morpheusadam/Dualyrics](https://github.com/morpheusadam/Dualyrics) 🐍 🏠 🪟 - Control a live-translation subtitle overlay: status, settings, templates, per-app profiles, target language, sync offset, Gemini usage and dubbing.
```

PR body:

```
Dualyrics ships a stdio MCP server (`Dualyrics.exe --mcp`) with 20 tools to read and configure the app. It talks to the running app through a token-protected API on 127.0.0.1 and never exposes the user's API keys. Docs: https://github.com/morpheusadam/Dualyrics/blob/main/docs/AI.md
```

### awesome-whisper (`sindresorhus/awesome-whisper`)

Section: **Apps**.

```
- [Dualyrics](https://github.com/morpheusadam/Dualyrics) - Windows overlay that captions and translates whatever your speakers play, using faster-whisper offline.
```

### awesome-python-applications (`mahmoud/awesome-python-applications`)

Section: **Audio** or **Desktop**. This list asks for applications (not libraries) with real users; open it once the repository has some.

```
* [Dualyrics](https://github.com/morpheusadam/Dualyrics) - Live translated subtitles for songs, videos and games on Windows. `(customtkinter, pillow, winrt)`
```

### awesome-persian (`fffaraz/awesome-persian`)

Section: **Applications** or **Tools**.

```
- [Dualyrics](https://github.com/morpheusadam/Dualyrics) - ترجمه‌ی زنده‌ی متن آهنگ، زیرنویس یوتیوب و بازی به فارسی روی ویندوز، با رابط و نصب‌کننده‌ی فارسی.
```

---

## People and places to contact (borrowed channels)

- YouTubers who review free Windows apps or language-learning tools: send a short, personal message with the GIF and the release link, no request to cover it.
- Newsletters: "Console" (open-source tools), "Python Weekly" and "PyCoder's Weekly" (submit the Dev.to article).
- AlternativeTo: add Dualyrics as an alternative to "Spotify lyrics", "Musixmatch" and "Live Captions" entries.
- Persian tech channels and podcasts that cover open-source projects.
