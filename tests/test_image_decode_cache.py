"""Tier 1.2 / 1.5 + smoothscale depth safety on the native pygame client.

Three defects are pinned here:

1.2  _get_cached_image keyed its decoded-surface cache on the GLOBAL
     data.last_image_refresh, so one endpoint arriving invalidated the decoded
     surfaces of all five and the render thread re-decoded every JPEG/PNG it
     already held. It also never remembered a decode FAILURE, so an
     undecodable payload cost three SDL probes on every single redraw.

1.5  _load_image handed SVG straight to SDL_image. nanosvg "succeeds" on the
     365 KB KC2G MUF vector, producing a 1526x905 / 5.5 MB greyscale surface
     in 104-193 ms on x86 — 3.1-5.2 s of synchronous render-loop stall on
     ARMv6, and ~11 MB of transient on a 512 MB box.

EXTRA pygame.transform.smoothscale raises ValueError on 8bpp AND 16bpp
     surfaces. 10-monitor.conf ships DefaultDepth 16 and /api/real-drap (the
     DEFAULT propagation tab) decodes as an 8bpp palettised PNG, so on real
     hardware draw_image's bare `except Exception: pass` silently blanked
     every image panel.
"""
import io

import pygame
import pytest

import hamclock_pygame as hp


# --- fixtures --------------------------------------------------------------

@pytest.fixture(scope='module')
def display():
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((720, 450))
    yield screen
    pygame.display.quit()


@pytest.fixture(autouse=True)
def _clear_decode_memo():
    hp._decode_failed_ts.clear()
    yield
    hp._decode_failed_ts.clear()


def _png(size=(8, 8), color=(200, 30, 30)):
    surf = pygame.Surface(size)
    surf.fill(color)
    buf = io.BytesIO()
    pygame.image.save(surf, buf, 'x.png')
    return buf.getvalue()


class _Data:
    """Current HamClockData read surface: per-key stamps present."""

    def __init__(self, images=None, stamps=None, refresh=100.0):
        self.images = images if images is not None else {}
        self.image_fetched_at = stamps if stamps is not None else {}
        self.last_image_refresh = refresh


class _LegacyData:
    """A stand-in with NO image_fetched_at — tests/test_themes.py's _StubData
    and the client embedded in the shipped installers both look like this."""

    def __init__(self, images=None, refresh=100.0):
        self.images = images if images is not None else {}
        self.last_image_refresh = refresh


# --- 1.2: per-key stamp ----------------------------------------------------

def test_image_stamp_prefers_per_key_value():
    d = _Data(stamps={'muf-map': 42.0}, refresh=100.0)
    assert hp._image_stamp(d, 'muf-map') == 42.0


def test_image_stamp_falls_back_to_global_refresh_for_unknown_key():
    d = _Data(stamps={'muf-map': 42.0}, refresh=100.0)
    assert hp._image_stamp(d, 'enlil') == 100.0


def test_image_stamp_tolerates_missing_attribute():
    """No AttributeError for objects predating image_fetched_at — the render
    loop swallows exceptions per panel, so a raise here is a blank panel."""
    assert hp._image_stamp(_LegacyData(refresh=7.0), 'enlil') == 7.0


def test_image_stamp_tolerates_non_dict_attribute():
    d = _LegacyData(refresh=7.0)
    d.image_fetched_at = None
    assert hp._image_stamp(d, 'enlil') == 7.0


def test_other_keys_are_not_redecoded_when_one_image_arrives(display, monkeypatch):
    """The regression: bumping muf-map's stamp must not re-decode enlil."""
    png = _png()
    d = _Data(images={'muf-map': png, 'enlil': png},
              stamps={'muf-map': 10.0, 'enlil': 10.0})
    cache, cache_ts = {}, {}
    calls = []
    real = hp._load_image
    monkeypatch.setattr(hp, '_load_image',
                        lambda b: (calls.append(1), real(b))[1])

    assert hp._get_cached_image(d, 'muf-map', cache, cache_ts) is not None
    assert hp._get_cached_image(d, 'enlil', cache, cache_ts) is not None
    assert len(calls) == 2

    # muf-map refetched; enlil untouched.
    d.image_fetched_at['muf-map'] = 20.0
    hp._get_cached_image(d, 'muf-map', cache, cache_ts)
    hp._get_cached_image(d, 'enlil', cache, cache_ts)
    assert len(calls) == 3, (
        'enlil was re-decoded because the cache is still keyed on the global '
        'last_image_refresh instead of the per-key fetch stamp')


def test_repeat_calls_at_the_same_stamp_decode_once(display, monkeypatch):
    d = _Data(images={'enlil': _png()}, stamps={'enlil': 10.0})
    cache, cache_ts = {}, {}
    calls = []
    real = hp._load_image
    monkeypatch.setattr(hp, '_load_image',
                        lambda b: (calls.append(1), real(b))[1])
    for _ in range(20):
        assert hp._get_cached_image(d, 'enlil', cache, cache_ts) is not None
    assert len(calls) == 1


def test_legacy_data_without_stamps_still_decodes(display):
    """tests/test_themes.py's _StubData shape must keep working."""
    d = _LegacyData(images={'enlil': _png()}, refresh=5.0)
    cache, cache_ts = {}, {}
    assert hp._get_cached_image(d, 'enlil', cache, cache_ts) is not None
    assert cache_ts['enlil'] == 5.0


# --- 1.2: decode failure is memoized --------------------------------------

def test_undecodable_payload_is_probed_once_per_refresh(display, monkeypatch):
    d = _Data(images={'enlil': b'not an image at all'}, stamps={'enlil': 10.0})
    cache, cache_ts = {}, {}
    calls = []
    real = hp._load_image
    monkeypatch.setattr(hp, '_load_image',
                        lambda b: (calls.append(1), real(b))[1])
    for _ in range(30):
        assert hp._get_cached_image(d, 'enlil', cache, cache_ts) is None
    assert len(calls) == 1, (
        '_load_image ran %d times for one bad payload — each call is up to '
        'three SDL probes on the render thread' % len(calls))

    # A new fetch stamp means a new payload: probe again exactly once.
    d.image_fetched_at['enlil'] = 20.0
    for _ in range(10):
        hp._get_cached_image(d, 'enlil', cache, cache_ts)
    assert len(calls) == 2


def test_failed_decode_keeps_serving_the_last_good_surface(display):
    good = _png(color=(10, 220, 10))
    d = _Data(images={'enlil': good}, stamps={'enlil': 10.0})
    cache, cache_ts = {}, {}
    first = hp._get_cached_image(d, 'enlil', cache, cache_ts)
    assert first is not None

    d.images['enlil'] = b'garbage'
    d.image_fetched_at['enlil'] = 20.0
    for _ in range(5):
        assert hp._get_cached_image(d, 'enlil', cache, cache_ts) is first, (
            'a bad refresh must not drop the image already on screen')


def test_recovery_after_a_failed_decode_clears_the_memo(display):
    d = _Data(images={'enlil': b'garbage'}, stamps={'enlil': 10.0})
    cache, cache_ts = {}, {}
    assert hp._get_cached_image(d, 'enlil', cache, cache_ts) is None
    assert hp._decode_failed_ts.get('enlil') == 10.0

    d.images['enlil'] = _png()
    d.image_fetched_at['enlil'] = 20.0
    assert hp._get_cached_image(d, 'enlil', cache, cache_ts) is not None
    assert 'enlil' not in hp._decode_failed_ts


def test_decode_failure_memo_is_bounded_by_endpoint_count(display):
    """It is keyed by endpoint name, so it can never grow without bound."""
    keys = ['solar-image', 'muf-map', 'enlil', 'drap', 'real-drap']
    d = _Data(images={k: b'garbage' for k in keys},
              stamps={k: 1.0 for k in keys})
    cache, cache_ts = {}, {}
    for tick in range(1, 40):
        for k in keys:
            d.image_fetched_at[k] = float(tick)
            hp._get_cached_image(d, k, cache, cache_ts)
    assert len(hp._decode_failed_ts) == len(keys)


# --- 1.5: SVG never reaches the decoder ------------------------------------

SVG_DECL = (b'<?xml version="1.0" encoding="utf-8" standalone="no"?>\n'
            b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
            b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
            b'<svg height="905pt" version="1.1" viewBox="0 0 1526 905" '
            b'width="1526pt" xmlns="http://www.w3.org/2000/svg">'
            b'<rect width="1526" height="905" fill="#888"/></svg>\n')

SVG_BARE = b'<svg xmlns="http://www.w3.org/2000/svg" width="1526" ' \
           b'height="905"><rect width="1526" height="905"/></svg>'


@pytest.mark.parametrize('payload', [
    SVG_DECL,
    SVG_BARE,
    b'\n\r\t   ' + SVG_DECL,          # leading whitespace
    b'   ' + SVG_BARE,
])
def test_load_image_refuses_svg(display, payload):
    assert hp._load_image(payload) is None, (
        'SDL_image/nanosvg will happily decode this into a multi-megabyte '
        'greyscale surface and stall the render loop for seconds on ARMv6')


def test_load_image_refuses_svg_without_touching_sdl(display, monkeypatch):
    """The guard must short-circuit BEFORE the decoder, not rely on it
    failing — nanosvg does not fail, that is the whole problem."""
    def boom(*a, **kw):
        raise AssertionError('SVG payload reached the SDL decoder')
    monkeypatch.setattr(pygame.image, 'load_extended', boom)
    monkeypatch.setattr(pygame.image, 'load', boom)
    assert hp._load_image(SVG_DECL) is None


def test_load_image_still_decodes_png(display):
    assert hp._load_image(_png()) is not None


def test_load_image_returns_none_for_empty_and_garbage(display):
    assert hp._load_image(b'') is None
    assert hp._load_image(None) is None
    assert hp._load_image(b'\x00\x01\x02\x03') is None


def test_svg_payload_is_memoized_as_a_decode_failure(display, monkeypatch):
    """The SVG refusal must go through the same once-per-refresh memo, or the
    MUF tab re-slices 256 bytes on every redraw for nothing."""
    d = _Data(images={'muf-map': SVG_DECL}, stamps={'muf-map': 10.0})
    cache, cache_ts = {}, {}
    calls = []
    real = hp._load_image
    monkeypatch.setattr(hp, '_load_image',
                        lambda b: (calls.append(1), real(b))[1])
    for _ in range(15):
        assert hp._get_cached_image(d, 'muf-map', cache, cache_ts) is None
    assert len(calls) == 1


# --- smoothscale depth safety ---------------------------------------------

@pytest.mark.parametrize('depth', [8, 16, 24, 32])
def test_smoothscale_safe_handles_every_depth(display, depth):
    src = pygame.Surface((64, 48), depth=depth)
    src.fill((120, 60, 30))
    out = hp._smoothscale_safe(src, (32, 24))
    assert out is not None
    assert out.get_size() == (32, 24)


@pytest.mark.parametrize('depth', [8, 16])
def test_raw_smoothscale_would_have_raised(display, depth):
    """Pins the premise: without the promotion this is a ValueError, which
    draw_image's bare except turns into a silently blank panel."""
    src = pygame.Surface((64, 48), depth=depth)
    with pytest.raises(ValueError):
        pygame.transform.smoothscale(src, (32, 24))


@pytest.mark.parametrize('depth', [8, 16, 24, 32])
def test_draw_image_paints_a_sub_24bpp_surface(display, depth):
    """End-to-end: the panel must not come out blank on a 16-bit X11 display
    or with the 8bpp palettised real-drap PNG."""
    theme = hp.THEMES['kstate']
    fonts = hp._make_fonts()
    screen = pygame.Surface((200, 120))
    screen.fill(theme['card'])
    src = pygame.Surface((400, 240), depth=depth)
    src.fill((0, 250, 0))
    rect = pygame.Rect(0, 0, 200, 120)
    hp._scaled_cache.clear()
    hp.draw_image(screen, rect, src, fonts, theme,
                  image_key='real-drap', fetched_at=1.0)
    assert screen.get_at((100, 60))[:3] != tuple(theme['card']), (
        'draw_image painted nothing for a %dbpp source — smoothscale raised '
        'and the bare except swallowed it' % depth)


def test_smoothscale_safe_falls_back_to_nearest_neighbour(display, monkeypatch):
    """If smoothscale rejects the surface for any reason we have not
    anticipated, a coarse image still beats a blank panel."""
    def boom(*a, **kw):
        raise ValueError('Only 24-bit or 32-bit surfaces can be smoothly scaled')
    monkeypatch.setattr(pygame.transform, 'smoothscale', boom)
    src = pygame.Surface((64, 48))
    src.fill((10, 20, 30))
    out = hp._smoothscale_safe(src, (32, 24))
    assert out.get_size() == (32, 24)
    assert out.get_at((16, 12))[:3] == (10, 20, 30)


def test_draw_image_uses_the_safe_wrapper_at_both_scale_sites():
    """Static guard: a future edit must not reintroduce a raw smoothscale in
    draw_image — the failure mode is a silently blank panel, not a crash."""
    import inspect
    import re
    src = inspect.getsource(hp.draw_image)
    assert not re.search(r'pygame\.transform\.smoothscale\s*\(', src), (
        'draw_image calls pygame.transform.smoothscale directly; use '
        '_smoothscale_safe so 8bpp/16bpp sources still render:\n%s' % src)
    assert src.count('_smoothscale_safe(') == 2, (
        'draw_image has two scale sites (cached and uncached); both must go '
        'through _smoothscale_safe')


# --- 1.2 mirrored into the tkinter client ---------------------------------
#
# hamclock_tkinter cannot be imported here (RPi OS Lite and this sandbox both
# ship python3 without tkinter), so the mirror is pinned by AST instead.

import ast
from pathlib import Path

TK_SRC = Path('/home/kasm-user/hamclock-pi1/hamclock_tkinter.py')


def _tk_func(name):
    tree = ast.parse(TK_SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('hamclock_tkinter has no %s()' % name)


def test_tkinter_update_images_has_a_per_key_gate():
    fn = _tk_func('_update_images')
    src = ast.dump(fn)
    assert 'last_image_refresh' not in src, (
        '_update_images still gates every image on the global refresh tick; '
        'one endpoint arriving re-decodes and re-thumbnails all five')
    assert '_image_stamp' in src, (
        '_update_images must resolve each image key through _image_stamp')


def test_tkinter_image_stamp_guards_a_missing_attribute():
    fn = _tk_func('_image_stamp')
    src = ast.dump(fn)
    assert 'getattr' in src and 'isinstance' in src, (
        'the image_fetched_at lookup needs the getattr/isinstance guard — '
        'older HamClockData copies do not have the attribute at all')


def test_tkinter_stamp_store_is_a_dict():
    src = TK_SRC.read_text()
    assert 'self._last_image_ts = {}' in src, (
        '_last_image_ts must be a per-key dict, not a single scalar stamp')
