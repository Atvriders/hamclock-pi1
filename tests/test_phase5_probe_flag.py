"""Phase 5 unblocker: --probe flag and gather_pi1_evidence.sh wiring.

These three pieces let an operator on real Pi 1B hardware produce the
two deferred Phase 0 / Phase 2 deliverables (docs/sdl-backend.md and
docs/muf-source.md) without needing a hamclock-pi1 clone.
"""
import hashlib
import subprocess
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")
MIRROR = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")

GATHER = REPO / "scripts" / "gather_pi1_evidence.sh"
KIOSK = REPO / "kiosk-install.sh"
OFFLINE = REPO / "offline-install.sh"


def test_gather_script_present_and_executable():
    assert GATHER.exists(), f"missing: {GATHER}"
    assert GATHER.stat().st_mode & 0o111, "gather script not executable"


def test_gather_script_bash_clean():
    r = subprocess.run(
        ["bash", "-n", str(GATHER)], capture_output=True, text=True
    )
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_probe_flag_in_kiosk_install():
    text = KIOSK.read_text()
    assert "--probe" in text, "kiosk-install.sh missing --probe flag"
    assert "gather_pi1_evidence.sh" in text, \
        "kiosk-install.sh --probe doesn't dispatch to gather script"


def test_probe_flag_in_offline_install():
    text = OFFLINE.read_text()
    assert "--probe" in text, "offline-install.sh missing --probe flag"
    # offline-install is curl-piped, so it embeds the probe inline rather
    # than dispatching to a script file -- look for the inline markers.
    assert "PROBE_ONLY=1" in text
    assert "muf-subprocess-timeout-s:" in text, \
        "offline-install.sh --probe doesn't emit muf-subprocess-timeout-s line"


def test_probe_flag_in_mirror():
    text = MIRROR.read_text()
    assert "--probe" in text, "dual-repo mirror missing --probe flag"


def test_mirror_byte_identical():
    src = hashlib.sha256(OFFLINE.read_bytes()).hexdigest()
    mir = hashlib.sha256(MIRROR.read_bytes()).hexdigest()
    assert src == mir, "dual-repo mirror has drifted from offline-install.sh"
