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


class BaseScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=COLORS["background"])

    def on_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
