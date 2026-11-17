"""Form building blocks for the settings window, built on customtkinter and the design tokens.

Every control takes a ``get`` / ``set`` pair, so the same helpers edit settings keys and template fields.
Each helper returns a refresh function that re-reads the value (used when the value changes elsewhere).
Layouts mirror for right-to-left interface languages.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser
from typing import Any, Callable, Sequence

import customtkinter as ctk

from dualyrics import fonts
from dualyrics.i18n import is_rtl, t
from dualyrics.ui import icons
from dualyrics.ui import tokens as T

Getter = Callable[[], Any]
Setter = Callable[[Any], None]
Refresh = Callable[[], None]


class Kit:
    """Fonts, icons and widget factories for one settings window."""

    def __init__(self, root: Any) -> None:
        self.root = root
        self.rtl = is_rtl()
        self._fonts: dict[str, ctk.CTkFont] = {}
        self._icons: dict[tuple[str, int, str], ctk.CTkImage] = {}
        self.ui_family = fonts.ui_family(self.rtl)
        self.display_family = fonts.ui_family(self.rtl, "display")
        self.mono_family = fonts.ui_family(False, "mono")

    # -- basics
    @property
    def anchor(self) -> str:
        return "e" if self.rtl else "w"

    @property
    def justify(self) -> str:
        return "right" if self.rtl else "left"

    @property
    def start(self) -> str:
        return "right" if self.rtl else "left"

    @property
    def end(self) -> str:
        return "left" if self.rtl else "right"

    def font(self, role: str) -> ctk.CTkFont:
        f = self._fonts.get(role)
        if f is None:
            size, weight = T.TYPE[role]
            fam = self.mono_family if role == "mono" else (
                self.display_family if role in ("display", "title") else self.ui_family)
            if weight >= 600 and weight < 700:
                semi = fonts.pick(fam, 600)[0]
                f = ctk.CTkFont(family=semi, size=size, weight="normal" if semi != fam else "bold")
            else:
                f = ctk.CTkFont(family=fam, size=size, weight="bold" if weight >= 700 else "normal")
            self._fonts[role] = f
        return f

    def icon(self, name: str, size: int = T.ICON, token: tuple[str, str] = T.TEXT_SECONDARY) -> ctk.CTkImage:
        key = (name, size, token[0] + token[1])
        img = self._icons.get(key)
        if img is None:
            img = ctk.CTkImage(light_image=icons.render(name, size * 2, token[0]),
                               dark_image=icons.render(name, size * 2, token[1]), size=(size, size))
            self._icons[key] = img
        return img

    def label(self, parent: Any, text: str, role: str = "body", color: tuple[str, str] = T.TEXT_PRIMARY,
              wrap: int = 0, reserve: int = 8, **kw: Any) -> ctk.CTkLabel:
        """A label in the type scale. With ``wrap``, the text wraps at the width of ``parent`` (minus
        ``reserve`` px for neighbours), so columns that shrink with the window never cut text off."""
        lbl = ctk.CTkLabel(parent, text=text, font=self.font(role), text_color=color, anchor=self.anchor,
                           justify=self.justify, wraplength=wrap, **kw)
        if wrap:
            def fit(e: Any) -> None:
                if e.widget is not parent:
                    return
                try:
                    scale = lbl._get_widget_scaling()  # noqa: SLF001
                    w = max(80, int(e.width / scale) - reserve)
                    if abs(w - int(lbl.cget("wraplength"))) > 6:
                        lbl.configure(wraplength=w)
                except Exception:
                    pass

            tk.Misc.bind(parent, "<Configure>", fit, "+")
        return lbl

    def button(self, parent: Any, text: str, command: Callable[[], Any], kind: str = "secondary",
               icon: str | None = None, width: int = 0) -> ctk.CTkButton:
        """``primary`` (accent, 36 px, one per view), ``secondary`` (outlined, 32 px), ``danger``, ``ghost``."""
        common: dict[str, Any] = dict(text=text, command=command, corner_radius=T.RADIUS_MD,
                                      font=self.font("body.strong" if kind == "primary" else "body"))
        if width:
            common["width"] = width
        if icon:
            tok = T.ON_ACCENT if kind == "primary" else (T.DANGER if kind == "danger" else T.TEXT_PRIMARY)
            common.update(image=self.icon(icon, T.ICON, tok), compound=self.start)
        if kind == "primary":
            b = ctk.CTkButton(parent, height=T.CONTROL_LG_H, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                              text_color=T.ON_ACCENT, text_color_disabled=T.TEXT_DISABLED, **common)
        else:
            b = ctk.CTkButton(parent, height=T.CONTROL_H, fg_color="transparent", hover_color=T.BG_HOVER,
                              border_width=0 if kind == "ghost" else 1, border_color=T.BORDER_STRONG,
                              text_color=T.DANGER if kind == "danger" else T.TEXT_PRIMARY,
                              text_color_disabled=T.TEXT_DISABLED, **common)
        focus_ring(b)
        return b

    # -- page structure
    def section_header(self, parent: Any, title: str, caption: str) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        self.label(f, title, "title").pack(fill="x")
        if caption:
            self.label(f, caption, "caption", T.TEXT_SECONDARY, wrap=600).pack(fill="x", pady=(T.SPACE[2], 0))
        return f

    def group(self, parent: Any, title: str | None = None, caption: str | None = None) -> ctk.CTkFrame:
        """A setting group card. Returns the inner frame that rows are packed into."""
        card = ctk.CTkFrame(parent, fg_color=T.BG_SURFACE, corner_radius=T.RADIUS_LG, border_width=1,
                            border_color=T.BORDER_SUBTLE)
        card.pack(fill="x", pady=(0, T.SPACE[7]))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=T.SPACE[6], pady=(T.SPACE[5], T.SPACE[5]))
        if title:
            self.label(inner, title, "subtitle").pack(fill="x")
        if caption:
            self.label(inner, caption, "caption", T.TEXT_SECONDARY, wrap=560).pack(fill="x", pady=(T.SPACE[2], 0))
        inner.card = card  # type: ignore[attr-defined]
        return inner

    def row(self, group: Any, label: str, help: str = "", indent: bool = False) -> ctk.CTkFrame:
        """A setting row: label (and help under it) at the start, the control at the end. Returns the frame the
        control goes into."""
        r = ctk.CTkFrame(group, fg_color="transparent")
        pad = (T.SPACE[5], 0) if indent and not self.rtl else ((0, T.SPACE[5]) if indent else (0, 0))
        r.pack(fill="x", pady=(T.SPACE[4], 0), padx=pad)
        lab_col, ctl_col = (1, 0) if self.rtl else (0, 1)
        r.grid_columnconfigure(lab_col, weight=1)
        left = ctk.CTkFrame(r, fg_color="transparent")
        left.grid(row=0, column=lab_col, sticky="nsew", padx=(T.SPACE[4], 0) if self.rtl else (0, T.SPACE[4]))
        self.label(left, label, "body").pack(fill="x")
        if help:
            self.label(left, help, "caption", T.TEXT_SECONDARY, wrap=340).pack(fill="x", pady=(T.SPACE[1], 0))
        holder = ctk.CTkFrame(r, fg_color="transparent")
        holder.grid(row=0, column=ctl_col, sticky="e" if not self.rtl else "w")
        r.label_frame = left  # type: ignore[attr-defined]
        return holder

    def note(self, group: Any, text: str, kind: str = "info") -> ctk.CTkFrame:
        tok = {"warning": T.WARNING, "danger": T.DANGER, "success": T.SUCCESS}.get(kind, T.TEXT_SECONDARY)
        icon = {"warning": "warning", "danger": "error", "success": "ok"}.get(kind, "about")
        f = ctk.CTkFrame(group, fg_color="transparent")
        f.pack(fill="x", pady=(T.SPACE[4], 0))
        ctk.CTkLabel(f, text="", image=self.icon(icon, T.ICON, tok), width=20).pack(side=self.start, anchor="n")
        self.label(f, text, "caption", T.TEXT_SECONDARY, wrap=540, reserve=44).pack(
            side=self.start, fill="x", expand=True, padx=T.SPACE[3])
        return f

    # -- controls
    def switch(self, group: Any, label: str, get: Getter, set_: Setter, help: str = "",
               indent: bool = False) -> Refresh:
        holder = self.row(group, label, help, indent)
        var = tk.BooleanVar(value=bool(get()))
        sw = ctk.CTkSwitch(holder, text="", variable=var, width=44, switch_width=40, switch_height=20,
                           progress_color=T.ACCENT, fg_color=T.BORDER_STRONG, button_color=T.TEXT_PRIMARY,
                           button_hover_color=T.TEXT_PRIMARY, command=lambda: set_(var.get()))
        sw.pack()
        return lambda: var.set(bool(get()))

    def option(self, group: Any, label: str, options: Sequence[tuple[Any, str]], get: Getter, set_: Setter,
               help: str = "", width: int = 240, indent: bool = False) -> Refresh:
        holder = self.row(group, label, help, indent)
        return self.option_menu(holder, options, get, set_, width)

    def option_menu(self, holder: Any, options: Sequence[tuple[Any, str]], get: Getter, set_: Setter,
                    width: int = 240) -> Refresh:
        to_label = {repr(v): lbl for v, lbl in options}
        to_value = {lbl: v for v, lbl in options}
        var = tk.StringVar(value=to_label.get(repr(get()), options[0][1] if options else ""))

        def chosen(lbl: str) -> None:
            set_(to_value[lbl])
            var.set(to_label.get(repr(get()), lbl))

        om = ctk.CTkOptionMenu(holder, values=[lbl for _, lbl in options], variable=var, width=width,
                               height=T.CONTROL_H, corner_radius=T.RADIUS_MD, fg_color=T.BG_RAISED,
                               button_color=T.BG_HOVER, button_hover_color=T.BORDER_SUBTLE,
                               text_color=T.TEXT_PRIMARY, font=self.font("body"),
                               dropdown_font=self.font("body"), dynamic_resizing=False, command=chosen,
                               anchor=self.anchor)
        om.pack(side=self.end)
        return lambda: var.set(to_label.get(repr(get()), var.get()))

    def segmented(self, group: Any, label: str, options: Sequence[tuple[Any, str]], get: Getter, set_: Setter,
                  help: str = "", indent: bool = False) -> Refresh:
        holder = self.row(group, label, help, indent)
        return self.segmented_in(holder, options, get, set_)

    def segmented_in(self, holder: Any, options: Sequence[tuple[Any, str]], get: Getter, set_: Setter) -> Refresh:
        opts = list(options)
        if self.rtl:
            opts.reverse()
        to_label = {repr(v): lbl for v, lbl in opts}
        to_value = {lbl: v for v, lbl in opts}
        seg = ctk.CTkSegmentedButton(holder, values=[lbl for _, lbl in opts], height=T.CONTROL_H,
                                     corner_radius=T.RADIUS_MD, font=self.font("body"), fg_color=T.BG_RAISED,
                                     selected_color=T.ACCENT, selected_hover_color=T.ACCENT_HOVER,
                                     unselected_color=T.BG_RAISED, unselected_hover_color=T.BG_HOVER,
                                     text_color=T.TEXT_PRIMARY, command=lambda lbl: set_(to_value[lbl]))
        seg.set(to_label.get(repr(get()), ""))
        seg.pack(side=self.end)
        _seg_text_colors(seg)
        return lambda: (seg.set(to_label.get(repr(get()), "")), _seg_text_colors(seg))

    def slider(self, group: Any, label: str, lo: float, hi: float, get: Getter, set_: Setter, step: float = 1,
               unit: str = "", fmt: Callable[[float], str] | None = None, help: str = "",
               indent: bool = False, parse: Callable[[str], float] | None = None) -> Refresh:
        holder = self.row(group, label, help, indent)
        return self.slider_in(holder, lo, hi, get, set_, step, unit, fmt, parse)

    def slider_in(self, holder: Any, lo: float, hi: float, get: Getter, set_: Setter, step: float = 1,
                  unit: str = "", fmt: Callable[[float], str] | None = None,
                  parse: Callable[[str], float] | None = None, width: int = 200) -> Refresh:
        integer = float(step).is_integer() and float(lo).is_integer()
        fmt = fmt or (lambda v: (f"{int(round(v))}" if integer else f"{v:g}") + (f" {unit}" if unit else ""))
        steps = max(1, int(round((hi - lo) / step)))

        def snap(v: float) -> float:
            v = lo + round((v - lo) / step) * step
            v = min(hi, max(lo, v))
            return int(round(v)) if integer else round(v, 4)

        val = ctk.CTkEntry(holder, width=72, height=T.CONTROL_H, font=self.font("mono"), justify="center",
                           corner_radius=T.RADIUS_MD, border_width=1, border_color=T.BORDER_STRONG,
                           fg_color=T.BG_RAISED, text_color=T.TEXT_PRIMARY)
        focus_ring(val)
        # right-to-left: the fill still grows from the start (right) edge
        kw: dict[str, Any] = dict(from_=lo, to=hi, number_of_steps=steps, width=width, height=18,
                                  button_color=T.TEXT_PRIMARY, button_hover_color=T.ACCENT_HOVER)
        if self.rtl:
            kw.update(from_=hi, to=lo, progress_color=T.BORDER_STRONG, fg_color=T.ACCENT)
        else:
            kw.update(progress_color=T.ACCENT, fg_color=T.BORDER_STRONG)

        def show(v: float) -> None:
            val.delete(0, "end")
            val.insert(0, fmt(v))

        def moved(v: float) -> None:
            v = snap(v)
            show(v)
            set_(v)

        sl = ctk.CTkSlider(holder, command=moved, **kw)
        sl.set(get())
        show(get())

        def typed(_e: Any = None) -> None:
            raw = val.get().strip().replace(unit, "").replace("%", "").strip()
            try:
                v = parse(raw) if parse else float(raw)
            except ValueError:
                show(get())
                return
            v = snap(v)
            sl.set(v)
            show(v)
            set_(v)

        val.bind("<Return>", typed)
        val.bind("<FocusOut>", typed)
        if self.rtl:
            val.pack(side="left")
            sl.pack(side="left", padx=(T.SPACE[3], 0))
        else:
            sl.pack(side="left", padx=(0, T.SPACE[3]))
            val.pack(side="left")

        def refresh() -> None:
            sl.set(get())
            show(get())

        return refresh

    def color(self, group: Any, label: str, get: Getter, set_: Setter, help: str = "",
              indent: bool = False) -> Refresh:
        holder = self.row(group, label, help, indent)
        return self.color_in(holder, get, set_, label)

    def color_in(self, holder: Any, get: Getter, set_: Setter, title: str = "") -> Refresh:
        hexe = ctk.CTkEntry(holder, width=92, height=T.CONTROL_H, font=self.font("mono"), justify="center",
                            corner_radius=T.RADIUS_MD, border_width=1, border_color=T.BORDER_STRONG,
                            fg_color=T.BG_RAISED, text_color=T.TEXT_PRIMARY)
        focus_ring(hexe)

        def pick() -> None:
            res = colorchooser.askcolor(color=get(), parent=holder.winfo_toplevel(), title=title)
            if res and res[1]:
                set_(res[1].upper())
                refresh()

        sw = ctk.CTkButton(holder, text="", width=32, height=32, corner_radius=T.RADIUS_MD, fg_color=get(),
                           hover_color=get(), border_width=1, border_color=T.BORDER_STRONG, command=pick)
        focus_ring(sw)

        def typed(_e: Any = None) -> None:
            v = hexe.get().strip()
            if not v.startswith("#"):
                v = "#" + v
            if len(v) == 7 and all(c in "0123456789abcdefABCDEF" for c in v[1:]):
                set_(v.upper())
            refresh()

        hexe.bind("<Return>", typed)
        hexe.bind("<FocusOut>", typed)
        if self.rtl:
            hexe.pack(side="left")
            sw.pack(side="left", padx=(T.SPACE[3], 0))
        else:
            sw.pack(side="left", padx=(0, T.SPACE[3]))
            hexe.pack(side="left")

        def refresh() -> None:
            c = get()
            sw.configure(fg_color=c, hover_color=c)
            hexe.delete(0, "end")
            hexe.insert(0, c)

        refresh()
        return refresh

    def entry(self, group: Any, label: str, get: Getter, set_: Setter, help: str = "", width: int = 240,
              placeholder: str = "", mono: bool = False, indent: bool = False) -> Refresh:
        holder = self.row(group, label, help, indent)
        return self.entry_in(holder, get, set_, width, placeholder, mono)

    def entry_in(self, holder: Any, get: Getter, set_: Setter, width: int = 240, placeholder: str = "",
                 mono: bool = False) -> Refresh:
        e = ctk.CTkEntry(holder, width=width, height=T.CONTROL_H, corner_radius=T.RADIUS_MD, border_width=1,
                         border_color=T.BORDER_STRONG, fg_color=T.BG_RAISED, text_color=T.TEXT_PRIMARY,
                         placeholder_text=placeholder or None, font=self.font("mono" if mono else "body"),
                         justify="right" if self.rtl and not mono else "left")
        focus_ring(e)
        if get():
            e.insert(0, str(get()))
        e.bind("<Return>", lambda _e: set_(e.get()))
        e.bind("<FocusOut>", lambda _e: set_(e.get()))
        e.pack(side=self.end)

        def refresh() -> None:
            if e.focus_get() is not e._entry:  # do not fight the user while typing
                e.delete(0, "end")
                if get():
                    e.insert(0, str(get()))

        e.flush = lambda: set_(e.get())  # type: ignore[attr-defined]
        refresh.widget = e  # type: ignore[attr-defined]
        return refresh

    def textbox(self, group: Any, value: str, height: int = 90) -> ctk.CTkTextbox:
        tb = ctk.CTkTextbox(group, height=height, wrap="word", corner_radius=T.RADIUS_MD, border_width=1,
                            border_color=T.BORDER_STRONG, fg_color=T.BG_RAISED, text_color=T.TEXT_PRIMARY,
                            font=self.font("body"))
        tb.insert("1.0", value)
        tb.pack(fill="x", pady=(T.SPACE[3], 0))
        return tb

    def actions(self, group: Any) -> ctk.CTkFrame:
        f = ctk.CTkFrame(group, fg_color="transparent")
        f.pack(fill="x", pady=(T.SPACE[5], 0))
        return f


def _seg_text_colors(seg: ctk.CTkSegmentedButton) -> None:
    """Selected segment uses ``on.accent`` text (CTkSegmentedButton has one text colour for all)."""
    try:
        current = seg.get()
        for name, btn in seg._buttons_dict.items():  # noqa: SLF001 - no public API for this
            btn.configure(text_color=T.ON_ACCENT if name == current else T.TEXT_PRIMARY)
    except Exception:
        pass


def focus_ring(widget: Any) -> None:
    """Visible 2 px keyboard focus ring (customtkinter shows none by default; DESIGN.md 1.4)."""
    try:
        normal_w = widget.cget("border_width")
        normal_c = widget.cget("border_color")
    except Exception:
        return
    inner = getattr(widget, "_entry", None) or getattr(widget, "_canvas", None)
    if inner is None:
        return
    if not isinstance(widget, ctk.CTkEntry):
        try:
            inner.configure(takefocus=1)
            inner.bind("<space>", lambda _e: widget.invoke() if hasattr(widget, "invoke") else None)
            inner.bind("<Return>", lambda _e: widget.invoke() if hasattr(widget, "invoke") else None)
        except Exception:
            return

    def on(_e: Any) -> None:
        try:
            widget.configure(border_width=2, border_color=T.FOCUS)
        except Exception:
            pass

    def off(_e: Any) -> None:
        try:
            widget.configure(border_width=normal_w, border_color=normal_c)
        except Exception:
            pass

    inner.bind("<FocusIn>", on, add="+")
    inner.bind("<FocusOut>", off, add="+")


class Toast:
    """Bottom-of-content message with an optional action ("Undo"); hides after 5 s, stays while hovered."""

    def __init__(self, kit: Kit, parent: Any) -> None:
        self.kit = kit
        self.frame = ctk.CTkFrame(parent, fg_color=T.BG_RAISED, corner_radius=T.RADIUS_LG, border_width=1,
                                  border_color=T.BORDER_SUBTLE)
        self._job: str | None = None
        self._hover = False
        self.frame.bind("<Enter>", lambda _e: setattr(self, "_hover", True))
        self.frame.bind("<Leave>", lambda _e: setattr(self, "_hover", False))

    def show(self, text: str, kind: str = "info", action: tuple[str, Callable[[], None]] | None = None) -> None:
        for w in self.frame.winfo_children():
            w.destroy()
        k = self.kit
        tok = {"warning": T.WARNING, "danger": T.DANGER, "success": T.SUCCESS}.get(kind, T.ACCENT)
        icon = {"warning": "warning", "danger": "error", "success": "ok"}.get(kind, "about")
        inner = ctk.CTkFrame(self.frame, fg_color="transparent")
        inner.pack(fill="x", padx=T.SPACE[5], pady=T.SPACE[3])
        ctk.CTkLabel(inner, text="", image=k.icon(icon, T.ICON, tok), width=20).pack(side=k.start)
        k.label(inner, text, "body", wrap=420, reserve=160 if action else 50).pack(side=k.start, padx=T.SPACE[3])
        if action:
            label, fn = action

            def run() -> None:
                self.hide()
                fn()

            k.button(inner, label, run, kind="ghost").pack(side=k.end)
        self.frame.place(relx=0.5, rely=1.0, y=-T.SPACE[5], anchor="s")
        self.frame.lift()
        if self._job:
            self.frame.after_cancel(self._job)
        self._job = self.frame.after(5000, self._maybe_hide)

    def _maybe_hide(self) -> None:
        if self._hover:
            self._job = self.frame.after(1000, self._maybe_hide)
        else:
            self.hide()

    def hide(self) -> None:
        self._job = None
        try:
            self.frame.place_forget()
        except tk.TclError:
            pass


def contrast_text(hex_color: str) -> str:
    try:
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"
    except (ValueError, IndexError):
        return "#FFFFFF"


__all__ = ["Kit", "Toast", "focus_ring", "contrast_text", "t"]
