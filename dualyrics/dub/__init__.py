"""Auto-dubbing (off by default): speak the translation (docs/ALGORITHM.md section 9).

- ``scheduler``: when each line is made and played (pure logic, unit tested).
- ``engines``: Gemini text-to-speech, Edge neural voices (free, online) and Piper (offline, optional
  download), with automatic fallback; ``gemini_live``: speech-to-speech translation of the captured audio
  through the Gemini Live API.
- ``audio``: playback (waveOut for PCM, MCI for MP3); ``ducking``: other apps get quieter while we speak.
- ``controller``: the ``Dubber`` the app drives from its tick.
"""
