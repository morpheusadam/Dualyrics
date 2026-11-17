# Changelog

All notable changes to Dualyrics are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: ../../compare/v2.0.0...HEAD
[2.0.0]: ../../compare/v1.0.0...v2.0.0
[1.0.0]: ../../releases/tag/v1.0.0
