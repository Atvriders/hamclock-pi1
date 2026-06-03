"""Phase 2 installer-shape tests.

The installers are shell scripts, so we grep for the load-bearing apt-install
line and verify it is gated by KIOSK_MODE = pygame. This catches regressions
where someone refactors the installer and drops the cairosvg dependency.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KIOSK = REPO / 'kiosk-install.sh'
OFFLINE = REPO / 'offline-install.sh'
MIRROR = Path('/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh')


def _read(p):
    return p.read_text() if p.exists() else ''


def test_kiosk_install_pygame_apt_includes_cairosvg_and_cpulimit():
    text = _read(KIOSK)
    assert 'python3-cairosvg' in text, (
        'Phase 2: kiosk-install.sh must apt-install python3-cairosvg in pygame mode.'
    )
    assert 'cpulimit' in text, (
        'Phase 2: kiosk-install.sh must apt-install cpulimit in pygame mode.'
    )
    # Both must be inside the `if [ "$KIOSK_MODE" = "pygame" ]; then` block.
    # We approximate this by requiring cairosvg appears AFTER the pygame-mode
    # opening and BEFORE the next `elif` or `fi`.
    m = re.search(
        r'if \[ "\$KIOSK_MODE" = "pygame" \]; then(.*?)(elif|fi)',
        text, re.DOTALL,
    )
    assert m, 'pygame-mode apt block not found in kiosk-install.sh'
    block = m.group(1)
    assert 'python3-cairosvg' in block
    assert 'cpulimit' in block


def test_offline_install_embeds_rasterize_muf():
    text = _read(OFFLINE)
    assert 'def _rasterize_muf' in text, (
        'Phase 2: offline-install.sh embedded server.py must define _rasterize_muf.'
    )
    assert 'PHASE2_TIMEOUT_S' in text
    assert 'cpulimit' in text and "'-l', '50'" in text


def test_offline_install_embeds_muf_image_png_cache_slot():
    text = _read(OFFLINE)
    assert "'muf_image_png'" in text, (
        'Phase 2: offline-install.sh CACHE block must declare muf_image_png.'
    )


def test_offline_install_pygame_apt_includes_cairosvg_and_cpulimit():
    text = _read(OFFLINE)
    assert 'python3-cairosvg' in text
    # cpulimit appears twice (once in our subprocess argv, once in apt list);
    # require apt install to mention it explicitly.
    assert re.search(r'sudo apt install -y[^\n]*cpulimit', text), (
        'Phase 2: offline-install.sh pygame apt list must include cpulimit.'
    )


def test_mirror_installer_matches_offline_install():
    if not MIRROR.exists():
        import pytest as _p
        _p.skip(f'mirror installer not present at {MIRROR}')
    import hashlib
    def h(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    assert h(OFFLINE) == h(MIRROR), (
        'Phase 2: offline-install.sh and pi1-install.sh must be byte-identical. '
        'Run `cp offline-install.sh /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh`.'
    )
