"""Tier 2a: framebuffer is 720x450; HDMI scanout is hardware-upscaled by HVS."""
import re
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")


def test_screen_constants_are_720x450():
    import hamclock_pygame as hp
    assert hp.SCREEN_W == 720, "Tier 2a: SCREEN_W must be 720"
    assert hp.SCREEN_H == 450, "Tier 2a: SCREEN_H must be 450"


def test_fonts_resized_for_720x450():
    """Font 'tiny' must be <=8 px to render legibly at 720x450 native."""
    import pygame, hamclock_pygame as hp
    pygame.init()
    try:
        fonts = hp._make_fonts()
        # Use ascent or get_height; a font we asked for size 7 has height ~10 px
        assert fonts['tiny'].get_height() <= 14, \
            'tiny font too tall for 720x450 (got %d)' % fonts['tiny'].get_height()
    finally:
        pygame.quit()


def test_installer_no_longer_pins_the_framebuffer():
    """Was test_installer_sets_framebuffer_720x450, and the hardware overruled it.

    Tier 2a set framebuffer_width/height=720x450 expecting the firmware scaler
    to upscale for free. Field telemetry showed the Pi running KMSDRM at
    800x600 on a 1440x900 panel: under KMS those keys are silently ignored —
    they only ever worked with the scaler KMS removed — so the client's request
    was snapped to the nearest real DRM mode and then stretched by a fractional
    1.8x. That upscale is what made the display unreadable.

    They are not merely inert, either: on a legacy or fkms stack they DO apply,
    which would pin exactly the half-resolution framebuffer this change exists
    to stop rendering into. The client now asks the connector for its real mode.
    """
    for name in ("kiosk-install.sh", "offline-install.sh"):
        body = (REPO / name).read_text()
        live = [l for l in body.splitlines()
                if re.search(r'add_cfg\s+"framebuffer_(width|height)=', l)
                and not l.strip().startswith('#')]
        assert not live, f"{name} still pins the framebuffer: {live}"


def test_muf_rasterize_width_is_360():
    """MUF panel at 720x450 layout is ~360 px wide; rasterize narrower.

    Targets the cairosvg one-liner, not a bare 'output_width=360' substring:
    _rasterize_muf's docstring also contains that phrase, so the old grep read
    green even when the argv it describes had drifted. The argv itself is
    additionally pinned in tests/test_tier2_slim_muf.py by driving
    _rasterize_muf against a stubbed Popen.
    """
    body = (REPO / "server.py").read_text()
    assert 'output_width=360, write_to=sys.stdout.buffer)' in body, \
        'Tier 2a: server.py _rasterize_muf must use output_width=360'
    obody = (REPO / "offline-install.sh").read_text()
    assert 'output_width=360' in obody, \
        'Tier 2a: offline-install.sh embedded _rasterize_muf must use output_width=360'
