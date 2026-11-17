# How Dualyrics decides what to show

This page describes the v2 engine: Smart Auto (lyrics or live captions), per-app profiles, Live mode and the
lyrics timing. The code is split so the decisions are pure functions with unit tests:

| Part | Code | Tests |
|---|---|---|
| Smart Auto decision and hysteresis | `dualyrics/automode.py` | `tests/test_automode.py` |
| Per-app profiles and matching | `dualyrics/profiles.py` | `tests/test_profiles.py` |
| Live text logic (filter, dedupe, merge, reading time, context) | `dualyrics/live/text.py` | `tests/test_live_text.py` |
| Voice activity segmentation | `dualyrics/live/vad.py` | `tests/test_live_text.py` |
| Lyrics look-ahead window | `dualyrics/sync.py` | `tests/test_profiles.py` |
| Wiring (threads, overlay, settings) | `dualyrics/app.py`, `dualyrics/live/engine.py` | smoke run |

## 1. The loop

Every 100 ms the app (Tk thread) does this:

1. **Observe.** The media watcher reports the Windows media session (track, playing or paused, source app).
   Twice a second the app reads the foreground window (process, title, fullscreen). Every 3 seconds it lists
   the running processes.
2. **Match a profile** (section 3). A different profile must stay matched for 1 s before it applies, so an
   alt-tab does not move the overlay around. The profile's overrides (template, target language, monitor,
   position, width, click-through, translation only) are applied on top of the global settings.
3. **Decide** Lyrics, Live or Hide (section 2), with hysteresis.
4. **Show.** Lyrics mode renders the synced line, its translation and the next line. Live mode renders the
   current caption and its translation. Hide hides the overlay.

## 2. Smart Auto

### Inputs

| Input | Meaning |
|---|---|
| `mode` | The profile's mode, or the global mode when the profile says Auto (`auto`, `lyrics`, `live`, `off`) |
| `profile_enabled` | The matched profile is switched on (the "Everything else" profile is off out of the box) |
| `media_playing` | A media session reports Playing |
| `media_recent` | It was playing a moment ago (paused for less than the idle delay, or "hide when idle" is off) |
| `lyrics` | Lookup state of the current track: none, searching, found, not found, network error |
| `live_ready` | Live mode is installed and switched on |
| `live_when_no_lyrics` | The user allows Live for songs and videos that have no lyrics |
| `live_context` | A game, a video player or any fullscreen app is in front (profile kind game/video, or the fullscreen profile) |

### Rules (first match wins)

| # | Condition | Result |
|---|---|---|
| 1 | profile disabled, or mode Off | **Hide** |
| 2 | mode Lyrics | **Lyrics** while media plays or just paused, else Hide |
| 3 | mode Live | **Live** when ready; else Lyrics while media plays, else Hide |
| 4 | Auto, media playing, lyrics found | **Lyrics** (synced lyrics are more exact than speech recognition, even over a game) |
| 5 | Auto, media playing, lookup running or not started | **Live** in a live context (a film keeps its captions), else **Lyrics** ("Looking for lyrics…") |
| 6 | Auto, media playing, network error | **Live** in a live context, else **Lyrics** (shows the error, retries in 30 s) |
| 7 | Auto, media playing, no lyrics | **Live** when ready and (allowed or live context); else **Lyrics** (shows "No lyrics" for 8 s, then hides) |
| 8 | Auto, nothing playing, live context | **Live** when ready, else Hide |
| 9 | Auto, paused a moment ago | **Lyrics** (keeps the line on screen) |
| 10 | otherwise | **Hide** |

"Nothing playing and silence" ends up hidden in two ways: rule 10 when no game or video is in front, and in
Live mode the caption disappears after its reading time when nothing more is heard.

### Hysteresis

A new decision must stay the same for a while before the overlay switches: 0.4 s to Lyrics, 1.5 s to Live,
2 s to Hide. The first decision and a profile that says Off apply at once. When Live is no longer wanted the
capture keeps running for 8 s (quick flips do not restart it), and the Whisper model is freed after 10 minutes
without Live.

## 3. Per-app profiles

A profile names one or more exe files (or one exact exe path) and sets: enabled, mode (Auto, Lyrics, Live,
Off), template, target language, monitor, position, width, click-through, translation only, and when it
activates (foreground only, or also while running in the background; "default" follows the global setting).

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
speakers (WASAPI loopback, 30 ms frames)
  -> voice activity segmentation (cut at a natural pause)
  -> Whisper (faster-whisper, offline; GPU float16 or CPU int8)
  -> hallucination filter -> dedupe -> fragment merging      (CaptionStream)
  -> translation with context (Gemini) or free Google        (a moment later)
  -> overlay, for a reading-time based duration
```

### Segmentation

The RMS level of each 30 ms frame is compared with `max(floor, noise x multiplier)`. The noise floor follows
the level while nobody speaks. Sensitivity 1 to 10 sets the floor (0.016 down to 0.0021) and the multiplier
(4.0 down to 1.75). A segment starts with 0.2 s of pre-roll, ends after a 0.32 s pause, and is force-cut at the
maximum length (setting, 6 s by default). After a forced cut the last 0.2 s is kept as the start of the next
piece. Pieces with less than 0.25 s of voice are ignored. WASAPI loopback sends nothing while the PC is silent,
so the capture thread feeds silent frames in real time; that is how the segmenter notices the pause after the
last sentence. When Whisper falls behind, the oldest waiting piece is dropped, so captions stay live.

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
Google endpoint has no context and is the fallback (or the provider when chosen). Lines already in the target
language are not translated. A cache of 500 lines avoids paying twice for repeated lines.

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
loaded and discards the audio until resumed.

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
