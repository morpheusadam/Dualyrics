# Dualyrics and AI assistants

Since v2.1, an AI assistant such as Claude can read and set up Dualyrics for you: what is playing and what is on screen, settings, templates, per-app profiles, the target language, timing, dubbing and your Gemini usage. You ask in plain words ("translate to Turkish", "the lyrics are half a second early") and the assistant calls Dualyrics' tools.

Two pieces make this work:

- **The control API**, inside the running app. It listens on `127.0.0.1` only, on a random free port, and every request needs a bearer token. The port and the token are in `%APPDATA%\Dualyrics\api.json`. It is on by default and can be switched off in **Settings › AI & API**.
- **The MCP server** (Model Context Protocol, stdio): `Dualyrics.exe --mcp`, or `python -m dualyrics.mcp` from source. Your assistant starts it by itself. It reads `api.json` and sends each tool call to the running app, so changes apply at once. When Dualyrics is not running, it changes `settings.json`, the templates and the profiles directly, and they apply at the next start.

Contents: [Set up Claude Desktop](#claude-desktop) · [Claude Code](#claude-code) · [Other assistants](#other-mcp-clients) · [Examples](#examples) · [Tools](#tools) · [HTTP API](#http-api-for-scripts) · [Security](#security-and-privacy) · [Troubleshooting](#troubleshooting)

## Setup

**Settings › AI & API** shows the exact text for your PC, with **Copy** buttons. The paths below are for the installed app; a portable copy uses the path of its own `Dualyrics.exe`.

### Claude Desktop

1. In Claude Desktop open **Settings › Developer › Edit Config**. That opens `%APPDATA%\Claude\claude_desktop_config.json`.
2. Add Dualyrics under `mcpServers`, next to any servers you already have:

   ```json
   {
     "mcpServers": {
       "dualyrics": {
         "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\Dualyrics\\Dualyrics.exe",
         "args": ["--mcp"]
       }
     }
   }
   ```

3. Save the file and restart Claude Desktop. The Dualyrics tools are listed under the tools button of the message box.

### Claude Code

Run once in a terminal:

```powershell
claude mcp add dualyrics --scope user -- "C:\Users\<you>\AppData\Local\Programs\Dualyrics\Dualyrics.exe" --mcp
```

`--scope user` makes it available in every project. Check it with `claude mcp list`, or with `/mcp` inside Claude Code.

### Other MCP clients

Any client that supports stdio servers works (Cursor, VS Code, Windsurf, Zed, Continue, LM Studio and others). The server is the command `Dualyrics.exe` with the argument `--mcp`; it needs no environment variables and no API key.

### Running from source

Use the console Python of your virtual environment and point `PYTHONPATH` at the repository:

```json
{
  "mcpServers": {
    "dualyrics": {
      "command": "C:\\path\\to\\Dualyrics\\.venv\\Scripts\\python.exe",
      "args": ["-m", "dualyrics.mcp"],
      "env": { "PYTHONPATH": "C:\\path\\to\\Dualyrics" }
    }
  }
}
```

## Examples

What you say, and what the assistant does with the tools:

**"Set the Game HUD template for God of War."**

1. `list_profiles`: no profile for God of War yet.
2. `list_running_apps` while the game is open: it finds `{"name": "God of War Ragnarök", "exe": "gowr.exe"}`.
3. `upsert_profile` with `{"profile": {"name": "God of War Ragnarök", "exe": "GoWR.exe"}, "preset": "games"}`: a profile with Live captions, click-through and top placement.
4. `apply_template` with `{"id": "Game HUD", "profile": "gowr.exe"}`: the Game HUD look for that game only; everything else keeps its look.

**"Translate to Turkish."**

`set_target_language` with `{"language": "Turkish"}`. Names, native names (`Türkçe`) and codes (`tr`) all work. It applies everywhere, and the app in front stops overriding the language, as when you pick a language in the overlay menu. To change one app only: `{"language": "tr", "profile": "spotify"}`.

**"Show my Gemini usage."**

`get_usage` with `{"period": "today"}` (or `week`, `month`): requests, tokens, audio, rate limits and cache hits, per model, per key and per song or video, today's share of each free-tier daily limit and when the quota resets. Keys are named by their position in Settings (#1, #2); the keys themselves are never shown.

More:

| You say | Tool and arguments |
|---|---|
| "What's playing, and what does the line on screen mean?" | `get_status` |
| "The lyrics are half a second early." | `sync_offset` `{"delta_ms": 500}` (positive = lines later; scope `song` by default, `global` for all songs) |
| "Make the translation bigger and yellow." | `create_or_update_template` `{"based_on": "liquid_glass", "name": "Big yellow", "template": {"lines": {"translation": {"color": "#FFD54F", "font": {"size": 34}}}}}`, then `apply_template` |
| "Only show the translation." | `update_settings` `{"changes": {"translation_only": true}}` |
| "Never show anything for Chrome." | `set_mode` `{"mode": "off", "profile": "browsers"}` |
| "That line should say 'I'm still here'." | `edit_line_translation` `{"text": "I'm still here"}` (the line on screen) |
| "Translate this song again." | `retranslate` |
| "Read the translations aloud." | `toggle_dubbing` `{"enabled": true}` |
| "Hide the subtitles." / "Bring them back." | `hide` / `show` |

## Tools

Every tool takes a JSON object. Invalid arguments and refused changes come back as a tool error with the reason; nothing is half-applied.

| Tool | Arguments | What it does |
|---|---|---|
| `get_status` | — | App running or not, mode, the source in use (`lyrics`, `screen`, `audio`, `none`) and why, the active app profile, what is playing (title, artist, player, position), the lyrics state, the lines on screen with their translation, effective language and template, offsets, dubbing |
| `get_settings` | `keys` (optional list) | All settings, the allowed values of the word settings (`choices`) and the range of every number (`ranges`). `gemini_keys` is replaced by `gemini_keys_count`; a proxy password is masked |
| `update_settings` | `changes` (object), `dry_run` | A validated patch. Numbers out of range are clamped and reported in `adjusted`; an unknown key or an invalid value changes nothing. Protected: `gemini_keys`, `api_enabled`, `version`; `profiles` has its own tools. Language settings accept names |
| `list_templates` | — | Built-in and user templates, which one is in use globally and which profiles use which |
| `get_template` | `id` | One template as JSON (schema `dualyrics.template/1`, [DESIGN.md part 2](DESIGN.md#2-subtitle-template-model)). Ids and names both work (`game_hud`, `Game HUD`) |
| `apply_template` | `id`, `scope` (`auto`, `global`, `profile`), `profile`, `apply_hints` | Makes a template the look in use. `auto` does what the settings window does: the active app's profile if it has its own template, else everything. `apply_hints` also applies the template's suggested placement, click-through and lock |
| `create_or_update_template` | `id`, `template` (whole or partial), `based_on`, `name`, `description` | Creates a user template (from `based_on`, default Liquid Glass) or deep-merges a partial template into an existing one. Built-in presets never change: editing one saves a copy. Values the sanitizer had to fix are listed in `fixed` |
| `list_profiles` | — | The per-app profiles; `active` marks the one in use now |
| `upsert_profile` | `profile` (object), `preset` (`games`, `video`, `music`, `custom`) | Changes a profile found by `id`, `name` or `exe`, or adds one for an app (needs `exe`). Built-in profiles keep their apps and kind |
| `delete_profile` | `profile` | Removes a user profile and returns it (built-in ones can only be switched off with `enabled: false`) |
| `list_running_apps` | — | Apps with an open window (name and exe) and the profile each already has. Window titles are not included |
| `set_mode` | `mode` (`auto`, `lyrics`, `live`, `off`), `profile` | Global mode (`off` hides the overlay), or the mode of one app |
| `set_target_language` | `language`, `profile` | The translation language, everywhere or for one app (`""` = follow the global language) |
| `sync_offset` | `scope` (`song`, `global`), `ms`, `delta_ms`, `reset` | Shifts lyric timing; with no value it reports the offset. Scope `song` needs the app running |
| `retranslate` | — | Drops the cached translation of the song playing and translates it again (app running) |
| `edit_line_translation` | `text`, `index` | Corrects one lyric line's translation, by default the line on screen; kept in the cache (app running) |
| `get_usage` | `period` (`today`, `week`, `month`) | Gemini usage (see the example above) |
| `toggle_dubbing` | `enabled` | Dubbing on or off (flips without `enabled`); follows the active profile when it has its own dubbing settings |
| `show`, `hide` | — | Shows or hides the overlay |

## HTTP API (for scripts)

The control API is plain JSON over HTTP, so a script can use it without MCP. Read `%APPDATA%\Dualyrics\api.json`:

```json
{ "app": "Dualyrics", "version": "2.2.0", "running": true, "token": "…", "url": "http://127.0.0.1:52817", "port": 52817, "pid": 1234 }
```

- `GET /v1/tools` lists the tools with their JSON Schemas.
- `POST /v1/<tool>` runs a tool; the body is the arguments object.
- Read-only tools also answer `GET /v1/<tool>?arg=value` (lists as `a,b`).
- Every request needs `Authorization: Bearer <token>`. Answers are `{"ok": true, "result": …}` or `{"ok": false, "error": "…"}` with HTTP 400 (refused), 401 (token), 403 (Host or Origin), 404, 405 or 503 (the app is busy, for example a dialog is open).

PowerShell:

```powershell
$api = Get-Content "$env:APPDATA\Dualyrics\api.json" | ConvertFrom-Json
$h = @{ Authorization = "Bearer $($api.token)" }
Invoke-RestMethod "$($api.url)/v1/get_status" -Headers $h
Invoke-RestMethod "$($api.url)/v1/set_target_language" -Method Post -Headers $h -ContentType "application/json" -Body '{"language": "Turkish"}'
```

Python:

```python
import json, os, urllib.request

info = json.load(open(os.path.expandvars(r"%APPDATA%\Dualyrics\api.json")))
req = urllib.request.Request(info["url"] + "/v1/update_settings", method="POST",
                             data=json.dumps({"changes": {"show_next": False}}).encode(),
                             headers={"Authorization": "Bearer " + info["token"], "Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req)))
```

A proxy set in your environment must not handle `127.0.0.1` (PowerShell's `Invoke-RestMethod` and Python's `urllib` skip it by default).

## Security and privacy

- The API is bound to `127.0.0.1`: other computers cannot reach it. The port is exclusive, so no other program can listen on it at the same time.
- Every request needs the token from `api.json`. The token is random (256 bits), stays the same across restarts, and changes when you click **Regenerate token**; connected assistants read the new one by themselves. `api.json` is in your own `%APPDATA%` folder, so only programs running as your Windows user can read it.
- Web pages cannot use it: requests with an `Origin` header (sent by browsers) or a `Host` other than `127.0.0.1` / `localhost` (DNS rebinding) are refused.
- Assistants never see or change Gemini API keys (only how many there are) and cannot switch the API on or off. Proxy passwords are masked.
- Everything an assistant changes goes through the same validation as the settings window. Nothing is sent to the internet by the API or the MCP server; what the assistant itself does with the answers depends on the assistant.
- Switched off (**Settings › AI & API**), the app opens no port. Assistants can then still edit the settings files while Dualyrics is closed, as any program running as you could.
- Logs: the names of the tools called in `logs\dualyrics.log`; the MCP server writes `logs\mcp.log` (tool names and refusals).

## Troubleshooting

| Problem | What to try |
|---|---|
| The assistant says Dualyrics is running but its API is off | Turn on **Settings › AI & API › Let AI assistants control Dualyrics**, or update a running v2.0 to v2.1 |
| "This needs Dualyrics running" | `retranslate`, `edit_line_translation` and song offsets need the app; start it from the Start menu |
| Claude Desktop shows no Dualyrics tools | Check the path in `claude_desktop_config.json` (double backslashes in JSON), restart Claude Desktop, and look at its MCP log (**Settings › Developer**) |
| A tool answers "did not answer in time" | A dialog of Dualyrics is open (for example a file picker); close it and try again |
| Anything else | `%APPDATA%\Dualyrics\logs\mcp.log` and `dualyrics.log` |

## Protocol notes

The MCP server implements the stdio transport of the [Model Context Protocol](https://modelcontextprotocol.io) (protocol versions 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05): `initialize`, `ping`, `tools/list`, `tools/call` and the notifications a tools server may ignore. Messages are JSON-RPC 2.0, one per line; logs go to stderr and `mcp.log`, never to stdout. Tool results carry the answer as JSON text and, from 2025-06-18 on, as `structuredContent`. It is a small module (`dualyrics/mcp.py`) rather than the official SDK, which would add pydantic, starlette, uvicorn and httpx to the app for these four requests. The tools and their schemas are defined once, in `dualyrics/control.py`, and served by both the MCP server and the HTTP API.
