"""Verify Phase 5 carries the installer changes mandated by Phase 0
(SDL backend decision) and Phase 2 (cairosvg subprocess timeout)."""
import json
import re
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")
DOCS = REPO / "docs"

def _sdl_decision() -> str:
    """docs/sdl-backend.md must declare exactly one of:
    sdl-backend: fbcon | kmsdrm | xinit"""
    text = (DOCS / "sdl-backend.md").read_text()
    m = re.search(r"^sdl-backend:\s*(fbcon|kmsdrm|xinit)\s*$", text, re.M)
    assert m, "docs/sdl-backend.md missing 'sdl-backend: <fbcon|kmsdrm|xinit>' line"
    return m.group(1)

def _muf_timeout() -> int:
    """docs/muf-source.md must declare 'muf-subprocess-timeout-s: <int>'."""
    text = (DOCS / "muf-source.md").read_text()
    m = re.search(r"^muf-subprocess-timeout-s:\s*(\d+)\s*$", text, re.M)
    assert m, "docs/muf-source.md missing 'muf-subprocess-timeout-s: <int>' line"
    return int(m.group(1))

def test_installer_carries_sdl_driver():
    drv = _sdl_decision()
    for installer in ("kiosk-install.sh", "offline-install.sh"):
        text = (REPO / installer).read_text()
        if drv == "fbcon":
            # Default kiosk.sh already exports fbcon; just confirm nothing
            # downgrades it.
            assert "SDL_VIDEODRIVER=fbcon" in text, f"{installer} lost fbcon export"
        elif drv == "kmsdrm":
            assert "SDL_VIDEODRIVER=kmsdrm" in text
            assert "gpu_mem=128" in text
            assert "dtoverlay=vc4-fkms-v3d" in text
        elif drv == "xinit":
            assert "xinit" in text and "matchbox-window-manager" in text

def test_installer_carries_muf_timeout():
    timeout = _muf_timeout()
    for installer in ("kiosk-install.sh", "offline-install.sh"):
        text = (REPO / installer).read_text()
        # Installer either inlines PHASE2_TIMEOUT_S=<n> or sed-patches
        # server.py to that value.
        assert (
            f"PHASE2_TIMEOUT_S={timeout}" in text
            or f"PHASE2_TIMEOUT_S = {timeout}" in text
        ), f"{installer} missing PHASE2_TIMEOUT_S={timeout}"

def test_phase5_blocked_until_phase0_and_phase2_records_exist():
    assert (DOCS / "sdl-backend.md").exists(), "Phase 0 record missing — Phase 5 cannot ship"
    assert (DOCS / "muf-source.md").exists(), "Phase 2 record missing — Phase 5 cannot ship"
