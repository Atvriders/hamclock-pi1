"""Phase 0 ships exactly these artifacts. Phase 5 is gated on this set.

This test is the single source-of-truth for 'Phase 0 done': if it passes,
Phase 5 may merge (subject to Phase 2 also being done).
"""
import os
import re
import stat
import subprocess

REPO = "/home/kasm-user/hamclock-pi1"


def test_conftest_present():
    assert os.path.isfile(f"{REPO}/tests/__init__.py")
    assert os.path.isfile(f"{REPO}/tests/conftest.py")
    with open(f"{REPO}/tests/conftest.py") as f:
        body = f.read()
    assert "SDL_VIDEODRIVER" in body and "dummy" in body
    assert "HAMCLOCK_DEBUG" in body and '"1"' in body


def test_requirements_dev_present():
    with open(f"{REPO}/requirements-dev.txt") as f:
        body = f.read()
    assert "pytest" in body
    assert "pytest-mock" in body


def test_probe_script_present_and_executable():
    path = f"{REPO}/scripts/probe_sdl_backends.sh"
    assert os.path.isfile(path)
    assert os.stat(path).st_mode & stat.S_IXUSR
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_sdl_backend_doc_present_with_chosen_backend():
    path = f"{REPO}/docs/sdl-backend.md"
    assert os.path.isfile(path)
    with open(path) as f:
        body = f.read().lower()
    m = re.search(r"chosen backend:\s*`?(fbcon|kmsdrm|xinit)`?", body)
    assert m, "docs/sdl-backend.md must record one of fbcon|kmsdrm|xinit"


def test_all_phase0_tests_collect_and_pass():
    # Sanity: the three Phase 0 test files collect cleanly together.
    r = subprocess.run(
        ["python3", "-m", "pytest",
         "tests/test_conftest_env.py",
         "tests/test_probe_script.py",
         "tests/test_sdl_backend_doc.py",
         "-v", "--no-header"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
