"""1.0.11's render schedule, proven through the real render loop.

The schedule's arithmetic (_next_due, _next_tab_at, _dirty_span) is tested on
its own in test_1_0_11.py. What those tests cannot see is the loop's WIRING of
that arithmetic, and a review of 1.0.11 mutated exactly that, line by line;
the whole suite passed every time. This module runs the real
_run_render_loop headless on a fake clock and checks what it actually
presented, frame by frame.

Why there is a schedule at all: SDL 2.32 under KMSDRM (the Pi) has no
framebuffer of its own, so display.update(rects) uploads ONE full-width band
from the top of the highest dirty rect to the bottom of the lowest. The
header (rows 0-60 at 1440x900) and a panel near the bottom in one present
upload nearly the whole screen. So the header redraws on whole seconds, the
status bar on the half second, every 60 s panel at k*6 + 0.25 s, and an image
panel with no image yet retries every 15 s at x.75 s. The propagation tab
cycle advances on that panel's own grid slot.

The harness:
  * hamclock_pygame.time is a fake. Its monotonic clock only moves when a
    frame ends or the loop sleeps; its wall clock is that plus an offset a
    scenario can step. Local time is UTC, so nothing depends on the TZ of the
    machine running the suite.
  * pygame.time.Clock is a fake whose tick() ends the frame ~100 ms (plus a
    little jitter) later, or wherever a scenario wants one frame to land.
  * HamClockData is a stand-in with fixed feeds and real PNG bytes.
  * pygame.display.update/flip are spied. Every present is recorded with its
    rects, and two emulated displays are updated from the back buffer:
    'span', the full-width band SDL uploads on the Pi, and 'exact', only the
    rects given, as an X11 desktop does.

The letters in the docstrings are the review's list of properties (A-G),
plus H: every draw lands in the first frame of its own slot. A and B alone let
a panel drift off its grid for as long as the one-frame deferral kept it out
of the header's upload, and the deferral is a backstop, not the schedule.

test_a_long_frame_at_a_tab_advance_still_waits_for_the_header guards a bug
this harness found: the tab advance used to bypass the one-frame deferral.
"""
import calendar
import io
import math
import os
import sys
import time as _real_time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp


BIG = (1440, 900)        # the Pi's own mode: where geometry is the point
SMALL = (720, 450)       # pixel-for-pixel checks, at a quarter of the cost

# 2027-01-15 08:00:00 UTC, a whole second: the schedule's epoch is the start
# of the run, so a slot reads directly as "seconds into the run".
WALL0 = 1_800_000_000.0
# 17:59:50 local (= UTC here): OPEN BANDS turns from DAY to NIGHT 10 s in.
WALL0_DUSK = WALL0 + 10 * 3600 - 10

FRAME_S = 0.1
JITTER = (0.0, 0.013, 0.031, 0.007, 0.022)     # frames are never exactly 100 ms
MAX_FRAME_S = FRAME_S + max(JITTER)

IMAGE_KEYS = ('muf-map', 'solar-image', 'drap', 'real-drap', 'enlil')
GREEN, RED, MAGENTA = (90, 130, 90), (200, 40, 40), (255, 0, 255)

# Loop name -> layout key, for every panel the loop schedules.
LAYOUT_KEY = dict((n, 'muf' if n == 'muf_text' else n) for n in hp._CADENCE_S)
SLOW = frozenset(n for n, c in hp._CADENCE_S.items() if c > 1.0)
CHROME = frozenset(('header', 'status'))

SETTINGS = {'callsign': 'K0XYZ',
            # A valid id, so opening the report box never tries to persist one.
            'device_id': '0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0'}
SOLAR = {'sfi': '121', 'ssn': '77', 'kIndex': '3', 'aIndex': '8',
         'xray': 'C1.2', 'solarWind': '466.9', 'magneticField': '-1.4',
         'geomagField': 'UNSETTLD', 'signalNoise': 'S2-S3', 'fof2': '7.1'}
# Day and night disagree, so the OPEN BANDS body changes at 18:00 as well as
# its title.
BANDS = {'80m-40m': {'day': 'Poor', 'night': 'Fair'},
         '30m-20m': {'day': 'Good', 'night': 'Good'},
         '17m-15m': {'day': 'Good', 'night': 'Fair'},
         '12m-10m': {'day': 'Fair', 'night': 'Poor'}}
SPOTS = [{'frequency': '14025.6', 'band': '20m', 'dx': 'WG3J',
          'spotter': 'N1AU', 'time': '1913 2026-10-21'},
         {'frequency': '7012.0', 'band': '40m', 'dx': 'PJ2/K8PGJ',
          'spotter': 'M9UDX', 'time': '1912 2026-10-21'}] * 8

_tobytes = getattr(pygame.image, 'tobytes', None) or pygame.image.tostring


# ------------------------------------------------------------------ harness

class _Clock(object):
    """Monotonic seconds that only frames and sleeps move, and a wall clock
    that is those plus an offset a scenario may step."""

    MONO0 = 10_000.0

    def __init__(self, wall0):
        self.mono = self.MONO0
        self.offset = wall0 - self.MONO0

    @property
    def rel(self):
        return self.mono - self.MONO0

    def wall(self):
        return self.mono + self.offset


class _FakeTime(object):
    """Stands in for the `time` module inside hamclock_pygame."""

    def __init__(self, clock):
        self._clock = clock

    def time(self):
        return self._clock.wall()

    def monotonic(self):
        return self._clock.mono

    def perf_counter(self):
        return self._clock.mono

    def sleep(self, secs):
        self._clock.mono += max(0.0, float(secs))

    def gmtime(self, secs=None):
        return _real_time.gmtime(self.time() if secs is None else secs)

    # Local time is UTC (see the module docstring).
    localtime = gmtime

    def strftime(self, fmt, t=None):
        return _real_time.strftime(fmt, self.localtime() if t is None else t)

    def mktime(self, t):
        return float(calendar.timegm(t))

    def __getattr__(self, name):
        # struct_time and friends. Everything that READS a clock is above.
        return getattr(_real_time, name)


class _FrameClock(object):
    """Replaces pygame.time.Clock: tick() is where a frame ends."""

    def __init__(self, run):
        self._run = run

    def tick(self, framerate=0):
        self._run._tick()
        return int(FRAME_S * 1000)

    def get_time(self):
        return int(FRAME_S * 1000)

    get_rawtime = get_time

    def get_fps(self):
        return 1.0 / FRAME_S


class _Feeds(object):
    """HamClockData with nothing behind it: fixed feeds, real PNG bytes."""

    def __init__(self, run):
        w = run.wall0
        self.server_url = 'http://127.0.0.1:9'
        self.solar = dict(SOLAR)
        self.bands = dict((k, dict(v)) for k, v in BANDS.items())
        self.dxspots = list(SPOTS)
        self.health = {'solar_age': 30, 'bands_age': 30, 'dx_age': 20,
                       'muf_age': 400}
        self.images = dict((k, run.png[k]) for k in run.images)
        self.image_fetched_at = dict((k, w - 30.0) for k in self.images)
        missing = [k for k in IMAGE_KEYS if k not in self.images]
        self.image_fail_streak = dict((k, 1) for k in missing)
        self.image_next_due = dict((k, w + 15.0) for k in missing)
        self.last_data_refresh = w - 5.0
        self.last_image_refresh = w - 30.0
        self.last_contact = w - 5.0
        self.health_at = w - 5.0
        self.errors = {}

    def start_background(self, *a, **kw):
        pass

    def stop(self):
        pass

    def mark_image_undecodable(self, key, retry_in=None):
        pass


class _Present(object):
    __slots__ = ('frame', 'rel', 'wall', 'sched', 'flip', 'rects', 'names',
                 'recovering')

    def __init__(self, frame, rel, wall, sched, flip, rects, names,
                 recovering):
        self.frame, self.rel, self.wall, self.sched = frame, rel, wall, sched
        self.flip, self.rects, self.names = flip, rects, names
        self.recovering = recovering

    def __repr__(self):
        what = 'FLIP' if self.flip else (sorted(self.names)
                                         or [tuple(r) for r in self.rects])
        return '%.2fs %s' % (self.sched, what)


def _as_rects(arg):
    if arg is None:
        return None
    if isinstance(arg, pygame.Rect):
        return [pygame.Rect(arg)]
    if (isinstance(arg, (tuple, list)) and len(arg) == 4
            and all(isinstance(v, (int, float)) for v in arg)):
        return [pygame.Rect(arg)]
    return [pygame.Rect(r) for r in arg if r is not None]


def _png(size, rgb):
    s = pygame.Surface(size)
    s.fill(rgb)
    pygame.draw.circle(s, (255, 255, 255), (size[0] // 3, size[1] // 2),
                       size[1] // 4)
    buf = io.BytesIO()
    pygame.image.save(s, buf, 'x.png')
    return buf.getvalue()


_PNGS = {}


def _pngs():
    """The feeds' image bytes, made once per session."""
    if not _PNGS:
        for k in IMAGE_KEYS:
            _PNGS[k] = _png((720, 426) if k == 'muf-map' else (480, 480), GREEN)
        _PNGS['muf-map-new'] = _png((720, 426), RED)
    return _PNGS


def _diff(a, b, w, h):
    """Where two RGB byte strings of a w x h image differ."""
    stride = 3 * w
    ys = [y for y in range(h)
          if a[y * stride:(y + 1) * stride] != b[y * stride:(y + 1) * stride]]
    x0, x1, n = w, -1, 0
    for y in ys:
        ra, rb = a[y * stride:(y + 1) * stride], b[y * stride:(y + 1) * stride]
        for x in range(w):
            if ra[3 * x:3 * x + 3] != rb[3 * x:3 * x + 3]:
                n += 1
                x0, x1 = min(x0, x), max(x1, x)
    return {'pixels': n, 'box': (x0, ys[0], x1 + 1, ys[-1] + 1)} if ys else None


class _Run(object):
    """One headless run of the real render loop."""

    def __init__(self, size, wall0=WALL0, images=IMAGE_KEYS, stop=60.0,
                 emulate=False):
        self.size = size
        self.wall0 = wall0
        self.epoch = float(math.floor(wall0))     # what the loop anchors to
        self.images = tuple(images)
        self.stop = stop
        self.emulate = emulate
        self.clock = _Clock(wall0)
        self.hooks = []            # [rel, label, fn, done]
        self.events = []           # [rel, make_event, done]
        self.tick_override = None  # fn(rel, dt) -> dt, or None for normal
        self.fail_header_at = None
        self._ticks = 0
        self.quit_called = False
        self._in_overlay = False
        self._chrome_due = False
        # What happened.
        self.frames = []           # (rel, wall) at the top of every frame
        self.presents = []
        self.event_frames = []     # (frame, event)
        self.tabs = []             # (frame, active, regions) per draw_tabs
        self.titles = []           # (frame, title) per draw_panel
        self.muf_draws = []        # (frame, status, map rect)
        self.recoveries = []       # frame of each RECOVERING overlay
        self.box_frames = set()    # frames that drew a confirm box
        self.header_failed_frame = None
        self.mismatches = {'span': [], 'exact': []}
        self.mismatch_count = {'span': 0, 'exact': 0}
        self.compared = 0
        self.after_recovery = None
        self.phase = 'start'

    # -- scenario building
    def at(self, rel, label, fn):
        self.hooks.append([rel, label, fn, False])

    def press(self, rel, key):
        self.events.append([rel, lambda: pygame.event.Event(
            pygame.KEYDOWN, key=key, mod=0, unicode='', scancode=0), False])

    def click_tab(self, rel, name):
        def make():
            return pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                                      pos=self.tab_rect(name).center)
        self.events.append([rel, make, False])

    def tab_rect(self, name):
        """Where the loop puts tab `name`, computed the way the loop does."""
        inner = hp._panel_inner_rect(self.layout['propagation'])
        tab_h = max(20, self.fonts['panel'].get_height() + 6)
        bar = pygame.Rect(inner.x, inner.y, inner.w, tab_h)
        tw = bar.w // len(hp.PROP_TABS)
        i = hp.PROP_TABS.index(name)
        return pygame.Rect(bar.x + i * tw, bar.y, max(1, tw - 2), tab_h)

    def step_wall(self, secs):
        self.clock.offset += secs

    def replace_muf_map(self):
        d = self.data
        images = dict(d.images)
        images['muf-map'] = self.png['muf-map-new']
        stamps = dict(d.image_fetched_at)
        stamps['muf-map'] = self.clock.wall()
        d.images, d.image_fetched_at = images, stamps
        d.last_image_refresh = self.clock.wall()

    # -- running
    def run(self, tmp_dir):
        with pytest.MonkeyPatch.context() as mp:
            self._patch(mp, tmp_dir)
            # Only what the loop uses. pygame.init() also starts SDL audio,
            # whose teardown costs about a second per run.
            pygame.display.init()
            pygame.font.init()
            try:
                self.screen = pygame.display.set_mode(self.size)
                self.png = _pngs()
                self.layout = hp._get_layout(self.size)
                self.theme = dict(hp.THEMES['kstate'])
                if self.emulate:
                    self.displays = {}
                    for mode in ('span', 'exact'):
                        self.displays[mode] = self.screen.copy()
                        self.displays[mode].fill(MAGENTA)
                self.fonts = hp._make_fonts(self.size)
                hp._run_render_loop(self.screen, self.fonts, self.theme,
                                    dict(SETTINGS), injected_iter=self._frames())
            finally:
                _reset_caches()
                # Leave pygame as the loop's own pygame.quit() would have,
                # less the audio teardown: no display mode set.
                pygame.display.quit()
                pygame.display.init()
                pygame.font.init()
        return self

    def _patch(self, mp, tmp_dir):
        _reset_caches()
        mp.setattr(hp, 'time', _FakeTime(self.clock))
        mp.setattr(hp, 'HamClockData', self._make_data)
        mp.setattr(pygame.time, 'Clock', lambda *a, **k: _FrameClock(self))
        mp.setattr(hp, 'RUN_DIR', str(tmp_dir))

        def loop_quit():
            # The loop's last act. Recorded, not run (see run()).
            self.quit_called = True

        mp.setattr(pygame, 'quit', loop_quit)
        mp.setattr(hp, 'SETTINGS_PATH', str(tmp_dir / 'settings.json'))

        real_update, real_flip = pygame.display.update, pygame.display.flip

        def update(*args):
            out = real_update(*args)
            self._present(args[0] if args else None)
            return out

        def flip():
            out = real_flip()
            self._present(None)
            return out

        mp.setattr(pygame.display, 'update', update)
        mp.setattr(pygame.display, 'flip', flip)

        real_tabs = hp.draw_tabs

        def draw_tabs(screen, rect, tabs, active, fonts, theme):
            regions = real_tabs(screen, rect, tabs, active, fonts, theme)
            self.tabs.append((len(self.frames) - 1, active, dict(regions)))
            return regions

        mp.setattr(hp, 'draw_tabs', draw_tabs)

        real_panel = hp.draw_panel

        def draw_panel(screen, rect, title, fonts, theme):
            self.titles.append((len(self.frames) - 1, title))
            return real_panel(screen, rect, title, fonts, theme)

        mp.setattr(hp, 'draw_panel', draw_panel)

        real_muf = hp.draw_muf_text

        def draw_muf_text(*a, **kw):
            r = real_muf(*a, **kw)
            self.muf_draws.append((len(self.frames) - 1, kw.get('status'),
                                   None if r is None else pygame.Rect(r)))
            return r

        mp.setattr(hp, 'draw_muf_text', draw_muf_text)

        real_overlay = hp._render_recovering_overlay

        def recovering(screen, fonts, theme):
            self._in_overlay = True
            try:
                return real_overlay(screen, fonts, theme)
            finally:
                self._in_overlay = False
                self.recoveries.append(len(self.frames) - 1)
                self._chrome_due = True

        mp.setattr(hp, '_render_recovering_overlay', recovering)

        def box_spy(real):
            def box(*a, **kw):
                self.box_frames.add(len(self.frames) - 1)
                return real(*a, **kw)
            return box

        for fname in ('draw_report_overlay', 'draw_update_overlay'):
            mp.setattr(hp, fname, box_spy(getattr(hp, fname)))

        if self.fail_header_at is not None:
            real_header = hp.draw_header

            def draw_header(*a, **kw):
                if (self.header_failed_frame is None
                        and self.clock.rel >= self.fail_header_at):
                    self.header_failed_frame = len(self.frames) - 1
                    raise RuntimeError('injected: the display went away '
                                       'mid-frame')
                return real_header(*a, **kw)

            mp.setattr(hp, 'draw_header', draw_header)

    def _make_data(self):
        self.data = _Feeds(self)
        return self.data

    def _frames(self):
        while True:
            for hook in self.hooks:
                if not hook[3] and self.clock.rel >= hook[0]:
                    hook[3] = True
                    self.phase = hook[1]
                    hook[2]()
            self.frames.append((self.clock.rel, self.clock.wall()))
            events = []
            for ev in self.events:
                if not ev[2] and self.clock.rel >= ev[0]:
                    ev[2] = True
                    events.append(ev[1]())
                    self.event_frames.append((len(self.frames) - 1, events[-1]))
            if self.clock.rel >= self.stop:
                events.append(pygame.event.Event(pygame.QUIT))
            yield events

    def _tick(self):
        dt = FRAME_S + JITTER[self._ticks % len(JITTER)]
        self._ticks += 1
        if self.tick_override is not None:
            forced = self.tick_override(self.clock.rel, dt)
            if forced is not None:
                dt = forced
        self.clock.mono += dt

    def _present(self, rect_arg):
        rects = _as_rects(rect_arg)
        flip = rects is None
        p = _Present(len(self.frames) - 1, self.clock.rel, self.clock.wall(),
                     self.clock.wall() - self.epoch, flip, rects or [],
                     frozenset() if flip else self.panels_in(rects),
                     self._in_overlay)
        self.presents.append(p)
        if self.emulate:
            self._emulate(p)
        if self.emulate and self._chrome_due and not self._in_overlay:
            self._chrome_due = False
            self.after_recovery = (p, self._chrome_check())

    def panels_in(self, rects):
        names = set()
        for r in rects:
            for name, key in LAYOUT_KEY.items():
                if self.layout[key].contains(r):
                    names.add(name)
                    break
        return frozenset(names)

    def _emulate(self, p):
        scr, (w, h) = self.screen, self.size
        span, exact = self.displays['span'], self.displays['exact']
        if p.flip:
            span.blit(scr, (0, 0))
            exact.blit(scr, (0, 0))
        elif p.rects:
            for r in p.rects:
                r = r.clip(scr.get_rect())
                if r.w > 0 and r.h > 0:
                    exact.blit(scr, r.topleft, r)
            # SDL_GetSpanEnclosingRect: full width, top of the highest rect
            # to the bottom of the lowest, clipped to the window.
            top = max(0, min(r.top for r in p.rects))
            bottom = min(h, max(r.bottom for r in p.rects))
            if bottom > top:
                band = pygame.Rect(0, top, w, bottom - top)
                span.blit(scr, band.topleft, band)
        self.compared += 1
        back = _tobytes(scr, 'RGB')
        for mode in ('span', 'exact'):
            shown = _tobytes(self.displays[mode], 'RGB')
            if shown != back:
                self.mismatch_count[mode] += 1
                if len(self.mismatches[mode]) < 3:
                    self.mismatches[mode].append(
                        (repr(p), 'after: %s' % self.phase,
                         _diff(shown, back, w, h)))

    def _chrome_check(self):
        """Panels the displays still show as bare background. The RECOVERING
        overlay fills the whole screen with it; every panel's own chrome (a
        card, a title bar) sits just inside its rect's corner."""
        bg = tuple(self.theme['bg'])[:3]
        bad = []
        for mode in sorted(self.displays):
            for name in sorted(LAYOUT_KEY):
                r = self.layout[LAYOUT_KEY[name]]
                got = tuple(self.displays[mode].get_at((r.x + 2, r.y + 2)))[:3]
                if got == bg:
                    bad.append((mode, name))
        return bad

    # -- reading the record
    def frame_wall(self, f):
        return self.frames[f][1]

    def tab_changes(self):
        out, prev = [], None
        for f, active, _ in self.tabs:
            if prev is not None and active != prev:
                out.append((f, prev, active))
            prev = active
        return out

    def rel_of_wall(self, wall):
        """Seconds since this run started (what hooks and overrides use)."""
        return wall - self.wall0

    def sched(self, wall):
        """Seconds on the schedule's own clock, since its whole-second epoch:
        what messages print, so a slot reads as x.0, x.25, x.5 or x.75."""
        return wall - self.epoch

    def assert_ran(self, recoveries=0):
        assert self.quit_called, 'the loop never reached its own teardown'
        assert self.frames and self.frames[-1][0] >= self.stop, (
            'the loop stopped at %.1f s of a %.1f s run'
            % (self.frames[-1][0] if self.frames else 0.0, self.stop))
        assert len(self.recoveries) == recoveries, (
            'the loop hit %d render errors (expected %d), at %s'
            % (len(self.recoveries), recoveries,
               ['%.2fs' % self.frames[f][0] for f in self.recoveries]))


def _reset_caches():
    """Module caches keyed by things two runs share (fetch stamps, refresh
    ticks): clear them so one run can never paint another's pixels."""
    hp._scaled_cache.clear()
    hp._decode_failed_ts.clear()
    hp._open_bands_cache.update(ts=None, period=None)
    hp._solar_snapshot.update(ts=None, view={})
    hp._strfmt_cache['key'] = None


def _tab_slot(run, k):
    """Wall time of the k-th automatic tab advance after start (k >= 1):
    the first propagation grid slot TAB_CYCLE_S after start, then one
    TAB_CYCLE_S apart."""
    first = hp._next_tab_at(run.epoch, run.epoch, hp.TAB_CYCLE_S)
    return first + (k - 1) * hp.TAB_CYCLE_S


def _land_after(rel_slot, late):
    """tick_override: the frame that would cross `rel_slot` ends `late`
    seconds past it instead."""
    def override(rel, dt):
        if rel < rel_slot <= rel + dt:
            return rel_slot + late - rel
        return None
    return override


def _first_frame_at_or_after(run, f, slot):
    """Is frame f the first frame whose time is at or after `slot`?"""
    return f > 0 and run.frame_wall(f - 1) < slot <= run.frame_wall(f) + 1e-9


# ---------------------------------------------------------------- scenarios
#
# Each is one run of the loop, shared by the tests that read it.

@pytest.fixture(scope='module')
def steady(tmp_path_factory):
    """1440x900, every image up, two automatic tab advances. The frame that
    crosses the first advance's slot ends 0.17 s late, more than any normal
    frame: an advance re-armed from when it RAN rather than from its slot
    then misses the next slot by a whole frame, which is visible."""
    run = _Run(BIG)
    first = run.rel_of_wall(_tab_slot(run, 1))
    run.stop = run.rel_of_wall(_tab_slot(run, 2)) + 2.0
    run.tick_override = _land_after(first, 0.17)
    return run.run(tmp_path_factory.mktemp('steady'))


@pytest.fixture(scope='module')
def dark(tmp_path_factory):
    """1440x900 with NO image anywhere (a feed outage, or the first minutes
    after boot): sdo and propagation retry every 15 s at x.75. Started at an
    arbitrary fraction of a second, so the epoch has to be floored for the
    header to tick with the clock it shows."""
    run = _Run(BIG, wall0=WALL0 + 0.6, images=(), stop=100.0)
    return run.run(tmp_path_factory.mktemp('dark'))


@pytest.fixture(scope='module')
def clicked(tmp_path_factory):
    """1440x900; the operator clicks a tab six seconds before the cycle
    would have advanced on its own."""
    run = _Run(BIG)
    first = _tab_slot(run, 1)
    run.click_at = run.rel_of_wall(first) - 5.95
    run.click_tab(run.click_at, 'enlil')
    # The first slot at least a whole cycle after the click.
    run.next_auto = _tab_slot(run, 2)
    run.stop = run.rel_of_wall(run.next_auto) + 2.0
    return run.run(tmp_path_factory.mktemp('clicked'))


@pytest.fixture(scope='module')
def long_frames(tmp_path_factory):
    """1440x900 with frames that overrun:
      * the frame crossing 30.0 s ends at 30.33: the header (30.0) and OPEN
        BANDS (30.25) are both due in the next frame;
      * the frame crossing 48.25 s ends at 48.6: the status bar (48.5) and
        SOLAR (48.25) are both due;
      * from 52 s to 62 s every frame takes 0.55 s, so EVERY frame draws the
        header or the status bar; X-RAY (54.25) and BAND ACTIVITY (60.25)
        come due inside that stretch."""
    run = _Run(BIG, stop=64.0)
    once_a = _land_after(30.0, 0.33)
    once_b = _land_after(48.25, 0.35)

    def override(rel, dt):
        if 52.0 <= rel < 62.0:
            return 0.55
        forced = once_a(rel, dt)
        return forced if forced is not None else once_b(rel, dt)

    run.tick_override = override
    run.stretch = (52.0, 62.0)
    return run.run(tmp_path_factory.mktemp('long'))


@pytest.fixture(scope='module')
def stepped(tmp_path_factory):
    """720x450; NTP steps the wall clock back 9.6 s (the Oct 2026 report's
    step) four seconds after an automatic tab advance."""
    run = _Run(SMALL)
    first = run.rel_of_wall(_tab_slot(run, 1))
    run.step_at = first + 3.75
    run.at(run.step_at, 'wall clock stepped back 9.6 s',
           lambda: run.step_wall(-9.6))
    # In wall time the next advance is still the second slot; the run has
    # 9.6 s more to go to reach it.
    run.stop = run.rel_of_wall(_tab_slot(run, 2)) + 9.6 + 2.0
    return run.run(tmp_path_factory.mktemp('stepped'))


@pytest.fixture(scope='module')
def pixels(tmp_path_factory):
    """720x450 with both emulated displays checked after every present,
    through:
       0     first paint; OPEN BANDS (DAY)
      10     18:00 local, so OPEN BANDS goes to NIGHT at its 30.25 s slot
      40     a new MUF map arrives (new bytes, new fetch stamp): 84.25 s slot
      90     the server says that map is over an hour old: 144.25 s slot
     150.3   T opens the report box; N at 154.7 dismisses it
     165     one frame's header draw raises: RECOVERING, then recovery"""
    run = _Run(SMALL, wall0=WALL0_DUSK, stop=170.0, emulate=True)
    run.at(40.0, 'new MUF map', run.replace_muf_map)
    run.at(90.0, 'MUF map over an hour old',
           lambda: run.data.health.update(muf_age=4000))
    run.at(150.0, 'report box', lambda: None)
    run.press(150.3, pygame.K_t)
    run.press(154.7, pygame.K_n)
    run.at(160.0, 'report box dismissed', lambda: None)
    run.fail_header_at = 165.0
    run.at(165.0, 'render error', lambda: None)
    return run.run(tmp_path_factory.mktemp('pixels'))


STEADY_RUNS = ['steady', 'dark', 'clicked', 'pixels', 'stepped']


def _grids(run):
    """The grids each panel may legitimately draw on in this run, as
    (cadence, shift) pairs for hp._next_due; None = the panel's own."""
    own = [(None, 0.0)]
    grids = dict((n, own) for n in hp._CADENCE_S)
    if 'solar-image' not in run.images:
        grids['sdo'] = [(hp._CADENCE_S_NO_IMAGE['sdo'], hp._NO_IMAGE_SHIFT_S)]
    prop_keys = set(hp.PROP_TAB_IMAGE_KEY[t] for t in hp.PROP_TABS)
    if not prop_keys & set(run.images):
        # Retrying at x.75; tab advances still land on its own 60 s grid.
        grids['propagation'] = own + [(hp._CADENCE_S_NO_IMAGE['propagation'],
                                       hp._NO_IMAGE_SHIFT_S)]
    return grids


# ------------------------------------------------------------------ A, B, H

@pytest.mark.parametrize('name', STEADY_RUNS)
def test_the_header_and_status_bar_never_share_an_upload_with_a_slow_panel(
        request, name):
    """A. The upload SDL makes on the Pi spans every rect in a present, so a
    present holding the header (or the status bar) and a 60 s panel uploads
    everything between them: up to 95% of the screen to change a tenth."""
    run = request.getfixturevalue(name)
    run.assert_ran(recoveries=1 if name == 'pixels' else 0)
    bad = [p for p in run.presents if not p.flip
           and p.names & CHROME and p.names & SLOW]
    assert not bad, ('%s: presents that uploaded the header/status bar with '
                     'a 60 s panel: %s' % (name, bad[:6]))
    # The classifier must have understood every rect, or the check above
    # proves nothing. The only rects outside a panel are the report box's.
    unknown = [p for p in run.presents if not p.flip and not p.names
               and p.frame not in run.box_frames]
    assert not unknown, '%s: presents of unknown rects: %s' % (name, unknown[:4])


@pytest.mark.parametrize('name', STEADY_RUNS)
def test_no_two_slow_panels_share_an_upload(request, name):
    """B. The 60 s panels sit 6 s apart and an image panel retrying without
    an image sits at x.75 for exactly this: two in one present upload the
    band between them, which on the 15 s retry grid at x.25 meant sdo with
    DX SPOTS and propagation with BANDS, once a minute."""
    run = request.getfixturevalue(name)
    bad = [p for p in run.presents if not p.flip and len(p.names & SLOW) > 1]
    assert not bad, ('%s: presents that uploaded two 60 s panels at once: %s'
                     % (name, bad[:6]))


@pytest.mark.parametrize('name', STEADY_RUNS)
def test_the_whole_screen_is_uploaded_only_when_it_has_to_be(request, name):
    """A full flip uploads all 900 rows. It is right on first paint, after a
    tab click, when a confirm box closes, for the RECOVERING overlay and for
    the repaint after it -- and nowhere else (not on an automatic tab
    advance, not on a slow panel's slot)."""
    run = request.getfixturevalue(name)
    expect = {0}
    for f, ev in run.event_frames:
        if (ev.type == pygame.MOUSEBUTTONDOWN
                or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_n)):
            expect.add(f)
    for f in run.recoveries:
        expect |= {f, f + 1}
    flips = set(p.frame for p in run.presents if p.flip)
    assert flips == expect, '%s: full flips at %s, expected at %s' % (
        name, sorted('%.2fs' % run.frames[f][0] for f in flips),
        sorted('%.2fs' % run.frames[f][0] for f in expect))


def test_the_muf_panel_uploads_its_map_only_when_the_map_changed(
        steady, pixels):
    """The centre panel is 796 rows tall and its map changes every 900 s,
    while the numbers beneath it redraw every minute. With the map unchanged
    -- same surface, fetch, place and age label -- only the band beneath it
    is pushed; a new map, or a new age label over it, pushes the whole
    panel."""
    def muf_pushes(run):
        maps = dict((f, m) for f, _, m in run.muf_draws)
        out = []
        for p in run.presents:
            if p.flip or 'muf_text' not in p.names:
                continue
            r, = [r for r in p.rects if run.layout['muf'].contains(r)]
            m = maps[p.frame]
            assert m is not None and r.bottom == run.layout['muf'].bottom
            out.append((round(p.rel, 2), 'beneath' if r.top == m.bottom
                        else 'panel' if r == run.layout['muf'] else tuple(r)))
        return out

    seen = muf_pushes(steady)
    assert len(seen) >= 2 and all(w == 'beneath' for _, w in seen), seen
    # pixels: map unchanged at 24.25 s, new map by 84.25 s, label by 144.25 s.
    seen = muf_pushes(pixels)
    assert [w for _, w in seen] == ['beneath', 'panel', 'panel'], seen


@pytest.mark.parametrize('name', STEADY_RUNS)
def test_every_panel_draws_in_the_first_frame_of_its_own_slot(request, name):
    """H. In steady state each panel is drawn in the first frame at or after
    a slot on its own grid (header whole seconds, status x.5, 60 s panels
    k*6 + 0.25, image retries x.75), never a frame or more later.

    The grid keeps the slow panels out of the header's and status bar's
    frames, so the one-frame deferral should never have to fire here. A
    panel rescheduled as now + cadence drifts off its slot; a phase that
    lands on a whole second is deferred into the next frame; both show up as
    a draw that did not happen in its slot's first frame."""
    run = request.getfixturevalue(name)
    grids = _grids(run)
    skip = set()
    if name == 'stepped':
        # The step frame itself: the previous frame's wall time is later.
        skip = set(f for f in range(1, len(run.frames))
                   if run.frame_wall(f) < run.frame_wall(f - 1))
    late = []
    for p in run.presents:
        if p.flip or p.frame == 0 or p.frame in skip:
            continue
        t = run.frame_wall(p.frame)
        for panel in sorted(p.names):
            ok = False
            for cadence, shift in grids[panel]:
                c = hp._CADENCE_S[panel] if cadence is None else cadence
                slot = hp._next_due(panel, t - c, run.epoch, cadence, shift)
                ok = ok or _first_frame_at_or_after(run, p.frame, slot)
            if not ok:
                late.append('%s at %.3fs' % (panel, run.sched(t)))
    assert not late, ('%s: draws that were not in the first frame of one of '
                      'their slots: %s' % (name, late[:8]))
    # And each slow panel was in fact drawn on its slots (not just never).
    drawn = set()
    for p in run.presents:
        if not p.flip:
            drawn |= p.names
    assert SLOW <= drawn, '%s: never redrawn after first paint: %s' % (
        name, sorted(SLOW - drawn))


# ------------------------------------------------------------------- C, D

@pytest.mark.parametrize('name', ['steady', 'dark'])
def test_the_tab_cycle_advances_every_interval_on_the_propagation_grid(
        request, name):
    """C. The first automatic advance comes no sooner than TAB_CYCLE_S after
    start, and they come TAB_CYCLE_S apart (give or take a frame), each in
    the first frame of a propagation grid slot -- the frame the panel was
    due anyway, clear of the header and the status bar -- repainting that
    panel and nothing else. (A full flip per advance was measured at 314 ms
    against a 100 ms budget.) With no images the panel retries on its 15 s
    grid at x.75, but the advance itself still shows on its slot."""
    run = request.getfixturevalue(name)
    run.assert_ran()
    if hp.TAB_CYCLE_S <= 0:
        pytest.skip('the tab cycle is disabled')
    changes = run.tab_changes()
    last = run.frame_wall(len(run.frames) - 1)
    expect = [s for s in (_tab_slot(run, k) for k in range(1, 100)) if s <= last]
    got = [run.frame_wall(f) for f, _, _ in changes]
    assert len(changes) == len(expect), (
        'automatic tab advances at %s s; expected one in the first frame at '
        'or after each of %s s' % (
            ['%.2f' % run.sched(t) for t in got],
            ['%.2f' % run.sched(t) for t in expect]))
    for (f, _, _), slot in zip(changes, expect):
        assert _first_frame_at_or_after(run, f, slot), (
            'an automatic advance showed at %.3f s, not in the first frame at '
            'or after its slot %.2f s (frames at %.3f and %.3f s)' % (
                run.sched(run.frame_wall(f)), run.sched(slot),
                run.sched(run.frame_wall(f - 1)),
                run.sched(run.frame_wall(f))))
        shown = [p for p in run.presents if p.frame == f]
        assert (len(shown) == 1 and not shown[0].flip
                and shown[0].names == {'propagation'}), (
            'the advance at %.3f s presented %s, not just the propagation '
            'panel' % (run.sched(run.frame_wall(f)), shown))
    assert got[0] - run.wall0 >= hp.TAB_CYCLE_S
    if name != 'steady':
        return
    assert len(got) >= 2
    for a, b in zip(got, got[1:]):
        assert abs((b - a) - hp.TAB_CYCLE_S) <= 2 * MAX_FRAME_S, (a, b)
    # The overrun the scenario put on the first advance really happened.
    assert run.frame_wall(changes[0][0]) - expect[0] > MAX_FRAME_S


def test_a_tab_the_operator_picks_stays_up_for_a_whole_cycle(clicked):
    """D. Someone standing at the screen chose that map: the cycle must not
    snatch it away a few seconds later, and its next advance goes back on
    the propagation grid (the first slot at least TAB_CYCLE_S away)."""
    run = clicked
    run.assert_ran()
    if hp.TAB_CYCLE_S <= 0:
        pytest.skip('the tab cycle is disabled')
    (click_frame, ev), = run.event_frames
    t_click = run.frame_wall(click_frame)
    # The click landed on the tab it aimed at, as the loop drew it.
    regions = [r for f, _, r in run.tabs if f < click_frame][-1]
    assert regions.get('enlil') == run.tab_rect('enlil'), (regions, run.tab_rect('enlil'))
    changes = run.tab_changes()
    by_click = [c for c in changes if c[0] == click_frame]
    assert by_click and by_click[0][2] == 'enlil', changes
    autos = [c for c in changes if c[0] > click_frame]
    early = [c for c in autos
             if run.frame_wall(c[0]) < t_click + hp.TAB_CYCLE_S]
    assert not early, (
        'clicked at %.2f s; the cycle moved on at %s s' % (
            run.sched(t_click),
            ['%.2f' % run.sched(run.frame_wall(c[0])) for c in early]))
    assert autos, 'no automatic advance after the click by %.1f s' % run.stop
    f = autos[0][0]
    assert _first_frame_at_or_after(run, f, run.next_auto), (
        'clicked at %.2f s; the next automatic advance ran at %.3f s, not in '
        'the first frame at or after the slot %.2f s' % (
            run.sched(t_click), run.sched(run.frame_wall(f)),
            run.sched(run.next_auto)))


# ---------------------------------------------------------------------- E

@pytest.mark.parametrize('mode', ['span', 'exact'])
def test_the_display_always_shows_what_was_drawn(pixels, mode):
    """E. After every present the display must equal the back buffer, pixel
    for pixel: 'span' is the Pi (SDL uploads the full-width band), 'exact' an
    X11 desktop (only the rects given). A redraw that is not pushed leaves
    the old pixels on screen; on 'exact' indefinitely."""
    run = pixels
    run.assert_ran(recoveries=1)
    assert run.compared == len(run.presents) > 0
    assert run.mismatch_count[mode] == 0, (
        '%s display differed from the back buffer after %d of %d presents; '
        'first: %s' % (mode, run.mismatch_count[mode], run.compared,
                       run.mismatches[mode]))


def test_the_pixel_run_exercised_what_it_claims(pixels):
    """Guards E against passing on a run in which nothing happened."""
    run = pixels
    # OPEN BANDS said DAY at first paint and was redrawn saying NIGHT.
    obs = [(f, t) for f, t in run.titles if t.startswith('OPEN BANDS')]
    assert obs[0][0] == 0 and 'DAY' in obs[0][1], obs[:2]
    night = [f for f, t in obs if 'NIGHT' in t]
    assert night and 10.0 < run.frames[night[0]][0] < 40.0, obs[:4]
    # The new map was drawn, and a later present showed it on both displays
    # (the check runs at the end, after recovery repainted it as well).
    new_map = [d for d in run.muf_draws if run.frames[d[0]][0] > 40.0]
    assert new_map and new_map[0][2] is not None
    m = new_map[0][2]
    for mode in ('span', 'exact'):
        got = tuple(run.displays[mode].get_at((m.x + 3, m.y + 3)))[:3]
        # smoothscale blends a little: near the new map's red, not the old green.
        assert all(abs(a - b) <= 12 for a, b in zip(got, RED)), (mode, got)
    # The age label was drawn over the map once the server said it was old.
    labelled = [d for d in run.muf_draws
                if d[1] and 'old' in d[1] and run.frames[d[0]][0] > 90.0]
    assert labelled, [d[1] for d in run.muf_draws]
    # The report box was up (its own rect presented) and closed on a flip.
    box = [p for p in run.presents
           if not p.flip and not p.names and 150.0 < p.rel < 155.0]
    assert len(box) >= 10, box[:3]
    after = [p for p in run.presents if p.rel >= 154.7]
    assert after and after[0].flip, after[:2]
    assert run.header_failed_frame is not None


def test_a_render_error_is_followed_by_a_full_repaint(pixels):
    """E (recovery). The RECOVERING overlay fills the whole screen. Without a
    full repaint after it the panels came back one at a time on their own
    cadences -- for up to a minute -- and the MUF map, whose rows are pushed
    only when it changes, stayed blank indefinitely where SDL uploads exact
    rects. The very next present must show every panel's chrome again."""
    run = pixels
    assert run.recoveries and run.recoveries[0] == run.header_failed_frame
    assert run.after_recovery is not None, 'nothing was presented after it'
    p, bad = run.after_recovery
    assert not bad, ('first present after RECOVERING (%r) left these on the '
                     'background colour: %s' % (p, bad[:8]))


# ---------------------------------------------------------------------- F

def test_a_long_frame_does_not_drag_a_slow_panel_into_the_header_upload(
        long_frames):
    """F. A frame that overruns into the next second owes the header AND any
    slow panel whose slot it also jumped. The panel waits one frame, so the
    header's upload is not stretched down to the panel's bottom edge (and
    likewise for the status bar)."""
    run = long_frames
    run.assert_ran()
    lo, hi = run.stretch
    # The overruns happened as designed.
    for at, late, what in ((30.0, 0.33, 'header'), (48.25, 0.35, 'status')):
        f = [i for i, (rel, _) in enumerate(run.frames)
             if abs(rel - (at + late)) < 1e-6]
        assert f, 'no frame ended at %.2f s' % (at + late)
        drew = [p for p in run.presents if p.frame == f[0]]
        assert drew and what in drew[0].names, (what, drew)
    bad = [p for p in run.presents if not p.flip and p.rel < lo
           and p.names & CHROME and p.names & SLOW]
    assert not bad, ('presents that uploaded the header/status bar with a '
                     '60 s panel after an overrun: %s' % bad)


def test_frames_that_keep_overrunning_do_not_starve_the_slow_panels(
        long_frames):
    """F (starvation). When every frame draws the header or the status bar,
    deferring a slow panel 'until a frame without them' would mean never.
    One that is already half a second late draws regardless."""
    run = long_frames
    lo, hi = run.stretch
    starved = []
    checked = 0
    for name in sorted(SLOW):
        slot = hp._next_due(name, run.wall0 + lo, run.epoch)
        if run.rel_of_wall(slot) >= hi - 1.5:
            continue
        checked += 1
        drew = [p.wall for p in run.presents
                if name in p.names and p.wall >= slot]
        if not drew or drew[0] - slot > 1.2:
            starved.append('%s due %.2fs, drawn %s' % (
                name, run.sched(slot),
                '%.2fs' % run.sched(drew[0]) if drew else 'never'))
    assert checked >= 2
    assert not starved, starved


def test_a_long_frame_at_a_tab_advance_still_waits_for_the_header(tmp_path):
    """F, at the one slot the F test above does not use: the frame crossing
    96.0 s (header due) ends at 96.33 s, past the first advance's 96.25 s.

    Found by this harness: the advance marked the panel due at 0.0, which
    _panel_due read as long overdue, so the deferral never applied and that
    frame uploaded rows 0-858. It now marks the panel due at the slot."""
    run = _Run(BIG)
    slot = run.rel_of_wall(_tab_slot(run, 1))
    run.stop = slot + 3.0
    run.tick_override = _land_after(math.floor(slot),
                                    slot - math.floor(slot) + 0.08)
    run.run(tmp_path)
    run.assert_ran()
    assert run.tab_changes(), 'the tab never advanced'
    bad = [p for p in run.presents if not p.flip
           and p.names & CHROME and p.names & SLOW]
    assert not bad, bad


# ---------------------------------------------------------------------- G

def test_a_backward_clock_step_does_not_freeze_the_clock(stepped):
    """G. An NTP step backwards put every due time that far in the future,
    and the header -- the clock -- froze for the length of the step. It
    must keep being presented about once a second, and the tab cycle keeps
    its place on the grid.

    Measured on the monotonic clock. Across the step itself the gap can
    reach two seconds: the header is re-planned to the next whole second of
    the NEW time (the H test checks it lands there), which can be up to a
    second after a draw that was itself up to a second before the step."""
    run = stepped
    run.assert_ran()
    header = [run.frames[p.frame][0] for p in run.presents
              if p.flip or 'header' in p.names]
    gaps = [(round(a, 2), round(b - a, 2)) for a, b in zip(header, header[1:])
            if b - a > 1.0 + 2 * MAX_FRAME_S
            and not (a < run.step_at <= b and b - a <= 2.0 + 2 * MAX_FRAME_S)]
    assert not gaps, ('the header went unpresented for (from, seconds): %s; '
                      'the wall clock stepped back at %.2f s'
                      % (gaps, run.step_at))
    if hp.TAB_CYCLE_S <= 0:
        return
    changes = run.tab_changes()
    after = [c for c in changes if run.frames[c[0]][0] > run.step_at]
    assert after, 'no tab advance after the step'
    f = after[0][0]
    assert _first_frame_at_or_after(run, f, _tab_slot(run, 2)), (
        'after the step the tab advanced at wall %.3f s, off the grid slot '
        '%.2f s' % (run.sched(run.frame_wall(f)),
                    run.sched(_tab_slot(run, 2))))
