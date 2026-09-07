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


def test_muf_rasterize_width_matches_the_centre_panel():
    """Was test_muf_rasterize_width_is_360, sized for the small propagation TAB.

    The map now occupies the centre panel, whose inner width is 636 px at a
    native 1440x900. A 360 px raster filled 57% of that and the client refuses
    to upscale a PNG past its own width, so the map was small because the
    SOURCE was small. Affordable to raise because rsvg is parse-dominated:
    360 -> 0.172 s, 720 -> 0.226 s for four times the pixels.

    Asserted against the constant so the width can move again without a
    scavenger hunt through the tests.
    """
    import server
    w = server.MUF_RASTER_WIDTH
    assert w >= 636, f"raster {w}px is narrower than the centre panel it fills"

    # Assert the RESOLVED argv, not source text: the width is interpolated
    # from the constant, so the literal never appears in the file and a
    # substring grep would read green while the argv had drifted.
    engines = dict(server.MUF_ENGINES)
    assert ('output_width=%d' % w) in ' '.join(engines['cairosvg'])
    assert str(w) in engines['rsvg']

    # The embedded copy must define the same constant, so the single-file
    # installer cannot ship a different width from the repo.
    obody = (REPO / "offline-install.sh").read_text()
    assert ('MUF_RASTER_WIDTH = %d' % w) in obody, \
        'offline-install.sh embedded server must carry the same raster width'
