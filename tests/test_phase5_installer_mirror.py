"""Dual-repo rule: the public download URL must serve a byte-identical
copy of offline-install.sh (per project_pi1_installer_dual_repo.md)."""
import hashlib
from pathlib import Path

SRC = Path("/home/kasm-user/hamclock-pi1/offline-install.sh")
MIRROR = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def test_mirror_exists():
    assert MIRROR.exists(), f"mirror missing: {MIRROR}"

def test_mirror_is_byte_identical():
    assert _sha(SRC) == _sha(MIRROR), "mirror has drifted from source"

def test_mirror_default_is_pygame():
    text = MIRROR.read_text()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("KIOSK_MODE=") and ";;" not in s and "esac" not in s:
            assert s.startswith('KIOSK_MODE="pygame"')
            return
    raise AssertionError("no default KIOSK_MODE line in mirror")
