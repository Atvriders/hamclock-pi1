"""Tier 2c: ETag + 304 conditional GET for /api/solar, /api/bands, /api/dxspots."""
import json
import server


def test_etag_helper_formats_timestamp():
    server.CACHE['solar_updated'] = 1234567890.0
    assert server._etag_for('solar_updated') == '"1234567890.000"'


def test_etag_helper_zero_when_never_fetched():
    server.CACHE['bands_updated'] = 0
    assert server._etag_for('bands_updated') == '"0.000"'


def _make_handler(path, headers=None):
    """Build a minimal Handler instance suitable for do_GET dispatch."""
    h = type('H', (), {})()
    h.command = 'GET'
    h.path = path
    h.headers = headers or {}
    h.responses = []
    h.headers_out = []
    h.body = b''
    h.send_response = lambda code: h.responses.append(code)
    h.send_header = lambda k, v: h.headers_out.append((k, v))
    h.end_headers = lambda: None
    h.send_error = lambda code, msg=None: h.responses.append(code)

    class FakeW:
        def write(self, b): h.body += b
    h.wfile = FakeW()
    h.send_json_with_etag = server.Handler.send_json_with_etag.__get__(h)
    h.send_json = server.Handler.send_json.__get__(h)
    return h


def test_solar_endpoint_emits_etag_on_first_get():
    server.CACHE['solar_updated'] = 1000.0
    server.CACHE['solar_bytes'] = b'{"sfi":"100"}'
    h = _make_handler('/api/solar')
    server.Handler.do_GET(h)
    etags = [v for (k, v) in h.headers_out if k == 'ETag']
    assert etags == ['"1000.000"']
    assert h.responses == [200]
    assert h.body == b'{"sfi":"100"}'


def test_solar_endpoint_returns_304_on_matching_if_none_match():
    server.CACHE['solar_updated'] = 1000.0
    server.CACHE['solar_bytes'] = b'{"sfi":"100"}'
    h = _make_handler('/api/solar',
                      headers={'If-None-Match': '"1000.000"'})
    server.Handler.do_GET(h)
    assert h.responses == [304]
    assert h.body == b''


def test_solar_endpoint_returns_body_on_stale_if_none_match():
    server.CACHE['solar_updated'] = 2000.0
    server.CACHE['solar_bytes'] = b'{"sfi":"200"}'
    h = _make_handler('/api/solar',
                      headers={'If-None-Match': '"1000.000"'})
    server.Handler.do_GET(h)
    assert h.responses == [200]
    assert h.body == b'{"sfi":"200"}'


def test_solar_endpoint_never_304_when_never_fetched():
    server.CACHE['solar_updated'] = 0
    server.CACHE['solar_bytes'] = None
    h = _make_handler('/api/solar', headers={'If-None-Match': '"0.000"'})
    server.Handler.do_GET(h)
    # Even with matching '0.000' tag, an unfetched cache must return body.
    assert h.responses == [200]


def test_dxspots_endpoint_emits_etag():
    server.CACHE['dx_updated'] = 3000.0
    server.CACHE['dxspots_bytes'] = b'[]'
    h = _make_handler('/api/dxspots')
    server.Handler.do_GET(h)
    etags = [v for (k, v) in h.headers_out if k == 'ETag']
    assert etags == ['"3000.000"']


def test_bands_endpoint_emits_etag():
    server.CACHE['bands_updated'] = 4000.0
    server.CACHE['bands_bytes'] = b'{}'
    h = _make_handler('/api/bands')
    server.Handler.do_GET(h)
    etags = [v for (k, v) in h.headers_out if k == 'ETag']
    assert etags == ['"4000.000"']


def test_client_sends_if_none_match_header(monkeypatch):
    """hamclock_data tracks the most recent ETag and replays it."""
    from hamclock_data import HamClockData
    d = HamClockData()
    d._etags['/api/solar'] = '"1000.000"'

    seen_headers = {}
    class FakeResp:
        def __init__(self, body, etag=None):
            self._body = body
            self.headers = {'ETag': etag} if etag else {}
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        for k, v in req.header_items():
            seen_headers[k] = v
        return FakeResp(b'{"sfi":"new"}', etag='"2000.000"')

    monkeypatch.setattr('hamclock_data.urlopen', fake_urlopen)
    result = d._fetch_json('/api/solar')
    assert seen_headers.get('If-none-match') == '"1000.000"' or \
           seen_headers.get('If-None-Match') == '"1000.000"'
    assert result == {'sfi': 'new'}
    assert d._etags['/api/solar'] == '"2000.000"'
