"""Regression: the cleanup must not leave the service unable to start.

Field failure (2026-07-26): hamclock-lite.service refused to start with
"control process exited with error code". The unit runs as User=$SERVICE_USER
(non-root) while $INSTALL_DIR is root-owned, and its ExecStartPre runs
`compileall`, which must CREATE $INSTALL_DIR/__pycache__. That only ever worked
because the directory already happened to exist; once cleanup_stale_install
removed it, compileall raised PermissionError and exited 1, so systemd refused
to start the service.

Two independent guards, both pinned here:
  1. the installer pre-compiles as root at install time, and
  2. the unit's ExecStartPre is prefixed with '-' so it can never block start.
"""
import os
import re
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLERS = [
    os.path.join(REPO, "kiosk-install.sh"),
    os.path.join(REPO, "offline-install.sh"),
]


@pytest.mark.parametrize("installer", INSTALLERS, ids=os.path.basename)
def test_installer_precompiles_bytecode_as_root(installer):
    """Install time is the only moment the .py files change, and the only
    moment we still have root — so bytecode must be built here."""
    src = open(installer).read()
    assert re.search(r'compileall -q "\$INSTALL_DIR"', src), (
        "installer must pre-compile bytecode into $INSTALL_DIR as root")
    assert re.search(r'sudo python3 -O -m compileall -q "\$INSTALL_DIR"', src), (
        "must also build the -O (.opt-1.pyc) variant: the pygame unit sets "
        "PYTHONOPTIMIZE=1 and will look for .opt-1.pyc, not plain .pyc")


@pytest.mark.parametrize("installer", INSTALLERS, ids=os.path.basename)
def test_precompile_runs_after_cleanup(installer):
    """Order matters: cleanup deletes __pycache__, so the rebuild must follow."""
    src = open(installer).read()
    cleanup_call = src.index("\ncleanup_stale_install\n")
    precompile = src.index('sudo python3 -m compileall -q "$INSTALL_DIR"')
    assert cleanup_call < precompile, (
        "pre-compile must run AFTER cleanup_stale_install, otherwise cleanup "
        "removes the __pycache__ that was just built")


@pytest.mark.parametrize("installer", INSTALLERS, ids=os.path.basename)
def test_execstartpre_compileall_is_non_fatal(installer):
    """A best-effort optimisation must never be able to refuse the service."""
    src = open(installer).read()
    m = re.search(r'LITE_PYGAME_PRE="ExecStartPre=(-?)/usr/bin/python3.*compileall.*"', src)
    assert m, "LITE_PYGAME_PRE compileall line not found"
    assert m.group(1) == "-", (
        "ExecStartPre compileall must be prefixed with '-' so a PermissionError "
        "(service user cannot write root-owned $INSTALL_DIR) cannot block start")


def test_compileall_fails_when_pycache_missing_and_dir_unwritable():
    """The actual mechanism, demonstrated — this is why the guards exist."""
    d = tempfile.mkdtemp()
    open(os.path.join(d, "m.py"), "w").write("x = 1\n")
    os.chmod(d, 0o555)  # root-owned-and-unwritable, from the service user's view
    try:
        r = subprocess.run([sys.executable, "-O", "-m", "compileall", "-q", d],
                           capture_output=True, text=True)
        assert r.returncode != 0, (
            "expected compileall to fail creating __pycache__ in an unwritable "
            "dir — if this ever passes, the root cause has changed")
        # And it succeeds once the cache exists (what the install-time pass buys).
        os.chmod(d, 0o755)
        subprocess.run([sys.executable, "-O", "-m", "compileall", "-q", d], check=True)
        os.chmod(d, 0o555)
        r2 = subprocess.run([sys.executable, "-O", "-m", "compileall", "-q", d],
                            capture_output=True, text=True)
        assert r2.returncode == 0, (
            "with bytecode already built and current, a re-run must succeed "
            "even though the directory is unwritable")
    finally:
        os.chmod(d, 0o755)
        import shutil
        shutil.rmtree(d, ignore_errors=True)
