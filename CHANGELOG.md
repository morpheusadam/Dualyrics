# Changelog

All notable changes to Dualyrics are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.1.0] - 2026-11-18

Smart source priority (the lyrics or subtitles the app already shows first, the audio only when there are none), any language through English, dubbing, a Gemini usage meter, the Liquid Glass look and an MCP server for AI assistants.

### Added

- Sources (`docs/ALGORITHM.md` section 2): synced lyrics, on-screen subtitles and audio (Live), tried in the user's order. New **Settings › Sources** page: order with up/down buttons, each source on or off, globally or per app profile ("This app has its own order").
- On-screen subtitles (`dualyrics/screen/`): caption elements of YouTube and other web players in Chrome and Edge read through Windows UI Automation; otherwise offline OCR (Windows.Media.Ocr) of a subtitle area of the window in front, sampled 1-6 times a second (3.5 by default) and only when the area changes (bright-pixel mask diff), with stable-text debounce, dedupe, a filter for interface text and OCR noise, and lines that never change (HUD, logos) ignored. OCR language automatic or fixed. Only the app in front is read, and only video players, browsers, games, fullscreen apps or apps with their own subtitle area. The overlay is excluded from screen capture while the screen is read, so OCR never reads Dualyrics' own lines.
- Subtitle-area picker: click the window, drag a rectangle on a frozen picture of it (saved as fractions of the window, globally or per profile), Reset, and **Test on a window…** showing what UI Automation and OCR read there.
- Any language: **Pivot through English** (on by default): multilingual Whisper with `task=translate` writes English for speech in any language, then English is translated into the target language; on-screen text in another language goes the same way through the translator. **Also show the original-language line** (off by default) adds the line in its own language, with the English as a small third line.
- Strict lyrics match for browsers (on by default, tolerance 4 s): title, artist and duration must agree, so an ordinary video never gets the lyrics of a song with a similar name. Strict lookups are cached separately.
- A browser video without matching lyrics goes to on-screen subtitles, then to Live captions, without waiting for the lookup and whatever "Caption songs that have no lyrics" says.
- AI assistants (`docs/AI.md`): an MCP server (Model Context Protocol, stdio), `Dualyrics.exe --mcp` or `python -m dualyrics.mcp`, so Claude Desktop, Claude Code and other MCP clients can read and configure Dualyrics. Tools: `get_status`, `get_settings`, `update_settings` (validated patch, all or nothing), `list_templates`, `get_template`, `apply_template`, `create_or_update_template`, `list_profiles`, `upsert_profile`, `delete_profile`, `list_running_apps`, `set_mode`, `set_target_language`, `sync_offset`, `retranslate`, `edit_line_translation`, `get_usage`, `toggle_dubbing`, `show`, `hide`. A small built-in implementation of the protocol (initialize, ping, tools/list, tools/call; versions 2024-11-05 to 2025-11-25) instead of the SDK, which would bundle pydantic, starlette, uvicorn and httpx.
- Local control API inside the running app (`dualyrics/api.py`): HTTP on 127.0.0.1 only, random port with exclusive use, bearer token; port and token in `%APPDATA%\Dualyrics\api.json` (the token survives restarts). Requests from web pages (an `Origin` header) or with another `Host` (DNS rebinding) are refused. On by default. The MCP server sends tool calls to it, so changes apply at once; when the app is closed it edits the settings files directly, and when the app runs with the API off it refuses. Assistants never see or change Gemini keys and cannot switch the API off.
- New **Settings › AI & API** page: the API switch and status, Regenerate token, Copy token, and copy-ready configuration for Claude Desktop (`claude_desktop_config.json`), Claude Code (`claude mcp add ...`) and other MCP clients, with example requests.
- Dubbing (`dualyrics/dub/`, `docs/ALGORITHM.md` section 9), off by default: the translation is spoken aloud. Lyrics lines are made ahead and start with their line (spoken up to 35 % faster to fit, skipped when late); captions are spoken once they stop changing. Engines: Gemini text-to-speech and Gemini Live (speech to speech from the captured audio) with your keys, Microsoft Edge voices (free, online, optional `edge-tts` package) and Piper (offline, optional download), with automatic fallback from one to the next. Other apps are turned down while the voice speaks (per-app volumes of every output device, restored afterwards and on exit). New **Settings › Dubbing** page with engine, voice, speed, volume, ducking and one-click voice installs; tray and overlay menu items, `Ctrl+Alt+D`, and dubbing settings per app profile.
- Gemini usage meter (`dualyrics/usage.py`): every Gemini request for translation, dubbing and Gemini Live, and every cache hit, recorded in `%APPDATA%\Dualyrics\usage.sqlite` with model, key position (never the key), tokens, seconds of audio, status and the song or video. New **Settings › Usage** page: totals for today, 7 days and this month, a 14-day chart (requests, tokens or audio), today's share of each free-tier daily limit with the reset time (midnight Pacific), breakdowns per model, per key and per song or video, editable limits, CSV export and reset. A notice at 80 % of a daily limit; when a model reaches its limit, the next model or key is used, then the free translator.
- Liquid Glass template, the new default look: a translucent box with real blur of what is behind it (a click-through window with the Windows acrylic accent under the box, `dualyrics/backdrop.py`), a bright rim, sheen and soft depth; it falls back to a simulated frost where Windows cannot blur. Users who never chose a look move from Cinema to Liquid Glass; a chosen look is kept.
- About page: links to the repository, issues and releases, and a "Star on GitHub" button.
- Repository: issue forms (bug, feature, translation request), a pull request template, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `docs/GOOD_FIRST_ISSUES.md`, `docs/PROMO.md`, `docs/social-preview.png` (`tools/social_preview.py`), and a rewritten README in English and Persian with a hero GIF and new screenshots.
- `tools/screenshots.py` makes `docs/hero.gif` from renderer frames, adds the Sources, Dubbing, Usage and AI & API pages (with sample usage data in its temporary folder), captures windows with `PrintWindow` so notifications or other windows never appear in the pictures, and takes `--overlays` and `--only=<sections>`.

### Changed

- The Smart Auto decision walks the enabled sources in order (new mode "screen"; 0.4 s hysteresis). With the default order every v2.0 decision stays the same; mode Live now means "captions": the app's own subtitles first, then the audio. Putting audio before lyrics captions songs even when they have lyrics.
- Settings and profiles have new keys (`source_order`, `sources_off`, `lyrics_strict_browsers`, `lyrics_strict_tolerance`, `screen_uia`, `screen_ocr`, `screen_uia_classes`, `ocr_region`, `ocr_language`, `ocr_fps`, `pivot_english`, `pivot_show_original`; per profile `sources_custom`, `source_order`, `sources_off`, `ocr_region`; `api_enabled`). Existing settings keep working.
- More new keys: `dub_enabled`, `dub_engine`, `dub_voice`, `dub_speed`, `dub_volume`, `dub_duck`, `dub_duck_amount`, `dub_lyrics`, `dub_captions`, `dub_gemini_tts_model`, `dub_gemini_live_model`, `hotkey_dub`, `usage_limits`, `usage_warn`, `template_chosen`.
- New dependencies: `comtypes` and the WinRT projections `Windows.Media.Ocr`, `Windows.Graphics.Imaging`, `Windows.Globalization` (bundled in the exe).

### Fixed

- Settings › Sources no longer shows an empty 200 px gap under "Settings for" when "All apps" is selected.
- Settings › Usage: the value above the tallest bar of the chart is no longer cut off.

## [2.0.0] - 2026-11-17

Live mode, smart auto, per-app profiles, subtitle templates and a Windows installer.

### Added

- Windows installer `Dualyrics-Setup-<version>.exe` (Inno Setup, `installer\Dualyrics.iss`): per-user install without admin rights to `%LOCALAPPDATA%\Programs\Dualyrics`, Start menu shortcut, optional desktop shortcut and "Start with Windows" (the same Run entry as the in-app switch), English and Persian Setup, app icon, uninstall entry in Windows Settings. The uninstaller closes the app, removes its startup entry and asks whether to delete the settings in `%APPDATA%\Dualyrics` (kept by default; silent uninstall keeps them unless `/PURGE=1`). Upgrading over v1 keeps the settings, and a v1 startup entry is moved to the installed copy. `tools\build.ps1` and the release workflow build it next to the portable exe and zip.
- `--quit` command-line option: closes the running copy cleanly (settings saved); used by Setup before replacing files.
- Live mode (optional): captions for whatever the speakers play, for games, films, videos and songs without lyrics. WASAPI loopback capture, segmentation at natural pauses, offline Whisper (faster-whisper; NVIDIA GPU with float16 when the CUDA libraries are present, otherwise CPU int8), translation a moment later. Model choice (tiny, base, small, medium; English-only or multilingual), spoken language (auto or fixed), device (auto, CPU, GPU), voice sensitivity, longest piece, reading speed, shortest time on screen, context lines, optional transcripts, pause and resume (tray, `Ctrl+Alt+P`).
- Live translation with context: the last lines and their translations, the app or game name and the media title go with every new line, so names and terms stay consistent. Fragments that end mid-sentence are merged, repeats are dropped, Whisper hallucinations ("Thank you for watching", sound tags, low-confidence guesses, repetition loops) are filtered, and captions stay on screen for a reading-time based duration.
- Whisper models download on first use into `%APPDATA%\Dualyrics\models` with progress, through the app's proxy setting, and resume after a broken connection.
- "Install Live mode" helper in the new Live tab. The heavy packages stay optional: without them the app runs as before.
- Smart auto mode (documented in `docs/ALGORITHM.md`): synced lyrics when they exist, live captions for songs and videos without lyrics (optional) and for games and fullscreen apps, hidden when nothing plays. Global mode: Smart auto, Lyrics only, Live only.
- Per-app profiles in the new Apps tab: enabled, mode (Auto, Lyrics, Live, Off), template, target language, monitor, position, width, click-through, translation only, activation (foreground or running). Built-in profiles for Spotify, Spotube, music apps, web browsers, VLC, MPC-HC/BE and PotPlayer, a Games preset for fullscreen apps (Live, Game HUD, click-through), "Other media players" (keeps every v1 player working) and "Everything else" (off by default). Add apps from running processes (with icons), by browsing an .exe, or by clicking a window.
- Translation only (hide the original line), off by default; also per app.
- Look-ahead translation: while Gemini translates the whole song, the next lines are translated quickly with the free translator (when the free fallback is allowed), so the first lines are not left empty.
- Subtitle templates (`docs/DESIGN.md` part 2): one JSON document per look, validated like the settings (unknown keys dropped, values clamped, bad values replaced by the preset) and checked against the JSON Schema on import, with a readable list of what was fixed. Eight built-in presets with the exact values of DESIGN.md: Cinema, Netflix, Karaoke, Minimal, Neon, Game HUD, Glass and Persian Classic; all pass the contrast check of DESIGN.md 3.3 (a unit test). User templates in `%APPDATA%\Dualyrics\templates`; editing a preset makes a copy (`based_on`), with Reset to preset, Duplicate, Rename, Delete with Undo, Import and Export.
- A new overlay renderer: text shaped by Windows (GDI/Uniscribe, so Persian and Arabic letters join), effects with Pillow: solid or gradient fill, round outline, up to four soft shadows or glows, letter spacing, line height, max lines with an ellipsis, uppercase, a box with colour or gradient, rim, corner radius, drop shadow and a simulated frosted look, karaoke highlight (whole line or a sweep timed by the lyrics) and fade / slide motion with easing, respecting Windows "Animation effects". The window uses per-pixel alpha (`UpdateLayeredWindow`), so soft edges look right over any game or film. Templates can set a minimum time on screen for fast lines; lines are never skipped.
- The overlay is resizable: drag the left or right edge to reflow the text, the top or bottom edge or a corner to scale it, or use the mouse wheel. Position and size are remembered per monitor and per app profile.
- Multi-monitor: choose the monitors the overlay may use, follow the monitor of the app in use, move it to monitor N from the menu, and it comes back on screen when a monitor is unplugged. Virtual desktops: show on all desktops (it follows you, through the public `IVirtualDesktopManager`) or on the current desktop only.
- A rich right-click menu with icons and shortcuts: mode, which lines to show, template, text size, opacity, position and monitor, target language, sync offset for this song or all songs, copy the line, copy the whole song with its translation, edit this line's translation inline (kept in the translation cache), retranslate, lock, click-through, hide for this song or app, pause live captions, save the transcript, settings, quit.
- New hotkey `Ctrl+Alt+K`: click-through on or off (so a click-through overlay can always be reached).
- Redesigned settings window following `docs/DESIGN.md` part 1: design tokens from the app icon (cyan and coral on deep navy) as a customtkinter theme, a sidebar with line icons (General, Profiles, Templates, Lyrics, Live mode, Translation, Monitors, Sync & hotkeys, Network, About; an icon rail below 1100 px), setting groups, help text under every control, sliders with typed values, colour swatches with hex fields, keyboard focus rings, toasts with Undo, "Saved automatically" and what is playing in the sidebar. Dark, light or same-as-Windows theme. The Persian interface mirrors the whole window.
- Template editor with every property and a live preview that uses the overlay renderer, over dark, bright, busy game and transparency backdrops, with per-line contrast badges and an animation preview.
- Profiles: import and export the app list, undo a removal, the app's own icon in the list.
- The Vazirmatn font (SIL Open Font License 1.1) is bundled and loaded for the app only.
- `tools/screenshots.py` takes the README screenshots with the real app and renderer (temporary data folder, generated scenes).

### Changed

- Settings schema version 2. New keys get defaults; v1 settings keep working. On the first start of v2 the v1 look (fonts, colours, opacity, outline, width...) becomes the template "My style (v1)", so nothing changes visually except that the background opacity no longer fades the text.
- The settings window's tabs became sidebar sections; Appearance moved to Templates (the look) and Monitors (where the overlay goes and how it behaves).
- The packaged exe now contains the whole Python standard library and `python3.dll`, so Live mode's packages, installed on demand into `%APPDATA%\Dualyrics\live-runtime`, load in it. They are still not bundled (about 250 MB), which keeps the exe near 25 MB and its start fast.

### Fixed (pre-release review of v2)

- Whisper model downloads are checked against the sizes and checksums Hugging Face publishes (SHA-256 for the model, git blob ids for the small files); a damaged file is deleted and downloaded again, and only one download of a model runs at a time (Live mode and the Settings button no longer write the same file).
- `Ctrl+Alt+K` and the click-through, translation-only and mode choices in the menus now work while an app profile sets them (for example the Games preset's click-through).
- A translation corrected from the overlay while the full translation is still running, or after it failed, is no longer saved as a finished translation with empty lines; the correction is merged when the translation arrives.
- Look-ahead translation also covers the lines after a seek backwards.
- Ctrl+wheel with an app profile no longer moves the overlay or resets its size; "Reset size" goes back to 100 %; the place saved after a quick resize matches the size.
- The speech model is freed 10 minutes after Live mode stops, however it stopped; stopping Live no longer waits on the audio device in the UI thread; a broken Live install or audio device retries with a growing pause (up to 10 minutes) instead of flashing an error every 30 seconds.
- A `settings.json` saved with a UTF-8 byte order mark (Notepad, PowerShell) is read instead of being reset; an unreadable file is no longer "migrated" as v1 settings; v1 positions on high-DPI screens move to the right place.
- Gemini keys that cannot be decrypted (settings copied from another PC or Windows user) are kept until new keys are entered; Gemini model names are limited to plain ids.
- Dualyrics never matches its own process in app profiles; the process scan runs off the UI thread; icons are not read from network paths; template ids cannot be Windows device names (`con`, `nul`...); the text renderer no longer leaks a font when its cache is flushed.

## [1.0.0] - 2026-11-17

First public release.

### Added

- Time-synced lyrics from LRCLIB for the song playing in any player that reports to Windows media controls (Spotify, Spotube, YouTube and YouTube Music in Chrome, Edge, Firefox, Brave or Opera, and more). Lyrics without timing are shown as a scrolling list.
- A translation under every line, in 46 languages: Google Gemini with your own free key (the whole song in one request, so idioms are translated in context) or free Google Translate with no key. Several Gemini keys and models with automatic fallback.
- A floating, always-on-top bar: current line, its translation and the next line. Fonts, sizes, colours, background opacity, a transparent text-only mode with outline and shadow, width, alignment, rounded corners, position presets, position lock and click-through.
- Right-to-left layout for Persian, Arabic, Hebrew, Urdu, Kurdish (Sorani) and Pashto.
- A full settings window (General, Translation, Appearance, Sync, Network, About) in English and Persian.
- Sync controls: global offset, remembered per-song offset and global hotkeys.
- System tray icon, optional start with Windows, single instance (a second launch opens the settings of the running copy).
- Proxy support (none, system or custom `http://` / `socks5://`) with a connection test.
- Settings, cache and logs in `%APPDATA%\Dualyrics`; Gemini keys encrypted with Windows DPAPI.
- Windows build: one-file `Dualyrics.exe` (PyInstaller) and a release zip, built by `tools\build.ps1` and by GitHub Actions on every `v*` tag.

### Fixed (during pre-release review)

- Lyrics no longer flip between two songs when two media sessions come from the same app (for example two browser tabs).
- The media watcher restarts every few seconds when Windows media controls are not ready yet, for example right after sign-in.
- An LRCLIB outage (HTTP 5xx, 429 or a broken transfer) is treated as a network error and retried, instead of being cached as "no lyrics" for 24 hours.
- A slow lyrics or translation load can no longer write into the state of the next song.
- Quit ends the process right away instead of waiting up to about 75 seconds for unfinished network requests.

[Unreleased]: ../../compare/v2.1.0...HEAD
[2.1.0]: ../../compare/v2.0.0...v2.1.0
[2.0.0]: ../../compare/v1.0.0...v2.0.0
[1.0.0]: ../../releases/tag/v1.0.0
