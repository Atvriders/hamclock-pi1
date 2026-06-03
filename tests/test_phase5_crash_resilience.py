"""Phase 5 must NOT regress the crash-resilience tokens in any installer.

Architectural note: kiosk-install.sh is a thin launcher that writes the
systemd unit + kiosk.sh while-true wrapper; it does NOT embed the Python
client source. The render-loop consecutive_errors guard therefore lives
in the embedded Python inside offline-install.sh and its dual-repo
mirror, not in kiosk-install.sh.
"""
import re
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")
KIOSK_LAUNCHER = REPO / "kiosk-install.sh"
FULL_INSTALLERS = [
    REPO / "offline-install.sh",
    Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh"),
]
ALL_INSTALLERS = [KIOSK_LAUNCHER] + FULL_INSTALLERS

SYSTEMD_TOKENS = [
    r"Restart=always",
    r"StartLimitIntervalSec=0",
    r"OOMScoreAdjust=-250",
]
LAUNCHER_LOOP_TOKEN = r"while\s+true;\s*do"
EMBEDDED_GUARD_TOKEN = r"consecutive_errors"


def test_systemd_tokens_in_all_installers():
    """Restart=always, StartLimitIntervalSec=0, OOMScoreAdjust=-250 must
    survive in every installer's systemd unit heredoc."""
    missing = []
    for path in ALL_INSTALLERS:
        if not path.exists():
            missing.append((str(path), "FILE MISSING"))
            continue
        body = path.read_text()
        for tok in SYSTEMD_TOKENS:
            if not re.search(tok, body):
                missing.append((str(path), tok))
    assert not missing, \
        "Systemd resilience tokens missing: " + repr(missing)


def test_while_true_launcher_loop_in_all_installers():
    """The kiosk.sh while-true relaunch wrapper exists in every installer."""
    missing = []
    for path in ALL_INSTALLERS:
        if not path.exists():
            missing.append((str(path), "FILE MISSING"))
            continue
        if not re.search(LAUNCHER_LOOP_TOKEN, path.read_text()):
            missing.append((str(path), LAUNCHER_LOOP_TOKEN))
    assert not missing, \
        "while-true relaunch loop missing: " + repr(missing)


def test_consecutive_errors_guard_in_full_installers_only():
    """The render-loop consecutive_errors guard lives in the embedded
    hamclock_pygame.py source — inside the full installers' heredocs."""
    missing = []
    for path in FULL_INSTALLERS:
        if not path.exists():
            missing.append((str(path), "FILE MISSING"))
            continue
        if not re.search(EMBEDDED_GUARD_TOKEN, path.read_text()):
            missing.append((str(path), EMBEDDED_GUARD_TOKEN))
    assert not missing, \
        "consecutive_errors render guard missing from full installers: " + repr(missing)


def test_no_restart_on_failure_anywhere():
    """The earlier session replaced Restart=on-failure with Restart=always
    everywhere; if it crept back, we have regressed."""
    for path in ALL_INSTALLERS:
        if not path.exists():
            continue
        assert "Restart=on-failure" not in path.read_text(), \
            "Restart=on-failure crept back into %s" % path
