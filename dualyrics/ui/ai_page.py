"""Settings > AI & API: the local control API (on / off, status, token) and copy-ready configuration for Claude
Desktop, Claude Code and other MCP clients (dualyrics/api.py, dualyrics/mcp.py, docs/AI.md)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from dualyrics import REPO_URL, mcp, paths
from dualyrics.i18n import t
from dualyrics.ui import tokens as T

if TYPE_CHECKING:
    from dualyrics.settings_window import SettingsWindow

log = logging.getLogger(__name__)

DOCS_URL = REPO_URL + "/blob/main/docs/AI.md"


def _snippet(k: Any, g: Any, text: str) -> ctk.CTkTextbox:
    tb = k.textbox(g, text, height=min(220, 22 + 18 * (text.count("\n") + 1)))
    tb.configure(font=k.font("mono"), wrap="none")
    return tb


def build(win: "SettingsWindow", f: Any) -> None:
    k = win.kit
    assert k is not None
    s = win.s
    server = getattr(win.app, "api", None)

    def copy(text: str) -> None:
        try:
            win.win.clipboard_clear()
            win.win.clipboard_append(text)
            win.toast(t("ai_copied"), "success")
        except Exception:
            log.debug("clipboard failed", exc_info=True)

    # ---- the control API
    g = k.group(f, t("ai_api"), t("ai_api_hint"))
    refresh_switch = k.switch(g, t("ai_enable"), *win.bind_key("api_enabled"), help=t("ai_enable_hint"))
    status = k.label(g, "", "body.strong", T.TEXT_SECONDARY)
    status.pack(fill="x", pady=(T.SPACE[4], 0))

    def show_status() -> None:
        if not status.winfo_exists():
            return
        if server is not None and server.running:
            status.configure(text=t("ai_status_on", port=server.port), text_color=T.SUCCESS)
        elif server is not None and server.error and s["api_enabled"]:
            status.configure(text=t("ai_status_error", err=server.error[:160]), text_color=T.DANGER)
        else:
            status.configure(text=t("ai_status_off"), text_color=T.TEXT_SECONDARY)

    def on_toggle() -> None:
        refresh_switch()
        if win.win is not None:
            win.win.after(400, show_status)    # the app starts / stops the server on its next tick

    win.watch("api_enabled", on_toggle)
    show_status()
    acts = k.actions(g)

    def regenerate() -> None:
        if server is not None:
            server.regenerate_token()
            win.toast(t("ai_regen_done"), "success")

    k.button(acts, t("ai_regen"), regenerate, icon="retranslate").pack(side=k.start)
    if server is not None:
        k.button(acts, t("ai_copy_token"), lambda: copy(server.token), icon="copy").pack(side=k.start, padx=8)
    k.button(acts, t("open_data"), lambda: os.startfile(paths.data_dir()), icon="folder").pack(side=k.start)

    # ---- Claude Desktop
    g = k.group(f, t("ai_desktop"), t("ai_desktop_hint"))
    desktop = mcp.claude_desktop_config()
    _snippet(k, g, desktop)
    acts = k.actions(g)
    k.button(acts, t("ai_copy"), lambda: copy(desktop), kind="primary", icon="copy").pack(side=k.start)

    def open_config() -> None:
        path = mcp.claude_desktop_config_path()
        try:
            if path.exists():
                os.startfile(path)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                os.startfile(path.parent)
        except OSError:
            try:
                os.startfile(path.parent)
            except OSError as e:
                win.toast(str(e), "warning")

    k.button(acts, t("ai_open_config"), open_config, icon="folder").pack(side=k.start, padx=8)

    # ---- Claude Code
    g = k.group(f, t("ai_code"), t("ai_code_hint"))
    code = mcp.claude_code_command()
    _snippet(k, g, code)
    k.button(k.actions(g), t("ai_copy"), lambda: copy(code), icon="copy").pack(side=k.start)

    # ---- any MCP client
    g = k.group(f, t("ai_other"), t("ai_other_hint"))
    other = mcp.generic_command()
    _snippet(k, g, other)
    acts = k.actions(g)
    k.button(acts, t("ai_copy"), lambda: copy(other), icon="copy").pack(side=k.start)
    k.button(acts, t("ai_docs"), lambda: os.startfile(DOCS_URL), icon="about").pack(side=k.start, padx=8)

    # ---- examples
    g = k.group(f, t("ai_examples"))
    k.label(g, t("ai_examples_text"), "body", T.TEXT_SECONDARY, wrap=560).pack(fill="x", pady=(T.SPACE[3], 0))
