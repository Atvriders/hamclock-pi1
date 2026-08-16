"""Phase 2 tests for the server-side MUF SVG -> PNG rasterizer.

These tests stub `subprocess.Popen` so the test suite never invokes cairosvg
or cpulimit. The contract under test is the wrapper around the subprocess —
argument list, timeout, process-group teardown, error handling, and the CACHE
wiring done by fetch_muf() + the /api/muf-map handler.

Tier 1.6 moved the rasterizer from subprocess.run() to Popen(
start_new_session=True) so a timeout can take down cpulimit AND the
python3/cairosvg grandchild it forked; the argv/stub assertions below track
that change.
"""
import signal
import subprocess
import sys
import time
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


class _FakePopen:
    """Minimal Popen stand-in that records how it was constructed/driven."""

    pid = 424242
    last = None

    def __init__(self, argv, stdin=None, stdout=None, stderr=None,
                 start_new_session=None, **kw):
        self.argv = argv
        self.kwargs = dict(stdin=stdin, stdout=stdout, stderr=stderr,
                           start_new_session=start_new_session, **kw)
        self.communicate_calls = []
        self.killed = 0
        self.returncode = 0
        self.stdout_bytes = FAKE_PNG
        self.timeout_on_first_communicate = False
        _FakePopen.last = self  # base class, so subclasses land here too

    def communicate(self, input=None, timeout=None):
        self.communicate_calls.append({'input': input, 'timeout': timeout})
        if self.timeout_on_first_communicate and len(self.communicate_calls) == 1:
            raise subprocess.TimeoutExpired(cmd=self.argv,
                                            timeout=timeout or 0)
        return self.stdout_bytes, b''

    def poll(self):
        # Real Popen.communicate() reaps and caches the returncode; before
        # that the child is still running.
        return self.returncode if self.communicate_calls else None

    def kill(self):
        self.killed += 1


def _install_fake_popen(monkeypatch, **attrs):
    """Patch subprocess.Popen with _FakePopen; `attrs` seed the instance."""
    def factory(argv, **kw):
        p = _FakePopen(argv, **kw)
        for k, v in attrs.items():
            setattr(p, k, v)
        return p
    monkeypatch.setattr(server.subprocess, 'Popen', factory)


def test_rasterize_muf_happy_path(monkeypatch):
    _install_fake_popen(monkeypatch)
    out = server._rasterize_muf(FAKE_SVG)
    assert out == FAKE_PNG
    p = _FakePopen.last
    # argv must start with the cpulimit guard
    assert p.argv[:5] == ['cpulimit', '-l', '50', '-q', '--']
    # then python3 -c "<cairosvg one-liner>"
    assert p.argv[5] == 'python3'
    assert p.argv[6] == '-c'
    one_liner = p.argv[7]
    assert 'cairosvg.svg2png' in one_liner
    assert 'output_width=360' in one_liner
    assert 'sys.stdin.buffer.read()' in one_liner
    assert 'sys.stdout.buffer' in one_liner
    # stdin contains the SVG bytes, fed through communicate()
    assert p.communicate_calls[0]['input'] == FAKE_SVG
    # timeout matches the published constant
    assert p.communicate_calls[0]['timeout'] == server.PHASE2_TIMEOUT_S
    # all three streams are pipes so .stdout is populated and the child's
    # stderr never lands on the server's own stderr
    assert p.kwargs['stdin'] is subprocess.PIPE
    assert p.kwargs['stdout'] is subprocess.PIPE
    assert p.kwargs['stderr'] is subprocess.PIPE
    # Tier 1.6: cpulimit + the cairosvg grandchild must share a process group
    # of their own so a timeout can take both down.
    assert p.kwargs['start_new_session'] is True


def test_rasterize_muf_returns_none_on_timeout(monkeypatch, capsys):
    _install_fake_popen(monkeypatch, timeout_on_first_communicate=True)
    killed = []
    monkeypatch.setattr(server, '_kill_process_group', lambda p: killed.append(p))
    assert server._rasterize_muf(FAKE_SVG) is None
    err = capsys.readouterr().err
    assert '[muf]' in err and 'rasterize failed' in err
    # Tier 1.6: the timeout path must tear down the whole process group, not
    # just cpulimit, or the SIGSTOPped cairosvg grandchild leaks ~48 MB.
    assert killed == [_FakePopen.last]
    # ...and it must still be reaped afterwards.
    assert len(_FakePopen.last.communicate_calls) == 2


def test_rasterize_muf_returns_none_on_called_process_error(monkeypatch, capsys):
    _install_fake_popen(monkeypatch, returncode=1, stdout_bytes=b'')
    assert server._rasterize_muf(FAKE_SVG) is None
    err = capsys.readouterr().err
    assert '[muf]' in err


def test_rasterize_muf_returns_none_when_cpulimit_missing(monkeypatch, capsys):
    def boom(*a, **kw):
        raise FileNotFoundError(2, 'No such file or directory', 'cpulimit')
    monkeypatch.setattr(server.subprocess, 'Popen', boom)
    assert server._rasterize_muf(FAKE_SVG) is None
    err = capsys.readouterr().err
    assert '[muf]' in err


def test_rasterize_muf_kills_the_group_on_an_unexpected_error(monkeypatch):
    """No exit path may leave cpulimit + cairosvg running."""
    class _Exploding(_FakePopen):
        def communicate(self, input=None, timeout=None):
            raise OSError('pipe went away')

    monkeypatch.setattr(server.subprocess, 'Popen',
                        lambda argv, **kw: _Exploding(argv, **kw))
    killed = []
    monkeypatch.setattr(server, '_kill_process_group', lambda p: killed.append(p))
    assert server._rasterize_muf(FAKE_SVG) is None
    assert killed == [_FakePopen.last]


def test_kill_process_group_sigconts_then_sigkills(monkeypatch):
    sent = []
    # getpgid(0) -> our own group (1); getpgid(child) -> the child's group.
    monkeypatch.setattr(server.os, 'getpgid', lambda pid: 1 if pid == 0 else 4242)
    monkeypatch.setattr(server.os, 'killpg', lambda pgid, sig: sent.append((pgid, sig)))

    class _P:
        pid = 4242

        def kill(self):
            raise AssertionError('must signal the group, not just the child')

    server._kill_process_group(_P())
    # SIGCONT first: a SIGSTOPped process (cpulimit's duty cycle) acts on
    # nothing else, and SIGKILL then works regardless of run state.
    assert sent == [(4242, signal.SIGCONT), (4242, signal.SIGKILL)]


def test_kill_process_group_refuses_to_signal_own_group(monkeypatch):
    """Safety rail: never killpg our own group — that SIGKILLs the server."""
    sent = []
    killed = []
    monkeypatch.setattr(server.os, 'getpgid', lambda pid: 999)
    monkeypatch.setattr(server.os, 'killpg', lambda pgid, sig: sent.append((pgid, sig)))

    class _P:
        pid = 4242

        def kill(self):
            killed.append(True)

    server._kill_process_group(_P())
    assert sent == []
    assert killed == [True]


def test_kill_process_group_survives_already_dead_child(monkeypatch):
    def gone(pid):
        raise ProcessLookupError(3, 'No such process')
    monkeypatch.setattr(server.os, 'getpgid', gone)

    class _P:
        pid = 4242

        def kill(self):
            raise ProcessLookupError(3, 'No such process')

    server._kill_process_group(_P())  # must not raise


def test_cache_has_muf_image_png_slot():
    assert 'muf_image_png' in server.CACHE, (
        "Phase 2: CACHE must declare a 'muf_image_png' slot (initial None)."
    )
    # On import, before any fetch, the PNG slot is None.
    assert server.CACHE['muf_image_png'] is None or isinstance(
        server.CACHE['muf_image_png'], (bytes, bytearray)
    )


class FakeResp:
    def __init__(self, body):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_fetch_muf_populates_png_when_rasterize_succeeds(monkeypatch):
    # Stub urlopen so fetch_muf does not hit the network.
    monkeypatch.setattr(server, 'urlopen', lambda req, timeout=20: FakeResp(FAKE_SVG))
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: FAKE_PNG)
    # Reset cache slots
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = None
    server.CACHE['muf_image_updated'] = 0
    server.CACHE['muf_image_png_updated'] = 0

    server.fetch_muf()

    assert server.CACHE['muf_image'] == FAKE_SVG
    assert server.CACHE['muf_image_png'] == FAKE_PNG
    assert server.CACHE['muf_image_updated'] > 0
    # The PNG carries its own stamp so keep-last-good can age it independently
    # of the SVG fetch (which succeeds even when the rasterize does not).
    assert server.CACHE['muf_image_png_updated'] > 0


def test_fetch_muf_keeps_last_good_png_when_rasterize_fails(monkeypatch):
    """Tier 1.5 contract change (was: 'leaves_png_none_when_rasterize_fails').

    Wiping the PNG on a failed rasterize used to be harmless because
    /api/muf-map fell back to the SVG. It no longer does for the native
    client (SVG decodes into a 5.5 MB surface on the render thread), so a
    single 45 s timeout would blank the MUF panel for 15+ minutes — or
    forever, since the timeout is deterministic. Keep the last good PNG.
    """
    monkeypatch.setattr(server, 'urlopen', lambda req, timeout=20: FakeResp(FAKE_SVG))
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: None)
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = b'LASTGOOD'
    server.CACHE['muf_image_png_updated'] = time.time() - 60
    server.CACHE['muf_image_updated'] = 0

    server.fetch_muf()

    # SVG must still cache (browser path still works).
    assert server.CACHE['muf_image'] == FAKE_SVG
    # ...and the previously rendered PNG survives.
    assert server.CACHE['muf_image_png'] == b'LASTGOOD'


def test_fetch_muf_drops_png_older_than_stale_max(monkeypatch):
    """Keep-last-good is bounded by MUF_STALE_MAX_S.

    A day-old MUF map has a day-old solar terminator; past that we would
    rather show nothing than something confidently wrong.
    """
    monkeypatch.setattr(server, 'urlopen', lambda req, timeout=20: FakeResp(FAKE_SVG))
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: None)
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = b'ANCIENT'
    server.CACHE['muf_image_png_updated'] = time.time() - server.MUF_STALE_MAX_S - 1
    server.CACHE['muf_image_updated'] = 0

    server.fetch_muf()

    assert server.CACHE['muf_image'] == FAKE_SVG
    assert server.CACHE['muf_image_png'] is None
    assert server.CACHE['muf_image_png_updated'] == 0


def test_muf_stale_max_is_bounded():
    assert isinstance(server.MUF_STALE_MAX_S, (int, float))
    assert 0 < server.MUF_STALE_MAX_S <= 24 * 3600


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


def _invoke_path(path):
    rec = _Recorder()
    rec.path = path
    return _invoke_muf_map(rec)


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
    """The BROWSER keeps the SVG: index.html:328 renders it in an <img>."""
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = None
    rec = _invoke_muf_map(_Recorder())
    assert rec.status == 200
    ctypes = [v for (k, v) in rec.headers if k.lower() == 'content-type']
    assert ctypes == ['image/svg+xml']
    assert rec.body == FAKE_SVG


def test_muf_map_cache_busted_browser_url_still_gets_svg():
    """index.html:842 requests '/api/muf-map?t=<epoch>' — still the SVG path."""
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = None
    rec = _invoke_path('/api/muf-map?t=1699999999')
    assert rec.status == 200
    ctypes = [v for (k, v) in rec.headers if k.lower() == 'content-type']
    assert ctypes == ['image/svg+xml']


def test_muf_map_503_when_neither_cached(monkeypatch):
    server.CACHE['muf_image'] = None
    server.CACHE['muf_image_png'] = None
    rec = _invoke_muf_map(_Recorder())
    assert rec.status == 503


def test_muf_map_fmt_png_never_serves_svg():
    """Tier 1.5: ?fmt=png is PNG-or-503, never the 365 KB SVG.

    Handing SVG to the native client makes SDL/nanosvg decode a 1526x905 /
    5.5 MB greyscale surface on the render thread — a multi-second freeze on
    ARMv6 for a 236x102 panel.

    Negotiation must key off the query string, not headers: _Shim (and the
    real <img src> path) has no usable Accept for us to switch on.
    """
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = None
    rec = _invoke_path('/api/muf-map?fmt=png')
    assert rec.status == 503
    assert rec.body == b''


def test_muf_map_fmt_png_serves_png_when_available():
    server.CACHE['muf_image'] = FAKE_SVG
    server.CACHE['muf_image_png'] = FAKE_PNG
    rec = _invoke_path('/api/muf-map?fmt=png')
    assert rec.status == 200
    ctypes = [v for (k, v) in rec.headers if k.lower() == 'content-type']
    assert ctypes == ['image/png']
    assert rec.body == FAKE_PNG


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


# ---- the bootstrap deadlock (found on real hardware, 2026-08-16) ----------

def test_timeout_escalates_while_no_render_has_ever_succeeded(monkeypatch):
    """The EWMA is only written on success, so a box whose true render time
    sits above the floor used to deadlock: time out at 45 s, record nothing,
    keep the 45 s budget, time out again, forever. A Pi 1B is that box —
    slimmed cairosvg is ~0.57 s on x86, which scaled for ARMv6 and doubled by
    cpulimit's 50% duty straddles 45 s.
    """
    monkeypatch.setattr(server, '_muf_render_ewma', None, raising=False)
    seen = []
    for n in (0, 1, 2, 3, 4):
        monkeypatch.setattr(server, '_MUF_RASTERIZE_TIMEOUT', n, raising=False)
        seen.append(server._muf_timeout())
    assert seen[0] == server.PHASE2_TIMEOUT_S, "first attempt must use the floor"
    assert seen[1] > seen[0], "a timeout must widen the budget, not repeat it"
    assert seen == sorted(seen), f"budget must be monotonic: {seen}"
    assert max(seen) <= server.PHASE2_TIMEOUT_MAX_S, "must respect the ceiling"
    assert seen[-1] == server.PHASE2_TIMEOUT_MAX_S, "must reach the ceiling"


def test_one_success_retires_the_escalation(monkeypatch):
    """A fast box must not keep paying for an early stumble."""
    monkeypatch.setattr(server, '_MUF_RASTERIZE_TIMEOUT', 3, raising=False)
    monkeypatch.setattr(server, '_muf_render_ewma', 3.0, raising=False)
    assert server._muf_timeout() == server.PHASE2_TIMEOUT_S
