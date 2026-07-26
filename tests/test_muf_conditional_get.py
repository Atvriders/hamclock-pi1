"""Tier 2.2 — Accept-Encoding: gzip + If-Modified-Since on all five fetchers.

Measured against the live upstreams on 2026-07-26:
  * prop.kc2g.com serves the MUF SVG gzipped: 361,111 B -> 145,316 B.
  * IMS + Accept-Encoding: gzip returns 304 with an empty body.
  * urllib does NOT auto-inflate, so gzip.decompress is mandatory.
  * The ETag is deliberately NOT replayed: KC2G is Apache/mod_deflate, which
    appends '-gzip' outbound without stripping it inbound (If-None-Match with
    that ETag returns 200 + the full body), and RFC 7232 makes If-None-Match
    win when both validators are sent.
"""
import gzip

import pytest

import server


@pytest.fixture(autouse=True)
def _isolate():
    """Every global this module touches is process-wide."""
    cache = dict(server.CACHE)
    lm = dict(server._HTTP_LAST_MODIFIED)
    last = dict(server._PERSIST_LAST)
    src = server._MUF_PNG_SOURCE
    server._HTTP_LAST_MODIFIED.clear()
    yield
    server.CACHE.clear()
    server.CACHE.update(cache)
    server._HTTP_LAST_MODIFIED.clear()
    server._HTTP_LAST_MODIFIED.update(lm)
    server._PERSIST_LAST.clear()
    server._PERSIST_LAST.update(last)
    server._MUF_PNG_SOURCE = src


class _Headers(dict):
    def get(self, k, default=None):
        for key, value in self.items():
            if key.lower() == k.lower():
                return value
        return default


class _Resp:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = _Headers(headers or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_recording(monkeypatch, responses):
    """Patch server.urlopen; `responses` is a list of _Resp or Exception."""
    seen = []
    queue = list(responses)

    def fake(req, timeout=None):
        seen.append(req)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item
    monkeypatch.setattr(server, 'urlopen', fake)
    return seen


def _http_error(code):
    return server.HTTPError('http://x/', code, 'Not Modified', {}, None)


# ---------------------------------------------------------------------------
# The import itself is load-bearing
# ---------------------------------------------------------------------------


def test_httperror_is_imported_by_name():
    """server.py has no `import urllib`, so `except urllib.error.HTTPError`
    would raise NameError *while matching the except clause* — escaping the
    trailing `except Exception` and killing background_fetcher on the first
    network blip, i.e. exactly the cold boot this tier optimises."""
    assert server.HTTPError is not None
    from urllib.error import HTTPError
    assert server.HTTPError is HTTPError
    assert not hasattr(server, 'urllib')


# ---------------------------------------------------------------------------
# _conditional_get
# ---------------------------------------------------------------------------


def test_sends_gzip_and_no_ims_on_the_first_request(monkeypatch):
    seen = _urlopen_recording(monkeypatch, [_Resp(b'BODY')])
    data, not_modified = server._conditional_get('http://x/a')
    assert (data, not_modified) == (b'BODY', False)
    req = seen[0]
    assert req.get_header('Accept-encoding') == 'gzip'
    assert req.get_header('If-modified-since') is None


def test_replays_last_modified_on_the_second_request(monkeypatch):
    seen = _urlopen_recording(monkeypatch, [
        _Resp(b'BODY', {'Last-Modified': 'Sun, 26 Jul 2026 15:10:54 GMT'}),
        _http_error(304),
    ])
    server._conditional_get('http://x/a')
    data, not_modified = server._conditional_get('http://x/a')
    assert not_modified is True
    assert data is None
    assert seen[1].get_header('If-modified-since') == \
        'Sun, 26 Jul 2026 15:10:54 GMT'


def test_never_sends_if_none_match(monkeypatch):
    """mod_deflate appends '-gzip' to the ETag outbound and does not strip it
    inbound, and RFC 7232 makes If-None-Match beat If-Modified-Since."""
    seen = _urlopen_recording(monkeypatch, [
        _Resp(b'BODY', {'ETag': '"58297-6578503184ae4-gzip"',
                        'Last-Modified': 'Sun, 26 Jul 2026 15:10:54 GMT'}),
        _Resp(b'BODY2', {'ETag': '"x-gzip"'}),
    ])
    server._conditional_get('http://x/a')
    server._conditional_get('http://x/a')
    assert seen[1].get_header('If-none-match') is None


def test_inflates_a_gzip_response(monkeypatch):
    """urllib does not auto-inflate; without the explicit decompress the
    fetchers would cache 145 KB of deflate stream and hand it to cairosvg."""
    payload = b'<svg>hello</svg>' * 100
    _urlopen_recording(monkeypatch, [
        _Resp(gzip.compress(payload), {'Content-Encoding': 'gzip'})])
    data, _ = server._conditional_get('http://x/a')
    assert data == payload


def test_leaves_an_identity_response_alone(monkeypatch):
    _urlopen_recording(monkeypatch, [_Resp(b'\xff\xd8\xffPLAIN')])
    data, _ = server._conditional_get('http://x/a')
    assert data == b'\xff\xd8\xffPLAIN'


def test_validators_are_keyed_per_url_not_per_fetcher(monkeypatch):
    """enlil/drap/real-drap each iterate a two-URL fallback list, and NOAA's
    Last-Modified is a shared mirror-sync stamp (measured identical across
    unrelated products) — a per-fetcher key would replay one product's
    validator against a different one."""
    seen = _urlopen_recording(monkeypatch, [
        _Resp(b'A', {'Last-Modified': 'STAMP-A'}),
        _Resp(b'B', {'Last-Modified': 'STAMP-B'}),
        _Resp(b'A2'), _Resp(b'B2'),
    ])
    server._conditional_get('http://x/primary')
    server._conditional_get('http://x/fallback')
    server._conditional_get('http://x/primary')
    server._conditional_get('http://x/fallback')
    assert seen[2].get_header('If-modified-since') == 'STAMP-A'
    assert seen[3].get_header('If-modified-since') == 'STAMP-B'


def test_non_304_http_errors_still_propagate(monkeypatch):
    _urlopen_recording(monkeypatch, [_http_error(500)])
    with pytest.raises(server.HTTPError):
        server._conditional_get('http://x/a')


def test_survives_a_response_object_with_no_headers(monkeypatch):
    """The suite's urlopen doubles expose read() only."""
    class _Bare:
        def read(self):
            return b'BODY'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(server, 'urlopen', lambda req, timeout=None: _Bare())
    assert server._conditional_get('http://x/a') == (b'BODY', False)


def test_validator_table_is_bounded(monkeypatch):
    _urlopen_recording(monkeypatch, [_Resp(b'B', {'Last-Modified': 'S'})])
    for i in range(server._LAST_MODIFIED_CAP * 3):
        server._conditional_get('http://x/frame-%d' % i)
    assert len(server._HTTP_LAST_MODIFIED) <= server._LAST_MODIFIED_CAP


def test_a_derived_frame_url_does_not_record_a_validator(monkeypatch):
    _urlopen_recording(monkeypatch, [_Resp(b'B', {'Last-Modified': 'S'})])
    server._conditional_get('http://x/frame', record_lm=False)
    assert server._HTTP_LAST_MODIFIED == {}


# ---------------------------------------------------------------------------
# fetch_muf: the 304 re-rasterize
# ---------------------------------------------------------------------------


FAKE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
FAKE_PNG = b'\x89PNG\r\n\x1a\nFAKEPNGBODYIEND\xaeB\x60\x82'


def test_muf_304_with_a_good_png_does_no_work(monkeypatch):
    _urlopen_recording(monkeypatch, [_http_error(304)])
    monkeypatch.setattr(server, '_rasterize_muf',
                        lambda b: pytest.fail('must not re-render'))
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = FAKE_PNG
    server.CACHE['muf_image_png_updated'] = 1000.0

    server.fetch_muf()

    assert server.CACHE['muf_image_png'] == FAKE_PNG
    assert server.CACHE['muf_image_png_updated'] == 1000.0


def test_muf_304_with_no_png_re_rasterizes_the_cached_svg(monkeypatch):
    """CRITICAL. Without this, one failed rasterize plus a stalled upstream
    generator strands /api/muf-map on 503 permanently: the only code path
    that renders is the one the 304 just returned early from."""
    _urlopen_recording(monkeypatch, [_http_error(304)])
    rendered = []

    def rasterize(b):
        rendered.append(b)
        return FAKE_PNG
    monkeypatch.setattr(server, '_rasterize_muf', rasterize)
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_updated'] = 2000.0
    server.CACHE['muf_image_png'] = None
    server.CACHE['muf_image_png_updated'] = 0

    server.fetch_muf()

    assert rendered == [FAKE_SVG]
    assert server.CACHE['muf_image_png'] == FAKE_PNG


def test_muf_304_re_rasterize_inherits_the_svg_epoch(monkeypatch):
    """Re-rendering a six-hour-old SVG must not make the map claim to be
    fresh — /api/health's muf_age is what the client labels the panel with."""
    _urlopen_recording(monkeypatch, [_http_error(304)])
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: FAKE_PNG)
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_updated'] = 2000.0
    server.CACHE['muf_image_png'] = None
    server.CACHE['muf_image_png_updated'] = 0

    server.fetch_muf()

    assert server.CACHE['muf_image_png_updated'] == 2000.0


def test_muf_304_with_nothing_cached_at_all_is_a_noop(monkeypatch):
    _urlopen_recording(monkeypatch, [_http_error(304)])
    monkeypatch.setattr(server, '_rasterize_muf',
                        lambda b: pytest.fail('nothing to render'))
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = None

    server.fetch_muf()  # must not raise

    assert server.CACHE['muf_image_png'] is None


def test_muf_200_inflates_and_caches(monkeypatch):
    _urlopen_recording(monkeypatch, [
        _Resp(gzip.compress(FAKE_SVG), {'Content-Encoding': 'gzip',
                                        'Last-Modified': 'STAMP'})])
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: FAKE_PNG)
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = None

    server.fetch_muf()

    assert server.CACHE['muf_image'] == FAKE_SVG
    assert server.CACHE['muf_image_png'] == FAKE_PNG
    assert server._HTTP_LAST_MODIFIED[server.MUF_URL] == 'STAMP'


# ---------------------------------------------------------------------------
# The image fetchers: a 304 returns, it does not fall through to the fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('fetch_name,cache_key', [
    ('fetch_enlil', 'enlil_image'),
    ('fetch_drap', 'drap_image'),
    ('fetch_real_drap', 'real_drap_image'),
    ('fetch_sdo', 'solar_image'),
])
def test_304_returns_instead_of_falling_through(monkeypatch, fetch_name,
                                                cache_key):
    """`continue` here would re-download the same product from the fallback
    URL on every single cycle — the opposite of what the 304 bought us."""
    seen = _urlopen_recording(monkeypatch, [_http_error(304)])
    server.CACHE[cache_key] = b'\xff\xd8\xffCACHED\xff\xd9'

    getattr(server, fetch_name)()

    assert len(seen) == 1, 'a 304 must not try the fallback URL'
    assert server.CACHE[cache_key] == b'\xff\xd8\xffCACHED\xff\xd9'


def test_note_unchanged_keeps_the_validator_when_the_body_is_cached():
    server.CACHE['drap_image'] = b'BODY'
    server._HTTP_LAST_MODIFIED['http://x/drap'] = 'STAMP'
    assert server._note_unchanged('Aurora', 'drap_image', 'http://x/drap') is True
    assert server._HTTP_LAST_MODIFIED['http://x/drap'] == 'STAMP'


def test_note_unchanged_drops_a_validator_with_no_body_behind_it():
    """Otherwise we replay it forever and the slot never refills."""
    server.CACHE['drap_image'] = None
    server._HTTP_LAST_MODIFIED['http://x/drap'] = 'STAMP'
    assert server._note_unchanged('Aurora', 'drap_image', 'http://x/drap') is False
    assert 'http://x/drap' not in server._HTTP_LAST_MODIFIED


@pytest.mark.parametrize('fetch_name,cache_key', [
    ('fetch_enlil', 'enlil_image'),
    ('fetch_drap', 'drap_image'),
    ('fetch_real_drap', 'real_drap_image'),
])
def test_304_with_an_empty_slot_falls_through_to_the_fallback(
        monkeypatch, fetch_name, cache_key):
    """The pathological case: a validator whose body we no longer hold. Here
    (and only here) trying the fallback URL is the right move."""
    seen = _urlopen_recording(monkeypatch, [_http_error(304)])
    server.CACHE[cache_key] = None

    getattr(server, fetch_name)()

    assert len(seen) == 2
    assert server.CACHE[cache_key] is None
    assert server._HTTP_LAST_MODIFIED == {}


@pytest.mark.parametrize('fetch_name,cache_key', [
    ('fetch_enlil', 'enlil_image'),
    ('fetch_drap', 'drap_image'),
    ('fetch_real_drap', 'real_drap_image'),
    ('fetch_sdo', 'solar_image'),
])
def test_image_fetchers_inflate_gzip(monkeypatch, fetch_name, cache_key):
    body = b'\xff\xd8\xff' + b'JPEGBODY' * 50 + b'\xff\xd9'
    _urlopen_recording(monkeypatch, [
        _Resp(gzip.compress(body), {'Content-Encoding': 'gzip'})])
    server.CACHE[cache_key] = None

    getattr(server, fetch_name)()

    assert server.CACHE[cache_key] == body


def test_background_fetcher_survives_a_network_error(monkeypatch):
    """The NameError trap this tier avoids showed up here: an exception
    raised while MATCHING an except clause escapes `except Exception` and
    kills the daemon thread, freezing the dashboard on empty data."""
    _urlopen_recording(monkeypatch, [OSError('network is unreachable')])
    for name in ('fetch_muf', 'fetch_enlil', 'fetch_drap', 'fetch_real_drap',
                 'fetch_sdo'):
        getattr(server, name)()  # none of these may raise
