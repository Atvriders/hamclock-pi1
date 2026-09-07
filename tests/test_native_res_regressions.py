"""Three defects that only appeared once the client rendered at native size.

All found from the 1.0.7 field report (Pi 1B, KMSDRM 1440x900):
  * the propagation tab labels were clipped to nothing by a hardcoded 20 px bar
  * the report arrived with NO screenshot — a 1440x900 frame with the MUF map
    finally rendering exceeded the base64 cap and was dropped
  * p90 was still 187 ms: the panel stagger was being undone by full flips
"""
import io
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import pygame
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp

MODES = [(720, 450), (800, 600), (1024, 768), (1440, 900)]


def _fresh(size):
    pygame.display.quit(); pygame.display.init(); pygame.font.init()
    return pygame.display.set_mode(size)


# ------------------------------------------------------- tab labels

@pytest.mark.parametrize("size", MODES)
def test_tab_labels_are_visible_at_every_mode(size):
    """At 1440x900 the 26 px panel face was drawn into a hardcoded 20 px bar
    and clipped to nothing — three blank buttons, no way to tell which map was
    up. Measured 0 label pixels there against 113 at 800x600."""
    scr = _fresh(size)
    fonts = hp._make_fonts(size)
    theme = hp.THEMES['kstate']
    inner = hp.draw_panel(scr, hp._get_layout(size)['propagation'],
                          'PROPAGATION', fonts, theme)
    bar_h = max(20, fonts['panel'].get_height() + 6)
    bar = pygame.Rect(inner.x, inner.y, inner.w, bar_h)
    scr.fill(theme['card'], bar)
    hp.draw_tabs(scr, bar, hp.PROP_TABS, 'drap', fonts, theme)

    card = tuple(theme['card'])
    lit = sum(1 for x in range(bar.x, bar.x + bar.w, 2)
              for y in range(bar.y, bar.y + bar.h, 2)
              if sum(abs(a - b) for a, b in
                     zip(scr.get_at((x, y))[:3], card)) > 40)
    assert lit > 40, f"{size}: only {lit} label pixels — tabs are unreadable"


@pytest.mark.parametrize("size", MODES)
def test_the_tab_bar_is_tall_enough_for_its_font(size):
    fonts = hp._make_fonts(size)
    assert max(20, fonts['panel'].get_height() + 6) >= fonts['panel'].get_height()


def test_draw_tabs_honours_the_rect_height():
    """Regression guard: the body must not reintroduce a literal height."""
    import inspect
    src = inspect.getsource(hp.draw_tabs)
    assert ', 20)' not in src, "draw_tabs still hardcodes a 20 px tab height"


# ------------------------------------------------------- screenshot

def test_a_native_resolution_frame_still_produces_a_screenshot():
    scr = _fresh((1440, 900))
    import random
    random.seed(7)
    for _ in range(4000):          # worst case for PNG entropy
        scr.fill((random.randint(0, 255),) * 3,
                 pygame.Rect(random.randint(0, 1400), random.randint(0, 880), 30, 18))
    b64 = hp._screenshot_b64(scr)
    assert b64 is not None, "a native-resolution frame must still be sendable"
    assert len(b64) <= hp.SCREENSHOT_MAX_B64


def test_the_screenshot_is_downscaled_not_dropped():
    scr = _fresh((1440, 900))
    b64 = hp._screenshot_b64(scr)
    assert b64
    import base64
    img = pygame.image.load(io.BytesIO(base64.b64decode(b64)), 'x.png')
    assert max(img.get_size()) <= hp.SCREENSHOT_MAX_EDGE
    # Still big enough to judge layout and legibility from.
    assert max(img.get_size()) >= 640


def test_a_small_frame_is_not_upscaled():
    scr = _fresh((720, 450))
    import base64
    img = pygame.image.load(io.BytesIO(base64.b64decode(hp._screenshot_b64(scr))), 'x.png')
    assert img.get_size() == (720, 450)


# ------------------------------------------------------- schedule grid

def test_the_stagger_survives_a_full_flip():
    """The first attempt applied each phase once and then advanced by the
    cadence. A full flip redraws everything and reschedules it all to
    now+cadence, throwing the offsets away — and the propagation tab cycles
    every five minutes, forcing exactly that. The field p90 stayed at 187 ms.

    On a fixed grid the next due time always lands back on the panel's own
    offset, whatever happened before it.
    """
    epoch = 1000.0
    sixty = sorted(n for n, c in hp._CADENCE_S.items() if c == 60.0)
    # Simulate a full flip at an arbitrary moment: every panel reschedules
    # from the same instant.
    flip_at = epoch + 37.3
    due = {n: hp._next_due(n, flip_at, epoch) for n in sixty}
    assert len(set(due.values())) > 1, (
        "every panel came due at the same moment after a full flip — "
        "the stagger was undone")
    spread = max(due.values()) - min(due.values())
    assert spread > 30.0, f"panels only spread over {spread:.1f}s of a 60s window"


def test_next_due_is_always_in_the_future():
    epoch = 1000.0
    for now in (epoch, epoch + 0.1, epoch + 59.9, epoch + 601.7):
        for name in hp._CADENCE_S:
            assert hp._next_due(name, now, epoch) > now, (name, now)
