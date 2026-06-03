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
    assert 'output_width=720' in one_liner
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
