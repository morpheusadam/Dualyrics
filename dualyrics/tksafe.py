"""Keep tkinter finalizers on the Tk thread.

``tkinter.Variable``, ``tkinter.Image``, ``tkinter.font.Font`` (customtkinter's ``CTkFont``) and Pillow's
``ImageTk.PhotoImage`` delete their Tcl side in ``__del__``.
Python runs ``__del__`` on whatever thread the garbage collector happens to run on; a Tcl call from another
thread is handed to the Tk thread and *waits* for it. When the Tk thread is at that moment waiting for that
thread (a ``join``, a lock it holds, a result), both wait forever: Settings froze this way (a background
thread collected the widgets of the page just left while the Tk thread waited for it).

``install()`` wraps those finalizers: on the Tk thread they run as before; on any other thread the object is
kept and its finalizer runs at the next ``drain()`` on the Tk thread (the app calls it every tick).
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

_pending: queue.SimpleQueue[tuple[Callable[[Any], None], Any]] = queue.SimpleQueue()
_tk_thread = 0
_installed = False


def _wrap(cls: type, attr: str = "__del__") -> None:
    orig = cls.__dict__.get(attr)
    if orig is None or getattr(orig, "_tksafe", False):
        return

    def finalizer(self: Any) -> None:
        if threading.get_ident() == _tk_thread:
            orig(self)
        else:
            _pending.put((orig, self))       # keeps the object alive until the Tk thread deletes it

    finalizer._tksafe = True  # type: ignore[attr-defined]
    finalizer.__doc__ = getattr(orig, "__doc__", None)
    setattr(cls, attr, finalizer)


def install() -> None:
    """Call once on the Tk thread, before Tk widgets exist."""
    global _tk_thread, _installed
    _tk_thread = threading.get_ident()
    if _installed:
        return
    _installed = True
    import tkinter
    import tkinter.font

    _wrap(tkinter.Variable)
    _wrap(tkinter.Image)
    _wrap(tkinter.font.Font)
    try:
        from PIL import ImageTk

        _wrap(ImageTk.PhotoImage)
        _wrap(ImageTk.BitmapImage)
    except Exception:  # noqa: BLE001 - Pillow without Tk support
        log.debug("ImageTk finalizers not wrapped", exc_info=True)


def drain(limit: int = 500) -> int:
    """Run finalizers that other threads handed over (Tk thread). Returns how many ran."""
    if threading.get_ident() != _tk_thread:
        return 0
    n = 0
    while n < limit:
        try:
            fn, obj = _pending.get_nowait()
        except queue.Empty:
            break
        n += 1
        try:
            fn(obj)
        except Exception:  # noqa: BLE001 - the interpreter may be gone (closing)
            log.debug("deferred tkinter finalizer failed", exc_info=True)
        del obj
    return n


def pending() -> int:
    return _pending.qsize()
