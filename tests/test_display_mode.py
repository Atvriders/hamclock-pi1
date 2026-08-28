"""Render at the panel's own resolution instead of letting KMS snap us.

Field failure (reported 2026-08-26): "everything is pixel like and hurts your
eyes to read". Telemetry from the Pi showed SDL_VIDEODRIVER=KMSDRM at 800x600
on a 1440x900 panel. The client asked for 720x450, which is not a real DRM mode
(the legacy framebuffer_width/height knobs only worked with the firmware scaler
KMS removed), so SDL snapped to the nearest offered mode — 800x600 — and the
panel then stretched that 4:3 image across a 16:10 screen at a fractional 1.8x.
Every source pixel landed on 1.8 destination pixels. No amount of font tuning or
antialiasing fixes a non-integer upscale; the fix is to stop upscaling.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp

COMMON = [(1440, 900), (1280, 800), (1280, 1024), (1024, 768), (800, 600), (640, 480)]


def test_the_reported_panel_renders_native():
    """1440x900 is 1.296M px, inside the budget — so no scaling at all."""
    assert hp._choose_display_mode((1440, 900), COMMON) == (1440, 900)


def test_native_is_preferred_whenever_it_fits():
    for native in [(1920, 1080), (1366, 768), (1024, 768), (800, 600)]:
        assert hp._choose_display_mode(native, COMMON + [native]) == native


def test_oversized_panels_fall_back_within_budget_keeping_shape():
    """A Pi 1B cannot drive 4K: 8.3M px is ~17x what it renders today. Fall
    back, but to the same ASPECT so the image is not distorted as well."""
    modes = [(3840, 2160), (1920, 1080), (1280, 720), (800, 600)]
    got = hp._choose_display_mode((3840, 2160), modes)
    assert got[0] * got[1] <= hp.DISPLAY_PIXEL_BUDGET
    assert abs(got[0] / got[1] - 3840 / 2160) <= hp._ASPECT_TOL, (
        f"{got} does not keep the panel's 16:9 shape")


def test_budget_is_never_exceeded():
    for native in [(3840, 2160), (2560, 1440), (5120, 2880)]:
        modes = [native, (1920, 1080), (1280, 720)]
        got = hp._choose_display_mode(native, modes)
        assert got[0] * got[1] <= hp.DISPLAY_PIXEL_BUDGET, f"{native} -> {got}"


def test_explicit_override_wins_when_offered():
    assert hp._choose_display_mode((1440, 900), COMMON,
                                   requested='1280x800') == (1280, 800)


def test_override_is_ignored_when_the_panel_cannot_do_it():
    """Honouring a mode no connector offers is how we got 800x600."""
    assert hp._choose_display_mode((1440, 900), COMMON,
                                   requested='999x999') == (1440, 900)


@pytest.mark.parametrize("bad", ['', 'auto', 'garbage', 'x', '0x0', '12x', None])
def test_malformed_overrides_do_not_crash(bad):
    got = hp._choose_display_mode((1440, 900), COMMON, requested=bad)
    assert got == (1440, 900)


def test_unknown_display_defers_to_sdl():
    """None means 'we learned nothing, let SDL choose' — not a guess."""
    assert hp._choose_display_mode(None, []) is None
    assert hp._choose_display_mode((0, 0), []) is None


def test_no_mode_list_still_returns_native():
    """list_modes() can return -1 (any mode ok) or nothing useful."""
    assert hp._choose_display_mode((1440, 900), []) == (1440, 900)


def test_init_display_accepts_the_override_argument():
    import inspect
    assert 'requested_mode' in inspect.signature(hp._init_display).parameters


def test_settings_contract_is_untouched():
    """display_mode is read opportunistically; load_settings must still return
    exactly its documented keys on a fresh install."""
    assert 'display_mode' not in hp.DEFAULT_SETTINGS
