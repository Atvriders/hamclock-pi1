"""Phase 1b acceptance: smoothscale and Font.render call counts each drop
>=50% vs the Phase 1 baseline on a 30-frame headless run.

The test exercises the real draw_* functions (with their existing rect-based
signatures) for the panels that Phase 1b targets:
  - draw_header / draw_status_bar (item 8: cached time/SFI/Kp strings)
  - draw_open_bands (item 7: cached open/closed strings)
  - draw_band_activity (item 6: pre-allocated counts)
  - draw_solar / draw_geomag / draw_xray (item 9: cached solar snapshot)
  - draw_panel and the layout dance (item 5: cached layout rects)

A counting smoothscale wrapper and a _CountingFont proxy (the Font class is
a C type whose render method cannot be monkey-patched directly) measure the
two reduction targets. A small synthetic data object feeds the draw paths.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")
BASELINE = REPO / "tests" / "data" / "phase1_baseline.json"


class _SyntheticData:
    """Minimal stand-in for HamClockData. last_data_refresh is *stable* — the
    semantics of Phase 1b are that values may keep arriving from the network
    or mutate at higher rates than the data-refresh tick, but Phase 1b's
    caches recompute only when last_data_refresh actually advances."""

    def __init__(self):
        self.solar = {
            'sfi': 145, 'kIndex': 3, 'ssn': 87, 'aIndex': 12,
            'xray': 'B2.4', 'solarWind': 412, 'bz': -3,
            'geomagField': 'Quiet', 'signalNoise': 'S2-S3', 'fof2': 9.8,
        }
        self.bands = {
            '80m-40m': {'day': 'Good',  'night': 'Fair'},
            '30m-20m': {'day': 'Good',  'night': 'Good'},
            '17m-15m': {'day': 'Fair',  'night': 'Poor'},
            '12m-10m': {'day': 'Poor',  'night': 'N/A'},
        }
        self.dxspots = [
            {'frequency': 14.205, 'band': '20m', 'dxCall': 'K1AB',
             'spotter': 'W2XYZ', 'time': '14:01'},
            {'frequency': 7.205,  'band': '40m', 'dxCall': 'JA1XYZ',
             'spotter': 'VK4ABC', 'time': '14:02'},
            {'frequency': 21.250, 'band': '15m', 'dxCall': 'DL1XX',
             'spotter': 'F5XYZ',  'time': '14:03'},
        ]
        self.images = {}
        self.image_fetched_at = {}
        self.last_data_refresh = 1700000000.0
        self.last_image_refresh = 1700000000.0

    def mutate_inflight(self, frame):
        """Simulate the real production case: between data-refresh ticks the
        underlying dicts can still be mutated by the background fetcher (a
        partial response, an in-flight DX spot, an SDR reading bumping
        kIndex). last_data_refresh stays put — Phase 1b's whole point is
        that these mid-tick mutations don't re-pay format / _safe / list-comp
        cost on every frame."""
        # solar values walk up and down each frame
        self.solar['kIndex'] = 3 + (frame % 5)
        self.solar['sfi'] = 140 + (frame % 11)
        self.solar['ssn'] = 80 + (frame % 13)
        self.solar['aIndex'] = 10 + (frame % 7)
        self.solar['xray'] = 'B%d.%d' % (1 + frame % 5, frame % 10)
        self.solar['solarWind'] = 400 + frame
        # bands flip Good/Fair each frame
        self.bands['80m-40m']['day'] = 'Good' if frame % 2 == 0 else 'Fair'
        self.bands['17m-15m']['day'] = 'Fair' if frame % 2 == 0 else 'Good'
        # dxspots length toggles
        if frame % 2 == 0:
            self.dxspots = self.dxspots[:2]
        else:
            self.dxspots = self.dxspots[:3]


class _CountingFont:
    """Proxy that counts Font.render calls (Font is a C type — cannot be
    monkey-patched at the class level)."""

    def __init__(self, inner, counter):
        self._inner = inner
        self._counter = counter

    def render(self, *a, **kw):
        self._counter[0] += 1
        return self._inner.render(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _run_30_frame_headless():
    """Reload hamclock_pygame with the dummy SDL driver and run 30 frames
    of the panels Phase 1b targets; return a dict of call counts."""
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["HAMCLOCK_DEBUG"] = "1"
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))

    import pygame
    pygame.display.init()
    pygame.font.init()

    import hamclock_pygame as hp

    # Clear Phase-1 caches so a clean run starts with empty state.
    hp._scaled_cache.clear()
    hp._glyph_cache.clear()
    # Reset any Phase 1b caches if they exist.
    for cache_attr in ("_layout_cache", "_open_bands_cache",
                       "_strfmt_cache", "_solar_snapshot"):
        cache = getattr(hp, cache_attr, None)
        if isinstance(cache, dict):
            for k in list(cache.keys()):
                cache[k] = None if k in ("size", "ts", "key") else cache[k]

    screen = pygame.display.set_mode((1440, 900))
    raw_fonts = hp._make_fonts()

    render_counter = [0]
    fonts = {k: _CountingFont(v, render_counter)
             for k, v in raw_fonts.items()}

    theme = hp.THEMES["kstate"]
    data = _SyntheticData()

    counts = {"smoothscale": 0, "font_render": 0}
    real_ss = pygame.transform.smoothscale

    def cnt_ss(*a, **kw):
        counts["smoothscale"] += 1
        return real_ss(*a, **kw)

    pygame.transform.smoothscale = cnt_ss
    try:
        # Pre-warm with one frame so first-frame cold-misses do not skew
        # the comparison (the baseline and the new run both pay them).
        # The 30-frame count includes those first-frame misses just like
        # the baseline does, so we do NOT subtract them.
        # Get cached layout once (the layout cache populates on first call).
        layout = hp._get_layout(screen.get_size())
        data_ts = data.last_data_refresh
        for frame in range(30):
            # Background fetcher mutates the dicts in place between refresh
            # ticks. Phase 1b caches must ignore these mutations until ts
            # advances; the baseline (no Phase 1b) re-reads them every frame
            # and produces fresh distinct strings → many glyph cache misses.
            data.mutate_inflight(frame)

            # Header / status (item 8 targets — pre-formatted UTC/LOC + status).
            hp.draw_header(screen, layout["header"], "N0CALL",
                           fonts, theme, data=data)
            hp.draw_status_bar(screen, layout["status"], data, fonts, theme)

            # Left column panels (items 5, 9 targets).
            hp.draw_solar(screen, layout["solar"], data.solar,
                          fonts, theme, data_refresh_ts=data_ts)
            hp.draw_bands(screen, layout["bands"], data.bands, fonts, theme)
            hp.draw_geomag(screen, layout["geomag"], data.solar,
                           fonts, theme, data_refresh_ts=data_ts)
            hp.draw_xray(screen, layout["xray"], data.solar,
                         fonts, theme, data_refresh_ts=data_ts)
            hp.draw_open_bands(screen, layout["open_bands"], data.bands,
                               fonts, theme, data_refresh_ts=data_ts)

            # Band activity (item 6 target — pre-allocated counts list).
            hp.draw_band_activity(screen, layout["band_activity"],
                                  data.dxspots, fonts, theme)

        counts["font_render"] = render_counter[0]
    finally:
        pygame.transform.smoothscale = real_ss
    return counts


def test_baseline_recorded():
    assert BASELINE.exists(), (
        "Phase 1 baseline missing — record tests/data/phase1_baseline.json "
        "BEFORE applying Phase 1b changes (run the harness against HEAD~ "
        "and commit the JSON)."
    )


def test_smoothscale_drops_at_least_50pct():
    base = json.loads(BASELINE.read_text())
    new = _run_30_frame_headless()
    assert new["smoothscale"] <= base["smoothscale"] * 0.5, (
        "smoothscale %d > 50%% of baseline %d"
        % (new["smoothscale"], base["smoothscale"]))


def test_font_render_drops_at_least_50pct():
    base = json.loads(BASELINE.read_text())
    new = _run_30_frame_headless()
    assert new["font_render"] <= base["font_render"] * 0.5, (
        "font.render %d > 50%% of baseline %d"
        % (new["font_render"], base["font_render"]))
