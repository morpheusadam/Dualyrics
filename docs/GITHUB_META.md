# GitHub repository metadata

Values to paste into the repository settings when the project is published.

## Repository name

`Dualyrics`

## Description (About box)

```
Synced lyrics with a translation under every line, floating over any app on Windows 10/11. Works with Spotify, Spotube, YouTube Music and browsers through Windows media controls. Lyrics from LRCLIB; translation by Gemini or free Google Translate into 46 languages, Persian included. Free, open source, no login.
```

## Topics

```
lyrics
synced-lyrics
lyrics-translation
translated-lyrics
spotify
spotify-lyrics
youtube-music
spotube
lrclib
lrc
gemini
google-translate
windows
windows-app
overlay
desktop-overlay
karaoke
language-learning
persian
python
```

## Social preview

Image size 1280 x 640 px (Settings › General › Social preview). Use the app icon on the left and `docs/screenshots/overlay.png` on the right, with this text:

```
Dualyrics
Synced lyrics + a translation under every line
Spotify · YouTube Music · Spotube · any Windows player
Free · Open source · 46 languages
```

## Website field

Leave empty, or point it to the latest release: `https://github.com/<owner>/Dualyrics/releases/latest`.

After publishing, set `REPO_URL` in `dualyrics/__init__.py` to the repository URL. The About tab shows it as a link, and it is added to the HTTP User-Agent of every request.

## Alternative names

| Name | Why |
|---|---|
| Undertone | The translation sits under the line being sung; short and easy to remember, though less obvious in search. |
| Lyriglot | Lyrics plus polyglot: says "lyrics in many languages" in one made-up word that is easy to own on GitHub and in search. |
| SongSubs | Describes the app as subtitles for songs, a phrase people already understand from video players. |
