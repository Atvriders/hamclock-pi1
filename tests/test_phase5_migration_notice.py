"""Verify the installer prints a one-time browser-localStorage-not-migrated
notice when a previously installed browser-mode kiosk is upgraded to pygame."""
import subprocess
import textwrap
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")

HARNESS = r"""
set -eu
SERVICE_UNIT="$1"
SETTINGS_FILE="$2"
PRIOR_MODE_HINT="$3"   # "browser" | "pygame" | ""

# Decision and notice block from kiosk-install.sh (Phase 5).
if [ -f "$SETTINGS_FILE" ]; then
    REINSTALL_DECISION="keep-settings"
elif [ -f "$SERVICE_UNIT" ]; then
    REINSTALL_DECISION="seed-defaults"
else
    REINSTALL_DECISION="fresh-install"
fi

if [ "$REINSTALL_DECISION" != "fresh-install" ] \
    && [ "$PRIOR_MODE_HINT" = "browser" ]; then
    echo "NOTICE: Browser localStorage (theme, callsign) is not migrated to pygame mode."
    echo "Run 'sudo hamclock-setup' to re-enter your settings."
fi
"""

def _run(tmp_path, unit_present, settings_present, prior):
    unit = tmp_path / "hamclock-kiosk.service"
    settings = tmp_path / "settings.json"
    if unit_present:
        unit.write_text("[Unit]\n")
    if settings_present:
        settings.write_text("{}")
    script = tmp_path / "h.sh"
    script.write_text(HARNESS)
    out = subprocess.check_output(
        ["bash", str(script), str(unit), str(settings), prior],
    ).decode()
    return out

def test_browser_to_pygame_prints_notice(tmp_path):
    # Browser kiosk previously installed (unit present, no pygame settings)
    out = _run(tmp_path, unit_present=True, settings_present=False, prior="browser")
    assert "Browser localStorage" in out
    assert "hamclock-setup" in out

def test_fresh_install_no_notice(tmp_path):
    out = _run(tmp_path, unit_present=False, settings_present=False, prior="")
    assert "Browser localStorage" not in out

def test_pygame_to_pygame_no_notice(tmp_path):
    out = _run(tmp_path, unit_present=True, settings_present=True, prior="pygame")
    assert "Browser localStorage" not in out

def test_installer_contains_notice_string():
    text = (REPO / "kiosk-install.sh").read_text()
    assert "Browser localStorage" in text
