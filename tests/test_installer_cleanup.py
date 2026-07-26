"""The installers must remove stale program files from earlier versions.

`compileall` (the pygame unit's ExecStartPre, Tier 1c) only ever ADDS .pyc
files — it never prunes them. Without a cleanup step a module dropped in a
later release leaves an importable orphan in __pycache__ forever. These tests
execute the real `cleanup_stale_install` shell function out of each installer
against a scratch INSTALL_DIR, so they pin behaviour rather than text.
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLERS = [
    os.path.join(REPO, "kiosk-install.sh"),
    os.path.join(REPO, "offline-install.sh"),
]

SHIPPED = [
    "server.py",
    "index.html",
    "hamclock_data.py",
    "hamclock_pygame.py",
    "hamclock_tkinter.py",
    "kiosk.sh",
]


def _extract_cleanup(path):
    """Pull HAMCLOCK_SHIPPED_FILES + cleanup_stale_install() out of an installer."""
    src = open(path).read()
    shipped = re.search(r'^HAMCLOCK_SHIPPED_FILES=.*$', src, re.M)
    func = re.search(r'^cleanup_stale_install\(\) \{.*?^\}$', src, re.M | re.S)
    assert shipped, f"{path}: HAMCLOCK_SHIPPED_FILES not found"
    assert func, f"{path}: cleanup_stale_install() not found"
    return shipped.group(0) + "\n" + func.group(0) + "\n"


def _run_cleanup(installer, install_dir, env_extra=None):
    """Execute the extracted cleanup against install_dir, with sudo stubbed."""
    shim = tempfile.mkdtemp()
    sudo = os.path.join(shim, "sudo")
    with open(sudo, "w") as f:
        f.write('#!/bin/sh\nexec "$@"\n')
    os.chmod(sudo, os.stat(sudo).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    script = (
        "#!/bin/bash\n"
        f'INSTALL_DIR="{install_dir}"\n'
        + _extract_cleanup(installer)
        + "cleanup_stale_install\n"
    )
    sh = os.path.join(shim, "run.sh")
    open(sh, "w").write(script)

    env = dict(os.environ)
    env["PATH"] = shim + os.pathsep + env["PATH"]
    if env_extra:
        env.update(env_extra)
    try:
        return subprocess.run(["bash", sh], capture_output=True, text=True, env=env)
    finally:
        shutil.rmtree(shim, ignore_errors=True)


@pytest.fixture
def install_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _populate(d):
    for name in SHIPPED:
        open(os.path.join(d, name), "w").write("# shipped\n")
    # Stale: a module we no longer ship, plus editor/packaging cruft.
    for name in ("hamclock_legacy.py", "old_dashboard.html", "helper.sh",
                 "server.py.bak", "index.html.orig", "notes.tmp", "server.pyc"):
        open(os.path.join(d, name), "w").write("# stale\n")
    # Stale bytecode cache with an orphaned module.
    pyc = os.path.join(d, "__pycache__")
    os.makedirs(pyc, exist_ok=True)
    open(os.path.join(pyc, "hamclock_legacy.cpython-311.pyc"), "wb").write(b"\x00")
    open(os.path.join(pyc, "hamclock_data.cpython-311.pyc"), "wb").write(b"\x00")


@pytest.mark.parametrize("installer", INSTALLERS, ids=lambda p: os.path.basename(p))
def test_cleanup_removes_stale_and_keeps_shipped(installer, install_dir):
    _populate(install_dir)
    r = _run_cleanup(installer, install_dir)
    assert r.returncode == 0, r.stderr

    left = set(os.listdir(install_dir))
    for name in SHIPPED:
        assert name in left, f"{name} was shipped and must survive cleanup"
    for name in ("hamclock_legacy.py", "old_dashboard.html", "helper.sh",
                 "server.py.bak", "index.html.orig", "notes.tmp", "server.pyc"):
        assert name not in left, f"{name} is stale and must be removed"
    assert "__pycache__" not in left, (
        "stale bytecode must go — compileall never prunes, so an orphan .pyc "
        "for a removed module would stay importable forever")


@pytest.mark.parametrize("installer", INSTALLERS, ids=lambda p: os.path.basename(p))
def test_cleanup_leaves_operator_files_alone(installer, install_dir):
    """Only file types we ship are swept, so a deliberately-parked file stays."""
    _populate(install_dir)
    for name in ("README.txt", "my-notes.md", "custom.json"):
        open(os.path.join(install_dir, name), "w").write("operator\n")
    r = _run_cleanup(installer, install_dir)
    assert r.returncode == 0, r.stderr
    left = set(os.listdir(install_dir))
    for name in ("README.txt", "my-notes.md", "custom.json"):
        assert name in left, f"{name} is not ours to delete"


@pytest.mark.parametrize("installer", INSTALLERS, ids=lambda p: os.path.basename(p))
def test_cleanup_is_opt_outable(installer, install_dir):
    _populate(install_dir)
    r = _run_cleanup(installer, install_dir, {"HAMCLOCK_SKIP_CLEAN": "1"})
    assert r.returncode == 0, r.stderr
    left = set(os.listdir(install_dir))
    assert "hamclock_legacy.py" in left
    assert "__pycache__" in left
    assert "HAMCLOCK_SKIP_CLEAN" in r.stdout


@pytest.mark.parametrize("installer", INSTALLERS, ids=lambda p: os.path.basename(p))
def test_cleanup_survives_missing_dir_and_empty_dir(installer, install_dir):
    """A first install (no dir yet) and a clean dir must both be no-ops."""
    missing = os.path.join(install_dir, "not-there")
    r = _run_cleanup(installer, missing)
    assert r.returncode == 0, r.stderr

    r = _run_cleanup(installer, install_dir)
    assert r.returncode == 0, r.stderr
    assert "removed" not in r.stdout


@pytest.mark.parametrize("installer", INSTALLERS, ids=lambda p: os.path.basename(p))
def test_cleanup_runs_before_units_and_never_touches_user_state(installer):
    """Settings and the image cache live outside INSTALL_DIR by design."""
    src = open(installer).read()
    func = re.search(r'^cleanup_stale_install\(\) \{.*?^\}$', src, re.M | re.S).group(0)
    assert "/etc/hamclock-lite" not in func, "must never touch user settings"
    assert "/var/cache/hamclock-lite" not in func, "must never touch the image cache"
    assert "systemd" not in func, "units are rewritten every run; not cleanup's job"
    # The call must be wired up, not merely defined.
    assert re.search(r'^cleanup_stale_install$', src, re.M), (
        "cleanup_stale_install is defined but never called")
