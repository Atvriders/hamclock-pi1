"""1.0.10: the four ways the SEND REPORT button let the operator down.

The report button is the only channel this project has to hardware the
maintainer does not own, so the failures that matter are the ones that leave
the person at the Pi with nothing to act on:

  * a send that wedged forever took the button with it, silently — the notice
    aged out after 10 s and every later press did nothing;
  * a send left no trace in the journal, so after the notice aged out there
    was no way to tell whether anything had been sent at all;
  * the status bar went back to looking idle while a send was still in flight;
  * the update box sat ahead of [T] in the event chain and ate it.

Each test below records, in a comment or its docstring, what the code did
before 1.0.10, because three of these bugs were invisible from the outside
and a test that does not say so is indistinguishable from a test that never
caught anything.
"""
import json
import re
import threading
import time

import pygame
import pytest

import hamclock_pygame as hp


# --- fixtures --------------------------------------------------------------

@pytest.fixture
def screen():
    """A real (dummy-driver) display surface, rebuilt per test.

    Function-scoped: the render-loop tests below run _run_render_loop to
    completion and it calls pygame.quit() on the way out.
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


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    p = tmp_path / 'settings.json'
    monkeypatch.setattr(hp, 'SETTINGS_PATH', str(p))
    return str(p)


@pytest.fixture
def rundir(tmp_path, monkeypatch):
    """A stand-in for /run/hamclock-lite."""
    d = tmp_path / 'run'
    d.mkdir()
    monkeypatch.setattr(hp, 'RUN_DIR', str(d))
    return d


class _StubData:
    """Enough HamClockData surface for the collector and the render loop."""
    def __init__(self):
        self.server_url = 'http://localhost:8080'
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


def _wedge(monkeypatch, state, screen, fonts, settings_path):
    """Open, present and confirm a report whose worker never finishes.

    That is the real shape of the failure rather than a contrived one:
    _post_telemetry's timeouts are socket timeouts, and getaddrinfo() runs
    before any socket exists, so a resolver that answers nothing leaves
    holder['done'] False for as long as the process lives. Returns the
    holder so a test can finish the send whenever it wants to.
    """
    holders = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda payload, holder, **kw: holders.append(holder))
    assert hp._report_open(state, screen, _StubData(), fonts, {},
                           settings_path=settings_path)
    # The render loop sets this once the confirm box has actually been drawn;
    # _report_confirm refuses without it.
    state['shown'] = True
    assert hp._report_confirm(state)
    assert state['stage'] == 'sending'
    return holders[0]


def _err_lines(capsys):
    out, err = capsys.readouterr()
    assert out == '', 'the report logged to stdout; the journal reads stderr'
    return [l for l in err.splitlines() if l.strip()]


class _FakeClock:
    """Stands in for the `time` module inside hamclock_pygame only.

    time() stays where it is until a test moves it, so the render loop can be
    walked past the 10 s notice TTL and the watchdog in one frame each.
    Everything else — sleep, strftime, gmtime — is the real module."""
    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch):
    c = _FakeClock()
    monkeypatch.setattr(hp, 'time', c)
    return c


def _ink(surf, rect, colour, tol=60):
    """Pixels inside `rect` within `tol` (manhattan, 8-bit RGB) of `colour`."""
    colour = tuple(colour)[:3]
    return sum(1 for x in range(rect.x, rect.right)
               for y in range(rect.y, rect.bottom)
               if sum(abs(a - b) for a, b in
                      zip(surf.get_at((x, y))[:3], colour)) < tol)


# --- the watchdog ----------------------------------------------------------

def test_a_wedged_send_is_released_only_after_the_watchdog(monkeypatch, screen,
                                                           fonts,
                                                           settings_path):
    """A resolver that never answers must not cost the operator the button.

    before: nothing but the worker could end the 'sending' stage, so the
    stage stayed there for the life of the process. _report_poll took no
    `now` at all, so this test could not even be written against it.
    """
    st = hp._new_report_state()
    _wedge(monkeypatch, st, screen, fonts, settings_path)
    started = st['sending_since']
    assert started > 0.0, "the send did not record when it started"

    # One second short of the watchdog: still waiting, because a slow send
    # that is going to succeed must not be cut off.
    assert not hp._report_poll(st, now=started
                               + hp.REPORT_SEND_WATCHDOG_S - 1.0)
    assert st['stage'] == 'sending'

    assert hp._report_poll(st, now=started + hp.REPORT_SEND_WATCHDOG_S + 1.0)
    assert st['stage'] == 'idle'
    assert st['sending_since'] == 0.0


def test_the_button_works_again_after_the_watchdog_fires(monkeypatch, screen,
                                                         fonts,
                                                         settings_path):
    """The symptom the operator actually saw: the button was dead.

    before: _report_open refuses while stage != 'idle', and the stage never
    left 'sending', so every press after a wedged send did nothing at all —
    with no notice on screen, because it had aged out ten seconds in.
    """
    st = hp._new_report_state()
    _wedge(monkeypatch, st, screen, fonts, settings_path)

    assert not hp._report_open(st, screen, _StubData(), fonts, {},
                               settings_path=settings_path), \
        "a second report was armed while one was still in flight"

    hp._report_poll(st, now=st['sending_since'] + hp.REPORT_SEND_WATCHDOG_S)
    assert hp._report_open(st, screen, _StubData(), fonts, {},
                           settings_path=settings_path), \
        "the button was still dead after the watchdog released the send"
    assert st['stage'] == 'confirm'


def test_a_send_that_completes_is_never_released_early(monkeypatch, screen,
                                                       fonts, settings_path):
    """The watchdog must not turn a slow success into a reported timeout."""
    st = hp._new_report_state()
    holder = _wedge(monkeypatch, st, screen, fonts, settings_path)
    started = st['sending_since']

    for t in (0.0, 1.0, hp.REPORT_SEND_WATCHDOG_S / 2.0,
              hp.REPORT_SEND_WATCHDOG_S - 0.5):
        assert not hp._report_poll(st, now=started + t), \
            'the send was released %.1f s in, before the watchdog' % t
        assert st['stage'] == 'sending'

    holder.update(ok=True, msg='report sent (202)', done=True)
    assert hp._report_poll(st, now=started + hp.REPORT_SEND_WATCHDOG_S - 0.4)
    assert st['stage'] == 'idle'
    assert st['notice'] == 'report sent (202)', \
        'the operator was shown %r instead of the send result' % st['notice']


def test_a_late_worker_cannot_post_a_second_outcome(monkeypatch, screen,
                                                    fonts, settings_path):
    """The stranded thread is still alive after the watchdog gives up on it.
    If it ever finishes, its answer is about a report the operator has
    already been told timed out, and must not overwrite that.

    Nor may it touch the NEXT send, which is the case the timeout notice
    invites: the operator presses T again, and the first thread wakes while
    the second send is in flight."""
    st = hp._new_report_state()
    first = _wedge(monkeypatch, st, screen, fonts, settings_path)
    started = st['sending_since']
    assert hp._report_poll(st, now=started + hp.REPORT_SEND_WATCHDOG_S)
    timed_out = st['notice']

    first.update(ok=True, msg='report sent (202)', done=True)
    assert not hp._report_poll(st, now=started + hp.REPORT_SEND_WATCHDOG_S + 5)
    assert st['notice'] == timed_out

    second = _wedge(monkeypatch, st, screen, fonts, settings_path)
    assert second is not first
    sending = st['notice']
    assert not hp._report_poll(st, now=st['sending_since'] + 1.0), \
        "the first send's late answer ended the second one"
    assert st['stage'] == 'sending'
    assert st['notice'] == sending

    second.update(ok=False, msg='send failed: [Errno 111] refused', done=True)
    assert hp._report_poll(st, now=st['sending_since'] + 2.0)
    assert '[Errno 111] refused' in st['notice'], \
        'the second send was reported with %r' % st['notice']


def test_a_released_worker_that_finishes_late_says_so_in_the_journal(
        monkeypatch, screen, fonts, settings_path, capsys):
    """The stranded thread, run for real rather than stood in by a lambda.

    Every other test here replaces _send_telemetry_async, so none of them
    runs the worker code that decides what a late finish does. Measured:
    deleting `holder['released'] = True` from _report_poll, or the worker's
    branch on it, passed the whole suite. That branch is the only record of
    a send the watchdog gave up on that then arrived after all — the bar
    told the operator it timed out, and whoever reads the journal next needs
    to know the first report landed before they chase a second.

    It must also be the ONLY thing a late finish does: the bar keeps the
    timeout it showed, and polling picks up nothing.

    No sleeps: the POST blocks on an Event the test sets only after the
    watchdog has released the send, and the thread is joined."""
    real_async = hp._send_telemetry_async
    monkeypatch.setattr(hp, '_fetch_server_diagnostics',
                        lambda *a, **kw: {'stub': True})
    late_lines = {}
    release_lines = set()
    for name, outcome in (('sent', (True, 'report sent (202)')),
                          ('failed', (False, 'send failed: [Errno 111] refused'))):
        gate = threading.Event()
        threads = []

        def post(payload, url=None, timeout=None, _gate=gate, _out=outcome):
            _gate.wait(30.0)
            return _out

        monkeypatch.setattr(hp, '_post_telemetry', post)
        monkeypatch.setattr(
            hp, '_send_telemetry_async',
            lambda *a, **kw: threads.append(real_async(*a, **kw)))
        try:
            st = hp._new_report_state()
            assert hp._report_open(st, screen, _StubData(), fonts, {},
                                   settings_path=settings_path)
            st['shown'] = True
            assert hp._report_confirm(st)
            assert len(threads) == 1 and threads[0].is_alive()
            started = st['sending_since']
            capsys.readouterr()

            assert hp._report_poll(st, now=started
                                   + hp.REPORT_SEND_WATCHDOG_S + 1.0)
            on_bar = (st['stage'], st['notice'], st['notice_color'])
            release_lines.update(_err_lines(capsys))
        finally:
            gate.set()
        threads[0].join(10.0)
        assert not threads[0].is_alive(), 'the worker never finished'

        got = _err_lines(capsys)
        assert len(got) == 1, (
            'a released send that then %s wrote %d journal lines, expected '
            '1: %r' % (name, len(got), got))
        late_lines[name] = got[0]
        assert not hp._report_poll(st, now=started
                                   + hp.REPORT_SEND_WATCHDOG_S + 5.0), \
            'the late finish reached the status bar'
        assert (st['stage'], st['notice'], st['notice_color']) == on_bar, \
            'the late finish changed what the bar shows'

    assert len(set(late_lines.values())) == 2, (
        'the journal says the same thing whether the late report arrived or '
        'not: %r' % late_lines)
    assert not set(late_lines.values()) & release_lines
    assert '[Errno 111] refused' in late_lines['failed'], late_lines['failed']
    assert all('report' in l.lower() for l in late_lines.values()), late_lines


def test_a_finished_send_is_reported_as_finished_however_late_it_is_polled(
        monkeypatch, screen, fonts, settings_path, capsys):
    """The watchdog is for a send with no answer, not for a late poll.

    _report_poll is not called every 100 ms without fail. S opens the setup
    wizard, which runs its own loop over the dashboard for as long as the
    operator stays in it, and a send confirmed just before that finishes
    with nobody polling. It HAS an answer, and that answer is what the bar
    and the journal must give — not a timeout invented because the clock was
    read before the holder. Measured: testing the elapsed time ahead of
    holder['done'] in _report_poll passed every test in the suite, and would
    tell an operator whose report arrived that it had not.

    Compared with the same outcome polled promptly, so nothing here depends
    on the wording of either."""
    def digitless(lines):
        return [re.sub(r'\d+(\.\d+)?', '#', l) for l in lines]

    for ok, msg in ((True, 'report sent (202)'),
                    (False, 'send failed: [Errno 111] refused')):
        seen = {}
        for late in (False, True):
            st = hp._new_report_state()
            h = _wedge(monkeypatch, st, screen, fonts, settings_path)
            h.update(ok=ok, msg=msg, done=True)
            capsys.readouterr()
            after = 3.0 * hp.REPORT_SEND_WATCHDOG_S if late else 2.0
            assert hp._report_poll(st, now=st['sending_since'] + after)
            assert st['stage'] == 'idle'
            seen[late] = (st['notice'], st['notice_color'],
                          digitless(_err_lines(capsys)))
        assert seen[True] == seen[False], (
            'a send that %s, polled after the watchdog, was reported as %r '
            'instead of %r' % ('arrived' if ok else 'failed', seen[True],
                               seen[False]))


def test_the_watchdog_outlasts_a_slow_send_and_still_fires():
    """Every other watchdog test here is written relative to
    REPORT_SEND_WATCHDOG_S, so none of them can see the constant itself go
    wrong. Measured against this file as first written: with it set to 5 s,
    and again to 100000 s, every test in it passed. At 5 s a report slower than five seconds is abandoned
    and the operator told it failed while it is still on its way; at 100000 s
    the button is dead for a day, which is the bug.

    Lower bound: the worker makes one diagnostics fetch and one POST, exactly
    one attempt each, under SERVER_DIAG_TIMEOUT_S and TELEMETRY_TIMEOUT_S, so
    a server that goes quiet costs at most their sum. Upper bound: three
    times that sum. That one is a policy, not a measurement; it is here so
    the watchdog cannot be made to pass by making it never fire."""
    quiet_server = hp.SERVER_DIAG_TIMEOUT_S + hp.TELEMETRY_TIMEOUT_S
    assert quiet_server < hp.REPORT_SEND_WATCHDOG_S <= 3 * quiet_server, (
        'watchdog %.0f s against a %.0f s slow-but-answering send'
        % (hp.REPORT_SEND_WATCHDOG_S, quiet_server))


def test_a_released_send_is_reported_as_a_failure(monkeypatch, screen, fonts,
                                                  settings_path):
    """What the bar says when the watchdog lets go.

    Three edits to the fix passed every test this file had before this one:
    a watchdog that released the button without a word, one that left
    'sending report…' up, and one that announced 'report sent' in the success
    colour. The last
    is the worst thing this button could do — tell the operator a report
    arrived when it never left the Pi. So the release has to be on the bar,
    no longer say sending, and look like a failed send, not a good one."""
    colour = {}
    for name, ok, msg in (('sent', True, 'report sent (202)'),
                          ('failed', False, 'send failed: timed out')):
        ref = hp._new_report_state()
        h = _wedge(monkeypatch, ref, screen, fonts, settings_path)
        h.update(ok=ok, msg=msg, done=True)
        assert hp._report_poll(ref, now=ref['sending_since'] + 2.0)
        colour[name] = ref['notice_color']
    assert colour['sent'] != colour['failed']

    st = hp._new_report_state()
    _wedge(monkeypatch, st, screen, fonts, settings_path)
    sending = hp._report_notice_text(st, st['sending_since'])
    released_at = st['sending_since'] + hp.REPORT_SEND_WATCHDOG_S + 1.0
    assert hp._report_poll(st, now=released_at)

    shown = hp._report_notice_text(st, released_at)
    assert shown, 'the watchdog handed the button back without a word'
    assert shown != sending, 'the bar still says %r after the release' % shown
    assert st['notice_color'] == colour['failed'], (
        'the release was painted %r; a failed send is %r and a good one %r'
        % (st['notice_color'], colour['failed'], colour['sent']))


def test_sending_since_is_zero_whenever_nothing_is_in_flight(monkeypatch,
                                                            screen, fonts,
                                                            settings_path):
    """The watchdog keys off this, so a stale non-zero value would release
    the next send the instant it started. Fresh, and after each of the three
    ways a send can end."""
    st = hp._new_report_state()
    assert st['sending_since'] == 0.0
    for name, finish, after in (
            ('sent', lambda h: h.update(ok=True, msg='ok', done=True), 2.0),
            ('failed', lambda h: h.update(ok=False, msg='x', done=True), 2.0),
            ('released', lambda h: None, hp.REPORT_SEND_WATCHDOG_S + 1.0)):
        finish(_wedge(monkeypatch, st, screen, fonts, settings_path))
        assert st['sending_since'] > 0.0
        assert hp._report_poll(st, now=st['sending_since'] + after)
        assert st['stage'] == 'idle'
        assert st['sending_since'] == 0.0, \
            'sending_since left at %r after a send that %s' % (
                st['sending_since'], name)


# --- the status bar during a send -----------------------------------------

def test_sending_stays_on_the_status_bar_for_the_whole_send(monkeypatch,
                                                            screen, fonts,
                                                            settings_path):
    """The one moment the bar must not look idle is while a send is running.

    before: 'sending report…' was posted with the ordinary 10 s TTL, so on
    anything slower than that the bar reverted to the normal status line
    while the button was still refusing presses — the display said nothing
    was happening at exactly the moment something was.
    """
    st = hp._new_report_state()
    _wedge(monkeypatch, st, screen, fonts, settings_path)
    started = st['sending_since']

    # Whatever the send posts when it starts, not a copy of its wording: the
    # behaviour is that the line survives the TTL, not what it says.
    sending = hp._report_notice_text(st, started)
    assert sending, 'the bar showed nothing the moment the send started'
    for t in (hp.REPORT_NOTICE_TTL_S + 1.0, 30.0,
              hp.REPORT_SEND_WATCHDOG_S - 1.0):
        assert hp._report_notice_text(st, started + t) == sending, \
            'the bar went idle %.0f s into a send that was still running' % t


def test_the_outcome_replaces_the_sending_line(monkeypatch, screen, fonts,
                                               settings_path):
    st = hp._new_report_state()
    holder = _wedge(monkeypatch, st, screen, fonts, settings_path)
    started = st['sending_since']
    sending = hp._report_notice_text(st, started)
    holder.update(ok=True, msg='report sent (202)', done=True)
    assert hp._report_poll(st, now=started + 3.0)

    shown = hp._report_notice_text(st, started + 3.0)
    assert shown == 'report sent (202)'
    assert shown != sending


def test_the_outcome_still_ages_out_like_any_other_notice(monkeypatch, screen,
                                                          fonts,
                                                          settings_path):
    """Keeping the sending line up must not be done by making notices
    permanent: once the send is over the bar has to go back to showing the
    clock and the band summary."""
    st = hp._new_report_state()
    holder = _wedge(monkeypatch, st, screen, fonts, settings_path)
    started = st['sending_since']
    holder.update(ok=True, msg='report sent (202)', done=True)
    hp._report_poll(st, now=started + 3.0)

    assert st['notice_until'] - (started + 3.0) <= hp.REPORT_NOTICE_TTL_S + 0.5
    assert hp._report_notice_text(st, st['notice_until'] - 0.5)
    assert hp._report_notice_text(st, st['notice_until'] + 0.5) is None


def test_the_loop_paints_the_send_until_it_ends(rundir, monkeypatch,
                                               settings_path, clock):
    """Looked at on the frames the real render loop drew, not on the string.

    The version of this test it replaces fed 'sending report…' to
    draw_status_bar by hand and counted the ink, which showed that the bar
    CAN paint a notice — true before 1.0.10 as well — and not that the loop
    hands it one at +30 s. This drives T, Y and a send that never finishes,
    moves the clock past the 10 s notice TTL and then past the watchdog, and
    compares the bar's text area on each frame with the same bar drawn off
    screen from the state the loop ended in.

    before: at +30 s the bar was back to the idle status line (no accent ink
    left of the REPORT chip), and after the watchdog there was no watchdog.
    """
    holders = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda payload, holder, **kw: holders.append(holder))
    painted = []
    real_bar = hp.draw_status_bar

    def spy(screen, rect, *a, **kw):
        regions = real_bar(screen, rect, *a, **kw)
        painted.append((pygame.Rect(rect), kw.get('notice'),
                        kw.get('notice_color')))
        return regions

    monkeypatch.setattr(hp, 'draw_status_bar', spy)
    shots = {}
    t0 = clock.now

    def snap(name):
        return lambda: shots.__setitem__(
            name, pygame.display.get_surface().copy())

    def at(dt):
        def move():
            clock.now = t0 + dt
        return move

    report, _update = _drive(monkeypatch, [
        [], snap('idle'),
        _key(pygame.K_t), [], _key(pygame.K_y),   # the send starts at t0
        at(hp.REPORT_NOTICE_TTL_S + 20.0), [], snap('mid'),
        at(hp.REPORT_SEND_WATCHDOG_S + 5.0), [], snap('released'),
    ], settings={'callsign': 'W1ABC'})

    assert len(holders) == 1, 'Y did not start exactly one send'
    assert report['stage'] == 'idle', 'the watchdog never released the send'
    mid_notice = [n for _r, n, _c in painted if n][0]

    fonts = hp._make_fonts()       # the loop's own died with pygame.quit()
    theme = dict(hp.THEMES['kstate'])
    rect = hp._get_layout((720, 450))['status']

    def text_ink(surf, colour):
        # Left of the REPORT [T] chip, which is itself drawn in accent.
        scratch = pygame.Surface((720, 450))
        chip = real_bar(scratch, rect, _StubData(), fonts, theme)
        area = pygame.Rect(rect.x, rect.y, chip['send_report'].x - rect.x - 8,
                           rect.h)
        return _ink(surf, area, theme[colour])

    def drawn(notice, colour):
        s = pygame.Surface((720, 450))
        real_bar(s, rect, _StubData(), fonts, theme,
                 notice=notice, notice_color=colour)
        return s

    assert text_ink(shots['idle'], 'accent') == 0, \
        'the idle status line has accent-coloured text; the count below is moot'

    want = text_ink(drawn(mid_notice, 'accent'), 'accent')
    got = text_ink(shots['mid'], 'accent')
    assert got >= 0.9 * want > 0, (
        '%.0f s into a send still running the bar painted %d px of the '
        'sending line against %d drawn whole — it had gone back to idle'
        % (hp.REPORT_NOTICE_TTL_S + 20.0, got, want))

    col = report['notice_color']
    want = text_ink(drawn(report['notice'], col), col)
    got = text_ink(shots['released'], col)
    assert got >= 0.9 * want > 0, (
        'after the watchdog the bar painted %d px of %r against %d'
        % (got, report['notice'], want))
    assert text_ink(shots['released'], 'accent') == 0, \
        'the sending line was still on the bar after the release'
    assert text_ink(shots['released'], 'good') == 0, \
        'the release was painted in the success colour'


# --- the journal -----------------------------------------------------------

def test_every_outcome_writes_exactly_one_line_to_stderr(monkeypatch, screen,
                                                         fonts, settings_path,
                                                         capsys):
    """A send has to be findable in `journalctl -u hamclock-lite` afterwards.

    before: nothing about a report was ever written to stderr. The only
    evidence a send had happened was a status-bar line that aged out after
    10 s, so an operator asked "did it go?" half an hour later had no way to
    answer, and neither did the maintainer reading their journal.

    One line each, and three distinguishable lines: a journal that says the
    same thing whether the report arrived or timed out is no better than
    silence.
    """
    lines = {}
    for name, finish in (
            ('success', lambda h: h.update(ok=True, msg='report sent (202)',
                                           done=True)),
            ('failure', lambda h: h.update(
                ok=False, msg='send failed: [Errno -3] name resolution',
                done=True)),
            ('watchdog', lambda h: None)):
        st = hp._new_report_state()
        holder = _wedge(monkeypatch, st, screen, fonts, settings_path)
        finish(holder)
        # Drop whatever opening and confirming wrote; this is about outcomes.
        capsys.readouterr()
        now = st['sending_since'] + (hp.REPORT_SEND_WATCHDOG_S + 1.0
                                     if name == 'watchdog' else 2.0)
        assert hp._report_poll(st, now=now)
        got = _err_lines(capsys)
        assert len(got) == 1, \
            '%s wrote %d lines to the journal, expected 1: %r' % (
                name, len(got), got)
        lines[name] = got[0]

    for name, line in lines.items():
        assert 'report' in line.lower(), \
            '%r does not say what it is about' % line
    assert len(set(lines.values())) == 3, \
        'the journal cannot tell these outcomes apart: %r' % lines
    assert '[Errno -3] name resolution' in lines['failure'], \
        'the failure line does not say why: %r' % lines['failure']


def test_a_send_that_is_still_running_writes_nothing_each_poll(monkeypatch,
                                                               screen, fonts,
                                                               settings_path,
                                                               capsys):
    """_report_poll runs every frame. A line per frame would bury the journal
    at ten a second."""
    st = hp._new_report_state()
    _wedge(monkeypatch, st, screen, fonts, settings_path)
    capsys.readouterr()
    for i in range(20):
        hp._report_poll(st, now=st['sending_since'] + i * 0.1)
    assert _err_lines(capsys) == []


def test_a_send_is_in_the_journal_before_it_can_wedge(monkeypatch, screen,
                                                      fonts, settings_path,
                                                      capsys):
    """The outcome lines only exist if the process lives to write one. An
    operator who gives up on a silent button and power-cycles the Pi inside
    the watchdog window leaves this line as the only sign a send was tried.
    Deleting it passed every other test in this file."""
    capsys.readouterr()
    st = hp._new_report_state()
    assert hp._report_open(st, screen, _StubData(), fonts, {},
                           settings_path=settings_path)
    assert _err_lines(capsys) == [], 'opening the box is not a send'
    st['shown'] = True
    monkeypatch.setattr(hp, '_send_telemetry_async', lambda *a, **kw: None)
    assert hp._report_confirm(st)
    got = _err_lines(capsys)
    assert len(got) == 1 and 'report' in got[0].lower(), got


# --- render loop: the update box and the [T] key --------------------------

def _status(state='available', installed='1.0.9', available='1.0.10',
            detail=''):
    return {'state': state, 'installed': installed, 'available': available,
            'detail': detail, 'checked': '2026-09-20T07:00:00Z'}


def _key(k):
    return [pygame.event.Event(pygame.KEYDOWN, key=k)]


def _drive(monkeypatch, events, settings=None, live=None):
    """Run the REAL _run_render_loop over `events`, one list per frame.

    The [T] defect is in the ORDER of the event chain, not in any handler, so
    it is only visible from the loop: a test that called _report_open itself
    would pass against the broken code. Returns (report_state, update_state).

    `live`, if given, receives the loop's two state dicts ('report' and
    'update') the moment the loop allocates them, so a callable in `events`
    can look at them between frames. Record there, assert afterwards: the
    loop absorbs any exception raised inside a frame, an AssertionError
    included, and carries on. live['wizard'] counts S presses that reached
    the dashboard's own S arm.
    """
    holder = {} if live is None else live
    real_report = hp._new_report_state
    real_update = hp._new_update_state
    holder['wizard'] = []

    def no_wizard(*a, **kw):
        # S on the dashboard opens the real setup wizard, which reads the
        # real event queue — empty under the dummy driver — until its own
        # timeout: measured at 166 s for one stray S when the update box
        # failed to open. Answer as a cancelled wizard would, and count it.
        holder['wizard'].append(1)
        return (None, 'settings unchanged', 'label')

    monkeypatch.setattr(hp, '_reopen_setup', no_wizard)

    def cap_report():
        st = real_report()
        holder['report'] = st
        return st

    def cap_update():
        st = real_update()
        holder['update'] = st
        return st

    monkeypatch.setattr(hp, '_new_report_state', cap_report)
    monkeypatch.setattr(hp, '_new_update_state', cap_update)
    monkeypatch.setattr(hp, 'HamClockData', _StubData)
    monkeypatch.setattr(hp, '_fetch_server_diagnostics',
                        lambda *a, **kw: {'stub': True})

    def gen():
        for ev in events:
            # A callable is not a frame: it runs between two frames, when
            # the display holds everything the previous one drew — the place
            # to take a screenshot or move the clock.
            if callable(ev):
                ev()
                continue
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
    return holder['report'], holder['update']


def _box_state(live, into):
    """A between-frames step recording where both boxes stand right then."""
    def record():
        into['update'] = (live['update']['stage'], live['update']['shown'])
        into['report'] = live['report']['stage']
    return record


def _assert_update_box_was_up(before_t):
    # Without this the T tests below pass for the wrong reason whenever U
    # stops opening the box: T then reaches the ordinary KEYDOWN arm, which
    # opens the report box by itself, and every assertion after it holds.
    # Measured by making _update_open a no-op: both went green.
    assert before_t == {'update': ('confirm', True), 'report': 'idle'}, (
        'the update box was not on screen when T was pressed (%r), so this '
        'says nothing about the key reaching past it' % (before_t,))


def test_t_while_the_update_box_is_up_opens_the_report_box(rundir, monkeypatch,
                                                           settings_path):
    """An operator looking at "install 1.0.10?" who presses the documented
    report key should get the report box, not silence.

    before: `elif update['stage'] == 'confirm':` sits ahead of the KEYDOWN arm
    that owns K_t, and it handled only Y/RETURN/KP_ENTER/N/ESCAPE/Q — every
    other key fell off the end of the branch and was discarded. The update box
    stayed up, the report box never opened, and nothing on screen changed.
    """
    (rundir / 'update-status.json').write_text(json.dumps(_status()))
    sent = []
    monkeypatch.setattr(hp, '_send_telemetry_async',
                        lambda *a, **kw: sent.append(a))
    live, before_t = {}, {}

    report, update = _drive(monkeypatch, [
        [],                      # frame 1: the loop reads update-status.json
        _key(pygame.K_u),        # frame 2: arm the update box
        [],                      # frame 3: it is drawn, and 'shown'
        _box_state(live, before_t),
        _key(pygame.K_t),        # frame 4: the key under test
        [], [],                  # the report box is drawn
    ], settings={'callsign': 'W1ABC'}, live=live)

    _assert_update_box_was_up(before_t)
    assert update['stage'] == 'idle', \
        'the update box swallowed T and stayed up (stage=%r)' % update['stage']
    assert report['stage'] == 'confirm', \
        'T did not open the report box (stage=%r)' % report['stage']
    assert report['shown'], 'the report box was armed but never drawn'
    assert not (rundir / 'update.request').exists(), \
        'answering the update box with T asked for the update'
    assert sent == [], 'T sent a report without a confirm'


def test_t_in_the_update_box_does_not_install_anything(rundir, monkeypatch,
                                                       settings_path):
    """T means "not now" to the update box. The update must be dismissed, not
    deferred into a state where a later stray RETURN installs it."""
    (rundir / 'update-status.json').write_text(json.dumps(_status()))
    monkeypatch.setattr(hp, '_send_telemetry_async', lambda *a, **kw: None)
    live, before_t = {}, {}

    _report, update = _drive(monkeypatch, [
        [], _key(pygame.K_u), [], _box_state(live, before_t),
        _key(pygame.K_t), [], [],
    ], live=live)
    _assert_update_box_was_up(before_t)
    assert update['stage'] == 'idle'
    assert not update['shown']
    assert sorted(p.name for p in rundir.iterdir()) == ['update-status.json']


def test_a_key_the_update_box_does_not_take_says_so(rundir, monkeypatch,
                                                    settings_path):
    """before: every key other than Y/RETURN/N/ESC/Q was discarded with no
    feedback at all, so a kiosk operator pressing S or an arrow key had no
    way to know whether the box had even seen it."""
    (rundir / 'update-status.json').write_text(json.dumps(_status()))
    shots = {}
    # What reached the glass, frame by frame. Under the dummy driver the
    # display surface IS what was drawn, so a pixel read from it cannot tell
    # a bar that was presented from one painted into the back buffer and
    # never handed to display.update(); on the Pi's KMSDRM only the former
    # is seen. Measured: dropping the status bar from the overlay branch's
    # update() list passed every pixel assertion below.
    presented = []
    real_update, real_flip = pygame.display.update, pygame.display.flip

    def update_spy(*a, **kw):
        rects = a[0] if a else kw.get('rectangle', kw.get('rectangles'))
        if rects is None:
            presented.append(None)                 # the whole display
        elif isinstance(rects, pygame.Rect):
            presented.append([pygame.Rect(rects)])
        else:
            presented.append([pygame.Rect(r) for r in rects if r is not None])
        return real_update(*a, **kw)

    def flip_spy():
        presented.append(None)
        return real_flip()

    monkeypatch.setattr(pygame.display, 'update', update_spy)
    monkeypatch.setattr(pygame.display, 'flip', flip_spy)

    def snap(name):
        def take():
            shots[name] = pygame.display.get_surface().copy()
            shots['t_' + name] = time.time()
            presented.append(name)
        return take

    live = {}
    _report, update = _drive(monkeypatch, [
        [], _key(pygame.K_u), [], snap('box'), _key(pygame.K_s), snap('key'),
        [],
    ], live=live)
    assert live['wizard'] == [], \
        'S reached the setup wizard: the update box was not up to take it'
    assert update['stage'] == 'confirm', \
        'an unknown key dismissed the box instead of prompting'
    assert update['notice'], 'an unhandled key left the operator no feedback'
    # Still current on the frame that drew it (not measured against however
    # long the loop then took to shut down).
    assert update['notice_until'] > shots['t_key']

    # In state is not on screen. The dashboard is frozen while the box is up,
    # so a hint left to the status bar's normal cadence is never painted, and
    # the box's own outcome overwrites it when it closes: the first version
    # of this fix posted the hint exactly that way, and the two assertions
    # above passed against it. Compare the bar's visible pixels on the frame
    # S was pressed with the bar drawn off screen carrying the hint.
    fonts = hp._make_fonts()       # the loop's own died with pygame.quit()
    theme = dict(hp.THEMES['kstate'])
    rect = hp._get_layout((720, 450))['status']
    box = hp._report_overlay_rect((720, 450), update['lines'], fonts)
    want = pygame.Surface((720, 450))
    hp.draw_status_bar(want, rect, _StubData(), fonts, theme,
                       notice=update['notice'],
                       notice_color=update['notice_color'],
                       update_label=update['button'])

    def same(a, b):
        return sum(abs(p - q) for p, q in zip(a[:3], b[:3])) < 30

    # The pixels the hint has to change: visible (not under the box) and
    # different, in the hint's bar, from what the frozen bar showed before.
    footprint = [(x, y) for x in range(rect.x, rect.right)
                 for y in range(rect.y, rect.bottom)
                 if not (box is not None and box.collidepoint(x, y))
                 and not same(want.get_at((x, y)), shots['box'].get_at((x, y)))]
    assert len(footprint) >= 20, \
        'the bar already showed the hint before the key; nothing to compare'
    hit = sum(1 for p in footprint
              if same(shots['key'].get_at(p), want.get_at(p)))
    assert hit >= 0.9 * len(footprint), (
        'the hint %r reached %d of the %d bar pixels it should have changed '
        '— it is in state and not on the screen'
        % (update['notice'], hit, len(footprint)))

    # And those pixels were presented on that frame, not left in the back
    # buffer for a flip that comes only once the box has closed and its
    # outcome has overwritten the hint.
    s_frame = presented[presented.index('box') + 1:presented.index('key')]
    assert s_frame, 'nothing at all was presented on the frame S was pressed'
    if None not in s_frame:
        shown = [r for rects in s_frame for r in rects]
        dark = [p for p in footprint
                if not any(r.collidepoint(p) for r in shown)]
        assert not dark, (
            '%d of the %d bar pixels the hint changed were drawn but never '
            'presented on the frame S was pressed (presented: %r)'
            % (len(dark), len(footprint), shown))
