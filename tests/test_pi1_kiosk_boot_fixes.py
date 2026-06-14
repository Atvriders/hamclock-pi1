"""Pin the three fixes that unblock Pi 1B kiosk auto-takeover at boot."""
import inspect
import re
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")


def test_display_init_uses_driver_ladder():
    """hamclock_pygame.main must NOT hard-fail when fbcon is missing.
    A ladder over fbcon/kmsdrm/x11/dummy must exist."""
    import hamclock_pygame as hp
    assert hasattr(hp, '_init_display'), \
        'Pi 1B unblock: _init_display() helper required'
    src = inspect.getsource(hp._init_display)
    for drv in ('fbcon', 'kmsdrm', 'x11', 'dummy'):
        assert drv in src, '_init_display ladder missing %s' % drv


def test_kiosk_service_chvt_7_in_both_installers():
    for p in (REPO / 'kiosk-install.sh', REPO / 'offline-install.sh'):
        body = p.read_text()
        assert 'ExecStartPre=/usr/bin/chvt 7' in body, \
            'Pi 1B unblock: %s missing chvt 7' % p.name


def test_installers_do_not_hard_set_sdl_videodriver_fbcon():
    """The kiosk.sh launcher used to export SDL_VIDEODRIVER=fbcon, defeating
    the Python ladder. Both installers must drop the hard export."""
    for p in (REPO / 'kiosk-install.sh', REPO / 'offline-install.sh'):
        body = p.read_text()
        assert 'export SDL_VIDEODRIVER=fbcon' not in body, \
            '%s still hard-exports SDL_VIDEODRIVER=fbcon' % p.name


def test_installers_seed_default_settings_json():
    for p in (REPO / 'kiosk-install.sh', REPO / 'offline-install.sh'):
        body = p.read_text()
        assert '/etc/hamclock-lite/settings.json' in body
        # Must have a default seed block (any sentinel: N0CALL or kstate as theme)
        assert ('"callsign"' in body and '"theme"' in body and
                ('"N0CALL"' in body or '"timezone"' in body)), \
            '%s missing default settings.json seed' % p.name
