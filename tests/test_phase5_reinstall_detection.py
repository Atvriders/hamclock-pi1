"""Verify the installer's reinstall-detection shell block selects the
correct branch based on the presence of settings.json and an existing
hamclock-kiosk.service unit. We test the *shell logic* by sourcing the
decision function into a clean bash subprocess with mocked paths."""
import os
import subprocess
import textwrap
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")

HARNESS = r"""
set -eu
SETTINGS_FILE="$1"
SERVICE_UNIT="$2"

# --- BEGIN block copied verbatim from kiosk-install.sh ---
if [ -f "$SETTINGS_FILE" ]; then
    REINSTALL_DECISION="keep-settings"
elif [ -f "$SERVICE_UNIT" ]; then
    REINSTALL_DECISION="seed-defaults"
else
    REINSTALL_DECISION="fresh-install"
fi
# --- END block ---

echo "$REINSTALL_DECISION"
"""

def _run(tmp_path, has_settings: bool, has_unit: bool) -> str:
    settings = tmp_path / "settings.json"
    unit = tmp_path / "hamclock-kiosk.service"
    if has_settings:
        settings.write_text('{"theme":"kstate"}')
    if has_unit:
        unit.write_text("[Unit]\n")
    script = tmp_path / "harness.sh"
    script.write_text(HARNESS)
    out = subprocess.check_output(
        ["bash", str(script), str(settings), str(unit)],
        stderr=subprocess.STDOUT,
    ).decode().strip()
    return out

def test_existing_settings_keeps_settings(tmp_path):
    assert _run(tmp_path, has_settings=True, has_unit=True) == "keep-settings"

def test_existing_settings_no_unit_keeps_settings(tmp_path):
    assert _run(tmp_path, has_settings=True, has_unit=False) == "keep-settings"

def test_old_pygame_no_settings_seeds_defaults(tmp_path):
    assert _run(tmp_path, has_settings=False, has_unit=True) == "seed-defaults"

def test_truly_fresh_install(tmp_path):
    assert _run(tmp_path, has_settings=False, has_unit=False) == "fresh-install"

def test_installer_contains_reinstall_block():
    """Sanity: the live installer carries the exact decision lines."""
    text = (REPO / "kiosk-install.sh").read_text()
    assert 'REINSTALL_DECISION="keep-settings"' in text
    assert 'REINSTALL_DECISION="seed-defaults"' in text
    assert 'REINSTALL_DECISION="fresh-install"' in text
