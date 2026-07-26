"""The update/settings mechanism must actually be INSTALLED by both installers.

Everything the previous phase built — the root updater, the root NTP applier,
the seven units, the version stamp — is inert unless the installers put it on
the Pi with the right ownership and enable the right units. These tests pin
that wiring, and specifically the parts of it that fail silently:

* **root:root 0755 on the two helper scripts.** They are executed BY ROOT from
  systemd units. If SERVICE_USER can write one of them, the "unprivileged side
  can only ask" model collapses into arbitrary root code execution — and it
  collapses invisibly, because everything still works. So the installers assert
  ownership rather than inheriting it, and `assert_root_owned` is exercised here
  against a stubbed `stat` so the guard itself is known to have teeth.

* **The stale-file sweep.** It deletes every `*.sh` in $INSTALL_DIR that is not
  in HAMCLOCK_SHIPPED_FILES. Omitting the two new scripts would delete them
  moments after installing them, leaving the path units pointing at nothing.

* **Only four of the seven units may be enabled.** The other three are started
  by a `.path` or the `.timer`. Enabling hamclock-update.service directly would
  attempt an update on every boot.

* **$INSTALL_DIR/VERSION.** installed_version() reads this and nothing else —
  it never asks git, because the single-file installer exists precisely for Pis
  with no checkout. Without the stamp every daily check reads 0.0.0 and reports
  an update available forever.

* **The manifest describes the published bytes.** If the manifest and the
  installer disagree, the Pi refuses to install. That is the safe failure, but
  it is indistinguishable from a broken updater, so it is checked here against
  the exact file being served.
"""
import hashlib
import importlib.util
import json
import os
import py_compile
import re
import shutil
import stat as stat_mod
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KIOSK = REPO / "kiosk-install.sh"
OFFLINE = REPO / "offline-install.sh"
PUBLISHED = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")
MANIFEST = PUBLISHED.with_name("pi1-install.version.json")
SYNC = REPO / "scripts" / "sync_installers.py"
UNITS_DIR = REPO / "systemd"

INSTALLERS = [KIOSK, OFFLINE]
INSTALLER_IDS = [p.name for p in INSTALLERS]

ROOT_SCRIPTS = ("hamclock-update.sh", "hamclock-apply-settings.sh")

#: Every unit that must land in /etc/systemd/system.
ALL_UNITS = {
    "hamclock-update.path",
    "hamclock-update.service",
    "hamclock-update-check.timer",
    "hamclock-update-check.service",
    "hamclock-apply-settings.path",
    "hamclock-apply-settings.service",
    "hamclock-apply-settings-boot.service",
}

#: The only ones that may be `systemctl enable`d. The rest are triggered.
ENABLED_UNITS = {
    "hamclock-update-check.timer",
    "hamclock-update.path",
    "hamclock-apply-settings.path",
    "hamclock-apply-settings-boot.service",
}


def _load_sync():
    spec = importlib.util.spec_from_file_location("hamclock_sync_installers", SYNC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


si = _load_sync()


def _heredocs(path):
    """{dest: body} for every heredoc sync_installers.py manages in *path*."""
    text = Path(path).read_text()
    lines = text.split("\n")
    return {b.dest: "\n".join(lines[b.open_idx + 1:b.close_idx]) + "\n"
            for b, _ in si.managed_blocks(text)}


def _shell_var(text, name):
    """The value of a single-line `NAME="..."` assignment."""
    m = re.search(r'^%s="([^"]*)"$' % re.escape(name), text, re.M)
    assert m, f"{name} not found (or no longer a single-line assignment)"
    return m.group(1)


def _func(text, name):
    m = re.search(r'^%s\(\) \{.*?^\}$' % re.escape(name), text, re.M | re.S)
    assert m, f"{name}() not found"
    return m.group(0)


def _stub_dir(**scripts):
    """A temp dir of executable stubs, for prepending to PATH."""
    d = tempfile.mkdtemp(prefix="hamclock-stub-")
    for name, body in scripts.items():
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, os.stat(p).st_mode | stat_mod.S_IXUSR | stat_mod.S_IXGRP
                 | stat_mod.S_IXOTH)
    return d


SUDO_PASSTHROUGH = '#!/bin/sh\nexec "$@"\n'


# ---------------------------------------------------------------------------
# 1. The embedded copies are real, current, and syntactically valid.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("installer", [OFFLINE, PUBLISHED],
                         ids=["offline-install.sh", "published"])
@pytest.mark.parametrize("unit", sorted(ALL_UNITS))
def test_embedded_units_are_byte_identical_to_repo(installer, unit):
    assert installer.exists(), f"missing: {installer}"
    body = _heredocs(installer).get(unit)
    assert body is not None, (
        f"{installer.name} does not write /etc/systemd/system/{unit}")
    assert body == (UNITS_DIR / unit).read_text(), (
        f"{installer.name}'s embedded {unit} has drifted from systemd/{unit}. "
        "Run: python3 scripts/sync_installers.py")


@pytest.mark.parametrize("installer", [OFFLINE, PUBLISHED],
                         ids=["offline-install.sh", "published"])
@pytest.mark.parametrize("script", ROOT_SCRIPTS)
def test_embedded_root_scripts_are_byte_identical_to_repo(installer, script):
    """A stale embedded copy of these is not a cosmetic bug — it is the wrong
    privileged code running as root on every installer-built Pi."""
    body = _heredocs(installer).get(script)
    assert body is not None, f"{installer.name} does not install {script}"
    assert body == (REPO / "scripts" / script).read_text(), (
        f"{installer.name}'s embedded {script} has drifted from scripts/. "
        "Run: python3 scripts/sync_installers.py")


@pytest.mark.parametrize("installer", [OFFLINE, PUBLISHED],
                         ids=["offline-install.sh", "published"])
def test_every_embedded_heredoc_is_syntactically_valid(installer):
    """Extract each embedded source and put the matching compiler over it.

    A heredoc that ends early (a delimiter collision) or expands a `$` produces
    a file that is still *written* successfully on the Pi and only fails at
    runtime, hours later, as a blank panel or a dead service.
    """
    checked = {"py": 0, "sh": 0, "unit": 0}
    for dest, body in sorted(_heredocs(installer).items()):
        tmp = tempfile.mkdtemp(prefix="hamclock-embed-")
        p = Path(tmp) / dest
        p.write_text(body)
        try:
            if dest.endswith(".py"):
                py_compile.compile(str(p), cfile=str(p) + "c", doraise=True)
                checked["py"] += 1
            elif dest.endswith(".sh"):
                r = subprocess.run(["bash", "-n", str(p)],
                                   capture_output=True, text=True)
                assert r.returncode == 0, f"{dest}: bash -n failed\n{r.stderr}"
                checked["sh"] += 1
            elif dest.endswith((".path", ".service", ".timer")):
                # systemd-analyze needs the ExecStart binary to exist, which it
                # does not on a dev box; the structural check is what is
                # meaningful here (the units themselves are verified against
                # systemd-analyze in tests/test_root_scripts.py).
                assert re.search(r"^\[Unit\]$", body, re.M), f"{dest}: no [Unit]"
                assert re.search(r"^\[(Service|Path|Timer)\]$", body, re.M), (
                    f"{dest}: no [Service]/[Path]/[Timer] section")
                checked["unit"] += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    assert checked["py"] >= 4 and checked["sh"] == 2 and checked["unit"] == 7, (
        f"unexpected heredoc census {checked}")


def test_the_two_root_scripts_use_delimiters_that_cannot_collide():
    """hamclock-update.sh contains its own `<<EOF` and hamclock-apply-settings.sh
    a `<<'PY'`. Either as a delimiter here would end the block early and pipe
    the remainder of the installer into bash."""
    text = OFFLINE.read_text()
    for b, rel in si.managed_blocks(text):
        if b.dest in ROOT_SCRIPTS:
            assert b.delim not in ("EOF", "PY"), (
                f"{b.dest} uses the colliding delimiter {b.delim!r}")
            src = (REPO / rel).read_text()
            assert b.delim not in src.split("\n"), (
                f"{b.dest} contains a line equal to its delimiter {b.delim!r}")


# ---------------------------------------------------------------------------
# 2. root:root 0755 — the privilege boundary.
# ---------------------------------------------------------------------------

def _loop_body(text, header_re):
    """The body of a `for ...; do ... done` whose header matches header_re."""
    m = re.search(header_re, text, re.M)
    assert m, f"loop {header_re!r} not found"
    rest = text[m.end():]
    end = re.search(r'^done$', rest, re.M)
    assert end, f"loop {header_re!r} is never closed"
    return m, rest[:end.start()]


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_root_scripts_are_installed_root_root_0755(installer):
    """Both scripts, both installers, ownership forced AND then verified."""
    text = installer.read_text()
    m, body = _loop_body(text, r'^for _hc_root in ([^\n;]+); do$')
    assert set(m.group(1).split()) == set(ROOT_SCRIPTS), (
        f"{installer.name}: the root-helper loop installs {m.group(1)!r}")
    forces_root = ("install -o root -g root -m 0755" in body
                   or ("chown root:root" in body and "chmod 0755" in body))
    assert forces_root, (
        f"{installer.name} does not force root:root 0755 on the root helpers; "
        "a tee inherits whatever ownership $INSTALL_DIR already had, and a "
        "service-user-writable script that root executes IS root")
    assert 'assert_root_owned "$INSTALL_DIR/$_hc_root" 755' in body, (
        f"{installer.name} sets the ownership but never checks it took")


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_installers_never_grant_the_service_user_sudo(installer):
    """The whole model is that the dashboard has no privilege to escalate."""
    text = installer.read_text()
    for forbidden in ("/etc/sudoers", "sudoers.d", "NOPASSWD"):
        assert forbidden not in text, (
            f"{installer.name} mentions {forbidden!r}; the dashboard user must "
            "be granted no sudo at all — it asks by creating a flag file")


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
@pytest.mark.parametrize("owner,mode,ok", [
    ("root:root", "755", True),
    ("pi:pi", "755", False),          # service-user owned == service user is root
    ("root:pi", "755", False),
    ("root:root", "775", False),      # group-writable is just as fatal
    ("root:root", "777", False),
    ("root:root", "644", False),      # not executable: the unit would fail
    ("?", "?", False),                # stat failed / file missing
])
def test_assert_root_owned_has_teeth(installer, owner, mode, ok):
    """Run the installer's real guard against a stubbed `stat`.

    An always-passing assertion would be worse than none: it would advertise a
    check on the one thing that turns a bug into a root compromise.
    """
    func = _func(installer.read_text(), "assert_root_owned")
    stub = _stub_dir(stat=(
        '#!/bin/sh\n'
        'case "$2" in\n'
        '  "%U:%G") printf "%s\\n" "$FAKE_OWNER" ;;\n'
        '  "%a")    printf "%s\\n" "$FAKE_MODE" ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n'
    ))
    try:
        env = dict(os.environ, PATH=stub + os.pathsep + os.environ["PATH"],
                   FAKE_OWNER=owner, FAKE_MODE=mode)
        r = subprocess.run(
            ["bash", "-c", func + '\nassert_root_owned /opt/hamclock-lite/x 755'],
            capture_output=True, text=True, env=env)
        assert (r.returncode == 0) is ok, (
            f"{installer.name}: assert_root_owned({owner}, {mode}) returned "
            f"{r.returncode}, expected {'0' if ok else 'non-zero'}\n{r.stderr}")
        if not ok:
            assert "FATAL" in r.stderr
    finally:
        shutil.rmtree(stub, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. The stale-file sweep must not eat the scripts it was just handed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_root_scripts_and_version_survive_the_stale_sweep(installer, tmp_path):
    """Execute the real cleanup against a scratch $INSTALL_DIR.

    cleanup_stale_install deletes every *.sh it does not recognise. The two
    root helpers are *.sh, so an unlisted one is removed seconds after being
    installed — and the path units then point at a file that is not there.
    """
    src = installer.read_text()
    shipped = re.search(r'^HAMCLOCK_SHIPPED_FILES=.*$', src, re.M)
    func = re.search(r'^cleanup_stale_install\(\) \{.*?^\}$', src, re.M | re.S)
    assert shipped and func

    install_dir = tmp_path / "opt"
    install_dir.mkdir()
    for name in list(ROOT_SCRIPTS) + ["server.py", "kiosk.sh", "VERSION"]:
        (install_dir / name).write_text("# shipped\n")
    (install_dir / "leftover.sh").write_text("# stale\n")

    stub = _stub_dir(sudo=SUDO_PASSTHROUGH)
    script = tmp_path / "run.sh"
    script.write_text('#!/bin/bash\nINSTALL_DIR="%s"\n%s\n%s\ncleanup_stale_install\n'
                      % (install_dir, shipped.group(0), func.group(0)))
    try:
        env = dict(os.environ, PATH=stub + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["bash", str(script)], capture_output=True,
                           text=True, env=env)
    finally:
        shutil.rmtree(stub, ignore_errors=True)

    assert r.returncode == 0, r.stderr
    left = set(os.listdir(install_dir))
    for name in ROOT_SCRIPTS:
        assert name in left, (
            f"{installer.name}: cleanup_stale_install deleted {name}; add it to "
            "HAMCLOCK_SHIPPED_FILES")
    assert "VERSION" in left, "the version stamp must survive a re-install"
    assert "leftover.sh" not in left, "the sweep must still remove real cruft"


# ---------------------------------------------------------------------------
# 4. All seven units installed; exactly four enabled.
# ---------------------------------------------------------------------------

def test_repo_unit_set_matches_what_the_installers_ship():
    """An eighth unit added to systemd/ must be wired up, not silently ignored."""
    on_disk = {p.name for p in UNITS_DIR.iterdir() if p.is_file()}
    assert on_disk == ALL_UNITS, f"systemd/ holds {sorted(on_disk)}"
    assert set(si.UNIT_BLOCKS) == ALL_UNITS, (
        "sync_installers.UNIT_BLOCKS is out of step with systemd/")


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_installer_installs_all_seven_units(installer):
    text = installer.read_text()
    listed = set(_shell_var(text, "HAMCLOCK_ROOT_UNITS").split())
    assert listed == ALL_UNITS, (
        f"{installer.name}: HAMCLOCK_ROOT_UNITS is {sorted(listed)}")

    _, body = _loop_body(text, r'^for _hc_unit in \$HAMCLOCK_ROOT_UNITS; do$')
    assert '/etc/systemd/system/$_hc_unit' in body, (
        f"{installer.name} lists the units but never writes them to "
        "/etc/systemd/system")
    assert 'assert_root_owned "/etc/systemd/system/$_hc_unit" 644' in body

    # The two installers get the bytes from different places, on purpose: one
    # has a checkout next to it, the other is a single file.
    if installer == KIOSK:
        assert ('sudo install -o root -g root -m 0644 '
                '"$SCRIPT_DIR/systemd/$_hc_unit"') in body, (
            "kiosk-install.sh must copy the units out of the checkout")
    else:
        assert 'sudo chown root:root "/etc/systemd/system/$_hc_unit"' in body
        assert ALL_UNITS <= set(_heredocs(installer)), (
            "offline-install.sh must carry every unit as a heredoc")


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_installer_enables_exactly_the_four_activatable_units(installer):
    text = installer.read_text()
    listed = set(_shell_var(text, "HAMCLOCK_ENABLE_UNITS").split())
    assert listed == ENABLED_UNITS, (
        f"{installer.name}: HAMCLOCK_ENABLE_UNITS is {sorted(listed)}")
    for triggered in ALL_UNITS - ENABLED_UNITS:
        assert triggered not in listed, (
            f"{installer.name} enables {triggered}, which a .path/.timer "
            "already starts — enabling it runs it at every boot")


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_enable_loop_really_reloads_and_enables(installer, tmp_path):
    """Run the installer's own enable loop against a logging systemctl."""
    text = installer.read_text()
    log = tmp_path / "systemctl.log"
    stub = _stub_dir(
        sudo=SUDO_PASSTHROUGH,
        systemctl='#!/bin/sh\necho "$@" >> "%s"\n' % log,
    )
    snippet = "\n".join([
        "#!/bin/bash",
        'HAMCLOCK_ENABLE_UNITS="%s"' % _shell_var(text, "HAMCLOCK_ENABLE_UNITS"),
        _func(text, "enable_hamclock_root_units"),
        "enable_hamclock_root_units",
    ])
    script = tmp_path / "enable.sh"
    script.write_text(snippet)
    try:
        env = dict(os.environ, PATH=stub + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["bash", str(script)], capture_output=True,
                           text=True, env=env)
    finally:
        shutil.rmtree(stub, ignore_errors=True)

    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert "daemon-reload" in calls, (
        f"{installer.name}: units written but never daemon-reloaded — systemd "
        "would not see them until the next boot")
    for unit in ENABLED_UNITS:
        assert f"enable {unit}" in calls, f"{installer.name}: never enabled {unit}"
        assert f"start {unit}" in calls, f"{installer.name}: never started {unit}"
    for triggered in ALL_UNITS - ENABLED_UNITS:
        assert f"enable {triggered}" not in calls


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_enable_loop_tolerates_a_unit_that_will_not_start_yet(installer, tmp_path):
    """`systemctl start` failing (no network, no settings yet) must not abort
    the install — the unit is enabled and the next boot runs it."""
    text = installer.read_text()
    stub = _stub_dir(
        sudo=SUDO_PASSTHROUGH,
        systemctl='#!/bin/sh\ncase "$1" in start) exit 1 ;; esac\nexit 0\n',
    )
    snippet = "\n".join([
        "#!/bin/bash",
        "set -euo pipefail",
        'HAMCLOCK_ENABLE_UNITS="%s"' % _shell_var(text, "HAMCLOCK_ENABLE_UNITS"),
        _func(text, "enable_hamclock_root_units"),
        "enable_hamclock_root_units",
        'echo REACHED_END',
    ])
    script = tmp_path / "enable.sh"
    script.write_text(snippet)
    try:
        env = dict(os.environ, PATH=stub + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["bash", str(script)], capture_output=True,
                           text=True, env=env)
    finally:
        shutil.rmtree(stub, ignore_errors=True)
    assert "REACHED_END" in r.stdout, (
        f"{installer.name}: a unit that cannot start yet aborts the whole "
        f"install\n{r.stderr}")


# ---------------------------------------------------------------------------
# 5. $INSTALL_DIR/VERSION — what installed_version() reads.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_installer_writes_the_version_stamp(installer):
    text = installer.read_text()
    assert '"$INSTALL_DIR/VERSION"' in text, (
        f"{installer.name} never writes $INSTALL_DIR/VERSION; installed_version() "
        "would read 0.0.0 and every daily check would offer an update forever")
    assert 'assert_root_owned "$INSTALL_DIR/VERSION" 644' in text


def test_offline_installer_bakes_the_version_into_its_own_text():
    """The single-file installer has no git and no checkout, which is the whole
    reason it exists. The version has to be in the file."""
    body = _heredocs(OFFLINE)["VERSION"]
    assert body == (REPO / "VERSION").read_text(), (
        "offline-install.sh's baked version has drifted from VERSION. "
        "Run: python3 scripts/sync_installers.py")
    assert body.strip() == (REPO / "VERSION").read_text().strip()
    assert "git" not in body


def test_kiosk_installer_takes_the_version_from_the_checkout():
    text = KIOSK.read_text()
    assert 'sudo install -o root -g root -m 0644 "$SCRIPT_DIR/VERSION"' in text, (
        "kiosk-install.sh must copy the checkout's VERSION into $INSTALL_DIR")
    assert 'if [ ! -f "$SCRIPT_DIR/VERSION" ]; then' in text, (
        "a missing VERSION must fail loudly, not install a Pi that reports "
        "0.0.0 forever")


def test_published_version_matches_the_repo():
    assert json.loads(MANIFEST.read_text())["version"] == \
        (REPO / "VERSION").read_text().strip()


# ---------------------------------------------------------------------------
# 6. /run/hamclock-lite: writable by SERVICE_USER, recreated every boot.
# ---------------------------------------------------------------------------

def _lite_unit(text):
    m = re.search(r"Description=HamClock Lite Server\n(.*?)\nEOF\n", text, re.S)
    assert m, "hamclock-lite unit heredoc not found"
    return m.group(1)


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_run_dir_is_a_runtime_directory_of_the_dashboard_unit(installer):
    """The dashboard creates the flag files, so /run/hamclock-lite has to be
    owned by the same User= the dashboard runs as."""
    unit = _lite_unit(installer.read_text())
    service = unit.split("[Service]", 1)[1].split("[Install]", 1)[0]
    assert "RuntimeDirectory=hamclock-lite" in service, (
        f"{installer.name}: hamclock-lite.service must declare "
        "RuntimeDirectory=hamclock-lite, or the service user cannot create the "
        "update.request / settings.request flag files and the whole "
        "ask-root-to-do-it mechanism is dead")
    assert "RuntimeDirectoryMode=0755" in service
    assert "RuntimeDirectoryPreserve=yes" in service, (
        f"{installer.name}: without Preserve=yes the directory (and the status "
        "file the dashboard polls) is torn down when the updater restarts "
        "hamclock-lite mid-apply")


@pytest.mark.parametrize("installer", INSTALLERS, ids=INSTALLER_IDS)
def test_run_dir_is_not_created_once_at_install_time(installer):
    """/run is a tmpfs: an install-time mkdir is gone after the first reboot."""
    text = installer.read_text()
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            continue
        assert not re.match(r'^sudo mkdir (-p )?/run/hamclock-lite\b', s), (
            f"{installer.name}: {s!r} — /run is a tmpfs, so this is empty "
            "again after every boot; systemd's RuntimeDirectory= must own it")


# ---------------------------------------------------------------------------
# 7. Publication: byte-identical copy + a manifest of the published bytes.
# ---------------------------------------------------------------------------

def test_published_installer_is_byte_identical_to_the_source():
    assert PUBLISHED.exists(), f"nothing published at {PUBLISHED}"
    assert hashlib.sha256(PUBLISHED.read_bytes()).hexdigest() == \
        hashlib.sha256(OFFLINE.read_bytes()).hexdigest()


def test_manifest_describes_the_exact_published_bytes():
    """This digest is the only thing standing between the Pi and `curl | bash`
    as root, so it must be of the served file, not of anything upstream of it."""
    assert MANIFEST.exists(), f"no manifest at {MANIFEST}"
    m = json.loads(MANIFEST.read_text())
    raw = PUBLISHED.read_bytes()
    assert m["sha256"] == hashlib.sha256(raw).hexdigest(), (
        "manifest sha256 is not the digest of the published installer — every "
        "Pi will refuse to update. Run: python3 scripts/sync_installers.py")
    assert m["size"] == len(raw)
    assert m["installer"].endswith("/downloads/pi1-install.sh")
    assert m["installer"].startswith("https://")


def test_manifest_check_mode_passes_on_the_published_pair():
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "make_version_manifest.py"),
         str(PUBLISHED), "--check"],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr


def test_manifest_check_catches_a_changed_installer(tmp_path):
    """Guard the guard: a manifest that cannot fail proves nothing."""
    fake = tmp_path / "pi1-install.sh"
    fake.write_bytes(PUBLISHED.read_bytes() + b"\n# tampered\n")
    shutil.copy(MANIFEST, tmp_path / "pi1-install.version.json")
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "make_version_manifest.py"),
         str(fake), "--check"],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode != 0, "a tampered installer passed the digest check"
    assert "sha256" in r.stderr


def test_sync_installers_check_covers_the_new_material():
    """--check must be clean, and must also NOTICE the new blocks — a --check
    that no longer looks at the units would pass while shipping stale ones."""
    r = subprocess.run([sys.executable, str(SYNC), "--check"],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
    managed = {b.dest for b, _ in si.managed_blocks(OFFLINE.read_text())}
    assert ALL_UNITS <= managed
    assert set(ROOT_SCRIPTS) <= managed
    assert "VERSION" in managed


def test_sync_installers_check_fails_on_a_stale_embedded_unit(tmp_path):
    """Teeth for the CI gate: edit an embedded unit by hand and --check must
    fail rather than let a Pi be built with the hand-edited copy."""
    work = tmp_path / "offline-install.sh"
    text = OFFLINE.read_text()
    text = text.replace("PathExists=/run/hamclock-lite/update.request",
                        "PathExists=/run/hamclock-lite/WRONG.request", 1)
    work.write_text(text)
    drift = si.sync(installer=work, mirror=None, repo=REPO, check=True,
                    do_mirror=False, log=lambda *a: None)
    assert drift, "a hand-edited embedded unit went unnoticed"


def test_sync_refuses_an_unlisted_unit_heredoc():
    """An eighth unit written by hand fails the build until it is declared."""
    text = OFFLINE.read_text()
    injected = text.replace(
        'sudo tee "/etc/systemd/system/hamclock-update.path" > /dev/null '
        "<< 'HCUNIT_UPDATE_PATH'",
        'sudo tee "/etc/systemd/system/hamclock-rogue.service" > /dev/null '
        "<< 'ROGUEUNIT'\n[Unit]\nROGUEUNIT\n"
        'sudo tee "/etc/systemd/system/hamclock-update.path" > /dev/null '
        "<< 'HCUNIT_UPDATE_PATH'",
        1)
    assert injected != text, "injection point not found — update this test"
    with pytest.raises(si.SyncError) as exc:
        si.managed_blocks(injected)
    assert "hamclock-rogue.service" in str(exc.value)


def test_templated_units_are_not_mistaken_for_synced_ones():
    """hamclock-lite.service and hamclock-kiosk.service interpolate
    $SERVICE_USER, so there is no static file to sync them from; the unit
    opener must not claim them."""
    units = {b.dest for b in si.find_unit_blocks(OFFLINE.read_text())}
    assert "hamclock-lite.service" not in units
    assert "hamclock-kiosk.service" not in units
    assert units == ALL_UNITS
