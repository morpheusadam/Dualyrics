# Security policy

## Supported versions

Security fixes go into the latest release. Please update before you report.

| Version | Supported |
|---|---|
| 2.1.x | Yes |
| 2.0.x and older | No |

## Reporting a problem

Please do not open a public issue for a security problem. Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/morpheusadam/Dualyrics/security/advisories/new)).

Include what you found, how to reproduce it, the Dualyrics version and what an attacker could do with it. You should get a first answer within 7 days. Once a fix is released, the advisory is published with credit to you, unless you prefer to stay anonymous.

## What is in scope

- The local control API (`dualyrics/api.py`): it must listen on `127.0.0.1` only, require the bearer token from `%APPDATA%\Dualyrics\api.json`, and refuse requests from web pages (`Origin` header) and DNS rebinding (wrong `Host`).
- The MCP server (`Dualyrics.exe --mcp`): it must never reveal or change Gemini keys or switch the API off.
- Storage of Gemini keys (Windows DPAPI, per Windows user).
- Downloads the app makes: Whisper models (checked against published sizes and checksums), Piper, Live mode and voice packages.
- The installer and the update path.

Out of scope: problems that need an attacker who already runs code as your Windows user, and the rate limits or availability of LRCLIB, Google Translate or Gemini.
