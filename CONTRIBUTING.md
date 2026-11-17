# Contributing to Dualyrics

Thanks for helping. Bug reports, fixes, interface translations and small features are all welcome.

## Reporting a bug

Open an issue with:

- your Windows version and the player you use (Spotify, Spotube, a browser and which one);
- the song title and artist, if the problem is about lyrics or timing;
- what you expected and what happened;
- the relevant part of the log. Start Dualyrics with `--debug`, reproduce the problem, then open the log from **Settings › About › Open log** (`%APPDATA%\Dualyrics\logs\dualyrics.log`).

Check logs and screenshots for API keys or anything private before you post them.

If the lyrics themselves are wrong or missing, the fix usually belongs on [LRCLIB](https://lrclib.net), not in Dualyrics.

## Setting up a development copy

Requirements: Windows 10 or 11 and Python 3.10 or newer.

```powershell
git clone <repository-url> Dualyrics
cd Dualyrics
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pytest
.venv\Scripts\python -m dualyrics --debug
```

To keep your test settings apart from your normal ones, set a separate data folder first:

```powershell
$env:DUALYRICS_HOME = "$PWD\.devdata"
```

## Running the tests

```powershell
.venv\Scripts\python -m pytest
```

The tests cover LRC parsing, title cleaning, settings validation and the translation line-count repair. They do not need network access. Please add a test when you fix a bug in one of those areas.

## Code layout

| File | What it does |
|---|---|
| `dualyrics/app.py` | Entry point, main loop, song loading |
| `dualyrics/media.py` | Now playing from Windows media controls, playback clock |
| `dualyrics/lyrics.py` | LRCLIB client, LRC parser, title and artist cleaning |
| `dualyrics/translate.py` | Gemini and free Google Translate, line-count repair |
| `dualyrics/overlay.py` | The floating lyrics window |
| `dualyrics/settings.py` | Defaults, validation, saving, DPAPI key encryption |
| `dualyrics/settings_window.py` | The settings window |
| `dualyrics/i18n.py` | Interface strings (English and Persian) |
| `dualyrics/langs.py` | Translation languages and language detection |
| `dualyrics/cache.py` | Lyrics, translation and offset cache |
| `dualyrics/tray.py`, `hotkeys.py`, `startup.py`, `net.py`, `winutil.py` | Tray icon, global hotkeys, start with Windows, HTTP and proxy, Win32 helpers |

## Building the Windows app

```powershell
.venv\Scripts\pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

This runs the tests and PyInstaller with `Dualyrics.spec`, and writes `dist\Dualyrics.exe`, `dist\Dualyrics-<version>-windows-x64.zip` and `dist\SHA256SUMS.txt`. To publish a release, bump `__version__` in `dualyrics/__init__.py`, add a section to `CHANGELOG.md`, then push a tag such as `v1.0.1`. The `Release` workflow checks that the tag matches the version, builds on `windows-latest` and attaches the files to a GitHub Release.

## Pull requests

- Keep each pull request to one change, and describe what it fixes and how you tested it.
- Match the existing style: type hints, short docstrings, standard library first.
- A new setting needs a default in `settings.defaults()`, validation in `settings.validate()`, a control in the settings window, strings in `i18n.py` and a row in the configuration table in `README.md`.
- New interface text needs both an `en` and an `fa` string in `i18n.py`.
- Do not add telemetry, accounts or network calls beyond LRCLIB and the chosen translator.

## Adding an interface language

Add the language code to `UI_LANGUAGES` in `settings.py` and to `set_language()` in `i18n.py`, add a translation for every entry in `i18n.py`, and add it to the `ui_language` option list in `settings_window.py`. For a right-to-left language, also make `is_rtl()` in `i18n.py` return true for it.

## License

By contributing, you agree that your contribution is released under the [MIT License](LICENSE).
