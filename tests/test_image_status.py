"""Tier 2.5 — honest image status on the two image panels.

Before this tier both image panels had exactly one thing to say when they had
nothing to draw: "image loading...". That single string covered four states an
operator has to tell apart before trusting (or distrusting) what is on screen:

  * the very first fetch is still in flight;
  * the feed has been failing for a while and the next retry is N s out;
  * bytes arrived and SDL will not decode them (and the panel may still be
    showing an OLDER surface that decoded fine, so the screen looks healthy);
  * a picture is up, but it is hours old because the server answered from its
    persisted disk cache or the client has not fetched successfully since.

The functions under test here are the ones that turn that state into text, and
the contract that matters most is _image_status_text being *total*: the render
loop evaluates it as an ARGUMENT to draw_image, inside the per-panel
`except Exception: pass`, so any exception it raises does not merely lose the
status line, it skips the draw_image call entirely and leaves the panel blank
— strictly worse than the string it replaced.
"""
import inspect
import time

import pygame
import pytest

import hamclock_pygame as hp


SENTINEL = (1, 2, 3)
SENTINEL_B = bytes(SENTINEL)


@pytest.fixture
def env():
    pygame.init()
    pygame.font.init()
    fonts = hp._make_fonts()
    theme = hp.THEMES['kstate']
    try:
        yield fonts, theme
    finally:
        pygame.quit()


class _Data(object):
    """Minimal stand-in with the Tier 1.2/1.4 image attributes."""

    def __init__(self, **kw):
        self.images = {}
        self.image_fetched_at = {}
        self.image_fail_streak = {}
        self.image_next_due = {}
        self.last_image_refresh = 0
        self.health = {}
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture(autouse=True)
def _clear_decode_memo():
    hp._decode_failed_ts.clear()
    yield
    hp._decode_failed_ts.clear()


# --- the no-image cadence table -------------------------------------------

def test_no_image_cadence_is_a_named_module_table():
    assert hasattr(hp, '_CADENCE_S_NO_IMAGE'), \
        'Tier 2.5: _CADENCE_S_NO_IMAGE table missing'
    assert isinstance(hp._CADENCE_S_NO_IMAGE, dict)
    assert set(hp._CADENCE_S_NO_IMAGE) == {'sdo', 'propagation'}


def test_no_image_cadence_is_faster_than_normal_but_not_a_spin():
    for key, val in hp._CADENCE_S_NO_IMAGE.items():
        assert key in hp._CADENCE_S, \
            '%r has a no-image cadence but no normal cadence' % key
        assert val < hp._CADENCE_S[key], \
            '%s no-image cadence %.1f is not faster than %.1f' % (
                key, val, hp._CADENCE_S[key])
        # 15 s, not 5 s: "no image" is a persistent state during an outage,
        # not a transient, so a 5 s cadence would be a 12x idle-CPU/redraw
        # amplifier on a single-core ARMv6 box for the entire outage.
        assert val >= 15.0, \
            '%s no-image cadence %.1f is a busy-redraw on an ARMv6 Pi' % (
                key, val)
        assert val <= 30.0, \
            '%s no-image cadence %.1f is too slow to read as "alive"' % (
                key, val)


def test_render_loop_reads_the_table_not_a_literal():
    """A call-site literal would drift from the table the tests guard."""
    src = inspect.getsource(hp._run_render_loop)
    assert src.count('_CADENCE_S_NO_IMAGE') >= 2, \
        'render loop does not consult _CADENCE_S_NO_IMAGE for both panels'
    assert '15.0' not in src and '15 ' not in src, \
        'render loop hardcodes a no-image cadence instead of using the table'


def test_image_surfaces_are_hoisted_before_their_try():
    """The cadence line reads sdo_surf/surf OUTSIDE the panel's try, so a
    NameError there escapes into the render loop's consecutive_errors
    counter instead of the panel's own except."""
    src = inspect.getsource(hp._run_render_loop)
    for name in ('sdo_surf', 'surf'):
        init = src.find('%s = None' % name)
        assign = src.find('%s = _get_cached_image' % name)
        assert init != -1, '%s is not pre-initialised to None' % name
        assert assign != -1
        assert init < assign, \
            '%s is only bound inside the try; the cadence line can NameError' \
            % name


# --- formatting helpers ----------------------------------------------------

@pytest.mark.parametrize('secs,want', [
    (0, 'now'), (-30, 'now'), (1, 'now'),
    (5, '5s'), (14.2, '15s'), (59, '59s'),
    (60, '1m'), (61, '2m'), (900, '15m'),
    (3600, '1h'), (7200, '2h'), (90000, '1d'),
])
def test_fmt_eta(secs, want):
    assert hp._fmt_eta(secs) == want


@pytest.mark.parametrize('secs,want', [
    (0, '0s'), (59, '59s'), (60, '1m'), (119, '1m'),
    (3600, '1h'), (7199, '1h'), (86400, '1d'),
])
def test_fmt_age(secs, want):
    assert hp._fmt_age(secs) == want


@pytest.mark.parametrize('junk', [None, 'x', object(), float('nan'), [], {}])
def test_fmt_helpers_are_total(junk):
    for fn in (hp._fmt_eta, hp._fmt_age):
        out = fn(junk)
        assert isinstance(out, str) and out, '%r returned %r' % (fn, out)


# --- the four states -------------------------------------------------------

def test_first_fetch_in_flight_says_fetching():
    d = _Data()
    txt = hp._image_status_text(d, 'real-drap')
    assert 'fetching' in txt
    assert 'D-layer' in txt, 'status must name the feed, not just the panel'


def test_failing_feed_reports_a_retry_eta():
    now = time.time()
    d = _Data(image_fail_streak={'real-drap': 1},
              image_next_due={'real-drap': now + 15})
    txt = hp._image_status_text(d, 'real-drap')
    assert 'retry 15s' in txt, txt
    assert 'feed down' not in txt, 'one failure is not an outage yet'


def test_persistent_failure_is_called_a_down_feed():
    now = time.time()
    d = _Data(image_fail_streak={'muf-map': hp._IMAGE_DOWN_AFTER_FAILS},
              image_next_due={'muf-map': now + 60})
    txt = hp._image_status_text(d, 'muf-map')
    assert 'feed down' in txt, txt
    assert 'retry 1m' in txt, txt
    assert 'MUF map' in txt


def test_status_is_two_short_lines_not_one_long_one():
    """The SDO content rect is 128 px wide at 720x450 — ~25 characters of the
    7 px monospace font. A single long line is truncated to noise."""
    now = time.time()
    d = _Data(image_fail_streak={'solar-image': 9},
              image_next_due={'solar-image': now + 60})
    txt = hp._image_status_text(d, 'solar-image')
    lines = txt.split('\n')
    assert len(lines) == 2, txt
    for line in lines:
        assert len(line) <= 24, 'line %r will not fit the SDO panel' % line


def test_undecodable_payload_is_reported_even_behind_a_good_surface():
    """The regression this state exists for: _get_cached_image keeps serving
    the last surface that decoded, so a newly-arrived bad payload leaves a
    perfectly healthy-looking panel. The verdict must come from
    _decode_failed_ts, never from `data.images.get(key) is not None`."""
    stamp = 1700000000.0
    d = _Data(images={'muf-map': b'<html>503</html>'},
              image_fetched_at={'muf-map': stamp})
    hp._decode_failed_ts['muf-map'] = stamp
    txt = hp._image_status_text(d, 'muf-map')
    assert 'not readable' in txt, txt
    assert 'MUF map' in txt


def test_decode_failure_from_an_older_fetch_is_not_reported():
    """A stale memo entry must not label a payload that decoded fine."""
    now = time.time()
    d = _Data(images={'muf-map': b'\x89PNG'},
              image_fetched_at={'muf-map': now})
    hp._decode_failed_ts['muf-map'] = now - 900.0
    assert hp._image_status_text(d, 'muf-map') is None


def test_fresh_image_says_nothing():
    now = time.time()
    d = _Data(images={'drap': b'\x89PNG'}, image_fetched_at={'drap': now})
    assert hp._image_status_text(d, 'drap') is None


def test_old_image_is_age_labelled():
    now = time.time()
    d = _Data(images={'drap': b'\x89PNG'},
              image_fetched_at={'drap': now - 7200})
    txt = hp._image_status_text(d, 'drap')
    assert txt == 'Aurora 2h old', txt


def test_server_reported_age_wins_over_the_client_fetch_stamp():
    """Tier 2.1 serve-stale: the client fetched the bytes a second ago, but
    the server answered them out of its persisted disk cache. Only
    /api/health knows the real content age (headers reach neither client)."""
    now = time.time()
    d = _Data(images={'muf-map': b'\x89PNG'},
              image_fetched_at={'muf-map': now},
              health={'muf_age': 18000})
    assert hp._image_status_text(d, 'muf-map') == 'MUF map 5h old'


def test_unknown_server_age_falls_back_to_the_client_stamp():
    now = time.time()
    d = _Data(images={'muf-map': b'\x89PNG'},
              image_fetched_at={'muf-map': now - 10800},
              health={'muf_age': -1})       # the server's "unknown" sentinel
    assert hp._image_status_text(d, 'muf-map') == 'MUF map 3h old'


def test_cumulative_images_dict_does_not_read_as_healthy_forever():
    """data.images is cumulative (refresh_images does .update() and never
    deletes), so a key stays truthy after one good cycle no matter how long
    the feed has been down. The age label is what keeps that honest."""
    now = time.time()
    d = _Data(images={'enlil': b'\x89PNG'},
              image_fetched_at={'enlil': now - 4 * 3600},
              image_fail_streak={'enlil': 40},
              image_next_due={'enlil': now + 60})
    assert hp._image_status_text(d, 'enlil') == 'Enlil 4h old'


# --- totality --------------------------------------------------------------

class _Hostile(object):
    @property
    def images(self):
        raise RuntimeError('boom')


@pytest.mark.parametrize('data', [
    None,
    object(),
    _Hostile(),
    _Data(images=None),
    _Data(images='not a dict'),
    _Data(images={'drap': b'x'}, image_fetched_at='nope'),
    _Data(images={'drap': b'x'}, image_fetched_at={'drap': 'yesterday'}),
    _Data(images={}, image_fail_streak={'drap': 'many'}),
    _Data(images={}, image_fail_streak={'drap': 3}, image_next_due={'drap': 'soon'}),
    _Data(images={'drap': b'x'}, health=[1, 2, 3]),
    _Data(images={'drap': b'x'}, health={'drap_age': 'old'}),
    _Data(images={'drap': b'x'}, health={'drap_age': True}),
    _Data(last_image_refresh=None),
])
@pytest.mark.parametrize('key', ['drap', 'muf-map', 'nonsense-key', None])
def test_image_status_text_is_total(data, key):
    out = hp._image_status_text(data, key)
    assert out is None or isinstance(out, str)


def test_image_status_text_never_returns_an_empty_string():
    """An empty string would paint nothing while looking like a status."""
    now = time.time()
    for d in (_Data(),
              _Data(image_fail_streak={'drap': 2},
                    image_next_due={'drap': now + 5}),
              _Data(images={'drap': b'x'},
                    image_fetched_at={'drap': now - 99999})):
        out = hp._image_status_text(d, 'drap')
        assert out is None or out.strip(), repr(out)


def test_status_source_uses_the_decode_memo():
    src = inspect.getsource(hp._image_status_text)
    assert '_decode_failed_ts' in src, \
        'the "not readable" verdict must come from the Tier 1.2 decode memo'


def test_status_uses_get_everywhere_it_touches_a_dict():
    """getattr guards the attribute, never the key — a [key] on a dict that
    came off the wire is a KeyError that blanks the panel."""
    src = inspect.getsource(hp._image_status_text)
    for bad in ("data.images[", "images[key]", ".health[", "_IMAGE_LABEL["):
        assert bad not in src, 'subscript %r can raise inside a total fn' % bad


# --- drawing ---------------------------------------------------------------

def test_draw_image_status_param_is_appended_last(env):
    """tests/test_perf_alloc.py and tests/test_themes.py pass up to five
    positional args; inserting `status` earlier would silently rebind them."""
    params = list(inspect.signature(hp.draw_image).parameters)
    assert params == ['screen', 'rect', 'surface', 'fonts', 'theme',
                      'image_key', 'fetched_at', 'status'], params


def test_draw_image_renders_the_status_instead_of_loading(env, monkeypatch):
    fonts, theme = env
    surf = pygame.Surface((236, 102))
    rect = pygame.Rect(0, 0, 236, 102)
    seen = []
    real = hp._blit_text
    monkeypatch.setattr(hp, '_blit_text',
                        lambda s, f, t, c, x, y: (seen.append(t),
                                                  real(s, f, t, c, x, y))[1])
    hp.draw_image(surf, rect, None, fonts, theme,
                  status='D-layer: feed down\nretry 15s')
    assert 'D-layer: feed down' in seen
    assert 'retry 15s' in seen
    assert not any('loading' in s for s in seen), seen


def test_draw_image_without_status_keeps_the_loading_placeholder(env):
    fonts, theme = env
    surf = pygame.Surface((236, 102))
    seen = []
    real = hp._blit_text
    try:
        hp._blit_text = lambda s, f, t, c, x, y: (seen.append(t),
                                                  real(s, f, t, c, x, y))[1]
        hp.draw_image(surf, pygame.Rect(0, 0, 236, 102), None, fonts, theme)
    finally:
        hp._blit_text = real
    assert seen == ['image loading...'], seen


def test_status_panel_is_never_blank(env):
    """The whole point of the tier: no state paints an empty panel."""
    fonts, theme = env
    rect = pygame.Rect(0, 0, 128, 53)          # the SDO inner rect at 720x450
    for status in (None,
                   'SDO: fetching...',
                   'SDO: feed down\nretry 15s',
                   'SDO: image data\nnot readable'):
        surf = pygame.Surface((128, 53))
        surf.fill(SENTINEL)
        hp.draw_image(surf, rect, None, fonts, theme, status=status)
        raw = pygame.image.tostring(surf, 'RGB')
        assert raw != SENTINEL_B * (128 * 53), \
            'panel painted nothing for status %r' % (status,)


def test_status_overlays_a_painted_image(env):
    fonts, theme = env
    rect = pygame.Rect(0, 0, 236, 102)
    src = pygame.Surface((472, 204))
    src.fill((90, 90, 90))

    plain = pygame.Surface((236, 102))
    plain.fill(SENTINEL)
    hp.draw_image(plain, rect, src, fonts, theme)

    labelled = pygame.Surface((236, 102))
    labelled.fill(SENTINEL)
    hp.draw_image(labelled, rect, src, fonts, theme, status='MUF map 5h old')

    assert pygame.image.tostring(plain, 'RGB') != \
        pygame.image.tostring(labelled, 'RGB'), \
        'a stale image was painted with no age label — serve-stale without a ' \
        'label is worse than blank for someone making a band decision'


@pytest.mark.parametrize('size', [(128, 53), (236, 102), (60, 10), (300, 200)])
@pytest.mark.parametrize('status', ['SDO: fetching...',
                                    'SDO: feed down\nretry 15s',
                                    'MUF map 5h old'])
def test_status_stays_inside_its_rect(env, size, status):
    """Same invariant as tests/test_panel_containment.py: fill with a
    sentinel, draw, erase the rect, and demand the surface is untouched."""
    fonts, theme = env
    w, h = size
    for surface in (None, pygame.Surface((w * 2, h * 2))):
        if surface is not None:
            surface.fill((80, 80, 80))
        screen = pygame.Surface((w + 40, h + 40))
        screen.fill(SENTINEL)
        rect = pygame.Rect(20, 20, w, h)
        hp.draw_image(screen, rect, surface, fonts, theme, status=status)
        screen.fill(SENTINEL, rect)
        raw = pygame.image.tostring(screen, 'RGB')
        assert raw == SENTINEL_B * ((w + 40) * (h + 40)), \
            'status %r painted outside %r at %r' % (status, tuple(rect), size)


def test_draw_image_survives_a_hostile_status(env):
    """draw_image is called with the status inline; a bad value must not
    take the image down with it."""
    fonts, theme = env
    screen = pygame.Surface((236, 102))
    rect = pygame.Rect(0, 0, 236, 102)
    src = pygame.Surface((472, 204))
    for bad in (object(), 12345, [1, 2], b'bytes'):
        hp.draw_image(screen, rect, None, fonts, theme, status=bad)
        hp.draw_image(screen, rect, src, fonts, theme, status=bad)


def test_status_line_helper_does_not_construct_fonts():
    src = inspect.getsource(hp._draw_status_lines)
    assert 'Font(' not in src, \
        '_draw_status_lines allocates a Font per redraw'


# --- the no-image cadence, end to end --------------------------------------

class _LoopData(object):
    """Enough HamClockData surface to drive _run_render_loop offline."""

    def __init__(self):
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


class _FrameClock(object):
    """A clock the injected-event generator steps once per rendered frame."""

    def __init__(self, start=1700000000.0):
        self.now = start

    def time(self):
        return self.now


def _drive_render_loop(monkeypatch, frames, dt, have_image):
    """Run _run_render_loop for `frames` frames of `dt` simulated seconds and
    return how many times the SDO panel called draw_image."""
    clk = _FrameClock()
    fake_time = type('t', (), {
        'time': staticmethod(clk.time),
        'sleep': staticmethod(lambda s: None),
        'gmtime': staticmethod(time.gmtime),
        'strftime': staticmethod(time.strftime),
    })
    monkeypatch.setattr(hp, 'time', fake_time)
    monkeypatch.setattr(hp, 'HamClockData', _LoopData)

    calls = {'sdo': 0}
    real_draw = hp.draw_image

    def counting(screen, rect, surface, fonts=None, theme=None,
                 image_key=None, fetched_at=None, status=None):
        if image_key == 'solar-image':
            calls['sdo'] += 1
        return real_draw(screen, rect, surface, fonts, theme,
                         image_key, fetched_at, status)
    monkeypatch.setattr(hp, 'draw_image', counting)

    if have_image:
        img = pygame.Surface((64, 64))
        monkeypatch.setattr(hp, '_get_cached_image',
                            lambda data, key, c, cts: img)
    else:
        monkeypatch.setattr(hp, '_get_cached_image',
                            lambda data, key, c, cts: None)

    def gen():
        for _ in range(frames):
            clk.now += dt
            yield []
        yield [pygame.event.Event(pygame.QUIT)]

    pygame.init()
    pygame.font.init()
    screen = pygame.display.set_mode((720, 450))
    try:
        hp._run_render_loop(screen, hp._make_fonts(), dict(hp.THEMES['kstate']),
                            {}, injected_iter=gen())
    finally:
        pygame.init()
        pygame.font.init()
    return calls['sdo']


def test_panel_with_no_image_redraws_on_the_faster_cadence(monkeypatch):
    """A retry countdown that only moves once a minute reads as a hang."""
    frames = 6
    dt = 16.0        # > _CADENCE_S_NO_IMAGE['sdo'], << _CADENCE_S['sdo']
    blank = _drive_render_loop(monkeypatch, frames, dt, have_image=False)
    assert blank >= frames - 1, \
        'blank SDO panel redrew only %d times in %d frames of %.0f s' % (
            blank, frames, dt)


def test_panel_with_an_image_keeps_the_slow_cadence(monkeypatch):
    frames = 6
    dt = 16.0
    with_img = _drive_render_loop(monkeypatch, frames, dt, have_image=True)
    assert with_img <= 3, \
        'SDO panel redrew %d times in %.0f s with an image up — the 60 s ' \
        'cadence is not being applied' % (with_img, frames * dt)
