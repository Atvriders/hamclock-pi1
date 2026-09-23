"""foF2: a missing reading must never render as a measurement of zero.

hamqsl is publishing an EMPTY element right now -- <fof2></fof2>, sitting next
to <muffactor></muffactor> and <muf>NoRpt</muf> in the 22 Sep 2026 1900 GMT
sample this file pins -- and server.py turned that absence into the string
'0'. The centre panel prints foF2 in the largest font it owns, so the Pi read

    FOF2   0 MHz

which is a reading nobody took, in the slot reserved for the headline
measurement, with nothing on screen to say the ionosonde was silent. An
operator picking a band off that is being actively misled, exactly like the
truncated-value bug in test_text_truncation_marked.py.

Three layers have to hold, and they are tested as three layers because the
client cannot see which server it is talking to: every server before this
release spells absence '0', so the client cannot assume the parse was fixed.

    parse    server.py maps an empty or missing element to '', a reading to
             itself, and keeps '0' available as the real reading it is
    format   hamclock_pygame._fmt_fof2 dashes None, '' AND '0'
    render   draw_muf_text does not print ' MHz' after that dash

The parse half also pins which OTHER defaults in the same dict were changed
and which were deliberately left numeric, so the next edit has to argue with
the reasoning instead of silently reverting it.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import pygame
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp
import server


# ---------------------------------------------------------------------------
# Layer 1: the parse (server.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_cache():
    """Other modules in this suite read/write server.CACHE; put it back."""
    saved = dict(server.CACHE)
    yield
    server.CACHE.clear()
    server.CACHE.update(saved)


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _parse(monkeypatch, inner):
    """Run fetch_hamqsl over a <solardata> body and return the solar dict."""
    body = ('<?xml version="1.0"?><solar><solardata>'
            + inner +
            '</solardata></solar>').encode('utf-8')
    monkeypatch.setattr(server, 'urlopen',
                        lambda req, timeout=None: _Resp(body))
    server.fetch_hamqsl()
    return server.CACHE['solar']


# The live feed, verbatim, on the day the bug was diagnosed. Two facts in one
# document are what make this the important fixture: <fof2> is EMPTY, and
# <kindex> is a genuine zero. Any scheme that renders absence as '0' destroys
# the difference between those two lines.
LIVE_SAMPLE = (
    '<source url="http://www.hamqsl.com/solar.html">N0NBH</source>'
    '<updated> 22 Sep 2026 1900 GMT</updated>'
    '<solarflux>104</solarflux>'
    '<aindex> 3</aindex>'
    '<kindex> 0</kindex>'
    '<kindexnt>No Report</kindexnt>'
    '<xray>B7.5</xray>'
    '<sunspots>111</sunspots>'
    '<heliumline>112.6</heliumline>'
    '<protonflux>13</protonflux>'
    '<electonflux>1740</electonflux>'
    '<aurora> 1</aurora>'
    '<solarwind>289.2</solarwind>'
    '<magneticfield>  1.3</magneticfield>'
    '<geomagfield>INACTIVE</geomagfield>'
    '<signalnoise>S0-S1</signalnoise>'
    '<fof2></fof2>'
    '<muffactor></muffactor>'
    '<muf>NoRpt</muf>'
)


def test_empty_fof2_element_is_not_a_zero_reading(monkeypatch):
    """The bug, at its source: <fof2></fof2> is what hamqsl sends today."""
    solar = _parse(monkeypatch, '<solarflux>104</solarflux><fof2></fof2>')
    assert solar['fof2'] == '', (
        f"an empty <fof2> parsed to {solar['fof2']!r}; anything that reads as "
        f"a number here becomes 'FOF2 {solar['fof2']} MHz' on the panel")


def test_missing_fof2_element_is_not_a_zero_reading(monkeypatch):
    solar = _parse(monkeypatch, '<solarflux>104</solarflux>')
    assert solar['fof2'] == ''


def test_whitespace_only_fof2_element_is_not_a_zero_reading(monkeypatch):
    solar = _parse(monkeypatch, '<fof2>   </fof2>')
    assert solar['fof2'] == ''


@pytest.mark.parametrize("raw,want", [
    ('9.8', '9.8'),
    ('12.5', '12.5'),
    (' 7.2 ', '7.2'),
    ('0', '0'),
])
def test_a_real_fof2_reading_survives_the_parse(monkeypatch, raw, want):
    """Including '0'. If an ionosonde ever does report zero, that is a
    reading, and it has to reach the client as one -- which is only possible
    because absence is now spelled differently."""
    solar = _parse(monkeypatch, f'<fof2>{raw}</fof2>')
    assert solar['fof2'] == want


def test_live_sample_keeps_an_absent_fof2_apart_from_a_real_zero(monkeypatch):
    """The whole point, in one document.

    kindex is genuinely ' 0' in this sample and fof2 is genuinely absent. If
    the two come out of the parse looking the same, the panel cannot tell an
    operator which of its numbers was measured.
    """
    solar = _parse(monkeypatch, LIVE_SAMPLE)
    assert solar['kIndex'] == '0', 'a measured zero must stay a zero'
    assert solar['fof2'] == '', 'an absent reading must not become a zero'
    assert solar['kIndex'] != solar['fof2'], (
        'a measured zero and a missing reading are indistinguishable on the '
        'wire, so the client has no way to render them differently')
    # The rest of the sample must be untouched by the default changes.
    assert solar['sfi'] == '104'
    assert solar['ssn'] == '111'
    assert solar['aIndex'] == '3'
    assert solar['aurora'] == '1'
    assert solar['solarWind'] == '289.2'
    assert solar['magneticField'] == '1.3'
    assert solar['geomagField'] == 'INACTIVE'
    assert solar['signalNoise'] == 'S0-S1'
    assert solar['updated'] == '22 Sep 2026 1900 GMT'


@pytest.mark.parametrize("key,collides_with", [
    ('ssn', 'SSN 0 -- a spotless sun, which happens for weeks at solar min'),
    ('aIndex', 'A 0 -- a quiet geomagnetic day'),
    ('kIndex', 'Kp 0 -- literally the value in the live sample above'),
    ('aurora', 'aurora 0 -- the feed sends a small integer starting at 0'),
    ('magneticField', 'Bz 0 -- Bz crosses zero every few hours'),
    ('geomagField', "'quiet' -- one of the feed's own condition words"),
    ('signalNoise', "'S0-S0' -- the feed's own S/N notation"),
    ('fof2', 'foF2 0'),
    # No real reading is 0 km/s, so this one does not collide with a value —
    # it is here because "Wind 0" reads as a measurement anyway, and an
    # alarming one.
    ('solarWind', 'Wind 0 -- not a possible reading, but it looks like one'),
])
def test_defaults_that_a_reading_could_collide_with_are_empty(
        monkeypatch, key, collides_with):
    """A default the feed could itself have sent is a fabricated measurement.

    Each of these used to default to a value indistinguishable from a real
    reading once it reached a panel, so absence was unreportable.
    """
    solar = _parse(monkeypatch, '<solarflux>104</solarflux>')
    assert solar[key] == '', (
        f"{key} defaults to {solar[key]!r}, which collides with a real "
        f"reading ({collides_with}); '' is the only value every consumer "
        f"already renders as absence")


@pytest.mark.parametrize("key,default,why", [
    ('sfi', '0',
     'the 10.7 cm flux floor is ~64 so 0 is not a reading, and index.html '
     'bails out of the entire solar panel on `if(!d.sfi)return;`'),
    ('xray', 'N/A', "'N/A' is not in that field's value format"),
    ('heliumLine', 'N/A', "'N/A' is not in that field's value format"),
    ('protonFlux', 'N/A', "'N/A' is not in that field's value format"),
    ('electronFlux', 'N/A', "'N/A' is not in that field's value format"),
    ('mpiVer', '', 'already honest'),
    ('updated', '', 'already honest'),
])
def test_defaults_left_alone_are_the_documented_exceptions(
        monkeypatch, key, default, why):
    """Pins the fields deliberately NOT changed, with the reason.

    Without this the next pass at 'make the defaults honest' either reverts
    the fixes above or sweeps up sfi, and index.html --
    which is not this repo's only consumer -- silently loses its solar panel.
    """
    solar = _parse(monkeypatch, '<geomagfield>INACTIVE</geomagfield>')
    assert solar[key] == default, (
        f"{key} was changed to {solar[key]!r}; it was left at {default!r} "
        f"on purpose because {why}")


def test_electron_flux_reads_the_tag_hamqsl_actually_ships(monkeypatch):
    """hamqsl misspells it <electonflux> -- no 'r'.

    'electronflux' therefore never matched, and the field has been falling
    through to 'N/A' for the life of this server. The default was honest, so
    nothing looked broken; the reading simply never reached /api/solar. No
    bundled client renders electronFlux yet, so this fixes the API, not a
    panel.
    """
    solar = _parse(monkeypatch, LIVE_SAMPLE)
    assert solar['electronFlux'] == '1740', (
        "electronFlux came out as {!r}; the live feed carries 1740 in a tag "
        "spelled <electonflux>".format(solar['electronFlux']))


def test_electron_flux_still_reads_the_correct_spelling(monkeypatch):
    """If hamqsl ever fixes the typo, the field must not go dark."""
    solar = _parse(monkeypatch, '<electronflux>2200</electronflux>')
    assert solar['electronFlux'] == '2200'


def test_absent_electron_flux_is_still_na(monkeypatch):
    solar = _parse(monkeypatch, '<solarflux>104</solarflux>')
    assert solar['electronFlux'] == 'N/A'


# ---------------------------------------------------------------------------
# Layer 2: the format (hamclock_pygame._fmt_fof2)
# ---------------------------------------------------------------------------

# Every dash a reasonable implementation might pick. The test cares that the
# result carries no digits, not which dash was chosen.
_DASHES = "-‐‑‒–—―−"


def _is_dash(out):
    s = str(out).strip()
    return bool(s) and all(ch in _DASHES for ch in s)


@pytest.mark.parametrize("value", [None, '', '   '])
def test_fmt_fof2_dashes_an_absent_reading(value):
    out = hp._fmt_fof2(value)
    assert _is_dash(out), (
        f"_fmt_fof2({value!r}) returned {out!r}; absence must render as a "
        f"dash, not as anything an operator could read as a measurement")


@pytest.mark.parametrize("value", ['0', '0.0', ' 0 ', '00', 0, 0.0])
def test_fmt_fof2_dashes_a_stale_servers_zero(value):
    """The version-skew case, and the reason this cannot be fixed in the
    server alone.

    Every server before this release sends '0' for a missing foF2. The
    installer copies the new files long before it restarts hamclock-lite
    (kiosk-install.sh copies near the top and restarts at the very end), and
    hamclock-kiosk is Restart=always, so a client relaunched in that window
    runs against the old server. Dashing zero costs nothing either way:
    foF2 = 0 would mean an F2 layer with no electrons in it, so no ionosonde
    reports it.
    """
    out = hp._fmt_fof2(value)
    assert _is_dash(out), (
        f"_fmt_fof2({value!r}) returned {out!r}; a Pi whose server has not "
        f"restarted yet still receives '0' over the wire")


@pytest.mark.parametrize("value,shown", [
    ('9.8', '9.8'),
    ('12.5', '12.5'),
    (' 7.2 ', '7.2'),
    ('0.5', '0.5'),
    (9.8, '9.8'),
])
def test_fmt_fof2_passes_a_real_reading_through(value, shown):
    """Only a finite value above zero is shown. 0.5 is a reading and must survive."""
    out = str(hp._fmt_fof2(value)).strip()
    assert out == shown, f"_fmt_fof2({value!r}) returned {out!r}"


# ---------------------------------------------------------------------------
# Layer 3: the render (hamclock_pygame.draw_muf_text)
#
# These look at the panel instead of trusting the row-building code: they
# record the strings that reach the rasterizer and then measure the ink those
# strings actually left on the surface.
# ---------------------------------------------------------------------------

PANEL_W, PANEL_H = 500, 700


@pytest.fixture(scope="module")
def screen():
    pygame.display.quit()
    pygame.display.init()
    pygame.font.init()
    s = pygame.display.set_mode((1440, 900))
    yield s
    pygame.display.quit()


def _render_muf(screen, fof2):
    """Draw the MUF panel and return (drawn, surface).

    `drawn` is every (font, text, x, y) that reached _blit_text -- the last
    call before font.render, so it is the text that actually rasterized, after
    any width clamping. The spy calls through, so the surface is the real
    panel, not a reconstruction of it.
    """
    fonts = hp._make_fonts((1440, 900))
    theme = hp.THEMES['kstate']
    drawn = []
    real = hp._blit_text

    def spy(surface, font, text, color, x, y):
        drawn.append((font, str(text), x, y))
        return real(surface, font, text, color, x, y)

    surf = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    solar = {'fof2': fof2, 'geomagField': 'INACTIVE', 'kIndex': '0',
             'sfi': '104', 'ssn': '111'}
    hp._blit_text = spy
    try:
        hp.draw_muf_text(surf, pygame.Rect(0, 0, PANEL_W, PANEL_H), solar,
                         fonts, theme, surf=None)
    finally:
        hp._blit_text = real
    return drawn, surf


def _fof2_value(drawn):
    """The (font, text, x, y) drawn immediately after the FOF2 label."""
    labels = [t for _f, t, _x, _y in drawn]
    assert 'FOF2' in labels, f"no FOF2 row was drawn at all; got {labels}"
    return drawn[labels.index('FOF2') + 1]


def _ink_width(surf, font, x, y):
    """Width of the ink the panel actually left, from x rightwards on row y.

    The crop runs to the panel's right edge rather than to the width of the
    string we expect, so a longer string than expected cannot hide outside the
    measurement.
    """
    cell = pygame.Rect(x, y, PANEL_W - x, font.get_height())
    cell = cell.clip(surf.get_rect())
    return surf.subsurface(cell).get_bounding_rect(min_alpha=1).width


def test_muf_panel_does_not_print_mhz_after_a_dash(screen):
    """'FOF2  -- MHz' is a unit attached to nothing.

    Before the fix the panel drew the literal string '-- MHz', because the row
    was built as '{} MHz'.format(...) around a placeholder.
    """
    drawn, _surf = _render_muf(screen, '')
    font, text, _x, _y = _fof2_value(drawn)
    assert 'MHz' not in text, (
        f"the panel rendered {text!r}: a unit printed after a dash claims "
        f"megahertz for a reading that does not exist")
    assert not any(ch.isdigit() for ch in text), (
        f"the panel rendered {text!r}, which reads as a measurement")


def test_muf_panel_does_not_print_mhz_after_a_stale_servers_zero(screen):
    """Same panel, same skew case as _fmt_fof2's: the wire still says '0'."""
    drawn, _surf = _render_muf(screen, '0')
    _font, text, _x, _y = _fof2_value(drawn)
    assert 'MHz' not in text and not any(ch.isdigit() for ch in text), (
        f"the panel rendered {text!r} for a server that has not restarted")


def test_muf_panel_fof2_ink_is_the_dash_and_nothing_else(screen):
    """Measured on the surface, not inferred from the row list.

    With the bug present this reads 101 px of ink at 1440x900 ('-- MHz' in
    the 35 px title font); the em dash the fix draws is 19 px.
    """
    drawn, surf = _render_muf(screen, '')
    font, _text, x, y = _fof2_value(drawn)

    # The reference is _fmt_fof2's own placeholder, NOT the string the panel
    # happened to draw. Measuring the drawn string against itself is how this
    # test first passed against the broken build: '-- MHz' is of course
    # exactly as wide as '-- MHz'.
    placeholder = str(hp._fmt_fof2('')).strip()
    alone = font.size(placeholder)[0]
    with_unit = font.size(placeholder + ' MHz')[0]

    # Precondition: the column is wide enough that ' MHz' would have FIT.
    # Otherwise _fit_text clamping, not the fix, could be what removed it.
    assert with_unit <= PANEL_W - x, (
        f"this panel is too narrow ({PANEL_W - x} px) to prove anything: "
        f"'{placeholder} MHz' would need {with_unit} px and would be clamped "
        f"away whether the bug was fixed or not")

    ink = _ink_width(surf, font, x, y)
    assert ink <= alone + 2, (
        f"the FOF2 value cell holds {ink} px of ink; the placeholder "
        f"{placeholder!r} is only {alone} px wide, so something else was "
        f"painted after it (a dash plus ' MHz' measures {with_unit} px)")


def test_muf_panel_still_prints_mhz_for_a_real_reading(screen):
    """The unit is not simply gone: a reading still carries it."""
    drawn, surf = _render_muf(screen, '9.8')
    font, text, x, y = _fof2_value(drawn)
    assert '9.8' in text, f"the reading did not reach the panel: {text!r}"
    assert 'MHz' in text, (
        f"the panel rendered {text!r}; a real foF2 reading is in MHz and has "
        f"to say so")
    ink = _ink_width(surf, font, x, y)
    assert ink > font.size('9.8')[0], (
        f"only {ink} px of ink for {text!r} -- the unit did not render")


def test_absent_fof2_leaves_less_ink_than_a_reading(screen):
    """A direct A/B on the surface: absence must be visibly the smaller mark.

    This is the check that would have caught the original bug without knowing
    anything about how the row is built -- '-- MHz' and '9.8 MHz' are nearly
    the same width on screen, which is precisely why the false zero read as a
    normal panel.
    """
    drawn_a, surf_a = _render_muf(screen, '')
    drawn_b, surf_b = _render_muf(screen, '9.8')
    fa, _ta, xa, ya = _fof2_value(drawn_a)
    fb, _tb, xb, yb = _fof2_value(drawn_b)
    ink_a = _ink_width(surf_a, fa, xa, ya)
    ink_b = _ink_width(surf_b, fb, xb, yb)
    assert ink_a * 2 < ink_b, (
        f"absence drew {ink_a} px of ink against {ink_b} px for a real "
        f"reading; at that size the two are the same mark to an operator "
        f"glancing at the panel")


@pytest.mark.parametrize("fof2", ['', '0'])
@pytest.mark.parametrize("mode", [(720, 450), (800, 600), (1024, 768),
                                  (1440, 900)])
def test_no_mhz_after_a_dash_at_any_resolution(screen, mode, fof2):
    """The centre panel is laid out per resolution; the unit must not come
    back at one of them.

    Measured on the pixels, in the inner rect the main loop really hands
    draw_muf_text, with a map raster above the rows as on the Pi -- the map
    is what pushes the rows down, and the synthetic 500x700 panel above has
    none.
    """
    fonts = hp._make_fonts(mode)
    inner = hp._panel_inner_rect(hp._get_layout(mode)['muf'])
    # 720 px wide is what the server rasterizes the MUF map at.
    muf_map = pygame.Surface((720, 426))
    muf_map.fill((40, 80, 120))
    drawn = []
    real = hp._blit_text

    def spy(surface, font, text, color, x, y):
        drawn.append((font, str(text), x, y))
        return real(surface, font, text, color, x, y)

    surf = pygame.Surface(mode, pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    hp._blit_text = spy
    try:
        hp.draw_muf_text(surf, inner,
                         {'fof2': fof2, 'geomagField': 'INACTIVE',
                          'kIndex': '0', 'sfi': '104', 'ssn': '111'},
                         fonts, hp.THEMES['kstate'], surf=muf_map)
    finally:
        hp._blit_text = real

    assert surf.get_at((inner.centerx, inner.y + 2)).a == 255, (
        f"at {mode} the map was not painted, so the rows were laid out for "
        f"a panel the Pi never shows")
    font, text, x, y = _fof2_value(drawn)
    assert 'MHz' not in text, f"at {mode} the panel rendered {text!r}"

    placeholder = str(hp._fmt_fof2('')).strip()
    alone = font.size(placeholder)[0]
    with_unit = font.size(placeholder + ' MHz')[0]
    assert with_unit <= inner.right - x, (
        f"at {mode} the value column ({inner.right - x} px) is too narrow "
        f"for '{placeholder} MHz' ({with_unit} px), so clamping alone would "
        f"hide the unit and this proves nothing")
    cell = pygame.Rect(x, y, inner.right - x, font.get_height())
    ink = surf.subsurface(cell.clip(surf.get_rect())).get_bounding_rect(
        min_alpha=1).width
    assert ink <= alone + 2, (
        f"at {mode} the FOF2 value cell holds {ink} px of ink for "
        f"fof2={fof2!r}; the placeholder is {alone} px, so something was "
        f"painted after it")
