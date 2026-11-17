# How Dualyrics decides what to show

This page describes the engine: Smart Auto with the source priority of v2.1 (lyrics, on-screen subtitles or
live captions), per-app profiles, Live mode, the English pivot, the lyrics timing, dubbing and the translation
chain (Gemini key pool, free and offline translators, caches). The code is split so the decisions are pure functions with unit tests:

| Part | Code | Tests |
|---|---|---|
| Smart Auto decision and hysteresis | `dualyrics/automode.py` | `tests/test_automode.py`, `tests/test_sources.py` |
| Source order, strict lyrics match, subtitle area | `dualyrics/sources.py` | `tests/test_sources.py` |
| On-screen subtitles: change detection, debounce, dedupe, reader loop | `dualyrics/screen/logic.py`, `dualyrics/screen/engine.py` | `tests/test_screen.py` |
| English pivot (audio and on-screen text) | `dualyrics/live/pivot.py` | `tests/test_pivot.py` |
| Per-app profiles and matching | `dualyrics/profiles.py` | `tests/test_profiles.py` |
| Live text logic (filter, dedupe, merge, reading time, context) | `dualyrics/live/text.py` | `tests/test_live_text.py` |
| Voice activity segmentation (Silero VAD and energy) | `dualyrics/live/vad.py`, `dualyrics/live/silero.py` | `tests/test_live_text.py`, `tests/test_live_v22.py` |
| Interim commits of long speech | `dualyrics/live/interim.py` | `tests/test_live_v22.py` |
| Windows Live Captions engine (choice, sentences, poll loop, hidden window) | `dualyrics/live/wincaptions.py` | `tests/test_live_v22.py` |
| Lyrics look-ahead window | `dualyrics/sync.py` | `tests/test_profiles.py` |
| Dubbing schedule, engine fallback, ducking (section 9) | `dualyrics/dub/scheduler.py`, `engines.py`, `ducking.py` | `tests/test_review_v21.py` |
| Translation chain, Gemini key pool, offline translator, line cache, OCR/UIA dedupe (section 10) | `dualyrics/translate.py`, `keypool.py`, `offline.py`, `linecache.py`, `screen/dedupe.py` | `tests/test_keys_offline_v22.py` |
| Wiring (threads, overlay, settings) | `dualyrics/app.py`, `dualyrics/live/engine.py`, `dualyrics/screen/` (OCR, UIA, capture) | smoke run |

## 1. The loop

Every 100 ms the app (Tk thread) does this:

1. **Observe.** The media watcher reports the Windows media session (track, playing or paused, source app).
   Twice a second the app reads the foreground window (process, title, fullscreen). Every 3 seconds it lists
   the running processes.
2. **Match a profile** (section 3). A different profile must stay matched for 1 s before it applies, so an
   alt-tab does not move the overlay around. The profile's overrides (template, target language, monitor,
   position, width, click-through, translation only) are applied on top of the global settings.
3. **Decide** Lyrics, Screen, Live or Hide by walking the enabled sources in order (section 2), with
   hysteresis; start or stop the screen reader when it is the next source to try.
4. **Show.** Lyrics mode renders the synced line, its translation and the next line. Live mode renders the
   current caption and its translation. Screen renders the subtitle the app shows and its translation. Hide
   hides the overlay.

## 2. Smart Auto and the source priority (v2.1)

The rule is: for whatever is in front of the user, if the app already shows its own lyrics or subtitles,
use those; if not, use the audio. There are three **sources**, tried in the user's order (Settings >
Sources, globally or per app profile; each one can be switched off):

| Source | What it reads | Code |
|---|---|---|
| `lyrics` | Synced lyrics of the media session (LRCLIB). Browsers: strict match only (section 2.3) | `lyrics.py`, `sources.py` |
| `screen` | Subtitles the window in front shows: web player caption elements (UI Automation), else OCR of the subtitle area | `screen/` (section 6) |
| `audio` | Live mode: speech recognition of what the speakers play | `live/` (section 4) |

Default order: lyrics, screen, audio. Whatever the source, the overlay shows a line with its translation
under it (and, with the English pivot and "Also show the original-language line", the English as a small
third line, section 7).

### 2.1 Inputs

| Input | Meaning |
|---|---|
| `mode` | The profile's mode, or the global mode when the profile says Auto (`auto`, `lyrics`, `live`, `off`) |
| `profile_enabled` | The matched profile is switched on (the "Everything else" profile is off out of the box) |
| `media_playing` | A media session reports Playing |
| `media_recent` | It was playing a moment ago (paused for less than the idle delay, or "hide when idle" is off) |
| `lyrics` | Lookup state of the current track: none, searching, found, not found, network error (a strict-match failure in a browser is "not found") |
| `live_ready` | Live mode is installed and switched on |
| `live_when_no_lyrics` | The user allows Live for songs that have no lyrics |
| `live_context` | A game, a video player or any fullscreen app is in front (profile kind game/video, or the fullscreen profile) |
| `sources` | The enabled sources in priority order (the profile's own list when it has one) |
| `screen_ready` | The window in front may be read (section 6.1) and a reader works on this PC |
| `screen_text` | The screen reader sees subtitles now, or saw them less than 8 s ago (gaps between lines) |
| `browser_video` | The playing media session belongs to a web browser |

`context` below means `live_context` or `browser_video`: games, films and browser videos never wait for a
lyrics lookup.

### 2.2 Decision table

| # | Condition | Result |
|---|---|---|
| 1 | profile disabled, or mode Off | **Hide** |
| 2 | mode Lyrics | **Lyrics** while media plays or just paused, else Hide |
| 3 | mode Live | the source order without `lyrics` (captions only); see the walk below |
| 4 | Walk the sources in order; the first one that has something wins: | |
| 4a | `lyrics`: media playing and lyrics found | **Lyrics** |
| 4b | `lyrics`: media playing, lookup running or network error, no `context` | **Lyrics** ("Looking for lyrics…" or the error); the next sources are not tried |
| 4c | `lyrics`: not found (or `context` while searching) | next source |
| 4d | `screen`: `screen_ready` and (`context`, mode Live or media playing) and `screen_text` | **Screen** |
| 4e | `screen`: ready but no subtitles yet | next source; the screen reader keeps looking (`probe_screen`) |
| 4f | `audio`: Live ready and (mode Live, or `context`, or audio is ahead of lyrics in the order, or media playing with "caption songs without lyrics" on and no lyrics) | **Live** |
| 5 | nothing found, mode Live | Lyrics while media plays when Live is not installed, else **Hide** |
| 6 | nothing found, media playing | **Lyrics** with its notice ("No lyrics" for 8 s, then hidden) when `lyrics` is enabled, else Hide |
| 7 | nothing found, game or video player in front | **Hide** |
| 8 | paused a moment ago | **Lyrics** (keeps the line on screen) |
| 9 | otherwise | **Hide** |

Examples with the default order:

| In front | Result |
|---|---|
| Spotify, song with lyrics | Lyrics |
| Spotify, song without lyrics | Live (if "caption songs without lyrics" is on), else "No lyrics" |
| YouTube in Chrome, a song whose title, artist and length match LRCLIB | Lyrics |
| YouTube in Chrome, an ordinary video with captions on | Screen (YouTube's caption text through UI Automation) |
| YouTube in Chrome, a video without captions | Live |
| VLC or a game showing subtitles | Screen (OCR of the subtitle area) |
| A game without subtitles | Live |

The screen reader runs only while it can matter (rule 4d/4e), and for a few seconds after, so it does not
read the screen while lyrics are shown or nothing plays.

### 2.3 Strict lyrics match for browsers

A media session from a browser (a profile of kind "browser" claims its app id) is looked up with a filter:
an LRCLIB answer is accepted only when

- its duration and the video's duration are both known and differ by at most the tolerance (setting, 4 s
  by default; a music video with a long intro is therefore not matched, and its lyrics would be out of time
  anyway);
- its title equals the video title after cleaning (`Artist - Song (Official Video)` is also tried as
  "Song" by "Artist"), or shares at least 80 % of the words;
- the video's artist (or the artist from the title) is one of the answer's artists.

Strict lookups are cached under their own key, so a song played in Spotify and the same title in a browser
do not share a "not found".

### 2.4 Hysteresis

A new decision must stay the same for a while before the overlay switches: 0.4 s to Lyrics or Screen, 1.5 s
to Live, 2 s to Hide. The first decision and a profile that says Off apply at once. When Live is no longer
wanted the capture keeps running for 8 s (quick flips do not restart it), and the Whisper model is freed
after 10 minutes without Live. The screen reader stops 4 s after it is no longer wanted.

## 3. Per-app profiles

A profile names one or more exe files (or one exact exe path) and sets: enabled, mode (Auto, Lyrics, Live,
Off), template, target language, monitor, position, width, click-through, translation only, and when it
activates (foreground only, or also while running in the background; "default" follows the global setting).
Since v2.1 a profile can also have its own source order (section 2) and its own subtitle area (section 6).

Built-in profiles: Spotify, Spotube, Music apps (Apple Music, iTunes, AIMP, foobar2000, MusicBee, Tidal,
Deezer, YouTube Music, Winamp, Windows Media Player), Web browsers, VLC, MPC-HC/MPC-BE, PotPlayer, and three
catch-alls:

- **Other media players**: the owner of a playing media session that has no profile. This keeps every player
  that worked in v1 working.
- **Fullscreen games and apps**: any fullscreen window without its own profile. It uses the Games preset
  (Live, Game HUD template, click-through, top of the screen, foreground only).
- **Everything else**: off by default, so unknown apps never show anything.

### Matching order

1. An enabled profile of the **foreground** app. An enabled profile with mode Off therefore silences the
   overlay while that app is in front.
2. The foreground window is **fullscreen** and its app has no profile: the fullscreen profile. (An app whose
   profile is disabled is not taken over by the fullscreen profile.)
3. The app that owns the **playing media session** (its profile, else "Other media players"), when that
   profile may act in the background.
4. An enabled profile of a **running** app that may act in the background; profiles with an explicit Live or
   Lyrics mode come before Auto ones.
5. **Everything else**.

Media sessions are matched by substrings of their app id (`spotify`, `chrome`, `msedge`, Firefox's hash id
and so on); processes by exe file name, or by full path when the profile was made from a specific exe.

### Adding apps (used by the Apps tab)

`procs.running_apps()` lists apps with a visible window (name from the exe's version info, plus its icon via
`procs.exe_icon`), a file dialog adds any .exe, and `procs.pick_window()` waits for a click on any window
(Esc or a right click cancels). `profiles.new_profile(name, path, preset)` builds the profile from a preset
(games, video, music, custom) and `profiles.add_profile` inserts it before the catch-alls, once per exe.

## 4. Live mode

### Pipeline

```
Windows Live Captions (Windows 11 22H2+)       or  speakers (WASAPI loopback, 30 ms frames)
  caption box read every 25 ms (UI Automation)      -> voice activity segmentation (Silero VAD, or energy)
  -> newest sentence, sent on end / idle / 3 edits  -> Whisper (faster-whisper, offline; GPU float16 or CPU
                                                       int8; task=translate for the English pivot, section 7)
                                                    -> interim commits of complete sentences in long speech
  -> hallucination filter -> dedupe -> fragment merging      (CaptionStream)
  -> translation with context (Gemini) or free Google        (a moment later)
  -> overlay, for a reading-time based duration
```

### What is recorded (v2.2, `live/capture.py`, `live/procloop.py`)

**Settings › Live mode › Listen to** (`live_capture`):

| Setting | Records | How |
|---|---|---|
| All apps except Dualyrics (default) | everything the speakers play except Dualyrics' own process tree, so the dubbed voice is never transcribed again | WASAPI process loopback, exclude mode, with our own PID |
| Only the app in use | the active profile's app and its child processes; other apps (music, calls) are not captioned | WASAPI process loopback, include mode |
| Everything you hear | the default output device | WASAPI loopback through PyAudioWPatch (the v2.0 path) |

Process loopback: `ActivateAudioInterfaceAsync("VAD\Process_Loopback")` with `AUDIOCLIENT_ACTIVATION_PARAMS`
(the PID and include or exclude), an agile completion handler, then `IAudioClient::Initialize` in shared
loopback mode with `AUTOCONVERTPCM`, asking for 16 kHz mono 16-bit (48 kHz stereo when that is refused, then
resampled to 16 kHz with a stateful resampler). Packets are drained every ~10 ms into 30 ms frames; while the
app renders nothing, silent frames keep the segmenter's clock running. The app to record is the foreground
app when it rules the profile, else a running process named in the profile; its PID is walked up to the
first parent with another exe name (a browser's audio process -> the browser), so the whole tree is captured.
The target is looked up once a second; a new app re-opens the stream, a closed one is noticed at once. With
no app known, or for an app whose stream cannot be opened (e.g. a protected process), everything except
Dualyrics is recorded. Process loopback needs Windows 10 build 20348 or Windows 11; on older builds, or when
activation fails the first time, the capture falls back to the system loopback with one log line and a note
on the Live page. The setting applies to the Whisper engine; Windows Live Captions does its own capture of
everything you hear.

### Speech engine (v2.2, `live/wincaptions.py`)

Setting `live_engine`: `auto` (default), `live_captions` or `whisper`.

| Situation | `auto` uses |
|---|---|
| Live Captions unavailable (older than Windows 11 22H2, no `LiveCaptions.exe`, no UI Automation, no language pack) | Whisper |
| The spoken language is set and Live Captions has no pack for it | Whisper |
| The English pivot needs Whisper's speech translation (multilingual model, pivot on, speech not set to English or the target) | Whisper |
| Dubbing on with Gemini Live possible (engine Gemini Live, or automatic with Gemini keys: it needs the captured audio) | Whisper |
| Live Captions failed in the last 10 minutes | Whisper |
| Whisper's packages are not installed | Live Captions, whenever it can run |
| Otherwise | Live Captions |

`live_captions` uses it whenever it can run (a missing language pack is tried anyway); `whisper` never does.
Language packs are found as `MicrosoftWindows.Speech.<locale>` packages in the per-user package repository of
the registry, so the check opens no window and starts nothing.

**Running it hidden.** A Live Captions the user already has open is used as it is and never closed. Otherwise
Dualyrics starts `LiveCaptions.exe` minimized without activation and, as soon as its window (class
`LiveCaptionsDesktopWindow`) appears, gives it the `WS_EX_TOOLWINDOW` style (no taskbar button, not in
Alt+Tab), moves it to (-32000, -32000) and minimizes it with `SW_SHOWMINNOACTIVE`, so focus never moves. Every
second it is hidden again if it showed itself. The window stays open between Live runs (no flash at every
switch) and is closed after 10 minutes without Live or when the app quits, with its window style and normal
position given back first. A hidden window left by a run that crashed (a minimized tool window) is adopted.

**Reading.** The UI Automation element with AutomationId `CaptionsTextBlock` is found once and its `Name` read
every 25 ms. A failed read (ElementNotAvailable) looks the element up again (at most 4 times a second); no
caption text for 15 s means Live Captions cannot work. A closed Live Captions is started again, up to 3 times in
a row (the count resets after a minute of work). After that, or when it does not open its window within 10 s,
Live mode switches to Whisper by itself and tries Live Captions again after 10 minutes or a settings change.

**Sentences.** The box text (newlines as spaces) is split at sentence terminators (`. ! ? …` and `。！？؟।۔`;
not inside abbreviations such as "Mr." or "U.S." or in numbers). Complete sentences that are already there when
reading starts, after a pause or after a restart are never sent. The newest, unfinished sentence is sent when
it ends, when it has not changed for 1.25 s, or after 3 changes. Each sentence has a key: an event with the key
of the caption on screen updates that caption in place and translates it again (a change of punctuation only
is shown without a new translation). A finished sentence makes its caption final, so the next sentence is a new
caption. Only the last two sentences can produce events; an older line that Live Captions revises is ignored.

**Stale translations.** A new version of a caption cancels the queued translation of its older version. With
Live Captions, which sends many quick updates, a finished translation also cancels the queued translations of
older captions (they are no longer on screen; their transcript line is written without a translation).

### Segmentation

**Silero VAD** (default when onnxruntime is installed; `live/vad.py`, `live/silero.py`). faster-whisper ships
Silero VAD v6 (`assets/silero_vad_v6.onnx`) and depends on onnxruntime, so nothing is downloaded. Capture frames
are resampled to 16 kHz (a 3:1 average for 48 kHz, phase-keeping linear interpolation otherwise) and scored in
32 ms windows (512 samples plus 64 of context, with the recurrent state carried over). Rules:

- the threshold is 0.5 at sensitivity 5 (0.7 at 1 down to 0.25 at 10);
- a piece starts with 3 windows (~96 ms) of pre-roll, scored at the threshold so they never look like a pause;
- it ends after a pause of `end_silence` (0.5 s at first), which adapts to the speaker: the 75th percentile of
  the last 50 pauses of at least 0.1 s inside speech, x 1.2, within 0.3 .. 2.0 s;
- the pause needed gets shorter as the piece grows: x 0.75 after 3 s, x 0.5 after 6 s, x 0.25 after 10 s (at
  least 3 windows);
- at the maximum length (setting, 6 s by default) the piece is cut at its quietest point: the confidence is
  smoothed over ~160 ms and its minimum in the last 70 % is used when it is below the threshold, below 80 % of
  the average, or simply below the average; the rest stays as the start of the next piece (no overlap). With no
  such point the whole piece goes;
- a piece with less than 0.3 s of speech windows waits up to 0.6 s for more speech and is merged with it; when
  none comes it is still kept with at least 0.12 s of speech (a click is dropped);
- a piece with fewer than 25 % speech windows (music, noise) is dropped.

**Energy** (fallback without onnxruntime, or `live_vad = energy`). The RMS level of each 30 ms frame is
compared with `max(floor, noise x multiplier)`. The noise floor follows the level while nobody speaks.
Sensitivity 1 to 10 sets the floor (0.016 down to 0.0021) and the multiplier (4.0 down to 1.75). A segment
starts with 0.2 s of pre-roll, ends after a 0.32 s pause, and is force-cut at the maximum length. After a forced
cut the last 0.2 s is kept as the start of the next piece. Pieces with less than 0.25 s of voice are ignored.

WASAPI loopback sends nothing while the PC is silent, so the capture thread feeds silent frames in real time;
that is how the segmenter notices the pause after the last sentence. When Whisper falls behind, the oldest
waiting piece is dropped, so captions stay live. Each piece has a generation number that changes when it is
emitted or dropped, so a trim meant for one piece never cuts the next.

### Interim commits (v2.2, `live/interim.py`)

Setting `live_interim` (on). While speech goes on, the piece buffered so far is transcribed again once at least
2 s of new audio came since the last pass (so at least 2 s are buffered; never sooner than 1 s after the last
pass, and only while Whisper has nothing else waiting). The text, minus what repeats the last 50 committed
characters (a case-insensitive suffix/prefix match), is split into sentences (a "sentence" with 8 letters or
fewer is merged into the next one). All complete sentences but the last are committed: captioned with times in
proportion to their length, and translated at once. Their audio is trimmed from the front of the piece: the
committed share of the text times the audio length, plus 0.3 s, keeping at least 0.5 s. When the piece ended
while the pass ran, the trim is applied to the finished piece instead. When the piece finally ends, its rest is
transcribed and the start that repeats the committed text is removed.

The unfinished last sentence is shown as a dim preview line (`live_interim_preview`, on), in the template's
small third line under the caption, or in the template's status line when no caption is on screen. It goes when
the piece ends, after 6 s without a newer pass, while paused, and with "translation only". Interim passes are
skipped when each piece needs two Whisper passes ("Also show the original-language line").

### Recognition

The source language is detected per piece, fixed by the user, or English for the `.en` models. The Whisper
prompt holds the app or game name and the previous caption, which helps it spell names (Kratos, Midgard) and
keep punctuation. The GPU is tried only when a CUDA device and the cuBLAS and cuDNN DLLs exist; otherwise, or
when loading on the GPU fails, the model runs on the CPU with int8.

### Hallucination filter

A recognition is dropped when:

- it has no letters or digits after removing music notes (`♪♪`, `...`);
- it is a sound tag only (`[Music]`, `(applause)`);
- it is a known subtitle-credit phrase ("Thank you for watching", "Please subscribe", Amara.org credits);
- it is a short phrase that is also real speech ("Thank you.", "Bye.", "you") and Whisper was not sure
  (average log probability at or below -0.5, or no-speech probability at or above 0.3);
- confidence is very low (average log probability below -1.6), or low (below -1.0) while Whisper also thinks
  there was no speech (no-speech probability above 0.6);
- it repeats itself (gzip compression ratio above 2.4, or the same word five times in a row).

Music notes around sung words are removed and the words are kept, so songs without lyrics still get captions.

### Dedupe and merging

- **Same text again** within 8 s (ignoring case and punctuation), or a piece of the previous caption: dropped.
- **The same words again plus more** (Whisper heard a longer version): replaces the previous caption.
- **Overlap** from the kept tail: words at the start of the new piece that repeat the end of the previous
  caption are removed.
- **Fragments**: when the previous caption has no sentence-ending punctuation, the pause is at most 1.2 s, the
  merged caption stays within 140 characters and 7 s of speech, the new piece is appended to it. The caption is
  then translated again; a translation of the older, shorter version is ignored when it arrives.

### Translation with context

Each caption is translated on its own, a moment after it appears (the overlay shows `…` meanwhile). Gemini
gets the last N captions with their translations (setting, 4 by default), the app or game name and the media
title, and is asked to translate only the new line and to keep names as it translated them before. The free
Google endpoint has no context and is the fallback (or the provider when chosen), and the offline translator
comes after it (section 10). Lines already in the target language are not translated. A cache of 500 lines in
memory and the line cache on disk (section 10.3) avoid paying twice for repeated lines, also after a restart.

### Reading time

A caption stays on screen for `clamp(min, chars / cps, 8 s)` from the moment it last changed (or its
translation arrived), where `chars` is the longer line plus half the shorter one (the reader reads one fully
and skims the other) and `cps` is the reading speed (17 characters per second by default). With "translation
only", only the translation counts. While speech continues, a caption stays 2 s longer so the overlay does
not flicker between lines. A newer caption always replaces the current one: Live never queues behind the
speaker.

### Transcripts and pause

With "Save transcripts" on, each finished caption and its translation is appended to
`%APPDATA%\Dualyrics\transcripts\YYYY-MM-DD.txt`. Pause (tray, hotkey Ctrl+Alt+P, Live tab) keeps the model
loaded and discards the audio until resumed; with Live Captions, what was said during the pause is not
captioned afterwards.

## 5. Lyrics mode

- **Line timing**: the playback clock (anchored to the player's reports, see `media.py`) minus the global
  offset and the per-song offset, plus a 0.2 s lead, picks the line with `bisect` over the LRC timestamps.
- **Whole-song translation** (v1): Gemini translates the whole song in one request so idioms follow the story;
  repeated lines are sent once; wrong line counts are repaired, retried, then chunked.
- **Look-ahead translation** (v2): while Gemini is still working, the current line and the next 11 lines are
  translated with the free translator (only when the user allows the free fallback), so the first lines are
  not left untranslated. When the singer passes the covered window, the next window is translated. Gemini's
  full translation replaces these as soon as it arrives.
- **Cache**: lyrics per track (a "not found" answer is retried after a day; network errors are never cached),
  translations per track, target language and provider, and per-song offsets, all in
  `%APPDATA%\Dualyrics\cache`.
- **Translation only**: the original line is hidden whenever a translation exists; the next-line slot then
  shows the next translation. Lines without a translation still show the original.

## 6. On-screen subtitles (the `screen` source)

### 6.1 Which window

Only the window in front, and only when the matched profile is that app (never a player in the background
while another app is in front), for profiles of kind video, browser or game, the fullscreen profile, or an
app whose profile has its own source order or subtitle area. Music apps are not read by default, so the
title and time in a player bar never become "subtitles".

The OCR grab copies whatever the screen shows in the subtitle area, so it runs only while the target window is
the foreground window: when another window comes in front (also during the few seconds the reader keeps
running after its source is no longer wanted), nothing is grabbed, read or sent for translation.

### 6.2 Readers

1. **UI Automation** (browsers): Chrome and Edge expose page elements with their HTML class as the UIA class
   name. The first element whose class contains one of the configured names (`ytp-caption-segment`,
   `caption-window`, `player-timedtext`, `vjs-text-track`, ... setting) is found by a search of the page at
   most every 3 s (every 0.5 s for 20 s after it was lost), then only its text children are read. A page
   that gave caption text in the last 15 s is trusted: no OCR runs for it. Firefox without native UIA, and
   pages without such elements, fall back to OCR.
2. **OCR** of the subtitle area of the window's client area (setting, as fractions, default 8-92 % across
   and 66-97 % down; per profile with the picker). Windows.Media.Ocr, offline and free; the language is
   automatic (the Windows profile languages) or fixed. The area is copied from the screen with GDI without
   `CAPTUREBLT`, and our overlay is excluded from capture (`WDA_EXCLUDEFROMCAPTURE`) while the reader runs,
   so OCR never reads our own lines.

The reader samples 1 to 6 times a second (setting, 3.5 by default).

### 6.3 From frames to subtitle lines (`screen/logic.py`)

- **Change detection**: the area is reduced to 160x40 and turned into a mask of bright pixels (subtitles
  are white or yellow). OCR runs when more than 0.6 % of the mask changed, and at least every 1.5 s. A film
  moving behind a subtitle barely changes the mask.
- **Line filter**: OCR lines that look like interface (short ALL-CAPS legends such as "ACTIVE MARKERS",
  mostly symbols) or like noise (fewer than 60 % of the tokens are words, which is what OCR makes of a
  script it cannot read) are dropped; a line that stays for 10 s is part of the app (HUD, logo, menu title)
  and is ignored until it has been gone for 30 s.
- **Debounce**: a text must be read the same way (similarity 0.82 or more, so one wrong letter does not
  count) on 2 samples in a row before it shows; it disappears after 2 empty samples. The most complete
  reading is kept.
- **Dedupe**: the same line read again is not a new caption; a line that comes back within 1.5 s after a
  flicker resumes the previous caption (and its translation); a near-identical reading later still gets its
  earlier translation without a request (section 10.4).
- **Activity**: the app counts as showing subtitles after two different lines within 25 s (one text could
  be a title), or one line from UI Automation; it stays so for 90 s, and `screen_text` stays true 8 s after
  the last line. A text on screen for 15 s is a label and is ignored afterwards.

Each new line is translated like a Live caption (context, Gemini or free Google, section 7 for other
languages) and stays on screen while the app shows it, at least the shortest time on screen.

## 7. Any language (English pivot)

**Audio.** With "Pivot through English" on (default) and a multilingual model, Whisper runs with
`task=translate`, which writes English for speech in any language and reports the spoken language. The
English line is then translated into the target language (with context). English speech is used as heard.
Speech already in the target language is written down as heard (a second pass). With "Also show the
original-language line" (default off) a second pass writes the original too; the overlay then shows the
original line, the translation under it and the English in the small third line. When the target language
is English, the English line is the translation. With the pivot off, Whisper writes the original language
and the translator goes straight to the target. English-only models never pivot.

**On-screen text.** The text goes to English first (free Google when it is the provider or the allowed
fallback, else Gemini; it also detects the language), then to the target. Text in English or already in the
target language skips the pivot. The same display rules apply.

| Setting | Default | Effect |
|---|---|---|
| Pivot through English | on | Any language -> English -> target |
| Also show the original-language line | off | Original on top, translation, English small (two Whisper passes) |

## 8. Settings > Sources

- **Settings for**: all apps, or one profile. A profile follows the global order until "This app has its
  own order" is switched on.
- **Order**: the three sources with their position, up / down buttons and an on / off switch.
- **Lyrics matching**: strict match in browsers (on), allowed length difference (4 s).
- **On-screen subtitles**: web player captions (UI Automation, on), the subtitle area (OCR, on), text
  language (automatic or an installed OCR language), readings per second (3.5), the subtitle area with
  **Pick on a window…** (click the window, then drag a rectangle on a frozen picture of it; Enter or Save
  keeps it, Esc cancels; saved for the chosen scope), **Reset**, **Test on a window…** (shows what UI
  Automation and OCR read there right now) and the caption element classes.
- **Any language**: pivot through English (on), also show the original-language line (off).

## 9. Dubbing

Dubbing (off by default, `dualyrics/dub/`) speaks the translation of what the overlay shows. The app calls the
dubber every tick with what is on screen: timed lyric lines, the current caption of Live mode or of on-screen
subtitles, or, for the Gemini Live engine, the captured audio itself. Clips are made on worker threads and
played on one player thread, which also turns the other apps down, so the interface never waits.

**Timed lines (lyrics).** The clips of the next 4 lines (at most 25 s ahead) are made in advance, so a line's
speech starts when the line starts. A clip that would not fit before the next line is spoken faster, up to
35 % faster. A line whose time passed more than 1.2 s ago is skipped instead of spoken late. When a line is
due while the previous clip still plays, that clip may run on for 0.6 s and is then cut, so speech stays in
step with the song. Clip lengths are estimated before the audio exists from a speaking rate per language
(15 characters per second by default, 13 for Persian and Arabic, 7 for Japanese).

**Captions (Live mode, on-screen subtitles).** Live captions grow while sentence fragments are merged, so a
caption is spoken once it has stayed the same for 0.7 s or a newer caption arrived. At most 2 captions wait,
and a clip older than 6 s is dropped, so speech never falls far behind the picture.

**Engines.** Automatic uses Gemini Live (speech to speech, the captured audio streams to Gemini and the
spoken translation comes back) for the audio source when a Gemini key exists; for text it tries Gemini
text-to-speech, then Microsoft Edge voices, then Piper (offline). An engine that fails (rate limit, network,
not installed) hands the line to the next one, with a notice. Every Gemini request goes to the usage meter.

**Voices.** With Voice set to Automatic, the voice follows the gender chosen for the target language
(`dub_gender`, female by default): Edge uses the language's first (female) or second (male) voice, for
Persian Dilara or Farid; Gemini uses Kore or Charon; Piper has a female and a male voice for some languages
and otherwise its one voice (all Persian Piper voices are male). Piper speaks with the other downloaded voice
of the language when the chosen one is not downloaded. A voice picked by name always wins.

**MP3 without Media Foundation.** Edge voices arrive as MP3. They are decoded with dr_mp3 (pyminiaudio,
bundled), else PyAV (installed with Live mode), and played as PCM through `waveOut`, which every Windows
edition has; the MCI `mpegvideo` driver, missing on Windows "N" editions, is the last resort.

**Ducking.** While a clip plays, every other audio session on the active output devices is lowered (by 60 %
by default) through the Windows volume mixer interfaces, and restored a moment after the voice stops and when
Dualyrics quits. Dualyrics' own session is never ducked, and a session whose volume the user changed while it
was ducked keeps the user's volume.

### 9.1 Heavy installs that survive a flaky connection (v2.2, `live/deps.py`, `download.py`)

"Install Live mode" (and the Edge voices, the offline translator) run pip in three steps:

1. `pip install --dry-run --report` resolves the packages (wheel metadata only) and lists every file with
   its URL and sha256. The app downloads them itself into `%APPDATA%\Dualyrics\downloads\wheels`: a broken
   connection continues with an HTTP `Range` request, up to 6 attempts with a growing pause, and each file is
   checked against its sha256 before it is used. pip then installs from those files with `--no-index`.
2. When that cannot be used (pip older than 22.2, a file the app cannot fetch, the local install fails), pip
   downloads by itself with `--retries 10 --timeout 60`, plus `--resume-retries 20` on pip 25.1 and later.
3. The packages are imported in a fresh interpreter. From the start of an install until this check passes,
   the packages are listed in `install-pending.json` and count as missing, so an install stopped half-way
   (closed app, lost connection, a DLL that does not load) never looks installed.

Downloads and caches follow the same rule (the idea of LiveTranslate's `_hf_repo_complete`): a Whisper model
counts as installed only with its `.complete` marker, written last, and every file at the size the marker
records; Piper only when its archive was unpacked to the end; offline translation packages only with their
`.complete` marker.

## 10. Translation that never stops (v2.2)

Every line goes down a chain of translators until one answers:

1. **Gemini**, through the key pool (`dualyrics/keypool.py`);
2. **free Google Translate** (when it is the provider or the allowed fallback);
3. **the offline translator** (`dualyrics/offline.py`), when it is switched on and a language package fits.

When every tier fails the line is marked failed; a song's translation is tried again after 90 s, 3 min, 6 min
and then every 10 min for as long as the song plays (before v2.2 it gave up after three tries).

### 10.1 Key pool

- **Round robin.** Request *n* starts at enabled key *n* mod *N* of the first model; the models stay in the
  user's order (the best one first). A key x model that rests is skipped, so the next key, then the next model,
  answers. Before v2.2 every request started at key 1, which used key 1 up first.
- **State per key x model.** `ok`; `rate_limited` (HTTP 429 with a per-minute quota: rests for the server's
  `retryDelay` + 1 s, else 90 s); `exhausted` (429 whose quota id contains `PerDay`: rests until the next reset
  of the free tier at midnight US Pacific, stored as an absolute time in `%APPDATA%\Dualyrics\
  gemini_keys_state.json`, so a restart does not ask again); `cooling` (bad JSON 20 s, other errors 60 s, 5xx
  30 s, a model the key cannot use 24 h). Per key: `invalid` (401, or 400/403 saying the API key is not valid:
  6 h or until a Test passes) and `off` (switched off in Settings). Only 12-hex-digit SHA-256 fingerprints of
  the keys are stored.
- **Nothing left.** When no key x model can be asked, `ask` raises `KeysResting` at once, without a request,
  and the next tier answers; one notice says when the first key is back. A network error does not try the other
  keys (they would fail the same way).
- **Shared projects.** Google counts the free quota per Cloud project, so two keys of one project add nothing.
  Evidence per pair of keys: +1 when both get 429 for the same model within 5 s, -1 when one answers within
  5 s of the other's 429; from 2 points the pair counts as one project. When two keys both ran out of the daily
  quota, their requests today (usage meter) decide: at most 1.4x the model's daily limit together = one
  project, 1.8x or more = separate projects. Settings shows a warning for such keys, and a daily-quota 429 of
  one key marks its partners exhausted too, so they are not asked in vain.
- **Settings › Translation** lists every key (masked): its state and when it is back, today's requests and
  tokens from the usage meter, a switch and a Test button (one tiny request per model, which also updates
  the state).

### 10.2 Offline translator

Argos Translate language packages (`.argosmodel`, MIT; the index is argospm-index's `index.json`) are read
directly with CTranslate2 (already there with Live mode) and SentencePiece, without the argostranslate package
or spaCy/Stanza. A package is a zip of one folder with `metadata.json`, a CTranslate2 model in `model/` and
`sentencepiece.model` (BPE packages are listed as not supported). A line is split into sentences at sentence
punctuation, all sentences of a request go through `translate_batch` together (beam 2, `replace_unknowns`,
length penalty 0.2, the package's `target_prefix`), and are decoded and joined again. A pair without its own
package goes through English when both halves are installed. The source language is the one known (Live,
pivot), else the writing system's guess, else English when the words look English (common English words),
else the only other language with a package to the target; Latin or Cyrillic text in an unknown language is not
translated offline (never pushed through the English model or passed through as "already English"). Packages
download on demand into
`%APPDATA%\Dualyrics\offline-translate` (resumable; zip members that would land outside the folder are
refused) and their licence is read from `metadata.json` or the README and shown; at most 3 models stay loaded.
The free Google tier rests 15 s after a failure (doubling to 2 min), so while it is down the offline tier
answers at once instead of every line waiting for a timeout.

### 10.3 Caches

- **Whole songs** (`cache.py`): one file per song, target language and translator. A song cached by a lower
  tier (free or offline) is used as it is while Gemini cannot be asked; when Gemini can, it is shown at once
  as a preview and replaced by Gemini's translation.
- **Lines** (`linecache.py`, `cache\lines.sqlite`): every Live and on-screen line per (normalized text, target
  language), with its translator; a better translator replaces a worse one, never the other way round. The
  English pivot of on-screen text is kept the same way. At most 60 000 lines, the ones used longest ago go first.
  A line a lower tier translated is asked from Gemini again only when Gemini can be asked.

### 10.4 Near-duplicate readings (`screen/dedupe.py`)

Rules of Translumo's `TextResultCacheService` (Apache-2.0): similarity = mean of Jaro and Sørensen-Dice (Dice
twice when Jaro is 0). On a new or more complete on-screen line, the line is a repeat when the same text was
read within 3 s (the cache empties itself after 3 s without readings), or when a reading more than 0.65
similar (0.55 for CJK and Thai) with as good a score is cached; a UI Automation reading (trusted) covers an OCR
reading only above 0.9. An OCR reading scores its number of letters. A repeated line that is more than 0.9
similar to one of the last 20 translated lines gets that translation at once, without a request. A new
translation of the same caption that is more than 0.955 Jaro-similar to the shown one does not replace it, in
on-screen subtitles and in Live mode, so captions do not flicker.
