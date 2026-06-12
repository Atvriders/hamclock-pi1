"""The probe script must exist, be executable, syntactically valid bash,
attempt set_mode + flip (not just init), and try all four drivers in order."""
import os
import stat
import subprocess

SCRIPT = "/home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh"


def test_probe_script_exists():
    assert os.path.isfile(SCRIPT), f"missing: {SCRIPT}"


def test_probe_script_is_executable():
    mode = os.stat(SCRIPT).st_mode
    assert mode & stat.S_IXUSR, "probe script must be chmod +x"


def test_probe_script_passes_bash_n():
    r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed: {r.stderr}"


def test_probe_script_tries_all_four_drivers_in_order():
    with open(SCRIPT) as f:
        body = f.read()
    pos_fbcon = body.find("'fbcon'")
    pos_kms = body.find("'kmsdrm'")
    pos_x11 = body.find("'x11'")
    pos_dummy = body.find("'dummy'")
    assert pos_fbcon != -1 and pos_kms != -1 and pos_x11 != -1 and pos_dummy != -1
    assert pos_fbcon < pos_kms < pos_x11 < pos_dummy


def test_probe_script_calls_set_mode_and_flip():
    with open(SCRIPT) as f:
        body = f.read()
    assert "set_mode" in body, "probe must call set_mode (init alone gives false positives)"
    assert "pygame.display.flip" in body or ".flip()" in body, "probe must call flip"
    assert "FULLSCREEN" in body
    assert "720, 450" in body


def test_probe_script_logs_keyboard_layout():
    with open(SCRIPT) as f:
        body = f.read()
    assert "/etc/default/keyboard" in body, "must log keyboard layout for non-US debug context"


def test_probe_script_checks_dri_card0_for_kmsdrm():
    with open(SCRIPT) as f:
        body = f.read()
    assert "/dev/dri/card0" in body
