"""System-tray icon (pystray). Menu actions are forwarded to the app's command queue (Tk thread)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from dualyrics import paths
from dualyrics.i18n import t

log = logging.getLogger(__name__)


class Tray:
    def __init__(self, send: Callable[[str], None], state: Callable[[str], Any]) -> None:
        """``send(cmd)`` queues a command; ``state(name)`` returns live flags for check marks."""
        self.send = send
        self.state = state
        self.icon: Any = None
        self._thread: threading.Thread | None = None

    def _item(self, label_key: str, cmd: str, checked: str | None = None, default: bool = False) -> Any:
        import pystray

        return pystray.MenuItem(
            lambda _item: t(label_key),
            lambda _icon, _item: self.send(cmd),
            checked=(lambda _item: bool(self.state(checked))) if checked else None,
            default=default,
        )

    def _menu(self) -> Any:
        import pystray

        return pystray.Menu(
            self._item("show_lyrics", "toggle_visible", checked="visible"),
            self._item("settings", "settings", default=True),
            self._item("retranslate", "retranslate"),
            pystray.Menu.SEPARATOR,
            self._item("click_through", "toggle_click_through", checked="click_through"),
            self._item("lock_position", "toggle_locked", checked="locked"),
            self._item("open_cache", "open_cache"),
            pystray.Menu.SEPARATOR,
            self._item("quit", "quit"),
        )

    def start(self) -> None:
        try:
            import pystray
            from PIL import Image

            image = Image.open(paths.ICON_PNG) if paths.ICON_PNG.exists() else Image.new("RGBA", (64, 64), "#7C5CFF")
            self.icon = pystray.Icon("Dualyrics", image, t("tray_tip"), self._menu())
            self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
            self._thread.start()
        except Exception:
            log.exception("tray icon unavailable")
            self.icon = None

    def refresh(self) -> None:
        """Re-read labels and check marks (after a language or state change)."""
        if self.icon:
            try:
                self.icon.title = t("tray_tip")
                self.icon.update_menu()
            except Exception:
                log.debug("tray refresh failed", exc_info=True)

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self.icon = None
