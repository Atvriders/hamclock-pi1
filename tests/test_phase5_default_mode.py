"""Verify both installers default to pygame mode after Phase 5."""
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")
MIRROR = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")

def _default_line(text: str) -> str:
    # Find the first uncommented assignment of KIOSK_MODE that lacks an
    # explicit override (so flag-handling lines like KIOSK_MODE="pygame" ;;
    # are ignored).
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("KIOSK_MODE=") and ";;" not in s and "esac" not in s:
            return s
    raise AssertionError("no default KIOSK_MODE line found")

def test_kiosk_installer_default_is_pygame():
    text = (REPO / "kiosk-install.sh").read_text()
    assert _default_line(text).startswith('KIOSK_MODE="pygame"')

def test_offline_installer_default_is_pygame():
    text = (REPO / "offline-install.sh").read_text()
    assert _default_line(text).startswith('KIOSK_MODE="pygame"')

def test_offline_installer_contains_reinstall_block():
    text = (REPO / "offline-install.sh").read_text()
    assert 'REINSTALL_DECISION="keep-settings"' in text
    assert 'REINSTALL_DECISION="seed-defaults"' in text
    assert 'REINSTALL_DECISION="fresh-install"' in text
