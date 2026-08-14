"""PlanningPing's high-contrast visual tokens.

The interface is monochrome by specification: every colour here is a neutral
grey, and severity is carried by lightness, weight and wording rather than by
hue. :func:`configure_theme` also flattens CustomTkinter's own palette, so
widgets we do not style explicitly cannot reintroduce the stock blue accent.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk


COLORS = {
    "background": "#090909",
    "surface": "#151515",
    "surface_raised": "#202020",
    "text": "#FFFFFF",
    "muted": "#BDBDBD",
    "line": "#4A4A4A",
    "accent": "#FFFFFF",
    "accent_text": "#000000",
    # Severity reads from lightness plus a bold weight, not from hue: "error"
    # is the brightest text in the palette, "warning" sits just below "muted".
    "error": "#FFFFFF",
    "warning": "#D6D6D6",
}
SPACING = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32}
TYPE = {
    "title": ("Segoe UI", 28, "bold"),
    "heading": ("Segoe UI", 18, "bold"),
    "body": ("Segoe UI", 13),
    # Used for validation messages, which can no longer rely on being red.
    "body_strong": ("Segoe UI", 13, "bold"),
}


def _to_grey(value: Any) -> Any:
    """Return ``value`` with any hue removed, preserving its brightness.

    Handles the shapes CustomTkinter stores in its theme: a hex string, a Tk
    colour name, ``"transparent"``, or a ``[light, dark]`` pair of those.
    Anything already neutral is returned untouched.
    """

    if isinstance(value, list):
        return [_to_grey(item) for item in value]
    if not isinstance(value, str) or not value.startswith("#") or len(value) != 7:
        # Named colours in the stock themes are all "gray..." variants, which
        # are already neutral, and "transparent" has nothing to convert.
        return value

    red, green, blue = (int(value[index : index + 2], 16) for index in (1, 3, 5))
    # Rec. 601 luma: matches how the eye weights each channel, so the grey
    # keeps the brightness the designer chose.
    luma = round(0.299 * red + 0.587 * green + 0.114 * blue)
    return "#{0:02X}{0:02X}{0:02X}".format(min(255, max(0, luma)))


def _flatten_theme() -> None:
    """Strip the hue out of every colour CustomTkinter would apply for us.

    Doing it wholesale rather than per widget means a widget added later is
    monochrome without anyone having to remember to style it.
    """

    for widget_theme in ctk.ThemeManager.theme.values():
        if not isinstance(widget_theme, dict):
            continue
        for key, value in widget_theme.items():
            if "color" in key:
                widget_theme[key] = _to_grey(value)


def configure_theme() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    _flatten_theme()
