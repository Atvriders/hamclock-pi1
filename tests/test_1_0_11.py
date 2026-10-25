"""1.0.11: what the 2026-10-04 report showed once someone read it closely.

That report came back clean on its face, and nearly everything in it was
misleading: the screen was drawn in a hairline face nobody had measured, the
DX column had been blank since April, OPEN BANDS showed the daytime forecast
at night, the status bar called a dead feed fresh, and the frames that blew
the budget were blamed on the wrong thing. Each test here names what the
operator saw before the fix.

Rendering tests run in the Pi's own face (tests/conftest.py pins
HAMCLOCK_FONT to FreeMonoBold) at the Pi's 1440x900.
"""
import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp

SIZE = (1440, 900)

SPOTS = [
    {'frequency': '14025.6', 'band': '20m', 'dx': 'WG3J', 'spotter': 'N1AU',
     'time': '1913 2026-10-21'},
    {'frequency': '14330.0', 'band': '20m', 'dx': 'PJ2/K8PGJ',
     'spotter': 'M9UDX', 'time': '1912 2026-10-21'},
] * 8


@pytest.fixture
def env():
    pygame.init()
    fonts = hp._make_fonts(SIZE)
    return fonts, hp.THEMES['kstate'], hp._get_layout(SIZE)


def _spy_blit_fit(monkeypatch):
    seen = []
    real = hp._blit_fit

    def spy(screen, font, text, color, x, y, max_w, cache=True):
        seen.append((font, str(text), hp._fit_text(font, str(text), max_w),
                     tuple(color)))
        return real(screen, font, text, color, x, y, max_w, cache=cache)

    monkeypatch.setattr(hp, '_blit_fit', spy)
    return seen


# --------------------------------------------------------------- the face

def test_an_unpinned_display_asks_for_the_bold_face(monkeypatch):
    """Before: SysFont('monospace') with no weight, which on the Pi is
    FreeMono — stems 0.7-1.2 px wide, faint grey hairlines from across a
    room. Bold resolves to FreeMonoBold from the same package."""
    asked = []
    real = pygame.font.SysFont

    def spy(name, size, bold=False, italic=False):
        asked.append(bold)
        return real(name, size, bold, italic)

    monkeypatch.delenv('HAMCLOCK_FONT', raising=False)
    monkeypatch.setattr(pygame.font, 'SysFont', spy)
    hp._make_fonts(SIZE)
    assert asked and all(asked), 'a face was requested without bold'


def test_a_pinned_face_is_the_one_drawn(env):
    fonts, _, _ = env
    path = os.environ['HAMCLOCK_FONT']
    size = int(round(11 * hp._font_scale(SIZE)))
    assert fonts['panel'].get_height() == pygame.font.Font(path, size).get_height()


# --------------------------------------------------------- the extra row

def test_rendered_text_is_trimmed_to_the_face_height_without_losing_ink():
    """Font.render can return a row more than get_height(); FreeMonoBold at
    8 px does. Every layout sum uses get_height(), so that row landed one
    pixel outside the box it was measured for."""
    f = pygame.font.Font(os.environ['HAMCLOCK_FONT'], 8)
    raw = f.render('MUF map 5h old', True, (255, 255, 255))
    assert raw.get_height() > f.get_height(), 'precondition: this face overruns'
    trimmed = hp._trim_to_line(raw, f.get_height(), hp._face_top_trim(f))
    assert trimmed.get_height() == f.get_height()

    def ink(s):
        return sum(1 for x in range(s.get_width()) for y in range(s.get_height())
                   if s.get_at((x, y))[3] > 0)
    assert ink(trimmed) == ink(raw), 'the trimmed row carried ink'


@pytest.mark.parametrize('size', [8, 9, 16, 18, 22, 30])
def test_trimming_keeps_neighbouring_strings_on_one_baseline(size):
    """Cropping only the strings that overran moved them a pixel above their
    neighbours: 'Kp' sat higher than its value, '15 spots' higher than the
    rest of the status bar. The 'A' of an overrunning string and the 'A' of
    a plain one must land on exactly the same rows."""
    f = pygame.font.Font(os.environ['HAMCLOCK_FONT'], size)
    hp._face_trim.clear()
    rows = []
    for text in ('Agjpq|', 'ABCDEF'):
        scr = pygame.Surface((300, 80))
        scr.fill((0, 0, 0))
        hp._glyph_cache.clear()
        hp._blit_text(scr, f, text, (255, 255, 255), 0, 10)
        w = f.size('A')[0]
        rows.append([y for y in range(80)
                     if any(scr.get_at((x, y))[:3] != (0, 0, 0) for x in range(w))])
    assert rows[0] == rows[1], 'the A moved %r -> %r' % (rows[1][:2], rows[0][:2])


@pytest.mark.parametrize('size', range(6, 37))
def test_text_never_paints_below_its_face_height(size):
    f = pygame.font.Font(os.environ['HAMCLOCK_FONT'], size)
    scr = pygame.Surface((400, 120))
    scr.fill((0, 0, 0))
    hp._glyph_cache.clear()
    hp._blit_text(scr, f, 'Ag|_ MUF 5h', (255, 255, 255), 2, 10)
    below = [(x, y) for x in range(400) for y in range(10 + f.get_height(), 120)
             if scr.get_at((x, y))[:3] != (0, 0, 0)]
    assert not below, 'size %d painted %d px below its face' % (size, len(below))


# ------------------------------------------------------------ the cache

def test_per_second_strings_no_longer_evict_the_panels(env, monkeypatch):
    """Before: the clock and status strings went into the 256-entry LRU at
    about 180 a minute, evicting every panel's strings before that panel's
    next once-a-minute redraw, so each redraw re-rendered all of its text."""
    fonts, theme, L = env
    hp._glyph_cache.clear()
    hp._volatile_glyphs.clear()
    scr = pygame.Surface(SIZE)
    inner = hp._panel_inner_rect(L['dx_spots'])
    hp.draw_dx_spots(scr, inner, SPOTS, fonts, theme)
    panel_keys = set(hp._glyph_cache)
    assert panel_keys

    clock = [1_800_000_000.0]
    real_gmtime, real_localtime = time.gmtime, time.localtime
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    monkeypatch.setattr(time, 'gmtime',
                        lambda *a: real_gmtime(a[0] if a else clock[0]))
    monkeypatch.setattr(time, 'localtime',
                        lambda *a: real_localtime(a[0] if a else clock[0]))

    class D:
        solar, bands, dxspots = {}, {}, SPOTS
        last_data_refresh = clock[0]
        last_image_refresh = clock[0]
        health = {'solar_age': 10, 'bands_age': 10, 'dx_age': 10}

    for _ in range(400):            # nearly seven minutes of seconds
        clock[0] += 1.0
        hp.draw_header(scr, L['header'], 'K0XYZ', fonts, theme, data=D)
        hp.draw_status_bar(scr, L['status'], D, fonts, theme)
    missing = panel_keys - set(hp._glyph_cache)
    assert not missing, '%d panel strings were evicted' % len(missing)
    assert len(hp._volatile_glyphs) <= hp._VOLATILE_CAP


# ------------------------------------------------------------ status bar

class _Feeds:
    last_data_refresh = 1000.0
    health = {'solar_age': 100, 'bands_age': 100, 'dx_age': 500}
    dxspots = [{}] * 15


def test_status_shows_each_feeds_real_age_and_flags_a_stale_one(env):
    """Before: 'Data:42s ... Solar:OK' — the time since the client polled
    its own server, which stayed fresh while an upstream feed was down."""
    _, theme, _ = env
    segs = hp._status_segments(_Feeds, theme, now=1020.0)
    assert [t for t, _ in segs] == ['Solar 2m', 'Bands 2m', 'DX 8m', '15 spots']
    colour = dict(segs)
    assert colour['DX 8m'] == theme['poor'], 'three missed DX fetches is an outage'
    assert colour['Solar 2m'] == theme['label']


def test_status_says_so_when_the_server_itself_goes_quiet(env):
    _, theme, _ = env
    segs = hp._status_segments(_Feeds, theme, now=1000.0 + 400)
    assert segs[0] == ('server silent 6m', theme['poor'])


def test_status_without_health_reports_the_poll_age_as_what_it_is(env):
    _, theme, _ = env

    class Old:
        last_data_refresh = 1000.0
        dxspots = []
    segs = hp._status_segments(Old, theme, now=1020.0)
    assert segs[0][0] == 'polled 20s ago'


def test_a_feed_not_fetched_yet_is_not_an_alarm(env):
    """-1 is every feed for the first seconds after boot."""
    _, theme, _ = env

    class Booting:
        last_data_refresh = 1000.0
        health = {'solar_age': -1, 'bands_age': -1, 'dx_age': -1}
        dxspots = []
    segs = hp._status_segments(Booting, theme, now=1001.0)
    assert segs[0] == ('Solar --', theme['label'])


def test_status_survives_junk_off_the_wire(env):
    _, theme, _ = env

    class Junk:
        last_data_refresh = 'yesterday'
        health = {'solar_age': 'x', 'bands_age': True, 'dx_age': float('nan')}
        dxspots = None
    assert isinstance(hp._status_segments(Junk, theme, now=1.0), list)


# -------------------------------------------------------------- DX panel

def test_dx_panel_uses_the_room_it_has(env, monkeypatch):
    """Before: a fixed five rows, sized for 720x450."""
    fonts, theme, L = env
    seen = _spy_blit_fit(monkeypatch)
    hp.draw_dx_spots(pygame.Surface(SIZE), hp._panel_inner_rect(L['dx_spots']),
                     SPOTS, fonts, theme)
    rows = sum(1 for _, text, _, _ in seen if text in ('WG3J', 'PJ2/K8PGJ'))
    assert rows >= 8, rows


def test_dx_and_spotter_are_never_cut_silently(env, monkeypatch):
    """Before: str(spotter)[:10] dropped characters with no marker."""
    fonts, theme, L = env
    seen = _spy_blit_fit(monkeypatch)
    long_spot = [dict(SPOTS[0], spotter='VE3ABCDEFGHI-#')]
    hp.draw_dx_spots(pygame.Surface(SIZE), hp._panel_inner_rect(L['dx_spots']),
                     long_spot, fonts, theme)
    drawn = [fitted for _, text, fitted, _ in seen if text.startswith('VE3')]
    assert drawn and (drawn[0] == 'VE3ABCDEFGHI-#' or drawn[0].endswith('…')), drawn


# ------------------------------------------------------------ OPEN BANDS

@pytest.mark.parametrize('hour,want', [(0, 'night'), (5, 'night'), (6, 'day'),
                                       (12, 'day'), (17, 'day'), (18, 'night')])
def test_band_period_follows_the_local_clock(hour, want):
    t = time.mktime((2026, 10, 21, hour, 30, 0, 0, 0, -1))
    assert hp._band_period(t) == want


BANDS = {'80m-40m': {'day': 'Poor', 'night': 'Fair'},
         '30m-20m': {'day': 'Good', 'night': 'Good'},
         '17m-15m': {'day': 'Good', 'night': 'Fair'},
         '12m-10m': {'day': 'Fair', 'night': 'Poor'}}


def test_night_uses_the_night_forecast():
    """Before: the 'day' column at every hour. With this (live, 2026-10-21)
    forecast a night operator was told 80m-40m was closed and 12m-10m open,
    the reverse of the night column."""
    hp._open_bands_cache['ts'] = None
    o, c = hp._open_bands_strings(BANDS, 1.0, 'night')
    assert '80m-40m' in o and '12m-10m' in c
    o, c = hp._open_bands_strings(BANDS, 1.0, 'day')
    assert '12m-10m' in o and '80m-40m' in c


def test_three_open_groups_wrap_instead_of_losing_the_third(env, monkeypatch):
    """Before: 'OPEN: 30m-20m, 17m-15m, 12…' — 310 px into a 272 px rect."""
    fonts, theme, L = env
    hp._open_bands_cache['ts'] = None
    seen = _spy_blit_fit(monkeypatch)
    hp.draw_open_bands(pygame.Surface(SIZE),
                       hp._panel_inner_rect(L['open_bands']), BANDS, fonts,
                       theme, data_refresh_ts=2.0, period='day')
    drawn = ' '.join(fitted for _, _, fitted, _ in seen)
    assert '…' not in drawn, drawn
    for band in ('30m-20m', '17m-15m', '12m-10m', '80m-40m'):
        assert band in drawn, band


# ---------------------------------------------------------------- header

def test_the_clock_is_drawn_in_the_large_face(env, monkeypatch):
    """Before: 'body' — 10 px capitals, smaller than the app's own name."""
    fonts, theme, L = env
    seen = _spy_blit_fit(monkeypatch)
    hp.draw_header(pygame.Surface(SIZE), L['header'], 'K0XYZ', fonts, theme)
    faces = {text[:3]: font for font, text, _, _ in seen}
    assert faces['UTC'] is fonts['title'] and faces['LOC'] is fonts['title']


def test_header_text_is_centred_in_its_bar(env):
    """Before: drawn at fixed offsets from the top, with the bottom 32 rows
    of the 60 px bar empty."""
    fonts, theme, L = env
    scr = pygame.Surface(SIZE)
    hp.draw_header(scr, L['header'], 'K0XYZ', fonts, theme)
    r = L['header']
    card = tuple(theme['card'])[:3]
    col_x = r.x + int(r.w * 0.53)                    # the UTC column
    rows = [y for y in range(r.y + 2, r.bottom - 2)
            if any(scr.get_at((x, y))[:3] != card
                   for x in range(col_x, col_x + 200))]
    top_gap, bottom_gap = rows[0] - r.y, r.bottom - 1 - rows[-1]
    assert abs(top_gap - bottom_gap) <= 6, (top_gap, bottom_gap)


# -------------------------------------------------------------- schedule

def test_no_slow_panel_shares_a_second_with_the_header_or_status():
    for name, c in hp._CADENCE_S.items():
        if c <= 1.0:
            continue
        frac = hp._PANEL_PHASE[name] % 1.0
        assert abs(frac) > 0.1 and abs(frac - 0.5) > 0.1, (name, frac)


def test_a_simulated_two_minutes_never_draws_a_slow_panel_with_the_header():
    """Before: every 60 s panel was phased on a whole second, so it was drawn
    in the header's frame and SDL uploaded the band from row 0 to its bottom
    edge — 95% of the screen for a panel at the bottom."""
    epoch = 1000.0
    # The first paint draws every panel by design; schedule from there.
    t = epoch + 0.05
    due = {n: hp._next_due(n, t, epoch) for n in hp._CADENCE_S}
    for _ in range(int(120 / 0.1)):
        t += 0.1
        drew = [n for n, d in due.items() if d <= t]
        for n in drew:
            due[n] = hp._next_due(n, t, epoch)
        slow = [n for n in drew if hp._CADENCE_S[n] > 1.0]
        assert not ('header' in drew and slow), (round(t - epoch, 2), drew)


def test_image_panels_keep_their_place_on_the_faster_grid():
    epoch = 1000.0
    for now in (1000.3, 1042.0, 1700.9):
        due = hp._next_due('sdo', now, epoch, 15.0)
        assert now < due <= now + 15.0, 'a 15 s retry cadence came back at %.1f s' % (due - now)
        assert abs(((due - epoch) % 1.0) - 0.25) < 1e-6


def test_the_tab_cycle_lands_on_the_propagation_grid():
    epoch = 1000.0
    ph = hp._PANEL_PHASE['propagation']
    first = hp._next_tab_at(epoch, epoch, hp.TAB_CYCLE_S)
    assert first - epoch >= hp.TAB_CYCLE_S - 1e-6
    assert abs(((first - epoch - ph) % 60.0)) < 1e-6
    # An automatic advance runs a frame or so after its slot; the next one
    # must still be exactly one interval after the slot, not drift late.
    nxt = hp._next_tab_at(first + 0.07, epoch,
                          hp.TAB_CYCLE_S - hp._CADENCE_S['propagation'] / 2.0)
    assert abs(nxt - first - hp.TAB_CYCLE_S) < 1e-6


def test_dirty_span_is_the_band_sdl_uploads():
    header = pygame.Rect(0, 0, 1440, 60)
    low = pygame.Rect(2, 733, 284, 125)
    assert hp._dirty_span([header, low]) == 858
    assert hp._dirty_span([low]) == 125
    assert hp._dirty_span([]) == 0


# ------------------------------------------------------------ MUF panel

def test_muf_panel_returns_its_map_rect_and_labels_an_old_map(env, monkeypatch):
    """Before: no age label. The server can serve the last good map for up to
    24 hours, and the centre panel said nothing."""
    fonts, theme, L = env
    seen = _spy_blit_fit(monkeypatch)
    inner = hp._panel_inner_rect(L['muf'])
    surf = pygame.Surface((720, 426))
    surf.fill((90, 90, 90))
    r = hp.draw_muf_text(pygame.Surface(SIZE), inner, {'sfi': '93'}, fonts,
                         theme, surf=surf, image_key='muf-map', fetched_at=1.0,
                         status='MUF map 5h old')
    assert isinstance(r, pygame.Rect) and r.top == inner.top and r.h > 100
    assert any(text == 'MUF map 5h old' for _, text, _, _ in seen)


def test_muf_panel_without_a_map_returns_none(env):
    fonts, theme, L = env
    assert hp.draw_muf_text(pygame.Surface(SIZE), hp._panel_inner_rect(L['muf']),
                            {}, fonts, theme) is None


# ------------------------------------------------------------ the report

def test_present_and_slow_frames_are_recorded():
    hp._slow_frames.clear()
    hp._record_present(12.0, 312, False)
    hp._record_present(80.0, 858, True)
    s = hp._present_summary()
    assert s['n'] >= 2 and s['span_max'] >= 858 and s['flips_total'] >= 1
    hp._note_slow_frame(150.0, 120.0, 858, {'header', 'open_bands'}, False)
    hp._note_slow_frame(20.0, 1.0, 60, {'header'}, False)      # not slow
    out = hp._slow_frames_summary()
    assert len(out) == 1 and out[0]['drew'] == 'header+open_bands'
    assert out[0]['span'] == 858 and out[0]['present_ms'] == 120.0


def test_a_backwards_clock_step_is_reported(monkeypatch):
    real = time.time
    monkeypatch.setattr(time, 'time', lambda: real() - 9.6)
    assert hp._clock_step_s() == pytest.approx(-9.6, abs=0.3)


def test_a_report_with_every_new_field_full_still_fits_the_receiver(env, tmp_path):
    """The receiver refuses a report whose fields, screenshot aside, exceed
    16 KB. Fill every new list to its cap and add a realistic server blob."""
    import json
    fonts, theme, _ = env
    hp._slow_frames.clear()
    for i in range(hp._slow_frames.maxlen):
        hp._note_slow_frame(100.0 + i, 90.0, 858, set(hp._CADENCE_S), True)

    class D:
        server_url = None
        solar, bands, dxspots, images = {}, {}, [], {}
        last_data_refresh = last_image_refresh = 0.0
    # settings_path: without it a device id is minted into the real
    # /etc/hamclock-lite/settings.json, which a root test run would write.
    p = hp._collect_telemetry(pygame.Surface(SIZE), D(), fonts, settings={},
                              settings_path=str(tmp_path / 'settings.json'),
                              screenshot=False, defer_server=True)
    p['server'] = {'k%03d' % i: 'x' * 20 for i in range(120)}    # ~4 KB
    sections = {k: p[k] for k in ('app', 'host', 'display', 'versions',
                                  'perf', 'server')}
    size = len(json.dumps(sections, separators=(',', ':')))
    assert size < 16 * 1024, size
    assert len(p['perf']['slow_frames']) <= 8


# ----------------------------------------------- the last two false readings

_xray_ts = [5_000_000.0]

@pytest.mark.parametrize('cached', [False, True], ids=['uncached', 'cached'])
@pytest.mark.parametrize('raw', [None, 'N/A', '--', 'Xnan'])
def test_a_missing_xray_reading_is_not_the_quietest_flux(env, monkeypatch,
                                                         cached, raw):
    """Before: a missing class defaulted to 'A0.0' — the quietest possible
    reading — drawn in the 'good' colour."""
    fonts, theme, L = env
    got = {}
    monkeypatch.setattr(hp, '_draw_value_and_bar',
                        lambda s, r, text, v, vmax, colour, f, t:
                        got.update(text=text, v=v, colour=tuple(colour)))
    solar = {} if raw is None else {'xray': raw}
    # _solar_view caches on this stamp: give every case its own.
    _xray_ts[0] += 1.0
    ts = _xray_ts[0] if cached else None
    hp.draw_xray(pygame.Surface(SIZE), L['xray'], solar, fonts, theme,
                 data_refresh_ts=ts)
    assert 'A0.0' not in got['text']
    assert got['v'] == 0.0 and got['colour'] == tuple(theme['label'])


def test_a_real_xray_class_keeps_its_verdict(env, monkeypatch):
    fonts, theme, L = env
    got = {}
    monkeypatch.setattr(hp, '_draw_value_and_bar',
                        lambda s, r, text, v, vmax, colour, f, t:
                        got.update(text=text, colour=tuple(colour)))
    hp.draw_xray(pygame.Surface(SIZE), L['xray'], {'xray': 'M2.1'}, fonts, theme)
    assert got['text'] == 'M2.1' and got['colour'] == tuple(theme['poor'])


def test_a_tiny_panel_keeps_the_whole_open_list(monkeypatch):
    """At 720x450 the OPEN BANDS box is 27 px tall: two lines. With three
    open groups the wrapped list needs two and CLOSED a third, so the
    fallback cut the third open band ('OPEN: 30m-20m, 17m-15m, …'). The OPEN
    list is what an operator acts on; CLOSED is its complement."""
    size = (720, 450)
    fonts, theme = hp._make_fonts(size), hp.THEMES['kstate']
    hp._open_bands_cache['ts'] = None
    seen = _spy_blit_fit(monkeypatch)
    hp.draw_open_bands(pygame.Surface(size),
                       hp._panel_inner_rect(hp._get_layout(size)['open_bands']),
                       BANDS, fonts, theme, data_refresh_ts=3.0, period='day')
    drawn = ' '.join(fitted for _, _, fitted, _ in seen)
    for band in ('30m-20m', '17m-15m', '12m-10m'):
        assert band in drawn, (band, drawn)


# ------------------------------------- the status bar against the real data layer

class _Resp:
    """Just enough of an HTTP response for HamClockData._fetch_json."""
    def __init__(self, body):
        self._body = body
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _server(monkeypatch, up, health_ages):
    """Point the REAL HamClockData at a stub server: answering or refusing."""
    import json as _json
    import urllib.error
    import hamclock_data

    def urlopen(req, timeout=None):
        if not up[0]:
            raise urllib.error.URLError('[Errno 111] Connection refused')
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        if url.endswith('/api/health'):
            return _Resp(_json.dumps(dict(health_ages[0], status='ok')).encode())
        if url.endswith('/api/dxspots'):
            return _Resp(b'[]')
        return _Resp(b'{}')

    monkeypatch.setattr(hamclock_data, 'urlopen', urlopen)
    return hamclock_data.HamClockData('http://127.0.0.1:9')


def test_the_bar_notices_when_the_pis_own_server_goes_down(env, monkeypatch):
    """The reviewer's repro, through the real data layer. Before: the poll
    stamp moved on every attempt, failed or not, so with the server down for
    30 minutes the bar still read 'Solar 2m  Bands 2m  DX 1m' in grey."""
    _, theme, _ = env
    clock = [2_000_000.0]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    up = [True]
    d = _server(monkeypatch, up, [{'solar_age': 120, 'bands_age': 120, 'dx_age': 60}])
    d.refresh_data()                       # the server answers once
    up[0] = False
    for _ in range(30):                    # then thirty refused polls
        clock[0] += 60.0
        d.refresh_data()
    segs = hp._status_segments(d, theme, now=clock[0])
    assert segs[0] == ('server silent 30m', theme['poor']), segs
    ages = dict(segs)
    assert ages.get('Solar 32m') == theme['poor'], segs   # 120 s + 30 min, still growing


def test_a_server_that_never_answers_is_called_unreachable(env, monkeypatch):
    """Before: 'polled 45s ago', though every poll had failed."""
    _, theme, _ = env
    clock = [2_000_000.0]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    d = _server(monkeypatch, [False], [{}])
    d.refresh_data()
    monkeypatch.setattr(hp, '_IMPORT_MONO', hp._mono() - 600.0)
    assert hp._status_segments(d, theme, now=clock[0] + 45)[0] == \
        ('server unreachable', theme['poor'])


def test_a_server_still_starting_at_boot_is_not_an_alarm(env, monkeypatch):
    """Client and server start together; the first poll can beat the server
    to its port, and the next poll is a minute away."""
    _, theme, _ = env
    clock = [2_000_000.0]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    d = _server(monkeypatch, [False], [{}])
    d.refresh_data()
    monkeypatch.setattr(hp, '_IMPORT_MONO', hp._mono() - 5.0)
    assert hp._status_segments(d, theme, now=clock[0] + 5)[0] == \
        ('waiting for server', theme['label'])


def test_a_304_counts_as_the_server_answering(env, monkeypatch):
    import urllib.error
    import hamclock_data
    _, theme, _ = env
    clock = [2_000_000.0]
    monkeypatch.setattr(time, 'time', lambda: clock[0])

    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 304, 'Not Modified', {}, None)

    monkeypatch.setattr(hamclock_data, 'urlopen', urlopen)
    d = hamclock_data.HamClockData('http://127.0.0.1:9')
    d.refresh_data()
    assert d.last_contact == clock[0]


def test_a_bad_font_pin_falls_back_to_the_pis_own_face(monkeypatch):
    """Before: a pinned file that would not load fell through to pygame's
    built-in default font, not the bold system face the Pi uses."""
    asked = []
    real = pygame.font.SysFont

    def spy(name, size, bold=False, italic=False):
        asked.append(bold)
        return real(name, size, bold, italic)

    monkeypatch.setenv('HAMCLOCK_FONT', '/nonexistent/NoSuchFace.ttf')
    monkeypatch.setattr(pygame.font, 'SysFont', spy)
    hp._make_fonts(SIZE)
    assert asked and all(asked)
