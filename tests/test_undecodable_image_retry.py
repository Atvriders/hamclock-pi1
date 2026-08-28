"""A 200 carrying bytes this client cannot render is a failed fetch.

Field failure (2026-08-16): the MUF panel showed "(map loading)" on a real Pi
while everything else worked. Cause: /api/muf-map falls back to the raw KC2G
SVG while the rasterize is still running, pygame cannot decode SVG, and
_fetch_binary treats any HTTP 200 as success — so the client cached undecodable
bytes, considered the key satisfied, and did not try again for 900 s. The
screenshot showed Img:590s, mid-way through exactly that wait.

Two defences, both pinned here: the client asks for PNG explicitly so the
server answers PNG-or-503, and a decode failure schedules a retry.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import time
import pygame
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp
import hamclock_data as hd

SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'


@pytest.fixture(autouse=True)
def _clean_decode_state():
    """_decode_failed_ts is module-level global state. Without clearing it, a
    payload already marked failed by an earlier test short-circuits before the
    report hook runs, and the next test sees a streak that never advanced."""
    hp._decode_failed_ts.clear()
    yield
    hp._decode_failed_ts.clear()


@pytest.fixture(scope="module")
def screen():
    pygame.display.quit(); pygame.display.init(); pygame.font.init()
    s = pygame.display.set_mode((800, 600))
    yield s
    pygame.display.quit()


def test_client_asks_the_server_for_png():
    """Without ?fmt=png the server may answer with SVG, which is unrenderable
    here and — worse — arrives as a 200 the retry logic cannot see through."""
    assert hd.HamClockData._IMAGE_ENDPOINTS['muf-map'].endswith('fmt=png')


def test_data_layer_exposes_the_report_hook():
    assert hasattr(hd.HamClockData, 'mark_image_undecodable')


def _client_with(payload):
    c = hd.HamClockData()
    c.images = {'muf-map': payload}
    c.image_fetched_at = {'muf-map': 1.0}
    c.last_image_refresh = 1.0
    return c


def test_svg_payload_schedules_a_prompt_retry(screen):
    c = _client_with(SVG)
    assert hp._get_cached_image(c, 'muf-map', {}, {}) is None, "SVG must be refused"
    due = c.image_next_due.get('muf-map')
    assert due is not None, "an undecodable payload must schedule a retry"
    wait = due - time.time()
    assert 0 < wait <= hd.HamClockData.IMAGE_RETRY_BACKOFF[0] + 1, (
        f"retry in {wait:.0f}s — must use the fast backoff, not the 900 s cycle")


def test_the_bad_payload_is_dropped(screen):
    c = _client_with(SVG)
    hp._get_cached_image(c, 'muf-map', {}, {})
    assert 'muf-map' not in c.images, (
        "keeping undecodable bytes makes 'do we have an image' answer yes")


def test_repeated_failures_back_off(screen):
    c = _client_with(SVG)
    hp._get_cached_image(c, 'muf-map', {}, {})
    first = c.image_fail_streak.get('muf-map')
    c.images = {'muf-map': SVG}
    c.image_fetched_at = {'muf-map': 2.0}
    c.last_image_refresh = 2.0
    hp._get_cached_image(c, 'muf-map', {}, {})
    assert c.image_fail_streak.get('muf-map') > first, "streak must advance"


def test_a_good_png_is_unaffected(screen):
    import io
    surf = pygame.Surface((40, 20)); surf.fill((10, 200, 10))
    buf = io.BytesIO(); pygame.image.save(surf, buf, 'x.png')
    c = _client_with(buf.getvalue())
    got = hp._get_cached_image(c, 'muf-map', {}, {})
    assert got is not None, "a real PNG must still decode"
    assert c.image_next_due.get('muf-map') is None, "no retry scheduled on success"
    assert 'muf-map' in c.images, "a good payload must not be dropped"


def test_report_hook_ignores_unknown_keys():
    c = hd.HamClockData()
    c.mark_image_undecodable('not-an-endpoint')
    assert 'not-an-endpoint' not in c.image_next_due


def test_client_tolerates_a_data_layer_without_the_hook(screen):
    """An older embedded data layer must not crash the render loop."""
    class Old:
        images = {'muf-map': SVG}
        image_fetched_at = {'muf-map': 1.0}
        last_image_refresh = 1.0
    assert hp._get_cached_image(Old(), 'muf-map', {}, {}) is None
