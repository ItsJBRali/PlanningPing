"""The interface is monochrome by specification, so no hue may survive anywhere.

Two sources of colour have to stay closed: our own palette, and the stock
CustomTkinter theme that any widget we do not style explicitly falls back to.
"""

from __future__ import annotations

import unittest

import customtkinter as ctk

from planning_ping.ui.theme import COLORS, configure_theme


def _hues(value, path="", colour_keys_only=False):
    """Yield every non-neutral hex colour reachable from ``value``.

    ``colour_keys_only`` restricts descent into a mapping to keys naming a
    colour, which is what CustomTkinter's theme needs (it mixes in sizes and
    font names). Our own flat palette is walked in full.
    """

    if isinstance(value, dict):
        for key, item in value.items():
            if colour_keys_only and not isinstance(item, dict) and "color" not in key:
                continue
            yield from _hues(item, f"{path}{key}.", colour_keys_only)
        return
    if isinstance(value, list):
        for item in value:
            yield from _hues(item, path, colour_keys_only)
        return
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        red, green, blue = (int(value[index : index + 2], 16) for index in (1, 3, 5))
        if not red == green == blue:
            yield f"{path.rstrip('.')}={value}"


class MonochromePaletteTests(unittest.TestCase):
    def test_every_palette_token_is_neutral_grey(self) -> None:
        offenders = sorted(_hues(dict(COLORS)))
        self.assertEqual(offenders, [], f"non-grey palette tokens: {offenders}")

    def test_severity_reads_from_lightness(self) -> None:
        """Errors must still stand out against ordinary and muted text."""

        def luma(token: str) -> int:
            value = COLORS[token]
            return int(value[1:3], 16)

        self.assertGreater(luma("error"), luma("warning"))
        self.assertGreater(luma("warning"), luma("line"))


class MonochromeWidgetThemeTests(unittest.TestCase):
    def test_configure_theme_leaves_no_hue_in_customtkinter(self) -> None:
        """Unstyled widgets must not reintroduce the stock blue accent."""

        configure_theme()
        offenders = sorted(_hues(ctk.ThemeManager.theme, colour_keys_only=True))
        self.assertEqual(offenders, [], f"non-grey widget colours: {offenders}")

    def test_the_stock_theme_really_does_carry_hue(self) -> None:
        """Guards the test above: it would pass vacuously if this were false."""

        ctk.set_default_color_theme("blue")
        self.assertTrue(list(_hues(ctk.ThemeManager.theme, colour_keys_only=True)))
        configure_theme()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
