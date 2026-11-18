r"""MCP server (Model Context Protocol, stdio transport) so AI assistants can read and configure Dualyrics.

Start it as ``Dualyrics.exe --mcp`` (installed app) or ``python -m dualyrics.mcp`` (from source); the assistant
(Claude Desktop, Claude Code or any MCP client) starts it itself, see Settings > AI & API and docs/AI.md.

A small, complete implementation of the parts of the protocol a tools server needs (JSON-RPC 2.0, one message
per line on stdin / stdout): ``initialize``, ``ping``, ``tools/list`` and ``tools/call``, plus the
``notifications/*`` it may ignore. The official Python SDK would add pydantic, starlette, uvicorn and httpx to the
app for the same three requests, so the app keeps this one module instead.

Each tool call goes to the running app through its local control API (dualyrics/api.py), so changes apply at
once. When the app is not running, the call works on the files in %APPDATA%\Dualyrics directly (settings.json,
templates, usage); when the app runs with its API switched off, calls are refused, because the app would write
over the files.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, BinaryIO, Callable

from dualyrics import __version__, api, control

log = logging.getLogger("dualyrics.mcp")

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")    # newest first
_STRUCTURED = {"2025-11-25", "2025-06-18"}      # versions with structuredContent in tool results

INSTRUCTIONS = (
    "Dualyrics shows synced lyrics, on-screen subtitles or live captions of what the PC plays, with a translation "
    "under every line, as an overlay on Windows. Call get_status first to see what is playing and what is on "
    "screen. Settings are changed with validated patches (update_settings); looks are templates "
    "(list_templates, apply_template, create_or_update_template); per-app behaviour lives in app profiles "
    "(list_profiles, upsert_profile; list_running_apps finds a game's exe). Ask before deleting a profile."
)
RUNNING_NO_API = ("Dualyrics is running, but its AI control API is switched off or does not answer (or the running "
                  "copy is older than v2.1). Switch on Settings > AI & API > \"Let AI assistants control Dualyrics\", "
                  "or quit Dualyrics so its files can be changed directly.")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------- where the tools run
def app_running() -> bool:
    from dualyrics import winutil

    return winutil.SingleInstance.is_running()


class AutoBackend:
    """The running app's control API when it answers; the files in %APPDATA%\\Dualyrics when the app is closed."""

    def __init__(self, info_path: Any = None, running: Callable[[], bool] = app_running,
                 offline: Callable[[], control.Controller] = control.offline_controller) -> None:
        self.client = api.ApiClient(info_path)
        self.running = running
        self.offline = offline

    def tools(self) -> list[dict[str, Any]]:
        return control.tool_list()

    def call(self, name: str, args: dict[str, Any]) -> Any:
        try:
            return self.client.call(name, args)
        except (api.NotRunning, api.Unreachable) as e:
            reason = type(e).__name__
        if self.running():
            log.warning("tool %s: app running but its API is not reachable (%s)", name, reason)
            raise control.OpError(RUNNING_NO_API)
        ctrl = self.offline()
        try:
            return ctrl.call(name, args)
        finally:
            ctrl.close()


# ---------------------------------------------------------------- protocol
class McpServer:
    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.protocol: str | None = None
        self.initialized = False

    def handle(self, msg: Any) -> Any:
        """One parsed message (or a JSON-RPC batch) in, the response to write out (None for notifications)."""
        if isinstance(msg, list):
            if not msg:
                return _error(None, INVALID_REQUEST, "Invalid Request: empty batch")
            out = [r for r in (self._one(m) for m in msg) if r is not None]
            return out or None
        return self._one(msg)

    def _one(self, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            mid = msg.get("id") if isinstance(msg, dict) else None
            return _error(mid if isinstance(mid, (str, int)) else None, INVALID_REQUEST, "Invalid Request")
        method = msg.get("method")
        has_id = "id" in msg
        mid = msg.get("id")
        if not isinstance(method, str):
            if has_id and ("result" in msg or "error" in msg):
                return None            # a response to a request we never send: ignore
            return _error(mid if isinstance(mid, (str, int)) else None, INVALID_REQUEST, "Invalid Request")
        if not has_id:
            if method == "notifications/initialized":
                self.initialized = True
            return None                # notifications (initialized, cancelled, progress...) get no answer
        if isinstance(mid, bool) or not isinstance(mid, (str, int)):
            return _error(None, INVALID_REQUEST, "Invalid Request: id must be a string or a number")
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(mid, INVALID_PARAMS, "Invalid params: params must be an object")
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.backend.tools()}
            elif method == "tools/call":
                result = self._call(params)
            else:
                return _error(mid, METHOD_NOT_FOUND, f"Method not found: {method}")
        except RpcError as e:
            return _error(mid, e.code, e.message)
        except Exception:  # noqa: BLE001
            log.exception("%s failed", method)
            return _error(mid, INTERNAL_ERROR, "Internal error")
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        self.protocol = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        log.info("initialize: client %s %s, protocol %s (asked %s)", client.get("name"), client.get("version"),
                 self.protocol, asked)
        return {"protocolVersion": self.protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "dualyrics", "title": "Dualyrics", "version": __version__},
                "instructions": INSTRUCTIONS}

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise RpcError(INVALID_PARAMS, "Invalid params: the tool name is missing")
        if name not in control.TOOL_BY_NAME:
            raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return self._tool_error("arguments must be an object")
        try:
            result = self.backend.call(name, args)
        except control.UnknownTool:
            raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}") from None
        except control.OpError as e:
            log.info("tool %s refused: %s", name, e)
            return self._tool_error(str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return self._tool_error(f"Dualyrics could not do this ({type(e).__name__}: {e})")
        log.info("tool %s ok", name)
        out: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                               "isError": False}
        if self.protocol in _STRUCTURED and isinstance(result, dict):
            out["structuredContent"] = result
        return out

    @staticmethod
    def _tool_error(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], "isError": True}


def serve(stdin: BinaryIO, stdout: BinaryIO, server: McpServer) -> None:
    """Read one JSON-RPC message per line until stdin closes; answer each request on one line."""
    for raw in iter(stdin.readline, b""):
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            resp: Any = _error(None, PARSE_ERROR, "Parse error")
        else:
            resp = server.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            stdout.flush()


# ---------------------------------------------------------------- client configuration (Settings > AI & API)
def launch_command() -> tuple[str, list[str], dict[str, str]]:
    """How an MCP client starts this server: (command, arguments, environment). A data folder set with
    ``DUALYRICS_HOME`` (portable mode) is passed on, or the server would read another api.json and settings."""
    home = os.environ.get("DUALYRICS_HOME")
    env = {"DUALYRICS_HOME": home} if home else {}
    if getattr(sys, "frozen", False):
        return sys.executable, ["--mcp"], env
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        exe = exe.with_name("python.exe")       # started from run.pyw: the console interpreter talks stdio
    return str(exe), ["-m", "dualyrics.mcp"], {"PYTHONPATH": str(Path(__file__).resolve().parent.parent), **env}


def claude_desktop_config() -> str:
    cmd, args, env = launch_command()
    entry: dict[str, Any] = {"command": cmd, "args": args}
    if env:
        entry["env"] = env
    return json.dumps({"mcpServers": {"dualyrics": entry}}, indent=2)


def claude_desktop_config_path() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Claude" / \
        "claude_desktop_config.json"


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or "\\" in arg) else arg


def claude_code_command() -> str:
    cmd, args, env = launch_command()
    envs = "".join(f" --env {k}={_quote(v)}" for k, v in env.items())
    return f"claude mcp add dualyrics --scope user{envs} -- {_quote(cmd)} {' '.join(args)}"


def generic_command() -> str:
    cmd, args, env = launch_command()
    prefix = "".join(f"{k}={_quote(v)} " for k, v in env.items())
    return f"{prefix}{_quote(cmd)} {' '.join(args)}"


# ---------------------------------------------------------------- process setup
def _std_streams() -> tuple[BinaryIO, BinaryIO]:
    """Binary stdin / stdout. The windowed Dualyrics.exe has no Python console streams, but an MCP client starts
    it with pipes: those inherited handles are opened directly."""
    ins = getattr(sys.stdin, "buffer", None)
    outs = getattr(sys.stdout, "buffer", None)
    if ins is not None and outs is not None:
        return ins, outs
    import ctypes
    import msvcrt

    k32 = ctypes.WinDLL("kernel32")
    k32.GetStdHandle.restype = ctypes.c_void_p

    def stream(which: int, flags: int, mode: str) -> BinaryIO:
        h = k32.GetStdHandle(which)
        if not h or h == ctypes.c_void_p(-1).value:
            raise SystemExit("Dualyrics --mcp talks MCP on stdin and stdout: an MCP client starts it (docs/AI.md)")
        return os.fdopen(msvcrt.open_osfhandle(h, flags), mode, buffering=0)

    if ins is None:
        ins = stream(-10, os.O_RDONLY | os.O_BINARY, "rb")
    if outs is None:
        outs = stream(-11, os.O_WRONLY | os.O_BINARY, "wb")
    return ins, outs


def _setup_logging() -> None:
    from dualyrics import paths

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        h = logging.handlers.RotatingFileHandler(paths.log_dir() / "mcp.log", maxBytes=500_000, backupCount=1,
                                                 encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)-7s %(name)s: %(message)s",
                                         "%Y-%m-%d %H:%M:%S"))
        root.addHandler(h)
    except OSError:
        pass
    if sys.stderr is not None:          # stderr is free for logs in the stdio transport; stdout is not
        err = logging.StreamHandler(sys.stderr)
        err.setLevel(logging.WARNING)
        root.addHandler(err)


def main() -> None:
    stdin, stdout = _std_streams()
    sys.stdout = sys.stderr if sys.stderr is not None else open(os.devnull, "w")   # stray prints never reach MCP
    _setup_logging()
    log.info("Dualyrics %s MCP server started (pid %d)", __version__, os.getpid())
    try:
        serve(stdin, stdout, McpServer(AutoBackend()))
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    log.info("MCP server stopped")


if __name__ == "__main__":
    main()
