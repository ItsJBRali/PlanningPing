"""Reusable CustomTkinter components."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from .theme import COLORS, SPACING, TYPE


class FocusButton(ctk.CTkButton):
    """A button with a visible keyboard-focus outline."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", COLORS["surface_raised"])
        kwargs.setdefault("hover_color", "#383838")
        kwargs.setdefault("text_color", COLORS["text"])
        kwargs.setdefault("border_color", COLORS["line"])
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("font", TYPE["body"])
        super().__init__(master, **kwargs)
        tk.Frame.configure(self, takefocus=True)
        tk.Frame.bind(self, "<FocusIn>", lambda _event: self.configure(border_color=COLORS["accent"], border_width=2))
        tk.Frame.bind(self, "<FocusOut>", lambda _event: self.configure(border_color=COLORS["line"], border_width=1))
        tk.Frame.bind(self, "<Return>", self._keyboard_invoke)
        tk.Frame.bind(self, "<space>", self._keyboard_invoke)

    def _keyboard_invoke(self, _event):
        self.invoke()
        return "break"


class RadarMark(tk.Canvas):
    """A code-drawn static sonar mark; no external asset or callback lifecycle."""

    def __init__(self, master, size: int = 48, **kwargs):
        super().__init__(
            master,
            width=size,
            height=size,
            background=COLORS["background"],
            highlightthickness=0,
            takefocus=False,
            **kwargs,
        )
        center = size / 2
        for inset in (5, 12, 19):
            self.create_oval(inset, inset, size - inset, size - inset, outline=COLORS["text"], width=1)
        self.create_line(center, center, size - 6, 8, fill=COLORS["text"], width=2)
        self.create_oval(center - 2, center - 2, center + 2, center + 2, fill=COLORS["text"], outline="")


class PageHeader(ctk.CTkFrame):
    def __init__(self, master, title: str, subtitle: str):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=title, font=TYPE["title"], text_color=COLORS["text"], anchor="w").pack(fill="x")
        ctk.CTkLabel(
            self,
            text=subtitle,
            font=TYPE["body"],
            text_color=COLORS["muted"],
            anchor="w",
            justify="left",
            wraplength=900,
        ).pack(fill="x", pady=(SPACING["xs"], 0))


class ScrollableTable(ctk.CTkFrame):
    """A two-axis table viewport with visible horizontal navigation."""

    def __init__(self, master, *, min_content_width: int):
        super().__init__(master, fg_color=COLORS["surface"])
        self._min_content_width = min_content_width
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._canvas = tk.Canvas(
            self,
            background=COLORS["surface"],
            highlightthickness=0,
            takefocus=True,
        )
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical_scrollbar = ctk.CTkScrollbar(
            self,
            orientation="vertical",
            command=self._canvas.yview,
        )
        self.vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        self.horizontal_scrollbar = ctk.CTkScrollbar(
            self,
            orientation="horizontal",
            command=self._canvas.xview,
        )
        self.horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self._canvas.configure(
            xscrollcommand=self.horizontal_scrollbar.set,
            yscrollcommand=self.vertical_scrollbar.set,
        )
        self.content = ctk.CTkFrame(self._canvas, fg_color=COLORS["surface"], corner_radius=0)
        self._window_id = self._canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._sync_scroll_region)
        self._canvas.bind("<Configure>", self._sync_content_width)
        self._canvas.bind("<Left>", lambda _event: self._scroll_x(-1))
        self._canvas.bind("<Right>", lambda _event: self._scroll_x(1))
        self.register_mousewheel_target(self._canvas)
        self.register_mousewheel_target(self.content)

    def _sync_scroll_region(self, _event=None) -> None:
        bounds = self._canvas.bbox("all")
        if bounds is not None:
            self._canvas.configure(scrollregion=bounds)

    def _sync_content_width(self, event) -> None:
        width = max(event.width, self._min_content_width, self.content.winfo_reqwidth())
        self._canvas.itemconfigure(self._window_id, width=width)
        self._sync_scroll_region()

    def _scroll_x(self, units: int):
        self._canvas.xview_scroll(units, "units")
        return "break"

    @staticmethod
    def _wheel_steps(delta: int) -> int:
        if delta == 0:
            return 0
        direction = 1 if delta > 0 else -1
        return direction * max(1, abs(delta) // 120)

    def _on_mousewheel(self, event):
        steps = self._wheel_steps(event.delta)
        if steps:
            self._canvas.yview_scroll(-steps, "units")
            return "break"
        return None

    def register_mousewheel_target(self, widget) -> None:
        """Keep wheel handling scoped to this table and its rendered cells."""

        targets = (
            widget,
            getattr(widget, "_canvas", None),
            getattr(widget, "_text_label", None),
            getattr(widget, "_image_label", None),
        )
        seen: set[str] = set()
        for target in targets:
            if target is None or str(target) in seen:
                continue
            seen.add(str(target))
            tk.Misc.bind(target, "<MouseWheel>", self._on_mousewheel, add="+")

    def xview(self) -> tuple[float, float]:
        return self._canvas.xview()

    def xview_moveto(self, fraction: float) -> None:
        self._canvas.xview_moveto(fraction)


class BaseScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=COLORS["background"])

    def on_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
