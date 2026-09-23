"""Three defects that only appeared once the client rendered at native size.

All found from the 1.0.7 field report (Pi 1B, KMSDRM 1440x900):
  * the propagation tab labels were clipped to nothing by a hardcoded 20 px bar
  * the report arrived with NO screenshot — a 1440x900 frame with the MUF map
    finally rendering exceeded the base64 cap and was dropped
  * p90 was still 187 ms: the panel stagger was being undone by full flips

The first of those was still broken on the Pi in the Sep 20 screenshot — three
empty tab boxes — while the guards below were green, so the tab section was
rewritten for 1.0.10. Both old guards measured something other than the thing
the operator cares about; see _label_ink for what they counted instead.
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

# draw_tabs paints text in exactly two colours: theme['label'] for a sleeping
# tab and theme['accent'] for the active one. Nothing else in the bar is
# anywhere near either — the card fill is 372 away from 'label' (manhattan,
# 8-bit RGB) and the border 303, so a tolerance of 60 admits glyph cores and
# nothing else. Anti-aliased glyph edges are excluded too, which is why these
# counts are much lower than the ink a human sees; they only have to be
# unreachable by a bar with no text in it.
_INK_TOLERANCE = 60

# What the previous version of this test counted was "any pixel that is not
# the card colour", which is the tab BORDERS plus the active tab's fill —
# 978 of them at 1440x900 while the labels rendered ZERO pixels. It passed for
# the whole time the Pi was showing three blank buttons.


def _label_ink(surf, rect, theme):
    """Count pixels inside `rect` that are a tab label's text colour."""
    targets = (tuple(theme['label']), tuple(theme['accent']))
    n = 0
    for x in range(rect.x, rect.right):
        for y in range(rect.y, rect.bottom):
            px = surf.get_at((x, y))[:3]
            if min(sum(abs(a - b) for a, b in zip(px, t))
                   for t in targets) < _INK_TOLERANCE:
                n += 1
    return n


# Measured with _label_ink on a bar of max(20, panel face + 6) — the height
# the render loop actually hands draw_tabs:
#     720x450    face 13    176 px of ink
#     800x600    face 14    208
#     1024x768   face 19    384
#     1440x900   face 26    810        <- 0 before the fix
# Ink scales with the square of the face (0.069 to 0.080 px per label
# character per face-pixel-squared across those four), so the floor below is
# a little over half the measured density: slack for a different monospace
# face. The same bar with the label blits suppressed measures 0 at all four.
_INK_PER_CHAR_PER_PX2 = 0.035


def _ink_floor(fonts):
    face = fonts['panel'].get_height()
    return int(_INK_PER_CHAR_PER_PX2 * face * face
               * sum(len(t) for t in hp.PROP_TABS))


def _label_reference(fonts, theme, active):
    """_label_ink of each label rendered on its own, on its own tab's fill,
    with nothing to clip it: what a bar that draws its labels whole shows."""
    f = fonts['panel']
    total = 0
    for name in hp.PROP_TABS:
        bg = theme['border'] if name == active else theme['card']
        fg = theme['accent'] if name == active else theme['label']
        img = f.render(name.upper(), True, fg, bg)
        s = pygame.Surface((img.get_width() + 4, img.get_height() + 4))
        s.fill(bg)
        s.blit(img, (2, 2))
        total += _label_ink(s, s.get_rect(), theme)
    return total


# The density floor cannot tell a label from half of one. Clipping each label
# to its top face//2 + 2 rows left 101, 125, 247 and 482 px of ink at the four
# modes — every one above its floor (88, 102, 189, 354) — and clipping is the
# very failure this section exists for. Against the reference above the bar
# measures 176/176, 208/208, 384/383 and 810/806, and the half-clipped labels
# 0.57 to 0.64 of it; 0.9 sits between, with room for anti-aliasing to move a
# few pixels either way.
_WHOLE = 0.9


def _assert_labels_whole(surf, bar, fonts, theme, where):
    ink = _label_ink(surf, bar, theme)
    ref = _label_reference(fonts, theme, 'drap')
    floor = _ink_floor(fonts)
    face = fonts['panel'].get_height()
    # A reference below the floor means the labels could not be seen even
    # drawn on their own (say, a theme whose label colour is its card colour),
    # and 0 >= 0.9 * 0 would pass.
    assert ref >= floor, ('%s: the labels rendered on their own come to only '
                          '%d px of ink (floor %d)' % (where, ref, floor))
    assert ink >= _WHOLE * ref, (
        '%s: %d label pixels in a %d px bar with a %d px face, against %d for '
        'the labels drawn whole — the tabs are blank or clipped'
        % (where, ink, bar.h, face, ref))


@pytest.mark.parametrize("size", MODES)
def test_tab_labels_are_visible_at_every_mode(size):
    """DRAP / AURORA / ENLIL must be readable on the bar, at every mode.

    The propagation panel cycles between three maps every five minutes. With
    the labels gone there is nothing on the display that says which one is
    up, and the three tabs are the only control on the dashboard — the
    operator is clicking unlabelled boxes.

    before: 0 label pixels at 1440x900 (26 px face into a 20 px bar, skipped
    by `if th >= fh`), against 176 at 720x450 where the face still fitted.
    """
    scr = _fresh(size)
    fonts = hp._make_fonts(size)
    theme = hp.THEMES['kstate']
    inner = hp.draw_panel(scr, hp._get_layout(size)['propagation'],
                          'PROPAGATION', fonts, theme)
    bar_h = max(20, fonts['panel'].get_height() + 6)
    bar = pygame.Rect(inner.x, inner.y, inner.w, bar_h)
    scr.fill(theme['card'], bar)
    hp.draw_tabs(scr, bar, hp.PROP_TABS, 'drap', fonts, theme)

    _assert_labels_whole(scr, bar, fonts, theme, str(size))


class _StubData:
    """Enough HamClockData for one frame of the real render loop."""
    def __init__(self):
        self.server_url = 'http://localhost:8080'
        self.solar = {}
        self.bands = {}
        self.dxspots = []
        self.images = {}
        self.image_fetched_at = {}
        self.image_fail_streak = {}
        self.image_next_due = {}
        self.health = {}
        self.last_data_refresh = 0.0
        self.last_image_refresh = 0.0

    def start_background(self, *a, **kw):
        pass

    def stop(self):
        pass


@pytest.mark.parametrize("size", MODES)
def test_the_render_loop_draws_the_tab_labels(size, monkeypatch, tmp_path):
    """The bar the dashboard actually draws, not one this file sized.

    Every other tab test here builds its own rect from `max(20, face + 6)`,
    copied out of the render loop. Put `_tab_h = 20` back in the loop and
    they all stay green while 1440x900 shows three empty boxes again —
    measured: with that one edit, all 35 tests in this file and
    test_report_button_1_0_10.py passed. So this runs the real loop for one
    frame, takes the rect the loop handed draw_tabs, and counts label ink on
    the frame the loop drew, after every other panel has been painted.

    The test this replaces asserted `max(20, face + 6) >= face`, which is
    true of every font there is and never looked at the loop.
    """
    monkeypatch.setattr(hp, 'HamClockData', _StubData)
    monkeypatch.setattr(hp, 'RUN_DIR', str(tmp_path))
    bars = []
    real = hp.draw_tabs

    def spy(screen, rect, *a, **kw):
        bars.append(pygame.Rect(rect))
        return real(screen, rect, *a, **kw)

    monkeypatch.setattr(hp, 'draw_tabs', spy)
    shot = {}

    def frames():
        yield []
        # Pulled for frame 2, so the display holds everything frame 1 drew.
        shot['frame'] = pygame.display.get_surface().copy()
        yield [pygame.event.Event(pygame.QUIT)]

    scr = _fresh(size)
    fonts = hp._make_fonts(size)
    theme = dict(hp.THEMES['kstate'])
    try:
        hp._run_render_loop(scr, fonts, theme, {}, injected_iter=frames())
    finally:
        # The loop ends in pygame.quit(); the tests below need fonts.
        pygame.init()
        pygame.font.init()
    # And every Font made before that quit is dangling — touching one
    # segfaults the interpreter — so the reference gets a fresh set of the
    # same faces.
    fonts = hp._make_fonts(size)

    assert bars, '%s: the loop never drew the tab bar' % (size,)
    bar = bars[0]
    face = fonts['panel'].get_height()
    assert bar.h >= face, ('%s: the loop handed draw_tabs a %d px bar for a '
                           '%d px face' % (size, bar.h, face))
    _assert_labels_whole(shot['frame'], bar, fonts, theme,
                         '%s render loop' % (size,))


@pytest.mark.parametrize("size", MODES)
def test_draw_tabs_honours_the_rect_height(size):
    """The bar must be as tall as the rect it was given.

    The caller sizes that rect from the font and then lays the map out below
    it, so a bar that silently draws itself shorter leaves a gap under the
    tabs and, worse, hands the labels a box their own face does not fit in.

    before: `th = min(20, rect.h)` capped every bar at 20 px, so at 1024x768
    a 25 px rect drew 20 and at 1440x900 a 32 px rect drew 20. The guard this
    replaces asserted `', 20)' not in inspect.getsource(draw_tabs)` — a string
    the live bug, written `min(20, rect.h)`, never contained.
    """
    fonts = hp._make_fonts(size)
    theme = hp.THEMES['kstate']
    surf = pygame.Surface(size)
    surf.fill(theme['card'])
    bar = pygame.Rect(0, 0, size[0] // 2, fonts['panel'].get_height() + 6)
    regions = hp.draw_tabs(surf, bar, hp.PROP_TABS, 'drap', fonts, theme)

    assert set(regions) == set(hp.PROP_TABS)
    for name, r in regions.items():
        assert r.h == bar.h, (
            '%s: the %s tab drew %d px tall into a %d px bar'
            % (size, name, r.h, bar.h))


@pytest.mark.parametrize("size", MODES)
def test_a_bar_sized_for_its_font_actually_renders_labels(size):
    """draw_tabs on its own, away from the panel layout: given a bar that is
    comfortably taller than the face, the labels must appear, whole.

    before: at 1440x900 this rendered nothing, because the 20 px cap made
    `th >= fh` false for the 26 px face however tall the bar was.
    """
    fonts = hp._make_fonts(size)
    theme = hp.THEMES['kstate']
    surf = pygame.Surface(size)
    surf.fill(theme['card'])
    bar = pygame.Rect(0, 0, size[0] // 2, fonts['panel'].get_height() + 6)
    hp.draw_tabs(surf, bar, hp.PROP_TABS, 'drap', fonts, theme)

    _assert_labels_whole(surf, bar, fonts, theme, '%s bare' % (size,))


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
