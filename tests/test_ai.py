"""AI integration: settings patch validation, the controller, the local control API (auth) and the MCP server."""

import http.client
import io
import json
import queue
import threading

import pytest

from dualyrics import api, control, mcp, settings as settings_mod
from dualyrics.settings import Settings
from dualyrics.templates import TemplateStore


@pytest.fixture
def ctrl(tmp_path):
    return control.Controller(Settings(tmp_path / "settings.json"), TemplateStore(tmp_path / "templates"),
                              usage_path=tmp_path / "usage.sqlite")


# ---------------------------------------------------------------- settings patches
def test_patch_valid_clamped_and_normalised():
    cur = settings_mod.defaults()
    applied, adjusted, rejected = control.check_patch(
        cur, {"show_next": False, "overlay_opacity": 7, "color_bg": "#abcdef", "target_language": "Turkish"})
    assert rejected == {}
    assert applied == {"show_next": False, "target_language": "tr"}
    assert adjusted["overlay_opacity"]["applied"] == 1.0 and adjusted["overlay_opacity"]["range"] == [0.2, 1.0]
    assert adjusted["color_bg"]["applied"] == "#ABCDEF"
    # normalised to exactly the default value: still accepted, not "invalid"
    players = [p.upper() for p in cur["preferred_players"]]
    applied, adjusted, rejected = control.check_patch(cur, {"hotkey_toggle": "Ctrl + Alt + L",
                                                            "preferred_players": players})
    assert rejected == {} and adjusted["hotkey_toggle"]["applied"] == "ctrl+alt+l"
    _, _, rejected = control.check_patch(cur, {"hotkey_toggle": "banana"})
    assert "hotkey_toggle" in rejected


def test_patch_rejects_unknown_protected_and_invalid():
    cur = settings_mod.defaults()
    _, _, rejected = control.check_patch(cur, {"mode": "banana", "nope": 1, "gemini_keys": ["k"], "api_enabled": False,
                                               "profiles": [], "visible": "yes", "target_language": "Klingon",
                                               "source_order": ["bogus"]})
    assert set(rejected) == {"mode", "nope", "gemini_keys", "api_enabled", "profiles", "visible", "target_language",
                             "source_order"}
    assert "allowed: auto, lyrics, live" in rejected["mode"]
    assert "expected a boolean" in rejected["visible"]
    with pytest.raises(control.OpError):
        control.check_patch(cur, {})


def test_patch_checks_template_exists():
    _, _, rejected = control.check_patch(settings_mod.defaults(), {"template": "no-such-look"}, lambda tid: False)
    assert "template" in rejected


def test_update_settings_is_all_or_nothing(ctrl):
    with pytest.raises(control.OpError, match="nothing was changed"):
        ctrl.call("update_settings", {"changes": {"show_next": False, "mode": "banana"}})
    assert ctrl.s["show_next"] is True
    res = ctrl.call("update_settings", {"changes": {"show_next": False}, "dry_run": True})
    assert res["dry_run"] and ctrl.s["show_next"] is True
    res = ctrl.call("update_settings", {"changes": {"show_next": False, "offset_ms": 99999}})
    assert ctrl.s["show_next"] is False and ctrl.s["offset_ms"] == 15000 and "offset_ms" in res["adjusted"]


def test_public_settings_hide_keys_and_proxy_password(ctrl):
    ctrl.s.update(gemini_keys=["AIza-secret-1", "AIza-secret-2"], proxy_mode="custom",
                  proxy_url="http://user:pw@127.0.0.1:8080")
    out = ctrl.call("get_settings")
    text = json.dumps(out)
    assert "secret" not in text and ":pw@" not in text
    assert out["settings"]["gemini_keys_count"] == 2 and "gemini_keys" not in out["settings"]
    assert out["choices"]["mode"] == ["auto", "lyrics", "live"]
    assert ctrl.call("get_settings", {"keys": ["mode", "zzz"]}) == {"settings": {"mode": "auto"},
                                                                    "unknown_keys": ["zzz"]}


def test_api_enabled_setting_defaults_on():
    assert settings_mod.defaults()["api_enabled"] is True
    assert settings_mod.validate({"api_enabled": "no"})["api_enabled"] is True
    assert settings_mod.validate({"api_enabled": False})["api_enabled"] is False
    assert "ai" in settings_mod.SECTIONS


def test_resolve_language():
    assert control.resolve_language("Turkish") == "tr"
    assert control.resolve_language("farsi") == "fa"
    assert control.resolve_language("Türkçe") == "tr"
    assert control.resolve_language("zh-cn") == "zh-CN"
    assert control.resolve_language("de-AT") == "de"
    with pytest.raises(control.OpError):
        control.resolve_language("Klingon")


# ---------------------------------------------------------------- controller (app closed)
def test_args_are_checked(ctrl):
    with pytest.raises(control.OpError, match="unknown argument"):
        ctrl.call("get_status", {"x": 1})
    with pytest.raises(control.OpError, match="missing argument"):
        ctrl.call("set_mode", {})
    with pytest.raises(control.OpError, match="one of"):
        ctrl.call("set_mode", {"mode": "loud"})
    with pytest.raises(control.UnknownTool):
        ctrl.call("format_disk", {})
    with pytest.raises(control.OpError, match="needs Dualyrics running"):
        ctrl.call("retranslate", {})


def test_game_profile_with_game_hud(ctrl):
    res = ctrl.call("upsert_profile", {"profile": {"name": "God of War Ragnarok", "exe": "GoWR.exe"},
                                       "preset": "games"})
    assert res["created"] and res["profile"]["exes"] == ["gowr.exe"] and res["profile"]["mode"] == "live"
    pid = res["profile"]["id"]
    out = ctrl.call("apply_template", {"id": "Game HUD", "profile": "GoWR"})
    assert out["template"] == "game_hud" and out["applied_to"] == f"profile {pid}"
    assert ctrl.s["template"] != "game_hud"          # the global look stays
    res = ctrl.call("upsert_profile", {"profile": {"exe": "gowr.exe", "target_language": "Persian"}})
    assert not res["created"] and res["profile"]["target_language"] == "fa"
    with pytest.raises(control.OpError, match="invalid values"):
        ctrl.call("upsert_profile", {"profile": {"id": pid, "mode": "loud"}})
    with pytest.raises(control.OpError, match="exe"):
        ctrl.call("upsert_profile", {"profile": {"name": "Nameless"}})
    with pytest.raises(control.OpError, match="built in"):
        ctrl.call("delete_profile", {"profile": "spotify"})
    gone = ctrl.call("delete_profile", {"profile": pid})["deleted"]
    assert gone["id"] == pid and all(p["id"] != pid for p in ctrl.s["profiles"])


def test_templates_create_update_and_builtins_stay(ctrl):
    res = ctrl.call("create_or_update_template", {"based_on": "cinema", "name": "Big yellow",
                                                  "template": {"lines": {"translation": {"color": "#ffd54f"}}}})
    tid = res["id"]
    assert res["created"] and res["template"]["based_on"] == "cinema"
    assert res["template"]["lines"]["translation"]["color"].upper() == "#FFD54F"
    upper = {"lines": {"original": {"uppercase": True}}}
    res = ctrl.call("create_or_update_template", {"id": tid, "template": upper})
    assert not res["created"] and res["id"] == tid and res["template"]["lines"]["original"]["uppercase"] is True
    assert res["template"]["lines"]["translation"]["color"].upper() == "#FFD54F"      # the earlier edit stays
    res = ctrl.call("create_or_update_template", {"id": "game_hud", "template": {**upper, "scale": 3}})
    assert res["created"] and res["id"] != "game_hud" and "unchanged" in res["note"]
    assert res["template"]["based_on"] == "game_hud" and res["fixed"]               # the bad "scale" was fixed
    assert res["template"]["lines"]["original"]["uppercase"] is True
    assert ctrl.tpl.get("game_hud")["lines"]["original"]["uppercase"] is False
    listed = {t["id"]: t for t in ctrl.call("list_templates")["templates"]}
    assert listed[tid]["name"] == "Big yellow" and listed["game_hud"]["builtin"]
    ctrl.call("apply_template", {"id": tid})
    assert ctrl.s["template"] == tid


def test_mode_language_offset_dubbing_visibility(ctrl):
    assert ctrl.call("set_mode", {"mode": "off"})["visible"] is False
    assert ctrl.call("set_mode", {"mode": "lyrics"}) == {"mode": "lyrics", "visible": True, "applied_to": "global"}
    assert ctrl.call("set_target_language", {"language": "Turkish"})["target_language"] == "tr"
    assert ctrl.call("sync_offset", {"delta_ms": 500})["offset_ms"] == 500
    assert ctrl.call("sync_offset", {"delta_ms": -250.0})["offset_ms"] == 250
    with pytest.raises(control.OpError, match="needs Dualyrics running"):
        ctrl.call("sync_offset", {"scope": "song", "ms": 1})
    assert ctrl.call("toggle_dubbing", {})["enabled"] is True
    assert ctrl.call("toggle_dubbing", {"enabled": False})["enabled"] is False
    assert ctrl.call("hide")["visible"] is False and ctrl.call("show")["visible"] is True
    assert ctrl.call("get_usage", {"period": "week"})["period"] == "week"
    st = ctrl.call("get_status")
    assert st["app_running"] is False and st["target_language"] == "tr" and st["global_offset_ms"] == 250


def test_operations_run_on_the_ui_thread(tmp_path):
    """With the app, the controller hands every operation to the app's Tk thread (call_soon) and waits."""
    jobs: queue.Queue = queue.Queue()
    seen: list[int] = []
    box: dict = {}
    stop = threading.Event()

    class FakeApp:
        def call_soon(self, fn):
            jobs.put(fn)

    def ui_thread():
        box["ctrl"] = control.Controller(Settings(tmp_path / "s.json"), TemplateStore(tmp_path / "t"), app=FakeApp())
        box["ready"].set()
        while not stop.is_set():
            try:
                fn = jobs.get(timeout=0.05)
            except queue.Empty:
                continue
            seen.append(threading.get_ident())
            fn()

    box["ready"] = threading.Event()
    th = threading.Thread(target=ui_thread, daemon=True)
    th.start()
    box["ready"].wait(5)
    try:
        out = box["ctrl"].call("get_settings", {"keys": ["mode"]})
        assert out == {"settings": {"mode": "auto"}}
        assert seen == [th.ident]
        with pytest.raises(control.OpError):
            box["ctrl"].call("update_settings", {"changes": {"mode": "x"}})   # errors come back to the caller
    finally:
        stop.set()


# ---------------------------------------------------------------- control API
@pytest.fixture
def server(ctrl, tmp_path):
    srv = api.ControlServer(ctrl, tmp_path / "api.json")
    assert srv.start()
    yield srv
    srv.stop()


def _req(srv, method, path, body=None, token=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    h = dict(headers or {})
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    conn.request(method, path, data, h)
    r = conn.getresponse()
    out = r.status, json.loads(r.read() or b"{}")
    conn.close()
    return out


def test_api_json_has_port_and_token(server, tmp_path):
    info = api.read_info(tmp_path / "api.json")
    assert info["running"] and info["port"] == server.port and info["token"] == server.token
    assert info["url"] == f"http://127.0.0.1:{server.port}" and len(server.token) >= 32
    assert server._httpd.server_address[0] == "127.0.0.1"


def test_api_requires_the_token(server):
    assert _req(server, "GET", "/v1/get_status")[0] == 401
    assert _req(server, "GET", "/v1/get_status", token="wrong" * 10)[0] == 401
    status, body = _req(server, "GET", "/v1/get_status", token=server.token)
    assert status == 200 and body["ok"] and body["result"]["version"]


def test_api_refuses_web_pages_and_rebinding(server):
    assert _req(server, "GET", "/v1/tools", token=server.token, headers={"Origin": "https://evil.example"})[0] == 403
    assert _req(server, "GET", "/v1/tools", token=server.token, headers={"Host": "evil.example"})[0] == 403


def test_api_routes(server):
    tok = server.token
    status, body = _req(server, "GET", "/v1/tools", token=tok)
    assert status == 200 and {t["name"] for t in body["result"]} == set(control.TOOL_BY_NAME)
    assert _req(server, "GET", "/v1/hide", token=tok)[0] == 405                     # changes need POST
    assert _req(server, "GET", "/v1/nothing", token=tok)[0] == 404
    assert _req(server, "POST", "/v1/update_settings", b"{not json", token=tok)[0] == 400
    status, body = _req(server, "POST", "/v1/update_settings", {"changes": {"mode": "x"}}, token=tok)
    assert status == 400 and "nothing was changed" in body["error"]
    status, body = _req(server, "POST", "/v1/update_settings", {"changes": {"show_next": False}}, token=tok)
    assert status == 200 and body["result"]["changed"] == ["show_next"]
    status, body = _req(server, "GET", "/v1/get_settings?keys=show_next,mode", token=tok)
    assert body["result"]["settings"] == {"show_next": False, "mode": "auto"}


def test_api_regenerate_token_and_stop(server, tmp_path):
    old = server.token
    new = server.regenerate_token()
    assert new != old and api.read_info(tmp_path / "api.json")["token"] == new
    assert _req(server, "GET", "/v1/tools", token=old)[0] == 401
    assert _req(server, "GET", "/v1/tools", token=new)[0] == 200
    server.stop()
    info = api.read_info(tmp_path / "api.json")
    assert info["running"] is False and "port" not in info and info["token"] == new
    again = api.ControlServer(server.controller, tmp_path / "api.json")
    assert again.token == new                     # the token survives a restart of the app


# ---------------------------------------------------------------- MCP
def _rpc(srv, method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return srv.handle(msg)


def test_mcp_initialize_and_tools_list(ctrl):
    srv = mcp.McpServer(ctrl)
    res = _rpc(srv, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "1"}})["result"]
    assert res["protocolVersion"] == "2025-06-18" and res["serverInfo"]["name"] == "dualyrics"
    assert res["capabilities"] == {"tools": {"listChanged": False}}
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert _rpc(srv, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == \
        mcp.PROTOCOL_VERSIONS[0]
    tools = _rpc(srv, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    for want in ("get_status", "get_settings", "update_settings", "list_templates", "get_template",
                 "apply_template", "create_or_update_template", "list_profiles", "upsert_profile", "delete_profile",
                 "set_mode", "set_target_language", "sync_offset", "retranslate", "edit_line_translation",
                 "get_usage", "toggle_dubbing", "show", "hide"):
        assert want in names
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]
    assert _rpc(srv, "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_mcp_tools_call(ctrl):
    srv = mcp.McpServer(ctrl)
    _rpc(srv, "initialize", {"protocolVersion": "2025-06-18"})
    res = _rpc(srv, "tools/call", {"name": "set_target_language", "arguments": {"language": "Turkish"}})["result"]
    assert res["isError"] is False and res["structuredContent"]["target_language"] == "tr"
    assert json.loads(res["content"][0]["text"])["target_language"] == "tr"
    res = _rpc(srv, "tools/call", {"name": "set_mode", "arguments": {"mode": "loud"}})["result"]
    assert res["isError"] is True and "one of" in res["content"][0]["text"]
    err = _rpc(srv, "tools/call", {"name": "rm_rf", "arguments": {}})["error"]
    assert err["code"] == -32602
    assert _rpc(srv, "resources/list")["error"]["code"] == -32601
    old = mcp.McpServer(ctrl)
    _rpc(old, "initialize", {"protocolVersion": "2024-11-05"})
    res = _rpc(old, "tools/call", {"name": "get_status"})["result"]
    assert "structuredContent" not in res and not res["isError"]


def test_mcp_stdio_round_trip(ctrl):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "b", "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "hide", "arguments": {}}},
    ]
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines) + b"{oops\n\n")
    stdout = io.BytesIO()
    mcp.serve(stdin, stdout, mcp.McpServer(ctrl))
    out = [json.loads(x) for x in stdout.getvalue().splitlines()]
    assert [o.get("id") for o in out] == [1, "b", 3, None]
    assert out[0]["result"]["protocolVersion"] == "2025-11-25"
    assert out[2]["result"]["structuredContent"] == {"visible": False}
    assert out[3]["error"]["code"] == -32700
    assert ctrl.s["visible"] is False


def test_mcp_invalid_messages(ctrl):
    srv = mcp.McpServer(ctrl)
    assert srv.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    assert srv.handle([])["error"]["code"] == -32600
    assert srv.handle({"jsonrpc": "2.0", "id": 9, "result": {}}) is None
    batch = srv.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "x/y"}])
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    assert srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": [1]})["error"]["code"] == -32602


def test_mcp_backend_uses_the_api_when_the_app_runs(server, tmp_path):
    used: list[str] = []
    backend = mcp.AutoBackend(tmp_path / "api.json", running=lambda: True,
                              offline=lambda: used.append("offline"))   # type: ignore[arg-type,return-value]
    srv = mcp.McpServer(backend)
    _rpc(srv, "initialize", {"protocolVersion": "2025-06-18"})
    res = _rpc(srv, "tools/call", {"name": "update_settings", "arguments": {"changes": {"show_next": False}}})
    assert res["result"]["isError"] is False and server.controller.s["show_next"] is False and used == []
    res = _rpc(srv, "tools/call", {"name": "set_mode", "arguments": {"mode": "x"}})["result"]
    assert res["isError"] and "one of" in res["content"][0]["text"]
    server.stop()                                   # the API is off but the app still runs: refuse
    res = _rpc(srv, "tools/call", {"name": "hide", "arguments": {}})["result"]
    assert res["isError"] and "AI & API" in res["content"][0]["text"] and used == []


def test_mcp_backend_edits_files_when_the_app_is_closed(ctrl, tmp_path):
    backend = mcp.AutoBackend(tmp_path / "missing-api.json", running=lambda: False, offline=lambda: ctrl)
    assert backend.call("hide", {}) == {"visible": False}
    assert Settings(ctrl.s.path)["visible"] is False          # written to settings.json


def test_client_config_snippets():
    cfg = json.loads(mcp.claude_desktop_config())["mcpServers"]["dualyrics"]
    assert cfg["command"] and cfg["args"] in (["--mcp"], ["-m", "dualyrics.mcp"])
    cmd = mcp.claude_code_command()
    assert cmd.startswith("claude mcp add dualyrics --scope user") and " -- " in cmd
    assert "dualyrics" in mcp.generic_command() or "--mcp" in mcp.generic_command()
