"""The panels draw what the server actually sends, not what a test typed.

Two readings shipped that the Pi never displayed, and both were found by
accident, in screenshots:

  * SOLAR's Bz row read solar['bz']. The server has only ever sent
    'magneticField', so every Bz the Pi drew was '--' (fixed in 1.0.10).
  * DX SPOTS read spot['dxCall']. The server sends the call as 'dx', so the
    DX column -- the call the operator is looking for -- showed '--' on every
    row from April until 1.0.11.

Both survived a green suite because each side tested against a payload it
had invented: the server's tests checked the server's dict, the client's fed
the panels a dict someone typed, and the two only met on the wall.

This module makes them meet here instead:

  * Two upstream documents recorded on 2026-10-21 are parsed by server.py's
    OWN fetch_hamqsl() and fetch_dx() (urlopen replays the recordings; no
    network, no background thread). The bytes the handler would write for
    /api/solar, /api/bands and /api/dxspots then go through the client's own
    HamClockData.refresh_data(), so the panels get exactly what the render
    loop would hold after one poll.
  * Each data panel is drawn at 1440x900 (the Pi's panel) and at 720x450,
    with every string handed to _blit_fit recorded, and the tests assert what
    the operator reads. Expected values come out of the payload, never out of
    this file, so a re-recorded feed needs no edits here.
  * A static pass over hamclock_pygame.py lists every literal key the
    payload-reading functions take out of a payload and checks each against
    the keys the server really sent. Run against e77a544 (1.0.9) it fails on
    'bz' and 'dxCall'; against 32cec91 (1.0.10), on 'dxCall'.

Recorded feeds (tests/data/feeds/), byte-for-byte as received, CRLFs and all:

  hamqsl_solarxml.xml  https://www.hamqsl.com/solarxml.php          19:13:07Z
  hamqth_dxc.csv       https://www.hamqth.com/dxc_csv.php?limit=15  19:13:17Z
"""
import ast
import collections
import contextlib
import functools
import inspect
import itertools
import json
import os
import re
import sys
import time
import urllib.error
from urllib.parse import urlparse

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import hamclock_data  # noqa: E402
import hamclock_pygame as hp  # noqa: E402
import server  # noqa: E402  (conftest has already pointed its cache dir away)

FEEDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'feeds')
CLIENT_SRC = os.path.join(ROOT, 'hamclock_pygame.py')

SIZES = [(1440, 900), (720, 450)]
SIZE_IDS = ['1440x900', '720x450']

# The three JSON endpoints. Each is CACHE[key] in the server, /api/<key> on
# the wire and HamClockData.<key> in the client.
ENDPOINTS = ('solar', 'bands', 'dxspots')

# Upstream host -> the recording that answers for it. Anything else a fetcher
# asks for is refused, so these tests cannot reach the network (fetch_dx's
# ha8tks fallback would only be tried if the hamqth recording failed).
RECORDED = {
    'www.hamqsl.com': 'hamqsl_solarxml.xml',
    'www.hamqth.com': 'hamqth_dxc.csv',
}

# What the client counts as an open band; 'Poor' is closed, anything else
# (N/A) is neither.
OPEN_CONDITIONS = ('Good', 'Fair')


class _Response:
    """The part of an HTTP response both urlopen callers touch."""

    def __init__(self, body):
        self._body = body
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ------------------------------------------------------------ the payloads

Served = collections.namedtuple('Served', 'wire solar bands dxspots')


@pytest.fixture(scope='module')
def served():
    """The three payloads exactly as server.py would serve them right now.

    The server's own parsers run over the recordings, and the payload is the
    pre-encoded body the handler writes to the socket (CACHE['<key>_bytes']),
    decoded the way the client decodes it. Everything the fetchers touch is
    put back afterwards: CACHE, and the counters _diag_fetch bumps."""
    bodies = {}
    for host, name in RECORDED.items():
        with open(os.path.join(FEEDS, name), 'rb') as f:
            bodies[host] = f.read()

    def replay(req, timeout=None):
        url = getattr(req, 'full_url', req)
        body = bodies.get(urlparse(url).hostname)
        if body is None:
            raise urllib.error.URLError(
                'no recording of %s; these tests never touch the network' % url)
        return _Response(body)

    cache = dict(server.CACHE)
    stats = {k: dict(v) for k, v in getattr(server, '_FETCH_STATS', {}).items()}
    first = dict(getattr(server, '_FIRST_OK_S', {}))
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(server, 'urlopen', replay)
        for key in ENDPOINTS:
            # So a parse that fails cannot pass off an earlier test's payload.
            server.CACHE[key] = None
            server.CACHE[key + '_bytes'] = None
        server.fetch_hamqsl()
        server.fetch_dx()
        wire = {}
        for key in ENDPOINTS:
            if server.CACHE.get(key) is None:
                pytest.fail('server.py made no %s payload out of the recorded '
                            'feeds' % key)
            wire[key] = (server.CACHE.get(key + '_bytes')
                         or json.dumps(server.CACHE[key]).encode('utf-8'))
    finally:
        mp.undo()
        server.CACHE.clear()
        server.CACHE.update(cache)
        # update(), never clear(): the server's diagnostics dicts keep one
        # fixed key set for life, so values are written back in place.
        for name, stat in stats.items():
            server._FETCH_STATS[name].update(stat)
        getattr(server, '_FIRST_OK_S', {}).update(first)
    payload = {k: json.loads(wire[k].decode('utf-8')) for k in ENDPOINTS}
    return Served(wire, payload['solar'], payload['bands'], payload['dxspots'])


@pytest.fixture(scope='module')
def client(served):
    """The render loop's `data` after one poll of that server.

    HamClockData.refresh_data() fetches the three endpoints through the
    client's own JSON path, so anything it ever does to a payload on the way
    in is part of what the panels are given."""
    routes = {'/api/' + k: served.wire[k] for k in ENDPOINTS}

    def serve(req, timeout=None):
        url = getattr(req, 'full_url', req)
        body = routes.get(urlparse(url).path)
        if body is None:
            raise urllib.error.URLError('not served in this test: %s' % url)
        return _Response(body)

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(hamclock_data, 'urlopen', serve)
        data = hamclock_data.HamClockData()
        data.refresh_data()
    finally:
        mp.undo()
    return data


def test_the_recorded_feeds_become_full_payloads(served):
    """If the server's parsers stopped understanding a feed, every panel test
    below would be checking an empty panel against an empty payload."""
    with open(os.path.join(FEEDS, RECORDED['www.hamqth.com']), 'rb') as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    assert len(served.dxspots) == len(lines), (
        'fetch_dx kept %d of the %d recorded spots'
        % (len(served.dxspots), len(lines)))
    assert all(s.get('dx') and s.get('time') for s in served.dxspots), \
        served.dxspots
    assert served.bands and all(
        {'day', 'night'} <= set(entry) for entry in served.bands.values()), \
        served.bands
    missing = [k for k in ('sfi', 'kIndex', 'ssn', 'magneticField')
               if not served.solar.get(k)]
    assert not missing, 'no %s in %r' % (missing, served.solar)


def test_the_client_holds_exactly_what_the_server_serves(served, client):
    assert client.solar == served.solar
    assert client.bands == served.bands
    assert client.dxspots == served.dxspots


# ------------------------------------------------------------ drawing

Blit = collections.namedtuple('Blit', 'text shown x y')

_stamps = itertools.count(1)


def _fresh_ts():
    """A new data.last_data_refresh, as after a poll. _solar_view and
    _open_bands_strings cache on it, so every draw gets its own."""
    return 1_800_000_000.0 + next(_stamps)


@pytest.fixture(autouse=True)
def _isolated_panel_caches():
    """Start each test with the per-refresh panel caches cold, and hand them
    back exactly as found: another module may be mid-way through using them."""
    saved = {}
    for name in ('_solar_snapshot', '_open_bands_cache'):
        cache = getattr(hp, name, None)
        if isinstance(cache, dict):
            saved[name] = dict(cache)
            cache['ts'] = object()          # equal to no stamp: next draw rebuilds
    yield
    for name, content in saved.items():
        cache = getattr(hp, name)
        cache.clear()
        cache.update(content)


class Canvas:
    """One frame at one resolution.

    Records every string a panel hands _blit_fit: `text` is what the panel
    asked to show, `shown` what fits in the cell at this resolution. The spy
    calls straight through, so the panel paints exactly as it would."""

    def __init__(self, size, monkeypatch):
        pygame.init()
        self.size = size
        self.screen = pygame.Surface(size)
        self.fonts = hp._make_fonts(size)
        self.theme = hp.THEMES['kstate']
        self.layout = hp._get_layout(size)
        self.blits = []
        real = hp._blit_fit
        sig = inspect.signature(real)

        def spy(*args, **kwargs):
            a = sig.bind(*args, **kwargs).arguments
            text = str(a['text'])
            self.blits.append(Blit(text, hp._fit_text(a['font'], text, a['max_w']),
                                   a['x'], a['y']))
            return real(*args, **kwargs)

        monkeypatch.setattr(hp, '_blit_fit', spy)

    def panel(self, key, title):
        """Paint a panel's chrome as the render loop does; return the rect its
        content is drawn into."""
        inner = hp.draw_panel(self.screen, self.layout[key], title,
                              self.fonts, self.theme)
        del self.blits[:]
        return inner

    def texts(self):
        return [b.text for b in self.blits]


@pytest.fixture(params=SIZES, ids=SIZE_IDS)
def canvas(request, monkeypatch):
    return Canvas(request.param, monkeypatch)


def _value_after(blits, label):
    """Panels that print 'label  value' blit the label, then its value."""
    texts = [b.text for b in blits]
    assert label in texts, '%r was never drawn; the panel drew %r' % (label, texts)
    i = texts.index(label)
    assert i + 1 < len(texts), 'nothing was drawn after %r: %r' % (label, texts)
    return texts[i + 1]


def _rows(blits):
    """Table cells grouped into rows by y, top to bottom, each row {x: Blit}."""
    rows = collections.OrderedDict()
    for b in blits:
        rows.setdefault(b.y, {})[b.x] = b
    return list(rows.values())


# ------------------------------------------------------------ DX SPOTS

def _dx_table(canvas, client):
    inner = canvas.panel('dx_spots', 'DX SPOTS')
    hp.draw_dx_spots(canvas.screen, inner, client.dxspots or [],
                     canvas.fonts, canvas.theme)
    rows = _rows(canvas.blits)
    assert rows, 'DX SPOTS drew nothing at all'
    heading = {b.text: x for x, b in rows[0].items()}
    for col in ('DX', 'TIME'):
        assert col in heading, (
            'DX SPOTS drew no %s heading; it drew %r' % (col, sorted(heading)))
    body = rows[1:]
    assert len(body) >= 3, (
        'DX SPOTS drew %d rows from %d spots' % (len(body), len(client.dxspots)))
    return heading, body


def _column(heading, body, name):
    """The cells under one heading, top to bottom (None where a row has none)."""
    return [row.get(heading[name]) for row in body]


def test_the_dx_column_shows_the_call_of_every_spot_it_draws(canvas, client):
    """From April until 1.0.11 every row of the DX column read '--': the panel
    asked for spot['dxCall'] and the server sends the call as 'dx'."""
    heading, body = _dx_table(canvas, client)
    cells = _column(heading, body, 'DX')
    drawn = [c.text if c else None for c in cells]
    sent = [s.get('dx') for s in client.dxspots[:len(body)]]
    assert collections.Counter(drawn) == collections.Counter(sent), (
        'the DX column drew %r for spots the server sent as %r '
        '(the operator read %r)' % (drawn, sent,
                                    [c.shown if c else None for c in cells]))


def test_no_dx_cell_is_the_placeholder_when_the_spot_names_a_call(
        canvas, client):
    """'--' means "no call in this spot". The server put one in every spot,
    and the operator was shown '--' anyway, on every row."""
    heading, body = _dx_table(canvas, client)
    spots = client.dxspots[:len(body)]
    assert all(s.get('dx') for s in spots), \
        'the recorded feed has a spot with no call; re-record'
    placeholders = [i for i, c in enumerate(_column(heading, body, 'DX'))
                    if c is None or c.text in ('--', '')]
    assert not placeholders, (
        'rows %s of the DX column drew the placeholder for calls the server '
        'sent as %r' % (placeholders, [spots[i]['dx'] for i in placeholders]))


def test_the_time_column_shows_the_spots_hhmm_and_nothing_else(canvas, client):
    """The feed's time is 'HHMM YYYY-MM-DD'. Drawn whole into a column sized
    for a time, every row ended in a clipped date -- '1913 …' on the 1440x900
    panel -- which reads as a value that was cut short."""
    heading, body = _dx_table(canvas, client)
    cells = _column(heading, body, 'TIME')
    drawn = [c.text if c else None for c in cells]
    sent = [s['time'].split()[0] for s in client.dxspots[:len(body)]]
    assert all(re.fullmatch(r'\d{4}', t) for t in sent), (
        "the recorded spots' times are not 'HHMM YYYY-MM-DD': %r"
        % [s['time'] for s in client.dxspots])
    assert collections.Counter(drawn) == collections.Counter(sent), (
        'the TIME column drew %r (the operator read %r); the spots were '
        'sent at %r' % (drawn, [c.shown if c else None for c in cells],
                        [s['time'] for s in client.dxspots[:len(body)]]))


# ------------------------------------------------------------ SOLAR

# (row label, /api/solar key). foF2 is left to test_solar_fof2.py: the feed
# sends it empty and the row draws a dash, not the payload.
SOLAR_ROWS = [
    ('SFI', 'sfi'), ('Kp', 'kIndex'), ('SSN', 'ssn'), ('A', 'aIndex'),
    ('Xray', 'xray'), ('Wind', 'solarWind'), ('Bz', 'magneticField'),
    ('Geo', 'geomagField'), ('S/N', 'signalNoise'),
]


@pytest.mark.parametrize('label,key', SOLAR_ROWS, ids=[l for l, _ in SOLAR_ROWS])
def test_each_solar_row_shows_the_reading_the_server_sent(
        canvas, client, label, key):
    """Until 1.0.10 the Bz row read '--' on every frame the Pi ever drew: it
    asked for solar['bz'] and the server sends 'magneticField'. Any row could
    go the same way, so each is checked against the payload."""
    sent = client.solar.get(key)
    assert sent not in (None, ''), \
        'the recorded feed has no %s reading; re-record' % key
    inner = canvas.panel('solar', 'SOLAR')
    # data_refresh_ts as the render loop passes it, so this is the cached
    # _solar_view path the wall display actually takes.
    hp.draw_solar(canvas.screen, inner, client.solar or {}, canvas.fonts,
                  canvas.theme, data_refresh_ts=_fresh_ts())
    assert _value_after(canvas.blits, label) == sent, (
        'SOLAR %s drew %r; the server sent %s=%r'
        % (label, _value_after(canvas.blits, label), key, sent))


MUF_ROWS = [('KP', 'kIndex'), ('SFI', 'sfi'), ('SSN', 'ssn'),
            ('GEOMAG', 'geomagField')]


@pytest.mark.parametrize('label,key', MUF_ROWS, ids=[l for l, _ in MUF_ROWS])
def test_each_muf_panel_readout_shows_the_reading_the_server_sent(
        canvas, client, label, key):
    """The MUF panel's readouts are the largest type on the screen: a wrong
    key there puts '--' in front of everyone in the room."""
    inner = canvas.panel('muf', 'MUF MAP')
    hp.draw_muf_text(canvas.screen, inner, client.solar or {}, canvas.fonts,
                     canvas.theme)
    assert _value_after(canvas.blits, label) == client.solar[key]


def test_geomagnetic_shows_the_servers_kp(canvas, client):
    """A Kp the panel cannot find draws 'Kp --' over an empty bar: the look
    of a quiet sky, whatever the sky is doing."""
    inner = canvas.panel('geomag', 'GEOMAGNETIC')
    hp.draw_geomag(canvas.screen, inner, client.solar or {}, canvas.fonts,
                   canvas.theme, data_refresh_ts=_fresh_ts())
    assert 'Kp %s' % client.solar['kIndex'] in canvas.texts(), canvas.texts()


def test_xray_flux_shows_the_servers_flare_class(canvas, client):
    """A flux class the panel cannot find is drawn as 'A0.0', the quietest
    reading there is, over an empty bar -- flare or no flare."""
    inner = canvas.panel('xray', 'X-RAY FLUX')
    hp.draw_xray(canvas.screen, inner, client.solar or {}, canvas.fonts,
                 canvas.theme, data_refresh_ts=_fresh_ts())
    assert client.solar['xray'] in canvas.texts(), canvas.texts()


# ------------------------------------------------------------ BANDS

def test_every_bands_row_shows_the_servers_day_and_night_conditions(
        canvas, client):
    """BANDS looks each row up by the server's band-group name. A name the
    server does not send reads N/A in both columns, forever."""
    inner = canvas.panel('bands', 'BANDS')
    hp.draw_bands(canvas.screen, inner, client.bands or {}, canvas.fonts,
                  canvas.theme)
    rows = _rows(canvas.blits)[1:]                  # under BAND / DAY / NIGHT
    assert rows, 'BANDS drew no band rows: %r' % canvas.texts()
    for row in rows:
        cells = [row[x].text for x in sorted(row)]
        assert len(cells) == 3, 'a BANDS row drew %r' % cells
        name, day, night = cells
        assert name in client.bands, (
            'BANDS drew a %r row; the server sends %s'
            % (name, sorted(client.bands)))
        sent = client.bands[name]
        assert (day, night) == (sent['day'], sent['night']), (
            '%s drew day=%r night=%r; the server sent %r'
            % (name, day, night, sent))


def test_band_activity_counts_the_spots_the_server_sent_on_each_band(
        canvas, client):
    """BAND ACTIVITY counts each spot's 'band'. Read from a key the server
    does not send, all ten bars sit at zero beside a full DX panel."""
    inner = canvas.panel('band_activity', 'BAND ACTIVITY')
    hp.draw_band_activity(canvas.screen, inner, client.dxspots or [],
                          canvas.fonts, canvas.theme)
    sent = collections.Counter(s.get('band') for s in client.dxspots)
    drawn = {}
    for row in _rows(canvas.blits):
        cells = [row[x].text for x in sorted(row)]
        assert len(cells) == 2, 'a BAND ACTIVITY row drew %r' % cells
        drawn[cells[0]] = cells[1]
    assert sum(sent[b] for b in drawn), (
        'BAND ACTIVITY drew no band that has a spot: %r' % drawn)
    assert drawn == {b: str(sent[b]) for b in drawn}


# ------------------------------------------------------------ OPEN BANDS

def _open_and_closed(blits):
    """The band names listed under OPEN: and under CLOSED:, however the
    lines were wrapped or split between blits. CLOSED is None when the panel
    did not draw a CLOSED: line at all."""
    lists, current = {'OPEN:': [], 'CLOSED:': []}, None
    closed_drawn = any('CLOSED:' in b.text for b in blits)
    for tok in re.split(r'(OPEN:|CLOSED:)|[\s,]+',
                        ' '.join(b.text for b in blits)):
        if not tok:
            continue
        if tok in lists:
            current = tok
        elif current and tok != '--':
            lists[current].append(tok)
    assert current, 'OPEN BANDS drew no OPEN:/CLOSED: lists: %r' % (
        [b.text for b in blits])
    return lists['OPEN:'], (lists['CLOSED:'] if closed_drawn else None)


def _expected_open_and_closed(bands, period):
    opens = [n for n, e in bands.items() if e.get(period) in OPEN_CONDITIONS]
    closes = [n for n, e in bands.items() if e.get(period) == 'Poor']
    return sorted(opens), sorted(closes)


def _draw_open_bands(canvas, client, **period):
    inner = canvas.panel('open_bands', 'OPEN BANDS')
    hp.draw_open_bands(canvas.screen, inner, client.bands or {}, canvas.fonts,
                       canvas.theme, data_refresh_ts=_fresh_ts(), **period)
    opens, closes = _open_and_closed(canvas.blits)
    return sorted(opens), (None if closes is None else sorted(closes))


def _assert_takes_period():
    assert 'period' in inspect.signature(hp.draw_open_bands).parameters, (
        'draw_open_bands takes no period: OPEN BANDS judges every band by the '
        'one column it was written for, at every hour of the day')


@pytest.mark.parametrize('period', ['day', 'night'])
def test_open_bands_for_a_period_is_decided_by_that_periods_column(
        canvas, client, period):
    """Before 1.0.11 OPEN BANDS read the day column at every hour, so after
    dark it listed daytime conditions."""
    _assert_takes_period()
    got = _draw_open_bands(canvas, client, period=period)
    want = _expected_open_and_closed(client.bands, period)
    if got[1] is None:
        # The 720x450 box is 27 px tall: two lines, both taken by a wrapped
        # OPEN list, so the panel gives up CLOSED (the complement) rather
        # than cut an open band. Nowhere else may it leave CLOSED out.
        assert canvas.size == (720, 450), (
            'OPEN BANDS left out CLOSED at %r, which has room for it'
            % (canvas.size,))
        want = (want[0], None)
    assert got == want, (
        'by %s OPEN BANDS listed OPEN %s / CLOSED %s; the %s column says '
        'OPEN %s / CLOSED %s' % (period, got[0], got[1], period,
                                 want[0], want[1]))


def _list_holding(name, open_and_closed):
    opens, closes = open_and_closed
    return ('OPEN' if name in opens
            else 'CLOSED' if name in (closes or ()) else None)


def test_a_band_whose_day_and_night_disagree_moves_list_with_the_period(
        canvas, client):
    """In the 2026-10-21 recording 12m-10m is Fair by day and Poor by night,
    and 80m-40m the reverse. Reading one column, the panel never moved them."""
    _assert_takes_period()
    flips = sorted(n for n, e in client.bands.items()
                   if (e.get('day') in OPEN_CONDITIONS)
                   != (e.get('night') in OPEN_CONDITIONS))
    assert flips, ('no band in the recorded feed differs between day and '
                   'night; re-record one that does: %r' % client.bands)
    by_day = _draw_open_bands(canvas, client, period='day')
    by_night = _draw_open_bands(canvas, client, period='night')
    for name in flips:
        assert (_list_holding(name, by_day)
                != _list_holding(name, by_night)), (
            '%s (%r) is under %s by day and by night'
            % (name, client.bands[name], _list_holding(name, by_day)))


@contextlib.contextmanager
def _local_clock_reads(hour):
    """Run with the process's local clock showing `hour` o'clock.

    Done with TZ, not by patching a clock function, so it holds however the
    client asks for local time. The hour can tick over mid-test; 12 and 0
    are chosen so that the next hour is still the same half of the day."""
    utc_hour = time.gmtime().tm_hour
    # POSIX TZ offsets are hours WEST of UTC: 'LCL-9' is UTC+9.
    west = (utc_hour - hour + 12) % 24 - 12
    old = os.environ.get('TZ')
    os.environ['TZ'] = 'LCL%+d' % west
    time.tzset()
    try:
        assert time.localtime().tm_hour in (hour, (hour + 1) % 24)
        yield
    finally:
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()


@pytest.mark.parametrize('hour,period', [(12, 'day'), (0, 'night')],
                         ids=['local-noon', 'local-midnight'])
def test_open_bands_with_no_period_follows_the_local_clock(
        canvas, client, hour, period):
    """With no period given the panel goes by the local clock, 06:00-17:59
    being day: the rule the render loop also uses to pick the period it
    passes. At midnight the old panel still read the day column -- in the
    2026-10-21 recording, 12m-10m OPEN on a night the feed called Poor."""
    with _local_clock_reads(hour):
        got = _draw_open_bands(canvas, client)
    want = _expected_open_and_closed(client.bands, period)
    if got[1] is None:
        # See test_open_bands_for_a_period_is_decided_by_that_periods_column.
        assert canvas.size == (720, 450), (
            'OPEN BANDS left out CLOSED at %r, which has room for it'
            % (canvas.size,))
        want = (want[0], None)
    assert got == want, (
        'at %02d:00 local OPEN BANDS listed OPEN %s / CLOSED %s; the %s '
        'column says OPEN %s / CLOSED %s'
        % (hour, got[0], got[1], period, want[0], want[1]))


# ------------------------------------------------------------ static contract
#
# Every literal key the client reads out of a payload -- _safe(x, 'key'),
# x.get('key'), x['key'] -- must be one the server sends. Payload values are
# tracked from each function's payload parameter (and from HamClockData's
# data.solar / data.bands / data.dxspots) through assignments, `or {}`,
# loops, slices and .get(); a call that hands one to another function of the
# module is followed into it. A read whose key is not a string literal is
# not checked here: the panel tests above cover those by drawing.

# Each function that takes a server payload, and the parameter carrying it.
CONSUMERS = {
    '_solar_view':         ('solar', 'solar'),
    'draw_solar':          ('solar', 'solar'),
    'draw_muf_text':       ('solar', 'solar'),
    'draw_geomag':         ('solar', 'solar'),
    'draw_xray':           ('solar', 'solar'),
    'draw_bands':          ('bands', 'bands'),
    '_open_bands_strings': ('bands', 'bands'),
    'draw_open_bands':     ('bands', 'bands'),
    'draw_dx_spots':       ('dxspots', 'dxspots'),
    'draw_band_activity':  ('dxspots', 'dxspots'),
}

# Each kind of payload value: its container, and what one step into it holds.
SHAPES = {
    'solar':   ('dict', None),       # /api/solar    {'sfi': '121', ...}
    'bands':   ('dict', 'band'),     # /api/bands    {'80m-40m': {...}, ...}
    'band':    ('dict', None),       #   one entry   {'day': 'Poor', 'night': 'Fair'}
    'dxspots': ('list', 'spot'),     # /api/dxspots  [{...}, ...]
    'spot':    ('dict', None),       #   one spot    {'dx': 'WG3J', ...}
}

WHERE = {
    'solar': '/api/solar',
    'bands': '/api/bands (band-group names)',
    'band': 'a /api/bands entry',
    'spot': 'a /api/dxspots spot',
}

# Keys the client may read although the server never sends them, but ONLY as
# a fallback: the same function must also read the key named here, which the
# server does send. On its own a fallback read IS the bug -- 1.0.9 read 'bz'
# with no 'magneticField', 1.0.10 read 'dxCall' with no 'dx'.
FALLBACK_FOR = {
    ('solar', 'bz'): 'magneticField',   # pre-1.0.10 name, kept for other producers
    ('spot', 'dxCall'): 'dx',           # pre-1.0.11 name, kept for other producers
}

# Keys the server sends only some of the time, which the client may read
# even though this recording lacks them. None today: fetch_hamqsl and
# fetch_dx emit every key in every payload, '' standing in for no reading.
OPTIONAL = frozenset()

_NamedExpr = getattr(ast, 'NamedExpr', ())
_Index = getattr(ast, 'Index', ())                   # Python < 3.9


def _kind(of):
    return SHAPES[of][0] if of else None


def _inside(of):
    return SHAPES[of][1] if of else None


def _payload(node, env):
    """Which kind of payload value `node` evaluates to, or None."""
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Attribute):              # data.dxspots
        return node.attr if node.attr in ENDPOINTS else None
    if isinstance(node, _NamedExpr):
        return _payload(node.value, env)
    if isinstance(node, ast.BoolOp):                 # solar or {}
        return next((p for p in (_payload(v, env) for v in node.values) if p),
                    None)
    if isinstance(node, ast.IfExp):                  # e if ok else {}
        return _payload(node.body, env) or _payload(node.orelse, env)
    if isinstance(node, ast.Subscript):
        outer = _payload(node.value, env)
        if isinstance(node.slice, ast.Slice):        # dxspots[:30]
            return outer if _kind(outer) == 'list' else None
        return _inside(outer)
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == 'get':
            return _inside(_payload(f.value, env))
        if isinstance(f, ast.Name) and f.id == '_safe' and node.args:
            return _inside(_payload(node.args[0], env))
    return None


def _bind(target, kind, env):
    if kind and isinstance(target, ast.Name):
        env[target.id] = kind


def _bind_loop(target, it, env):
    """`for target in it`: which names come out holding payload values."""
    if (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
            and it.func.id == 'enumerate' and it.args):
        if isinstance(target, ast.Tuple) and len(target.elts) == 2:
            _bind_loop(target.elts[1], it.args[0], env)
        return
    if (isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute)
            and it.func.attr in ('items', 'values')):
        outer = _payload(it.func.value, env)
        if _kind(outer) != 'dict':
            return
        if it.func.attr == 'values':
            _bind(target, _inside(outer), env)
        elif isinstance(target, ast.Tuple) and len(target.elts) == 2:
            _bind(target.elts[1], _inside(outer), env)
        return
    outer = _payload(it, env)
    if _kind(outer) == 'list':
        _bind(target, _inside(outer), env)


def _payload_names(fn, seeds):
    """{local name: payload kind} in fn, grown from the seeded parameters."""
    env = dict(seeds)
    while True:
        before = dict(env)
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    _bind(target, _payload(node.value, env), env)
            elif isinstance(node, (ast.AnnAssign, _NamedExpr)) and node.value:
                _bind(node.target, _payload(node.value, env), env)
            elif isinstance(node, (ast.For, ast.comprehension)):
                _bind_loop(node.target, node.iter, env)
        if env == before:
            return env


def _literal(node):
    if isinstance(node, _Index):
        node = node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


Read = collections.namedtuple('Read', 'func line kind key')


def _reads(fn, env):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == '_safe' and len(node.args) >= 2:
                owner, key = node.args[0], node.args[1]
            elif isinstance(f, ast.Attribute) and f.attr == 'get' and node.args:
                owner, key = f.value, node.args[0]
            else:
                continue
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            owner, key = node.value, node.slice
        else:
            continue
        kind, name = _payload(owner, env), _literal(key)
        if _kind(kind) == 'dict' and name is not None:
            yield Read(fn.name, node.lineno, kind, name)


def _params(fn):
    a = fn.args
    return ([p.arg for p in list(getattr(a, 'posonlyargs', [])) + list(a.args)],
            [p.arg for p in a.kwonlyargs])


def _handoffs(fn, env, defs):
    """(callee, {parameter: kind}) for each call in fn that passes a payload
    value to another function of the module."""
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        callee = defs.get(node.func.id)
        if callee is None or callee.name == '_safe':
            continue
        positional, kwonly = _params(callee)
        seeds = {}
        for param, arg in zip(positional, node.args):
            if isinstance(arg, ast.Starred):
                break
            if _payload(arg, env):
                seeds[param] = _payload(arg, env)
        for kw in node.keywords:
            if kw.arg in positional + kwonly and _payload(kw.value, env):
                seeds[kw.arg] = _payload(kw.value, env)
        if seeds:
            yield callee, seeds


@functools.lru_cache(maxsize=None)
def _client_module():
    with open(CLIENT_SRC, encoding='utf-8') as f:
        tree = ast.parse(f.read(), CLIENT_SRC)
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


@functools.lru_cache(maxsize=None)
def _client_reads():
    """Every literal payload key hamclock_pygame.py reads, as Read tuples."""
    defs = _client_module()
    todo = [(defs[name], {param: kind})
            for name, (param, kind) in CONSUMERS.items() if name in defs]
    todo += [(fn, {}) for fn in defs.values()
             if any(isinstance(n, ast.Attribute) and n.attr in ENDPOINTS
                    for n in ast.walk(fn))]
    found, seen = set(), set()
    while todo:
        fn, seeds = todo.pop()
        key = (fn.name, tuple(sorted(seeds.items())))
        if key in seen:
            continue
        seen.add(key)
        env = _payload_names(fn, seeds)
        found.update(_reads(fn, env))
        todo.extend(_handoffs(fn, env, defs))
    return frozenset(found)


def _sent_keys(served):
    return {
        'solar': set(served.solar),
        'bands': set(served.bands),
        'band': set().union(*map(set, served.bands.values())),
        'spot': set().union(*map(set, served.dxspots)),
    }


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_every_key_the_client_reads_is_one_the_server_sends(served, endpoint):
    """The check that catches the bug class itself. A panel reading a key the
    server never sends draws '--' forever and nothing ever errors: the render
    loop wraps each panel in `except Exception: pass`, and _safe turns the
    missing key into the placeholder."""
    kinds = {'solar': ('solar',), 'bands': ('bands', 'band'),
             'dxspots': ('dxspots', 'spot')}[endpoint]
    sent = _sent_keys(served)
    reads = sorted(r for r in _client_reads() if r.kind in kinds)
    assert reads, (
        'found no literal reads of /api/%s anywhere in hamclock_pygame.py, so '
        'this check has gone blind; bring CONSUMERS / SHAPES up to date'
        % endpoint)
    wrong = []
    for r in reads:
        if r.key in sent[r.kind] or (r.kind, r.key) in OPTIONAL:
            continue
        primary = FALLBACK_FOR.get((r.kind, r.key))
        if primary and any(o.func == r.func and o.kind == r.kind
                           and o.key == primary for o in reads):
            continue
        msg = ('%s (hamclock_pygame.py:%d) reads %r from %s, which the server '
               'never sends' % (r.func, r.line, r.key, WHERE[r.kind]))
        if primary:
            msg += ('; %r is allowed only as a fallback behind %r, and %s '
                    'never reads %r' % (r.key, primary, r.func, primary))
        wrong.append('%s. The server sends: %s'
                     % (msg, ', '.join(sorted(sent[r.kind]))))
    assert not wrong, '\n'.join(wrong)


def test_the_contract_still_names_every_payload_reader_and_its_parameter():
    """A renamed function or parameter, or a new panel fed a payload, would
    otherwise drop out of the static check without a sound."""
    defs = _client_module()
    problems = []
    for name, (param, _kind_) in sorted(CONSUMERS.items()):
        if name not in defs:
            problems.append('%s is gone from hamclock_pygame.py' % name)
        elif param not in sum(_params(defs[name]), []):
            problems.append('%s no longer has a %r parameter' % (name, param))
    for fn in defs.values():
        if fn.name in CONSUMERS:
            continue
        env = _payload_names(fn, {})
        for callee, seeds in _handoffs(fn, env, defs):
            if callee.name not in CONSUMERS:
                problems.append(
                    '%s hands %s to %s, which CONSUMERS does not list'
                    % (fn.name, sorted(seeds.values()), callee.name))
    assert not problems, '\n'.join(problems)
