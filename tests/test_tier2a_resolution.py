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


def test_installer_sets_framebuffer_720x450():
    body = (REPO / "kiosk-install.sh").read_text()
    assert 'framebuffer_width=720' in body
    assert 'framebuffer_height=450' in body
    obody = (REPO / "offline-install.sh").read_text()
    assert 'framebuffer_width=720' in obody
    assert 'framebuffer_height=450' in obody


def test_muf_rasterize_width_is_360():
    """MUF panel at 720x450 layout is ~360 px wide; rasterize narrower."""
    body = (REPO / "server.py").read_text()
    assert 'output_width=360' in body, \
        'Tier 2a: server.py _rasterize_muf must use output_width=360'
    obody = (REPO / "offline-install.sh").read_text()
    assert 'output_width=360' in obody, \
        'Tier 2a: offline-install.sh embedded _rasterize_muf must use output_width=360'
