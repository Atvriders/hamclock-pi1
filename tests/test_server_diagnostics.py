"""GET /api/diagnostics — the read-only counter snapshot the client embeds.

Why this endpoint exists: nobody working on this project has ARMv6 hardware,
so every Pi 1 figure here is extrapolated from x86 and unverified. The two
questions it is built to answer are "is PHASE2_TIMEOUT_S=45 actually firing on
real hardware" (muf.rasterize_timeout vs muf.last_render_s) and "is the
conditional GET actually producing 304s in the field" (cache.*.http_304 vs
http_full).

The three contracts that matter most, and are tested hardest below:

  * it never raises — including immediately at boot, with every counter still
    at its initial value and nothing in CACHE. A diagnostics endpoint that can
    500 tells the maintainer nothing at exactly the moment they need it.
  * it performs NO network I/O. Upstream fetching on a handler thread is the
    bug that was just removed from /api/solar-image, where one 20 s stall ate
    the native client's entire serial image budget.
  * it leaks nothing. Failures are recorded as exception TYPE names, never
    str(e), because an exception message can carry a proxy URL with
    credentials in it and this body is what the operator transmits.
"""
import json
import subprocess
import time

import pytest

import server


ALL_COUNTER_NAMES = (
    '_MUF_RASTERIZE_OK', '_MUF_RASTERIZE_TIMEOUT', '_MUF_RASTERIZE_FAIL',
    '_MUF_LAST_RENDER_S', '_MUF_LAST_TIMEOUT_BUDGET_S', '_MUF_LAST_SVG_BYTES',
    '_MUF_LAST_SLIM_BYTES', '_MUF_LAST_PNG_BYTES', '_MUF_SLIM_OK',
    '_MUF_SLIM_DECLINED', '_MUF_UNSLIMMED_RETRIES', '_MUF_LAST_SERVED',
    '_MUF_PNG_SOURCE', '_DISK_RESTORED', '_BOOT_DISK_WARM',
)


@pytest.fixture(autouse=True)
def _isolate():
    """Every counter here is process-wide and the suite runs in one process."""
    saved = {n: getattr(server, n) for n in ALL_COUNTER_NAMES}
    cache = dict(server.CACHE)
    stats = {k: dict(v) for k, v in server._FETCH_STATS.items()}
    first = dict(server._FIRST_OK_S)
    lm = dict(server._HTTP_LAST_MODIFIED)
    try:
        yield
    finally:
        for n, v in saved.items():
            setattr(server, n, v)
        server.CACHE.clear()
        server.CACHE.update(cache)
        for k, v in stats.items():
            server._FETCH_STATS[k].clear()
            server._FETCH_STATS[k].update(v)
        server._FIRST_OK_S.clear()
        server._FIRST_OK_S.update(first)
        server._HTTP_LAST_MODIFIED.clear()
        server._HTTP_LAST_MODIFIED.update(lm)


def _reset_counters():
    """Put the module back into its just-imported, nothing-fetched-yet state."""
    server._MUF_RASTERIZE_OK = 0
    server._MUF_RASTERIZE_TIMEOUT = 0
    server._MUF_RASTERIZE_FAIL = 0
    server._MUF_LAST_RENDER_S = None
    server._MUF_LAST_TIMEOUT_BUDGET_S = None
    server._MUF_LAST_SVG_BYTES = None
    server._MUF_LAST_SLIM_BYTES = None
    server._MUF_LAST_PNG_BYTES = None
    server._MUF_SLIM_OK = 0
    server._MUF_SLIM_DECLINED = 0
    server._MUF_UNSLIMMED_RETRIES = 0
    server._MUF_LAST_SERVED = 'none'
    server._MUF_PNG_SOURCE = 'none'
    server._DISK_RESTORED = ()
    server._BOOT_DISK_WARM = False
    server._muf_render_ewma = None
    for name in server._FETCH_NAMES:
        server._FETCH_STATS[name].clear()
        server._FETCH_STATS[name].update(server._new_fetch_stat())
        server._FIRST_OK_S[name] = None
    for key in ('muf_image', 'muf_image_png', 'solar_image', 'enlil_image',
                'drap_image', 'real_drap_image'):
        server.CACHE[key] = None
    for key in ('muf_image_updated', 'muf_image_png_updated',
                'solar_image_updated', 'enlil_image_updated',
                'drap_image_updated', 'real_drap_image_updated'):
        server.CACHE[key] = 0


class _Handler:
    """Minimal stand-in with just the BaseHTTPRequestHandler write API."""

    def __init__(self, path='/api/diagnostics', command='GET'):
        self.path = path
        self.command = command
        self.status = None
        self.headers = {}
        self.headers_out = []
        self.body = b''
        outer = self

        class _W:
            def write(self, b):
                outer.body += b
        self.wfile = _W()

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers_out.append((k, str(v)))

    def end_headers(self):
        pass

    def send_error(self, code, msg=None):
        self.status = code


def _get(path='/api/diagnostics', command='GET'):
    h = _Handler(path, command)
    h.send_json = server.Handler.send_json.__get__(h, server.Handler)
    h.send_json_with_etag = server.Handler.send_json_with_etag.__get__(
        h, server.Handler)
    h.send_binary = server.Handler.send_binary.__get__(h, server.Handler)
    server.Handler.do_GET(h)
    return h


def _body(path='/api/diagnostics'):
    h = _get(path)
    assert h.status == 200
    return json.loads(h.body.decode('utf-8'))


class _Resp:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


JPEG = b'\xff\xd8\xff' + b'BODY' * 20 + b'\xff\xd9'
FAKE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
FAKE_PNG = b'\x89PNG\r\n\x1a\nBODYIEND\xaeB\x60\x82'


# ---------------------------------------------------------------------------
# The endpoint exists, answers JSON, and carries the documented shape
# ---------------------------------------------------------------------------


def test_endpoint_returns_200_json():
    h = _get()
    assert h.status == 200
    ctypes = [v for (k, v) in h.headers_out if k.lower() == 'content-type']
    assert ctypes == ['application/json']
    json.loads(h.body.decode('utf-8'))  # must parse


def test_top_level_keys():
    d = _body()
    assert d['schema'] == 1
    for key in ('muf', 'deps', 'cache', 'fetch', 'boot', 'process'):
        assert key in d, key


def test_muf_section_documents_the_timeout_question():
    """The whole reason the endpoint exists: is 45 s enough on ARMv6?"""
    d = _body()['muf']
    for key in ('last_render_s', 'render_ewma_s', 'timeout_s',
                'timeout_floor_s', 'timeout_max_s', 'last_timeout_budget_s',
                'rasterize_ok', 'rasterize_timeout', 'rasterize_fail',
                'svg_bytes', 'slim_bytes', 'png_bytes',
                'png_source', 'last_served'):
        assert key in d, key
    assert d['timeout_floor_s'] == server.PHASE2_TIMEOUT_S
    assert d['timeout_max_s'] == server.PHASE2_TIMEOUT_MAX_S
    assert d['timeout_s'] == server._muf_timeout()


def test_deps_section_keys():
    d = _body()['deps']
    assert set(d) == {'cairosvg', 'cairosvg_version', 'cpulimit'}
    assert isinstance(d['cairosvg'], bool)
    assert isinstance(d['cpulimit'], bool)


def test_cache_section_covers_all_five_image_products():
    d = _body()['cache']
    assert set(d) == set(server._PERSIST_KEYS)
    for key, entry in d.items():
        assert set(entry) == {'bytes', 'age_s', 'from_disk',
                              'http_304', 'http_full'}, key


def test_fetch_section_covers_every_fetcher():
    d = _body()['fetch']
    assert set(d) == set(server._FETCH_NAMES)
    for name, entry in d.items():
        for key in ('last_ms', 'last_status', 'consecutive_failures'):
            assert key in entry, (name, key)


def test_boot_section_keys():
    d = _body()['boot']
    assert set(d) == {'started_at', 'uptime_s', 'disk_warm', 'restored',
                      'first_ok_s'}
    assert set(d['first_ok_s']) == set(server._FETCH_NAMES)
    assert d['started_at'] > 0
    assert d['uptime_s'] >= 0


def test_process_section_reports_rss_threads_and_python():
    d = _body()['process']
    assert set(d) == {'rss_kb', 'threads', 'python'}
    assert d['python'].count('.') == 2
    # On Linux (the Pi, and CI) these are real numbers; elsewhere null.
    assert d['rss_kb'] is None or d['rss_kb'] > 0
    assert d['threads'] is None or d['threads'] >= 1


def test_snapshot_is_json_serializable_without_a_default_hook():
    json.dumps(server._diagnostics_snapshot())


def test_head_sends_headers_but_no_body():
    h = _get(command='HEAD')
    assert h.status == 200
    assert h.body == b''


# ---------------------------------------------------------------------------
# Boot state: initial counters must not raise or lie
# ---------------------------------------------------------------------------


def test_never_raises_at_boot_before_any_fetch():
    _reset_counters()
    d = _body()  # must not raise
    assert d['muf']['last_render_s'] is None
    assert d['muf']['render_ewma_s'] is None
    assert d['muf']['rasterize_ok'] == 0
    assert d['muf']['png_source'] == 'none'
    assert d['boot']['disk_warm'] is False
    assert d['boot']['restored'] == []
    assert all(v is None for v in d['boot']['first_ok_s'].values())
    for entry in d['cache'].values():
        assert entry['bytes'] == 0
        # -1, not 0: "never stamped" is not "zero seconds old".
        assert entry['age_s'] == -1
        assert entry['from_disk'] is False
    for entry in d['fetch'].values():
        assert entry['last_status'] is None
        assert entry['consecutive_failures'] == 0


def test_snapshot_never_raises_when_proc_is_unreadable(monkeypatch):
    """A hidden /proc must yield null, not a 500."""
    import builtins
    real_open = builtins.open

    def no_proc(path, *a, **kw):
        if str(path).startswith('/proc/'):
            raise OSError(13, 'Permission denied')
        return real_open(path, *a, **kw)
    monkeypatch.setattr(builtins, 'open', no_proc)
    d = server._diagnostics_snapshot()
    assert d['process']['rss_kb'] is None
    assert d['process']['threads'] is None


def test_proc_self_status_parses_rss_and_threads(tmp_path, monkeypatch):
    import builtins
    real_open = builtins.open
    fake = tmp_path / 'status'
    fake.write_text('Name:\tpython3\nVmRSS:\t   20304 kB\nThreads:\t3\n')

    def fake_open(path, *a, **kw):
        if str(path) == '/proc/self/status':
            return real_open(str(fake), *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr(builtins, 'open', fake_open)
    assert server._proc_self_status() == (20304, 3)


# ---------------------------------------------------------------------------
# No network I/O, no subprocess, no cairosvg import
# ---------------------------------------------------------------------------


def test_handling_the_request_does_no_network_io(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError('/api/diagnostics must never touch the network')
    monkeypatch.setattr(server, 'urlopen', boom)
    monkeypatch.setattr(server, '_conditional_get', boom)
    d = _body()
    assert d['schema'] == 1


def test_handling_the_request_starts_no_subprocess(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError('/api/diagnostics must not fork anything')
    monkeypatch.setattr(server.subprocess, 'Popen', boom)
    monkeypatch.setattr(server.subprocess, 'run', boom)
    _body()


def test_deps_probe_does_not_import_cairosvg():
    """cairosvg costs ~48 MB RSS to import — that is why the rasterize lives
    in a subprocess. A diagnostics request must not make the long-lived
    server pay it."""
    import sys
    if 'cairosvg' in sys.modules:
        pytest.skip('cairosvg already imported by something else')
    server._DEPS_SNAPSHOT = None
    try:
        server._deps_snapshot()
    finally:
        pass
    assert 'cairosvg' not in sys.modules


def test_deps_snapshot_is_memoised():
    server._DEPS_SNAPSHOT = None
    first = server._deps_snapshot()
    assert server._deps_snapshot() is first


def test_deps_reports_cpulimit_presence(monkeypatch):
    import shutil
    server._DEPS_SNAPSHOT = None
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/cpulimit')
    assert server._deps_snapshot()['cpulimit'] is True
    server._DEPS_SNAPSHOT = None
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    assert server._deps_snapshot()['cpulimit'] is False
    server._DEPS_SNAPSHOT = None


def test_deps_never_leaks_the_cpulimit_path(monkeypatch):
    """A PATH entry under /home would put the account name in the payload."""
    import shutil
    server._DEPS_SNAPSHOT = None
    monkeypatch.setattr(shutil, 'which',
                        lambda name: '/home/operator/bin/cpulimit')
    snap = server._deps_snapshot()
    server._DEPS_SNAPSHOT = None
    assert 'operator' not in json.dumps(snap)


# ---------------------------------------------------------------------------
# MUF rasterize counters — the measurement this whole feature is for
# ---------------------------------------------------------------------------


class _FakePopen:
    pid = 4242

    def __init__(self, argv, **kw):
        self.argv = argv
        self.returncode = 0
        self.calls = 0

    def communicate(self, input=None, timeout=None):
        self.calls += 1
        return FAKE_PNG, b''

    def poll(self):
        return self.returncode if self.calls else None

    def kill(self):
        pass


class _TimingOutPopen(_FakePopen):
    def communicate(self, input=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired(cmd=self.argv, timeout=timeout)
        return b'', b''


def test_successful_rasterize_records_seconds_and_sizes(monkeypatch):
    _reset_counters()
    monkeypatch.setattr(server.subprocess, 'Popen',
                        lambda argv, **kw: _FakePopen(argv, **kw))
    server._rasterize_muf(FAKE_SVG)
    d = _body()['muf']
    assert d['rasterize_ok'] == 1
    assert d['rasterize_timeout'] == 0
    assert d['rasterize_fail'] == 0
    assert d['last_render_s'] is not None and d['last_render_s'] >= 0
    assert d['png_bytes'] == len(FAKE_PNG)
    assert d['svg_bytes'] == len(FAKE_SVG)
    # This tiny document is not the KC2G map, so slimming declines it — which
    # is exactly the state that doubles the render on a real upstream change.
    assert d['slim_bytes'] is None
    assert d['slim_declined'] == 1
    assert d['render_ewma_s'] is not None


def test_a_timeout_is_counted_apart_from_a_failure(monkeypatch):
    """The log line says 'rasterize failed' for both. Only these two counters
    tell the maintainer whether PHASE2_TIMEOUT_S is too low or cairosvg is
    simply missing."""
    _reset_counters()
    monkeypatch.setattr(server.subprocess, 'Popen',
                        lambda argv, **kw: _TimingOutPopen(argv, **kw))
    monkeypatch.setattr(server, '_kill_process_group', lambda p: None)
    assert server._rasterize_muf(FAKE_SVG) is None
    d = _body()['muf']
    assert d['rasterize_timeout'] == 1
    assert d['rasterize_fail'] == 0
    assert d['rasterize_ok'] == 0
    # The budget that actually fired, so a raised PHASE2_TIMEOUT_S in the
    # field is distinguishable from the shipped 45 s floor.
    assert d['last_timeout_budget_s'] == server.PHASE2_TIMEOUT_S
    # A timeout must not pretend to be a successful render.
    assert d['last_render_s'] is None
    assert d['render_ewma_s'] is None


def test_a_missing_cpulimit_counts_as_a_failure_not_a_timeout(monkeypatch):
    _reset_counters()

    def missing(*a, **kw):
        raise FileNotFoundError(2, 'No such file or directory', 'cpulimit')
    monkeypatch.setattr(server.subprocess, 'Popen', missing)
    assert server._rasterize_muf(FAKE_SVG) is None
    d = _body()['muf']
    assert d['rasterize_fail'] == 1
    assert d['rasterize_timeout'] == 0


def test_an_empty_stdout_counts_as_a_failure(monkeypatch):
    _reset_counters()

    class _Empty(_FakePopen):
        def communicate(self, input=None, timeout=None):
            self.calls += 1
            return b'', b''
    monkeypatch.setattr(server.subprocess, 'Popen',
                        lambda argv, **kw: _Empty(argv, **kw))
    assert not server._rasterize_muf(FAKE_SVG)
    assert _body()['muf']['rasterize_fail'] >= 1
    assert _body()['muf']['rasterize_ok'] == 0


def test_slimmed_and_original_sizes_are_both_reported(monkeypatch):
    """Tier 2.3's slimming halves the render; if a future upstream re-render
    makes _slim_muf_svg decline, slim_bytes goes null and the cost doubles."""
    _reset_counters()
    monkeypatch.setattr(server, '_slim_muf_svg', lambda b: b'<svg/>')
    monkeypatch.setattr(server.subprocess, 'Popen',
                        lambda argv, **kw: _FakePopen(argv, **kw))
    server._rasterize_muf(FAKE_SVG)
    d = _body()['muf']
    assert d['svg_bytes'] == len(FAKE_SVG)
    assert d['slim_bytes'] == len(b'<svg/>')
    assert d['slim_ok'] == 1
    assert d['slim_declined'] == 0


def test_muf_timeout_reflects_a_learned_ewma(monkeypatch):
    """A budget above the 45 s floor is itself the finding: it means real
    renders here cost more than ~11 s."""
    _reset_counters()
    server._record_muf_render(20.0)
    d = _body()['muf']
    assert d['render_ewma_s'] == 20.0
    assert d['timeout_s'] == server._muf_timeout() == 80
    assert d['timeout_s'] > d['timeout_floor_s']


# ---------------------------------------------------------------------------
# What /api/muf-map last served, and where the PNG came from
# ---------------------------------------------------------------------------


def test_last_served_tracks_png_svg_and_none():
    _reset_counters()
    assert _body()['muf']['last_served'] == 'none'

    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = None
    _get('/api/muf-map')
    assert _body()['muf']['last_served'] == 'svg'

    server.CACHE['muf_image_png'] = FAKE_PNG
    _get('/api/muf-map')
    assert _body()['muf']['last_served'] == 'png'

    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = None
    _get('/api/muf-map?fmt=png')
    assert _body()['muf']['last_served'] == 'none'


def test_png_source_distinguishes_disk_from_live():
    _reset_counters()
    server.CACHE['muf_image_png'] = FAKE_PNG
    server._MUF_PNG_SOURCE = 'disk'
    assert _body()['muf']['png_source'] == 'disk'
    server._MUF_PNG_SOURCE = 'live'
    assert _body()['muf']['png_source'] == 'live'
    # No PNG held => 'none', whatever the last render claimed.
    server.CACHE['muf_image_png'] = None
    assert _body()['muf']['png_source'] == 'none'


# ---------------------------------------------------------------------------
# Fetch + cache counters
# ---------------------------------------------------------------------------


def test_a_successful_fetch_is_recorded(monkeypatch):
    _reset_counters()
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: _Resp(JPEG))
    server.fetch_sdo()
    d = _body()
    stat = d['fetch']['sdo']
    assert stat['last_status'] == 'ok'
    assert stat['ok'] == 1
    assert stat['consecutive_failures'] == 0
    assert stat['last_ms'] is not None and stat['last_ms'] >= 0
    assert stat['last_at'] > 0
    assert d['cache']['solar_image']['http_full'] == 1
    assert d['cache']['solar_image']['bytes'] == len(JPEG)
    assert d['cache']['solar_image']['age_s'] >= 0
    assert d['boot']['first_ok_s']['sdo'] is not None


def test_a_304_is_counted_separately_from_a_full_refetch(monkeypatch):
    """This is the field measurement of Tier 2.2: on a working install the
    304 count should dominate."""
    _reset_counters()
    server.CACHE['solar_image'] = JPEG

    def not_modified(req, timeout=None):
        raise server.HTTPError('http://x/', 304, 'Not Modified', {}, None)
    monkeypatch.setattr(server, 'urlopen', not_modified)
    server.fetch_sdo()
    d = _body()
    assert d['fetch']['sdo']['last_status'] == 'not_modified'
    assert d['cache']['solar_image']['http_304'] == 1
    assert d['cache']['solar_image']['http_full'] == 0
    # A 304 is a successful fetch for boot timing: the panel has current data.
    assert d['boot']['first_ok_s']['sdo'] is not None


def test_consecutive_failures_accumulate_then_reset(monkeypatch):
    _reset_counters()

    def down(req, timeout=None):
        raise OSError('network is unreachable')
    monkeypatch.setattr(server, 'urlopen', down)
    server.fetch_sdo()
    server.fetch_sdo()
    stat = _body()['fetch']['sdo']
    assert stat['consecutive_failures'] == 2
    assert stat['errors'] == 2
    assert stat['last_status'] == 'error'
    assert _body()['boot']['first_ok_s']['sdo'] is None

    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: _Resp(JPEG))
    server.fetch_sdo()
    stat = _body()['fetch']['sdo']
    assert stat['consecutive_failures'] == 0
    assert stat['errors'] == 2
    assert stat['last_error'] is None


def test_a_fallback_rescue_is_one_record_not_two(monkeypatch):
    """enlil/drap/real-drap iterate a two-URL list. Recording inside the loop
    would count a primary failure the fallback rescued as a fetch failure."""
    _reset_counters()
    calls = []

    def flaky(req, timeout=None):
        calls.append(req)
        if len(calls) == 1:
            raise OSError('primary down')
        return _Resp(JPEG)
    monkeypatch.setattr(server, 'urlopen', flaky)
    server.fetch_drap()
    stat = _body()['fetch']['drap']
    assert stat['last_status'] == 'ok'
    assert stat['ok'] == 1
    assert stat['errors'] == 0
    assert stat['consecutive_failures'] == 0


def test_both_urls_failing_counts_one_failure_not_two(monkeypatch):
    _reset_counters()
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            OSError('down')))
    server.fetch_drap()
    stat = _body()['fetch']['drap']
    assert stat['errors'] == 1
    assert stat['consecutive_failures'] == 1


def test_muf_fetch_duration_excludes_the_rasterize(monkeypatch):
    """fetch.muf.last_ms is the HTTP exchange. Folding a 45 s render into it
    would hide whichever of the two is actually slow."""
    _reset_counters()
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: _Resp(FAKE_SVG))

    def slow_render(svg):
        time.sleep(0.25)
        return FAKE_PNG
    monkeypatch.setattr(server, '_rasterize_muf', slow_render)
    server.fetch_muf()
    stat = _body()['fetch']['muf']
    assert stat['last_status'] == 'ok'
    assert stat['last_ms'] < 200, stat['last_ms']


def test_hamqsl_records_both_success_and_a_parse_failure(monkeypatch):
    _reset_counters()
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: _Resp(b'<xml/>'))
    server.fetch_hamqsl()
    stat = _body()['fetch']['hamqsl']
    assert stat['last_status'] == 'error'
    assert stat['last_error'] == 'no_solardata'

    good = (b'<?xml version="1.0"?><solar><solardata>'
            b'<solarflux>150</solarflux><kindex>2</kindex>'
            b'</solardata></solar>')
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: _Resp(good))
    server.fetch_hamqsl()
    assert _body()['fetch']['hamqsl']['last_status'] == 'ok'


def test_dx_failure_is_recorded_once_per_call(monkeypatch):
    _reset_counters()
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            OSError('down')))
    server.fetch_dx()
    assert _body()['fetch']['dx']['errors'] == 1


# ---------------------------------------------------------------------------
# Privacy: what must NEVER appear in the body
# ---------------------------------------------------------------------------


def test_an_error_is_recorded_as_a_type_name_not_a_message(monkeypatch):
    """urllib messages can carry a proxied URL with credentials in it, and
    this body is what the operator transmits."""
    _reset_counters()
    secret = 'http://ham:hunter2@proxy.internal:3128'

    def leaky(req, timeout=None):
        raise server.URLError('cannot reach %s' % secret)
    monkeypatch.setattr(server, 'urlopen', leaky)
    server.fetch_sdo()
    d = _body()
    assert d['fetch']['sdo']['last_error'] == 'URLError'
    blob = json.dumps(d)
    assert 'hunter2' not in blob
    assert 'proxy.internal' not in blob


def test_an_http_error_keeps_its_code(monkeypatch):
    _reset_counters()

    def five_hundred(req, timeout=None):
        raise server.HTTPError('http://x/', 503, 'Service Unavailable', {},
                               None)
    monkeypatch.setattr(server, 'urlopen', five_hundred)
    server.fetch_sdo()
    assert _body()['fetch']['sdo']['last_error'] == 'HTTPError:503'


def test_the_body_carries_no_urls_or_hostnames():
    """Upstream URLs are constants in this file; there is no reason to ship
    them, and echoing a URL is how a proxy or a LAN name would slip in."""
    _reset_counters()
    blob = json.dumps(_body())
    # '.com'/'.gov' would catch prop.kc2g.com, hamqsl.com, services.swpc.
    # noaa.gov; '://' any URL; the two path prefixes any filesystem echo.
    # ('hamqsl' alone is fine — it is the fetcher's NAME, not a host.)
    for needle in ('://', '.com', '.gov', '.hu', '/home/', '/etc/', '/var/'):
        assert needle not in blob, needle


# ---------------------------------------------------------------------------
# Disk cache warmth at boot
# ---------------------------------------------------------------------------


def test_disk_warm_and_from_disk_after_a_restore(tmp_path, monkeypatch):
    _reset_counters()
    monkeypatch.setattr(server, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(server, '_PERSIST_LAST', {})
    png = FAKE_PNG
    (tmp_path / 'muf.png').write_bytes(png)
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'version': 1,
        'entries': {'muf_image_png': {'file': 'muf.png', 'size': len(png),
                                      'epoch': time.time() - 60}},
    }))
    assert server._load_persisted() == ['muf_image_png']
    d = _body()
    assert d['boot']['disk_warm'] is True
    assert d['boot']['restored'] == ['muf_image_png']
    assert d['cache']['muf_image_png']['from_disk'] is True
    assert d['cache']['muf_image_png']['bytes'] == len(png)
    assert d['muf']['png_source'] == 'disk'


def test_disk_cold_when_nothing_was_restored(tmp_path, monkeypatch):
    _reset_counters()
    monkeypatch.setattr(server, 'CACHE_DIR', str(tmp_path))
    assert server._load_persisted() == []
    d = _body()
    assert d['boot']['disk_warm'] is False
    assert d['boot']['restored'] == []
    assert d['cache']['muf_image_png']['from_disk'] is False


# ---------------------------------------------------------------------------
# Shape guarantees the client depends on
# ---------------------------------------------------------------------------


def test_counter_dicts_never_grow_so_a_concurrent_copy_is_safe():
    """The fetcher thread writes into these while the handler copies them. A
    dict that could GAIN a key mid-copy raises RuntimeError ('changed size
    during iteration') inside the diagnostics handler."""
    before = {n: set(server._FETCH_STATS[n]) for n in server._FETCH_NAMES}
    keys_before = set(server._FIRST_OK_S)
    server._diag_fetch('sdo', 'ok', time.monotonic())
    server._diag_fetch('sdo', 'error', time.monotonic(), OSError('x'))
    for n in server._FETCH_NAMES:
        assert set(server._FETCH_STATS[n]) == before[n]
    assert set(server._FIRST_OK_S) == keys_before


def test_diag_fetch_ignores_an_unknown_name():
    """A typo must not create a key (see the no-resize rule) or raise."""
    server._diag_fetch('not_a_fetcher', 'ok', time.monotonic())
    assert 'not_a_fetcher' not in server._FETCH_STATS


def test_diag_fetch_never_raises():
    server._diag_fetch('sdo', 'ok', None)  # bad 'started'
    server._diag_fetch(None, None, time.monotonic())


def test_the_endpoint_is_registered_in_do_get():
    import inspect
    src = inspect.getsource(server.Handler.do_GET)
    assert "'/api/diagnostics'" in src


def test_health_endpoint_still_works():
    """Regression guard: the diagnostics branch sits next to /api/health."""
    h = _get('/api/health')
    assert h.status == 200
    assert json.loads(h.body.decode('utf-8'))['status'] == 'ok'
