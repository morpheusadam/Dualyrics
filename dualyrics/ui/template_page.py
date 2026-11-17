"""Settings › Templates: preset gallery, template editor (every property), live preview, duplicate / rename /
delete / reset / import / export (docs/DESIGN.md 1.5 to 1.7 and part 2)."""

from __future__ import annotations

import copy
import logging
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk
from PIL import ImageTk

from dualyrics import fonts, render, templates
from dualyrics.i18n import language, t
from dualyrics.ui import preview, tokens as T
from dualyrics.ui.widgets import Kit, focus_ring

if TYPE_CHECKING:
    from dualyrics.settings_window import SettingsWindow

log = logging.getLogger(__name__)

WEIGHTS = (100, 200, 300, 400, 500, 600, 700, 800, 900)
MOTIONS = ("none", "fade", "slide_up", "slide_down", "fade_slide_up", "fade_slide_down")
EASINGS = ("linear", "ease_out", "ease_in_out", "ease_out_back")
CARD_W, CARD_H = 176, 136


def _key(cur: Any, part: str) -> Any:
    return int(part) if isinstance(cur, list) else part


def get_path(doc: dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        cur = cur[_key(cur, part)]
    return cur


def set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = doc
    for part in parts[:-1]:
        cur = cur[_key(cur, part)]
    cur[_key(cur, parts[-1])] = value


class TemplatePage:
    def __init__(self, sw: "SettingsWindow", kit: Kit, content: Any, side: Any) -> None:
        self.sw = sw
        self.app = sw.app
        self.store: templates.TemplateStore = sw.app.templates
        self.kit = kit
        self.content = content          # scrollable content column
        self.side = side                # preview column
        self.renderer = render.Renderer()
        self.doc: dict[str, Any] = self.store.resolve(self.sw.s["template"])
        self.line_kind = "original"
        self._save_job: str | None = None
        self._preview_job: str | None = None
        self._thumbs: dict[tuple[str, str, str], ctk.CTkImage] = {}
        self._cards: dict[str, ctk.CTkFrame] = {}
        self._groups: dict[str, dict[str, Any]] = {}
        self._photo: Any = None
        self._anim_job: str | None = None
        self._deleted: dict[str, Any] | None = None
        self.build()

    # ------------------------------------------------------------------ model
    @property
    def lang(self) -> str:
        return language()

    def active_id(self) -> str:
        return self.sw.s["template"]

    def _edit(self, path: str, value: Any, rebuild: bool = False) -> None:
        """Change one template field. A built-in preset is copied first, so presets stay intact."""
        if self.doc.get("builtin"):
            src = self.doc
            self.doc = self.store.fork(src["id"], names=self._names_for(src))
            self.sw.s.update(template=self.doc["id"])
            self.sw.toast(t("tpl_forked", name=templates.display_name(src, self.lang, t)))
            self._refresh_gallery()
            self._refresh_actions()
        set_path(self.doc, path, value)
        self._schedule_save()
        self._schedule_preview()
        if rebuild:
            self._rebuild_open_groups()

    def _names_for(self, src: dict[str, Any]) -> dict[str, str]:
        if src.get("builtin"):
            prev = language()
            names = {}
            for lg in ("en", "fa"):
                from dualyrics import i18n

                i18n.set_language(lg)
                names[lg] = i18n.t(f"tpl_{src['id']}").replace(i18n.RLE, "").replace(i18n.PDF, "")
            from dualyrics import i18n

            i18n.set_language(prev)
            return names
        return dict(src.get("name") or {"en": src["id"]})

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            self.content.after_cancel(self._save_job)
        self._save_job = self.content.after(250, self._flush)

    def _flush(self) -> None:
        self._save_job = None
        if self.doc.get("builtin"):
            return
        try:
            self.doc = self.store.save(self.doc)
        except OSError as e:
            self.sw.toast(t("tpl_save_failed", err=str(e)[:120]), "danger")
            return
        self._refresh_card(self.doc["id"])

    def flush(self) -> None:
        if self._save_job is not None:
            self.content.after_cancel(self._save_job)
            self._flush()

    def on_template_setting(self) -> None:
        """The active template changed elsewhere (overlay menu, profile)."""
        if self.active_id() != self.doc.get("id"):
            self.flush()
            self.doc = self.store.resolve(self.active_id())
            self._refresh_gallery()
            self._refresh_actions()
            self._rebuild_open_groups()
            self._schedule_preview()

    # ------------------------------------------------------------------ layout
    def build(self) -> None:
        k = self.kit
        c = self.content
        gal = k.group(c, t("tpl_gallery"), t("tpl_gallery_hint"))
        self.gallery = ctk.CTkFrame(gal, fg_color="transparent")
        self.gallery.pack(fill="x", pady=(T.SPACE[4], 0))
        self._cols = 0
        self.gallery.bind("<Configure>", lambda _e: self._grid_cards(), add="+")
        self.actions = ctk.CTkFrame(gal, fg_color="transparent")
        self.actions.pack(fill="x", pady=(T.SPACE[5], 0))
        self._refresh_gallery()
        self._refresh_actions()
        self._group("text", t("tpl_section_text"), self._build_text, open_=True)
        self._group("box", t("tpl_section_box"), self._build_box)
        self._group("layout", t("tpl_section_layout"), self._build_layout)
        self._group("karaoke", t("tpl_section_karaoke"), self._build_karaoke)
        self._group("motion", t("tpl_section_motion"), self._build_motion)
        self._group("timing", t("tpl_section_timing"), self._build_timing)
        k.note(c, t("tpl_fullscreen_note"))
        self._build_preview()

    def _group(self, key: str, title: str, builder: Callable[[Any], None], open_: bool = False) -> None:
        """Collapsible setting group; its rows are built the first time it opens (keeps the page fast)."""
        k = self.kit
        card = ctk.CTkFrame(self.content, fg_color=T.BG_SURFACE, corner_radius=T.RADIUS_LG, border_width=1,
                            border_color=T.BORDER_SUBTLE)
        card.pack(fill="x", pady=(0, T.SPACE[5]))
        head = ctk.CTkButton(card, text=title, anchor=k.anchor, font=k.font("subtitle"), fg_color="transparent",
                             hover_color=T.BG_HOVER, text_color=T.TEXT_PRIMARY, height=44,
                             corner_radius=T.RADIUS_LG, compound=k.end,
                             image=k.icon("chevron_left" if k.rtl else "chevron_right", T.ICON, T.TEXT_SECONDARY))
        head.pack(fill="x", padx=2, pady=2)
        focus_ring(head)
        body = ctk.CTkFrame(card, fg_color="transparent")
        g = {"card": card, "head": head, "body": body, "builder": builder, "open": False, "built": False}
        self._groups[key] = g
        head.configure(command=lambda: self._toggle(key))
        if open_:
            self._toggle(key)

    def _toggle(self, key: str, force: bool | None = None) -> None:
        g = self._groups[key]
        g["open"] = (not g["open"]) if force is None else force
        k = self.kit
        if g["open"]:
            if not g["built"]:
                g["builder"](g["body"])
                g["built"] = True
            g["body"].pack(fill="x", padx=T.SPACE[6], pady=(0, T.SPACE[5]))
            g["head"].configure(image=k.icon("chevron_left" if k.rtl else "chevron_right", T.ICON, T.ACCENT))
        else:
            g["body"].pack_forget()
            g["head"].configure(image=k.icon("chevron_left" if k.rtl else "chevron_right", T.ICON, T.TEXT_SECONDARY))

    def _rebuild_open_groups(self) -> None:
        for g in self._groups.values():
            if g["built"]:
                for w in g["body"].winfo_children():
                    w.destroy()
                g["builder"](g["body"])

    def _rebuild(self, key: str) -> None:
        g = self._groups.get(key)
        if g and g["built"]:
            for w in g["body"].winfo_children():
                w.destroy()
            g["builder"](g["body"])

    # ------------------------------------------------------------------ gallery
    def _thumb(self, doc: dict[str, Any]) -> ctk.CTkImage:
        key = (doc["id"], templates.style_hash(doc), self.sw.effective_target())
        img = self._thumbs.get(key)
        if img is None:
            pil = preview.thumbnail(self.renderer, doc, self.sw.effective_target())
            img = ctk.CTkImage(light_image=pil, dark_image=pil, size=preview.THUMB_SIZE)
            self._thumbs[key] = img
        return img

    def _refresh_gallery(self) -> None:
        for w in self.gallery.winfo_children():
            w.destroy()
        self._cards = {}
        for doc in self.store.all():
            self._card(doc)
        self._cols = 0
        self._grid_cards()
        self._mark_selected()

    def _grid_cards(self) -> None:
        """As many card columns as fit the content column (it narrows when the preview is shown)."""
        k = self.kit
        try:
            scale = self.gallery._get_widget_scaling()  # noqa: SLF001
            width = self.gallery.winfo_width() / scale
        except Exception:
            width = 0
        cols = max(1, int((width + T.SPACE[4]) // (CARD_W + T.SPACE[4]))) if width > 50 else 2
        if cols == self._cols:
            return
        self._cols = cols
        for c in range(cols + 1):
            self.gallery.grid_columnconfigure(c, weight=0)
        if k.rtl:  # cards hug the right edge
            self.gallery.grid_columnconfigure(0, weight=1)
        for i, card in enumerate(self._cards.values()):
            r, col = divmod(i, cols)
            if k.rtl:
                col = cols - col
            card.grid(row=r, column=col, padx=(0, T.SPACE[4]) if not k.rtl else (T.SPACE[4], 0),
                      pady=(0, T.SPACE[4]), sticky="n")

    def _card(self, doc: dict[str, Any]) -> ctk.CTkFrame:
        k = self.kit
        tid = doc["id"]
        card = ctk.CTkFrame(self.gallery, width=CARD_W, height=CARD_H, corner_radius=T.RADIUS_XL,
                            fg_color=T.BG_RAISED, border_width=1, border_color=T.BORDER_SUBTLE)
        card.pack_propagate(False)
        img = ctk.CTkLabel(card, text="", image=self._thumb(doc), width=160, height=90, corner_radius=T.RADIUS_MD)
        img.pack(pady=(7, 2))
        name = templates.display_name(doc, self.lang, t)
        title = k.label(card, _short(name, 22), "body.strong", height=20)
        title.pack(fill="x", padx=10)
        use = templates.description(doc, self.lang, t)
        sub = k.label(card, _short(use, 26), "caption", T.TEXT_SECONDARY, height=16)
        sub.pack(fill="x", padx=10)
        for w in (card, img, title, sub):
            w.bind("<Button-1>", lambda _e, i=tid: self.select(i))
            w.configure(cursor="hand2")
        card.bind("<Enter>", lambda _e, c=card, i=tid: c.configure(fg_color=T.BG_HOVER) if i != self.active_id()
                  else None)
        card.bind("<Leave>", lambda _e, c=card, i=tid: c.configure(
            fg_color=T.BG_SELECTED if i == self.active_id() else T.BG_RAISED))
        self._cards[tid] = card
        card.check = None  # type: ignore[attr-defined]
        return card

    def _mark_selected(self) -> None:
        active = self.active_id()
        k = self.kit
        for tid, card in self._cards.items():
            sel = tid == active
            card.configure(border_width=2 if sel else 1, border_color=T.ACCENT if sel else T.BORDER_SUBTLE,
                           fg_color=T.BG_SELECTED if sel else T.BG_RAISED)
            chk = getattr(card, "check", None)
            if sel and chk is None:
                chk = ctk.CTkLabel(card, text="", image=k.icon("ok", 20, T.ACCENT), width=22, height=22,
                                   fg_color="transparent")
                chk.place(relx=0.04 if k.rtl else 0.96, rely=0.04, anchor="ne" if not k.rtl else "nw")
                card.check = chk  # type: ignore[attr-defined]
            elif not sel and chk is not None:
                chk.destroy()
                card.check = None  # type: ignore[attr-defined]

    def _refresh_card(self, tid: str) -> None:
        card = self._cards.get(tid)
        doc = self.store.get(tid)
        if card is None or doc is None:
            self._refresh_gallery()
            return
        for w in card.winfo_children():
            if isinstance(w, ctk.CTkLabel) and w.cget("image") is not None and w is not getattr(card, "check", None):
                w.configure(image=self._thumb(doc))
                break

    def select(self, tid: str) -> None:
        self.flush()
        self.app.apply_template(tid, ask=True, parent=self.sw.win)
        self.doc = self.store.resolve(self.active_id())
        self._mark_selected()
        self._refresh_actions()
        self._rebuild_open_groups()
        self._schedule_preview()

    # ------------------------------------------------------------------ actions
    def _refresh_actions(self) -> None:
        for w in self.actions.winfo_children():
            w.destroy()
        k = self.kit
        doc = self.doc
        user = not doc.get("builtin")
        row1 = ctk.CTkFrame(self.actions, fg_color="transparent")
        row1.pack(fill="x")
        side = k.start
        k.button(row1, t("tpl_duplicate"), self.duplicate, icon="copy").pack(side=side, padx=(0, 8) if not k.rtl
                                                                              else (8, 0))
        if user:
            k.button(row1, t("tpl_rename"), self.rename, icon="edit").pack(side=side, padx=(0, 8) if not k.rtl
                                                                           else (8, 0))
            if doc.get("based_on"):
                k.button(row1, t("tpl_reset"), self.reset).pack(side=side, padx=(0, 8) if not k.rtl else (8, 0))
            k.button(row1, t("tpl_delete"), self.delete, kind="danger", icon="trash").pack(side=side)
        row2 = ctk.CTkFrame(self.actions, fg_color="transparent")
        row2.pack(fill="x", pady=(T.SPACE[3], 0))
        k.button(row2, t("tpl_import"), self.import_, icon="import").pack(side=side, padx=(0, 8) if not k.rtl
                                                                         else (8, 0))
        k.button(row2, t("tpl_export"), self.export, icon="export").pack(side=side)
        if doc.get("builtin"):
            k.label(self.actions, t("tpl_builtin_hint"), "caption", T.TEXT_SECONDARY, wrap=560).pack(
                fill="x", pady=(T.SPACE[3], 0))
        prof_tpl = self.app.effective_cfg().get("template")
        if prof_tpl and prof_tpl != self.active_id():
            other = self.store.resolve(prof_tpl)
            k.note(self.actions, t("tpl_profile_note", name=templates.display_name(other, self.lang, t)), "warning")

    def duplicate(self) -> None:
        self.flush()
        src = self.doc
        new = self.store.fork(src["id"], names=self._names_for(src))
        self.sw.s.update(template=new["id"])
        self.doc = new
        self._refresh_gallery()
        self._refresh_actions()
        self.sw.toast(t("tpl_duplicated", name=templates.display_name(new, self.lang, t)), "success")

    def rename(self) -> None:
        if self.doc.get("builtin"):
            return
        self.flush()
        dlg = ctk.CTkInputDialog(text=t("tpl_rename_prompt"), title=t("tpl_rename"))
        try:
            dlg.after(50, lambda: dlg.lift())
        except tk.TclError:
            pass
        name = dlg.get_input()
        if name and name.strip():
            d = self.store.rename(self.doc["id"], name.strip(), self.lang)
            if d:
                self.doc = d
            self._refresh_gallery()

    def reset(self) -> None:
        self.flush()
        d = self.store.reset_to_preset(self.doc["id"])
        if d:
            self.doc = d
            self._rebuild_open_groups()
            self._schedule_preview()
            self._refresh_card(d["id"])

    def delete(self) -> None:
        self.flush()
        doc = self.doc
        if doc.get("builtin"):
            return
        removed = self.store.delete(doc["id"])
        if removed is None:
            return
        fallback = doc.get("based_on") or templates.DEFAULT_ID
        self.sw.s.update(template=fallback)
        self.doc = self.store.resolve(fallback)
        self._refresh_gallery()
        self._refresh_actions()
        self._rebuild_open_groups()
        self._schedule_preview()

        def undo() -> None:
            restored = self.store.restore(removed)
            self.sw.s.update(template=restored["id"])
            self.doc = restored
            self._refresh_gallery()
            self._refresh_actions()
            self._rebuild_open_groups()
            self._schedule_preview()

        self.sw.toast(t("tpl_deleted_undo"), "info", (t("undo"), undo))

    def import_(self) -> None:
        path = filedialog.askopenfilename(parent=self.sw.win, title=t("tpl_import"),
                                          filetypes=[(t("tpl_files"), "*.json"), ("*", "*.*")])
        if not path:
            return
        try:
            doc, problems = self.store.import_file(Path(path))
        except (ValueError, OSError) as e:
            self.sw.toast(t("tpl_import_bad", err=str(e)[:160]), "danger")
            return
        self.sw.s.update(template=doc["id"])
        self.doc = doc
        self._refresh_gallery()
        self._refresh_actions()
        self._rebuild_open_groups()
        self._schedule_preview()
        name = templates.display_name(doc, self.lang, t)
        if problems:
            self.sw.toast(t("tpl_import_fixed", name=name, n=len(problems), first=problems[0][:120]), "warning")
            log.info("template import fixed %d problems: %s", len(problems), problems)
        else:
            self.sw.toast(t("tpl_imported", name=name), "success")

    def export(self) -> None:
        self.flush()
        doc = self.doc
        path = filedialog.asksaveasfilename(parent=self.sw.win, title=t("tpl_export"), defaultextension=".json",
                                            initialfile=f"{doc['id']}.json",
                                            filetypes=[(t("tpl_files"), "*.json")])
        if not path:
            return
        try:
            self.store.export(doc["id"], Path(path))
            self.sw.toast(t("tpl_exported", name=Path(path).name), "success")
        except OSError as e:
            self.sw.toast(t("tpl_save_failed", err=str(e)[:120]), "danger")

    # ------------------------------------------------------------------ editor helpers
    def _g(self, path: str) -> Callable[[], Any]:
        return lambda: get_path(self.doc, path)

    def _s(self, path: str, rebuild: bool = False) -> Callable[[Any], None]:
        return lambda v: self._edit(path, v, rebuild)

    def _pct(self, body: Any, label: str, path: str, help: str = "", indent: bool = False) -> None:
        self.kit.slider(body, label, 0, 100, lambda: int(round(get_path(self.doc, path) * 100)),
                        lambda v: self._edit(path, round(v / 100, 3)), 1, "%", help=help, indent=indent)

    def _num(self, body: Any, label: str, path: str, lo: float, hi: float, step: float = 1, unit: str = "px",
             help: str = "", indent: bool = False) -> None:
        self.kit.slider(body, label, lo, hi, self._g(path), self._s(path), step, unit, help=help, indent=indent)

    def _font(self, body: Any, label: str, path: str, help: str = "") -> None:
        k = self.kit
        holder = k.row(body, label, help)
        current = get_path(self.doc, path)
        shown = current[0] if current else "auto"
        btn = k.button(holder, shown if shown != "auto" else t("tpl_font_auto"), lambda: None, width=220)

        def chosen(fam: str) -> None:
            stack = list(get_path(self.doc, path))
            if fam == "auto":
                new = ["auto"]
            else:
                new = [fam] + [f for f in stack if f not in (fam, "auto")]
            self._edit(path, new[:6])
            btn.configure(text=fam if fam != "auto" else t("tpl_font_auto"))

        btn.configure(command=lambda: FontPicker(self.sw.win, self.kit, chosen,
                                                 "rtl" in path))
        btn.pack(side=k.end)

    def _gradient(self, body: Any, path: str, color_path: str, label: str) -> None:
        k = self.kit
        g = get_path(self.doc, path)

        def toggle(on: bool) -> None:
            if on:
                base = get_path(self.doc, color_path)
                grad = copy.deepcopy(templates.DEFAULT_GRADIENT)
                grad["stops"][0]["color"] = base
                grad["stops"][1]["color"] = _darker(base)
                self._edit(path, grad, rebuild=True)
            else:
                self._edit(path, None, rebuild=True)

        k.switch(body, label, lambda: get_path(self.doc, path) is not None, toggle)
        if g is None:
            return
        self._num(body, t("tp_angle"), f"{path}.angle", 0, 360, 15, "°", indent=True)

        def g_stop(i: int, field: str) -> Any:
            return get_path(self.doc, path)["stops"][i][field]

        for i in range(len(g["stops"])):
            holder = k.row(body, t("tp_stop", n=i + 1), indent=True)
            box = ctk.CTkFrame(holder, fg_color="transparent")
            box.pack(side=k.end)
            k.color_in(box, lambda i=i: g_stop(i, "color"), lambda v, i=i: self._set_stop(path, i, "color", v))
            k.slider_in(box, 0, 100, lambda i=i: int(round(g_stop(i, "offset") * 100)),
                        lambda v, i=i: self._set_stop(path, i, "offset", round(v / 100, 3)), 5, "%", width=90)

        acts = k.actions(body)
        if len(g["stops"]) < 4:
            k.button(acts, t("tp_add_stop"), lambda: self._stops(path, +1), icon="add").pack(side=k.start)
        if len(g["stops"]) > 2:
            k.button(acts, t("tp_remove_stop"), lambda: self._stops(path, -1), kind="danger").pack(
                side=k.start, padx=8)

    def _set_stop(self, path: str, i: int, field: str, value: Any) -> None:
        grad = copy.deepcopy(get_path(self.doc, path))
        grad["stops"][i][field] = value
        self._edit(path, grad)

    def _stops(self, path: str, delta: int) -> None:
        grad = copy.deepcopy(get_path(self.doc, path))
        if delta > 0:
            last = grad["stops"][-1]
            for s_ in grad["stops"]:
                s_["offset"] = round(s_["offset"] * 0.75, 3)
            grad["stops"].append({"offset": 1, "color": last["color"]})
        else:
            grad["stops"].pop()
            grad["stops"][-1]["offset"] = 1
        self._edit(path, grad, rebuild=True)

    def _shadow_fields(self, body: Any, path: str) -> None:
        self._num(body, t("tp_shadow_x"), f"{path}.x", -20, 20, 1, indent=True)
        self._num(body, t("tp_shadow_y"), f"{path}.y", -20, 20, 1, indent=True)
        self._num(body, t("tp_shadow_blur"), f"{path}.blur", 0, 40, 1, indent=True)
        self.kit.color(body, t("tp_color"), self._g(f"{path}.color"), self._s(f"{path}.color"), indent=True)
        self._pct(body, t("tp_opacity"), f"{path}.opacity", indent=True)

    def _optional_shadow(self, body: Any, path: str, label: str, help: str = "") -> None:
        k = self.kit

        def toggle(on: bool) -> None:
            self._edit(path, copy.deepcopy(templates.DEFAULT_SHADOW) if on else None, rebuild=True)

        k.switch(body, label, lambda: get_path(self.doc, path) is not None, toggle, help)
        if get_path(self.doc, path) is not None:
            self._shadow_fields(body, path)

    def _shadows(self, body: Any, path: str) -> None:
        k = self.kit
        items = get_path(self.doc, path)
        k.label(body, t("tp_shadows"), "body.strong").pack(fill="x", pady=(T.SPACE[5], 0))
        k.label(body, t("tp_shadows_hint"), "caption", T.TEXT_SECONDARY, wrap=520).pack(fill="x")
        for i in range(len(items)):
            head = ctk.CTkFrame(body, fg_color="transparent")
            head.pack(fill="x", pady=(T.SPACE[4], 0))
            k.label(head, t("tp_shadow_n", n=i + 1), "body", T.TEXT_SECONDARY).pack(side=k.start)
            k.button(head, t("tp_remove"), lambda i=i: self._shadow_list(path, remove=i), kind="danger",
                     icon="trash").pack(side=k.end)
            self._shadow_fields(body, f"{path}.{i}")
        if len(items) < 4:
            k.button(k.actions(body), t("tp_add_shadow"), lambda: self._shadow_list(path), icon="add").pack(
                side=k.start)

    def _shadow_list(self, path: str, remove: int | None = None) -> None:
        items = copy.deepcopy(get_path(self.doc, path))
        if remove is None:
            items.append(copy.deepcopy(templates.DEFAULT_SHADOW))
        else:
            items.pop(remove)
        self._edit(path, items, rebuild=True)

    def _seg(self, body: Any, label: str, path: str, opts: list[tuple[Any, str]], help: str = "",
             rebuild: bool = False, indent: bool = False) -> None:
        self.kit.segmented(body, label, opts, self._g(path), self._s(path, rebuild), help, indent)

    def _opt(self, body: Any, label: str, path: str, opts: list[tuple[Any, str]], help: str = "",
             width: int = 220, indent: bool = False) -> None:
        self.kit.option(body, label, opts, self._g(path), self._s(path), help, width, indent)

    # ------------------------------------------------------------------ editor groups
    def _build_text(self, body: Any) -> None:
        k = self.kit
        kinds = [("original", t("tpl_line_original")), ("translation", t("tpl_line_translation")),
                 ("next", t("tpl_line_next")), ("speaker", t("tpl_line_speaker"))]
        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.pack(fill="x", pady=(T.SPACE[2], 0))

        def pick(kind: str) -> None:
            self.line_kind = kind
            self._rebuild("text")

        k.segmented_in(bar, kinds, lambda: self.line_kind, pick)
        p = f"lines.{self.line_kind}"
        k.switch(body, t("tp_visible"), self._g(f"{p}.visible"), self._s(f"{p}.visible"))
        self._font(body, t("tp_font_latin"), f"{p}.font.latin", t("tp_font_latin_hint"))
        self._font(body, t("tp_font_rtl"), f"{p}.font.rtl", t("tp_font_rtl_hint"))
        self._num(body, t("size"), f"{p}.font.size", 8, 120, 1)
        self._opt(body, t("tp_weight"), f"{p}.font.weight", [(w, t(f"w_{w}")) for w in WEIGHTS])
        k.switch(body, t("tp_italic"), self._g(f"{p}.font.italic"), self._s(f"{p}.font.italic"),
                 t("tp_italic_hint"))
        k.color(body, t("tp_color"), self._g(f"{p}.color"), self._s(f"{p}.color"))
        self._gradient(body, f"{p}.gradient", f"{p}.color", t("tp_gradient"))
        self._pct(body, t("tp_opacity"), f"{p}.opacity")
        self._num(body, t("tp_outline_width"), f"{p}.outline.width", 0, 8, 0.5, help=t("tp_outline_hint"))
        k.color(body, t("tp_outline_color"), self._g(f"{p}.outline.color"), self._s(f"{p}.outline.color"))
        self._pct(body, t("tp_outline_opacity"), f"{p}.outline.opacity")
        self._shadows(body, f"{p}.shadow")
        self._num(body, t("tp_letter_spacing"), f"{p}.letter_spacing", -2, 12, 0.5,
                  help=t("tp_letter_spacing_hint"))
        self._num(body, t("tp_line_height"), f"{p}.line_height", 0.8, 2.5, 0.05, "")
        self._num(body, t("tp_max_lines"), f"{p}.max_lines", 1, 8, 1, "")
        k.switch(body, t("tp_uppercase"), self._g(f"{p}.uppercase"), self._s(f"{p}.uppercase"),
                 t("tp_uppercase_hint"))
        if self.line_kind == "speaker":
            k.entry(body, t("tp_format"), self._g(f"{p}.format"), self._speaker_format, t("tp_format_hint"),
                    width=160, mono=True)
            self._seg(body, t("tp_placement"), f"{p}.placement", [("inline", t("tp_inline")),
                                                                ("above", t("tp_above"))])

    def _speaker_format(self, v: str) -> None:
        v = (v or "").strip()[:40]
        if "{name}" in v:
            self._edit("lines.speaker.format", v)

    def _build_box(self, body: Any) -> None:
        k = self.kit
        p = "container"
        k.color(body, t("tp_box_color"), self._g(f"{p}.background.color"), self._s(f"{p}.background.color"))
        self._pct(body, t("tp_box_opacity"), f"{p}.background.opacity", t("tp_box_opacity_hint"))
        self._gradient(body, f"{p}.background.gradient", f"{p}.background.color", t("tp_gradient"))
        self._seg(body, t("tp_frost"), f"{p}.blur.mode", [("none", t("tp_frost_none")),
                                                         ("simulated", t("tp_frost_sim")),
                                                         ("acrylic", t("tp_frost_acrylic"))],
                  t("tp_frost_hint"), rebuild=True)
        if get_path(self.doc, f"{p}.blur.mode") != "none":
            self._num(body, t("tp_frost_strength"), f"{p}.blur.strength", 0, 40, 1, indent=True)
            self.kit.slider(body, t("tp_frost_noise"), 0, 10, lambda: round(get_path(self.doc, f"{p}.blur.noise") * 100, 1),
                            lambda v: self._edit(f"{p}.blur.noise", round(v / 100, 4)), 0.5, "%", indent=True)
            self.kit.slider(body, t("tp_frost_sheen"), 0, 30, lambda: round(get_path(self.doc, f"{p}.blur.sheen") * 100, 1),
                            lambda v: self._edit(f"{p}.blur.sheen", round(v / 100, 4)), 1, "%", indent=True)
        self._num(body, t("tp_padding_x"), f"{p}.padding.x", 0, 80, 1)
        self._num(body, t("tp_padding_y"), f"{p}.padding.y", 0, 80, 1)
        self._num(body, t("tp_radius"), f"{p}.corner_radius", 0, 48, 1)
        self._num(body, t("tp_border_width"), f"{p}.border.width", 0, 6, 0.5)
        k.color(body, t("tp_border_color"), self._g(f"{p}.border.color"), self._s(f"{p}.border.color"))
        self._pct(body, t("tp_border_opacity"), f"{p}.border.opacity")
        self._optional_shadow(body, f"{p}.shadow", t("tp_box_shadow"))
        self._seg(body, t("tp_width_unit"), f"{p}.width.unit", [("percent", t("tp_unit_percent")),
                                                               ("px", t("tp_unit_px"))], rebuild=True)
        if get_path(self.doc, f"{p}.width.unit") == "percent":
            self._num(body, t("tp_width"), f"{p}.width.value", 10, 100, 1, "%", t("tp_width_pct_hint"))
        else:
            self._num(body, t("tp_width"), f"{p}.width.value", 100, 4000, 10)
        k.switch(body, t("tp_fit"), self._g(f"{p}.width.fit_content"), self._s(f"{p}.width.fit_content"),
                 t("tp_fit_hint"))
        self._num(body, t("tp_min_width"), f"{p}.width.min_px", 0, 2000, 10, help=t("tp_min_width_hint"))
        self._opt(body, t("align"), f"{p}.align", [("center", t("al_center")), ("start", t("al_start")),
                                                   ("end", t("al_end")), ("left", t("al_left")),
                                                   ("right", t("al_right"))], t("tp_align_hint"))
        self._num(body, t("tp_line_gap"), f"{p}.line_gap", 0, 48, 1)
        self._num(body, t("tp_group_gap"), f"{p}.group_gap", 0, 64, 1)

    def _build_layout(self, body: Any) -> None:
        k = self.kit
        self._seg(body, t("tp_translation_pos"), "layout.translation_position",
                  [("below", t("tp_below")), ("above", t("tp_above_original"))])
        k.switch(body, t("tp_mirror"), self._g("layout.mirror_for_rtl"), self._s("layout.mirror_for_rtl"),
                 t("tp_mirror_hint"))
        self._seg(body, t("tp_scale_mode"), "scale.mode", [("screen", t("tp_scale_screen")),
                                                           ("dpi", t("tp_scale_dpi")),
                                                           ("fixed", t("tp_scale_fixed"))], t("tp_scale_hint"),
                  rebuild=True)
        if get_path(self.doc, "scale.mode") == "screen":
            self._num(body, t("tp_ref_height"), "scale.reference_height", 480, 4320, 10, indent=True)

    def _build_karaoke(self, body: Any) -> None:
        k = self.kit
        p = "karaoke"
        k.switch(body, t("tp_karaoke_on"), self._g(f"{p}.enabled"), self._s(f"{p}.enabled"), t("tp_karaoke_hint"))
        self._seg(body, t("tp_karaoke_mode"), f"{p}.mode", [("line", t("tp_k_line")), ("sweep", t("tp_k_sweep")),
                                                           ("word", t("tp_k_word"))], t("tp_k_mode_hint"))
        self._seg(body, t("tp_karaoke_applies"), f"{p}.applies_to", [("original", t("tpl_line_original")),
                                                                    ("translation", t("tpl_line_translation")),
                                                                    ("both", t("tp_both"))])
        k.color(body, t("tp_highlight"), self._g(f"{p}.highlight_color"), self._s(f"{p}.highlight_color"))
        self._gradient(body, f"{p}.highlight_gradient", f"{p}.highlight_color", t("tp_highlight_gradient"))
        self._pct(body, t("tp_inactive"), f"{p}.inactive_opacity", t("tp_inactive_hint"))
        self._optional_shadow(body, f"{p}.glow", t("tp_glow"), t("tp_glow_hint"))

    def _build_motion(self, body: Any) -> None:
        k = self.kit
        p = "animation"
        motions = [(m, t(f"mo_{m}")) for m in MOTIONS]
        self._opt(body, t("tp_enter"), f"{p}.enter", motions)
        self._opt(body, t("tp_exit"), f"{p}.exit", motions)
        self._num(body, t("tp_duration"), f"{p}.duration_ms", 0, 1000, 10, "ms")
        self._num(body, t("tp_exit_duration"), f"{p}.exit_duration_ms", 0, 1000, 10, "ms", t("tp_exit_hint"))
        self._num(body, t("tp_slide"), f"{p}.slide_px", 0, 48, 1)
        self._opt(body, t("tp_easing"), f"{p}.easing", [(e, t(f"ease_{e}")) for e in EASINGS])
        self._seg(body, t("tp_reduced"), f"{p}.reduced_motion", [("respect", t("tp_respect")),
                                                                ("ignore", t("tp_ignore"))], t("tp_reduced_hint"))
        k.button(k.actions(body), t("preview_play"), self.play, icon="play").pack(side=k.start)

    def _build_timing(self, body: Any) -> None:
        self._num(body, t("tp_min_display"), "timing.min_display_ms", 0, 10000, 100, "ms", t("tp_min_display_hint"))
        self.kit.label(body, t("tp_hints"), "body.strong").pack(fill="x", pady=(T.SPACE[5], 0))
        self.kit.label(body, t("tp_hints_hint"), "caption", T.TEXT_SECONDARY, wrap=520).pack(fill="x")
        self._ensure_hints()
        self._opt(body, t("tp_hint_place"), "hints.placement", [("keep", t("tp_keep")), ("bottom", t("pr_bottom")),
                                                               ("top", t("pr_top")), ("center", t("pr_center"))])
        tri = [(None, t("tp_no_opinion")), (True, t("on")), (False, t("off"))]
        self._opt(body, t("click_through"), "hints.click_through", tri)
        self._opt(body, t("lock_position"), "hints.locked", tri)

    def _ensure_hints(self) -> None:
        h = self.doc.setdefault("hints", {})
        h.setdefault("placement", "keep")
        h.setdefault("click_through", None)
        h.setdefault("locked", None)

    # ------------------------------------------------------------------ preview pane
    def _build_preview(self) -> None:
        k = self.kit
        side = self.side
        top = ctk.CTkFrame(side, fg_color="transparent")
        top.pack(fill="x")
        k.label(top, t("preview").upper(), "caption", T.TEXT_SECONDARY).pack(side=k.start)
        frame = ctk.CTkFrame(side, fg_color=T.BG_SURFACE, corner_radius=T.RADIUS_LG, border_width=1,
                             border_color=T.BORDER_SUBTLE)
        frame.pack(fill="x", pady=(T.SPACE[3], 0))
        self.canvas = tk.Label(frame, bd=0, highlightthickness=0, bg="#0B1120")
        self.canvas.pack(padx=T.SPACE[4], pady=(T.SPACE[4], T.SPACE[3]))
        self.badges = ctk.CTkFrame(frame, fg_color="transparent")
        self.badges.pack(fill="x", padx=T.SPACE[4], pady=(0, T.SPACE[3]))
        bd_label = k.label(side, t("preview_backdrop"), "body.strong")
        bd_label.pack(fill="x", pady=(T.SPACE[4], 0))
        bd = ctk.CTkFrame(side, fg_color="transparent")
        bd.pack(fill="x", pady=(T.SPACE[1], 0))
        opts = [("dark", t("preview_dark")), ("bright", t("preview_bright")), ("game", t("preview_game")),
                ("checker", t("preview_checker"))]
        var = tk.StringVar(value=self.sw.s["preview_backdrop"])
        for i, (val, lbl) in enumerate(opts):
            rb = ctk.CTkRadioButton(bd, text=lbl, value=val, variable=var, font=k.font("body"),
                                    command=lambda: (self.sw.s.update(preview_backdrop=var.get()),
                                                     self._schedule_preview()),
                                    fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, border_color=T.BORDER_STRONG,
                                    text_color=T.TEXT_PRIMARY, radiobutton_width=18, radiobutton_height=18)
            r, c = divmod(i, 2)
            rb.grid(row=r, column=(1 - c) if k.rtl else c, sticky="e" if k.rtl else "w", padx=(0, 12), pady=3)
        bd.grid_columnconfigure((0, 1), weight=1)
        self.sample = tk.StringVar(value="sample")
        holder = ctk.CTkFrame(side, fg_color="transparent")
        holder.pack(fill="x", pady=(T.SPACE[4], 0))
        k.label(holder, t("preview_sample"), "body.strong").pack(side=k.start)
        k.segmented_in(holder, [("sample", t("preview_builtin")), ("current", t("preview_current"))],
                       lambda: self.sample.get(), lambda v: (self.sample.set(v), self._schedule_preview()))
        play = k.button(side, t("preview_play"), self.play, icon="play")
        play.pack(fill="x", pady=(T.SPACE[4], 0))
        self._extras = [(bd_label, dict(fill="x", pady=(T.SPACE[4], 0))), (bd, dict(fill="x", pady=(T.SPACE[1], 0))),
                        (holder, dict(fill="x", pady=(T.SPACE[4], 0))), (play, dict(fill="x", pady=(T.SPACE[4], 0)))]
        self._compact = False
        self._render_preview()

    def set_compact(self, on: bool) -> None:
        """Docked under the content (narrow window): only the preview picture, as a short strip."""
        if on == getattr(self, "_compact", False):
            return
        self._compact = on
        for w, kw in self._extras:
            w.pack_forget()
        if on:
            self.badges.pack_forget()
        else:
            self.badges.pack(fill="x", padx=T.SPACE[4], pady=(0, T.SPACE[3]))
            for w, kw in self._extras:
                w.pack(**kw)

    def _lines(self) -> render.Lines:
        cur = None
        if self.sample.get() == "current":
            c = self.app.overlay._content
            if c is not None and (c.original or c.translation):
                cur = render.Lines(c.original, c.translation, c.next)
        return preview.sample_lines(self.sw.effective_target(), cur, speaker=True)

    def _schedule_preview(self) -> None:
        if self._preview_job is not None:
            self.side.after_cancel(self._preview_job)
        self._preview_job = self.side.after(60, self._render_preview)

    def _render_preview(self, progress: float = 0.45, alpha: float = 1.0, dy: int = 0) -> None:
        self._preview_job = None
        try:
            if not self.canvas.winfo_exists():
                return
            img = preview.compose(self.renderer, self.doc, self._lines(), self.sw.s["preview_backdrop"],
                                  progress=progress, alpha=alpha, dy=dy)
        except Exception:
            log.exception("preview failed")
            return
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.configure(image=self._photo)
        if alpha >= 1.0 and dy == 0:
            self._render_badges()

    def _render_badges(self) -> None:
        for w in self.badges.winfo_children():
            w.destroy()
        k = self.kit
        lines = self._lines()
        visible = [kind for kind, text in (("original", lines.original), ("translation", lines.translation),
                                           ("next", lines.next)) if text and self.doc["lines"][kind]["visible"]]
        for kind, ratio, _need, ok in preview.contrast(self.doc, self.sw.s["preview_backdrop"], visible):
            row = ctk.CTkFrame(self.badges, fg_color="transparent")
            row.pack(fill="x", pady=1)
            tok = T.SUCCESS if ok else T.WARNING
            ctk.CTkLabel(row, text="", image=k.icon("ok" if ok else "warning", T.ICON, tok), width=20).pack(
                side=k.start)
            text = t("contrast_ok" if ok else "contrast_low", ratio=f"{ratio:.1f}")
            k.label(row, f"{t('tpl_line_' + kind)}: {text}", "caption", tok).pack(side=k.start, padx=6)

    def play(self) -> None:
        """Run enter, karaoke sweep and exit once in the preview (respects reduced motion)."""
        if self._anim_job is not None:
            return
        import time as _t

        a = self.doc["animation"]
        enter, exit_ = a["enter"], a["exit"]
        dur, exit_dur = max(1, a["duration_ms"]), max(1, a["exit_duration_ms"])
        if a["reduced_motion"] == "respect" and render.reduced_motion():
            enter = "fade" if enter != "none" else "none"
            exit_ = "fade" if exit_ != "none" else "none"
            dur, exit_dur = min(dur, 120), min(exit_dur, 120)
        sweep_ms = 2200
        frame = preview.frame_for(self.renderer, self.doc, self._lines(), preview.PREVIEW_SIZE, 0.42)
        slide = int(round(a["slide_px"] * 0.42))
        t0 = _t.perf_counter()
        ease = {"linear": lambda x: x, "ease_out": lambda x: 1 - (1 - x) ** 3,
                "ease_in_out": lambda x: 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2,
                "ease_out_back": lambda x: 1 + 2.70158 * (x - 1) ** 3 + 1.70158 * (x - 1) ** 2}.get(
            a["easing"], lambda x: x)

        def step() -> None:
            self._anim_job = None
            ms = (_t.perf_counter() - t0) * 1000
            if not self.canvas.winfo_exists():
                return
            if ms < dur:
                p = ease(ms / dur)
                alpha = p if "fade" in enter else 1.0
                dy = int(round((1 - p) * slide)) * (1 if "slide_up" in enter else -1 if "slide_down" in enter else 0)
                prog = 0.0
            elif ms < dur + sweep_ms:
                alpha, dy, prog = 1.0, 0, (ms - dur) / sweep_ms
            elif ms < dur + sweep_ms + exit_dur:
                p = ease((ms - dur - sweep_ms) / exit_dur)
                alpha = 1 - p if "fade" in exit_ else (1.0 if exit_ == "none" else 0.0)
                dy = -int(round(p * slide)) if "slide_up" in exit_ else (
                    int(round(p * slide)) if "slide_down" in exit_ else 0)
                prog = 1.0
            else:
                self._render_preview()
                return
            img = preview.compose(self.renderer, self.doc, self._lines(), self.sw.s["preview_backdrop"],
                                  progress=prog, alpha=max(0.0, alpha), dy=dy, frame=frame)
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.configure(image=self._photo)
            self._anim_job = self.side.after(16, step)

        step()


class FontPicker:
    """Searchable font list; each family is shown in its own face."""

    def __init__(self, parent: Any, kit: Kit, on_pick: Callable[[str], None], rtl: bool) -> None:
        self.kit = kit
        self.on_pick = on_pick
        self.rtl_sample = rtl
        top = ctk.CTkToplevel(parent)
        self.top = top
        top.title(t("tp_pick_font"))
        top.geometry("420x520")
        top.transient(parent)
        top.configure(fg_color=T.BG_APP)
        self.query = tk.StringVar()
        e = ctk.CTkEntry(top, textvariable=self.query, placeholder_text=t("tp_search_fonts"), height=T.CONTROL_H,
                         corner_radius=T.RADIUS_MD, border_width=1, border_color=T.BORDER_STRONG,
                         fg_color=T.BG_RAISED, font=kit.font("body"))
        e.pack(fill="x", padx=12, pady=(12, 6))
        focus_ring(e)
        self.list = ctk.CTkScrollableFrame(top, fg_color=T.BG_SURFACE, corner_radius=T.RADIUS_LG)
        self.list.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        fams = sorted({f for f in fonts.families() if not f.startswith("@")}, key=str.lower)
        self.all = ["auto"] + fams
        self.query.trace_add("write", lambda *_: self._fill())
        self._fill()
        top.after(80, lambda: top.winfo_exists() and (top.lift(), top.focus_force(), e.focus_set()))
        e.bind("<Return>", lambda _e: self._first())
        top.bind("<Escape>", lambda _e: top.destroy())

    def _matches(self) -> list[str]:
        q = self.query.get().strip().lower()
        out = [f for f in self.all if q in f.lower()] if q else self.all
        return out[:60]

    def _first(self) -> None:
        m = self._matches()
        if m:
            self._choose(m[0])

    def _fill(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        sample = "متن نمونه  Aa" if self.rtl_sample else "Aa Bb 123"
        for fam in self._matches():
            label = t("tpl_font_auto") if fam == "auto" else f"{fam}    {sample}"
            font = self.kit.font("body") if fam == "auto" else ctk.CTkFont(family=fam, size=15)
            b = ctk.CTkButton(self.list, text=label, anchor="w", font=font, fg_color="transparent",
                              hover_color=T.BG_HOVER, text_color=T.TEXT_PRIMARY, height=34,
                              command=lambda f=fam: self._choose(f))
            b.pack(fill="x")

    def _choose(self, fam: str) -> None:
        self.top.destroy()
        self.on_pick(fam)


def _short(text: str, n: int) -> str:
    text = (text or "").split(":")[0].split("،")[0].split(".")[0]
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _darker(hex_color: str) -> str:
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return "#{:02X}{:02X}{:02X}".format(int(r * 0.7), int(g * 0.7), int(b * 0.7))
    except ValueError:
        return "#808080"


__all__ = ["TemplatePage", "FontPicker"]
