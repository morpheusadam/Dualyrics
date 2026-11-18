# Reverse engineering: Dualyrics v2.2 next to the best open-source projects in its space

This page compares each Dualyrics module with the open-source projects that solve the same problem, from
their **implementations** (not their READMEs), and lists what they do better: edge cases, timing constants,
recovery paths, UX details and performance tricks. It ends with a prioritised gap list and implementation
notes for the top 10.

- Dualyrics baseline: v2.2.0 (`794000e`).
- References read on 2026-11-18 from their default branches (file and function names below are upstream's).
- Licence rule (same as `NOTICE` / `docs/ATTRIBUTION_NOTES.md`): code may be ported only from MIT, BSD or
  Apache-2.0 projects, with a NOTICE entry; GPL projects (LunaTranslator, BetterLyrics) are **ideas only** and
  are marked *(idea)* below. Nothing in this page is copied source text.

| Reference | Licence | Read (implementation files) | Compared with |
|---|---|---|---|
| [SakiRinn/LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator) | Apache-2.0 | `src/Translator.cs` (`SyncLoop`, `TranslateLoop`, `DisplayLoop`, `IsOverwrite`), `src/utils/TextUtil.cs`, `src/utils/RegexPatterns.cs`, `src/models/TranslationTaskQueue.cs`, `src/utils/LiveCaptionsHandler.cs`, `src/apis/TranslateAPI.cs`, `src/apis/LLMRequestDataFactory.cs`, `src/models/Setting.cs` | `live/wincaptions.py`, `live/engine.py`, `live/livetranslate.py`, `render.py` |
| [TheDeathDragon/LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate) | MIT | `main.py` (`_run_asr`, `_recover_asr_worker`, `_maybe_recycle_asr_worker`, `_process_segment`, `_do_interim_asr`), `translator.py`, `vad_processor.py`, `audio_capture.py`, `asr_worker.py`, `model_manager.py` | `live/*`, `live/livetranslate.py`, `translate.py` |
| [ramjke/Translumo](https://github.com/ramjke/Translumo) | Apache-2.0 | `Translumo.Processing/TranslationProcessingService.cs` (`TranslateInternal`, `GetIterationDelayMs`, `GetBestDetectionResult`), `TextProcessing/*`, `OCR/WindowsOCR/WinOCREngineWithPreprocess.cs`, `WindowsOCRHelper.cs`, `Services/ScreenDXCapturer.cs` | `screen/*` |
| [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming) | MIT | `whisper_online.py` (`HypothesisBuffer`, `OnlineASRProcessor.prompt/process_iter/chunk_completed_*`), `silero_vad_iterator.py` | `live/interim.py`, `live/stt.py`, `live/engine.py` |
| [Hon-Lu/OverTranslate](https://github.com/Hon-Lu/OverTranslate) | MIT | `Services/Realtime/*` (`FrameFingerprint`, `RealtimeRetryBackoff`, `RealtimeInFlightTranslations`, `RealtimePublishOrder`, `RealtimeReadingMerge`, `CollapsedDetection`, `RealtimeGate`, `Capture/CaptureMiss`, `Capture/WgcFrameThrottle`), `Services/LatestRequest.cs`, `OverTranslate.Translation/OpenAi/OpenAiPrompt.cs`, `Layout/RealtimeTextSizes.cs` | `screen/*`, `live/livetranslate.py`, `render.py` |
| [yuxino/Mimi](https://github.com/yuxino/Mimi) | MIT | `shared/mimi-core/src/translation_policy.rs`, `openai_transcript_committer.rs`, `live_pair_aligner.rs`, `shared/mimi-runtime/src/clients/gemini_live_client.rs`, `provider_network.rs` | `translate.py`, `keypool.py`, `dub/gemini_live.py` |
| [KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | Apache-2.0 | `core/realtime_text_stabilizer.py`, `core/preroll.py`, `core/tail_transcription.py`, `core/realtime_merge.py`, `core/voice_activity.py`, `core/realtime_boundary_detector.py` | `live/vad.py`, `live/interim.py`, `live/wincaptions.py` |
| [HIllya51/LunaTranslator](https://github.com/HIllya51/LunaTranslator) | GPL-3.0 *(ideas)* | `textio/textsource/ocrtext.py` (`getresauto`, `waitforstable`), `textio/textsource/mssr.py`, `translator/basetranslator.py`, `tts/basettsclass.py` | `screen/*`, `translate.py`, `dub/*` |
| [jayfunc/BetterLyrics](https://github.com/jayfunc/BetterLyrics) | GPL-3.0 *(ideas)* | `Helpers/MetadataComparer.cs`, `Helpers/Lyrics/LyricsGapHelper.cs`, `Helpers/LatestOnlyTaskRunner.cs`, `Services/GsmtcService/GsmtcServiceBase.cs`, `Services/LyricsSearchService/LyricsSearchService.cs` | `lyrics.py`, `media.py`, `render.py` |

Several findings were checked by running the Dualyrics code in `.venv` (section 12).

---

## 1. Windows Live Captions engine (`live/wincaptions.py`)

Dualyrics already ports the core of LiveCaptions-Translator (25 ms poll, `CaptionsTextBlock`, idle 1.25 s / 3
changes, stale-translation cancel) and goes further (a user's own Live Captions is left alone, window style
restored, sentence keys, automatic Whisper fallback). What the reference does that Dualyrics does not:

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 1.1 | **Minimum length before a change counts.** `syncCount` only grows when the unfinished sentence is at least `SHORT_THRESHOLD = 10` UTF-8 bytes; a 1-3 word start never triggers a translation. | `Translator.SyncLoop`, `TextUtil.SHORT_THRESHOLD` | `SentenceTracker.feed` counts every change: "I am go" (7 bytes) is sent after 3 polls (75 ms). A 23-word unpunctuated sentence produced **7 partial emits** in a probe (section 12), each a Gemini request. |
| 1.2 | **Short last sentence is extended.** When the text after the last terminator is under 10 bytes, the previous sentence is prepended, so "Yes." + "I" is never a caption of its own. | `SyncLoop` (`lastEOSIndex` back-off) | not done |
| 1.3 | **Newlines are boundaries for CJK.** Live Captions breaks Japanese with `\n` instead of `。`. `ReplaceNewlines` turns a line break after >= `MEDIUM_THRESHOLD = 40` bytes into `。` (CJK) or `. `, else into `——` / `—`. | `TextUtil.ReplaceNewlines` | `sentences()` replaces `\n` with a space: Japanese stays one ever-growing sentence (probe: three lines -> one incomplete sentence). |
| 1.4 | **Acronym and punctuation spacing normalised** before splitting: `U. S.` -> `US` (`Acronym`, `AcronymWithWords`), spaces around `.!?,` collapsed, none around `。！？，、`. | `RegexPatterns`, `SyncLoop` preprocess | abbreviations are protected in `split_sentences`, but spaced acronyms and stray spaces reach the translator and the dedupe keys. |
| 1.5 | **Display shortening keeps the tail.** `ShortenDisplaySentence` drops leading clauses at `PUNC_EOS`/`PUNC_COMMA` until the text is under `VERYLONG_THRESHOLD = 220` bytes, so the newest words stay visible. | `TextUtil.ShortenDisplaySentence` | `render.Renderer._wrap` cuts the **end** with `…` (right for lyrics, wrong for a growing caption: the newest words disappear). |
| 1.6 | **Visual "choke".** After a complete sentence's translation is displayed, the display loop holds it 720 ms before taking the next update, so a finished line does not flash past. | `DisplayLoop` | reading-time logic covers most of this (`live/text.py`); no explicit hold after a final translation. |
| 1.7 | **N previous sentences on the overlay** (`DisplaySentences`, `Caption.OverlayOriginalCaption` walks back N terminators). | `SyncLoop`, `Setting.DisplaySentences` | one caption (plus preview line). |
| 1.8 | **Opening Live Captions' own settings** (`ClickSettingsButton` invokes `SettingsButton`) so the user can add a language or "Include microphone audio". | `LiveCaptionsHandler.ClickSettingsButton` | the Live page explains, but has no button. |
| 1.9 | *(RealtimeSTT)* **Punctuation needs confirmation.** `punctuation_min_confirmations = 4` with stable right context; characters need 2 observations over >= 0.6 s; an observation < 0.35 similar to consensus is an outlier (max 1 gap). | `realtime_text_stabilizer.py` (`_punctuation_is_stable`, `_outlier_decision`) | a terminator is final on first sight. Probe: Live Captions writing "I think we." then "I think we should go now." gives a final caption and a second translation request (CaptionStream merges them on screen, the first request is wasted). |
| 1.10 | *(LunaTranslator, idea)* reads only the **last line** of the box at a configurable refresh interval. | `mssr.py` `__dointernal` | Dualyrics' sentence model is better; no change needed. |

## 2. Audio capture and segmentation (`live/capture.py`, `live/procloop.py`, `live/vad.py`, `live/silero.py`)

Dualyrics ports LiveTranslate's `VADProcessor` and adds process loopback, a stateful resampler and
generation numbers; it is ahead of all references on per-process capture.

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 2.1 | **Follows the default output device.** Every `DEVICE_CHECK_INTERVAL = 2.0` s a fresh PortAudio instance reads the default output; a change restarts the stream (headphones plugged in). | LiveTranslate `audio_capture.py` capture loop, `_query_current_default` | `SystemCapture` reads the default device only when Live starts; the old device keeps delivering silence, so captions just stop. (Process loopback modes are device independent and unaffected.) |
| 2.2 | **Microphone mix-in** (mic stream resampled and mixed into the loopback for ASR, separate restart event). | `audio_capture.py` `_open_mic_stream` | not offered for the Whisper engine (Live Captions has its own mic switch). |
| 2.3 | **Pre-roll chosen at real silence.** `select_preroll_frames`: walk back to >= 200 ms of stable silence, keep a 160 ms guard, include at least 600 ms, bridge gaps <= 80 ms, noise floor x 2.5. | RealtimeSTT `core/preroll.py` | fixed 3 windows (~96 ms). Whisper then often drops a soft first syllable. |
| 2.4 | **Long segment with almost no text is noise**: a piece >= 2.0 s whose text has <= 3 alphanumerics is dropped. | LiveTranslate `main._process_segment` | not in `hallucination_reason` (`live/text.py`). |
| 2.5 | **Language filter.** With a fixed ASR language, a result in another language is discarded. | `_process_segment` | Whisper is forced to the language, so not needed; but in `auto` the per-piece `language_probability` is not used at all (music detected as Welsh/Nynorsk then translated). |

## 3. Speech recognition, interim commits (`live/stt.py`, `live/interim.py`, `live/engine.py`)

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 3.1 | **LocalAgreement-2**: a word is committed only when the last two hypotheses agree on it (longest common prefix), with 1-5-gram removal of words that repeat the committed tail. | whisper_streaming `HypothesisBuffer.insert/flush` | interim commits every complete sentence but the last from **one** pass; a pass that mishears is committed for good. |
| 3.2 | **Trim at word timestamps.** The buffer is cut at the end time of the last committed segment/sentence (`chunk_completed_segment`, `chunk_at`), buffer trimming `("segment", 15 s)`. | `OnlineASRProcessor` | `interim.trim_seconds` cuts by the committed share of **characters** x duration + 0.3 s margin: can cut into the next word or repeat the last one (the echo stripper hides most of it). |
| 3.3 | **Prompt = 200-char suffix of committed text that has scrolled out of the audio buffer.** | `OnlineASRProcessor.prompt` | prompt = app name + previous caption, last 200 chars (similar; fine). |
| 3.4 | **Final pass on the tail only.** The final transcription uses the last 3 s and is merged into the live text through a 3-4-word anchor (fuzzy distance 1). | RealtimeSTT `tail_transcription.py` (`find_tail_anchor`, `merge_live_and_tail_transcription`) | the final pass transcribes what interim passes left; equivalent in effect. |
| 3.5 | **ASR in a worker process** with a transcribe timeout, automatic restart (`_recover_asr_worker`, bounded attempts), recycling when the worker's RSS grows past its post-load baseline (`_maybe_recycle_asr_worker`), and a generation guard so a late worker never replaces a newer one. | LiveTranslate `main.py`, `asr_worker.py` | faster-whisper runs in the app process: a CUDA/cuDNN crash or a native leak takes the whole app (overlay, lyrics) with it; a hung `transcribe` blocks Live forever. |
| 3.6 | **Beam size** 5 measured faster and better than 1 on GPU (whisper_streaming comment in `FasterWhisperASR.transcribe`). | `whisper_online.py` | beam 3 on GPU, 1 on CPU (reasonable for CPU). |

## 4. Live translation (`live/livetranslate.py`, `translate.py` `Gemini._call/ask`)

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 4.1 | **Thinking switched off for live lines**, per provider (`thinking_disable_body`: `thinking.type=disabled`, `enable_thinking=false`, `reasoning_effort=none`), plus a warning when an empty answer used completion tokens (`_warn_if_thinking_burned`). | LiveTranslate `translator.py` | `generationConfig` is only `temperature: 0.4` (+ JSON mime). The default models (`gemini-flash-latest`, `gemini-2.5-flash`) think by default: seconds of latency and thought tokens on every caption (`usage.py` already counts `thoughtsTokenCount`). |
| 4.2 | **Short, hard deadlines.** LiveCaptions-Translator: 8 s HTTP timeout. LiveTranslate: 10 s total deadline even while streaming. Mimi: `GEMINI_FINISH_TIMEOUT_MS = 4500`. | `TranslateAPI.client`, `Translator._translate_streaming`, `translation_policy.rs` | live lines use `net.TIMEOUT_FAST = (6, 15)`: a caption can wait 15 s for Gemini before the free tier is tried. |
| 4.3 | **`max_tokens = 256`** and a repetition-loop check on the output (`_check_repetition`: text >= 40 chars that repeats a prefix of >= 8 chars). | `translator.py` | no `maxOutputTokens`; no loop check on the LLM answer. |
| 4.4 | **Fallback request shapes**: on HTTP 400/422 the next body shape is tried (`LLMRequestDataFactory`, `openai_fallback_index`). | `TranslateAPI.OpenAI` | a 400 cools the model x key for 120 s. Needed once `thinkingConfig` is sent (models that reject it must not be cooled down). |
| 4.5 | **Content blocks are not key failures** (implicit: LCT returns an `[ERROR]` string; nothing is put to rest). | `TranslateAPI` | **Bug-class gap, verified**: a candidate with `finishReason: SAFETY` (no parts) makes `json.loads("")` raise, `ask` cools that pair 20 s ("bad JSON") and moves on; every pair answers the same, so **one violent game line rests every key x model for 20 s** and raises `KeysResting` (section 12). A `promptFeedback` block (no candidates) cools pairs 30 s. No `safetySettings` are sent. |
| 4.6 | **Prompt rules measured on real data**: context as labelled pairs (`Japanese: …` / `Persian: …`), a short heading, the instruction last right before the text; plain-text answers (no JSON) are faster. | OverTranslate `OpenAiPrompt.cs` remarks | similar order; JSON wrapper kept (needed for the pivot answer). Fine. |
| 4.7 | **"Auto-correct likely ASR errors"** in the system prompt; presets per content type (daily, esports, anime). | LiveTranslate `DEFAULT_PROMPT`, `PROMPT_PRESETS` | prompt says the line may be a fragment, but not that it may be misheard. |
| 4.8 | **Streaming display** of the translation as it arrives (character by character). | LiveTranslate `translate_iter` | whole answer only. |
| 4.9 | *(LunaTranslator, idea)* **User glossary** (`gpt_dict`: source term, translation, note) injected into the LLM prompt. | `basetranslator.py` (`using_gpt_dict`, `_gpt_common_parse_context`) | names stay consistent only through the last N context lines; no glossary, no Whisper `hotwords`. |

## 5. Retry, pacing and the key pool (`keypool.py`, `translate.py`)

Dualyrics' key pool (round robin, per key x model state, daily reset at Pacific midnight, shared-project
evidence) is more complete than anything in the references. Gaps are around it:

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 5.1 | **Failed lines are retried while they matter.** OverTranslate: a failed translation is asked again after 1 -> 2 -> 4 s (cap 4 s), reset on success; only the retry waits, a new picture is read at once. Mimi: 3 attempts inside one 45 s deadline, 429 waits 4 s then 8 s, transient errors 600 ms doubling to 8 s, permanent errors never. | `RealtimeRetryBackoff`, `translation_policy.rs` (`retry_delay_ms`, `retry_with_remaining`) | a Live or on-screen line whose translation failed stays `failed` for as long as it is on screen (`screen/engine.py` `_translate`, `live/engine.py` `_translate`); only whole songs are retried. |
| 5.2 | **Bounded queue**: at most 3 waiting final translations (Mimi `MAX_FINAL_QUEUE_DEPTH`), at most 4 in flight (Translumo `MAX_TRANSLATE_TASK_POOL`). | as above | Live cancels queued older versions; the screen source has no stale cancel (2 workers, every new line submitted). |
| 5.3 | **In-flight coalescing**: a line already on its way is not sent again; later passes wait for the same answer, keyed with a generation so a pause or language change starts afresh. | OverTranslate `RealtimeInFlightTranslations` | memo/linecache cover finished lines, not lines in flight. |
| 5.4 | **Pacing** *(LunaTranslator idea, `intervaledtranslate`)*: a minimum interval between requests per translator. | `basetranslator.py` | no proactive requests-per-minute pacing per key x model; the pool learns limits from 429s (each costs a round trip and 60-90 s of rest). |
| 5.5 | **Latest-request guard with two locks**: cancel the superseded request *and* number it, so a late answer is never drawn. | OverTranslate `LatestRequest` | done (caption id + version). |

## 6. On-screen subtitles (`screen/logic.py`, `screen/ocr.py`, `screen/capture.py`, `screen/engine.py`, `screen/dedupe.py`)

Translumo's similarity cache is already ported (`screen/dedupe.py`). The rest of its loop and OverTranslate's
measured change detection are not:

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 6.1 | **Two OCR passes, majority vote.** Plain Windows OCR plus Windows OCR on a binarised frame (gray, threshold 150, morphological open); `GetBestDetectionResult` takes a text read the same by >= 3 engines, else the best validity score; results <= `MIN_SCORE_THRESHOLD = 2.1` are dropped. | `TranslationProcessingService.TranslateInternal`, `WinOCREngineWithPreprocess.PreProcess` | one pass on the upscaled colour image (`ocr.prepare`). Subtitles over busy or bright backgrounds are read worse. |
| 6.2 | **Typewriter text detection.** The previous and current gray frames are united (`UnionImages`); if the union reads as cached but the current text is > 3 letters longer (`SEQUENTIAL_DIFF_LETTERS`), the text is still being typed: wait 620 ms before the next pass. | `GetSecondaryCheckText`, `GetIterationDelayMs` | the 2-sample debounce plus "most complete reading" handles growth; visual-novel text that types slowly is translated several times as it grows (each growth is an `update`). |
| 6.3 | **Adaptive cadence**: 115 ms after a cheap pass, 320 ms after a full pass, 620 ms during typed text. | `GetIterationDelayMs` | fixed `ocr_fps` (3.5/s). |
| 6.4 | **Colour-agnostic change fingerprint**: 32 x 8 cells (16 rows for a full area) of average brightness, sampled every 2nd pixel; a cell changes only beyond 16 levels, the frame only when >= 5 % of cells changed. Measured: tolerance 12 counted a +16 brightness drift as a change in 85 % of cells; 16 removes the drift and still catches every subtitle change. | OverTranslate `FrameFingerprint` | bright-pixel mask (`BRIGHT = 185`, 160 x 40, 0.6 %). Green, blue, red or dark subtitles never cross 185, so they are read only by the 1.5 s safety interval; a bright scene behind white text triggers OCR for nothing. |
| 6.5 | **Collapsed detection**: one box spanning the whole region that reads 1-2 characters ("A", "M") is a detector failure, not a subtitle. | OverTranslate `CollapsedDetection` | `garbage()` and the letter count drop most of these. Fine. |
| 6.6 | **Per-line re-reading rule**: a re-reading replaces a line on screen only when its own score improves by >= 0.02, or it contains the shown line and continues it. | OverTranslate `RealtimeReadingMerge` | whole-text debounce (similarity 0.82) and "most complete reading": comparable for one-line subtitles. |
| 6.7 | **OCR language install**: `Add-WindowsCapability Language.OCR~~~<tag>~0.0.1.0` from an elevated PowerShell. | Translumo `WindowsOCRHelper.InstallOcrLanguageCapability` | lists installed OCR languages only. |
| 6.8 | **GPU capture**: DXGI Desktop Duplication (`TryAcquireNextFrame(100)`, re-init on access lost); OverTranslate uses Windows.Graphics.Capture with a readback throttle that keeps the newest held frame instead of dropping it (`WgcFrameThrottle`) and asks a still window to repaint after a resize (`CaptureMiss`, 300 ms / 600 ms, at most 4 redraws and 2 restarts). | `ScreenDXCapturer`, `Capture/*` | GDI `BitBlt` of the screen DC: fine for windowed and borderless apps, black for exclusive-fullscreen games; no black-frame notice. |
| 6.9 | *(LunaTranslator, idea)* **Stable-then-read**: OCR only when the frame is stable against the previous frame (MSSIM > 0.5) and differs from the last *recognised* frame (MSSIM < 0.95); a text within edit distance 3 of the last one is the same. | `ocrtext.py` `getresauto`, `waitforstable` | change detector compares with the previous frame only. |
| 6.10 | Failed translations retried with back-off (see 5.1). | | not done |

## 7. Dubbing (`dub/*`)

Dualyrics' scheduler, engine chain, clip cache, ducking journal and self-voice filter have no equal in the
references. Gaps:

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 7.1 | **Make-before-break session rotation** on `goAway`: the new Gemini Live session is set up (timeout 10 s) while the old one drains (1.5 s), up to 2 s of PCM is buffered meanwhile, the closing tail of the old session survives. | Mimi `gemini_live_client.rs` (`ROTATION_SETUP_TIMEOUT`, `ROTATION_DRAIN_TIMEOUT`, `ROTATION_PENDING_PCM_BYTES`) | break-before-make: `feed` drops audio while `connected` is false, so the words spoken during the reconnect are never dubbed; no `sessionResumption` handle and no `contextWindowCompression`, so each session starts without context and audio sessions hit the server's length limit. |
| 7.2 | **Transcript assembly with quiet checkpoints** (2000 ms quiet ends a block, 500 ms turn tail). | `openai_transcript_committer.rs` | output transcription is passed on as it comes. |
| 7.3 | **Silence is not streamed** (start-up audio limit 2 s; recognition streams end at a boundary). | `translation_policy.rs` `STARTUP_AUDIO_LIMIT_MS` | every 100 ms chunk is sent, silence included (audio tokens and quota). Gemini Live accepts `audioStreamEnd` when the stream pauses. |
| 7.4 | *(LunaTranslator, idea)* TTS cache keyed by text, voice **and** speech parameters. | `basettsclass.py` `ttscachekey` | `CLIP_CACHE = 60` exists; make sure the boost factor is part of the key (it is speed dependent). |
| 7.5 | Volume ramps (not in the references; common practice). | | `Ducker.duck` sets the target volume in one step; a 120-200 ms ramp sounds less abrupt. |

## 8. Overlay and rendering (`overlay.py`, `render.py`)

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 8.1 | **Tail-keeping truncation** for live text (see 1.5). | LCT `ShortenDisplaySentence` | `_wrap` keeps the head. |
| 8.2 | **Steady font size**: line heights are filed into sizes and each line is drawn at the median of its size class, so a re-read never changes the size; short lines never found a size. | OverTranslate `RealtimeTextSizes` | fixed template sizes (no in-place OCR overlay), so not needed. |
| 8.3 | *(BetterLyrics, idea)* **Instrumental gaps**: `LyricsGapHelper.FindGaps` merges overlapping line intervals and returns gaps >= a minimum, before the first line and between lines (not after the last), which drive a countdown/dots animation. | `LyricsGapHelper.cs` | a `♪` placeholder; no countdown to the next line. |
| 8.4 | *(BetterLyrics, idea)* eased value transitions (`ValueTransition`, easing types) for scroll and scale. | `ValueTransition.cs` | fade/slide motion already exists; fine. |

## 9. Smart Auto, sources, lyrics and the media clock (`automode.py`, `sources.py`, `lyrics.py`, `media.py`)

| # | Reference behaviour | Where | Dualyrics today |
|---|---|---|---|
| 9.1 | *(idea)* **Metadata score for search results**: title 0.30, artist 0.30, album 0.10, duration 0.30 (Jaro-Winkler for strings; duration 100 % within 1 s, 0 % beyond 10 s, linear between); artists compared sorted. | BetterLyrics `MetadataComparer.CalculateScore` | `LrcLib._score` rates synced/plain, instrumental and duration only; the title and artist of a search hit are not compared (outside the strict browser match), so a cover or remix of the same length can win. |
| 9.2 | *(idea)* **Debounced media-property changes**: GSMTC sends title, artist, duration in separate events; a debouncer waits before acting. | `GsmtcServiceBase` (`_onMediaPropsChangedDebouncer`) | a new `title|artist` key starts a lookup at once; duration may still be 0 (strict browser match then fails, cached as not found). |
| 9.3 | *(idea)* **Per-player position offset**, reset on song change when the player needs it. | `GsmtcServiceBase.UpdateCurrentMediaSourceProviderInfoPositionOffset` | global + per-song offsets only. |
| 9.4 | Playback rate (GSMTC `PlaybackRate`). | (WinRT `GlobalSystemMediaTransportControlsSessionPlaybackInfo.PlaybackRate`) | `PlaybackClock` assumes 1.0x; a 1.25x video drifts until the next report re-anchors it. |
| 9.5 | *(idea)* **Latest-only task runner**: rapid track skips run only the newest lookup. | `LatestOnlyTaskRunner.cs` | lookups are keyed by track; fine. |
| 9.6 | *(LunaTranslator, idea)* extra text sources: clipboard, game text hooks. | `textsource/copyboard.py`, `texthook.py` | screen/UIA/audio only (hooks are out of scope; clipboard is cheap). |

## 10. Where Dualyrics is already ahead

Process loopback with self-exclusion; Live Captions run hidden and fully restored; automatic engine choice
and fallback; English pivot; Silero + adaptive pauses with generation numbers; offline Argos translator via
CTranslate2; key pool with daily-reset persistence and shared-project evidence; persistent line cache with
tier upgrade; dubbing schedule with look-ahead clips and self-voice filtering; resumable, hash-checked installs.
None of the references has more than two of these.

---

## 11. Prioritised gap list

Impact 1-5 (5 = users see it every session or it wastes quota); effort 1-5 (1 = an hour, 5 = days);
**priority = impact x (6 - effort)**.

| Rank | Gap | Module | From | Impact | Effort | Priority |
|---|---|---|---|---|---|---|
| 1 | Gemini content blocks rest every key x model; no `safetySettings` (4.5) | `translate.py` | LCT, LiveTranslate behaviour | 5 | 1 | **25** |
| 2 | Live-line generation config: thinking off, `maxOutputTokens`, 5 s deadline, 400 fallback shape (4.1-4.4) | `translate.py`, `live/livetranslate.py` | LiveTranslate, LCT, Mimi | 5 | 2 | **20** |
| 3 | Live Captions partial emits: minimum length, minimum interval, cheap tier for partials (1.1) | `live/wincaptions.py`, `live/engine.py` | LCT | 4 | 2 | **16** |
| 4 | Retry failed Live / screen line translations with back-off (5.1, 6.10) | `live/engine.py`, `screen/engine.py` | OverTranslate, Mimi | 4 | 2 | **16** |
| 5 | Tail-keeping truncation for live and screen captions (1.5, 8.1) | `render.py` | LCT | 3 | 1 | **15** |
| 6 | Live Captions text normalisation: CJK newlines, short last sentence, acronyms, terminator confirmation (1.2-1.4, 1.9) | `live/wincaptions.py` | LCT, RealtimeSTT | 3 | 2 | **12** |
| 7 | Proactive requests-per-minute pacing per key x model (5.4) | `keypool.py` | LunaTranslator *(idea)*, Mimi | 3 | 2 | **12** |
| 8 | User glossary in prompts and Whisper `hotwords` (4.9) | `live/livetranslate.py`, `translate.py`, `profiles.py`, `live/stt.py` | LunaTranslator *(idea)* | 3 | 2 | **12** |
| 9 | Lyrics search hits scored by title/artist similarity (9.1) | `lyrics.py` | BetterLyrics *(idea)* | 2 | 1 | **10** |
| 10 | Noise filters: long piece with <= 3 alphanumerics; low language probability in auto (2.4, 2.5) | `live/text.py`, `live/stt.py` | LiveTranslate | 2 | 1 | **10** |
| 11 | Settle 0.4 s on media-property changes; playback rate in the clock (9.2, 9.4) | `media.py` | BetterLyrics *(idea)* | 2 | 1 | 10 |
| 12 | OCR: binarised second pass with vote; colour-agnostic fingerprint; adaptive cadence (6.1, 6.3, 6.4) | `screen/ocr.py`, `screen/logic.py`, `screen/engine.py` | Translumo, OverTranslate | 3 | 3 | 9 |
| 13 | Interim commits: word timestamps for the trim, LocalAgreement-2 (3.1, 3.2) | `live/interim.py`, `live/stt.py`, `live/engine.py` | whisper_streaming | 3 | 3 | 9 |
| 14 | Gemini Live: resumption handle, context compression, make-before-break with 2 s buffer, `audioStreamEnd` on silence (7.1, 7.3) | `dub/gemini_live.py` | Mimi | 3 | 3 | 9 |
| 15 | Silence-anchored pre-roll (2.3) | `live/vad.py` | RealtimeSTT | 2 | 2 | 8 |
| 16 | Screen source: stale cancel and in-flight coalescing (5.2, 5.3) | `screen/engine.py` | OverTranslate, Translumo | 2 | 2 | 8 |
| 17 | Install a Windows OCR language from Settings (6.7) | `ui/sources_page.py`, `screen/ocr.py` | Translumo | 2 | 2 | 8 |
| 18 | Previous N sentences on the overlay; Live Captions settings button (1.7, 1.8) | `overlay.py`, `ui/` | LCT | 2 | 2 | 8 |
| 19 | Whisper in a worker process with timeout, restart and RSS recycling (3.5) | `live/stt.py`, `live/engine.py` | LiveTranslate | 3 | 4 | 6 |
| 20 | Microphone mix-in (2.2) | `live/capture.py` | LiveTranslate | 2 | 3 | 6 |
| 21 | Typewriter detection for visual novels (6.2) | `screen/engine.py` | Translumo | 2 | 3 | 6 |
| 22 | System loopback follows the default device (2.1) | `live/capture.py` | LiveTranslate | 1 | 1 | 5 |
| 23 | Ducking ramps (7.5) | `dub/ducking.py` | - | 1 | 1 | 5 |
| 24 | Streaming translation display (4.8) | `translate.py`, `live/engine.py` | LiveTranslate | 2 | 4 | 4 |
| 25 | GPU capture (DXGI/WGC) for exclusive fullscreen (6.8) | `screen/capture.py` | Translumo, OverTranslate | 2 | 5 | 2 |

## Top 10: implementation notes

**1. Content blocks must not rest keys** (`translate.py`). In `Gemini._call`, read
`j.get("promptFeedback", {}).get("blockReason")` and `cands[0].get("finishReason")`; for `SAFETY`,
`PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`, `RECITATION` (or an empty text with one of them) return a new
status, e.g. `(None, 200, "blocked: SAFETY", "")` with a `ContentBlocked(TranslationError)` raised by `ask`
**without** `pool.cooldown` and without trying further pairs (they block the same text). Add to `body`:
`"safetySettings": [{"category": c, "threshold": "BLOCK_NONE"} for c in ("HARM_CATEGORY_HARASSMENT",
"HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]`.
`fallback_chain` then answers that one line from the free/offline tier. Keep `json.JSONDecodeError` -> 20 s
cooldown only for a non-empty unparsable answer. Tests: a fake session returning a SAFETY candidate must leave
every slot `ok` and raise `ContentBlocked` after one request.

**2. Live-line generation config** (`translate.py`, `live/livetranslate.py`). Give `Gemini.ask/_call` a
`live: bool` (or a `gen` dict) used by `LiveTranslator.translate` and `to_english`: `maxOutputTokens: 256`,
`thinkingConfig: {"thinkingBudget": 0}` for `gemini-2.5-flash*`/`-lite*` and the `*-latest` aliases,
`{"thinkingLevel": "low"}` for `gemini-3*` (a model that rejects the field answers 400 mentioning "thinking":
retry once without it and remember per model, like LCT's `openai_fallback_index`, instead of the 120 s
cooldown). Whole-song translation keeps thinking. Use a live timeout of `(4, 6)` and a total deadline of
~5 s (Mimi's 4.5 s) before the free tier. Add LiveTranslate's loop check (output >= 40 chars repeating a
prefix of >= 8 chars -> treat as a bad answer). Expect the median live latency to fall from seconds to well
under one second, and thought tokens to disappear from the usage meter.

**3. Fewer Live Captions partial requests** (`live/wincaptions.py` `SentenceTracker.feed`,
`live/engine.py` `_on_caption`). Count a change toward `MAX_CHANGES` only when the unfinished sentence has
>= 10 UTF-8 bytes (`SHORT_BYTES = 10`, LCT's `SHORT_THRESHOLD`), and emit a partial at most every 1.5 s per
sentence key (`MIN_PARTIAL_GAP`). In `_on_caption`, translate a partial (`e.done is False`) with the free or
offline tier when Gemini's pacing says it is busy, and always send the final (`done`) to Gemini; the final
replaces the partial in place (same key). The probe in section 12 (23 words, 7 emits) is the regression test:
expect <= 3 emits.

**4. Retry failed line translations** (`live/engine.py`, `screen/engine.py`). Keep a per-caption
`attempts` and `retry_at`. On `failed`: if the caption is still the one on screen (Live: newest caption, not
past its reading time; screen: `self._cap is cap and cap.gone_at is None`) schedule a retry after
`min(4, 1 * 2**(attempts-1))` s (OverTranslate), or 4 s then 8 s after `KeysResting`/429 (Mimi), at most 3
attempts within 45 s. Drive it from the existing loops (`step` for screen, the queue-timeout branch of
`_listen` / the poll loop for Live). Reset on success. Tests with a fake translator that fails twice.

**5. Tail-keeping truncation** (`render.py` `Renderer._wrap`, `block`). Add `keep: str = "head"` and pass
`"tail"` for the Live and Screen caption roles (lyrics keep `"head"`). With `"tail"`, drop whole wrapped lines
from the start and prefix `…` to the first kept line, preferring to cut after a clause mark (`PUNC_EOS` and
`, ، 、 —`) like LCT's `ShortenDisplaySentence`. RTL: the ellipsis goes at the logical start. Tests: a
long caption with `max_lines=2` shows its last words.

**6. Live Captions text normalisation** (`live/wincaptions.py` `sentences`). Before splitting:
(a) for a CJK caption language, a newline after >= 40 bytes becomes `。`, a shorter one is joined without a
space (LCT `ReplaceNewlines`); other scripts keep today's space; (b) collapse `([A-Z])\s*\.\s*([A-Z])` acronyms;
(c) collapse spaces before `.,!?` and around `。！？，、`. After splitting: when the newest sentence is
< 10 bytes and there is a previous one, emit them together. Confirmation: treat a sentence as `done` only after
its terminator has been seen on 4 consecutive polls (100 ms, RealtimeSTT's `punctuation_min_confirmations`);
if the terminator disappears, the key stays the same and nothing is sent. Tests: the Japanese and "I think we."
probes of section 12.

**7. Requests-per-minute pacing** (`keypool.py`, `usage.py`). Add a `DEFAULT_RPM` table next to
`DEFAULT_LIMITS` (editable like the daily limits) and a 60 s sliding window of request times per
fingerprint x model (memory only). `KeyPool.order` skips a pair whose window is full (treat it as resting
until the oldest request leaves the window) and also a pair whose day count already reached its daily limit.
Effect: the round robin spreads bursts instead of collecting 429s, and a full pool goes straight to the next
tier without a request. Tests with a fake clock.

**8. Glossary** (`settings.py`, `profiles.py`, `live/livetranslate.py`, `translate.py`, `live/stt.py`).
A global and per-profile list of `source => target (note)` entries (Settings > Translation, and the profile
editor: game names, places). `build_live_prompt` and `build_prompt` add "Always translate these terms as
given:" with the entries whose source occurs in the text (case-insensitive, max ~30). Whisper gets the source
terms through `hotwords=` (faster-whisper) and the existing `initial_prompt`. The line cache key must include
a glossary hash so an edited glossary is not hidden by old lines. This is an idea from GPL code: write it from
scratch.

**9. Lyrics search scoring** (`lyrics.py` `LrcLib._score`). Add title and artist similarity of the hit to
the cleaned query (reuse `screen/dedupe.similarity` or a Jaro-Winkler written here), weighted like
BetterLyrics' idea (title 0.3, artist 0.3, duration 0.3) on top of the synced/instrumental terms; artists
compared as sorted sets. Reject hits with title similarity < 0.5 even when the duration matches. Tests: a
cover with the same length and a different artist loses to the original.

**10. Noise filters** (`live/text.py` `hallucination_reason`, `live/stt.py`). Add `"noise"` when the piece
lasted >= 2.0 s and its text has <= 3 alphanumerics (LiveTranslate `_process_segment`). Return
`info.language_probability` in `Recognition` and, with the source language on auto, drop a recognition whose
probability is < 0.5 when its language differs from the last 3 accepted ones, or re-run it once with the most
recent language (prefer the re-run when Whisper is idle). Tests with fake recognitions.

---

## 12. Probes run for this page (`.venv`, Dualyrics `794000e`)

| Probe | Result |
|---|---|
| `SentenceTracker`: "Okay." -> "Okay. I think we." -> ... -> "Okay. I think we should go now." | `Emit('I think we.', done=True, key=1)` then `Emit('I think we should go now.', done=True, key=2)`: two finals, two translations. `CaptionStream.push` merges them on screen (`update`), so only the request is wasted. |
| Four quick changes "Hi. I" ... "Hi. I am going" (25 ms apart) | `Emit('I am go', done=False)` after 75 ms: a cut word sent for translation. |
| 23-word sentence without punctuation, one word per 0.3 s | 7 partial emits (7 Gemini requests before the sentence ends). |
| `sentences('今日は天気がいいですね\n明日も晴れるでしょう\nでは')` | one incomplete sentence: Japanese never splits at Live Captions' line breaks. |
| `Gemini.ask` with every answer `{"candidates":[{"finishReason":"SAFETY"}]}`, 2 keys x 2 models | `KeysResting`; all four pairs `cooling … 'bad JSON'` for 20 s after one blocked line. |
