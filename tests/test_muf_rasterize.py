"""Phase 2 tests for the server-side MUF SVG -> PNG rasterizer.

These tests stub `subprocess.run` so the test suite never invokes cairosvg or
cpulimit. The contract under test is the wrapper around the subprocess —
argument list, timeout, error handling, and the CACHE wiring done by
fetch_muf() + the /api/muf-map handler.
"""
import subprocess
import sys
from unittest import mock

import pytest

import server


def test_rasterize_muf_symbol_exists():
    assert hasattr(server, "_rasterize_muf"), (
        "Phase 2 contract: server must export `_rasterize_muf(svg_bytes)`."
    )


def test_phase2_timeout_constant_exists():
    assert hasattr(server, "PHASE2_TIMEOUT_S"), (
        "Phase 2 contract: server must export PHASE2_TIMEOUT_S."
    )
    assert isinstance(server.PHASE2_TIMEOUT_S, int)
    assert server.PHASE2_TIMEOUT_S >= 45


FAKE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
FAKE_PNG = b'\x89PNG\r\n\x1a\nFAKEPNGBODY'


def _stub_completed(stdout, returncode=0):
    r = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b'')
    return r


def test_rasterize_muf_happy_path(monkeypatch):
    captured = {}

    def fake_run(argv, input=None, capture_output=None, timeout=None, check=None):
        captured['argv'] = argv
        captured['input'] = input
        captured['timeout'] = timeout
        captured['check'] = check
        captured['capture_output'] = capture_output
        return _stub_completed(FAKE_PNG)

    monkeypatch.setattr(server.subprocess, 'run', fake_run)
    out = server._rasterize_muf(FAKE_SVG)
    assert out == FAKE_PNG
    # argv must start with the cpulimit guard
    assert captured['argv'][:5] == ['cpulimit', '-l', '50', '-q', '--']
    # then python3 -c "<cairosvg one-liner>"
    assert captured['argv'][5] == 'python3'
    assert captured['argv'][6] == '-c'
    one_liner = captured['argv'][7]
    assert 'cairosvg.svg2png' in one_liner
    assert 'output_width=360' in one_liner
    assert 'sys.stdin.buffer.read()' in one_liner
    assert 'sys.stdout.buffer' in one_liner
    # stdin contains the SVG bytes
    assert captured['input'] == FAKE_SVG
    # timeout matches the published constant
    assert captured['timeout'] == server.PHASE2_TIMEOUT_S
    # check=True so non-zero exits raise CalledProcessError -> None
    assert captured['check'] is True
    # capture_output=True so .stdout is populated
    assert captured['capture_output'] is True


def test_rasterize_muf_returns_none_on_timeout(monkeypatch, capsys):
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd='python3', timeout=server.PHASE2_TIMEOUT_S)
    monkeypatch.setattr(server.subprocess, 'run', fake_run)
    assert server._rasterize_muf(FAKE_SVG) is None
    err = capsys.readouterr().err
    assert '[muf]' in err and 'rasterize failed' in err


def test_rasterize_muf_returns_none_on_called_process_error(monkeypatch, capsys):
    def fake_run(*a, **kw):
        raise subprocess.CalledProcessError(returncode=1, cmd='python3', stderr=b'boom')
    monkeypatch.setattr(server.subprocess, 'run', fake_run)
    assert server._rasterize_muf(FAKE_SVG) is None
    err = capsys.readouterr().err
    assert '[muf]' in err


def test_rasterize_muf_returns_none_when_cpulimit_missing(monkeypatch, capsys):
    def fake_run(*a, **kw):
        raise FileNotFoundError(2, 'No such file or directory', 'cpulimit')
    monkeypatch.setattr(server.subprocess, 'run', fake_run)
    assert server._rasterize_muf(FAKE_SVG) is None
    err = capsys.readouterr().err
    assert '[muf]' in err


def test_cache_has_muf_image_png_slot():
    assert 'muf_image_png' in server.CACHE, (
        "Phase 2: CACHE must declare a 'muf_image_png' slot (initial None)."
    )
    # On import, before any fetch, the PNG slot is None.
    assert server.CACHE['muf_image_png'] is None or isinstance(
        server.CACHE['muf_image_png'], (bytes, bytearray)
    )


def test_fetch_muf_populates_png_when_rasterize_succeeds(monkeypatch):
    # Stub urlopen so fetch_muf does not hit the network.
    class FakeResp:
        def __init__(self, body):
            self._body = body
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(server, 'urlopen', lambda req, timeout=20: FakeResp(FAKE_SVG))
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: FAKE_PNG)
    # Reset cache slots
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = None
    server.CACHE['muf_image_updated'] = 0

    server.fetch_muf()

    assert server.CACHE['muf_image'] == FAKE_SVG
    assert server.CACHE['muf_image_png'] == FAKE_PNG
    assert server.CACHE['muf_image_updated'] > 0


def test_fetch_muf_leaves_png_none_when_rasterize_fails(monkeypatch):
    class FakeResp:
        def __init__(self, body):
            self._body = body
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(server, 'urlopen', lambda req, timeout=20: FakeResp(FAKE_SVG))
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: None)
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = b'STALE'
    server.CACHE['muf_image_updated'] = 0

    server.fetch_muf()

    # SVG must still cache (browser path still works).
    assert server.CACHE['muf_image'] == FAKE_SVG
    # PNG slot cleared so /api/muf-map falls through to SVG.
    assert server.CACHE['muf_image_png'] is None


class _Recorder:
    """Mimic the BaseHTTPRequestHandler write API enough to capture output."""
    def __init__(self):
        self.status = None
        self.headers = []
        self.body = b''
        self.command = 'GET'
        self.path = '/api/muf-map'
    def send_response(self, code):
        self.status = code
    def send_header(self, k, v):
        self.headers.append((k, str(v)))
    def end_headers(self):
        pass
    def write(self, b):
        self.body += b


def _invoke_muf_map(rec):
    # Drive only the /api/muf-map branch by re-implementing the dispatch
    # contract here — we can't easily instantiate the full Handler in tests.
    # Instead, exercise the production code by importing Handler and
    # calling the muf branch via a thin shim attached in this test.
    from server import Handler
    # Bind the recorder's writes to wfile interface.
    class _W:
        def __init__(self, rec): self.rec = rec
        def write(self, b): self.rec.write(b)
    class _Shim(Handler):
        def __init__(s):  # bypass super().__init__
            s.command = rec.command
            s.path = rec.path
            s.wfile = _W(rec)
        def send_response(s, code): rec.send_response(code)
        def send_header(s, k, v): rec.send_header(k, v)
        def end_headers(s): rec.end_headers()
        def send_error(s, code, msg=None):
            rec.status = code
    shim = _Shim()
    shim.do_GET()
    return rec


def test_muf_map_serves_png_when_available(monkeypatch):
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = FAKE_PNG
    rec = _invoke_muf_map(_Recorder())
    assert rec.status == 200
    ctypes = [v for (k, v) in rec.headers if k.lower() == 'content-type']
    assert ctypes == ['image/png']
    assert rec.body == FAKE_PNG


def test_muf_map_falls_back_to_svg_when_png_missing(monkeypatch):
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = None
    rec = _invoke_muf_map(_Recorder())
    assert rec.status == 200
    ctypes = [v for (k, v) in rec.headers if k.lower() == 'content-type']
    assert ctypes == ['image/svg+xml']
    assert rec.body == FAKE_SVG


def test_muf_map_503_when_neither_cached(monkeypatch):
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = None
    rec = _invoke_muf_map(_Recorder())
    assert rec.status == 503


# ---------------------------------------------------------------------------
# Tier 1b perf: pre-encoded JSON byte cache for /api/solar, /api/bands,
# /api/dxspots. The CACHE must declare the three byte slots, and send_json
# must accept pre-encoded bytes verbatim so we skip the per-request dumps.
# ---------------------------------------------------------------------------


def test_cache_has_prebaked_bytes_slots():
    assert 'solar_bytes' in server.CACHE
    assert 'bands_bytes' in server.CACHE
    assert 'dxspots_bytes' in server.CACHE


def test_send_json_accepts_prebaked_bytes(monkeypatch):
    body_seen = {'b': None}

    class FakeW:
        def write(self, b):
            body_seen['b'] = b

    h = type('H', (), {'wfile': FakeW(), 'command': 'GET'})()
    h.send_response = lambda code: None
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    server.Handler.send_json(h, b'{"x":1}')
    assert body_seen['b'] == b'{"x":1}'
