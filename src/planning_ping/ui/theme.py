"""PlanningPing's high-contrast visual tokens."""

from __future__ import annotations

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
    "error": "#FF7070",
    "warning": "#FFE08A",
}
SPACING = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32}
TYPE = {"title": ("Segoe UI", 28, "bold"), "heading": ("Segoe UI", 18, "bold"), "body": ("Segoe UI", 13)}


def configure_theme() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
