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
            # The original expectation here (gpu_mem=128 + dtoverlay=vc4-fkms-v3d)
            # was written before any hardware existed. The first real diagnostics
            # report contradicts it: a Pi 1B runs kmsdrm at 32bpp with gpu_mem=16
            # and a 1.2 ms median frame time, on real KMS under kernel 6.12 /
            # trixie. fkms is a different, deprecated stack — forcing it there is
            # at best a no-op and at worst breaks a working display — and raising
            # gpu_mem would surrender ~48 MB of a 486 MB box for no measured gain.
            # See docs/sdl-backend.md for the report this is derived from.
            #
            # What actually matters is that the driver ladder can still reach
            # kmsdrm: fbcon is tried first and is unavailable on this SDL build,
            # so the ladder is the thing that makes the kiosk work at all.
            assert "kmsdrm" in text, f"{installer} lost the kmsdrm rung"
            assert "gpu_mem=128" not in text, (
                f"{installer} sets gpu_mem=128; measured hardware runs kmsdrm "
                f"fine at gpu_mem=16 and needs the RAM more")
        elif drv == "xinit":
            assert "xinit" in text and "matchbox-window-manager" in text

def test_installer_carries_muf_timeout():
    timeout = _muf_timeout()
    for installer in ("kiosk-install.sh", "offline-install.sh"):
        text = (REPO / installer).read_text()
        # Installer either inlines PHASE2_TIMEOUT_S=<n> or sed-patches
        # server.py to that value.
        inlined = (f"PHASE2_TIMEOUT_S={timeout}" in text
                   or f"PHASE2_TIMEOUT_S = {timeout}" in text)
        # kiosk-install.sh carries the value a third way, which the original
        # rule did not anticipate: it COPIES server.py verbatim rather than
        # embedding or sed-patching it. The requirement is that the INSTALLED
        # server ends up with the recorded timeout, and copying the file that
        # defines it satisfies that exactly as well as inlining a literal.
        copies_server = 'cp "$SCRIPT_DIR/$_hc_file"' in text or \
                        'cp "$SCRIPT_DIR/server.py"' in text
        if copies_server and not inlined:
            src = (REPO / "server.py").read_text()
            assert f"PHASE2_TIMEOUT_S = {timeout}" in src, (
                f"{installer} copies server.py, but server.py does not define "
                f"PHASE2_TIMEOUT_S = {timeout}")
        else:
            assert inlined, f"{installer} missing PHASE2_TIMEOUT_S={timeout}"

def test_phase5_blocked_until_phase0_and_phase2_records_exist():
    assert (DOCS / "sdl-backend.md").exists(), "Phase 0 record missing — Phase 5 cannot ship"
    assert (DOCS / "muf-source.md").exists(), "Phase 2 record missing — Phase 5 cannot ship"
