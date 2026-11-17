# Good first issues

Drafts to open as issues with the `good first issue` label. Each one is small, has a clear end and touches one area of the code. Run `.venv\Scripts\python -m pytest` and `.venv\Scripts\python -m pyflakes dualyrics tests tools` before you open a pull request.

## 1. Add mpv, SMPlayer, KMPlayer and GOM Player to the built-in video profiles

**Area:** `dualyrics/profiles.py` · **Skills:** Python, no Windows API knowledge needed

The built-in profiles cover VLC, MPC-HC/BE and PotPlayer. People also watch films in mpv, SMPlayer, KMPlayer and GOM Player, which today fall under "Other media players" or "Everything else".

- Add one built-in profile per player (or one "More video players" profile) with the right `.exe` names (`mpv.exe`, `smplayer.exe`, `KMPlayer64.exe`, `KMPlayer.exe`, `GOM64.exe`, `GOM.exe`), kind `video`.
- Add the profile names to the i18n tables (`ap_builtin_<id>`, English and Persian).
- Extend `tests/test_profiles.py` so each exe matches the new profile.

## 2. Persian group captions wrap badly on the Sources page

**Area:** `dualyrics/ui/widgets.py` (`Kit.group`, `Kit.label`) · **Skills:** Tkinter / CustomTkinter

With the interface in Persian, the caption of the **Order** group on **Settings › Sources** ("اولین منبعی که...") is cut off: the end of the sentence shows at the start of the line and the rest is clipped. English is fine. Long right-to-left captions need the same wrap-to-width handling (`wrap` + `reserve`) that row help texts get, and a check that the label grows to two lines.

- Reproduce: Settings › General › Interface language › Persian, then open Sources.
- Fix the caption label in `Kit.group` so it wraps inside the card in both directions.
- Retake the screenshot with `tools\screenshots.py --only=fa-sources` after adding `"sources"` to `FA_SECTIONS`.

## 3. Save Live transcripts as .srt as well as .txt

**Area:** `dualyrics/live/engine.py` (transcripts) · **Skills:** Python, text formats

**Save transcripts** writes `transcripts\YYYY-MM-DD.txt`. An `.srt` file next to it (original and translation as two lines per cue, with start and end times from the caption timing) would let people keep subtitles for a video they watched.

- Add a setting `live_transcript_srt` (default off) with validation in `dualyrics/settings.py` and a switch in Settings › Live mode (English and Persian strings).
- Write the cues with correct `HH:MM:SS,mmm` times and numbering; add a unit test for the formatter.

## 4. A high-contrast built-in template

**Area:** `dualyrics/templates/builtin/` · **Skills:** JSON, an eye for readability

For people with low vision: large bold text, a solid black box at full opacity, white original line, yellow translation, no motion.

- Copy `minimal.json` to `high_contrast.json`, set the values, give it English and Persian names and descriptions.
- `tests/test_templates.py` checks every built-in template against the schema and the contrast rule of `docs/DESIGN.md` 3.3; make sure it passes (aim for a ratio of 7:1 or more).
- Add a row to the preset table in `docs/DESIGN.md` part 3.

## 5. Show exact values when hovering the Usage chart

**Area:** `dualyrics/ui/usage_page.py` · **Skills:** Tkinter canvas

The day-by-day chart on **Settings › Usage** prints a small number above each bar. On narrow windows the numbers are hard to read and tokens are rounded (`65.0k`). A tooltip on hover with the date, requests, tokens and seconds of audio for that day would help.

- Bind `<Motion>` and `<Leave>` on the canvas, find the bar under the pointer, and show a small label (reuse the toast or tooltip styling from `dualyrics/ui/widgets.py`).
- Keep it working in the mirrored Persian layout, where the bars run right to left.
