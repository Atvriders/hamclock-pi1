"""kiosk-install.sh must refresh an EXISTING install, not just a fresh one.

kiosk-install.sh is the path the owner's own Pi uses. It used to wrap the
server.py / index.html copy in `if [ ! -f "$INSTALL_DIR/server.py" ]`, so
re-running it on a box that already had HamClock refreshed the three native
client modules and *nothing else* -- every server-side fix (fetch ordering,
cache persistence, MUF rasterize) silently never reached hardware, and the
newly-copied client then talked to a months-old server.

The same shape applied to the systemd unit: it sat inside
`if ! systemctl is-enabled hamclock-lite`, so unit changes (CacheDirectory=)
were first-install-only too.

These tests pin the corrected structure without executing the installer (it
needs sudo, apt and a real Pi).
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KIOSK = REPO / "kiosk-install.sh"
OFFLINE = REPO / "offline-install.sh"

SHIPPED_FILES = ("server.py", "index.html", "hamclock_data.py",
                 "hamclock_pygame.py", "hamclock_tkinter.py")


@pytest.fixture(scope="module")
def kiosk():
    return KIOSK.read_text()


def test_kiosk_install_bash_clean():
    r = subprocess.run(["bash", "-n", str(KIOSK)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_offline_install_bash_clean():
    r = subprocess.run(["bash", "-n", str(OFFLINE)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_server_copy_is_not_gated_on_first_install(kiosk):
    code = "\n".join(l for l in kiosk.split("\n") if not l.lstrip().startswith("#"))
    assert 'if [ ! -f "$INSTALL_DIR/server.py" ]' not in code, (
        "kiosk-install.sh still gates the server.py/index.html copy on a "
        "first install; re-running it would not refresh the server."
    )


def test_every_shipped_file_is_refreshed_unconditionally(kiosk):
    """Each shipped file must be copied outside any `[ ! -f ... ]` guard."""
    # The copy loop is the only place these are installed from SCRIPT_DIR.
    m = re.search(r"^for _hc_file in ([^\n]+); do$", kiosk, re.M)
    assert m, "kiosk-install.sh lost the unconditional refresh loop"
    listed = m.group(1).split()
    for name in SHIPPED_FILES:
        assert name in listed, f"{name} is not refreshed on re-install"
    # ...and the loop body must actually copy into $INSTALL_DIR.
    assert 'sudo cp "$SCRIPT_DIR/$_hc_file" "$INSTALL_DIR/$_hc_file"' in kiosk


def test_refresh_loop_is_not_inside_a_first_install_guard(kiosk):
    """Bracket check: no `FIRST_INSTALL`/`[ ! -f ]` conditional may still be
    open when the copy loop runs."""
    lines = kiosk.split("\n")
    loop = next(i for i, l in enumerate(lines) if l.startswith("for _hc_file in "))
    guard = next(i for i, l in enumerate(lines)
                 if l.startswith('[ -f "$INSTALL_DIR/server.py" ] ||'))
    # Count `if`/`fi` between the FIRST_INSTALL probe and the loop: must balance.
    depth = 0
    for l in lines[guard:loop]:
        s = l.strip()
        if s.startswith("if ") or s == "if":
            depth += 1
        elif s == "fi":
            depth -= 1
    assert depth == 0, (
        "the refresh loop is nested inside an unbalanced conditional "
        f"(depth={depth}) — it would not run on a re-install"
    )


def test_first_run_only_work_stays_guarded(kiosk):
    """mkdir/enable/start remain first-run behaviour; the copies do not."""
    assert "FIRST_INSTALL=1" in kiosk, "lost the first-install detection"
    assert 'if ! systemctl is-enabled hamclock-lite &>/dev/null; then' in kiosk
    m = re.search(
        r"if ! systemctl is-enabled hamclock-lite &>/dev/null; then\n(.*?)\nfi\n",
        kiosk, re.S)
    assert m, "could not locate the enable guard"
    body = m.group(1)
    assert "systemctl enable hamclock-lite" in body
    assert "systemctl start hamclock-lite" in body
    # The unit heredoc must NOT be inside that guard any more.
    assert "hamclock-lite.service" not in body, (
        "the systemd unit is still written only on a first install; unit "
        "changes such as CacheDirectory= would never reach an existing Pi"
    )


def test_settings_json_is_never_overwritten(kiosk):
    """User state must survive a re-install."""
    assert "/etc/hamclock-lite/settings.json" in kiosk
    m = re.search(r'sudo tee "\$SETTINGS_FILE"', kiosk)
    assert m, "settings seed block missing"
    head = kiosk[:m.start()]
    # The seed write is guarded by an explicit not-exists test.
    assert 'if [ ! -f "$SETTINGS_FILE" ]; then' in head, (
        "settings.json seed is no longer guarded by [ ! -f ] — a re-install "
        "would clobber the operator's callsign/theme"
    )
    # And settings.json must not be in the unconditional refresh loop.
    loop = re.search(r"^for _hc_file in ([^\n]+); do$", kiosk, re.M).group(1)
    assert "settings.json" not in loop


@pytest.mark.parametrize("installer", [KIOSK, OFFLINE])
def test_unit_declares_cache_directory(installer):
    """server.py persists images under /var/cache/hamclock-lite; the service
    runs as an unprivileged user and cannot mkdir there itself."""
    text = installer.read_text()
    assert "CacheDirectory=hamclock-lite" in text, (
        f"{installer.name}: hamclock-lite.service must declare "
        "CacheDirectory=hamclock-lite or image persistence silently fails "
        "with EACCES on every boot"
    )
    # It has to be in the [Service] section of the hamclock-lite unit.
    m = re.search(r"Description=HamClock Lite Server\n(.*?)\nEOF\n", text, re.S)
    assert m, f"{installer.name}: hamclock-lite unit heredoc not found"
    unit = m.group(1)
    assert "[Service]" in unit
    service = unit.split("[Service]", 1)[1]
    assert "CacheDirectory=hamclock-lite" in service.split("[Install]", 1)[0]


@pytest.mark.parametrize("installer", [KIOSK, OFFLINE])
def test_cache_dir_is_not_created_inside_the_unit_heredoc(installer):
    """A stray shell line inside the unit body makes the unit unparseable and
    the service never starts."""
    text = installer.read_text()
    m = re.search(r"Description=HamClock Lite Server\n(.*?)\nEOF\n", text, re.S)
    unit = m.group(1)
    for line in unit.split("\n"):
        assert not line.lstrip().startswith("sudo "), (
            f"{installer.name}: shell command {line!r} inside the systemd unit "
            "heredoc — systemd would reject the unit"
        )


@pytest.mark.parametrize("installer", [KIOSK, OFFLINE])
def test_pygame_apt_line_keeps_its_three_packages(installer):
    """One missing package fails the whole apt line, so nothing extra may be
    merged into it; new needs go on their own `|| true` line."""
    text = installer.read_text()
    m = re.search(r"^\s*sudo apt install -y python3-pygame[^\n]*$", text, re.M)
    assert m, f"{installer.name}: pygame apt line not found"
    line = m.group(0).strip()
    pkgs = line.split("sudo apt install -y", 1)[1].split()
    assert pkgs == ["python3-pygame", "python3-cairosvg", "cpulimit"], (
        f"{installer.name}: pygame apt line changed to {pkgs!r}; add new "
        "packages on a separate `sudo apt install -y <pkg> || true` line "
        "instead, so one unavailable package cannot fail the whole install"
    )
