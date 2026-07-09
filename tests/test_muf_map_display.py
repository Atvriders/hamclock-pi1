"""Regression tests for the MUF map appearing on the native pygame client.

Root cause captured here: the client fetched /api/muf-map into
data.images['muf-map'] every image cycle but NO panel ever blitted it — the
MUF slot was text-only ('(Map available in web UI)'). The propagation panel's
tab list and image-key map were also function-local, so nothing could assert
the wiring. These tests pin two contracts:

  1. The propagation panel exposes a 'muf' tab wired to the 'muf-map' image
     key (module-level PROP_TABS / PROP_TAB_IMAGE_KEY).
  2. A real PNG placed in data.images['muf-map'] actually reaches the screen
     via the same _get_cached_image -> draw_image path the render loop uses
     (i.e. pixels change) — the assertion that would have caught the gap.
"""
import io
import os

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('HAMCLOCK_DEBUG', '1')

import pygame
import pytest

import hamclock_pygame as hp


def test_prop_tabs_include_muf_wired_to_muf_map():
    # The map is only reachable if 'muf' is a selectable propagation tab AND
    # it resolves to the 'muf-map' image key the data layer fetches.
    assert 'muf' in hp.PROP_TABS, (
        "propagation panel must offer a 'muf' tab so the KC2G MUF map is "
        "reachable on the native client")
    assert hp.PROP_TAB_IMAGE_KEY.get('muf') == 'muf-map', (
        "the 'muf' tab must resolve to the 'muf-map' image endpoint key")


def test_muf_map_key_matches_data_layer_endpoint():
    # Guard against drift between the client tab wiring and the endpoint the
    # data layer actually populates.
    import hamclock_data
    assert 'muf-map' in hamclock_data.HamClockData._IMAGE_ENDPOINTS


class _FakeData:
    """Minimal stand-in for hamclock_data.DataClient's read surface."""
    def __init__(self, images):
        self.images = images
        self.image_fetched_at = {k: 123.0 for k in images}
        self.last_image_refresh = 123.0


def _make_png_bytes(size, color):
    surf = pygame.Surface(size)
    surf.fill(color)
    buf = io.BytesIO()
    pygame.image.save(surf, buf, 'muf.png')  # namehint picks the PNG encoder
    return buf.getvalue()


def test_muf_map_png_blits_onto_screen():
    # Rendering path parity: feed a known-color PNG through the exact helpers
    # the render loop uses for the propagation image and assert it paints.
    pygame.display.init()
    screen = pygame.display.set_mode((720, 450))
    try:
        theme = hp.THEMES['kstate']
        card = theme['card']
        png = _make_png_bytes((120, 80), (210, 40, 40))
        data = _FakeData({'muf-map': png})

        surf = hp._get_cached_image(data, 'muf-map', {}, {})
        assert surf is not None, "_get_cached_image must decode the muf-map PNG"

        rect = pygame.Rect(0, 0, 120, 80)
        screen.fill(card)
        hp.draw_image(screen, rect, surf, fonts=None, theme=theme,
                      image_key='muf-map', fetched_at=123.0)

        # The map fills the rect; an interior pixel must differ from the card
        # background (i.e. something actually got blitted).
        assert screen.get_at((60, 40))[:3] != tuple(card), (
            "muf-map PNG did not reach the screen — panel still shows only "
            "the card background")
    finally:
        pygame.display.quit()
