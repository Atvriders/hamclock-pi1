"""Tier 1c: verify OS tuning blocks are present in both installers."""
from pathlib import Path

REPO = Path("/home/kasm-user/hamclock-pi1")
KIOSK = REPO / "kiosk-install.sh"
OFFLINE = REPO / "offline-install.sh"

REQUIRED_TOKENS = [
    "MALLOC_ARENA_MAX=1",
    "PYTHONOPTIMIZE=1",
    "PYTHONDONTWRITEBYTECODE=1",
    "compileall",
    "systemctl mask bluetooth",
    "gpu_mem=16",
    "dtparam=audio=off",
    "hdmi_blanking=0",
    "vt.global_cursor_default=0",
    "fsck.mode=skip",
    "Storage=volatile",
    "RuntimeMaxUse=32M",
    "noatime,commit=60",
    "vm.swappiness=10",
    "vm.dirty_ratio=10",
]

def test_kiosk_install_has_tier1c_tokens():
    body = KIOSK.read_text()
    missing = [t for t in REQUIRED_TOKENS if t not in body]
    assert not missing, f"kiosk-install.sh missing: {missing}"

def test_offline_install_has_tier1c_tokens():
    body = OFFLINE.read_text()
    missing = [t for t in REQUIRED_TOKENS if t not in body]
    assert not missing, f"offline-install.sh missing: {missing}"
