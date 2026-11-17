# Changelog

All notable changes to Dualyrics are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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

[1.0.0]: ../../releases/tag/v1.0.0
