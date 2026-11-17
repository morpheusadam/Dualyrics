r"""Local control API: lets AI assistants (through the MCP server, dualyrics/mcp.py) read and change the running app.

* HTTP on 127.0.0.1 only, on a random free port; nothing is reachable from the network.
* Every request needs ``Authorization: Bearer <token>``. The port and the token are written to
  ``%APPDATA%\Dualyrics\api.json`` (the user's own folder); the token stays the same across restarts until the
  user regenerates it in Settings > AI & API.
* Requests from web pages are refused: a request with an ``Origin`` header, or a ``Host`` other than
  127.0.0.1 / localhost (DNS rebinding), gets 403.
* On by default (Settings > AI & API, setting ``api_enabled``).

Routes (JSON in and out, see docs/AI.md):  ``GET /v1/tools`` lists the operations; ``POST /v1/<tool>`` with the
arguments as a JSON object runs one; read-only tools also answer ``GET /v1/<tool>?arg=value``. Answers are
``{"ok": true, "result": ...}`` or ``{"ok": false, "error": "..."}``.
"""

from __future__ import annotations

import hmac
import http.client
import json
import logging
import os
import re
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dualyrics import __version__, control

log = logging.getLogger(__name__)

INFO_FILE = "api.json"
MAX_BODY = 1024 * 1024
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def info_path() -> Path:
    from dualyrics import paths

    return paths.data_dir() / INFO_FILE


def read_info(path: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads((path or info_path()).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _write_info(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.error("could not write %s: %s", path.name, e)


# ---------------------------------------------------------------- server
class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False      # on Windows SO_REUSEADDR would let another program take the port

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _Handler(BaseHTTPRequestHandler):
    server_version = "Dualyrics"
    sys_version = ""
    timeout = 30            # a client that opens a connection and sends nothing never holds a thread for long

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.debug("api: " + format, *args)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _refuse(self) -> None:
        self._send(405, {"ok": False, "error": "use GET or POST"})

    do_PUT = do_PATCH = do_DELETE = _refuse

    def _send(self, code: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method: str) -> None:
        owner: ControlServer = self.server.owner  # type: ignore[attr-defined]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in (f"127.0.0.1:{owner.port}", f"localhost:{owner.port}"):
            return self._send(403, {"ok": False, "error": "wrong Host header"})
        if self.headers.get("Origin") is not None:
            return self._send(403, {"ok": False, "error": "requests from web pages are not accepted"})
        auth = self.headers.get("Authorization") or ""
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not token or not hmac.compare_digest(token.encode("utf-8"), owner.token.encode("utf-8")):
            return self._send(401, {"ok": False, "error": "missing or wrong token (it is in api.json)"},
                              {"WWW-Authenticate": 'Bearer realm="Dualyrics"'})
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        if len(parts) != 2 or parts[0] != "v1":
            return self._send(404, {"ok": False, "error": "not found; GET /v1/tools lists the operations"})
        name = parts[1]
        if name == "tools":
            return self._send(200, {"ok": True, "result": control.tool_list()})
        spec = control.TOOL_BY_NAME.get(name)
        if spec is None:
            return self._send(404, {"ok": False, "error": f"unknown tool {name!r}"})
        if method == "GET":
            if name not in control.READ_ONLY:
                return self._send(405, {"ok": False, "error": f"{name} changes something: use POST"})
            args: Any = control.coerce_query(spec["inputSchema"],
                                             {k: v[-1] for k, v in parse_qs(url.query).items()})
        else:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {"ok": False, "error": "body too large"})
            raw = self.rfile.read(n) if n else b""
            try:
                args = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            except (UnicodeDecodeError, ValueError) as e:
                return self._send(400, {"ok": False, "error": f"the body is not JSON: {e}"})
            if not isinstance(args, dict):
                return self._send(400, {"ok": False, "error": "the body must be a JSON object of arguments"})
        try:
            result = owner.controller.call(name, args)
        except control.OpError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        except TimeoutError as e:
            return self._send(503, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("api: %s failed", name)
            return self._send(500, {"ok": False, "error": f"internal error ({type(e).__name__}); see the log"})
        log.info("api: %s", name)
        return self._send(200, {"ok": True, "result": result})


class ControlServer:
    """The API server of the running app. ``start()`` / ``stop()`` from the Tk thread; requests run on their
    own threads and hand the work to ``controller`` (which runs it on the Tk thread)."""

    def __init__(self, controller: Any, path: Path | None = None) -> None:
        self.controller = controller
        self.path = path or info_path()
        tok = read_info(self.path).get("token")
        self.token = tok if isinstance(tok, str) and _TOKEN_RE.match(tok) else new_token()
        self.port = 0
        self.error = ""
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}" if self.running else ""

    def start(self) -> bool:
        if self._httpd is not None:
            return True
        try:
            httpd = _Server(("127.0.0.1", 0), _Handler)
        except OSError as e:
            self.error = str(e)
            log.error("control API could not start: %s", e)
            self._write()
            return False
        httpd.owner = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        self.error = ""
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5},
                                        name="control-api", daemon=True)
        self._thread.start()
        self._write()
        log.info("control API on 127.0.0.1:%d", self.port)
        return True

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                log.debug("control API stop failed", exc_info=True)
            log.info("control API stopped")
        self.port = 0
        self._write()

    def regenerate_token(self) -> str:
        """A new token: clients that read api.json pick it up on their next request; the old one stops working."""
        self.token = new_token()
        self._write()
        log.info("control API token regenerated")
        return self.token

    def _write(self) -> None:
        data: dict[str, Any] = {"app": "Dualyrics", "version": __version__, "running": self.running,
                                "token": self.token}
        if self.running:
            data.update(url=self.url, port=self.port, pid=os.getpid())
        _write_info(self.path, data)


# ---------------------------------------------------------------- client (used by the MCP server)
class NotRunning(Exception):
    """api.json names no running API (the app is closed or its API is off)."""


class Unreachable(Exception):
    """api.json names a port but nothing answers there (the app closed without cleaning up)."""


class ApiClient:
    def __init__(self, path: Path | None = None, timeout: float = control.UI_TIMEOUT + 10) -> None:
        self.path = path
        self.timeout = timeout

    def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        info = read_info(self.path or info_path())
        port, token = info.get("port"), info.get("token")
        if not info.get("running") or not isinstance(port, int) or not isinstance(token, str):
            raise NotRunning()
        body = json.dumps(args or {}).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=self.timeout)
        try:
            conn.request("POST", f"/v1/{name}", body, {"Authorization": f"Bearer {token}",
                                                       "Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
        except (ConnectionError, socket.timeout, OSError) as e:
            raise Unreachable(str(e)) from None
        finally:
            conn.close()
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise control.OpError(f"the app answered HTTP {resp.status} without JSON") from None
        if resp.status == 401:
            raise control.OpError("the app refused the token in api.json (regenerated just now? try again)")
        if resp.status == 404 and name not in control.TOOL_BY_NAME:
            raise control.UnknownTool(data.get("error") or name)
        if not data.get("ok"):
            raise control.OpError(data.get("error") or f"HTTP {resp.status}")
        return data.get("result")
