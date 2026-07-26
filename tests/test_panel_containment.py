"""Tier 1.1 — panel containment: no draw_* may paint outside the rect it got.

Commit 2a73ec3 halved the native framebuffer to 720x450 (hamclock_pygame.py
SCREEN_W/SCREEN_H) but left every draw function's geometry in absolute pixels
sized for the old 1440x900 dashboard: `y += 16` row pitch, a value column at
`rect.x + 70`, DAY/NIGHT headers at `+100`/`+160`, DX columns at
`+90/+140/+230/+340`, `row_h = max(14, ...)`. At 720x450 the BANDS inner rect
is only 128 px wide (NIGHT was drawn at +160) and the SOLAR inner rect is
128x53 while ten rows at pitch 16 need 160 px, so panels bled over their own
borders and into their neighbours.

The invariant pinned here is deliberately mechanical and resolution-agnostic:
fill a screen with a sentinel colour, run ONE panel's content draw against the
rect the render loop hands it, paint the sentinel back over that rect, and
assert the surface is bit-identical to an all-sentinel surface. Any pixel a
draw function put outside its rect survives the erase and fails the compare.

Runs at BOTH 720x450 (the Pi 1 framebuffer) and 1440x900 (the pre-2a
geometry), so a future "fix" that just re-hardcodes the other resolution
cannot pass.
"""
import pygame
import pytest

import hamclock_pygame as hp


SENTINEL = (1, 2, 3)          # not a colour any theme paints
SENTINEL_B = bytes(SENTINEL)

SIZES = [(720, 450), (1440, 900)]


# --- worst-case-ish sample data (long strings, full row counts) -------------

SOLAR = {
    'sfi': 145, 'kIndex': 3, 'ssn': 187, 'aIndex': 12, 'xray': 'B2.4',
    'solarWind': 412, 'bz': -3.5, 'geomagField': 'Very Unsettled',
    'signalNoise': 'S2-S3', 'fof2': 9.8,
}

BANDS = {
    '80m-40m': {'day': 'Good', 'night': 'Fair'},
    '30m-20m': {'day': 'Good', 'night': 'Good'},
    '17m-15m': {'day': 'Fair', 'night': 'Poor'},
    '12m-10m': {'day': 'Poor', 'night': 'N/A'},
}

DXSPOTS = [
    {'frequency': 14.20512, 'band': '20m', 'dxCall': 'JW/DL1ABC',
     'spotter': 'VK4ABCDEFG', 'time': '14:01Z'},
    {'frequency': 7.20500, 'band': '40m', 'dxCall': 'JA1XYZ/P',
     'spotter': 'W2XYZABCDE', 'time': '14:02Z'},
    {'frequency': 21.25000, 'band': '15m', 'dxCall': 'DL1XX',
     'spotter': 'F5XYZ', 'time': '14:03Z'},
    {'frequency': 28.49000, 'band': '10m', 'dxCall': 'VK9ZZZ',
     'spotter': 'ZL1AAA', 'time': '14:04Z'},
    {'frequency': 3.79000, 'band': '80m', 'dxCall': 'PY2ABC',
     'spotter': 'LU1BBB', 'time': '14:05Z'},
    {'frequency': 10.11000, 'band': '30m', 'dxCall': 'UA0ZZ',
     'spotter': 'JA7CCC', 'time': '14:06Z'},
]


class _StubData:
    """Read surface draw_status_bar / draw_header need."""
    solar = SOLAR
    bands = BANDS
    dxspots = DXSPOTS
    images = {}
    image_fetched_at = {}
    last_data_refresh = 1700000000.0
    last_image_refresh = 1700000000.0


@pytest.fixture(scope='module')
def env():
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((1440, 900))
    fonts = hp._make_fonts()
    theme = hp.THEMES['kstate']
    yield screen, fonts, theme
    pygame.display.quit()


def _fresh(size):
    surf = pygame.Surface(size)
    surf.fill(SENTINEL)
    return surf


def _stray(surf, rect):
    """Erase `rect`, then return (count, first_xy) of surviving non-sentinel
    pixels. The whole-buffer compare is a single C-level memcmp; the slow
    per-pixel walk only runs when the assertion is already failing."""
    surf.fill(SENTINEL, rect)
    w, h = surf.get_size()
    raw = pygame.image.tostring(surf, 'RGB')
    if raw == SENTINEL_B * (w * h):
        return 0, None
    count = 0
    first = None
    for i in range(0, len(raw), 3):
        if raw[i:i + 3] != SENTINEL_B:
            count += 1
            if first is None:
                first = ((i // 3) % w, (i // 3) // w)
    return count, first


def _assert_contained(surf, rect, what):
    count, first = _stray(surf, rect)
    assert count == 0, (
        '%s painted %d pixel(s) outside its rect %r (first stray at %r); '
        'panel geometry must be derived from the rect, not hardcoded for '
        '1440x900' % (what, count, tuple(rect), first))


def _inner(size, key):
    return hp._panel_inner_rect(hp._get_layout(size)[key])


# --- left column -----------------------------------------------------------

@pytest.mark.parametrize('size', SIZES)
def test_solar_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'solar')
    hp.draw_solar(surf, rect, SOLAR, fonts, theme)
    _assert_contained(surf, rect, 'draw_solar@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_solar_contained_with_cached_view(env, size):
    """The data_refresh_ts branch reads the _solar_view snapshot — same
    geometry, different value source; both paths must stay inside."""
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'solar')
    hp.draw_solar(surf, rect, SOLAR, fonts, theme, data_refresh_ts=12345.0)
    _assert_contained(surf, rect, 'draw_solar(ts)@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_bands_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'bands')
    hp.draw_bands(surf, rect, BANDS, fonts, theme)
    _assert_contained(surf, rect, 'draw_bands@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_geomag_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'geomag')
    hp.draw_geomag(surf, rect, SOLAR, fonts, theme)
    _assert_contained(surf, rect, 'draw_geomag@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_xray_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'xray')
    hp.draw_xray(surf, rect, SOLAR, fonts, theme)
    _assert_contained(surf, rect, 'draw_xray@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_open_bands_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'open_bands')
    hp._open_bands_cache['ts'] = None
    # A real ts (not None) or _open_bands_strings short-circuits on its own
    # sentinel and hands back the empty cached strings.
    hp.draw_open_bands(surf, rect, BANDS, fonts, theme,
                       data_refresh_ts=1700000000.0)
    _assert_contained(surf, rect, 'draw_open_bands@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_sdo_image_placeholder_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'sdo')
    hp.draw_image(surf, rect, None, fonts, theme)
    _assert_contained(surf, rect, 'draw_image(loading)@%dx%d' % size)


# --- middle column ---------------------------------------------------------

@pytest.mark.parametrize('size', SIZES)
def test_muf_text_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'muf')
    hp.draw_muf_text(surf, rect, SOLAR, fonts, theme)
    _assert_contained(surf, rect, 'draw_muf_text@%dx%d' % size)


# --- right column ----------------------------------------------------------

@pytest.mark.parametrize('size', SIZES)
def test_dx_spots_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'dx_spots')
    hp.draw_dx_spots(surf, rect, DXSPOTS, fonts, theme)
    _assert_contained(surf, rect, 'draw_dx_spots@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_band_activity_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = _inner(size, 'band_activity')
    hp.draw_band_activity(surf, rect, DXSPOTS, fonts, theme)
    _assert_contained(surf, rect, 'draw_band_activity@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_propagation_tabs_and_image_contained(env, size):
    """The propagation panel is composed the way _run_render_loop composes it:
    a 20 px tab bar at the top of the inner rect, the image below it."""
    _, fonts, theme = env
    surf = _fresh(size)
    inner = _inner(size, 'propagation')
    tab_bar = pygame.Rect(inner.x, inner.y, inner.w, 20)
    hp.draw_tabs(surf, tab_bar, hp.PROP_TABS, 'drap', fonts, theme)
    _assert_contained(surf, tab_bar, 'draw_tabs@%dx%d' % size)

    surf = _fresh(size)
    img_rect = pygame.Rect(inner.x, inner.y + 24, inner.w, inner.h - 24)
    big = pygame.Surface((1024, 1024))
    big.fill((200, 30, 30))
    hp.draw_image(surf, img_rect, big, fonts, theme,
                  image_key='real-drap', fetched_at=1.0)
    _assert_contained(surf, img_rect, 'draw_image(scaled)@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_draw_image_does_not_upscale_past_rect(env, size):
    """A source smaller than the rect is blitted 1:1 and centred; a source
    with a wildly different aspect ratio must still be letterboxed inside."""
    _, fonts, theme = env
    inner = _inner(size, 'propagation')
    for src_size in ((17, 900), (900, 17), (4, 4)):
        surf = _fresh(size)
        src = pygame.Surface(src_size)
        src.fill((0, 200, 0))
        hp.draw_image(surf, inner, src, fonts, theme)
        _assert_contained(surf, inner,
                          'draw_image%r@%dx%d' % (src_size, size[0], size[1]))


# --- chrome ----------------------------------------------------------------

@pytest.mark.parametrize('size', SIZES)
def test_header_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = hp._get_layout(size)['header']
    hp._strfmt_cache['key'] = None
    hp.draw_header(surf, rect, 'VE3ABCD/QRP', fonts, theme, data=_StubData())
    _assert_contained(surf, rect, 'draw_header@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
def test_status_bar_contained(env, size):
    _, fonts, theme = env
    surf = _fresh(size)
    rect = hp._get_layout(size)['status']
    hp._strfmt_cache['key'] = None
    hp.draw_status_bar(surf, rect, _StubData(), fonts, theme)
    _assert_contained(surf, rect, 'draw_status_bar@%dx%d' % size)


@pytest.mark.parametrize('size', SIZES)
@pytest.mark.parametrize('key', ['solar', 'bands', 'sdo', 'geomag', 'xray',
                                 'open_bands', 'muf', 'dx_spots',
                                 'band_activity', 'propagation'])
def test_panel_chrome_contained(env, size, key):
    """draw_panel's card/border/title bar must stay inside the panel rect,
    and _panel_inner_rect must stay inside it too (the render loop uses the
    latter on frames where a panel's cadence has not elapsed)."""
    _, fonts, theme = env
    surf = _fresh(size)
    rect = hp._get_layout(size)[key]
    inner = hp.draw_panel(surf, rect, 'TEST TITLE', fonts, theme)
    _assert_contained(surf, rect, 'draw_panel[%s]@%dx%d' % (key, size[0], size[1]))
    assert rect.contains(inner), (
        '_panel_inner_rect(%s) %r escapes its panel %r'
        % (key, tuple(inner), tuple(rect)))
    assert inner.w > 0 and inner.h > 0, (
        'panel %s has a degenerate inner rect %r at %dx%d — its content has '
        'nowhere to go' % (key, tuple(inner), size[0], size[1]))


# --- the layout itself -----------------------------------------------------

@pytest.mark.parametrize('size', SIZES)
def test_layout_panels_do_not_overlap(env, size):
    """Containment is only meaningful if the rects themselves are disjoint."""
    rects = hp._get_layout(size)
    keys = sorted(rects)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert not rects[a].colliderect(rects[b]), (
                'panels %s %r and %s %r overlap at %dx%d'
                % (a, tuple(rects[a]), b, tuple(rects[b]), size[0], size[1]))


@pytest.mark.parametrize('size', SIZES)
def test_layout_panels_fit_the_screen(env, size):
    screen = pygame.Rect(0, 0, size[0], size[1])
    for key, r in hp._get_layout(size).items():
        assert screen.contains(r), (
            'panel %s %r falls outside the %dx%d screen'
            % (key, tuple(r), size[0], size[1]))


# --- degenerate rects ------------------------------------------------------

TINY = [
    pygame.Rect(40, 40, 128, 1),
    pygame.Rect(40, 40, 128, 2),
    pygame.Rect(40, 40, 128, 9),
    pygame.Rect(40, 40, 4, 40),
    pygame.Rect(40, 40, 1, 1),
    pygame.Rect(40, 40, 40, 40),
]


def _draw_all(surf, rect, fonts, theme):
    """Every panel body, against the same rect."""
    hp.draw_solar(surf, rect, SOLAR, fonts, theme)
    hp.draw_bands(surf, rect, BANDS, fonts, theme)
    hp.draw_geomag(surf, rect, SOLAR, fonts, theme)
    hp.draw_xray(surf, rect, SOLAR, fonts, theme)
    hp._open_bands_cache['ts'] = None
    hp.draw_open_bands(surf, rect, BANDS, fonts, theme,
                       data_refresh_ts=1700000000.0)
    hp.draw_muf_text(surf, rect, SOLAR, fonts, theme)
    hp.draw_dx_spots(surf, rect, DXSPOTS, fonts, theme)
    hp.draw_band_activity(surf, rect, DXSPOTS, fonts, theme)
    hp.draw_image(surf, rect, None, fonts, theme)
    src = pygame.Surface((300, 200))
    src.fill((0, 200, 0))
    hp.draw_image(surf, rect, src, fonts, theme)
    hp.draw_tabs(surf, rect, hp.PROP_TABS, 'drap', fonts, theme)


@pytest.mark.parametrize('rect', TINY, ids=lambda r: '%dx%d' % (r.w, r.h))
def test_degenerate_rects_stay_contained(env, rect):
    """Panel rects are derived from the screen size; a font that measures
    taller on the Pi than in this sandbox shrinks the effective room. Every
    draw function must drop content it cannot fit rather than paint outside —
    the render loop wraps each call in `except Exception: pass`, so silent
    bleed is the realistic failure, not a traceback."""
    _, fonts, theme = env
    surf = _fresh((300, 200))
    _draw_all(surf, rect, fonts, theme)
    _assert_contained(surf, rect, 'panel bodies @ %r' % (tuple(rect),))


def test_zero_and_negative_rects_do_not_raise(env):
    _, fonts, theme = env
    for rect in (pygame.Rect(10, 10, 0, 0), pygame.Rect(10, 10, 0, 50),
                 pygame.Rect(10, 10, 50, 0)):
        surf = _fresh((300, 200))
        _draw_all(surf, rect, fonts, theme)
        _assert_contained(surf, rect, 'panel bodies @ %r' % (tuple(rect),))
