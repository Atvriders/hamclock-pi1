"""Tier 1.3 — server boot order, fetch_sdo, pure-cache /api/solar-image.

Covers the four defects this tier removes:

  1. /api/solar-image was the only endpoint doing upstream I/O on a handler
     thread, with a 20 s timeout equal to the native client's IMAGE_TIMEOUT
     and no negative cache — so one slow SDO ate the client's whole serial
     image budget before it ever reached /api/muf-map.
  2. 'real-drap' backs the propagation panel's DEFAULT tab but was fetched
     LAST at boot, ~56 s after the client's single image refresh gave up.
  3. The solar/DX fast-retry (the network-not-ready recovery path) sat BELOW
     five image fetches, delaying recovery by ~100 s.
  4. A boot-time image failure was not retried until the next 900 s tick.

Plus the ThreadingHTTPServer write-order race: the *_updated stamps are the
ETag source, so they must be written AFTER the bodies they describe.
"""
import time

import pytest

import server


@pytest.fixture(autouse=True)
def _restore_cache():
    """Other modules in this suite read/write server.CACHE; put it back."""
    saved = dict(server.CACHE)
    yield
    server.CACHE.clear()
    server.CACHE.update(saved)


class _Stop(BaseException):
    """Escapes background_fetcher's `except Exception` so the loop ends."""


class FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# fetch_sdo
# ---------------------------------------------------------------------------


def test_fetch_sdo_exists_and_populates_cache(monkeypatch):
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: FakeResp(b'\xff\xd8JPEGBODY'))
    server.CACHE['solar_image'] = None
    server.CACHE['solar_image_updated'] = 0

    server.fetch_sdo()

    assert server.CACHE['solar_image'] == b'\xff\xd8JPEGBODY'
    assert server.CACHE['solar_image_updated'] > 0


def test_fetch_sdo_never_writes_none_over_a_good_image(monkeypatch):
    def boom(req, timeout=None):
        raise OSError('upstream down')
    monkeypatch.setattr(server, 'urlopen', boom)
    server.CACHE['solar_image'] = b'GOOD'
    server.CACHE['solar_image_updated'] = 1234.0

    server.fetch_sdo()  # must not raise

    assert server.CACHE['solar_image'] == b'GOOD'
    assert server.CACHE['solar_image_updated'] == 1234.0


def test_fetch_sdo_rejects_empty_body(monkeypatch):
    monkeypatch.setattr(server, 'urlopen', lambda req, timeout=None: FakeResp(b''))
    server.CACHE['solar_image'] = b'GOOD'
    server.fetch_sdo()
    assert server.CACHE['solar_image'] == b'GOOD'


# ---------------------------------------------------------------------------
# /api/solar-image is a pure cache read
# ---------------------------------------------------------------------------


def _make_handler(path):
    h = type('H', (), {})()
    h.command = 'GET'
    h.path = path
    h.headers = {}
    h.responses = []
    h.headers_out = []
    h.body = b''
    h.send_response = lambda code: h.responses.append(code)
    h.send_header = lambda k, v: h.headers_out.append((k, str(v)))
    h.end_headers = lambda: None
    h.send_error = lambda code, msg=None: h.responses.append(code)

    class FakeW:
        def write(self, b):
            h.body += b
    h.wfile = FakeW()
    h.send_json = server.Handler.send_json.__get__(h)
    h.send_json_with_etag = server.Handler.send_json_with_etag.__get__(h)
    h.send_binary = server.Handler.send_binary.__get__(h)
    h.do_GET = server.Handler.do_GET.__get__(h)
    h._HEAD_UNSAFE_PREFIXES = server.Handler._HEAD_UNSAFE_PREFIXES
    h.do_HEAD = server.Handler.do_HEAD.__get__(h)
    return h


def test_solar_image_endpoint_does_no_upstream_io(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError('/api/solar-image must never fetch upstream')
    monkeypatch.setattr(server, 'urlopen', no_network)

    server.CACHE['solar_image'] = b'\xff\xd8SDO'
    server.CACHE['solar_image_updated'] = 1.0  # deliberately ancient
    h = _make_handler('/api/solar-image')
    h.do_GET()

    assert h.responses == [200]
    assert h.body == b'\xff\xd8SDO'
    ctypes = [v for (k, v) in h.headers_out if k.lower() == 'content-type']
    assert ctypes == ['image/jpeg']


def test_solar_image_endpoint_503_when_cold(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError('/api/solar-image must never fetch upstream')
    monkeypatch.setattr(server, 'urlopen', no_network)

    server.CACHE['solar_image'] = None
    h = _make_handler('/api/solar-image')
    h.do_GET()

    # 503 (try again shortly), not the old 502 — nothing upstream failed here.
    assert h.responses == [503]
    assert h.body == b''


def test_head_does_not_trigger_a_callsign_lookup(monkeypatch):
    def no_lookup(call):
        raise AssertionError('HEAD must not run an upstream callbook lookup')
    monkeypatch.setattr(server, 'lookup_callsign', no_lookup)

    h = _make_handler('/api/callsign/W1AW')
    h.command = 'HEAD'
    h.do_HEAD()

    assert h.responses == [405]


def test_head_still_serves_cached_endpoints(monkeypatch):
    server.CACHE['solar_image'] = b'\xff\xd8SDO'
    h = _make_handler('/api/solar-image')
    h.command = 'HEAD'
    h.do_HEAD()
    assert h.responses == [200]
    assert h.body == b''  # headers only


# ---------------------------------------------------------------------------
# Boot order + hoisted fast-retry
# ---------------------------------------------------------------------------


def _patch_fetchers(monkeypatch, order, fill=True):
    """Replace every fetcher with a recorder appending to `order`."""
    slots = {
        'fetch_hamqsl': ('solar', {'sfi': '100'}),
        'fetch_dx': ('dxspots', [{'dx': 'W1AW'}]),
        'fetch_real_drap': ('real_drap_image', b'RD'),
        'fetch_drap': ('drap_image', b'D'),
        'fetch_enlil': ('enlil_image', b'E'),
        'fetch_sdo': ('solar_image', b'S'),
        'fetch_muf': ('muf_image', b'M'),
    }
    for name, (key, value) in slots.items():
        def make(name=name, key=key, value=value):
            def rec():
                order.append(name)
                if fill:
                    server.CACHE[key] = value
            return rec
        monkeypatch.setattr(server, name, make())


def test_boot_order_puts_real_drap_first_and_muf_last(monkeypatch):
    order = []
    _patch_fetchers(monkeypatch, order)
    monkeypatch.setattr(server, '_image_fast_retry',
                        lambda *a, **kw: order.append('fast_retry'))

    def fake_sleep(_s):
        raise _Stop()
    monkeypatch.setattr(server.time, 'sleep', fake_sleep)

    server.CACHE['solar'] = None
    server.CACHE['dxspots'] = None
    for key in ('real_drap_image', 'drap_image', 'enlil_image',
                'solar_image', 'muf_image'):
        server.CACHE[key] = None

    with pytest.raises(_Stop):
        server.background_fetcher()

    assert order == [
        'fetch_hamqsl', 'fetch_dx',
        # 'real-drap' is the DEFAULT propagation tab's image: first.
        'fetch_real_drap', 'fetch_drap', 'fetch_enlil', 'fetch_sdo',
        # fetch_muf is the slowest (download + rasterize): last.
        'fetch_muf',
        'fast_retry',
    ]


def test_solar_dx_fast_retry_is_hoisted_above_the_images(monkeypatch):
    """The recovery path must not sit behind five image round trips."""
    order = []
    # fill=False: solar/dxspots stay empty so the retry loop actually runs.
    _patch_fetchers(monkeypatch, order, fill=False)
    monkeypatch.setattr(server, '_image_fast_retry',
                        lambda *a, **kw: order.append('fast_retry'))

    calls = {'n': 0}

    def fake_sleep(_s):
        calls['n'] += 1
        order.append('sleep')
        if calls['n'] > 6:  # 6 retry sleeps, then the periodic loop's
            raise _Stop()
    monkeypatch.setattr(server.time, 'sleep', fake_sleep)

    server.CACHE['solar'] = None
    server.CACHE['dxspots'] = None

    with pytest.raises(_Stop):
        server.background_fetcher()

    first_image = order.index('fetch_real_drap')
    assert order.count('sleep') == 7
    # All six retry sleeps land before the first image fetch.
    assert order[:first_image].count('sleep') == 6
    assert order[:first_image].count('fetch_hamqsl') == 7  # boot + 6 retries


# ---------------------------------------------------------------------------
# Bounded image fast-retry
# ---------------------------------------------------------------------------


def test_image_fast_retry_is_a_noop_when_everything_landed(monkeypatch):
    slept = []
    monkeypatch.setattr(server.time, 'sleep', lambda s: slept.append(s))
    order = []
    _patch_fetchers(monkeypatch, order)
    for key in ('real_drap_image', 'drap_image', 'enlil_image',
                'solar_image', 'muf_image'):
        server.CACHE[key] = b'x'

    server._image_fast_retry()

    assert order == []
    assert slept == []  # must not sleep before checking


def test_image_fast_retry_only_retries_empty_slots(monkeypatch):
    slept = []
    monkeypatch.setattr(server.time, 'sleep', lambda s: slept.append(s))
    order = []
    _patch_fetchers(monkeypatch, order)  # fill=True: succeeds on retry
    for key in ('real_drap_image', 'drap_image', 'enlil_image', 'muf_image'):
        server.CACHE[key] = b'x'
    server.CACHE['solar_image'] = None

    server._image_fast_retry()

    assert order == ['fetch_sdo']
    assert slept == [server.IMAGE_FAST_RETRY_SLEEP_S]


def test_image_fast_retry_is_bounded(monkeypatch):
    slept = []
    monkeypatch.setattr(server.time, 'sleep', lambda s: slept.append(s))
    order = []
    _patch_fetchers(monkeypatch, order, fill=False)  # never recovers
    for key in ('real_drap_image', 'drap_image', 'enlil_image',
                'solar_image', 'muf_image'):
        server.CACHE[key] = None

    server._image_fast_retry()

    assert server.IMAGE_FAST_RETRY_PASSES == 2
    assert len(slept) == server.IMAGE_FAST_RETRY_PASSES
    assert order.count('fetch_muf') == server.IMAGE_FAST_RETRY_PASSES
    assert len(order) == 5 * server.IMAGE_FAST_RETRY_PASSES


def test_image_fast_retry_probes_the_svg_slot_not_the_png(monkeypatch):
    """A failed rasterize is a deterministic timeout, not a transient.

    Retrying fetch_muf on an empty muf_image_png would re-run a 45 s render
    twice more during the boot window for no chance of success.
    """
    keys = [k for k, _name in server._IMAGE_FETCH_ORDER]
    assert 'muf_image' in keys
    assert 'muf_image_png' not in keys


# ---------------------------------------------------------------------------
# Stagger comb
# ---------------------------------------------------------------------------


def test_stagger_is_a_five_slot_180s_comb():
    off = server._image_stagger_offsets(10000.0)
    assert len(off) == 5
    assert server.IMAGE_STAGGER_S == 180
    vals = sorted(off.values())
    assert vals == [10000.0 - 720, 10000.0 - 540, 10000.0 - 360,
                    10000.0 - 180, 10000.0]
    # 5 slots x 180 s exactly fills the 900 s image cadence.
    assert len(off) * server.IMAGE_STAGGER_S == 900


def test_stagger_gives_the_earliest_slot_to_the_first_fetched():
    off = server._image_stagger_offsets(0.0)
    # real_drap is fetched first at boot => oldest => refreshes first.
    assert off['real_drap'] == min(off.values())
    # muf is fetched last and is the most expensive => full 900 s.
    assert off['muf'] == max(off.values()) == 0.0


def test_every_image_fetcher_name_resolves():
    """_image_fetchers() looks fetchers up by name; a typo would kill the
    background thread on the first boot."""
    pairs = server._image_fetchers()
    assert [f.__name__ for _k, f in pairs] == [
        'fetch_real_drap', 'fetch_drap', 'fetch_enlil',
        'fetch_sdo', 'fetch_muf',
    ]
    for key, _fn in pairs:
        assert key in server.CACHE


def test_periodic_loop_refreshes_sdo(monkeypatch):
    """fetch_sdo must be on the 900 s cadence, not only at boot."""
    order = []
    _patch_fetchers(monkeypatch, order)
    monkeypatch.setattr(server, '_image_fast_retry', lambda *a, **kw: None)

    real_time = time.time
    clock = {'t': None}

    def fake_time():
        if clock['t'] is None:
            clock['t'] = real_time()
            return clock['t']
        return clock['t'] + 1000.0
    monkeypatch.setattr(server.time, 'time', fake_time)

    calls = {'n': 0}

    def fake_sleep(_s):
        calls['n'] += 1
        if calls['n'] > 1:
            raise _Stop()
    monkeypatch.setattr(server.time, 'sleep', fake_sleep)

    server.CACHE['solar'] = {'sfi': '1'}
    server.CACHE['dxspots'] = [{}]

    with pytest.raises(_Stop):
        server.background_fetcher()

    # boot pass + one periodic pass 1000 s later: every image product is due.
    assert order.count('fetch_sdo') == 2


# ---------------------------------------------------------------------------
# Write-order race (ThreadingHTTPServer)
# ---------------------------------------------------------------------------


def _stamp_spy(monkeypatch, watch_key):
    """Snapshot CACHE[watch_key] at the moment time.time() is called.

    The *_updated stamps are the only time.time() calls inside the fetchers,
    so this observes exactly the window a concurrent request would land in.
    """
    seen = []
    real_time = time.time

    def spy():
        seen.append(server.CACHE.get(watch_key))
        return real_time()
    monkeypatch.setattr(server.time, 'time', spy)
    return seen


HAMQSL_XML = (
    '<?xml version="1.0"?><solar><solardata>'
    '<solarflux>150</solarflux><sunspots>42</sunspots><kindex>2</kindex>'
    '<band name="80m-40m" time="day">Good</band>'
    '</solardata></solar>'
).encode('utf-8')


def test_fetch_hamqsl_writes_body_before_etag_stamp(monkeypatch):
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: FakeResp(HAMQSL_XML))
    server.CACHE['solar_bytes'] = None
    server.CACHE['bands_bytes'] = None
    server.CACHE['solar_updated'] = 0
    seen = _stamp_spy(monkeypatch, 'solar_bytes')

    server.fetch_hamqsl()

    assert server.CACHE['solar_updated'] > 0
    assert seen, 'expected the *_updated stamp to call time.time()'
    # The body was already published when the stamp (== the ETag) was taken.
    # Otherwise a request racing in between gets the NEW ETag with the OLD
    # body, and is then 304'd against it for a full 5-minute cadence.
    assert seen[0] is not None


def test_fetch_hamqsl_stamps_solar_and_bands_together(monkeypatch):
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: FakeResp(HAMQSL_XML))
    server.fetch_hamqsl()
    assert server.CACHE['solar_updated'] == server.CACHE['bands_updated']


DX_CSV = 'W1AW^14025.0^DX1ABC^cq^1200^x^x^x^x^x^United States\n'


def test_fetch_dx_writes_body_before_etag_stamp(monkeypatch):
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: FakeResp(DX_CSV.encode()))
    server.CACHE['dxspots_bytes'] = None
    server.CACHE['dx_updated'] = 0
    seen = _stamp_spy(monkeypatch, 'dxspots_bytes')

    server.fetch_dx()

    assert server.CACHE['dx_updated'] > 0
    assert seen
    assert seen[0] is not None


# ---------------------------------------------------------------------------
# Log disambiguation
# ---------------------------------------------------------------------------


def test_aurora_and_drap_log_lines_are_distinguishable(capsys, monkeypatch):
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: FakeResp(b'IMGBYTES'))
    server.fetch_drap()
    server.fetch_real_drap()
    out = capsys.readouterr().out
    assert 'Aurora updated' in out
    assert 'DRAP updated' in out
    assert out.count('DRAP updated') == 1
