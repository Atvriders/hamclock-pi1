"""A clipped value must never look like a complete one.

_fit_text clamps text to a panel's width so a long value cannot paint over its
neighbour. Clamping by character alone is dangerous for space weather: an SFI
of 148 becomes "14" and a solar wind of 456.9 becomes "45" — both entirely
plausible readings, with nothing on screen to say they are fragments. An
operator picking a band off a truncated SFI is being actively misled.

These tests pin two things: truncation is always marked, and the fonts stay
small enough that the real worst-case values do not truncate in the first
place.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import pygame
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp


@pytest.fixture(scope="module")
def screen():
    pygame.display.quit()
    pygame.display.init()
    pygame.font.init()
    s = pygame.display.set_mode((720, 450))
    yield s
    pygame.display.quit()


MARKS = ("…", "~")


def test_truncated_text_is_marked(screen):
    f = pygame.font.SysFont("monospace", 10)
    full = "456.9"
    narrow = f.size(full)[0] // 2
    out = hp._fit_text(f, full, narrow)
    assert out != full, "precondition: this width must force truncation"
    assert out.endswith(MARKS), (
        f"truncated {full!r} rendered as {out!r} with no marker — an operator "
        f"cannot tell this from a complete reading")


def test_untruncated_text_is_returned_verbatim(screen):
    f = pygame.font.SysFont("monospace", 10)
    for s in ("148", "456.9", "C1.0", "INACTIVE"):
        assert hp._fit_text(f, s, 10_000) == s, "no marker when nothing is cut"


def test_marked_result_still_fits_the_budget(screen):
    """The marker must be inside max_w, not spill past it."""
    f = pygame.font.SysFont("monospace", 10)
    for width in (12, 18, 25, 40, 60):
        out = hp._fit_text(f, "INACTIVE", width)
        assert f.size(out)[0] <= width, (
            f"{out!r} is {f.size(out)[0]}px, over the {width}px budget")


def test_degenerate_widths_do_not_crash(screen):
    f = pygame.font.SysFont("monospace", 10)
    assert hp._fit_text(f, "148", 0) == ""
    assert hp._fit_text(f, "148", -5) == ""
    assert hp._fit_text(f, "", 50) == ""


def test_real_solar_values_fit_without_truncation(screen):
    """The font sizes must be chosen so live numbers never clip.

    SOLAR is the tightest panel on the grid; if a real reading truncates here
    the marker saves the operator from misreading it, but the right answer is
    for it to fit.

    Mirrors draw_solar's own cell arithmetic: labels take the width they need,
    values get the remainder of the column.
    """
    fonts = hp._make_fonts()
    layout = hp._get_layout((720, 450))
    r = layout["solar"]
    rect = pygame.Rect(r.x + 4, r.y + 18, r.w - 8, r.h - 22)
    lab_f, val_f = fonts["label"], fonts["body"]
    rows = [("SFI", "148"), ("Kp", "0"), ("SSN", "99"), ("A", "6"),
            ("Xray", "C1.0"), ("Wind", "456.9"), ("Bz", "--"),
            ("Geo", "INACTIVE"), ("S/N", "50-51"), ("foF2", "0")]

    lab_h = lab_f.get_height()
    glyph_h = max(lab_h, val_f.get_height())
    n = len(rows)
    for ncols in range(1, 5):
        per_col = -(-n // ncols)
        if (per_col - 1) * lab_h + glyph_h <= rect.h:
            break
    col_w = rect.w // ncols
    lab_max = max(lab_f.size(l)[0] for l, _ in rows)
    val_x = min(col_w // 2, lab_max + 4)
    val_w = col_w - val_x - 2

    # Every NUMERIC reading must survive intact. 'INACTIVE' is a word, not a
    # measurement, and may clip (marked) without misleading anyone.
    for label, value in rows:
        if label == "Geo":
            continue
        assert hp._fit_text(val_f, value, val_w) == value, (
            f"{label} {value!r} clips to "
            f"{hp._fit_text(val_f, value, val_w)!r} in a {val_w}px cell — "
            f"a truncated space-weather number misleads the operator")


def test_all_fonts_antialiased(screen):
    """AA is on everywhere; the header-only policy left the rest pixelated."""
    fonts = hp._make_fonts()
    for name, f in fonts.items():
        assert hp._font_aa.get(id(f)) is True, f"font {name!r} is not antialiased"
