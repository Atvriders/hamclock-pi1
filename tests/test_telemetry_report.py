"""Opt-in diagnostics report: collection, confirm-before-send, and send.

The maintainer has no ARMv6 hardware, so this feature exists to answer
questions (SDL driver, colour depth, real frame times) that cannot be
measured any other way. That makes it a feature which collects facts about
someone else's machine and puts them on the network, so the tests here are
weighted towards the properties that keep that honest:

  * nothing leaves the Pi without an explicit confirm, every time;
  * the payload cannot carry a secret-shaped key, including inside the one
    block this client does not author (the local server's /api/diagnostics);
  * an unreadable /proc yields nulls, never an exception in the render loop;
  * the screenshot is capped and DROPPED rather than truncated;
  * the report id is random and persisted, never derived from the callsign.
"""
import base64
import builtins
import inspect
import json
import re
import types

import pygame
import pytest

import hamclock_pygame as hp


# --- fixtures --------------------------------------------------------------

@pytest.fixture
def screen():
    """A real (dummy-driver) display surface, rebuilt per test.

    Function-scoped on purpose: the render-loop integration tests below run
    _run_render_loop to completion, and it calls pygame.quit() on the way out.
    """
    pygame.init()
    pygame.font.init()
    surf = pygame.display.set_mode((720, 450))
    yield surf
    pygame.init()
    pygame.font.init()


@pytest.fixture
def fonts(screen):
    return hp._make_fonts()


@pytest.fixture
def theme():
    return dict(hp.THEMES['kstate'])


class _StubData:
    """Enough HamClockData surface for the collector and the render loop."""
    def __init__(self, server_url='http://localhost:8080'):
        self.server_url = server_url
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


@pytest.fixture
def no_server(monkeypatch):
    """Never touch a real localhost:8080 from the test suite."""
    monkeypatch.setattr(hp, '_fetch_server_diagnostics',
                        lambda *a, **kw: {'ok': True, 'stub': 1})


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    p = tmp_path / 'settings.json'
    monkeypatch.setattr(hp, 'SETTINGS_PATH', str(p))
    return str(p)


def _walk_keys(obj, depth=0):
    """Yield every mapping key anywhere in a nested structure."""
    if depth > 20:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            for kk in _walk_keys(v, depth + 1):
                yield kk
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            for kk in _walk_keys(v, depth + 1):
                yield kk


# --- payload shape ---------------------------------------------------------

def test_payload_top_level_shape_is_the_agreed_schema(screen, fonts,
                                                      no_server,
                                                      settings_path):
    """Three agents (Pi client, server endpoint, dashboard) share this shape.
    A silent rename here is a silent data loss at the other two."""
    p = hp._collect_telemetry(screen, _StubData(), fonts, settings={})
    assert set(p) == {
        'schema', 'device_id', 'sent_at', 'app', 'host', 'display',
        'versions', 'perf', 'server', 'screenshot_png_b64',
    }, 'top-level payload keys drifted from the agreed schema'
    assert p['schema'] == 1
    assert set(p['app']) == {'version', 'mode', 'install'}
    assert p['app']['mode'] == 'pygame'
    assert set(p['host']) == {'model', 'cpu', 'cores', 'mem_total_kb',
                              'kernel', 'os', 'python', 'uptime_s'}
    assert set(p['display']) == {'sdl_driver', 'bitsize', 'size',
                                 'fullscreen'}
    assert set(p['versions']) == {'pygame', 'sdl', 'cairosvg', 'cpulimit'}
    assert set(p['perf']) == {'frame_ms', 'panel_ms', 'boot_to_first_paint_s'}
    assert set(p['perf']['frame_ms']) == {'p50', 'p90', 'p99', 'n'}
    assert re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', p['sent_at'])


def test_payload_is_json_serialisable(screen, fonts, no_server,
                                      settings_path):
    """It is POSTed as JSON; a stray non-serialisable value would only be
    discovered on the operator's Pi, at send time."""
    p = hp._collect_telemetry(screen, _StubData(), fonts, settings={})
    json.dumps(p)


def test_payload_has_no_secret_shaped_keys(screen, fonts, settings_path,
                                           monkeypatch):
    """No credential, token, WiFi name/PSK or key material may ever be in the
    payload — including anything the server endpoint hands us."""
    monkeypatch.setattr(hp, '_fetch_server_diagnostics',
                        lambda *a, **kw: hp._scrub_secrets({
                            'cache': {'muf_age': 12},
                            'wifi_ssid': 'HomeNet',
                            'api_token': 'abc123',
                        }))
    p = hp._collect_telemetry(screen, _StubData(), fonts, settings={})
    bad = [k for k in _walk_keys(p) if hp._SECRET_KEY_RE.search(str(k))]
    assert bad == [], 'secret-shaped keys reached the payload: %r' % bad


def test_display_block_answers_the_driver_and_depth_question(screen, fonts,
                                                             no_server,
                                                             settings_path):
    """The whole point of the exercise: 10-monitor.conf ships DefaultDepth 16
    and smoothscale raises on 16bpp, and nobody has ever observed what the
    kiosk actually gets."""
    d = hp._collect_telemetry(screen, _StubData(), fonts,
                              settings={})['display']
    assert d['sdl_driver'] == pygame.display.get_driver()
    assert d['bitsize'] == screen.get_bitsize()
    assert d['size'] == [720, 450]
    assert isinstance(d['fullscreen'], bool)


def test_display_block_survives_a_dead_surface(monkeypatch):
    """A surface that raises on every query must still yield a shaped dict."""
    class _Dead:
        def get_bitsize(self):
            raise pygame.error('boom')

        def get_size(self):
            raise pygame.error('boom')

        def get_flags(self):
            raise pygame.error('boom')

    d = hp._collect_display(_Dead())
    assert d['bitsize'] is None and d['size'] is None
    assert d['fullscreen'] is None


# --- /proc guards ----------------------------------------------------------

def test_read_text_returns_none_when_open_raises(monkeypatch):
    def boom(*a, **kw):
        raise OSError('nope')
    monkeypatch.setattr(builtins, 'open', boom)
    assert hp._read_text('/proc/cpuinfo') is None


def test_host_block_degrades_to_null_when_proc_is_unreadable(monkeypatch):
    """A missing or exploding /proc yields null for that field, never an
    exception — an exception here lands in the render loop's error counter."""
    real_open = builtins.open

    def selective(path, *a, **kw):
        if isinstance(path, str) and (path.startswith('/proc')
                                      or path.startswith('/etc')):
            raise OSError('simulated unreadable /proc')
        return real_open(path, *a, **kw)
    monkeypatch.setattr(builtins, 'open', selective)
    monkeypatch.setattr(hp, '_uname', lambda: None)

    host = hp._collect_host()          # must not raise
    assert host['model'] is None
    assert host['cpu'] is None
    assert host['mem_total_kb'] is None
    assert host['kernel'] is None
    assert host['os'] is None
    assert host['uptime_s'] is None
    # Facts we hold in-process are still reported.
    assert host['python'].count('.') == 2


def test_whole_payload_survives_an_unreadable_proc(screen, fonts, no_server,
                                                   settings_path, monkeypatch):
    real_open = builtins.open

    def selective(path, *a, **kw):
        if isinstance(path, str) and (path.startswith('/proc')
                                      or path.startswith('/etc')):
            raise OSError('simulated unreadable /proc')
        return real_open(path, *a, **kw)
    monkeypatch.setattr(builtins, 'open', selective)

    p = hp._collect_telemetry(screen, _StubData(), fonts, settings={})
    json.dumps(p)
    assert p['host']['mem_total_kb'] is None


def test_cpuinfo_parses_the_pi_field_names(monkeypatch):
    """Raspberry Pi OS reports the board as Model and the SoC as Hardware +
    Revision; x86 reports neither and uses 'model name'."""
    monkeypatch.setattr(hp, '_read_text', lambda path, limit=65536: (
        'processor\t: 0\n'
        'model name\t: ARMv6-compatible processor rev 7 (v6l)\n'
        'Hardware\t: BCM2835\n'
        'Revision\t: 000e\n'
        'Model\t\t: Raspberry Pi Model B Rev 2\n'
    ) if path == '/proc/cpuinfo' else None)
    monkeypatch.setattr(hp, '_uname', lambda: None)
    host = hp._collect_host()
    assert host['model'] == 'Raspberry Pi Model B Rev 2'
    assert 'ARMv6' in host['cpu']
    assert 'BCM2835' in host['cpu'] and '000e' in host['cpu']


def test_meminfo_total_kb_parsed(monkeypatch):
    monkeypatch.setattr(hp, '_read_text', lambda path, limit=65536: (
        'MemTotal:         445124 kB\nMemFree:  1 kB\n'
    ) if path == '/proc/meminfo' else None)
    assert hp._meminfo_total_kb() == 445124


def test_meminfo_garbage_is_null(monkeypatch):
    monkeypatch.setattr(hp, '_read_text',
                        lambda path, limit=65536: 'MemTotal: not-a-number\n')
    assert hp._meminfo_total_kb() is None


# --- screenshot ------------------------------------------------------------

def test_screenshot_is_base64_of_a_png(screen):
    b64 = hp._screenshot_b64(screen)
    assert isinstance(b64, str) and b64
    raw = base64.b64decode(b64)
    assert raw[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'


def test_screenshot_over_the_cap_is_dropped_not_truncated(screen):
    """Half a PNG is not a smaller screenshot, it is a broken one."""
    b64 = hp._screenshot_b64(screen, cap=16)
    assert b64 is None


def test_screenshot_cap_default_is_350k():
    assert hp.SCREENSHOT_MAX_B64 == 350 * 1024


def test_oversized_screenshot_leaves_the_rest_of_the_payload_intact(
        screen, fonts, no_server, settings_path, monkeypatch):
    monkeypatch.setattr(hp, 'SCREENSHOT_MAX_B64', 16)
    p = hp._collect_telemetry(screen, _StubData(), fonts, settings={})
    assert p['screenshot_png_b64'] is None
    assert p['display']['size'] == [720, 450]


def test_screenshot_of_nothing_is_none():
    assert hp._screenshot_b64(None) is None


# --- device id -------------------------------------------------------------

def test_device_id_is_a_uuid4_persisted_to_settings(settings_path):
    s = {'callsign': 'W1ABC', 'timezone': 'UTC', 'theme': 'kstate', 'ntp': ''}
    did = hp._get_or_create_device_id(s, settings_path)
    assert hp._DEVICE_ID_RE.match(did), 'not a uuid: %r' % did
    assert did[14] == '4', 'not a version-4 (random) uuid: %r' % did
    on_disk = json.loads(open(settings_path).read())
    assert on_disk['device_id'] == did
    # ...and every other setting survived the write.
    assert on_disk['callsign'] == 'W1ABC'


def test_device_id_is_stable_across_a_reload(settings_path):
    s = dict(hp.DEFAULT_SETTINGS)
    first = hp._get_or_create_device_id(s, settings_path)
    reloaded = hp.load_settings(settings_path)
    assert reloaded['device_id'] == first
    assert hp._get_or_create_device_id(reloaded, settings_path) == first


def test_device_id_is_not_derived_from_the_callsign(settings_path, tmp_path):
    """Its only job is to correlate two reports from the same Pi. Nothing
    about the operator may be recoverable from it."""
    a = {'callsign': 'W1ABC', 'timezone': 'UTC', 'theme': 'kstate', 'ntp': ''}
    b = {'callsign': 'W1ABC', 'timezone': 'UTC', 'theme': 'kstate', 'ntp': ''}
    id_a = hp._get_or_create_device_id(a, str(tmp_path / 'a.json'))
    id_b = hp._get_or_create_device_id(b, str(tmp_path / 'b.json'))
    assert id_a != id_b, 'two Pis with the same callsign share a report id'
    for call in ('W1ABC', 'w1abc'):
        assert call not in id_a
    hexed = id_a.replace('-', '')
    assert 'W1ABC'.encode('utf-8').hex() not in hexed


def test_device_id_survives_a_failed_write(monkeypatch, tmp_path):
    """A read-only /etc must not cost the operator their report."""
    def boom(*a, **kw):
        raise PermissionError('read-only')
    monkeypatch.setattr(hp, 'write_settings', boom)
    did = hp._get_or_create_device_id({}, str(tmp_path / 'x.json'))
    assert hp._DEVICE_ID_RE.match(did)


def test_load_settings_default_shape_is_unchanged(tmp_path):
    """device_id is deliberately NOT in DEFAULT_SETTINGS: a Pi that has never
    opened the dialog has no id, and the installed base round-trips the four
    original keys."""
    assert 'device_id' not in hp.DEFAULT_SETTINGS
    d = hp.load_settings(str(tmp_path / 'missing.json'))
    assert d == hp.DEFAULT_SETTINGS
    assert 'device_id' not in d


def test_setup_cli_rerun_preserves_an_existing_device_id(tmp_path):
    """Fixing a typo in the callsign must not orphan this Pi's history."""
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({
        'callsign': 'W1ABC', 'timezone': 'UTC', 'theme': 'kstate', 'ntp': '',
        'device_id': '11111111-2222-4333-8444-555555555555',
    }))
    rc = hp._cli_main(['--setup-cli', '--callsign', 'W1ABD',
                       '--timezone', 'UTC', '--theme', 'amber',
                       '--settings-path', str(p)])
    assert rc == 0
    after = json.loads(p.read_text())
    assert after['callsign'] == 'W1ABD'
    assert after['device_id'] == '11111111-2222-4333-8444-555555555555'


def test_setup_cli_does_not_invent_a_device_id(tmp_path):
    p = tmp_path / 'settings.json'
    rc = hp._cli_main(['--setup-cli', '--callsign', 'W1ABC',
                       '--timezone', 'UTC', '--theme', 'kstate',
                       '--settings-path', str(p)])
    assert rc == 0
    assert 'device_id' not in json.loads(p.read_text())


# --- server diagnostics block ---------------------------------------------

class _FakeResp:
    def __init__(self, body, code=200):
        self._body = body
        self._code = code

    def read(self, n=-1):
        return self._body[:n] if n and n > 0 else self._body

    def getcode(self):
        return self._code

    def close(self):
        pass


def test_server_block_is_embedded(monkeypatch):
    body = json.dumps({'cache': {'muf_age': 42}, 'feeds': ['sdo']}).encode()
    monkeypatch.setattr(hp, '_urlopen', lambda req, timeout=None:
                        _FakeResp(body))
    got = hp._fetch_server_diagnostics('http://localhost:8080')
    assert got == {'cache': {'muf_age': 42}, 'feeds': ['sdo']}


def test_server_block_is_scrubbed_of_secret_shaped_keys(monkeypatch):
    """This is the one block the client does not author. Defence in depth."""
    body = json.dumps({
        'cache': {'muf_age': 42, 'api_token': 'sekrit'},
        'wifi_ssid': 'HomeNet',
        'nested': [{'password': 'hunter2', 'ok': 1}],
    }).encode()
    monkeypatch.setattr(hp, '_urlopen', lambda req, timeout=None:
                        _FakeResp(body))
    got = hp._fetch_server_diagnostics()
    assert got == {'cache': {'muf_age': 42}, 'nested': [{'ok': 1}]}


def test_server_block_is_null_when_the_server_is_down(monkeypatch):
    def boom(*a, **kw):
        raise OSError('connection refused')
    monkeypatch.setattr(hp, '_urlopen', boom)
    assert hp._fetch_server_diagnostics() is None


def test_server_block_is_null_when_the_body_is_absurd(monkeypatch):
    huge = b'{"x": "' + b'a' * (hp.SERVER_DIAG_MAX_BYTES + 10) + b'"}'
    monkeypatch.setattr(hp, '_urlopen', lambda req, timeout=None:
                        _FakeResp(huge))
    assert hp._fetch_server_diagnostics() is None


def test_server_block_uses_the_live_server_url(monkeypatch, screen, fonts,
                                               settings_path):
    seen = {}

    def fake(req, timeout=None):
        seen['url'] = req.full_url
        return _FakeResp(b'{}')
    monkeypatch.setattr(hp, '_urlopen', fake)
    hp._collect_telemetry(screen, _StubData('http://127.0.0.1:9999/'), fonts,
                          settings={})
    assert seen['url'] == 'http://127.0.0.1:9999/api/diagnostics'


# --- sending ---------------------------------------------------------------

def test_post_is_a_single_attempt_and_never_retries(monkeypatch):
    """A retry loop is an unattended resend of data confirmed exactly once."""
    calls = []

    def boom(req, timeout=None):
        calls.append(req)
        raise hp.urllib.error.URLError('down')
    monkeypatch.setattr(hp, '_urlopen', boom)
    ok, msg = hp._post_telemetry({'schema': 1})
    assert ok is False
    assert len(calls) == 1, 'made %d attempts, must be exactly 1' % len(calls)
    assert 'reach' in msg


def test_post_sends_json_by_post(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen['url'] = req.full_url
        seen['method'] = req.get_method()
        seen['ctype'] = req.get_header('Content-type')
        seen['body'] = req.data
        seen['timeout'] = timeout
        return _FakeResp(b'{"ok":true}')
    monkeypatch.setattr(hp, '_urlopen', fake)
    ok, msg = hp._post_telemetry({'schema': 1, 'device_id': 'x'})
    assert ok is True
    assert seen['url'] == 'https://hamclock-reborn.org/api/telemetry'
    assert seen['method'] == 'POST'
    assert seen['ctype'] == 'application/json'
    assert json.loads(seen['body'].decode()) == {'schema': 1,
                                                 'device_id': 'x'}
    assert seen['timeout'] == 15.0


def test_post_reports_an_http_error_verbatim_enough_to_act_on(monkeypatch):
    def boom(req, timeout=None):
        raise hp.urllib.error.HTTPError(
            'https://x', 503, 'Service Unavailable', {}, None)
    monkeypatch.setattr(hp, '_urlopen', boom)
    ok, msg = hp._post_telemetry({})
    assert ok is False and '503' in msg


def test_post_treats_a_non_2xx_as_a_failure(monkeypatch):
    monkeypatch.setattr(hp, '_urlopen',
                        lambda req, timeout=None: _FakeResp(b'', code=418))
    ok, msg = hp._post_telemetry({})
    assert ok is False and '418' in msg


def test_send_runs_on_a_daemon_thread_and_reports_back(monkeypatch):
    monkeypatch.setattr(hp, '_urlopen',
                        lambda req, timeout=None: _FakeResp(b'{}'))
    holder = {'done': False}
    t = hp._send_telemetry_async({'schema': 1}, holder)
    assert t.daemon, 'a non-daemon sender would hang kiosk shutdown'
    t.join(timeout=10)
    assert holder['done'] is True and holder['ok'] is True


def test_send_thread_never_lets_an_exception_escape(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('unexpected')
    monkeypatch.setattr(hp, '_post_telemetry', boom)
    holder = {'done': False}
    hp._send_telemetry_async({}, holder).join(timeout=10)
    assert holder['done'] is True and holder['ok'] is False


# --- confirm before send ---------------------------------------------------

def _payload(screen, fonts, **kw):
    return hp._collect_telemetry(screen, _StubData(), fonts, settings={},
                                 **kw)


def test_confirm_lines_name_the_destination(screen, fonts, no_server,
                                            settings_path):
    text = '\n'.join(t for t, _ in
                     hp._report_confirm_lines(_payload(screen, fonts),
                                              'W1ABC'))
    assert hp.TELEMETRY_URL in text
    assert 'hamclock-reborn.org' in text


def test_confirm_lines_say_plainly_it_is_not_anonymous(screen, fonts,
                                                       no_server,
                                                       settings_path):
    """The screenshot shows the operator's callsign in the header. Say so
    rather than let anyone assume otherwise."""
    lines = hp._report_confirm_lines(_payload(screen, fonts), 'W1ABC')
    text = '\n'.join(t for t, _ in lines)
    assert 'NOT ANONYMOUS' in text.upper()
    assert 'CALLSIGN' in text.upper()
    assert 'W1ABC' in text
    assert 'anonymous' not in text.replace('NOT ANONYMOUS', '')


def test_confirm_lines_inventory_every_block_that_is_sent(screen, fonts,
                                                          no_server,
                                                          settings_path):
    text = '\n'.join(t for t, _ in
                     hp._report_confirm_lines(_payload(screen, fonts),
                                              'W1ABC')).lower()
    for word in ('report id', 'hardware', 'system', 'display', 'versions',
                 'speed', 'server', 'screenshot'):
        assert word in text, 'confirm dialog never mentions %r' % word
    assert 'nothing has been sent yet' in text


def test_confirm_lines_drop_the_callsign_warning_with_no_screenshot(
        screen, fonts, no_server, settings_path):
    p = _payload(screen, fonts, screenshot=False)
    text = '\n'.join(t for t, _ in hp._report_confirm_lines(p, 'W1ABC'))
    assert 'NOT ANONYMOUS' not in text.upper()
    assert 'nothing attached' in text


def test_open_builds_the_payload_and_sends_nothing(screen, fonts, no_server,
                                                   settings_path, monkeypatch):
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    monkeypatch.setattr(hp, '_post_telemetry',
                        lambda *a, **kw: sent.append(a))
    st = hp._new_report_state()
    assert hp._report_open(st, screen, _StubData(), fonts, {}, 'W1ABC')
    assert st['stage'] == 'confirm'
    assert st['payload'] is not None
    assert sent == [], 'the first press sent a report'


def test_cancel_sends_nothing_and_drops_the_payload(screen, fonts, no_server,
                                                    settings_path,
                                                    monkeypatch):
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    st = hp._new_report_state()
    hp._report_open(st, screen, _StubData(), fonts, {}, 'W1ABC')
    assert hp._report_cancel(st)
    assert st['stage'] == 'idle'
    assert st['payload'] is None
    assert sent == []
    assert 'nothing was sent' in st['notice']


def test_confirm_is_the_only_thing_that_sends(screen, fonts, no_server,
                                              settings_path, monkeypatch):
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda payload, holder, url=None: sent.append(payload))
    st = hp._new_report_state()
    # Confirming from idle is a no-op: there is no armed payload to send.
    assert hp._report_confirm(st) is False
    assert sent == []
    hp._report_open(st, screen, _StubData(), fonts, {}, 'W1ABC')
    st['shown'] = True                 # the render loop sets this once drawn
    assert hp._report_confirm(st) is True
    assert len(sent) == 1
    assert sent[0]['schema'] == 1
    assert st['stage'] == 'sending'
    # A second confirm cannot double-send: the payload is gone.
    assert hp._report_confirm(st) is False
    assert len(sent) == 1


def test_confirm_is_refused_until_the_box_has_been_on_screen(
        screen, fonts, no_server, settings_path, monkeypatch):
    """A 'T' and a 'Y' inside the same 100 ms event batch must not send a
    report the operator was never actually shown. Consent means having seen
    the box, not having produced the keystroke."""
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    st = hp._new_report_state()
    hp._report_open(st, screen, _StubData(), fonts, {}, 'W1ABC')
    assert st['shown'] is False
    assert hp._report_confirm(st) is False
    assert sent == []
    assert st['stage'] == 'confirm', 'the dialog should still be armed'


def test_the_loop_marks_the_box_as_shown_once_it_is_drawn(monkeypatch,
                                                          settings_path):
    st = _drive(monkeypatch,
                [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t)], []],
                settings={'callsign': 'W1ABC'})
    assert st['shown'] is True


def test_same_batch_t_then_y_does_not_send(monkeypatch, settings_path):
    """The render loop drains a whole batch of events per frame; both keys in
    one batch must still leave the report unsent and the box up."""
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    st = _drive(monkeypatch,
                [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t),
                  pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y)]],
                settings={'callsign': 'W1ABC'})
    assert sent == []
    assert st['stage'] == 'confirm'


def test_poll_surfaces_the_outcome_then_returns_to_idle():
    st = hp._new_report_state()
    st['stage'] = 'sending'
    st['result'] = {'done': False}
    assert hp._report_poll(st) is False
    st['result'].update(done=True, ok=True, msg='report sent — thank you')
    assert hp._report_poll(st) is True
    assert st['stage'] == 'idle'
    assert 'sent' in st['notice']
    assert st['notice_color'] == 'good'


def test_poll_reports_a_failure_with_its_reason():
    st = hp._new_report_state()
    st['stage'] = 'sending'
    st['result'] = {'done': True, 'ok': False, 'msg': 'HTTP 503'}
    assert hp._report_poll(st) is True
    assert 'NOT sent' in st['notice'] and '503' in st['notice']
    assert st['notice_color'] == 'poor'


def test_notice_auto_clears(monkeypatch):
    st = hp._new_report_state()
    hp._report_notice(st, 'sending report…', 'accent', now=1000.0)
    assert hp._report_notice_text(st, 1000.0) == 'sending report…'
    assert hp._report_notice_text(st, 1000.0 + hp.REPORT_NOTICE_TTL_S - 0.1)
    assert hp._report_notice_text(st, 1000.0 + hp.REPORT_NOTICE_TTL_S) is None


# --- no automatic send anywhere -------------------------------------------

def _functions():
    for name, obj in vars(hp).items():
        if isinstance(obj, types.FunctionType) and obj.__module__ == hp.__name__:
            try:
                yield name, inspect.getsource(obj)
            except (OSError, TypeError):
                continue


def test_only_report_confirm_reaches_the_network():
    """Static guard: there must be no code path to a POST that does not go
    through the operator's explicit confirm."""
    senders = {name for name, src in _functions()
               if '_send_telemetry_async(' in src}
    assert senders == {'_send_telemetry_async', '_report_confirm'}, \
        'unexpected caller of the sender: %r' % (senders,)
    posters = {name for name, src in _functions()
               if '_post_telemetry(' in src}
    assert posters <= {'_post_telemetry', '_send_telemetry_async'}, \
        'unexpected caller of the POST: %r' % (posters,)
    confirmers = {name for name, src in _functions()
                  if '_report_confirm(' in src}
    assert confirmers <= {'_report_confirm', '_run_render_loop'}, \
        'unexpected caller of confirm: %r' % (confirmers,)


@pytest.mark.parametrize('fn', ['_post_telemetry', '_send_telemetry_async'])
def test_no_retry_loop_in_the_sender(fn):
    """A loop in the send path is an unattended resend of data the operator
    confirmed exactly once. Checked on the AST so a docstring cannot pass or
    fail it by accident."""
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(hp, fn))))
    loops = [n for n in ast.walk(tree)
             if isinstance(n, (ast.For, ast.While, ast.AsyncFor))]
    assert loops == [], '%s contains a loop in the send path' % fn


def test_importing_the_module_starts_no_sender():
    import threading
    assert not [t for t in threading.enumerate()
                if t.name == 'hamclock-report'], \
        'a report thread exists without anyone pressing anything'


# --- frame-time ring -------------------------------------------------------

def test_frame_ring_is_fixed_size_and_never_grows(monkeypatch):
    monkeypatch.setattr(hp, '_frame_ms_pos', 0)
    monkeypatch.setattr(hp, '_frame_ms_n', 0)
    before = len(hp._frame_ms_ring)
    for _ in range(hp._FRAME_MS_CAP * 3):
        hp._record_frame_ms(hp._mono())
    assert len(hp._frame_ms_ring) == before
    assert hp._frame_ms_n == hp._FRAME_MS_CAP


def test_frame_ring_ignores_a_clock_step(monkeypatch):
    """The loop falls back to the wall clock where monotonic is unavailable;
    an NTP step would otherwise land in p99 as a 'frame time'."""
    monkeypatch.setattr(hp, '_frame_ms_pos', 0)
    monkeypatch.setattr(hp, '_frame_ms_n', 0)
    hp._record_frame_ms(hp._mono() + 3600.0)      # negative duration
    hp._record_frame_ms(hp._mono() - 86400.0)     # a day-long 'frame'
    assert hp._frame_ms_n == 0


def test_percentiles_over_the_ring(monkeypatch):
    monkeypatch.setattr(hp, '_frame_ms_ring', [float(i) for i in range(100)])
    monkeypatch.setattr(hp, '_frame_ms_n', 100)
    s = hp._frame_ms_summary()
    assert s['n'] == 100
    assert s['p50'] == 50.0
    assert s['p90'] == 89.0
    assert s['p99'] == 98.0
    assert s['p50'] <= s['p90'] <= s['p99']


def test_frame_summary_with_no_samples(monkeypatch):
    monkeypatch.setattr(hp, '_frame_ms_n', 0)
    assert hp._frame_ms_summary() == {'p50': None, 'p90': None, 'p99': None,
                                      'n': 0}


def test_panel_ms_records_the_last_draw_cost(monkeypatch):
    monkeypatch.setattr(hp, '_panel_ms', {})
    hp._record_panel_ms('solar', hp._mono())
    assert 'solar' in hp._panel_ms
    assert 0.0 <= hp._panel_ms['solar'] < 1000.0


# --- status bar button -----------------------------------------------------

SIZES = [(720, 450), (1440, 900)]
SENTINEL = (255, 0, 255)


@pytest.mark.parametrize('size', SIZES)
def test_status_bar_registers_a_send_report_hit_target(fonts, theme, size):
    surf = pygame.Surface(size)
    rect = hp._get_layout(size)['status']
    regions = hp.draw_status_bar(surf, rect, _StubData(), fonts, theme)
    assert 'send_report' in regions, \
        'no clickable report target in the status bar at %r' % (size,)
    assert rect.contains(regions['send_report']), \
        'the report button escapes the status bar'


@pytest.mark.parametrize('size', SIZES)
def test_status_bar_button_paints_inside_the_bar(fonts, theme, size):
    """Same containment contract the rest of the chrome is held to."""
    surf = pygame.Surface(size)
    surf.fill(SENTINEL)
    rect = hp._get_layout(size)['status']
    hp._strfmt_cache['key'] = None
    hp.draw_status_bar(surf, rect, _StubData(), fonts, theme)
    surf.fill(SENTINEL, rect)
    raw = pygame.image.tostring(surf, 'RGB')
    assert raw == bytes(SENTINEL) * (size[0] * size[1]), \
        'draw_status_bar painted outside its rect'


def test_status_bar_shows_the_report_outcome(fonts, theme):
    """The notice displaces the least important text on the display for ten
    seconds, which is the right trade for telling the operator what happened."""
    surf = pygame.Surface((720, 450))
    rect = hp._get_layout((720, 450))['status']
    hp._strfmt_cache['key'] = None
    hp.draw_status_bar(surf, rect, _StubData(), fonts, theme,
                       notice='report NOT sent: HTTP 503',
                       notice_color='poor')
    # The poor colour must actually appear inside the bar.
    found = any(surf.get_at((x, y))[:3] == theme['poor']
                for x in range(rect.x, rect.right, 2)
                for y in range(rect.y, rect.bottom, 2))
    assert found, 'the failure notice is not visible in the status bar'


def test_status_bar_still_returns_a_dict_when_it_cannot_fit_the_button(
        fonts, theme):
    surf = pygame.Surface((120, 20))
    regions = hp.draw_status_bar(surf, pygame.Rect(0, 0, 120, 20),
                                 _StubData(), fonts, theme)
    assert regions == {}


# --- confirm overlay -------------------------------------------------------

def test_overlay_paints_inside_its_rect_and_offers_both_answers(fonts, theme,
                                                                screen,
                                                                no_server,
                                                                settings_path):
    surf = pygame.Surface((720, 450))
    surf.fill(SENTINEL)
    rect = hp._report_overlay_rect((720, 450))
    lines = hp._report_confirm_lines(_payload(screen, fonts), 'W1ABC')
    regions = hp.draw_report_overlay(surf, rect, lines, fonts, theme)
    assert set(regions) == {'send', 'cancel'}
    for r in regions.values():
        assert rect.contains(r)
    surf.fill(SENTINEL, rect)
    raw = pygame.image.tostring(surf, 'RGB')
    assert raw == bytes(SENTINEL) * (720 * 450), \
        'the confirm box painted outside its own rect'


def test_overlay_rect_fits_the_screen():
    for size in SIZES:
        r = hp._report_overlay_rect(size)
        assert pygame.Rect(0, 0, *size).contains(r)


def test_overlay_rect_is_sized_to_its_content(fonts, screen, no_server,
                                              settings_path):
    """A box padded out to 92% of a 450 px screen with 200 px of empty violet
    below the text reads as broken, and hides more dashboard than it needs."""
    lines = hp._report_confirm_lines(_payload(screen, fonts), 'W1ABC')
    sized = hp._report_overlay_rect((720, 450), lines, fonts)
    assert pygame.Rect(0, 0, 720, 450).contains(sized)
    assert sized.h < hp._report_overlay_rect((720, 450)).h
    body_h = fonts['small'].get_height() + 1
    assert sized.h >= len(lines) * body_h, 'content would be clipped'


def test_overlay_survives_a_degenerate_rect(fonts, theme):
    surf = pygame.Surface((720, 450))
    assert hp.draw_report_overlay(surf, pygame.Rect(0, 0, 0, 0), [],
                                  fonts, theme) == {}
    assert hp.draw_report_overlay(surf, None, [], fonts, theme) == {}


# --- render loop integration ----------------------------------------------

def _drive(monkeypatch, events, settings=None):
    """Run _run_render_loop over `events` (one list per frame) and return the
    report state dict the loop used."""
    holder = {}
    real_new = hp._new_report_state

    def capture():
        st = real_new()
        holder['state'] = st
        return st
    monkeypatch.setattr(hp, '_new_report_state', capture)
    monkeypatch.setattr(hp, 'HamClockData', _StubData)
    monkeypatch.setattr(hp, '_fetch_server_diagnostics',
                        lambda *a, **kw: {'stub': True})

    def gen():
        for ev in events:
            yield ev
        yield [pygame.event.Event(pygame.QUIT)]

    pygame.init()
    pygame.font.init()
    scr = pygame.display.set_mode((720, 450))
    try:
        hp._run_render_loop(scr, hp._make_fonts(), dict(hp.THEMES['kstate']),
                            settings if settings is not None else {},
                            injected_iter=gen())
    finally:
        pygame.init()
        pygame.font.init()
    return holder['state']


def test_t_key_opens_the_dialog_and_sends_nothing(monkeypatch, settings_path):
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    st = _drive(monkeypatch,
                [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t)],
                 [], []],
                settings={'callsign': 'W1ABC'})
    assert st['stage'] == 'confirm', \
        'T did not arm the confirm dialog (stage=%r)' % st['stage']
    assert sent == [], 'pressing the button once sent a report'


def test_confirming_after_the_dialog_sends_exactly_once(monkeypatch,
                                                        settings_path):
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda payload, holder, url=None: sent.append(payload))
    st = _drive(monkeypatch,
                [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t)],
                 [],
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y)],
                 []],
                settings={'callsign': 'W1ABC'})
    assert len(sent) == 1, 'expected exactly one send, got %d' % len(sent)
    assert sent[0]['app']['mode'] == 'pygame'
    assert st['stage'] in ('sending', 'idle')


def test_escape_closes_the_dialog_instead_of_quitting(monkeypatch,
                                                      settings_path):
    """ESC while the box is up must dismiss the report, not kill the kiosk
    out from under an operator who is still reading it."""
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    frames = [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t)],
              [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)],
              [], []]
    st = _drive(monkeypatch, frames, settings={'callsign': 'W1ABC'})
    assert st['stage'] == 'idle'
    assert sent == []
    assert 'nothing was sent' in st['notice']


def test_clicking_the_status_bar_button_opens_the_dialog(monkeypatch,
                                                         settings_path):
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    rect = hp._get_layout((720, 450))['status']
    fonts = hp._make_fonts()
    theme = dict(hp.THEMES['kstate'])
    probe = pygame.Surface((720, 450))
    hp._strfmt_cache['key'] = None
    btn = hp.draw_status_bar(probe, rect, _StubData(), fonts,
                             theme)['send_report']
    st = _drive(monkeypatch,
                [[],  # frame 1 registers the region
                 [pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                     pos=btn.center, button=1)],
                 []],
                settings={'callsign': 'W1ABC'})
    assert st['stage'] == 'confirm'
    assert sent == []


def test_the_screenshot_is_taken_before_the_dialog_is_drawn(monkeypatch,
                                                            settings_path):
    """The report must show the real dashboard, not the confirm box."""
    order = []
    real_shot = hp._screenshot_b64
    real_overlay = hp.draw_report_overlay

    def shot(surface, cap=None):
        order.append('capture')
        return real_shot(surface, cap)

    def overlay(*a, **kw):
        order.append('overlay')
        return real_overlay(*a, **kw)
    monkeypatch.setattr(hp, '_screenshot_b64', shot)
    monkeypatch.setattr(hp, 'draw_report_overlay', overlay)
    monkeypatch.setattr(hp, '_send_telemetry_async', lambda *a, **kw: None)
    _drive(monkeypatch,
           [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t)], [], []],
           settings={'callsign': 'W1ABC'})
    assert order[0] == 'capture', \
        'the confirm box was drawn before the screenshot was taken: %r' % order


def test_end_to_end_press_confirm_and_the_post_actually_goes(monkeypatch,
                                                             settings_path):
    """The whole path with nothing stubbed but the socket: press T, confirm,
    quit — and exactly one well-formed POST reaches the endpoint, even though
    the operator quit while it was in flight."""
    import time as _time
    posts = []

    def fake_urlopen(req, timeout=None):
        _time.sleep(0.2)                 # in flight when the loop exits
        posts.append((req.full_url, json.loads(req.data.decode())))
        return _FakeResp(b'{"ok":true}')
    monkeypatch.setattr(hp, '_urlopen', fake_urlopen)

    _drive(monkeypatch,
           [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t)],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y)]],
           settings={'callsign': 'W1ABC'})

    assert len(posts) == 1, 'expected one POST, got %d' % len(posts)
    url, body = posts[0]
    assert url == hp.TELEMETRY_URL
    assert body['schema'] == 1
    assert hp._DEVICE_ID_RE.match(body['device_id'])
    assert body['display']['bitsize'] == 32     # dummy driver on the runner
    assert body['screenshot_png_b64']
    assert len(body['screenshot_png_b64']) <= hp.SCREENSHOT_MAX_B64
    assert [k for k in _walk_keys(body)
            if hp._SECRET_KEY_RE.search(str(k))] == []


def test_the_loop_records_real_frame_times(monkeypatch, settings_path):
    monkeypatch.setattr(hp, '_frame_ms_pos', 0)
    monkeypatch.setattr(hp, '_frame_ms_n', 0)
    _drive(monkeypatch, [[], [], [], []])
    assert hp._frame_ms_n >= 4, \
        'the render loop recorded %d frame samples' % hp._frame_ms_n
    s = hp._frame_ms_summary()
    assert s['p50'] is not None and s['p99'] >= s['p50']


def test_the_loop_stamps_boot_to_first_paint(monkeypatch, settings_path):
    monkeypatch.setattr(hp, '_first_paint_s', None)
    _drive(monkeypatch, [[], []])
    assert hp._first_paint_s is not None
    assert hp._first_paint_s >= 0.0
