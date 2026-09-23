"""1.0.10 follow-ups: readings that were missing but drawn as numbers, a real
reading that was never drawn at all, and two guards nothing tested.

Everything here was found by the adversarial review of 1.0.10, which reverted
each fix one at a time and found three whose removal left the whole suite
green. A guard nobody can break is a guard nobody is checking, so each test
below names what it saw before the fix.

Text is captured at _blit_fit / _draw_value_and_bar rather than inferred from
pixels, because the question is which STRING a panel chose to draw. The pixel
side of the dash is already covered in test_solar_fof2.py.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp

SIZE = (1440, 900)
_ts = [1_000_000.0]


def _fresh_ts():
    """_solar_view caches on the refresh timestamp; reuse one and a test reads
    the previous test's data."""
    _ts[0] += 1.0
    return _ts[0]


@pytest.fixture
def env():
    pygame.init()
    screen = pygame.Surface(SIZE)
    return screen, hp._make_fonts(SIZE), hp.THEMES['kstate'], hp._get_layout(SIZE)


def _capture_blits(monkeypatch):
    drawn = []
    real = hp._blit_fit

    def spy(screen, font, text, *a, **kw):
        drawn.append(str(text))
        return real(screen, font, text, *a, **kw)

    monkeypatch.setattr(hp, '_blit_fit', spy)
    return drawn


def _value_after(drawn, label):
    """draw_solar blits each row as label then value."""
    i = drawn.index(label)
    return drawn[i + 1]


# ------------------------------------------------------------ SOLAR foF2

@pytest.mark.parametrize("cached", [False, True], ids=['uncached', 'cached'])
@pytest.mark.parametrize("raw", ['0', '', 'NoRpt'])
def test_solar_panel_never_prints_a_missing_fof2_as_a_reading(
        env, monkeypatch, cached, raw):
    """Before: removing either _fmt_fof2 call in draw_solar left all 1263
    tests green, so the SOLAR panel's 'foF2 0' could have come back unseen.
    'NoRpt' is hamqsl's own word for no reading, used in the same document."""
    screen, fonts, theme, layout = env
    drawn = _capture_blits(monkeypatch)
    ts = _fresh_ts() if cached else None
    hp.draw_solar(screen, layout['solar'], {'sfi': '104', 'fof2': raw},
                  fonts, theme, data_refresh_ts=ts)
    assert _value_after(drawn, 'foF2') == hp.FOF2_ABSENT, drawn


@pytest.mark.parametrize("cached", [False, True], ids=['uncached', 'cached'])
def test_solar_panel_still_prints_a_real_fof2(env, monkeypatch, cached):
    screen, fonts, theme, layout = env
    drawn = _capture_blits(monkeypatch)
    ts = _fresh_ts() if cached else None
    hp.draw_solar(screen, layout['solar'], {'sfi': '104', 'fof2': '5.2'},
                  fonts, theme, data_refresh_ts=ts)
    assert _value_after(drawn, 'foF2') == '5.2'


def test_muf_panel_does_not_put_a_unit_after_a_worded_no_reading(
        env, monkeypatch):
    """Before: _fmt_fof2('NoRpt') returned 'NoRpt', so the headline row read
    'FOF2 NoRpt MHz'."""
    screen, fonts, theme, layout = env
    drawn = _capture_blits(monkeypatch)
    hp.draw_muf_text(screen, hp._panel_inner_rect(layout['muf']),
                     {'fof2': 'NoRpt', 'kIndex': '2'}, fonts, theme)
    assert 'NoRpt' not in ' '.join(drawn)
    assert not any('MHz' in t for t in drawn), drawn


# ------------------------------------------------------------ SOLAR Bz

@pytest.mark.parametrize("cached", [False, True], ids=['uncached', 'cached'])
def test_bz_is_read_from_the_key_the_server_actually_sends(
        env, monkeypatch, cached):
    """Before: the panel read solar['bz']; the server has only ever sent
    'magneticField' (index.html reads it there). Every Bz ever drawn on the
    Pi was '--'."""
    screen, fonts, theme, layout = env
    drawn = _capture_blits(monkeypatch)
    ts = _fresh_ts() if cached else None
    hp.draw_solar(screen, layout['solar'],
                  {'sfi': '104', 'magneticField': '-3.4'},
                  fonts, theme, data_refresh_ts=ts)
    assert _value_after(drawn, 'Bz') == '-3.4', drawn


# ------------------------------------------------------------ GEOMAG Kp

def _capture_bar(monkeypatch):
    got = {}

    def spy(screen, rect, text, value, vmax, color, fonts, theme):
        got.update(text=text, value=value, color=tuple(color))

    monkeypatch.setattr(hp, '_draw_value_and_bar', spy)
    return got


@pytest.mark.parametrize("cached", [False, True], ids=['uncached', 'cached'])
def test_a_missing_kp_is_not_drawn_as_a_quiet_sky(env, monkeypatch, cached):
    """Before: GEOMAG defaulted a missing Kp to 0 and drew 'Kp 0' — a
    quiet-sky reading from no data — while SOLAR, after 1.0.10's server
    change, drew the same missing value as '--'. The text is what an operator
    saw: draw_bar paints no fill at 0, so the 'good' colour it was handed was
    never on screen. The colour assertion is here for the day the bar gets a
    minimum sliver, not because it changes a pixel today."""
    screen, fonts, theme, layout = env
    got = _capture_bar(monkeypatch)
    ts = _fresh_ts() if cached else None
    hp.draw_geomag(screen, layout['geomag'], {'kIndex': ''}, fonts, theme,
                   data_refresh_ts=ts)
    assert got['text'] == 'Kp --', got
    assert got['value'] == 0.0
    assert got['color'] == tuple(theme['label']), (
        "no reading must not be coloured as a good/fair/poor verdict")


def test_a_real_kp_keeps_its_verdict_colour(env, monkeypatch):
    screen, fonts, theme, layout = env
    got = _capture_bar(monkeypatch)
    hp.draw_geomag(screen, layout['geomag'], {'kIndex': '5'}, fonts, theme)
    assert got['text'] == 'Kp 5'
    assert got['color'] == tuple(theme['fair'])


# ------------------------------------------------------------ non-finite text
#
# float() accepts 'NAN', 'nan', 'inf' and 'Infinity', and hamqsl is PHP, which
# prints a failed float as NAN / INF. Rendered through the real loop at
# 1440x900 before this was fixed: the MUF headline read "FOF2 NAN MHz", and
# GEOMAG drew "Kp NAN" over a FULL bar in 'poor' — NaN fails every '<' in the
# verdict chain, and draw_bar's min(1.0, nan) is 1.0 — a storm from a number
# that failed to compute.

@pytest.mark.parametrize("raw", ['NAN', 'nan', 'inf', '-INF', 'Infinity',
                                 '-1', '-0.5'])
def test_fmt_fof2_dashes_values_that_parse_but_are_not_a_frequency(raw):
    assert hp._fmt_fof2(raw) == hp.FOF2_ABSENT


def test_muf_panel_does_not_put_a_unit_after_nan(env, monkeypatch):
    screen, fonts, theme, layout = env
    drawn = _capture_blits(monkeypatch)
    hp.draw_muf_text(screen, hp._panel_inner_rect(layout['muf']),
                     {'fof2': 'NAN', 'kIndex': '2'}, fonts, theme)
    assert not any('MHz' in t or 'NAN' in t for t in drawn), drawn


def _ink_of(screen, rect, colour):
    """Pixels in `rect` that are exactly `colour` — the bar fill is a flat
    rect, and the value text above it is drawn in theme['bright']."""
    sub = screen.subsurface(pygame.Rect(rect).clip(screen.get_rect()))
    return pygame.mask.from_threshold(
        sub, tuple(colour)[:3] + (255,), (1, 1, 1, 255)).count()


@pytest.mark.parametrize("cached", [False, True], ids=['uncached', 'cached'])
@pytest.mark.parametrize("raw", ['NAN', 'inf'])
def test_a_non_finite_kp_paints_no_verdict(env, cached, raw):
    screen, fonts, theme, layout = env
    rect = layout['geomag']
    # Control: the same panel, same rect, with a real storm-level Kp does
    # paint 'poor' ink — so a zero below is the bar, not the measurement.
    screen.fill(theme['bg'])
    hp.draw_geomag(screen, rect, {'kIndex': '7'}, fonts, theme,
                   data_refresh_ts=_fresh_ts() if cached else None)
    assert _ink_of(screen, rect, theme['poor']) > 0
    screen.fill(theme['bg'])
    hp.draw_geomag(screen, rect, {'kIndex': raw}, fonts, theme,
                   data_refresh_ts=_fresh_ts() if cached else None)
    for verdict in ('good', 'fair', 'poor'):
        assert _ink_of(screen, rect, theme[verdict]) == 0, (
            "Kp %r painted %s ink" % (raw, verdict))


# ------------------------------------------------------------ watchdog clock

def _wedged(since):
    st = hp._new_report_state()
    st['stage'] = 'sending'
    st['sending_since'] = since
    st['result'] = {'done': False, 'ok': False, 'msg': ''}
    return st


def test_a_backwards_clock_step_cannot_stretch_the_watchdog():
    """The Pi 1 has no RTC and NTP steps its clock. Before the guard, a step
    back of an hour measured elapsed time from a start that was now in the
    future, so the button stayed dead for the watchdog PLUS the hour.
    Removing the guard left the whole suite green."""
    t0 = 50_000.0
    st = _wedged(t0)
    stepped = t0 - 3600.0
    assert hp._report_poll(st, now=stepped) is False
    assert st['stage'] == 'sending'
    assert hp._report_poll(
        st, now=stepped + hp.REPORT_SEND_WATCHDOG_S - 0.1) is False
    assert st['stage'] == 'sending'
    assert hp._report_poll(st, now=stepped + hp.REPORT_SEND_WATCHDOG_S) is True
    assert st['stage'] == 'idle', (
        "released 75 s after the step, not 75 s + 3600 s")


def test_a_forward_step_only_fires_the_watchdog_early():
    """The documented cost of the other direction: at worst a resend."""
    st = _wedged(50_000.0)
    assert hp._report_poll(st, now=50_000.0 + 3600.0) is True
    assert st['stage'] == 'idle'
