"""Tier 2.3 (_slim_muf_svg) + Tier 2.4 (adaptive rasterize budget).

2.3 strips chrome from the KC2G MUF SVG before cairosvg sees it. The whole
risk of that change is over-stripping: three specific things destroy real map
data, and each has a test below pinned against the actual upstream document
(tests/data/mufd-normal-now.svg.gz, fetched 2026-07-26).

2.4 turns PHASE2_TIMEOUT_S from a hard cap into a floor, so a render that is
merely slow on ARMv6 stops being indistinguishable from one that is hung.
"""
import gzip
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

import server

REAL_SVG_GZ = Path(__file__).parent / "data" / "mufd-normal-now.svg.gz"
SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


@pytest.fixture(autouse=True)
def _restore_globals():
    """These are process-wide and other test modules assert on them."""
    ewma = server._muf_render_ewma
    yield
    server._muf_render_ewma = ewma


@pytest.fixture(scope="module")
def real_svg():
    if not REAL_SVG_GZ.exists():
        pytest.skip("upstream MUF SVG fixture not present")
    with gzip.open(REAL_SVG_GZ, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Synthetic document: the structural contract, independent of the fixture
# ---------------------------------------------------------------------------

MINI_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" \
xmlns:xlink="http://www.w3.org/1999/xlink" \
width="1526.465" height="905.181" viewBox="0 0 1144.848 678.886">\
<defs><style>*{stroke-linejoin:round;stroke-linecap:butt}</style></defs>\
<g id="figure_1"><path id="patch_1" d="M0 678.886h1144.848V0H0z"/>\
<g id="axes_1">\
<path id="patch_2" d="M35.305 570.542v-546.4h1092.8v546.4z" style="fill:#fff"/>\
<g id="FeatureArtist_1"><path d="M1 2h3z"/></g>\
<g id="matplotlib.axis_1"><g id="xtick_1"><g id="text_1">\
<defs><path id="DejaVuSans-31" d="M9 9h9z"/><path id="DejaVuSans-2212" d="M8 8h8z"/></defs>\
<use xlink:href="#DejaVuSans-31" x="5"/><use xlink:href="#DejaVuSans-2212" x="9"/>\
</g></g></g>\
<g id="matplotlib.axis_2"><g id="ytick_1"><use xlink:href="#DejaVuSans-31"/></g></g>\
<path id="FeatureArtist_5" d="M4 4h4z"/>\
<g id="text_29"><use xlink:href="#DejaVuSans-31" x="70"/></g>\
<g id="text_52"><use xlink:href="#DejaVuSans-31" x="80"/></g>\
</g>\
<g id="axes_2"><path id="patch_26" d="M2 2h2z"/>\
<g id="matplotlib.axis_3"><use xlink:href="#DejaVuSans-31"/></g></g>\
</g></svg>"""


def _parse(b):
    return ElementTree.fromstring(b)


def _ids(root, tag):
    return [e.get("id") for e in root.iter(SVG_NS + tag) if e.get("id")]


def test_slim_removes_axes_and_colorbar():
    out = server._slim_muf_svg(MINI_SVG)
    assert out is not None
    gids = _ids(_parse(out), "g")
    assert "matplotlib.axis_1" not in gids
    assert "matplotlib.axis_2" not in gids
    assert "matplotlib.axis_3" not in gids
    assert "axes_2" not in gids
    # ...and keeps the map itself.
    assert "axes_1" in gids
    assert "FeatureArtist_1" in gids


def test_slim_keeps_station_values_and_contour_labels():
    """(a) A blanket <use> strip destroys the data the panel exists to show.

    text_29..text_50 are the per-station MUF values printed on the ionosonde
    dots; text_52..text_69 are the contour value labels. Both are <use>-only,
    so anything that removes <use> wholesale removes the numbers.
    """
    root = _parse(server._slim_muf_svg(MINI_SVG))
    gids = _ids(root, "g")
    assert "text_29" in gids
    assert "text_52" in gids
    assert len(list(root.iter(SVG_NS + "use"))) == 2


def test_slim_hoists_glyph_defs_out_of_the_removed_axis_group():
    """(b) Six digit glyph <defs> live INSIDE matplotlib.axis_1.

    Removing that group without hoisting them leaves the surviving station
    and contour labels referencing ids that no longer exist — the labels stay
    in the document and render as nothing.
    """
    root = _parse(server._slim_muf_svg(MINI_SVG))
    defined = {e.get("id") for e in root.iter() if e.get("id")}
    assert "DejaVuSans-31" in defined, "glyph referenced by text_29 was lost"
    # ...but only what is still referenced: the minus sign was only ever used
    # by the axis tick labels we just deleted.
    assert "DejaVuSans-2212" not in defined


def test_slim_keeps_the_document_style():
    """(c) Pruning "groups with no drawing descendant" deletes the <style>."""
    out = server._slim_muf_svg(MINI_SVG)
    assert b"stroke-linejoin:round" in out
    assert len(list(_parse(out).iter(SVG_NS + "style"))) == 1


def test_slim_crops_the_viewbox_to_the_axes_rect():
    root = _parse(server._slim_muf_svg(MINI_SVG))
    vb = [float(v) for v in root.get("viewBox").split()]
    assert vb == pytest.approx([35.305, 24.142, 1092.8, 546.4], abs=0.01)
    # width/height must follow, or preserveAspectRatio letterboxes the crop
    # back to the old 1526x905 aspect and we gain nothing.
    scale = 1526.465 / 1144.848
    assert float(root.get("width")) == pytest.approx(1092.8 * scale, rel=1e-4)
    assert float(root.get("height")) == pytest.approx(546.4 * scale, rel=1e-4)


def test_slim_output_is_still_svg_namespaced():
    """cairosvg is handed these bytes directly; the namespace must survive."""
    out = server._slim_muf_svg(MINI_SVG)
    assert b'xmlns="http://www.w3.org/2000/svg"' in out
    assert _parse(out).tag == SVG_NS + "svg"


# ---------------------------------------------------------------------------
# Refusing to guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    b"",
    b"not xml at all",
    b"<svg xmlns='http://www.w3.org/2000/svg'><g id='axes_2'/></svg>",   # no patch_2
    b"<html><body>503</body></html>",
])
def test_slim_returns_none_on_anything_unexpected(payload):
    assert server._slim_muf_svg(payload) is None


def test_slim_returns_none_when_the_crop_is_implausible():
    """A 5x5 crop out of a 1145x679 page is a parse accident, not a map."""
    bad = MINI_SVG.replace(b'd="M35.305 570.542v-546.4h1092.8v546.4z"',
                           b'd="M1 6v-5h5v5z"')
    assert server._slim_muf_svg(bad) is None


def test_slim_returns_none_when_nothing_matches_the_chrome_ids():
    """An upstream re-render that renames its groups must degrade to today's
    exact unslimmed path, not to a half-stripped map."""
    renamed = (MINI_SVG.replace(b'id="matplotlib.axis_1"', b'id="mpl_x"')
                       .replace(b'id="matplotlib.axis_2"', b'id="mpl_y"')
                       .replace(b'id="matplotlib.axis_3"', b'id="mpl_c"')
                       .replace(b'id="axes_2"', b'id="cbar"'))
    assert server._slim_muf_svg(renamed) is None


def test_slim_refuses_an_absurdly_large_document():
    """Bounds the DOM build on a 512 MB box (and an entity-expansion payload:
    xml.etree is XXE-safe but not billion-laughs-safe, and defusedxml is not
    in the stdlib so it cannot ship to the Pi)."""
    huge = b"<svg xmlns='http://www.w3.org/2000/svg'>" + b" " * (
        server._MUF_SVG_MAX_BYTES + 1) + b"</svg>"
    assert server._slim_muf_svg(huge) is None


def test_slim_never_raises(monkeypatch):
    """_rasterize_muf calls this unconditionally; a raise blanks the panel."""
    def boom(*a, **kw):
        raise RuntimeError('expat exploded')
    monkeypatch.setattr(server.ElementTree, 'fromstring', boom)
    assert server._slim_muf_svg(MINI_SVG) is None


# ---------------------------------------------------------------------------
# Against the real upstream document
# ---------------------------------------------------------------------------


def test_real_svg_slims_and_keeps_every_data_bearing_group(real_svg):
    out = server._slim_muf_svg(real_svg)
    assert out is not None
    before = _parse(real_svg)
    after = _parse(out)

    before_g = set(_ids(before, "g"))
    after_g = set(_ids(after, "g"))

    # Every per-station MUF value and every contour label survives.
    station = {"text_%d" % n for n in range(29, 51)}
    contour = {"text_%d" % n for n in range(52, 70)}
    assert station <= before_g and contour <= before_g, "fixture drifted"
    assert station <= after_g
    assert contour <= after_g

    # The solar terminator (FeatureArtist_5, ~half the map, moves every
    # render) and the map geometry survive.
    assert "FeatureArtist_5" in {e.get("id") for e in after.iter()}
    for name in ("FeatureArtist_1", "FeatureArtist_3", "GeoContourSet_1"):
        assert name in after_g

    # The chrome is gone.
    assert not [g for g in after_g if g.startswith("matplotlib.axis")]
    assert "axes_2" not in after_g


def test_real_svg_keeps_every_glyph_the_survivors_reference(real_svg):
    """The end-to-end statement of rule (b): no dangling <use> href."""
    after = _parse(server._slim_muf_svg(real_svg))
    defined = {e.get("id") for e in after.iter() if e.get("id")}
    dangling = set()
    for el in after.iter(SVG_NS + "use"):
        href = el.get(XLINK_HREF) or el.get("href") or ""
        if href.startswith("#") and href[1:] not in defined:
            dangling.add(href[1:])
    assert dangling == set()
    # Specifically the six digits that live inside matplotlib.axis_1.
    for glyph in ("30", "31", "32", "34", "36", "38"):
        assert "DejaVuSans-%s" % glyph in defined


def test_real_svg_drops_only_chrome_use_elements(real_svg):
    """257 <use> in, 114 out. A blanket strip would leave 0."""
    assert real_svg.count(b"<use") == 257
    assert server._slim_muf_svg(real_svg).count(b"<use") == 114


def test_real_svg_keeps_the_style_element(real_svg):
    out = server._slim_muf_svg(real_svg)
    assert b"stroke-linejoin:round" in out


def test_real_svg_crop_matches_patch_2(real_svg):
    root = _parse(server._slim_muf_svg(real_svg))
    vb = [float(v) for v in root.get("viewBox").split()]
    assert vb == pytest.approx([35.305, 24.142, 1092.8, 546.4], abs=0.01)


def test_slimmed_real_svg_still_renders_in_cairosvg(real_svg):
    """The payload switch is worthless if cairosvg rejects our surgery."""
    cairosvg = pytest.importorskip("cairosvg")
    png = cairosvg.svg2png(bytestring=server._slim_muf_svg(real_svg),
                           output_width=360)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert width == 360
    # 1092.8 / 546.4 == 2.0 exactly once the axis gutters are cropped off.
    assert height == 180


# ---------------------------------------------------------------------------
# _rasterize_muf hands cairosvg the SLIMMED payload
# ---------------------------------------------------------------------------


class _FakePopen:
    last = None
    pid = 4242

    def __init__(self, argv, **kw):
        self.argv = argv
        self.calls = []
        self.returncode = 0
        self.out = b"\x89PNG\r\n\x1a\nBODY"
        _FakePopen.last = self

    def communicate(self, input=None, timeout=None):
        self.calls.append({"input": input, "timeout": timeout})
        return self.out, b""

    def poll(self):
        return self.returncode if self.calls else None

    def kill(self):
        pass


def _fake_popen(monkeypatch, cls=_FakePopen):
    seen = []

    def factory(argv, **kw):
        p = cls(argv, **kw)
        seen.append(p)
        return p
    monkeypatch.setattr(server.subprocess, "Popen", factory)
    return seen


def test_rasterize_feeds_the_slimmed_bytes_to_cairosvg(monkeypatch, real_svg):
    seen = _fake_popen(monkeypatch)
    server._rasterize_muf(real_svg)
    payload = seen[0].calls[0]["input"]
    assert payload != real_svg
    assert payload == server._slim_muf_svg(real_svg)
    assert len(payload) < len(real_svg)


def test_rasterize_falls_back_to_the_original_when_slimming_declines(
        monkeypatch):
    """An unrecognised document must render exactly as it does today."""
    seen = _fake_popen(monkeypatch)
    unslimmable = b'<svg xmlns="http://www.w3.org/2000/svg" width="10"/>'
    server._rasterize_muf(unslimmable)
    assert seen[0].calls[0]["input"] == unslimmable


def test_rasterize_output_width_is_360_in_the_argv(monkeypatch):
    """Pinned against the argv the code builds, not a file substring: the
    _rasterize_muf docstring also says 'output_width=360', so a bare grep
    reads green even when the one-liner has drifted."""
    seen = _fake_popen(monkeypatch)
    server._rasterize_muf(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    one_liner = seen[0].argv[7]
    assert "output_width=360" in one_liner
    assert "cairosvg.svg2png" in one_liner


def test_rasterize_retries_unslimmed_when_the_slimmed_payload_fails_fast(
        monkeypatch, real_svg):
    """Safety net: a cairosvg complaint about our surgery must not be able to
    turn a working map into a permanently blank panel."""
    class _FailFirst(_FakePopen):
        def __init__(self, argv, **kw):
            super().__init__(argv, **kw)
            if _FailFirst.n == 0:
                self.returncode = 1
                self.out = b""
            _FailFirst.n += 1
    _FailFirst.n = 0

    seen = _fake_popen(monkeypatch, _FailFirst)
    out = server._rasterize_muf(real_svg)
    assert out == b"\x89PNG\r\n\x1a\nBODY"
    assert len(seen) == 2
    assert seen[0].calls[0]["input"] != real_svg   # slimmed
    assert seen[1].calls[0]["input"] == real_svg   # untouched


def test_rasterize_does_not_retry_when_the_slimmed_attempt_burned_the_budget(
        monkeypatch, real_svg):
    """The retry is for "cairosvg rejected our surgery", which fails fast.

    A slow/timed-out attempt is a slow box, and retrying it would spend two
    full budgets back to back (up to 220 s) — starving the 120 s fetch_dx
    cadence, the exact thing PHASE2_TIMEOUT_MAX_S exists to prevent.
    """
    class _Timeout(_FakePopen):
        def communicate(self, input=None, timeout=None):
            self.calls.append({"input": input, "timeout": timeout})
            if len(self.calls) == 1:
                raise subprocess.TimeoutExpired(cmd=self.argv, timeout=timeout)
            return b"", b""

    seen = _fake_popen(monkeypatch, _Timeout)
    monkeypatch.setattr(server, "_kill_process_group", lambda p: None)
    monkeypatch.setattr(server.time, "monotonic",
                        _stepping_clock([0.0, 60.0, 60.0]))
    assert server._rasterize_muf(real_svg) is None
    assert len(seen) == 1


def _stepping_clock(values):
    seq = list(values)

    def clock():
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return clock


# ---------------------------------------------------------------------------
# Tier 2.4 — adaptive budget
# ---------------------------------------------------------------------------


def test_phase2_timeout_s_is_still_the_greppable_floor():
    """The installer greps this literal and tests/test_muf_rasterize.py pins
    it at >= 45; 2.4 must not replace it with a computed value."""
    assert isinstance(server.PHASE2_TIMEOUT_S, int)
    assert server.PHASE2_TIMEOUT_S == 45
    assert "PHASE2_TIMEOUT_S = 45" in Path(server.__file__).read_text()


def test_timeout_ceiling_is_below_the_dx_cadence():
    """A slow render must never be able to starve fetch_dx (120 s)."""
    assert server.PHASE2_TIMEOUT_MAX_S == 110
    assert server.PHASE2_TIMEOUT_MAX_S < 120


def test_timeout_is_the_floor_before_anything_has_been_measured():
    """First attempt only. _MUF_RASTERIZE_TIMEOUT is module-level global state
    that other tests mutate, and after a timeout the budget deliberately
    escalates (see test_timeout_escalates_while_no_render_has_ever_succeeded),
    so pin the counter rather than inheriting it."""
    server._muf_render_ewma = None
    server._MUF_RASTERIZE_TIMEOUT = 0
    assert server._muf_timeout() == server.PHASE2_TIMEOUT_S


def test_timeout_never_drops_below_the_floor_on_a_fast_box():
    server._muf_render_ewma = 0.6          # x86: 4 x 0.6 = 2.4 s
    assert server._muf_timeout() == server.PHASE2_TIMEOUT_S


def test_timeout_scales_with_the_measured_render():
    server._muf_render_ewma = 20.0         # ARMv6-ish
    assert server._muf_timeout() == 80     # 4 x 20


def test_timeout_is_capped():
    server._muf_render_ewma = 900.0
    assert server._muf_timeout() == server.PHASE2_TIMEOUT_MAX_S


def test_timeout_is_always_an_int():
    for ewma in (None, 0.0, 13.7, 27.3, 1e9):
        server._muf_render_ewma = ewma
        assert isinstance(server._muf_timeout(), int)


def test_ewma_records_only_successful_renders(monkeypatch):
    server._muf_render_ewma = None

    class _Fail(_FakePopen):
        def __init__(self, argv, **kw):
            super().__init__(argv, **kw)
            self.returncode = 1
            self.out = b""

    _fake_popen(monkeypatch, _Fail)
    server._rasterize_muf(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    assert server._muf_render_ewma is None


def test_ewma_is_seeded_then_smoothed():
    server._muf_render_ewma = None
    server._record_muf_render(30.0)
    assert server._muf_render_ewma == pytest.approx(30.0)
    server._record_muf_render(10.0)
    # A single fast render must not collapse the budget in one step.
    assert 10.0 < server._muf_render_ewma < 30.0


@pytest.mark.parametrize("bad", [None, "x", 0.0, -5.0, float("nan"),
                                 float("inf")])
def test_ewma_rejects_garbage_samples(bad):
    server._muf_render_ewma = 25.0
    server._record_muf_render(bad)
    assert server._muf_render_ewma == 25.0


def test_the_budget_is_never_persisted():
    """RAM only. Persisting it would make the rasterize tests non-idempotent
    across runs and let one pathological boot raise the budget forever."""
    src = Path(server.__file__).read_text()
    assert "_muf_render_ewma" not in server._PERSIST_KEYS
    # It must not be reachable from the persistence code paths at all.
    persisted = re.search(r"def _persist\(key\):(.*?)\ndef ", src, re.DOTALL)
    assert persisted and "_muf_render_ewma" not in persisted.group(1)
    assert "ewma" not in str(server._read_manifest())
