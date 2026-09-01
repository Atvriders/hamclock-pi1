"""The MUF map must be rasterized by something a Pi 1B can actually run.

Field diagnostics (1.0.6, 2026-08-30) settled a question open since the start:

    rasterize_ok: 0,  rasterize_timeout: 3,  last_timeout_budget_s: 88,
    png_source: "none",  last_served: "none"

cairosvg never completed once, and was still timing out at an 88 s budget after
the escalation had widened it twice. docs/muf-source.md's own decision rule says
">30 s -> change source"; this is triple that.

Profiling put ~61% of cairosvg's time in an O(n^2) cssselect2 re-walk performed
once per <use> element, of which this map has 257. librsvg is C and has no
equivalent path. Measured here on the real KC2G SVG: cairosvg 1.204 s,
rsvg-convert 0.181 s — 6.7x, with visually identical output.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import server


def test_rsvg_is_tried_before_cairosvg():
    names = [n for n, _ in server.MUF_ENGINES]
    assert names[0] == 'rsvg', f"fastest engine must be first: {names}"
    assert 'cairosvg' in names, (
        "cairosvg must remain as a fallback — a Pi upgraded in place gets a new "
        "server.py without necessarily having librsvg2-bin, and falling back "
        "beats a permanently blank panel")
    assert names.index('rsvg') < names.index('cairosvg')


def test_every_engine_is_cpu_limited():
    """The render must never have the whole core; the loop needs its budget."""
    for name, argv in server.MUF_ENGINES:
        assert argv[0] == 'cpulimit', f"{name} is not throttled: {argv}"
        assert '-l' in argv, f"{name} has no cpulimit percentage"


def test_every_engine_renders_at_the_panel_width():
    for name, argv in server.MUF_ENGINES:
        assert '360' in ' '.join(argv), f"{name} does not render at 360px: {argv}"


def test_engine_used_is_reported():
    """The report that exposed this could not say which engine ran, because
    there was only one. With a ladder the next report must."""
    assert hasattr(server, '_MUF_ENGINE_USED')
    src = open(os.path.join(REPO, 'server.py')).read()
    assert "'engine_used'" in src, "/api/diagnostics must name the engine"


def test_output_must_be_a_real_png():
    """cpulimit can swallow a failing child's exit status, so a zero return
    code is not proof. Anything that is not a PNG is a failed render."""
    assert server._PNG_MAGIC == b'\x89PNG\r\n\x1a\x0a'
    src = open(os.path.join(REPO, 'server.py')).read()
    assert '_PNG_MAGIC' in src.split('def _rasterize_once', 1)[1][:2000], (
        "_rasterize_once does not verify the PNG magic")


@pytest.mark.parametrize("installer", ["kiosk-install.sh", "offline-install.sh"])
def test_installers_offer_librsvg_without_risking_the_rest(installer):
    src = open(os.path.join(REPO, installer)).read()
    assert 'librsvg2-bin' in src, f"{installer} does not install rsvg-convert"
    line = next(l for l in src.splitlines()
                if 'librsvg2-bin' in l and l.strip().startswith('sudo apt'))
    assert '|| true' in line, (
        "librsvg2-bin must not be able to fail the install: apt aborts the "
        "whole transaction on one unavailable package, and this is an "
        "optimisation, not a requirement")
    assert 'python3-pygame' not in line, (
        "must be its own apt line — sharing one with pygame reintroduces "
        "exactly the all-or-nothing failure the || true is guarding against")


@pytest.mark.parametrize("installer", ["kiosk-install.sh", "offline-install.sh"])
def test_installers_no_longer_pin_the_half_resolution_framebuffer(installer):
    """framebuffer_width/height are ignored under KMS, but DO apply on a
    legacy/fkms stack — where they would pin the very half-res framebuffer
    whose 1.8x upscale made the display unreadable."""
    src = open(os.path.join(REPO, installer)).read()
    # Match the actual invocation, not prose: these files embed server.py as a
    # heredoc, and its docstrings legitimately DISCUSS these knobs.
    live = [l for l in src.splitlines()
            if re.search(r'add_cfg\s+"framebuffer_(width|height)=', l)
            and not l.strip().startswith('#')]
    assert not live, f"{installer} still sets framebuffer_width: {live}"
