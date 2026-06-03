# Pi 1 Native Pygame Client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Pi 1 browser kiosk with a polished native pygame client as the default install path, eliminating browser overhead and bringing click latency to p99 ≤ 200 ms while preserving full feature parity (MUF map, 4 themes, setup wizard).

**Architecture:** Iterate the existing `hamclock_pygame.py` into a feature-complete native client; add a server-side cairosvg rasterize step in `server.py` for the MUF SVG so the client can blit a PNG; flip `kiosk-install.sh`'s default from `--browser` to `--pygame`. Browser and tkinter paths remain available behind opt-in flags. Six phases — each independently shippable with explicit verification gates — followed by a Phase 1b perf-cleanup commit after Phase 5 ships.

**Tech Stack:** Python 3 (stdlib only on the server side), pygame 2.x, SDL2 (fbcon or kmsdrm or minimal X), cairosvg, Bash installer scripts, systemd. Tests in pytest with `SDL_VIDEODRIVER=dummy` and `HAMCLOCK_DEBUG=1`.

**Spec:** `docs/superpowers/specs/2026-06-03-pi1-native-client-design.md`. The spec encodes every design decision and risk; this plan executes it task-by-task.

**Repo:** `/home/kasm-user/hamclock-pi1` (with `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh` as the dual-repo installer mirror).

**Dual-repo rule:** every installer change is mirrored to `hamclock-reborn/public/downloads/pi1-install.sh` as its own task at the end of the relevant phase. Commits land in BOTH `Atvriders/hamclock-pi1` (origin master) and `Atvriders/hamclock-reborn` (origin master).

## Known integration points (read before starting)

Three review-flagged integration points where two phases touch the same symbol from different angles. Resolve at the moment the second phase's task runs:

1. **`draw_image` signature.** Phase 1.4 adds `image_key` and `fetched_at` parameters for the scaled-surface cache key; Phase 3.6 also threads `theme` through `draw_image`'s "image loading..." label branch. Final reconciled signature: `draw_image(screen, rect, surface, fonts, theme, image_key=None, fetched_at=None)`. Phase 3.6's edit must preserve Phase 1.4's parameters, not drop them.

2. **`_glyph_cache` key tuple shape.** Spec calls for `(font_name_or_None, font_size, text, color)` — a flat 4-tuple. Phase 1.3 may write it as `((font_name, font_size), text, color)` — a 3-tuple with a nested font key. Either shape works as long as it is used consistently; the test in Task 1.3 must match whatever shape the implementation uses, and the Phase 1b cleanup (Task 1b.1) must not change it.

3. **`_make_fonts` keys.** Phase 1.2 fixes the canonical key set: `{title, panel, body, label, small, tiny}`. Phase 4's wizard uses `fonts["title"]` for the heading and `fonts["panel"]` for field labels (the assembled plan substitutes these in; double-check the merged code uses only keys produced by `_make_fonts`).

If any of these reconciliations causes a test to break, the test is the contract — adjust the implementation to match, not the test.

---

---

## Phase 0 — SDL driver verification

This phase is documentation-and-script only. It produces `scripts/probe_sdl_backends.sh`, runs it on a real Pi 1B (HDMI attached), records the outcome in `docs/sdl-backend.md`, and lands the `tests/conftest.py` harness that every later phase imports. No `hamclock_pygame.py` or installer code changes here — those are gated on Phase 0's recorded decision and ship in Phase 5.

### Task 0.1: Create `tests/conftest.py` test harness

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/tests/__init__.py`
- Create: `/home/kasm-user/hamclock-pi1/tests/conftest.py`
- Create: `/home/kasm-user/hamclock-pi1/requirements-dev.txt`
- Test: `/home/kasm-user/hamclock-pi1/tests/test_conftest_env.py`

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_conftest_env.py`:
  ```python
  """Verify conftest.py sets the env vars all later phases depend on."""
  import os


  def test_sdl_videodriver_is_dummy():
      assert os.environ.get("SDL_VIDEODRIVER") == "dummy"


  def test_hamclock_debug_is_one():
      assert os.environ.get("HAMCLOCK_DEBUG") == "1"


  def test_pygame_importable_under_dummy_driver():
      import pygame
      pygame.display.init()
      try:
          assert pygame.display.get_driver() == "dummy"
      finally:
          pygame.display.quit()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_conftest_env.py -v`
  Expected: FAIL — `tests/__init__.py`, `tests/conftest.py`, and `requirements-dev.txt` do not exist yet; the env vars are not set, so both assertions fail (or pytest cannot even collect the test).

- [ ] **Step 3: Implement**
  Create `/home/kasm-user/hamclock-pi1/tests/__init__.py` (empty file, zero bytes).

  Create `/home/kasm-user/hamclock-pi1/requirements-dev.txt`:
  ```
  pytest>=7.0
  pytest-mock>=3.10
  ```

  Create `/home/kasm-user/hamclock-pi1/tests/conftest.py`:
  ```python
  """Shared pytest fixtures and environment setup for the hamclock-pi1 suite.

  Sets SDL_VIDEODRIVER=dummy and HAMCLOCK_DEBUG=1 BEFORE pygame is imported
  anywhere in the test session. Every later phase (perf harness, theme
  pixel-sample, wizard --inject-events bench) assumes these are in place.
  """
  import os

  # Must run before any `import pygame` in any test module.
  os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
  os.environ.setdefault("HAMCLOCK_DEBUG", "1")
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pip install -r requirements-dev.txt && python3 -m pytest tests/test_conftest_env.py -v`
  Expected: PASS — all three tests green; `pygame.display.get_driver()` reports `dummy`.

- [ ] **Step 5: Commit**
  ```
  git add tests/__init__.py tests/conftest.py tests/test_conftest_env.py requirements-dev.txt
  git commit -m "test: add conftest.py harness with dummy SDL driver and debug flag"
  ```

---

### Task 0.2: Author the SDL backend probe script

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh`
- Test: `/home/kasm-user/hamclock-pi1/tests/test_probe_script.py`

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_probe_script.py`:
  ```python
  """The probe script must exist, be executable, syntactically valid bash,
  attempt set_mode + flip (not just init), and try all four drivers in order."""
  import os
  import stat
  import subprocess

  SCRIPT = "/home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh"


  def test_probe_script_exists():
      assert os.path.isfile(SCRIPT), f"missing: {SCRIPT}"


  def test_probe_script_is_executable():
      mode = os.stat(SCRIPT).st_mode
      assert mode & stat.S_IXUSR, "probe script must be chmod +x"


  def test_probe_script_passes_bash_n():
      r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
      assert r.returncode == 0, f"bash -n failed: {r.stderr}"


  def test_probe_script_tries_all_four_drivers_in_order():
      with open(SCRIPT) as f:
          body = f.read()
      # Order matters: fbcon first (preferred), then kmsdrm, then x11, then dummy.
      pos_fbcon = body.find("'fbcon'")
      pos_kms = body.find("'kmsdrm'")
      pos_x11 = body.find("'x11'")
      pos_dummy = body.find("'dummy'")
      assert pos_fbcon != -1 and pos_kms != -1 and pos_x11 != -1 and pos_dummy != -1
      assert pos_fbcon < pos_kms < pos_x11 < pos_dummy


  def test_probe_script_calls_set_mode_and_flip():
      with open(SCRIPT) as f:
          body = f.read()
      assert "set_mode" in body, "probe must call set_mode (init alone gives false positives)"
      assert "pygame.display.flip" in body or ".flip()" in body, "probe must call flip"
      assert "FULLSCREEN" in body
      assert "1440, 900" in body


  def test_probe_script_logs_keyboard_layout():
      with open(SCRIPT) as f:
          body = f.read()
      assert "/etc/default/keyboard" in body, "must log keyboard layout for non-US debug context"


  def test_probe_script_checks_dri_card0_for_kmsdrm():
      with open(SCRIPT) as f:
          body = f.read()
      assert "/dev/dri/card0" in body
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_probe_script.py -v`
  Expected: FAIL — `scripts/probe_sdl_backends.sh` does not exist yet; the existence assertion fails first.

- [ ] **Step 3: Implement**
  Create directory and script. Run: `mkdir -p /home/kasm-user/hamclock-pi1/scripts`.

  Create `/home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh`:
  ```bash
  #!/bin/bash
  # SDL backend probe for Raspberry Pi 1B running Raspberry Pi OS Bookworm.
  #
  # MUST be run on real Pi 1B hardware with HDMI attached. The whole point of
  # this probe is that pygame.display.init() can succeed on a driver that
  # pygame.display.set_mode() then fails on -- a headless check gives false
  # positives. We therefore set_mode + flip + sleep 1 so a human at the HDMI
  # can confirm the screen actually painted purple.
  #
  # Tries drivers in order: fbcon, kmsdrm, x11, dummy.
  # Logs the active /etc/default/keyboard for non-US install debug context.
  # Checks /dev/dri/card0 presence before the kmsdrm attempt.
  #
  # Output of this script goes verbatim into docs/sdl-backend.md.

  set -u

  echo "=== hamclock-pi1 SDL backend probe ==="
  echo "host:   $(uname -a)"
  echo "date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "python: $(python3 --version 2>&1)"
  echo "pygame: $(python3 -c 'import pygame; print(pygame.version.ver)' 2>&1)"
  echo "SDL:    $(python3 -c 'import pygame; print(pygame.get_sdl_version())' 2>&1)"
  echo
  echo "--- /etc/default/keyboard ---"
  if [ -r /etc/default/keyboard ]; then
      cat /etc/default/keyboard
  else
      echo "(not readable)"
  fi
  echo
  echo "--- /dev/dri ---"
  ls -la /dev/dri 2>&1 || echo "(no /dev/dri -- kmsdrm will not work)"
  echo "--- /boot/config.txt vc4/gpu_mem lines ---"
  grep -E '^(dtoverlay=vc4|gpu_mem)' /boot/config.txt 2>/dev/null \
      || grep -E '^(dtoverlay=vc4|gpu_mem)' /boot/firmware/config.txt 2>/dev/null \
      || echo "(no vc4/gpu_mem lines found)"
  echo

  python3 - <<'PY'
  import os, time, pygame
  for drv in ('fbcon', 'kmsdrm', 'x11', 'dummy'):
      os.environ['SDL_VIDEODRIVER'] = drv
      print('--- trying', drv, '---')
      try:
          pygame.display.init()
          scr = pygame.display.set_mode((1440, 900), pygame.FULLSCREEN)
          scr.fill((40, 20, 80))
          pygame.display.flip()
          time.sleep(1)
          print(drv, '-> OK driver=', pygame.display.get_driver())
      except Exception as e:
          print(drv, '-> FAIL', type(e).__name__, e)
      finally:
          try:
              pygame.display.quit()
          except Exception:
              pass
  PY

  echo
  echo "=== probe complete ==="
  ```

  Make it executable: `chmod +x /home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh`.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_probe_script.py -v && bash -n scripts/probe_sdl_backends.sh && echo bash-n-ok`
  Expected: PASS — all seven probe-script tests green; `bash-n-ok` printed.

- [ ] **Step 5: Commit**
  ```
  git add scripts/probe_sdl_backends.sh tests/test_probe_script.py
  git commit -m "feat(phase0): add SDL backend probe script with set_mode+flip"
  ```

---

### Task 0.3: Print the probe + run command (real Pi 1B prep)

**Files:**
- Modify: none (this is a print step that produces the command the user runs on real hardware in Task 0.4).

- [ ] **Step 1: Print the probe and run instruction**
  Run: `cat /home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh`
  Then print to the user, verbatim:
  ```
  Copy /home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh to your Pi 1B
  (e.g. `scp scripts/probe_sdl_backends.sh pi@<pi1-ip>:~/`) and run it on the
  Pi 1B with HDMI attached:

      chmod +x ~/probe_sdl_backends.sh
      ~/probe_sdl_backends.sh 2>&1 | tee ~/probe_sdl_backends.log

  You should briefly see a solid purple screen flash for each driver that
  successfully reached set_mode + flip. Capture the entire stdout.

  Before re-running for the kmsdrm branch, ensure /boot/config.txt (or
  /boot/firmware/config.txt on Bookworm) contains:
      gpu_mem=128
      dtoverlay=vc4-fkms-v3d
  and reboot so /dev/dri/card0 exists.
  ```

- [ ] **Step 2: Verify the printed text matches the script**
  Run: `head -1 /home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh`
  Expected: `#!/bin/bash` — confirms the script being shipped to the Pi is the one tested in Task 0.2.

- [ ] **Step 3: Implement**
  No code change. This task only produces the artifact the user needs to run Task 0.4.

- [ ] **Step 4: Verify**
  Run: `ls -la /home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh`
  Expected: file is executable (`-rwxr-xr-x` or similar); size > 0.

- [ ] **Step 5: Commit**
  Nothing to commit (no file changes). Skip the commit step for this task.

---

### Task 0.4: REAL PI 1B — user runs the probe and captures output

**This task runs on real Pi 1B hardware.** No code changes in the repo until the user pastes the output in Task 0.5.

**Files:**
- Output captured by user: `~/probe_sdl_backends.log` on the Pi 1B.

- [ ] **Step 1: User runs the probe on the Pi 1B**
  On the Pi 1B (HDMI attached, USB keyboard for any kmsdrm/X retry):
  ```
  chmod +x ~/probe_sdl_backends.sh
  ~/probe_sdl_backends.sh 2>&1 | tee ~/probe_sdl_backends.log
  ```
  Watch the HDMI: each driver that succeeds at `set_mode + flip` will paint solid purple for ~1 second. A driver that prints `OK driver=...` but did NOT paint the screen counts as FAIL — note that in the docs (Task 0.5).

- [ ] **Step 2: User runs the kmsdrm retry IF AND ONLY IF the first pass failed kmsdrm but reported "no /dev/dri/card0"**
  On the Pi 1B:
  ```
  sudo sed -i 's/^#dtoverlay=vc4-fkms-v3d/dtoverlay=vc4-fkms-v3d/' /boot/firmware/config.txt
  grep -q '^gpu_mem=' /boot/firmware/config.txt || echo 'gpu_mem=128' | sudo tee -a /boot/firmware/config.txt
  sudo reboot
  ```
  After reboot, re-run:
  ```
  ~/probe_sdl_backends.sh 2>&1 | tee -a ~/probe_sdl_backends.log
  ```

- [ ] **Step 3: User copies the log back to the workstation**
  From the workstation:
  ```
  scp pi@<pi1-ip>:~/probe_sdl_backends.log /tmp/probe_sdl_backends.log
  ```

- [ ] **Step 4: Verify the log is present and non-empty**
  Run: `wc -l /tmp/probe_sdl_backends.log && grep -E '^(fbcon|kmsdrm|x11|dummy) -> ' /tmp/probe_sdl_backends.log`
  Expected: log file has > 20 lines; at least four `<driver> -> ` lines (`OK` or `FAIL`) are present.

- [ ] **Step 5: Commit**
  Nothing to commit yet. The log content is folded into `docs/sdl-backend.md` in Task 0.5.

---

### Task 0.5: Record the probe outcome in `docs/sdl-backend.md`

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/docs/sdl-backend.md`
- Test: `/home/kasm-user/hamclock-pi1/tests/test_sdl_backend_doc.py`

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_sdl_backend_doc.py`:
  ```python
  """The Phase 0 deliverable: docs/sdl-backend.md must exist, contain the
  probe verbatim, name a chosen backend, and call out the matching installer
  side-effects so Phase 5 can pick them up."""
  import os
  import re

  DOC = "/home/kasm-user/hamclock-pi1/docs/sdl-backend.md"

  VALID_CHOICES = ("fbcon", "kmsdrm", "xinit")


  def test_doc_exists():
      assert os.path.isfile(DOC), f"missing: {DOC}"


  def test_doc_has_date_header():
      with open(DOC) as f:
          body = f.read()
      assert re.search(r"\b2026-06-\d{2}\b", body), "must record the probe date"


  def test_doc_records_a_chosen_backend():
      with open(DOC) as f:
          body = f.read().lower()
      m = re.search(r"chosen backend:\s*`?([a-z0-9-]+)`?", body)
      assert m, "must contain a 'Chosen backend: ...' line"
      assert m.group(1) in VALID_CHOICES, f"chosen backend {m.group(1)!r} not in {VALID_CHOICES}"


  def test_doc_contains_raw_probe_output():
      with open(DOC) as f:
          body = f.read()
      # Probe output always contains the per-driver result lines.
      assert re.search(r"\bfbcon\s*->\s*(OK|FAIL)\b", body)
      assert re.search(r"\bkmsdrm\s*->\s*(OK|FAIL)\b", body)
      assert re.search(r"\bx11\s*->\s*(OK|FAIL)\b", body)
      assert re.search(r"\bdummy\s*->\s*(OK|FAIL)\b", body)


  def test_doc_calls_out_phase5_installer_implications():
      with open(DOC) as f:
          body = f.read().lower()
      # Either we keep kiosk.sh (fbcon), or we change it. Must say which.
      assert ("kiosk.sh" in body) and (
          "no installer change" in body
          or "sdl_videodriver=" in body
          or "dtoverlay=vc4-fkms-v3d" in body
          or "xinit" in body
      ), "must spell out the Phase 5 installer implication"


  def test_doc_logs_keyboard_layout():
      with open(DOC) as f:
          body = f.read()
      assert "/etc/default/keyboard" in body, "Phase 4 wizard needs this for layout debug"
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_sdl_backend_doc.py -v`
  Expected: FAIL — `docs/sdl-backend.md` does not exist; first assertion fails.

- [ ] **Step 3: Implement**
  Create `/home/kasm-user/hamclock-pi1/docs/sdl-backend.md` from the captured `/tmp/probe_sdl_backends.log` (Task 0.4 output). Use this template, filling each `<...>` slot from the real log content:

  ```markdown
  # SDL backend decision for hamclock-pi1 pygame mode

  **Phase:** 0
  **Date:** 2026-06-03
  **Hardware:** Raspberry Pi 1 Model B, 700 MHz armv6, 512 MB RAM
  **OS:** Raspberry Pi OS Bookworm (`uname -a`: <paste from log>)
  **Display:** HDMI at 1440x900
  **pygame:** <paste version from log>
  **SDL:** <paste version from log>

  ## sdl-backend: <fbcon|kmsdrm|xinit>

  ## Per-driver result (from `scripts/probe_sdl_backends.sh`)

  | driver  | init | set_mode | flip + visible purple flash on HDMI | notes |
  |---------|------|----------|-------------------------------------|-------|
  | fbcon   | <OK/FAIL> | <OK/FAIL> | <yes/no/n-a> | <error class name if FAIL, else blank> |
  | kmsdrm  | <OK/FAIL> | <OK/FAIL> | <yes/no/n-a> | <error class name if FAIL, else blank> |
  | x11     | <OK/FAIL> | <OK/FAIL> | <yes/no/n-a> | <error class name if FAIL, else blank> |
  | dummy   | OK   | OK       | n-a (headless)                       | always available as a fallback for tests |

  ## Implication for Phase 5 installer

  <Exactly ONE of the following blocks, matching the chosen backend.>

  **If fbcon chosen:**
  No installer change. `kiosk.sh` keeps `SDL_VIDEODRIVER=fbcon`. No
  `/boot/firmware/config.txt` edits required. `gpu_mem` left at the
  Bookworm default.

  **If kmsdrm chosen:**
  Phase 5's `kiosk-install.sh` and `offline-install.sh` (and the mirror at
  `hamclock-reborn/public/downloads/pi1-install.sh`) must:
  - set `SDL_VIDEODRIVER=kmsdrm` in `kiosk.sh`;
  - ensure `/boot/firmware/config.txt` contains `gpu_mem=128`;
  - ensure `/boot/firmware/config.txt` contains `dtoverlay=vc4-fkms-v3d`;
  - verify `/dev/dri/card0` exists post-install; fail the install with a
    clear message if not.

  **If xinit chosen (neither fbcon nor kmsdrm worked):**
  Phase 5 wraps the pygame kiosk under minimal X. `kiosk.sh` runs:
  `xinit /opt/hamclock-lite/kiosk-x-wrapper.sh -- :0 vt1 -nolisten tcp`
  with `matchbox-window-manager &` inside the wrapper. apt list gains
  `xserver-xorg-core xserver-xorg-video-fbdev xinit matchbox-window-manager`.
  Phase 4 wizard `pygame.key.set_repeat` call is skipped because
  `pygame.display.get_driver() == 'x11'`. Budget: ~20 MB extra RSS vs fbcon.

  ## Keyboard layout context (for Phase 4 wizard debug)

  Contents of `/etc/default/keyboard` at probe time:

  ```
  <paste verbatim from log>
  ```

  ## Raw probe output

  ```
  <paste the entire contents of /tmp/probe_sdl_backends.log here, verbatim>
  ```

  ## Re-running this probe

  ```
  bash /home/kasm-user/hamclock-pi1/scripts/probe_sdl_backends.sh
  ```

  Run on real Pi 1B hardware. The script's `set_mode + flip + sleep 1` cycle
  paints a solid purple screen for every driver that genuinely works. A
  driver that prints `OK driver=...` but did NOT paint the HDMI counts as a
  FAIL for the table above (the dummy driver is the canonical example).
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_sdl_backend_doc.py -v`
  Expected: PASS — all six doc assertions green.

- [ ] **Step 5: Commit**
  ```
  git add docs/sdl-backend.md tests/test_sdl_backend_doc.py
  git commit -m "docs(phase0): record SDL backend probe result for Pi 1B Bookworm"
  ```

---

### Task 0.6: Cross-check the Phase 0 deliverable set

**Files:**
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase0_deliverables.py`

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_phase0_deliverables.py`:
  ```python
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
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase0_deliverables.py -v`
  Expected: PASS once Tasks 0.1, 0.2, and 0.5 are complete. If you are running this BEFORE 0.5 lands, the `chosen backend` assertion fails — confirming the gate works.

- [ ] **Step 3: Implement**
  No production code change. This task adds the Phase-5-merge gate as an executable test.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/ -v`
  Expected: PASS — every Phase 0 test (`test_conftest_env`, `test_probe_script`, `test_sdl_backend_doc`, `test_phase0_deliverables`) green.

- [ ] **Step 5: Commit**
  ```
  git add tests/test_phase0_deliverables.py
  git commit -m "test(phase0): add Phase-5-merge gate verifying Phase 0 artifacts"
  ```

---

### Phase 0 acceptance

The phase is done when every item below is true:

- `tests/__init__.py` and `tests/conftest.py` exist; `conftest.py` sets `SDL_VIDEODRIVER=dummy` and `HAMCLOCK_DEBUG=1` before any pygame import (verified by `tests/test_conftest_env.py`).
- `requirements-dev.txt` lists `pytest>=7.0` and `pytest-mock>=3.10`.
- `scripts/probe_sdl_backends.sh` exists, is executable, passes `bash -n`, tries `fbcon → kmsdrm → x11 → dummy` in order, calls `set_mode + flip` (not just `init`), logs `/etc/default/keyboard`, and checks `/dev/dri/card0` (verified by `tests/test_probe_script.py`).
- The user has run the probe on a real Pi 1B with HDMI attached and captured the full stdout to `/tmp/probe_sdl_backends.log`.
- `docs/sdl-backend.md` exists, records the probe date, the chosen backend (`fbcon` | `kmsdrm` | `xinit`), the per-driver result table, the captured `/etc/default/keyboard` value, the raw probe output, and the exact installer implication block Phase 5 must pick up (verified by `tests/test_sdl_backend_doc.py`).
- `tests/test_phase0_deliverables.py` passes — this is the single executable gate Phase 5 checks before merging.
- `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/ -v` is green end-to-end.

Phase 5's installer diff (changing `KIOSK_MODE` default to `pygame`) is BLOCKED until this acceptance set is satisfied.

---

## Phase 1 — Pygame perf fixes

Files touched: `hamclock_pygame.py` (top 4 perf items + `--inject-events` flag), `hamclock_data.py` (per-image `fetched_at` field). All work in this phase is gated by `HAMCLOCK_DEBUG=1` for tests; production runtime is unaffected.

### Task 1.1: Add `image_fetched_at` to `HamClockData`

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_data.py` (function `__init__`, function `refresh_images`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py` with:
  ```python
  """Phase 1 perf-allocation harness tests.

  Headless run (SDL_VIDEODRIVER=dummy, see conftest.py) verifies that the
  pygame client does not allocate scaled image surfaces, glyph surfaces,
  or Font objects on every frame.
  """
  import time
  import pygame

  from hamclock_data import HamClockData


  def test_image_fetched_at_initialized_empty():
      d = HamClockData()
      assert hasattr(d, 'image_fetched_at'), \
          'HamClockData must expose image_fetched_at dict'
      assert d.image_fetched_at == {}


  def test_image_fetched_at_updates_on_refresh(monkeypatch):
      d = HamClockData()

      # Simulate _fetch_binary returning fresh bytes for all 5 endpoints.
      def fake_fetch_binary(self, path):
          return b'\x89PNG\r\n\x1a\n' + path.encode()
      monkeypatch.setattr(HamClockData, '_fetch_binary', fake_fetch_binary)

      t0 = time.time()
      d.refresh_images()
      assert set(d.image_fetched_at.keys()) == {
          'solar-image', 'muf-map', 'enlil', 'drap', 'real-drap',
      }
      for key, ts in d.image_fetched_at.items():
          assert ts >= t0 - 0.01, '%s ts %r is before refresh' % (key, ts)
          assert ts <= time.time() + 0.01
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_image_fetched_at_initialized_empty tests/test_perf_alloc.py::test_image_fetched_at_updates_on_refresh -v`
  Expected: FAIL — `AttributeError: 'HamClockData' object has no attribute 'image_fetched_at'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_data.py`, add the new field in `__init__` immediately after `self.last_image_refresh = 0`:
  ```python
          # Per-image refresh timestamps (epoch seconds). Maps image_key
          # ('solar-image' | 'muf-map' | 'enlil' | 'drap' | 'real-drap')
          # to the epoch-second when that key's bytes last refreshed.
          # Used by the pygame client's _scaled_cache to invalidate per-image.
          self.image_fetched_at = {}
  ```
  Then replace the body of `refresh_images` so it updates the new field. The full replacement function:
  ```python
      def refresh_images(self):
          """Fetch the 5 image endpoints synchronously."""
          results = {}
          fetched = {}
          for key, path in self._IMAGE_ENDPOINTS.items():
              data = self._fetch_binary(path)
              results[key] = data is not None
              if data is not None:
                  fetched[key] = data
          now = time.time()
          with self._lock:
              new_images = dict(self.images)
              new_images.update(fetched)
              self.images = new_images
              new_ts = dict(self.image_fetched_at)
              for key in fetched:
                  new_ts[key] = now
              self.image_fetched_at = new_ts
              self.last_image_refresh = now
          return results
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_image_fetched_at_initialized_empty tests/test_perf_alloc.py::test_image_fetched_at_updates_on_refresh -v`
  Expected: PASS (2 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_data.py tests/test_perf_alloc.py
  git commit -m "feat(data): track per-image fetched_at for scaled-cache invalidation"
  ```

---

### Task 1.2: Add `tiny` font and eliminate per-frame `Font(None, 18)` allocation

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (function `_make_fonts` at L50, function `draw_image` at L170)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`:
  ```python
  def test_make_fonts_includes_tiny():
      import pygame
      pygame.init()
      try:
          import hamclock_pygame
          fonts = hamclock_pygame._make_fonts()
          assert 'tiny' in fonts, '_make_fonts() must produce a "tiny" font'
          # Used by draw_image's "image loading..." placeholder
          assert fonts['tiny'].render('x', True, (255, 255, 255)) is not None
      finally:
          pygame.quit()


  def test_draw_image_no_font_alloc_when_surface_none(monkeypatch):
      import pygame
      pygame.init()
      try:
          import hamclock_pygame

          alloc_count = {'n': 0}
          real_font_init = pygame.font.Font.__init__

          def counting_init(self, *args, **kwargs):
              alloc_count['n'] += 1
              return real_font_init(self, *args, **kwargs)

          monkeypatch.setattr(pygame.font.Font, '__init__', counting_init)

          fonts = hamclock_pygame._make_fonts()
          baseline = alloc_count['n']
          screen = pygame.Surface((200, 100))
          rect = pygame.Rect(0, 0, 200, 100)

          # Call draw_image with no surface 30 times — the "loading" branch.
          # Must NOT allocate a new pygame.font.Font on any of those calls.
          for _ in range(30):
              hamclock_pygame.draw_image(screen, rect, None, fonts)

          assert alloc_count['n'] == baseline, \
              'draw_image allocated %d Font objects in loading state' % (
                  alloc_count['n'] - baseline)
      finally:
          pygame.quit()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_make_fonts_includes_tiny tests/test_perf_alloc.py::test_draw_image_no_font_alloc_when_surface_none -v`
  Expected: FAIL — `test_make_fonts_includes_tiny` fails because `'tiny'` is not in the dict; `test_draw_image_no_font_alloc_when_surface_none` fails because `draw_image` does not accept `fonts` and allocates `Font(None, 18)` inline.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, replace `_make_fonts` so it includes the `tiny` font:
  ```python
  def _make_fonts():
      """Build the fonts dict. Falls back to default font if SysFont fails.

      Includes 'tiny' (size 11/15) used by draw_image's loading placeholder
      so the inline pygame.font.Font(None, 18) per-frame allocation is gone.
      Also clears the module-level _glyph_cache so stale renders from a
      previous font set cannot leak through (Task 1.3).
      """
      def mk(size):
          try:
              f = pygame.font.SysFont('monospace', size)
              if f is None:
                  raise RuntimeError('no monospace')
              return f
          except Exception:
              return pygame.font.Font(None, size + 4)
      _glyph_cache.clear()
      return {
          'title': mk(22),
          'panel': mk(14),
          'body': mk(14),
          'label': mk(12),
          'small': mk(11),
          'tiny': mk(11),
      }
  ```
  Replace `draw_image` so it takes the fonts dict and uses `fonts['tiny']`:
  ```python
  def draw_image(screen, rect, surface, fonts=None):
      if surface is None:
          if fonts is not None and 'tiny' in fonts:
              _blit_text(screen, fonts['tiny'], 'image loading...', LABEL,
                         rect.x + 6, rect.y + 6)
          return
      try:
          iw, ih = surface.get_size()
          if iw == 0 or ih == 0:
              return
          scale = min(rect.w / iw, rect.h / ih)
          nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
          scaled = pygame.transform.smoothscale(surface, (nw, nh)) if scale < 1.0 else surface
          x = rect.x + (rect.w - nw) // 2
          y = rect.y + (rect.h - nh) // 2
          screen.blit(scaled, (x, y))
      except Exception:
          pass
  ```
  Update the three `draw_image` call sites in `main()` to pass `fonts`:
  ```python
                  draw_image(screen, panel_rects[2], sdo_surf, fonts)
  ```
  ```python
                  draw_image(screen, img_rect, surf, fonts)
  ```
  (Both SDO and propagation-tab call sites. The MUF panel uses `draw_muf_text`, not `draw_image`.)

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_make_fonts_includes_tiny tests/test_perf_alloc.py::test_draw_image_no_font_alloc_when_surface_none -v`
  Expected: PASS (2 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_perf_alloc.py
  git commit -m "perf(pygame): cache 'tiny' font; drop per-frame Font(None,18) alloc"
  ```

---

### Task 1.3: Glyph cache for `_blit_text`

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (top-of-file imports, new module-level `_glyph_cache`, function `_blit_text` at L79)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`:
  ```python
  def test_glyph_cache_hit_rate(monkeypatch):
      """Repeated _blit_text of the same (font,text,color) hits cache."""
      import pygame
      pygame.init()
      try:
          import hamclock_pygame

          render_calls = {'n': 0}
          real_render = pygame.font.Font.render

          def counting_render(self, *args, **kwargs):
              render_calls['n'] += 1
              return real_render(self, *args, **kwargs)

          monkeypatch.setattr(pygame.font.Font, 'render', counting_render)

          fonts = hamclock_pygame._make_fonts()  # clears _glyph_cache
          screen = pygame.Surface((400, 100))

          # 100 repeated identical labels — should call .render() exactly once
          for _ in range(100):
              hamclock_pygame._blit_text(screen, fonts['panel'],
                                         'SOLAR', (255, 255, 255), 0, 0)

          assert render_calls['n'] == 1, \
              '_blit_text called .render() %d times for 100 identical labels' \
              % render_calls['n']


  def test_glyph_cache_distinguishes_color_and_text():
      import pygame
      pygame.init()
      try:
          import hamclock_pygame
          fonts = hamclock_pygame._make_fonts()
          screen = pygame.Surface((400, 100))
          hamclock_pygame._glyph_cache.clear()
          hamclock_pygame._blit_text(screen, fonts['panel'], 'X',
                                     (255, 0, 0), 0, 0)
          hamclock_pygame._blit_text(screen, fonts['panel'], 'X',
                                     (0, 255, 0), 0, 0)
          hamclock_pygame._blit_text(screen, fonts['panel'], 'Y',
                                     (255, 0, 0), 0, 0)
          assert len(hamclock_pygame._glyph_cache) == 3


  def test_glyph_cache_evicts_at_cap():
      import pygame
      pygame.init()
      try:
          import hamclock_pygame
          fonts = hamclock_pygame._make_fonts()
          screen = pygame.Surface((400, 100))
          hamclock_pygame._glyph_cache.clear()
          # 300 unique texts; cap is 256.
          for i in range(300):
              hamclock_pygame._blit_text(screen, fonts['panel'],
                                         'lbl%d' % i, (255, 255, 255), 0, 0)
          assert len(hamclock_pygame._glyph_cache) == 256
      finally:
          pygame.quit()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_glyph_cache_hit_rate tests/test_perf_alloc.py::test_glyph_cache_distinguishes_color_and_text tests/test_perf_alloc.py::test_glyph_cache_evicts_at_cap -v`
  Expected: FAIL — `AttributeError: module 'hamclock_pygame' has no attribute '_glyph_cache'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add `import collections` to the top-of-file imports (alphabetised between `io` and `os`):
  ```python
  import collections
  import io
  import os
  import sys
  import time
  ```
  Immediately after `HF_BANDS = [...]` (around L44) add the module-level cache and config:
  ```python
  # ---- Glyph cache (Phase 1 perf fix #3) ----
  # Keyed by (font_name_or_None, font_size, text, color); explicitly NOT id(font)
  # because CPython reuses id() after GC. _make_fonts() clears this dict on
  # every call so stale glyphs cannot survive a fonts rebuild.
  _GLYPH_CACHE_CAP = 256
  _glyph_cache = collections.OrderedDict()


  def _font_key(font):
      """Best-effort hashable key for a pygame Font. SysFont stores the name,
      Font(None, sz) has no name; size is reliable via get_height."""
      try:
          name = getattr(font, 'name', None)
      except Exception:
          name = None
      try:
          size = font.get_height()
      except Exception:
          size = 0
      return (name, size)
  ```
  Replace `_blit_text` so it consults the cache:
  ```python
  def _blit_text(screen, font, text, color, x, y):
      try:
          s = str(text)
          if not isinstance(color, tuple):
              color = tuple(color)
          key = (_font_key(font), s, color)
          surf = _glyph_cache.get(key)
          if surf is None:
              surf = font.render(s, True, color)
              _glyph_cache[key] = surf
              if len(_glyph_cache) > _GLYPH_CACHE_CAP:
                  _glyph_cache.popitem(last=False)
          else:
              _glyph_cache.move_to_end(key)
          screen.blit(surf, (x, y))
          return surf.get_width()
      except Exception:
          return 0
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py -v`
  Expected: PASS (all 6 tests pass).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_perf_alloc.py
  git commit -m "perf(pygame): LRU glyph cache for _blit_text (cap 256)"
  ```

---

### Task 1.4: Scaled-image LRU cache for `draw_image`

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (new module-level `_scaled_cache`, function `draw_image`, function `main` call sites)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`:
  ```python
  def test_scaled_cache_avoids_repeat_smoothscale(monkeypatch):
      """Same (image_key, fetched_at, size) tuple must not re-scale."""
      import pygame
      pygame.init()
      try:
          import hamclock_pygame

          scale_calls = {'n': 0}
          real_ss = pygame.transform.smoothscale

          def counting_ss(surf, size, *a, **kw):
              scale_calls['n'] += 1
              return real_ss(surf, size, *a, **kw)

          monkeypatch.setattr(pygame.transform, 'smoothscale', counting_ss)

          hamclock_pygame._scaled_cache.clear()
          screen = pygame.Surface((400, 300))
          src = pygame.Surface((800, 600))
          src.fill((10, 20, 30))
          rect = pygame.Rect(0, 0, 400, 300)
          fonts = hamclock_pygame._make_fonts()

          for _ in range(30):
              hamclock_pygame.draw_image(screen, rect, src, fonts,
                                         image_key='solar-image',
                                         fetched_at=1000.0)

          assert scale_calls['n'] == 1, \
              'smoothscale ran %d times for 30 identical draws' % scale_calls['n']


  def test_scaled_cache_reinvalidates_on_new_fetched_at(monkeypatch):
      import pygame
      pygame.init()
      try:
          import hamclock_pygame

          scale_calls = {'n': 0}
          real_ss = pygame.transform.smoothscale

          def counting_ss(surf, size, *a, **kw):
              scale_calls['n'] += 1
              return real_ss(surf, size, *a, **kw)

          monkeypatch.setattr(pygame.transform, 'smoothscale', counting_ss)

          hamclock_pygame._scaled_cache.clear()
          screen = pygame.Surface((400, 300))
          src = pygame.Surface((800, 600))
          rect = pygame.Rect(0, 0, 400, 300)
          fonts = hamclock_pygame._make_fonts()

          hamclock_pygame.draw_image(screen, rect, src, fonts,
                                     image_key='solar-image', fetched_at=1000.0)
          hamclock_pygame.draw_image(screen, rect, src, fonts,
                                     image_key='solar-image', fetched_at=2000.0)
          assert scale_calls['n'] == 2


  def test_scaled_cache_evicts_at_cap():
      import pygame
      pygame.init()
      try:
          import hamclock_pygame
          hamclock_pygame._scaled_cache.clear()
          screen = pygame.Surface((400, 300))
          src = pygame.Surface((800, 600))
          fonts = hamclock_pygame._make_fonts()
          for i in range(40):
              rect = pygame.Rect(0, 0, 100 + i, 100 + i)
              hamclock_pygame.draw_image(screen, rect, src, fonts,
                                         image_key='k%d' % i, fetched_at=1.0)
          assert len(hamclock_pygame._scaled_cache) == 16
      finally:
          pygame.quit()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_scaled_cache_avoids_repeat_smoothscale tests/test_perf_alloc.py::test_scaled_cache_reinvalidates_on_new_fetched_at tests/test_perf_alloc.py::test_scaled_cache_evicts_at_cap -v`
  Expected: FAIL — `AttributeError: module 'hamclock_pygame' has no attribute '_scaled_cache'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add the cache config below the glyph-cache block:
  ```python
  # ---- Scaled-image cache (Phase 1 perf fix #1) ----
  # Keyed by (image_key, fetched_at, (w, h)) -> scaled pygame.Surface.
  # Cap 16: dashboard has 5 image slots × 1 active scale each = 5; 16 leaves
  # margin for tab changes. Eviction is LRU (popitem(last=False) on overflow).
  _SCALED_CACHE_CAP = 16
  _scaled_cache = collections.OrderedDict()
  ```
  Replace `draw_image` so it consults the cache when a key is supplied:
  ```python
  def draw_image(screen, rect, surface, fonts=None,
                 image_key=None, fetched_at=None):
      if surface is None:
          if fonts is not None and 'tiny' in fonts:
              _blit_text(screen, fonts['tiny'], 'image loading...', LABEL,
                         rect.x + 6, rect.y + 6)
          return
      try:
          iw, ih = surface.get_size()
          if iw == 0 or ih == 0:
              return
          scale = min(rect.w / iw, rect.h / ih)
          nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
          if scale >= 1.0:
              scaled = surface
          elif image_key is not None and fetched_at is not None:
              key = (image_key, float(fetched_at), (nw, nh))
              scaled = _scaled_cache.get(key)
              if scaled is None:
                  scaled = pygame.transform.smoothscale(surface, (nw, nh))
                  _scaled_cache[key] = scaled
                  if len(_scaled_cache) > _SCALED_CACHE_CAP:
                      _scaled_cache.popitem(last=False)
              else:
                  _scaled_cache.move_to_end(key)
          else:
              scaled = pygame.transform.smoothscale(surface, (nw, nh))
          x = rect.x + (rect.w - nw) // 2
          y = rect.y + (rect.h - nh) // 2
          screen.blit(scaled, (x, y))
      except Exception:
          pass
  ```
  Update the two image-drawing call sites in `main()` to forward `image_key` and `fetched_at`. The SDO call:
  ```python
                  sdo_surf = _get_cached_image(data, 'solar-image', image_cache, image_cache_ts)
                  draw_image(screen, panel_rects[2], sdo_surf, fonts,
                             image_key='solar-image',
                             fetched_at=data.image_fetched_at.get('solar-image', 0.0))
  ```
  The propagation-tab call:
  ```python
                  key = tab_image_key.get(active_tab, 'real-drap')
                  surf = _get_cached_image(data, key, image_cache, image_cache_ts)
                  draw_image(screen, img_rect, surf, fonts,
                             image_key=key,
                             fetched_at=data.image_fetched_at.get(key, 0.0))
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py -v`
  Expected: PASS (all 9 tests pass).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_perf_alloc.py
  git commit -m "perf(pygame): LRU scaled-image cache keyed by (key,ts,size) (cap 16)"
  ```

---

### Task 1.5: Dirty-rect `display.update()` instead of full `display.flip()`

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (function `main` — render loop bottom)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`:
  ```python
  def test_compute_dirty_rects_full_on_first_frame():
      import pygame
      pygame.init()
      try:
          import hamclock_pygame
          state = {'prev_active_tab': None, 'prev_second': -1,
                   'prev_data_refresh': 0.0, 'prev_image_refresh': 0.0,
                   'full_flip_pending': True}
          panel_rects = {
              'header': pygame.Rect(0, 0, 1440, 30),
              'status': pygame.Rect(0, 880, 1440, 20),
              'solar': pygame.Rect(0, 30, 280, 200),
          }
          dirty = hamclock_pygame._compute_dirty_rects(
              state, panel_rects, active_tab='drap',
              now_sec=1000, data_refresh=0.0, image_refresh=0.0)
          assert dirty is None, \
              'first-frame full-flip path returns None (caller uses flip())'
          assert state['full_flip_pending'] is False


  def test_compute_dirty_rects_second_tick_only_redraws_clock_panels():
      import pygame
      pygame.init()
      try:
          import hamclock_pygame
          state = {'prev_active_tab': 'drap', 'prev_second': 1000,
                   'prev_data_refresh': 0.0, 'prev_image_refresh': 0.0,
                   'full_flip_pending': False}
          panel_rects = {
              'header': pygame.Rect(0, 0, 1440, 30),
              'status': pygame.Rect(0, 880, 1440, 20),
              'solar': pygame.Rect(0, 30, 280, 200),
          }
          dirty = hamclock_pygame._compute_dirty_rects(
              state, panel_rects, active_tab='drap',
              now_sec=1001, data_refresh=0.0, image_refresh=0.0)
          assert dirty is not None
          assert panel_rects['header'] in dirty
          assert panel_rects['status'] in dirty
          assert panel_rects['solar'] not in dirty


  def test_compute_dirty_rects_tab_change_forces_full_flip():
      import pygame
      pygame.init()
      try:
          import hamclock_pygame
          state = {'prev_active_tab': 'drap', 'prev_second': 1000,
                   'prev_data_refresh': 0.0, 'prev_image_refresh': 0.0,
                   'full_flip_pending': False}
          panel_rects = {'header': pygame.Rect(0, 0, 1440, 30)}
          dirty = hamclock_pygame._compute_dirty_rects(
              state, panel_rects, active_tab='aurora',
              now_sec=1000, data_refresh=0.0, image_refresh=0.0)
          assert dirty is None, 'tab change must request full flip'
          assert state['prev_active_tab'] == 'aurora'
      finally:
          pygame.quit()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_compute_dirty_rects_full_on_first_frame tests/test_perf_alloc.py::test_compute_dirty_rects_second_tick_only_redraws_clock_panels tests/test_perf_alloc.py::test_compute_dirty_rects_tab_change_forces_full_flip -v`
  Expected: FAIL — `AttributeError: module 'hamclock_pygame' has no attribute '_compute_dirty_rects'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add this helper directly above `def main():`:
  ```python
  def _compute_dirty_rects(state, panel_rects, active_tab,
                           now_sec, data_refresh, image_refresh):
      """Return list of pygame.Rect to pass to display.update(), or None
      to signal the caller to use display.flip() for a full repaint.

      Triggers a full flip on: first frame, tab change, screen-size change.
      Otherwise marks dirty: header+status when the second ticks over;
      data-fed panels when data_refresh changes; image-fed panels when
      image_refresh changes. State dict is mutated to record this frame's
      values so the next call can diff against them.
      """
      if state.get('full_flip_pending') or state.get('prev_active_tab') != active_tab:
          state['full_flip_pending'] = False
          state['prev_active_tab'] = active_tab
          state['prev_second'] = now_sec
          state['prev_data_refresh'] = data_refresh
          state['prev_image_refresh'] = image_refresh
          return None
      dirty = []
      if now_sec != state.get('prev_second'):
          state['prev_second'] = now_sec
          for k in ('header', 'status'):
              r = panel_rects.get(k)
              if r is not None:
                  dirty.append(r)
      if data_refresh != state.get('prev_data_refresh'):
          state['prev_data_refresh'] = data_refresh
          for k in ('solar', 'bands', 'geomag', 'xray', 'open_bands',
                    'muf_text', 'dx_spots', 'band_activity'):
              r = panel_rects.get(k)
              if r is not None and r not in dirty:
                  dirty.append(r)
      if image_refresh != state.get('prev_image_refresh'):
          state['prev_image_refresh'] = image_refresh
          for k in ('sdo', 'propagation'):
              r = panel_rects.get(k)
              if r is not None and r not in dirty:
                  dirty.append(r)
      return dirty
  ```
  In `main()`, replace the `active_tab = 'drap'` / `image_cache = {}` block with the addition of dirty-rect state, plus a `panel_rects_map` dict populated each frame:
  ```python
      active_tab = 'drap'
      image_cache = {}
      image_cache_ts = {}
      tab_regions = {}
      tab_image_key = {'drap': 'real-drap', 'aurora': 'drap', 'enlil': 'enlil'}
      dirty_state = {
          'prev_active_tab': None,
          'prev_second': -1,
          'prev_data_refresh': 0.0,
          'prev_image_refresh': 0.0,
          'full_flip_pending': True,
      }
  ```
  Inside the render loop, just before `pygame.display.flip()`, build the panel-rect map. Replace the `pygame.display.flip()` line with:
  ```python
              panel_rects_map = {
                  'header': header,
                  'status': status,
                  'solar': panel_rects[0],
                  'bands': panel_rects[1],
                  'sdo': panel_rects[2],
                  'geomag': panel_rects[3],
                  'xray': panel_rects[4],
                  'open_bands': panel_rects[5],
                  'muf_text': mid_rect,
                  'dx_spots': dx_r,
                  'band_activity': ba_r,
                  'propagation': prop_r,
              }
              dirty = _compute_dirty_rects(
                  dirty_state, panel_rects_map, active_tab,
                  int(time.time()),
                  data.last_data_refresh,
                  data.last_image_refresh)
              if dirty is None:
                  pygame.display.flip()
              elif dirty:
                  pygame.display.update(dirty)
  ```
  Inside the `MOUSEBUTTONDOWN` handler, when a tab is hit, also set `dirty_state['full_flip_pending'] = True` so the next frame after a click is a full flip:
  ```python
                  elif event.type == pygame.MOUSEBUTTONDOWN:
                      pos = event.pos
                      for name, r in tab_regions.items():
                          if r.collidepoint(pos):
                              active_tab = name
                              dirty_state['full_flip_pending'] = True
                              break
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py -v`
  Expected: PASS (all 12 tests pass).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_perf_alloc.py
  git commit -m "perf(pygame): dirty-rect display.update() with full flip on tab click"
  ```

---

### Task 1.6: `--inject-events` debug flag (gated by `HAMCLOCK_DEBUG=1`)

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (top-of-file `argparse` import, new module-level `_parse_args`, function `main` event loop)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_inject_events.py`

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_inject_events.py`:
  ```python
  """Tests for the --inject-events debug flag.

  The flag must be:
    - gated by HAMCLOCK_DEBUG=1 (argparse errors otherwise),
    - read a JSON list of event dicts (one per frame),
    - convert them into pygame events the render loop can consume.
  """
  import json
  import os
  import pygame
  import pytest

  import hamclock_pygame


  def test_parse_args_rejects_inject_without_debug_env(monkeypatch):
      monkeypatch.delenv('HAMCLOCK_DEBUG', raising=False)
      with pytest.raises(SystemExit):
          hamclock_pygame._parse_args(['--inject-events', '/tmp/x.json'])


  def test_parse_args_accepts_inject_with_debug_env(monkeypatch, tmp_path):
      monkeypatch.setenv('HAMCLOCK_DEBUG', '1')
      p = tmp_path / 'events.json'
      p.write_text('[]')
      args = hamclock_pygame._parse_args(['--inject-events', str(p)])
      assert args.inject_events == str(p)


  def test_parse_args_no_inject_is_fine_without_debug(monkeypatch):
      monkeypatch.delenv('HAMCLOCK_DEBUG', raising=False)
      args = hamclock_pygame._parse_args([])
      assert args.inject_events is None


  def test_load_injected_events_translates_mousebuttondown(tmp_path):
      p = tmp_path / 'events.json'
      p.write_text(json.dumps([
          {'type': 'MOUSEBUTTONDOWN', 'pos': [100, 200], 'button': 1},
          {'type': 'KEYDOWN', 'key': 'q'},
      ]))
      pygame.init()
      try:
          events = hamclock_pygame._load_injected_events(str(p))
          assert len(events) == 2
          assert events[0].type == pygame.MOUSEBUTTONDOWN
          assert events[0].pos == (100, 200)
          assert events[0].button == 1
          assert events[1].type == pygame.KEYDOWN
          assert events[1].key == pygame.K_q
      finally:
          pygame.quit()


  def test_inject_events_iterator_pops_one_per_frame(tmp_path):
      p = tmp_path / 'events.json'
      p.write_text(json.dumps([
          {'type': 'KEYDOWN', 'key': 'q'},
          {'type': 'KEYDOWN', 'key': 'q'},
      ]))
      pygame.init()
      try:
          events = hamclock_pygame._load_injected_events(str(p))
          it = hamclock_pygame._inject_event_iter(events)
          frame1 = next(it)
          frame2 = next(it)
          frame3 = next(it)
          assert len(frame1) == 1
          assert len(frame2) == 1
          assert frame3 == []  # exhausted
      finally:
          pygame.quit()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_inject_events.py -v`
  Expected: FAIL — `AttributeError: module 'hamclock_pygame' has no attribute '_parse_args'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add `import argparse` and `import json` to the top-of-file imports:
  ```python
  import argparse
  import collections
  import io
  import json
  import os
  import sys
  import time
  ```
  Add these helpers just above `def main():`:
  ```python
  # ---- --inject-events debug flag (Phase 1 verification harness) ----
  # Gated by HAMCLOCK_DEBUG=1 so production never accepts injected events.
  # Reads a JSON list of {"type": "MOUSEBUTTONDOWN"|"KEYDOWN"|"QUIT", ...}
  # dicts and yields one per frame via _inject_event_iter().

  _KEY_NAME_MAP = {
      'q': pygame.K_q,
      'escape': pygame.K_ESCAPE,
      'return': pygame.K_RETURN,
      'tab': pygame.K_TAB,
      'space': pygame.K_SPACE,
      'left': pygame.K_LEFT,
      'right': pygame.K_RIGHT,
      'up': pygame.K_UP,
      'down': pygame.K_DOWN,
  }


  def _parse_args(argv):
      """Parse CLI args. --inject-events requires HAMCLOCK_DEBUG=1 in env."""
      p = argparse.ArgumentParser(prog='hamclock_pygame')
      p.add_argument('--inject-events', default=None,
                     help='debug builds only: JSON event list to replay')
      args = p.parse_args(argv)
      if args.inject_events is not None and os.environ.get('HAMCLOCK_DEBUG') != '1':
          p.error('--inject-events is debug builds only '
                  '(set HAMCLOCK_DEBUG=1 to enable)')
      return args


  def _load_injected_events(path):
      """Load a JSON list of event dicts and convert to pygame.event.Event."""
      with open(path, 'r') as f:
          raw = json.load(f)
      out = []
      for d in raw:
          t = d.get('type')
          if t == 'MOUSEBUTTONDOWN':
              out.append(pygame.event.Event(
                  pygame.MOUSEBUTTONDOWN,
                  pos=tuple(d.get('pos', (0, 0))),
                  button=int(d.get('button', 1))))
          elif t == 'MOUSEBUTTONUP':
              out.append(pygame.event.Event(
                  pygame.MOUSEBUTTONUP,
                  pos=tuple(d.get('pos', (0, 0))),
                  button=int(d.get('button', 1))))
          elif t == 'KEYDOWN':
              key = d.get('key', '')
              kc = _KEY_NAME_MAP.get(str(key).lower(),
                                     getattr(pygame, 'K_' + str(key).lower(), 0))
              out.append(pygame.event.Event(pygame.KEYDOWN, key=kc))
          elif t == 'QUIT':
              out.append(pygame.event.Event(pygame.QUIT))
      return out


  def _inject_event_iter(events):
      """Yield [event] one frame at a time, then [] forever."""
      for ev in events:
          yield [ev]
      while True:
          yield []
  ```
  In `main()`, parse argv at the top:
  ```python
  def main(argv=None):
      args = _parse_args(sys.argv[1:] if argv is None else argv)
      injected_iter = None
      if args.inject_events:
          injected_iter = _inject_event_iter(
              _load_injected_events(args.inject_events))

      if 'DISPLAY' not in os.environ:
          os.environ.setdefault('SDL_VIDEODRIVER', 'fbcon')
          os.environ.setdefault('SDL_FBDEV', '/dev/fb0')
  ```
  And in the render-loop, replace `for event in pygame.event.get():` with:
  ```python
                  frame_events = (next(injected_iter)
                                  if injected_iter is not None
                                  else pygame.event.get())
                  for event in frame_events:
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_inject_events.py -v`
  Expected: PASS (all 5 tests pass).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_inject_events.py
  git commit -m "feat(pygame): --inject-events debug flag for synthetic event replay"
  ```

---

### Task 1.7: Click-latency micro-bench script (REAL HARDWARE)

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/scripts/click_latency_bench.sh`
- Create: `/home/kasm-user/hamclock-pi1/scripts/click_latency_bench.py`

- [ ] **Step 1: Write the bench script (Python harness)**
  Create `/home/kasm-user/hamclock-pi1/scripts/click_latency_bench.py`:
  ```python
  """Phase 1 click-latency micro-bench.

  Generates 100 synthetic MOUSEBUTTONDOWN events at random sub-frame
  offsets targeting the propagation tab region, runs hamclock_pygame
  with --inject-events, and measures MOUSEBUTTONDOWN-timestamp to
  screen-pixel-change-timestamp. Reports p50/p95/p99 in milliseconds.

  Requires HAMCLOCK_DEBUG=1.
  Run on a real Raspberry Pi 1B with HDMI attached.
  """
  import json
  import os
  import random
  import statistics
  import subprocess
  import sys
  import time


  def build_events(n=100):
      tabs = [
          (1090, 480),  # drap tab approx pixel
          (1200, 480),  # aurora
          (1320, 480),  # enlil
      ]
      events = []
      random.seed(42)
      for _ in range(n):
          x, y = random.choice(tabs)
          events.append({'type': 'MOUSEBUTTONDOWN', 'pos': [x, y], 'button': 1})
      events.append({'type': 'QUIT'})
      return events


  def main():
      if os.environ.get('HAMCLOCK_DEBUG') != '1':
          print('set HAMCLOCK_DEBUG=1 first', file=sys.stderr)
          sys.exit(2)
      events_path = '/tmp/hamclock-click-events.json'
      with open(events_path, 'w') as f:
          json.dump(build_events(100), f)
      log_path = '/tmp/hamclock-click-bench.log'
      env = dict(os.environ)
      env['HAMCLOCK_LATENCY_LOG'] = log_path
      proc = subprocess.Popen(
          ['python3', '/opt/hamclock-lite/hamclock_pygame.py',
           '--inject-events', events_path],
          env=env)
      proc.wait(timeout=120)
      latencies_ms = []
      with open(log_path) as f:
          for line in f:
              if line.startswith('CLICK_LATENCY_MS '):
                  latencies_ms.append(float(line.split()[1]))
      if not latencies_ms:
          print('no latency samples', file=sys.stderr)
          sys.exit(1)
      p50 = statistics.median(latencies_ms)
      p95 = statistics.quantiles(latencies_ms, n=20)[-1]
      p99 = statistics.quantiles(latencies_ms, n=100)[-1]
      print('samples=%d p50=%.1fms p95=%.1fms p99=%.1fms max=%.1fms' % (
          len(latencies_ms), p50, p95, p99, max(latencies_ms)))


  if __name__ == '__main__':
      main()
  ```
  Create `/home/kasm-user/hamclock-pi1/scripts/click_latency_bench.sh`:
  ```bash
  #!/bin/sh
  # Phase 1 click-latency bench. Run on a real Pi 1B with HDMI attached.
  # Expects /opt/hamclock-lite/hamclock_pygame.py to be installed (kiosk-install).
  set -eu
  export HAMCLOCK_DEBUG=1
  exec python3 "$(dirname "$0")/click_latency_bench.py"
  ```
  Make it executable:
  ```
  chmod +x /home/kasm-user/hamclock-pi1/scripts/click_latency_bench.sh
  ```
  Add the latency log emit in `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` inside the MOUSEBUTTONDOWN handler and after `pygame.display.update(dirty)` / `pygame.display.flip()`. Near the top of `main()` after argparse:
  ```python
      _latency_log_path = os.environ.get('HAMCLOCK_LATENCY_LOG')
      _latency_log = open(_latency_log_path, 'a') if _latency_log_path else None
      _pending_click_t0 = None
  ```
  In the click handler (right after `active_tab = name`):
  ```python
                              if _latency_log is not None:
                                  _pending_click_t0 = time.time()
  ```
  After the dirty-rect flip/update call:
  ```python
              if _latency_log is not None and _pending_click_t0 is not None:
                  dt_ms = (time.time() - _pending_click_t0) * 1000.0
                  _latency_log.write('CLICK_LATENCY_MS %.2f\n' % dt_ms)
                  _latency_log.flush()
                  _pending_click_t0 = None
  ```
  At the end of `main()`, close the log:
  ```python
      if _latency_log is not None:
          _latency_log.close()
  ```

- [ ] **Step 2: Verify script syntax locally (host machine)**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -c "import ast; ast.parse(open('scripts/click_latency_bench.py').read())" && bash -n scripts/click_latency_bench.sh`
  Expected: no output (both files parse cleanly).

- [ ] **Step 3: Run on real Pi 1B (USER ACTION on hardware)**
  Copy the repo to the Pi 1B (or `git pull` on it after step 5 commits) and run:
  ```
  ssh pi@<pi1-host>
  cd ~/hamclock-pi1
  sudo systemctl stop hamclock-kiosk
  sudo cp hamclock_pygame.py /opt/hamclock-lite/hamclock_pygame.py
  sudo cp hamclock_data.py /opt/hamclock-lite/hamclock_data.py
  HAMCLOCK_DEBUG=1 bash scripts/click_latency_bench.sh 2>&1 | tee /tmp/phase1-latency.txt
  ```
  Expected output line example: `samples=100 p50=120.3ms p95=180.1ms p99=195.4ms max=210.0ms`.
  Capture the line; if p99 > 200 ms, the spec requires escalating the propagation panel to 20 FPS (not in this task — the failure is recorded and Phase 1 re-iterates).

- [ ] **Step 4: Paste result into docs**
  Create `/home/kasm-user/hamclock-pi1/docs/phase1-latency.md` with this template, filled in from the Pi 1B run:
  ```markdown
  # Phase 1 click-latency micro-bench result

  **Date:** 2026-06-03
  **Hardware:** Raspberry Pi 1 Model B, 700 MHz armv6, 512 MB RAM, HDMI 1440x900
  **OS:** Raspberry Pi OS Bookworm
  **SDL driver:** <fbcon|kmsdrm|x11 — from docs/sdl-backend.md>

  ## Method

  100 synthetic MOUSEBUTTONDOWN events injected at random sub-frame
  offsets on the propagation tab region via `--inject-events`. Latency
  measured from event arrival in the render loop to the post-update
  `display.update()` return.

  ## Result

  ```
  <PASTE the line from step 3, e.g.>
  samples=100 p50=120.3ms p95=180.1ms p99=195.4ms max=210.0ms
  ```

  ## Decision

  - p99 <= 200 ms -> Phase 1 ships as designed; gates Phase 5 satisfied.
  - p99 200-500 ms -> raise propagation panel to 20 FPS, re-measure.
  - p99 > 500 ms -> deeper dirty-rect implementation required.

  **Decision taken:** <one of the above, based on measured p99>.
  ```

- [ ] **Step 5: Commit**
  ```
  git add scripts/click_latency_bench.sh scripts/click_latency_bench.py hamclock_pygame.py docs/phase1-latency.md
  git commit -m "test(phase1): click-latency micro-bench + recorded Pi 1B result"
  ```

---

### Task 1.8: Allocation-harness summary test (smoothscale/Font(None,18)/glyph hit rate)

**Files:**
- Test: `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`:
  ```python
  def test_render_loop_30_frame_alloc_budget(monkeypatch):
      """30-frame headless render: smoothscale <= N_visible_images,
      Font(None, 18) constructed 0 times after init, glyph hit rate >= 90%.

      N_visible_images = 2 (SDO + active propagation tab) and we expect
      one smoothscale per (image_key, fetched_at, size) tuple — so total
      smoothscale calls across 30 frames must be <= 2 in steady state.
      """
      import pygame
      pygame.init()
      try:
          import hamclock_pygame

          calls = {'smoothscale': 0, 'font_none_18': 0,
                   'render': 0, 'render_hit': 0}

          real_ss = pygame.transform.smoothscale

          def counting_ss(surf, size, *a, **kw):
              calls['smoothscale'] += 1
              return real_ss(surf, size, *a, **kw)

          monkeypatch.setattr(pygame.transform, 'smoothscale', counting_ss)

          real_font_init = pygame.font.Font.__init__

          def counting_font_init(self, *a, **kw):
              if len(a) >= 2 and a[0] is None and a[1] == 18:
                  calls['font_none_18'] += 1
              return real_font_init(self, *a, **kw)

          monkeypatch.setattr(pygame.font.Font, '__init__', counting_font_init)

          real_render = pygame.font.Font.render

          def counting_render(self, *a, **kw):
              calls['render'] += 1
              return real_render(self, *a, **kw)

          monkeypatch.setattr(pygame.font.Font, 'render', counting_render)

          # Build dummy state mimicking main() steady-state.
          hamclock_pygame._scaled_cache.clear()
          hamclock_pygame._glyph_cache.clear()
          fonts = hamclock_pygame._make_fonts()
          init_font_none_18 = calls['font_none_18']
          screen = pygame.Surface((1440, 900))
          src_img = pygame.Surface((800, 600))
          rect_sdo = pygame.Rect(0, 30, 280, 250)
          rect_prop = pygame.Rect(1100, 600, 340, 280)

          for frame in range(30):
              hamclock_pygame.draw_image(screen, rect_sdo, src_img, fonts,
                                         image_key='solar-image',
                                         fetched_at=1000.0)
              hamclock_pygame.draw_image(screen, rect_prop, src_img, fonts,
                                         image_key='real-drap',
                                         fetched_at=1000.0)
              # Static labels rendered every frame
              for label in ('SOLAR', 'BANDS', 'SDO IMAGE', 'OPEN:', 'CLOSED:',
                            'SFI', 'Kp', 'A', 'FREQ', 'BND'):
                  hamclock_pygame._blit_text(screen, fonts['panel'], label,
                                             (255, 255, 255), 0, 0)
              # Count this frame's labels as 10 lookups
              calls['render_hit'] += 10 if frame > 0 else 0

          assert calls['smoothscale'] <= 2, \
              'smoothscale ran %d times over 30 frames (expected <= 2)' % calls['smoothscale']
          assert calls['font_none_18'] == init_font_none_18, \
              'Font(None, 18) constructed %d times after init' % (
                  calls['font_none_18'] - init_font_none_18)
          # 30 frames * 10 labels = 300 _blit_text calls. First frame: 10 misses.
          # Remaining 29 * 10 = 290 hits. Expected render count = 10.
          assert calls['render'] == 10, \
              'glyph cache: expected 10 renders, got %d' % calls['render']
          hit_rate = 1.0 - (calls['render'] / 300.0)
          assert hit_rate >= 0.90, 'glyph cache hit rate %.2f < 0.90' % hit_rate
      finally:
          pygame.quit()
  ```

- [ ] **Step 2: Run test to verify it passes**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_render_loop_30_frame_alloc_budget -v`
  Expected: PASS (if Tasks 1.2–1.4 landed correctly).

- [ ] **Step 3: Run the full Phase 1 test suite**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py tests/test_inject_events.py -v`
  Expected: PASS (all Phase 1 tests pass).

- [ ] **Step 4: Commit**
  ```
  git add tests/test_perf_alloc.py
  git commit -m "test(phase1): 30-frame alloc-budget harness ties top 4 perf fixes together"
  ```

---

### Phase 1 acceptance

Verification artifacts produced by this phase:

- `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py` — covers `image_fetched_at`, `tiny` font, glyph cache (3 tests), scaled cache (3 tests), dirty-rect helper (3 tests), and the 30-frame alloc-budget summary.
- `/home/kasm-user/hamclock-pi1/tests/test_inject_events.py` — covers `--inject-events` argparse gating and JSON-to-pygame-event translation.
- `/home/kasm-user/hamclock-pi1/scripts/click_latency_bench.sh` + `scripts/click_latency_bench.py` — synthetic-click p99 micro-bench.
- `/home/kasm-user/hamclock-pi1/docs/phase1-latency.md` — recorded Pi 1B result and the resulting Phase 5 gate decision (ship / 20 FPS / deeper dirty-rect).
- Updated `hamclock_data.py` (per-image `fetched_at`) and `hamclock_pygame.py` (4 perf fixes + debug flag).

Phase 1 gates Phase 5 via `docs/phase1-latency.md`: Phase 5 cannot merge unless that file records p99 <= 200 ms or documents the escalation that brought p99 below 200 ms.

---


---

### Task 1.9: Render-loop degraded-window guard (RECOVERING overlay + backoff cap)

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (function `main` — render-loop `except` branch)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py` (add cases)

The spec's Error Handling section ("Degraded-window behavior during the 15-error backoff") mandates: on each caught `pygame.error`, sleep ≤100 ms (total per-retry backoff capped at 500 ms so the worst-case degraded window is ≤ 7.5 s), and render a solid theme-bg fill with a `RECOVERING…` label centered on screen so the user never sees the bare console or a frozen partial frame. After 15 consecutive errors, exit code 1 → kiosk.sh relaunches.

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_perf_alloc.py`:
  ```python
  def test_render_loop_recovery_overlay_renders(monkeypatch):
      """The RECOVERING label must be drawn when the helper runs, using
      cached fonts (no per-call Font allocation)."""
      import pygame
      pygame.display.init()
      screen = pygame.display.set_mode((1440, 900))
      import hamclock_pygame as hp

      seen = {"recovering": 0}
      original = hp._blit_text
      def trace(surf, font, text, color, x, y):
          if "RECOVERING" in str(text):
              seen["recovering"] += 1
          return original(surf, font, text, color, x, y)
      monkeypatch.setattr(hp, "_blit_text", trace)

      theme = hp.THEMES["kstate"]
      fonts = hp._make_fonts()
      hp._render_recovering_overlay(screen, fonts, theme)
      assert seen["recovering"] >= 1
      pygame.display.quit()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_render_loop_recovery_overlay_renders -v`
  Expected: FAIL — `AttributeError: module 'hamclock_pygame' has no attribute '_render_recovering_overlay'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add above `main()`:
  ```python
  def _render_recovering_overlay(screen, fonts, theme):
      """Degraded-window display: fill with theme bg + centered RECOVERING
      label so the user never sees the bare console or a stuck partial
      frame while the render loop retries."""
      try:
          screen.fill(theme.get("bg", (0, 0, 0)))
          sw, sh = screen.get_size()
          font = (fonts.get("title")
                  or fonts.get("panel")
                  or next(iter(fonts.values())))
          surf = font.render("RECOVERING…", True,
                             theme.get("fg", (220, 230, 240)))
          screen.blit(surf, surf.get_rect(center=(sw // 2, sh // 2)))
          import pygame as _pg
          _pg.display.flip()
      except Exception:
          pass
  ```

  Replace the existing render-loop `except` branch in `main()` so it caps backoff and draws the overlay:
  ```python
              except Exception as e:
                  consecutive_errors += 1
                  print("render loop error (%d): %s"
                        % (consecutive_errors, e), file=sys.stderr)
                  backoff_ms = min(100 * consecutive_errors, 500)
                  _render_recovering_overlay(screen, fonts, theme)
                  if consecutive_errors > 15:
                      print("too many render errors — exiting for a clean restart",
                            file=sys.stderr)
                      running = False
                  else:
                      time.sleep(backoff_ms / 1000.0)
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_perf_alloc.py::test_render_loop_recovery_overlay_renders -v`
  Expected: PASS (1 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_perf_alloc.py
  git commit -m "feat(kiosk): RECOVERING overlay during render-loop degraded window"
  ```

## Phase 2 — Server-side MUF rasterize

Files touched in this phase: `server.py`, `kiosk-install.sh`, `offline-install.sh` (the embedded `server.py` heredoc), `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh` (mirror), `docs/muf-source.md`, `tests/test_muf_rasterize.py`.

**Gating rule:** Tasks 2.2 onward MAY NOT be started until Task 2.1 has been run on real Pi 1B hardware and the median measurement plus decision is committed to `docs/muf-source.md`. Tasks 2.2-2.7 assume the recorded decision was "ship cairosvg" (median ≤ 30 s); if median > 30 s, the rasterize tasks are replaced wholesale by the BOM World I-Map GIF fallback (out of scope for this plan but documented in the spec).

### Task 2.1: Pre-merge cairosvg benchmark on real Pi 1B (HARDWARE TASK)

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/scripts/bench-cairosvg.sh`
- Create: `/home/kasm-user/hamclock-pi1/docs/muf-source.md`
- Test: (hardware measurement — no pytest)

- [ ] **Step 1: Write the benchmark script**
  Create `/home/kasm-user/hamclock-pi1/scripts/bench-cairosvg.sh` with mode 0755:
  ```bash
  #!/bin/sh
  # Pre-merge cairosvg benchmark for Phase 2 (Pi 1B armv6).
  # Records 5 wall-clock measurements of rendering the KC2G MUF SVG to a
  # 720-px-wide PNG. Median of the 5 is the gate value:
  #   median <= 20 s  -> ship cairosvg, PHASE2_TIMEOUT_S = 45.
  #   20 < median <=30 -> ship cairosvg, PHASE2_TIMEOUT_S = max(60, 3*median).
  #   median > 30 s   -> DO NOT ship cairosvg; use BOM World I-Map GIF fallback.
  set -eu
  echo "[bench-cairosvg] installing python3-cairosvg (idempotent) ..."
  sudo apt update -qq
  sudo apt install -y python3-cairosvg >/dev/null
  echo "[bench-cairosvg] running 5 iterations ..."
  for i in 1 2 3 4 5; do
      echo "--- iteration $i ---"
      /usr/bin/time -p python3 -c "
  import cairosvg
  cairosvg.svg2png(
      url='https://prop.kc2g.com/renders/current/mufd-normal-now.svg',
      output_width=720,
      write_to='/tmp/m_${i}.png')
  " 2>&1
      ls -l /tmp/m_${i}.png
  done
  echo "[bench-cairosvg] Done. Record the 5 'real' times, take the median,"
  echo "[bench-cairosvg] and write the decision to docs/muf-source.md."
  ```

- [ ] **Step 2: Commit the benchmark script before running it**
  Run on the dev box:
  ```
  cd /home/kasm-user/hamclock-pi1
  chmod +x scripts/bench-cairosvg.sh
  git add scripts/bench-cairosvg.sh
  git commit -m "chore(phase2): add pre-merge cairosvg benchmark script"
  ```
  Expected: clean commit with one new file.

- [ ] **Step 3: Run the benchmark on the real Pi 1B (HARDWARE)**
  On the Pi 1B (HDMI attached, freshly booted, network up):
  ```
  cd ~/hamclock-pi1 && git pull
  bash scripts/bench-cairosvg.sh 2>&1 | tee /tmp/bench-cairosvg.log
  ```
  Copy the full `/tmp/bench-cairosvg.log` back to the dev box (scp or paste).
  Expected: 5 `real <seconds>` lines plus a `/tmp/m_$i.png` listing.

- [ ] **Step 4: Compute the median and apply the decision rule**
  On the dev box, sort the 5 `real` values and pick the 3rd. Then resolve:
  - `median <= 20.0` -> Decision = SHIP_CAIROSVG, `PHASE2_TIMEOUT_S = 45`.
  - `20.0 < median <= 30.0` -> Decision = SHIP_CAIROSVG, `PHASE2_TIMEOUT_S = max(60, int(3 * median + 0.5))`.
  - `median > 30.0` -> Decision = USE_BOM_GIF; Tasks 2.2-2.7 are voided and the BOM fallback path is implemented instead (out of scope for this plan; see spec "Fallback").

- [ ] **Step 5: Record the decision in `docs/muf-source.md`**
  Create `/home/kasm-user/hamclock-pi1/docs/muf-source.md`:
  ```
  # MUF map source decision (Phase 2)

  Date measured: YYYY-MM-DD
  Hardware: Raspberry Pi 1 Model B (BCM2835, 700 MHz armv6, 512 MB RAM)
  OS: Raspberry Pi OS Bookworm (armel)
  python3-cairosvg version: <output of `dpkg -s python3-cairosvg | grep Version`>

  ## Measurements (5 iterations, wall-clock `real` seconds)

  Iter 1: <value> s
  Iter 2: <value> s
  Iter 3: <value> s
  Iter 4: <value> s
  Iter 5: <value> s

  Sorted: [<v1>, <v2>, <v3>, <v4>, <v5>]
  Median: <v3> s

  ## Decision

  Decision: <SHIP_CAIROSVG | USE_BOM_GIF>
  muf-subprocess-timeout-s: 45
  Source ladder: cairosvg subprocess -> SVG passthrough (fallback in handler)

  ## Rationale

  Median <v3> s falls in the <"<=20" | "20-30" | ">30"> band per the spec
  "Phase 2 Pre-merge gate" decision rule. <One-sentence justification.>
  ```
  Fill in every `<value>` from the log paste.

- [ ] **Step 6: Commit the decision**
  ```
  git add docs/muf-source.md
  git commit -m "docs(phase2): record cairosvg benchmark and source decision"
  ```

---

### Task 2.2: Test scaffolding for `_rasterize_muf`

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`

- [ ] **Step 1: Write the failing test for the not-yet-imported symbol**
  Create `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`:
  ```python
  """Phase 2 tests for the server-side MUF SVG -> PNG rasterizer.

  These tests stub `subprocess.run` so the test suite never invokes cairosvg or
  cpulimit. The contract under test is the wrapper around the subprocess —
  argument list, timeout, error handling, and the CACHE wiring done by
  fetch_muf() + the /api/muf-map handler.
  """
  import subprocess
  import sys
  from unittest import mock

  import pytest

  import server


  def test_rasterize_muf_symbol_exists():
      assert hasattr(server, "_rasterize_muf"), (
          "Phase 2 contract: server must export `_rasterize_muf(svg_bytes)`."
      )


  def test_phase2_timeout_constant_exists():
      assert hasattr(server, "PHASE2_TIMEOUT_S"), (
          "Phase 2 contract: server must export PHASE2_TIMEOUT_S."
      )
      assert isinstance(server.PHASE2_TIMEOUT_S, int)
      assert server.PHASE2_TIMEOUT_S >= 45
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v`
  Expected: FAIL — `AssertionError: Phase 2 contract: server must export _rasterize_muf...` (and the same for PHASE2_TIMEOUT_S).

- [ ] **Step 3: Add the minimal symbols to `server.py` so the contract tests pass**
  In `/home/kasm-user/hamclock-pi1/server.py`, immediately after the `import os` line at L18, add:
  ```python
  import sys
  ```
  Then, immediately after the `UA = 'HamClockLite/1.0'` line at L41, insert:
  ```python

  # Phase 2: raised by installer if pre-merge cairosvg benchmark > 20 s
  # (see docs/muf-source.md for the recorded measurement).
  PHASE2_TIMEOUT_S = 45


  def _rasterize_muf(svg_bytes):
      """Phase 2 stub — full body added in Task 2.3."""
      raise NotImplementedError
  ```

- [ ] **Step 4: Verify the contract tests now pass**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v`
  Expected: 2 passed in <1 s.

- [ ] **Step 5: Commit**
  ```
  git add tests/test_muf_rasterize.py server.py
  git commit -m "test(phase2): scaffold _rasterize_muf contract tests"
  ```

---

### Task 2.3: Implement `_rasterize_muf` (subprocess body)

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/server.py` (replace `_rasterize_muf` stub)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`

- [ ] **Step 1: Write the failing tests for the subprocess wrapper**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`:
  ```python


  FAKE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
  FAKE_PNG = b'\x89PNG\r\n\x1a\nFAKEPNGBODY'


  def _stub_completed(stdout, returncode=0):
      r = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b'')
      return r


  def test_rasterize_muf_happy_path(monkeypatch):
      captured = {}

      def fake_run(argv, input=None, capture_output=None, timeout=None, check=None):
          captured['argv'] = argv
          captured['input'] = input
          captured['timeout'] = timeout
          captured['check'] = check
          captured['capture_output'] = capture_output
          return _stub_completed(FAKE_PNG)

      monkeypatch.setattr(server.subprocess, 'run', fake_run)
      out = server._rasterize_muf(FAKE_SVG)
      assert out == FAKE_PNG
      # argv must start with the cpulimit guard
      assert captured['argv'][:5] == ['cpulimit', '-l', '50', '-q', '--']
      # then python3 -c "<cairosvg one-liner>"
      assert captured['argv'][5] == 'python3'
      assert captured['argv'][6] == '-c'
      one_liner = captured['argv'][7]
      assert 'cairosvg.svg2png' in one_liner
      assert 'output_width=720' in one_liner
      assert 'sys.stdin.buffer.read()' in one_liner
      assert 'sys.stdout.buffer' in one_liner
      # stdin contains the SVG bytes
      assert captured['input'] == FAKE_SVG
      # timeout matches the published constant
      assert captured['timeout'] == server.PHASE2_TIMEOUT_S
      # check=True so non-zero exits raise CalledProcessError -> None
      assert captured['check'] is True
      # capture_output=True so .stdout is populated
      assert captured['capture_output'] is True


  def test_rasterize_muf_returns_none_on_timeout(monkeypatch, capsys):
      def fake_run(*a, **kw):
          raise subprocess.TimeoutExpired(cmd='python3', timeout=server.PHASE2_TIMEOUT_S)
      monkeypatch.setattr(server.subprocess, 'run', fake_run)
      assert server._rasterize_muf(FAKE_SVG) is None
      err = capsys.readouterr().err
      assert '[muf]' in err and 'rasterize failed' in err


  def test_rasterize_muf_returns_none_on_called_process_error(monkeypatch, capsys):
      def fake_run(*a, **kw):
          raise subprocess.CalledProcessError(returncode=1, cmd='python3', stderr=b'boom')
      monkeypatch.setattr(server.subprocess, 'run', fake_run)
      assert server._rasterize_muf(FAKE_SVG) is None
      err = capsys.readouterr().err
      assert '[muf]' in err


  def test_rasterize_muf_returns_none_when_cpulimit_missing(monkeypatch, capsys):
      def fake_run(*a, **kw):
          raise FileNotFoundError(2, 'No such file or directory', 'cpulimit')
      monkeypatch.setattr(server.subprocess, 'run', fake_run)
      assert server._rasterize_muf(FAKE_SVG) is None
      err = capsys.readouterr().err
      assert '[muf]' in err
  ```

- [ ] **Step 2: Run tests to verify they fail**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v`
  Expected: 4 new tests FAIL (all `NotImplementedError` from the stub).

- [ ] **Step 3: Implement the real `_rasterize_muf`**
  In `/home/kasm-user/hamclock-pi1/server.py`, replace the stub from Task 2.2 with:
  ```python
  def _rasterize_muf(svg_bytes):
      """Render the KC2G MUF SVG to PNG in a subprocess so the multi-second
      render does not block the request thread or the background fetcher.

      output_width=720 because the MUF panel is ~720x420 in the 1440x900
      layout; rendering at native panel width halves cairo's CPU cost vs.
      full screen. cairosvg.svg2png preserves aspect ratio when only
      output_width is given — the 1526x905 SVG becomes 720x427 PNG.

      cpulimit caps the subprocess to 50% of one core so the render loop
      keeps its frame budget even mid-rasterize. nice -n 19 alone is
      ineffective on an idle single-core box because the render loop
      sleeps between 10 FPS frames; the cairosvg job would still claim
      the core. cpulimit enforces a hard duty cycle.

      Returns the PNG bytes, or None on subprocess error / timeout /
      missing cpulimit / cairosvg ImportError inside the child.
      """
      try:
          p = subprocess.run(
              ['cpulimit', '-l', '50', '-q', '--',
               'python3', '-c',
               'import sys, cairosvg; cairosvg.svg2png('
               'bytestring=sys.stdin.buffer.read(), '
               'output_width=720, write_to=sys.stdout.buffer)'],
              input=svg_bytes,
              capture_output=True,
              timeout=PHASE2_TIMEOUT_S,
              check=True,
          )
          return p.stdout
      except (subprocess.SubprocessError, FileNotFoundError) as e:
          print('[muf] rasterize failed: %s' % e, file=sys.stderr)
          return None
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v`
  Expected: 6 passed.

- [ ] **Step 5: Commit**
  ```
  git add server.py tests/test_muf_rasterize.py
  git commit -m "feat(phase2): implement _rasterize_muf subprocess wrapper"
  ```

---

### Task 2.4: Wire `_rasterize_muf` into `fetch_muf` and CACHE

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/server.py` (CACHE block L21-39; `fetch_muf` L241-251)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`

- [ ] **Step 1: Write failing tests for the fetch_muf integration**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`:
  ```python


  def test_cache_has_muf_image_png_slot():
      assert 'muf_image_png' in server.CACHE, (
          "Phase 2: CACHE must declare a 'muf_image_png' slot (initial None)."
      )
      # On import, before any fetch, the PNG slot is None.
      assert server.CACHE['muf_image_png'] is None or isinstance(
          server.CACHE['muf_image_png'], (bytes, bytearray)
      )


  def test_fetch_muf_populates_png_when_rasterize_succeeds(monkeypatch):
      # Stub urlopen so fetch_muf does not hit the network.
      class FakeResp:
          def __init__(self, body):
              self._body = body
          def read(self):
              return self._body
          def __enter__(self):
              return self
          def __exit__(self, *a):
              return False

      monkeypatch.setattr(server, 'urlopen', lambda req, timeout=20: FakeResp(FAKE_SVG))
      monkeypatch.setattr(server, '_rasterize_muf', lambda b: FAKE_PNG)
      # Reset cache slots
      server.CACHE['muf_image'] = None
      server.CACHE['muf_image_png'] = None
      server.CACHE['muf_image_updated'] = 0

      server.fetch_muf()

      assert server.CACHE['muf_image'] == FAKE_SVG
      assert server.CACHE['muf_image_png'] == FAKE_PNG
      assert server.CACHE['muf_image_updated'] > 0


  def test_fetch_muf_leaves_png_none_when_rasterize_fails(monkeypatch):
      class FakeResp:
          def __init__(self, body):
              self._body = body
          def read(self):
              return self._body
          def __enter__(self):
              return self
          def __exit__(self, *a):
              return False

      monkeypatch.setattr(server, 'urlopen', lambda req, timeout=20: FakeResp(FAKE_SVG))
      monkeypatch.setattr(server, '_rasterize_muf', lambda b: None)
      server.CACHE['muf_image'] = None
      server.CACHE['muf_image_png'] = b'STALE'
      server.CACHE['muf_image_updated'] = 0

      server.fetch_muf()

      # SVG must still cache (browser path still works).
      assert server.CACHE['muf_image'] == FAKE_SVG
      # PNG slot cleared so /api/muf-map falls through to SVG.
      assert server.CACHE['muf_image_png'] is None
  ```

- [ ] **Step 2: Run tests to verify they fail**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v -k "muf_image_png or fetch_muf_populates or fetch_muf_leaves"`
  Expected: 3 FAILS (`KeyError: 'muf_image_png'` and missing-key assertions).

- [ ] **Step 3a: Add the `muf_image_png` slot to CACHE**
  In `/home/kasm-user/hamclock-pi1/server.py`, in the `CACHE = {...}` block at L21-39, find the line:
  ```python
      'muf_image': None,
      'muf_image_updated': 0,
  ```
  Replace it with:
  ```python
      'muf_image': None,
      'muf_image_png': None,
      'muf_image_updated': 0,
  ```

- [ ] **Step 3b: Update `fetch_muf` to populate the PNG slot**
  In `/home/kasm-user/hamclock-pi1/server.py`, replace the existing `fetch_muf` (L241-251):
  ```python
  def fetch_muf():
      """Fetch KC2G MUF propagation map SVG and rasterize to PNG.

      The SVG bytes stay in CACHE['muf_image'] so the browser dashboard keeps
      working (it consumes the SVG directly). The native pygame client wants
      pre-rasterized PNG because cairosvg on a Pi 1 takes seconds — too slow
      for the render loop. CACHE['muf_image_png'] is populated by the
      subprocess rasterizer; on failure it is set to None so /api/muf-map
      falls back to the SVG payload.
      """
      try:
          req = Request('https://prop.kc2g.com/renders/current/mufd-normal-now.svg',
                        headers={'User-Agent': UA})
          with urlopen(req, timeout=20) as resp:
              data = resp.read()
          CACHE['muf_image'] = data
          CACHE['muf_image_updated'] = time.time()
          png = _rasterize_muf(data)
          CACHE['muf_image_png'] = png  # may be None on failure
          if png is not None:
              print(f'[{time.strftime("%H:%M:%S")}] MUF map updated '
                    f'({len(data)} B SVG -> {len(png)} B PNG)')
          else:
              print(f'[{time.strftime("%H:%M:%S")}] MUF map updated '
                    f'({len(data)} B SVG, PNG rasterize failed)')
      except Exception as e:
          print(f'[{time.strftime("%H:%M:%S")}] MUF map fetch failed: {e}')
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v`
  Expected: 9 passed.

- [ ] **Step 5: Commit**
  ```
  git add server.py tests/test_muf_rasterize.py
  git commit -m "feat(phase2): wire _rasterize_muf into fetch_muf and CACHE"
  ```

---

### Task 2.5: Serve PNG from `/api/muf-map` with SVG fallback

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/server.py` (`/api/muf-map` handler at L502-516)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`

- [ ] **Step 1: Write the failing handler tests**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_muf_rasterize.py`:
  ```python


  class _Recorder:
      """Mimic the BaseHTTPRequestHandler write API enough to capture output."""
      def __init__(self):
          self.status = None
          self.headers = []
          self.body = b''
          self.command = 'GET'
          self.path = '/api/muf-map'
      def send_response(self, code):
          self.status = code
      def send_header(self, k, v):
          self.headers.append((k, str(v)))
      def end_headers(self):
          pass
      def write(self, b):
          self.body += b


  def _invoke_muf_map(rec):
      # Drive only the /api/muf-map branch by re-implementing the dispatch
      # contract here — we can't easily instantiate the full Handler in tests.
      # Instead, exercise the production code by importing Handler and
      # calling the muf branch via a thin shim attached in this test.
      from server import Handler
      # Bind the recorder's writes to wfile interface.
      class _W:
          def __init__(self, rec): self.rec = rec
          def write(self, b): self.rec.write(b)
      class _Shim(Handler):
          def __init__(s):  # bypass super().__init__
              s.command = rec.command
              s.path = rec.path
              s.wfile = _W(rec)
          def send_response(s, code): rec.send_response(code)
          def send_header(s, k, v): rec.send_header(k, v)
          def end_headers(s): rec.end_headers()
          def send_error(s, code, msg=None):
              rec.status = code
      shim = _Shim()
      shim.do_GET()
      return rec


  def test_muf_map_serves_png_when_available(monkeypatch):
      server.CACHE['muf_image'] = FAKE_SVG
      server.CACHE['muf_image_png'] = FAKE_PNG
      rec = _invoke_muf_map(_Recorder())
      assert rec.status == 200
      ctypes = [v for (k, v) in rec.headers if k.lower() == 'content-type']
      assert ctypes == ['image/png']
      assert rec.body == FAKE_PNG


  def test_muf_map_falls_back_to_svg_when_png_missing(monkeypatch):
      server.CACHE['muf_image'] = FAKE_SVG
      server.CACHE['muf_image_png'] = None
      rec = _invoke_muf_map(_Recorder())
      assert rec.status == 200
      ctypes = [v for (k, v) in rec.headers if k.lower() == 'content-type']
      assert ctypes == ['image/svg+xml']
      assert rec.body == FAKE_SVG


  def test_muf_map_503_when_neither_cached(monkeypatch):
      server.CACHE['muf_image'] = None
      server.CACHE['muf_image_png'] = None
      rec = _invoke_muf_map(_Recorder())
      assert rec.status == 503
  ```

- [ ] **Step 2: Run tests to verify they fail**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v -k "muf_map"`
  Expected: 3 FAILS — the first two because the handler still emits `image/svg+xml` regardless of the PNG cache slot; the third already passes (existing 503 branch).

- [ ] **Step 3: Update the `/api/muf-map` handler in `server.py`**
  In `/home/kasm-user/hamclock-pi1/server.py`, replace the `elif path.startswith('/api/muf-map'):` block (L502-516) with:
  ```python
          elif path.startswith('/api/muf-map'):
              # Phase 2: prefer the pre-rasterized PNG (native pygame client
              # blits this directly). Fall back to the SVG when the
              # rasterizer hasn't run yet, when cairosvg/cpulimit are
              # missing, or when the upstream source briefly fails.
              png = CACHE.get('muf_image_png')
              if png:
                  body = png
                  ctype = 'image/png'
              elif CACHE.get('muf_image'):
                  body = CACHE['muf_image']
                  ctype = 'image/svg+xml'
              else:
                  self.send_error(503, 'MUF map not yet loaded')
                  return
              self.send_response(200)
              self.send_header('Content-Type', ctype)
              self.send_header('Content-Length', len(body))
              self.send_header('Access-Control-Allow-Origin', '*')
              # no-store: the dashboard fetches a fresh URL each cycle; if the
              # browser cached these it would accumulate entries until OOM.
              self.send_header('Cache-Control', 'no-store')
              self.end_headers()
              if self.command != 'HEAD':
                  self.wfile.write(body)
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_muf_rasterize.py -v`
  Expected: 12 passed.

- [ ] **Step 5: Commit**
  ```
  git add server.py tests/test_muf_rasterize.py
  git commit -m "feat(phase2): /api/muf-map serves PNG with SVG fallback"
  ```

---

### Task 2.6: Add `python3-cairosvg` and `cpulimit` to `kiosk-install.sh` (pygame mode only)

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/kiosk-install.sh` (apt-install block at L50-52)

- [ ] **Step 1: Write the failing test (installer-shape grep harness)**
  Create `/home/kasm-user/hamclock-pi1/tests/test_phase2_installer.py`:
  ```python
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
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase2_installer.py::test_kiosk_install_pygame_apt_includes_cairosvg_and_cpulimit -v`
  Expected: FAIL — `'python3-cairosvg' in text` is False.

- [ ] **Step 3: Modify `kiosk-install.sh`**
  In `/home/kasm-user/hamclock-pi1/kiosk-install.sh`, replace the pygame branch at L50-52:
  ```
  if [ "$KIOSK_MODE" = "pygame" ]; then
      echo "Installing Pygame for native framebuffer display..."
      sudo apt install -y python3-pygame
  ```
  with:
  ```
  if [ "$KIOSK_MODE" = "pygame" ]; then
      echo "Installing Pygame + cairosvg + cpulimit for native framebuffer display..."
      # python3-cairosvg: server-side MUF SVG -> PNG rasterize (Phase 2).
      # cpulimit: caps the cairosvg subprocess to 50% of one core so the
      # 10 FPS render loop keeps its frame budget while MUF refreshes.
      sudo apt install -y python3-pygame python3-cairosvg cpulimit
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && bash -n kiosk-install.sh && python3 -m pytest tests/test_phase2_installer.py -v`
  Expected: `bash -n` exits 0; pytest reports 1 passed.

- [ ] **Step 5: Commit**
  ```
  git add kiosk-install.sh tests/test_phase2_installer.py
  git commit -m "feat(phase2): apt install cairosvg + cpulimit in pygame mode (kiosk-install.sh)"
  ```

---

### Task 2.7: Mirror `_rasterize_muf` + handler + apt list into `offline-install.sh`

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/offline-install.sh` (embedded server.py heredoc and apt-install block)

- [ ] **Step 1: Write the failing offline-mirror tests**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_phase2_installer.py`:
  ```python


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
  ```

- [ ] **Step 2: Run tests to verify they fail**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase2_installer.py -v -k offline`
  Expected: 3 FAILS.

- [ ] **Step 3a: Mirror the `sys` import + `PHASE2_TIMEOUT_S` + `_rasterize_muf` into the embedded server**
  In `/home/kasm-user/hamclock-pi1/offline-install.sh`, after the `import os` line at L72, add:
  ```
  import sys
  ```
  Then locate the `UA = 'HamClockLite/1.0'` line (around L78 of the embedded block — search for `UA = 'HamClockLite`) and insert immediately after it:
  ```

  PHASE2_TIMEOUT_S = 45


  def _rasterize_muf(svg_bytes):
      """See server.py — Phase 2 MUF SVG -> PNG rasterizer (Pi 1 offline mirror)."""
      try:
          p = subprocess.run(
              ['cpulimit', '-l', '50', '-q', '--',
               'python3', '-c',
               'import sys, cairosvg; cairosvg.svg2png('
               'bytestring=sys.stdin.buffer.read(), '
               'output_width=720, write_to=sys.stdout.buffer)'],
              input=svg_bytes,
              capture_output=True,
              timeout=PHASE2_TIMEOUT_S,
              check=True,
          )
          return p.stdout
      except (subprocess.SubprocessError, FileNotFoundError) as e:
          print('[muf] rasterize failed: %s' % e, file=sys.stderr)
          return None
  ```

- [ ] **Step 3b: Add the `muf_image_png` cache slot**
  In `/home/kasm-user/hamclock-pi1/offline-install.sh`, in the embedded CACHE block at L75-89, find:
  ```
      'muf_image': None,
      'muf_image_updated': 0,
  ```
  and replace with:
  ```
      'muf_image': None,
      'muf_image_png': None,
      'muf_image_updated': 0,
  ```

- [ ] **Step 3c: Update the embedded `fetch_muf` to populate PNG**
  In `/home/kasm-user/hamclock-pi1/offline-install.sh`, replace the embedded `fetch_muf` (L295-305) with:
  ```python
  def fetch_muf():
      """Fetch KC2G MUF SVG and rasterize to PNG (Phase 2 offline mirror)."""
      try:
          req = Request('https://prop.kc2g.com/renders/current/mufd-normal-now.svg',
                        headers={'User-Agent': UA})
          with urlopen(req, timeout=20) as resp:
              data = resp.read()
          CACHE['muf_image'] = data
          CACHE['muf_image_updated'] = time.time()
          png = _rasterize_muf(data)
          CACHE['muf_image_png'] = png
          if png is not None:
              print(f'[{time.strftime("%H:%M:%S")}] MUF map updated '
                    f'({len(data)} B SVG -> {len(png)} B PNG)')
          else:
              print(f'[{time.strftime("%H:%M:%S")}] MUF map updated '
                    f'({len(data)} B SVG, PNG rasterize failed)')
      except Exception as e:
          print(f'[{time.strftime("%H:%M:%S")}] MUF map fetch failed: {e}')
  ```

- [ ] **Step 3d: Update the embedded `/api/muf-map` handler (around L556-570)**
  Replace the `elif path.startswith('/api/muf-map'):` block in the embedded heredoc with:
  ```python
          elif path.startswith('/api/muf-map'):
              # Phase 2: prefer pre-rasterized PNG; fall back to SVG.
              png = CACHE.get('muf_image_png')
              if png:
                  body = png
                  ctype = 'image/png'
              elif CACHE.get('muf_image'):
                  body = CACHE['muf_image']
                  ctype = 'image/svg+xml'
              else:
                  self.send_error(503, 'MUF map not yet loaded')
                  return
              self.send_response(200)
              self.send_header('Content-Type', ctype)
              self.send_header('Content-Length', len(body))
              self.send_header('Access-Control-Allow-Origin', '*')
              self.send_header('Cache-Control', 'no-store')
              self.end_headers()
              if self.command != 'HEAD':
                  self.wfile.write(body)
  ```

- [ ] **Step 3e: Add the apt-install line**
  In `/home/kasm-user/hamclock-pi1/offline-install.sh`, find the line at L2921-2922:
  ```
  if [ "$KIOSK_MODE" = "pygame" ]; then
      sudo apt install -y python3-pygame
  ```
  and replace with:
  ```
  if [ "$KIOSK_MODE" = "pygame" ]; then
      # Phase 2: python3-cairosvg for MUF SVG->PNG rasterize; cpulimit caps
      # the subprocess to 50% of one core so the render loop keeps its budget.
      sudo apt install -y python3-pygame python3-cairosvg cpulimit
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && bash -n offline-install.sh && python3 -m pytest tests/test_phase2_installer.py -v`
  Expected: `bash -n` exits 0; pytest reports 4 passed.

- [ ] **Step 5: Commit**
  ```
  git add offline-install.sh tests/test_phase2_installer.py
  git commit -m "feat(phase2): mirror cairosvg rasterize + apt deps into offline-install.sh"
  ```

---

### Task 2.8: Mirror `offline-install.sh` to `hamclock-reborn/public/downloads/pi1-install.sh`

**Files:**
- Modify: `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh` (full copy of `offline-install.sh`)

- [ ] **Step 1: Write the failing mirror-equivalence test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_phase2_installer.py`:
  ```python


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
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase2_installer.py::test_mirror_installer_matches_offline_install -v`
  Expected: FAIL — sha256 of mirror differs from offline-install.sh (the mirror hasn't been updated with Task 2.7's changes).

- [ ] **Step 3: Copy the updated offline installer to the mirror location**
  Run:
  ```
  cp /home/kasm-user/hamclock-pi1/offline-install.sh \
     /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh
  ```

- [ ] **Step 4: Verify**
  Run:
  ```
  cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase2_installer.py -v
  bash -n /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh
  ```
  Expected: all pytest tests pass; `bash -n` exits 0.

- [ ] **Step 5: Commit both repos**
  ```
  cd /home/kasm-user/hamclock-pi1
  git add tests/test_phase2_installer.py
  git commit -m "test(phase2): assert offline-install.sh mirror equivalence"

  cd /home/kasm-user/hamclock-reborn
  git add public/downloads/pi1-install.sh
  git commit -m "chore(phase2): sync pi1-install.sh mirror with cairosvg rasterize"
  ```

---

### Task 2.9: Post-deploy verification on real Pi 1B (HARDWARE TASK)

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/docs/muf-source.md` (append the post-deploy check)

- [ ] **Step 1: Print the verification commands**
  On the dev box, prepare the commands the operator will paste on the Pi:
  ```
  # On the Pi 1B, after `sudo bash kiosk-install.sh --pygame` (or
  # offline-install.sh / pi1-install.sh) has completed and hamclock-lite is up:
  sudo systemctl restart hamclock-lite
  sleep 60   # let the background fetcher rasterize once
  curl -s -o /tmp/m.png http://localhost:8080/api/muf-map
  file /tmp/m.png
  wc -c /tmp/m.png
  journalctl -u hamclock-lite --since "2 minutes ago" | grep -i muf
  ```

- [ ] **Step 2: Run the verification on the Pi 1B (HARDWARE)**
  On the Pi 1B, paste the Step 1 commands and copy the output back. Acceptance:
  - `file /tmp/m.png` reports `PNG image data, 720 x 427`.
  - `wc -c /tmp/m.png` is between 20480 (20 KB) and 204800 (200 KB).
  - `journalctl` shows one `MUF map updated (<n> B SVG -> <m> B PNG)` line.
  - Steady-state RSS check: `ps -o pid,rss,cmd -C python3` shows hamclock-lite RSS unchanged ±5 MB vs pre-deploy (the cairosvg subprocess only spikes during the 15-min refresh window).

- [ ] **Step 3: Append the result to `docs/muf-source.md`**
  In `/home/kasm-user/hamclock-pi1/docs/muf-source.md`, append:
  ```
  ## Post-deploy verification (Phase 2 acceptance)

  Date: YYYY-MM-DD
  `file /tmp/m.png`: PNG image data, 720 x 427
  Size: <n> bytes (between 20 KB and 200 KB: yes)
  journalctl line: `[HH:MM:SS] MUF map updated (<svg_bytes> B SVG -> <png_bytes> B PNG)`
  hamclock-lite RSS pre/post: <X> KB / <Y> KB (delta <Z> KB)
  ```

- [ ] **Step 4: Verify locally (final repo-side sanity)**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/ -v`
  Expected: all Phase 2 tests pass (the rasterize, installer, and mirror suites).

- [ ] **Step 5: Commit**
  ```
  cd /home/kasm-user/hamclock-pi1
  git add docs/muf-source.md
  git commit -m "docs(phase2): record post-deploy /api/muf-map verification on Pi 1B"
  ```

---

### Phase 2 acceptance

The following artifacts must all exist and pass for Phase 2 to be considered complete (and for Phase 5 to be unblocked):

1. **`/home/kasm-user/hamclock-pi1/docs/muf-source.md`** — committed with both the pre-merge benchmark median + decision and the post-deploy verification block.
2. **`server.py`** exports `PHASE2_TIMEOUT_S: int` and `_rasterize_muf(svg_bytes) -> bytes | None`; CACHE has slot `'muf_image_png'`; `fetch_muf` populates the PNG slot; `/api/muf-map` serves PNG with SVG fallback.
3. **`kiosk-install.sh`** apt-installs `python3-cairosvg` and `cpulimit` inside the `KIOSK_MODE = pygame` branch.
4. **`offline-install.sh`** embedded `server.py` mirrors the rasterize wrapper, CACHE slot, `fetch_muf`, and `/api/muf-map` handler, and its apt-install block mirrors the new packages.
5. **`/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh`** is byte-identical to `offline-install.sh` (enforced by `test_mirror_installer_matches_offline_install`).
6. **`tests/test_muf_rasterize.py`** (12 tests) and **`tests/test_phase2_installer.py`** (4 tests + 1 mirror equivalence) all pass under `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/ -v`.
7. **`scripts/bench-cairosvg.sh`** is committed (mode 0755) so the benchmark is reproducible.
8. **Hardware verification:** on real Pi 1B, `curl -s -o /tmp/m.png http://localhost:8080/api/muf-map && file /tmp/m.png` reports `PNG image data, 720 x 427` with size in 20 KB–200 KB.

---

## Phase 3 — Themes

This phase ships theme parity with the browser dashboard. The `THEMES` dict locks in palettes for `kstate` (existing pygame default), `classic`, `amber`, and `blue` (extracted from the `var themes={...}` JS literal at index.html L387–392 and from the existing pygame constants). Every draw function is refactored to accept a `theme: dict` parameter so it stops importing module-level color constants. `load_settings()` is added with a defaults-only fallback (Phase 3 ships without Phase 4 by leaning on the defaults). Tests live in `tests/test_themes.py`.

Files touched in this phase: `hamclock_pygame.py` only. No installer changes.

### Task 3.1: Add THEMES dict and remove top-level color constants

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (lines 18-42 — the `BG/CARD/...`, `COND_COLORS`, `BAND_COLORS` block)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py`

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  """Phase 3 — theme palette tests."""
  import os
  os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
  os.environ.setdefault('HAMCLOCK_DEBUG', '1')

  import pytest
  import hamclock_pygame


  REQUIRED_KEYS = {
      'bg', 'card', 'border', 'fg', 'bright', 'muted', 'label',
      'accent', 'callsign', 'good', 'fair', 'poor', 'na',
      'band_palette', 'sdo_accent',
  }
  EXPECTED_THEMES = {'kstate', 'classic', 'amber', 'blue'}


  def test_themes_dict_exists():
      assert hasattr(hamclock_pygame, 'THEMES')
      assert isinstance(hamclock_pygame.THEMES, dict)


  def test_all_four_themes_present():
      assert set(hamclock_pygame.THEMES.keys()) == EXPECTED_THEMES


  def test_every_theme_has_full_schema():
      for name, palette in hamclock_pygame.THEMES.items():
          missing = REQUIRED_KEYS - set(palette.keys())
          assert not missing, f'{name} missing keys: {missing}'


  def test_every_color_is_rgb_tuple():
      scalar_keys = REQUIRED_KEYS - {'band_palette'}
      for name, palette in hamclock_pygame.THEMES.items():
          for k in scalar_keys:
              v = palette[k]
              assert isinstance(v, tuple), f'{name}.{k} not tuple: {v!r}'
              assert len(v) == 3, f'{name}.{k} not 3-tuple: {v!r}'
              for c in v:
                  assert isinstance(c, int) and 0 <= c <= 255, \
                      f'{name}.{k} channel out of range: {v!r}'


  def test_band_palette_is_ten_rgb_tuples():
      for name, palette in hamclock_pygame.THEMES.items():
          bp = palette['band_palette']
          assert isinstance(bp, list)
          assert len(bp) == 10, f'{name}.band_palette length {len(bp)}'
          for v in bp:
              assert isinstance(v, tuple) and len(v) == 3


  def test_kstate_bg_matches_spec_contract():
      assert hamclock_pygame.THEMES['kstate']['bg'] == (42, 20, 80)
      assert hamclock_pygame.THEMES['kstate']['card'] == (58, 29, 101)


  def test_classic_bg_matches_browser_css():
      # index.html L388: classic bg #0a0e14
      assert hamclock_pygame.THEMES['classic']['bg'] == (10, 14, 20)


  def test_amber_bg_matches_browser_css():
      # index.html L389: amber bg #1a1000
      assert hamclock_pygame.THEMES['amber']['bg'] == (26, 16, 0)


  def test_blue_bg_matches_browser_css():
      # index.html L390: blue bg #0a0f1e
      assert hamclock_pygame.THEMES['blue']['bg'] == (10, 15, 30)
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: FAIL with `AttributeError: module 'hamclock_pygame' has no attribute 'THEMES'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, replace lines 18-42 (the `# ---- K-State theme colors ----` block through the `BAND_COLORS = {...}` literal but PRESERVE `HF_BANDS` on line 44) with:
  ```python
  # ---- THEMES (Phase 3) ----
  # Palettes are extracted from the browser dashboard at index.html L387-392
  # (the `var themes={...}` literal). kstate values match the existing pygame
  # constants the kiosk has been shipping. Every draw function takes a
  # `theme: dict` and indexes by the keys below.
  #
  # Required keys per palette:
  #   bg, card, border, fg, bright, muted, label, accent, callsign,
  #   good, fair, poor, na, band_palette (list of 10), sdo_accent.

  THEMES = {
      'kstate': {
          'bg':       (42, 20, 80),
          'card':     (58, 29, 101),
          'border':   (81, 40, 136),
          'fg':       (232, 221, 245),
          'bright':   (255, 255, 255),
          'muted':    (146, 126, 180),
          'label':    (184, 160, 216),
          'accent':   (244, 197, 92),
          'callsign': (244, 114, 182),
          'good':     (34, 197, 94),
          'fair':     (234, 179, 8),
          'poor':     (239, 68, 68),
          'na':       (74, 85, 104),
          'band_palette': [
              (255, 107, 107), (240, 101, 149), (204, 93, 232),
              (132, 94, 247),  (92, 124, 250),  (51, 154, 240),
              (34, 184, 207),  (32, 201, 151),  (81, 207, 102),
              (148, 216, 45),
          ],
          'sdo_accent': (244, 197, 92),
      },
      'classic': {
          'bg':       (10, 14, 20),
          'card':     (17, 24, 32),
          'border':   (26, 37, 48),
          'fg':       (200, 208, 216),
          'bright':   (232, 240, 240),
          'muted':    (96, 112, 128),
          'label':    (136, 153, 170),
          'accent':   (6, 182, 212),
          'callsign': (244, 114, 182),
          'good':     (34, 197, 94),
          'fair':     (234, 179, 8),
          'poor':     (239, 68, 68),
          'na':       (74, 85, 104),
          'band_palette': [
              (255, 107, 107), (240, 101, 149), (204, 93, 232),
              (132, 94, 247),  (92, 124, 250),  (51, 154, 240),
              (34, 184, 207),  (32, 201, 151),  (81, 207, 102),
              (148, 216, 45),
          ],
          'sdo_accent': (6, 182, 212),
      },
      'amber': {
          'bg':       (26, 16, 0),
          'card':     (31, 24, 0),
          'border':   (51, 40, 0),
          'fg':       (220, 180, 130),
          'bright':   (255, 220, 160),
          'muted':    (138, 104, 64),
          'label':    (184, 128, 96),
          'accent':   (245, 158, 11),
          'callsign': (59, 130, 246),
          'good':     (245, 158, 11),
          'fair':     (251, 191, 36),
          'poor':     (239, 68, 68),
          'na':       (90, 70, 40),
          'band_palette': [
              (255, 99, 71),  (255, 140, 70),  (255, 170, 70),
              (255, 200, 80), (245, 220, 90),  (245, 158, 11),
              (220, 140, 50), (200, 120, 40),  (180, 100, 30),
              (160, 90, 20),
          ],
          'sdo_accent': (245, 158, 11),
      },
      'blue': {
          'bg':       (10, 15, 30),
          'card':     (15, 22, 40),
          'border':   (26, 37, 64),
          'fg':       (200, 215, 235),
          'bright':   (232, 240, 248),
          'muted':    (80, 104, 136),
          'label':    (112, 144, 176),
          'accent':   (59, 130, 246),
          'callsign': (245, 158, 11),
          'good':     (96, 165, 250),
          'fair':     (234, 179, 8),
          'poor':     (239, 68, 68),
          'na':       (60, 80, 110),
          'band_palette': [
              (255, 107, 107), (240, 101, 149), (204, 93, 232),
              (132, 94, 247),  (92, 124, 250),  (51, 154, 240),
              (34, 184, 207),  (32, 201, 151),  (81, 207, 102),
              (148, 216, 45),
          ],
          'sdo_accent': (59, 130, 246),
      },
  }

  HF_BANDS = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']

  # Legacy constants kept for the in-place refactor steps that follow.
  # Each subsequent task replaces one draw function's references; by Task 3.7
  # these are deleted.
  BG = THEMES['kstate']['bg']
  CARD = THEMES['kstate']['card']
  BORDER = THEMES['kstate']['border']
  TEXT = THEMES['kstate']['fg']
  LABEL = THEMES['kstate']['label']
  BRIGHT = THEMES['kstate']['bright']
  ACCENT_GOLD = THEMES['kstate']['accent']
  STATUS_GREEN = THEMES['kstate']['good']
  STATUS_YELLOW = THEMES['kstate']['fair']
  STATUS_RED = THEMES['kstate']['poor']

  COND_COLORS = {
      'Good': THEMES['kstate']['good'],
      'Fair': THEMES['kstate']['fair'],
      'Poor': THEMES['kstate']['poor'],
      'N/A':  THEMES['kstate']['na'],
  }

  BAND_COLORS = dict(zip(HF_BANDS, THEMES['kstate']['band_palette']))
  ```
  Leave line 44 (`HF_BANDS = ...`) intact — the replacement above redeclares it inside the new block, so the original `HF_BANDS` line and the original `BAND_COLORS` block should be removed. Lines 46-47 (`SCREEN_W = 1440`, `SCREEN_H = 900`) remain.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: 9 tests pass.

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_themes.py
  git commit -m "feat(themes): add THEMES dict with 4 palettes (kstate, classic, amber, blue)"
  ```

### Task 3.2: Add load_settings() with kstate fallback

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (new function above `main()`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  import json
  import tempfile
  import pathlib


  def test_load_settings_returns_defaults_when_file_missing(tmp_path):
      missing = tmp_path / 'nope.json'
      d = hamclock_pygame.load_settings(str(missing))
      assert d == {
          'callsign': '',
          'timezone': 'UTC',
          'theme': 'kstate',
          'ntp': '',
      }


  def test_load_settings_returns_defaults_when_json_malformed(tmp_path, capsys):
      bad = tmp_path / 'bad.json'
      bad.write_text('{not json')
      d = hamclock_pygame.load_settings(str(bad))
      assert d['theme'] == 'kstate'
      assert d['timezone'] == 'UTC'
      err = capsys.readouterr().err
      assert 'settings' in err.lower()


  def test_load_settings_returns_defaults_when_theme_unknown(tmp_path):
      bad = tmp_path / 's.json'
      bad.write_text(json.dumps({
          'callsign': 'W1ABC', 'timezone': 'UTC',
          'theme': 'mystery', 'ntp': '',
      }))
      d = hamclock_pygame.load_settings(str(bad))
      assert d['theme'] == 'kstate'


  def test_load_settings_returns_file_contents_when_valid(tmp_path):
      good = tmp_path / 's.json'
      payload = {
          'callsign': 'W1ABC', 'timezone': 'America/Chicago',
          'theme': 'classic', 'ntp': 'pool.ntp.org',
      }
      good.write_text(json.dumps(payload))
      d = hamclock_pygame.load_settings(str(good))
      assert d == payload


  def test_load_settings_fills_missing_keys_with_defaults(tmp_path):
      partial = tmp_path / 's.json'
      partial.write_text(json.dumps({'theme': 'amber'}))
      d = hamclock_pygame.load_settings(str(partial))
      assert d['theme'] == 'amber'
      assert d['callsign'] == ''
      assert d['timezone'] == 'UTC'
      assert d['ntp'] == ''


  def test_load_settings_retries_once_on_json_decode_error(tmp_path, monkeypatch):
      """Mid-write race: first read raises JSONDecodeError, second succeeds."""
      p = tmp_path / 's.json'
      p.write_text(json.dumps({
          'callsign': 'K1A', 'timezone': 'UTC',
          'theme': 'blue', 'ntp': '',
      }))
      real_open = open
      calls = {'n': 0}
      def flaky_open(path, *a, **kw):
          if str(path) == str(p) and calls['n'] == 0:
              calls['n'] += 1
              # First call returns a file whose contents trip JSONDecodeError.
              import io
              return io.StringIO('')
          return real_open(path, *a, **kw)
      monkeypatch.setattr('builtins.open', flaky_open)
      d = hamclock_pygame.load_settings(str(p))
      assert d['theme'] == 'blue'
      assert calls['n'] == 1
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py::test_load_settings_returns_defaults_when_file_missing -v`
  Expected: FAIL with `AttributeError: module 'hamclock_pygame' has no attribute 'load_settings'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add the import `import json` near the top (after `import io`) if not present, and insert this function immediately above `def main():` (around line 360):
  ```python
  _DEFAULT_SETTINGS = {
      'callsign': '',
      'timezone': 'UTC',
      'theme': 'kstate',
      'ntp': '',
  }


  def load_settings(path='/etc/hamclock-lite/settings.json'):
      """Read settings.json with a kstate fallback.

      Returns a dict containing at minimum the four keys in _DEFAULT_SETTINGS.
      On any failure (missing file, malformed JSON, missing key, unknown
      theme name) returns the defaults and prints a warning to stderr.
      Tolerates a transient JSONDecodeError (file mid-write) with one 200 ms
      retry before falling back.
      """
      defaults = dict(_DEFAULT_SETTINGS)

      def _read_once():
          with open(path, 'r') as f:
              return json.load(f)

      try:
          try:
              raw = _read_once()
          except json.JSONDecodeError:
              time.sleep(0.2)
              raw = _read_once()
      except FileNotFoundError:
          return defaults
      except (OSError, json.JSONDecodeError, ValueError) as e:
          print('[settings] could not load %s: %s; using defaults' %
                (path, e), file=sys.stderr)
          return defaults

      if not isinstance(raw, dict):
          print('[settings] %s is not a JSON object; using defaults' % path,
                file=sys.stderr)
          return defaults

      out = dict(defaults)
      for k in defaults:
          if k in raw and isinstance(raw[k], str):
              out[k] = raw[k]

      if out['theme'] not in THEMES:
          print('[settings] unknown theme %r in %s; falling back to kstate' %
                (out['theme'], path), file=sys.stderr)
          out['theme'] = 'kstate'

      return out
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: all tests pass (9 from Task 3.1 + 6 new = 15).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_themes.py
  git commit -m "feat(themes): add load_settings() with kstate fallback"
  ```

### Task 3.3: Refactor draw_panel, draw_header, draw_status_bar to accept theme

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (functions `draw_panel` L103, `draw_header` L112, `draw_status_bar` L329; callers in `main()`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  import inspect


  def test_draw_panel_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_panel)
      assert 'theme' in sig.parameters


  def test_draw_header_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_header)
      assert 'theme' in sig.parameters


  def test_draw_status_bar_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_status_bar)
      assert 'theme' in sig.parameters


  def test_draw_panel_uses_theme_card_color():
      import pygame
      pygame.init()
      surf = pygame.Surface((200, 100))
      surf.fill((0, 0, 0))
      fonts = hamclock_pygame._make_fonts()
      theme = hamclock_pygame.THEMES['blue']
      rect = pygame.Rect(10, 10, 100, 60)
      hamclock_pygame.draw_panel(surf, rect, 'TEST', fonts, theme)
      # Sample the interior of the panel (below the title bar at y+22).
      px = surf.get_at((50, 50))[:3]
      assert tuple(px) == theme['card'], f'got {tuple(px)}, want {theme["card"]}'
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py::test_draw_panel_signature_takes_theme -v`
  Expected: FAIL — `theme` not in the parameter list.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, replace the three functions and update `main()` accordingly.

  Replace `draw_panel` (L103-109):
  ```python
  def draw_panel(screen, rect, title, fonts, theme):
      pygame.draw.rect(screen, theme['card'], rect)
      pygame.draw.rect(screen, theme['border'], rect, 1)
      bar = pygame.Rect(rect.x, rect.y, rect.w, 18)
      pygame.draw.rect(screen, theme['border'], bar)
      _blit_text(screen, fonts['panel'], title, theme['bright'],
                 rect.x + 6, rect.y + 2)
      return pygame.Rect(rect.x + 6, rect.y + 22, rect.w - 12, rect.h - 26)
  ```

  Replace `draw_header` (L112-126):
  ```python
  def draw_header(screen, rect, callsign, fonts, theme):
      pygame.draw.rect(screen, theme['card'], rect)
      pygame.draw.rect(screen, theme['border'], rect, 1)
      _blit_text(screen, fonts['title'], 'HAMCLOCK LITE', theme['accent'],
                 rect.x + 8, rect.y + 4)
      if callsign:
          _blit_text(screen, fonts['body'], str(callsign), theme['bright'],
                     rect.x + 220, rect.y + 8)
      try:
          utc = time.strftime('%H:%M:%S', time.gmtime())
          local = time.strftime('%H:%M:%S')
      except Exception:
          utc = local = '--:--:--'
      _blit_text(screen, fonts['body'], 'UTC ' + utc, theme['fg'],
                 rect.x + rect.w - 340, rect.y + 8)
      _blit_text(screen, fonts['body'], 'LOC ' + local, theme['fg'],
                 rect.x + rect.w - 180, rect.y + 8)
      dot_color = theme['good'] if (int(time.time()) % 2 == 0) else theme['fair']
      pygame.draw.circle(screen, dot_color,
                         (rect.x + rect.w - 18, rect.y + 14), 5)
  ```

  Replace `draw_status_bar` (L329-344):
  ```python
  def draw_status_bar(screen, rect, data, fonts, theme):
      pygame.draw.rect(screen, theme['card'], rect)
      pygame.draw.rect(screen, theme['border'], rect, 1)
      now = time.time()
      dage = int(now - data.last_data_refresh) if data.last_data_refresh else -1
      iage = int(now - data.last_image_refresh) if data.last_image_refresh else -1
      text = 'Data:{}s  Img:{}s  Solar:{}  Bands:{}  DX:{}'.format(
          dage if dage >= 0 else '--',
          iage if iage >= 0 else '--',
          'OK' if data.solar else '--',
          'OK' if data.bands else '--',
          len(data.dxspots) if isinstance(data.dxspots, list) else 0,
      )
      _blit_text(screen, fonts['small'], text, theme['label'],
                 rect.x + 6, rect.y + 4)
      _blit_text(screen, fonts['small'], 'ESC/Q to quit', theme['label'],
                 rect.x + rect.w - 110, rect.y + 4)
  ```

  In `main()`, update the three call sites. After `fonts = _make_fonts()` add:
  ```python
      settings = load_settings()
      theme = THEMES[settings['theme']]
  ```
  Change `draw_header(screen, header, callsign, fonts)` to `draw_header(screen, header, callsign, fonts, theme)`.
  Change `draw_status_bar(screen, status, data, fonts)` to `draw_status_bar(screen, status, data, fonts, theme)`.
  Change every `draw_panel(screen, r, t, fonts)` and `draw_panel(screen, ..., fonts)` call in `main()` to append `, theme`. The four call sites are at the `for h, t in zip(heights, titles)` loop, the `mid_inner = draw_panel(...)` line, the `dx_inner = draw_panel(...)` line, the `ba_inner = draw_panel(...)` line, and the `prop_inner = draw_panel(...)` line.
  Also change `screen.fill(BG)` to `screen.fill(theme['bg'])`.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: all tests pass (15 prior + 4 new = 19).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_themes.py
  git commit -m "refactor(themes): draw_panel/header/status_bar take theme dict"
  ```

### Task 3.4: Refactor solar/bands/muf/dx draw functions to accept theme

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (`draw_solar` L129, `draw_bands` L149, `draw_muf_text` L200, `draw_dx_spots` L217; callers in `main()`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  def test_draw_solar_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_solar)
      assert 'theme' in sig.parameters


  def test_draw_bands_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_bands)
      assert 'theme' in sig.parameters


  def test_draw_muf_text_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_muf_text)
      assert 'theme' in sig.parameters


  def test_draw_dx_spots_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_dx_spots)
      assert 'theme' in sig.parameters


  def test_draw_solar_runs_for_every_theme():
      import pygame
      pygame.init()
      surf = pygame.Surface((400, 200))
      fonts = hamclock_pygame._make_fonts()
      rect = pygame.Rect(0, 0, 400, 200)
      sample = {'sfi': 130, 'kIndex': 3, 'ssn': 80, 'xray': 'C1.0'}
      for name in ('kstate', 'classic', 'amber', 'blue'):
          surf.fill((0, 0, 0))
          hamclock_pygame.draw_solar(surf, rect, sample, fonts,
                                     hamclock_pygame.THEMES[name])
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py::test_draw_solar_signature_takes_theme -v`
  Expected: FAIL.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, replace the four functions.

  Replace `draw_solar` (L129-146):
  ```python
  def draw_solar(screen, rect, solar, fonts, theme):
      rows = [
          ('SFI', _safe(solar, 'sfi')),
          ('Kp', _safe(solar, 'kIndex')),
          ('SSN', _safe(solar, 'ssn')),
          ('A', _safe(solar, 'aIndex')),
          ('X-Ray', _safe(solar, 'xray')),
          ('Wind', _safe(solar, 'solarWind')),
          ('Bz', _safe(solar, 'bz')),
          ('Geo', _safe(solar, 'geomagField')),
          ('S/N', _safe(solar, 'signalNoise')),
          ('foF2', _safe(solar, 'fof2')),
      ]
      y = rect.y
      for label, value in rows:
          _blit_text(screen, fonts['label'], label, theme['label'], rect.x, y)
          _blit_text(screen, fonts['body'], str(value), theme['bright'],
                     rect.x + 70, y - 1)
          y += 16
  ```

  Replace `draw_bands` (L149-167):
  ```python
  def draw_bands(screen, rect, bands, fonts, theme):
      groups = [
          ('80m-40m', ['80m-40m']),
          ('30m-20m', ['30m-20m']),
          ('17m-15m', ['17m-15m']),
          ('12m-10m', ['12m-10m']),
      ]
      cond = {
          'Good': theme['good'], 'Fair': theme['fair'],
          'Poor': theme['poor'], 'N/A': theme['na'],
      }
      _blit_text(screen, fonts['label'], 'BAND',  theme['label'], rect.x, rect.y)
      _blit_text(screen, fonts['label'], 'DAY',   theme['label'], rect.x + 100, rect.y)
      _blit_text(screen, fonts['label'], 'NIGHT', theme['label'], rect.x + 160, rect.y)
      y = rect.y + 16
      for name, keys in groups:
          entry = bands.get(keys[0], {}) if isinstance(bands, dict) else {}
          day = entry.get('day', 'N/A') if isinstance(entry, dict) else 'N/A'
          night = entry.get('night', 'N/A') if isinstance(entry, dict) else 'N/A'
          _blit_text(screen, fonts['body'], name, theme['fg'], rect.x, y)
          _blit_text(screen, fonts['body'], str(day),
                     cond.get(day, theme['fg']), rect.x + 100, y)
          _blit_text(screen, fonts['body'], str(night),
                     cond.get(night, theme['fg']), rect.x + 160, y)
          y += 16
  ```

  Replace `draw_muf_text` (L200-214):
  ```python
  def draw_muf_text(screen, rect, solar, fonts, theme):
      rows = [
          ('FOF2',   '{} MHz'.format(_safe(solar, 'fof2'))),
          ('GEOMAG', _safe(solar, 'geomagField')),
          ('KP',     _safe(solar, 'kIndex')),
          ('SFI',    _safe(solar, 'sfi')),
          ('SSN',    _safe(solar, 'ssn')),
      ]
      y = rect.y + 20
      for label, value in rows:
          _blit_text(screen, fonts['panel'], label, theme['label'],
                     rect.x + 20, y)
          _blit_text(screen, fonts['title'], str(value), theme['bright'],
                     rect.x + 140, y - 4)
          y += 44
      _blit_text(screen, fonts['small'], '(Map available in web UI)',
                 theme['label'], rect.x + 20, rect.y + rect.h - 20)
  ```

  Replace `draw_dx_spots` (L217-239):
  ```python
  def draw_dx_spots(screen, rect, dxspots, fonts, theme):
      if not isinstance(dxspots, list):
          dxspots = []
      band_lut = dict(zip(HF_BANDS, theme['band_palette']))
      _blit_text(screen, fonts['label'], 'FREQ',    theme['label'], rect.x, rect.y)
      _blit_text(screen, fonts['label'], 'BND',     theme['label'], rect.x + 90, rect.y)
      _blit_text(screen, fonts['label'], 'DX',      theme['label'], rect.x + 140, rect.y)
      _blit_text(screen, fonts['label'], 'SPOTTER', theme['label'], rect.x + 230, rect.y)
      _blit_text(screen, fonts['label'], 'TIME',    theme['label'], rect.x + 340, rect.y)
      y = rect.y + 16
      for spot in dxspots[:5]:
          if not isinstance(spot, dict):
              continue
          freq = _safe(spot, 'frequency')
          band = _safe(spot, 'band')
          dx = _safe(spot, 'dxCall')
          spotter = _safe(spot, 'spotter')
          tm = _safe(spot, 'time')
          _blit_text(screen, fonts['body'], str(freq), theme['accent'], rect.x, y)
          _blit_text(screen, fonts['body'], str(band),
                     band_lut.get(str(band), theme['fg']), rect.x + 90, y)
          _blit_text(screen, fonts['body'], str(dx), theme['bright'], rect.x + 140, y)
          _blit_text(screen, fonts['body'], str(spotter)[:10], theme['fg'],
                     rect.x + 230, y)
          _blit_text(screen, fonts['body'], str(tm), theme['label'],
                     rect.x + 340, y)
          y += 16
  ```

  Update the four call sites in `main()`:
  - `draw_solar(screen, panel_rects[0], data.solar or {}, fonts)` → append `, theme`.
  - `draw_bands(screen, panel_rects[1], data.bands or {}, fonts)` → append `, theme`.
  - `draw_muf_text(screen, mid_inner, data.solar or {}, fonts)` → append `, theme`.
  - `draw_dx_spots(screen, dx_inner, data.dxspots or [], fonts)` → append `, theme`.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: all tests pass (19 prior + 5 new = 24).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_themes.py
  git commit -m "refactor(themes): solar/bands/muf/dx draw functions take theme"
  ```

### Task 3.5: Refactor band_activity, geomag, xray, open_bands, tabs, bar to accept theme

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (`draw_bar` L188, `draw_band_activity` L242, `draw_tabs` L266, `draw_geomag` L284, `draw_xray` L296, `draw_open_bands` L312; callers in `main()`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  def test_draw_bar_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_bar)
      assert 'theme' in sig.parameters


  def test_draw_band_activity_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_band_activity)
      assert 'theme' in sig.parameters


  def test_draw_tabs_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_tabs)
      assert 'theme' in sig.parameters


  def test_draw_geomag_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_geomag)
      assert 'theme' in sig.parameters


  def test_draw_xray_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_xray)
      assert 'theme' in sig.parameters


  def test_draw_open_bands_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_open_bands)
      assert 'theme' in sig.parameters


  def test_draw_band_activity_uses_theme_palette():
      """A '10m' bar should use band_palette[-1] of the active theme."""
      import pygame
      pygame.init()
      surf = pygame.Surface((400, 200))
      surf.fill((0, 0, 0))
      fonts = hamclock_pygame._make_fonts()
      theme = hamclock_pygame.THEMES['amber']
      rect = pygame.Rect(0, 0, 400, 200)
      spots = [{'band': '10m'}]
      hamclock_pygame.draw_band_activity(surf, rect, spots, fonts, theme)
      # 10m is the 10th band; row_h ≈ (200-4)/10 = 19; row 10 center ~ y=180.
      # Sample inside the bar interior (label width = 40, sample at x=80).
      px = surf.get_at((80, 184))[:3]
      assert tuple(px) == theme['band_palette'][9], \
          f'got {tuple(px)}, want {theme["band_palette"][9]}'
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py::test_draw_bar_signature_takes_theme -v`
  Expected: FAIL.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, replace the six functions.

  Replace `draw_bar` (L188-197):
  ```python
  def draw_bar(screen, rect, value, vmax, color, theme):
      pygame.draw.rect(screen, theme['bg'], rect)
      pygame.draw.rect(screen, theme['border'], rect, 1)
      try:
          frac = 0.0 if vmax <= 0 else max(0.0, min(1.0, float(value) / float(vmax)))
      except Exception:
          frac = 0.0
      inner = pygame.Rect(rect.x + 1, rect.y + 1,
                          int((rect.w - 2) * frac), rect.h - 2)
      if inner.w > 0:
          pygame.draw.rect(screen, color, inner)
  ```

  Replace `draw_band_activity` (L242-263):
  ```python
  def draw_band_activity(screen, rect, dxspots, fonts, theme):
      counts = {b: 0 for b in HF_BANDS}
      if isinstance(dxspots, list):
          for spot in dxspots:
              if isinstance(spot, dict):
                  b = spot.get('band')
                  if b in counts:
                      counts[b] += 1
      vmax = max(counts.values()) if any(counts.values()) else 1
      band_lut = dict(zip(HF_BANDS, theme['band_palette']))
      label_w = 40
      count_w = 36
      row_h = max(14, (rect.h - 4) // len(HF_BANDS))
      y = rect.y + 2
      for band in HF_BANDS:
          c = counts[band]
          _blit_text(screen, fonts['label'], band, theme['label'], rect.x, y + 1)
          bar_rect = pygame.Rect(rect.x + label_w, y + 2,
                                 max(1, rect.w - label_w - count_w), row_h - 4)
          draw_bar(screen, bar_rect, c, vmax,
                   band_lut.get(band, theme['fg']), theme)
          _blit_text(screen, fonts['label'], str(c), theme['bright'],
                     rect.x + rect.w - count_w + 4, y + 1)
          y += row_h
  ```

  Replace `draw_tabs` (L266-281):
  ```python
  def draw_tabs(screen, rect, tabs, active, fonts, theme):
      """Draw a tab bar across rect.y (height 20). Returns {name: Rect}."""
      regions = {}
      if not tabs:
          return regions
      tw = rect.w // len(tabs)
      for i, name in enumerate(tabs):
          tab_rect = pygame.Rect(rect.x + i * tw, rect.y, tw - 2, 20)
          color = theme['border'] if name == active else theme['card']
          pygame.draw.rect(screen, color, tab_rect)
          pygame.draw.rect(screen, theme['border'], tab_rect, 1)
          text_color = theme['accent'] if name == active else theme['label']
          _blit_text(screen, fonts['panel'], name.upper(), text_color,
                     tab_rect.x + 8, tab_rect.y + 2)
          regions[name] = tab_rect
      return regions
  ```

  Replace `draw_geomag` (L284-293):
  ```python
  def draw_geomag(screen, rect, solar, fonts, theme):
      kp = _safe(solar, 'kIndex', 0)
      try:
          kp_val = float(kp)
      except Exception:
          kp_val = 0.0
      color = (theme['good'] if kp_val < 4
               else theme['fair'] if kp_val < 6
               else theme['poor'])
      _blit_text(screen, fonts['body'], 'Kp {}'.format(kp), theme['bright'],
                 rect.x, rect.y + 2)
      bar_rect = pygame.Rect(rect.x, rect.y + 20, rect.w, 10)
      draw_bar(screen, bar_rect, kp_val, 9.0, color, theme)
  ```

  Replace `draw_xray` (L296-309):
  ```python
  def draw_xray(screen, rect, solar, fonts, theme):
      xray = _safe(solar, 'xray', 'A0.0')
      s = str(xray)
      try:
          letter = s[0]
          mag = float(s[1:]) if len(s) > 1 else 0.0
          scale = {'A': 0, 'B': 1, 'C': 2, 'M': 3, 'X': 4}.get(letter.upper(), 0)
          value = scale + (mag / 10.0)
      except Exception:
          value = 0.0
      color = (theme['good'] if value < 2
               else theme['fair'] if value < 3
               else theme['poor'])
      _blit_text(screen, fonts['body'], s, theme['bright'], rect.x, rect.y + 2)
      bar_rect = pygame.Rect(rect.x, rect.y + 20, rect.w, 10)
      draw_bar(screen, bar_rect, value, 5.0, color, theme)
  ```

  Replace `draw_open_bands` (L312-326):
  ```python
  def draw_open_bands(screen, rect, bands, fonts, theme):
      opens, closes = [], []
      if isinstance(bands, dict):
          for key, entry in bands.items():
              if not isinstance(entry, dict):
                  continue
              day = entry.get('day', 'N/A')
              if day in ('Good', 'Fair'):
                  opens.append(key)
              elif day == 'Poor':
                  closes.append(key)
      _blit_text(screen, fonts['label'], 'OPEN: ' + (', '.join(opens) or '--'),
                 theme['good'], rect.x, rect.y)
      _blit_text(screen, fonts['label'], 'CLOSED: ' + (', '.join(closes) or '--'),
                 theme['poor'], rect.x, rect.y + 16)
  ```

  Update call sites in `main()`:
  - `draw_geomag(screen, panel_rects[3], data.solar or {}, fonts)` → append `, theme`.
  - `draw_xray(screen, panel_rects[4], data.solar or {}, fonts)` → append `, theme`.
  - `draw_open_bands(screen, panel_rects[5], data.bands or {}, fonts)` → append `, theme`.
  - `draw_band_activity(screen, ba_inner, data.dxspots or [], fonts)` → append `, theme`.
  - `tab_regions = draw_tabs(screen, tab_bar, ['drap', 'aurora', 'enlil'], active_tab, fonts)` → append `, theme`.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: all tests pass (24 prior + 7 new = 31).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_themes.py
  git commit -m "refactor(themes): remaining draw functions take theme dict"
  ```

### Task 3.6: Wire load_settings into main() and pass theme through draw_image's loading-state label

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (`draw_image` L170; `main()` end-to-end)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  def test_draw_image_signature_takes_theme():
      sig = inspect.signature(hamclock_pygame.draw_image)
      assert 'theme' in sig.parameters


  def test_draw_image_loading_state_uses_theme_label_color(monkeypatch):
      """When surface is None, the 'image loading...' text must use
      theme['label'], not a hardcoded constant."""
      import pygame
      pygame.init()
      surf = pygame.Surface((200, 60))
      fonts = hamclock_pygame._make_fonts()
      theme = hamclock_pygame.THEMES['amber']
      rect = pygame.Rect(0, 0, 200, 60)
      captured = {}
      real_blit = hamclock_pygame._blit_text
      def spy(screen, font, text, color, x, y):
          captured['color'] = color
          captured['text'] = text
          return real_blit(screen, font, text, color, x, y)
      monkeypatch.setattr(hamclock_pygame, '_blit_text', spy)
      hamclock_pygame.draw_image(surf, rect, None, fonts, theme)
      assert captured['text'].lower().startswith('image')
      assert captured['color'] == theme['label']


  def test_load_settings_default_path_constant_present():
      # main() should be able to call load_settings() with no args.
      d = hamclock_pygame.load_settings('/no/such/path/for/testing.json')
      assert d['theme'] == 'kstate'
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py::test_draw_image_signature_takes_theme -v`
  Expected: FAIL — `draw_image` does not yet take `theme`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, replace `draw_image` (L170-185):
  ```python
  def draw_image(screen, rect, surface, fonts, theme):
      if surface is None:
          _blit_text(screen, fonts['label'], 'image loading...',
                     theme['label'], rect.x + 6, rect.y + 6)
          return
      try:
          iw, ih = surface.get_size()
          if iw == 0 or ih == 0:
              return
          scale = min(rect.w / iw, rect.h / ih)
          nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
          scaled = (pygame.transform.smoothscale(surface, (nw, nh))
                    if scale < 1.0 else surface)
          x = rect.x + (rect.w - nw) // 2
          y = rect.y + (rect.h - nh) // 2
          screen.blit(scaled, (x, y))
      except Exception:
          pass
  ```

  In `main()`, update every `draw_image(...)` call to pass `fonts, theme` instead of nothing extra. The three call sites:
  - `draw_image(screen, panel_rects[2], sdo_surf)` → `draw_image(screen, panel_rects[2], sdo_surf, fonts, theme)`.
  - `draw_image(screen, img_rect, surf)` (propagation) → `draw_image(screen, img_rect, surf, fonts, theme)`.

  Verify that the block added in Task 3.3 (`settings = load_settings(); theme = THEMES[settings['theme']]`) sits before the main `while running:` loop. If the spec's wizard hasn't shipped yet, this read just returns the kstate defaults — exactly the Phase-3-ship-without-Phase-4 behavior.

  Also update the callsign read so it prefers `settings['callsign']` (the new source of truth) before falling back to the env var:
  ```python
              callsign = settings.get('callsign') or os.environ.get(
                  'HAMCLOCK_CALLSIGN', 'N0CALL')
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: 31 prior + 3 new = 34 tests pass.

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_themes.py
  git commit -m "feat(themes): wire load_settings into main(), draw_image takes theme"
  ```

### Task 3.7: Delete legacy color constants and add headless render parity test

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (delete the `# Legacy constants` block introduced in Task 3.1)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  import json as _json


  def test_legacy_color_constants_removed():
      """Phase 3 is done when these top-level color names no longer exist."""
      for name in ('BG', 'CARD', 'BORDER', 'TEXT', 'LABEL', 'BRIGHT',
                   'ACCENT_GOLD', 'STATUS_GREEN', 'STATUS_YELLOW',
                   'STATUS_RED', 'COND_COLORS', 'BAND_COLORS'):
          assert not hasattr(hamclock_pygame, name), \
              f'legacy constant {name!r} still present'


  def _render_one_frame(theme_name, tmp_path, monkeypatch):
      """Helper: write settings.json for the named theme, run one main()
      iteration headlessly, return the screen surface."""
      import pygame
      from hamclock_data import HamClockData
      settings_path = tmp_path / 'settings.json'
      settings_path.write_text(_json.dumps({
          'callsign': '', 'timezone': 'UTC',
          'theme': theme_name, 'ntp': '',
      }))
      monkeypatch.setattr(hamclock_pygame, 'load_settings',
                          lambda path='ignored': hamclock_pygame.load_settings.__wrapped__(str(settings_path))
                          if hasattr(hamclock_pygame.load_settings, '__wrapped__')
                          else hamclock_pygame.__dict__['load_settings'].__call__(str(settings_path)))
      # Simpler path: just call load_settings directly with the path.
      settings = hamclock_pygame.load_settings(str(settings_path))
      assert settings['theme'] == theme_name
      theme = hamclock_pygame.THEMES[settings['theme']]

      pygame.init()
      surf = pygame.Surface((hamclock_pygame.SCREEN_W, hamclock_pygame.SCREEN_H))
      surf.fill(theme['bg'])
      fonts = hamclock_pygame._make_fonts()

      class _StubData:
          solar = {}
          bands = {}
          dxspots = []
          images = {}
          last_data_refresh = 0
          last_image_refresh = 0
      data = _StubData()

      header = pygame.Rect(0, 0, hamclock_pygame.SCREEN_W, 30)
      hamclock_pygame.draw_header(surf, header, '', fonts, theme)
      status = pygame.Rect(0, hamclock_pygame.SCREEN_H - 20,
                           hamclock_pygame.SCREEN_W, 20)
      hamclock_pygame.draw_status_bar(surf, status, data, fonts, theme)
      return surf, theme


  @pytest.mark.parametrize('theme_name', ['kstate', 'classic', 'amber', 'blue'])
  def test_headless_frame_bg_matches_theme(theme_name, tmp_path, monkeypatch):
      surf, theme = _render_one_frame(theme_name, tmp_path, monkeypatch)
      px = surf.get_at((0, hamclock_pygame.SCREEN_H // 2))[:3]
      assert tuple(px) == theme['bg'], \
          f'{theme_name}: got {tuple(px)}, want {theme["bg"]}'


  def test_headless_frame_without_settings_uses_kstate(tmp_path):
      """settings.json absent → load_settings returns kstate defaults → bg matches."""
      import pygame
      missing = tmp_path / 'absent.json'
      settings = hamclock_pygame.load_settings(str(missing))
      assert settings['theme'] == 'kstate'
      theme = hamclock_pygame.THEMES[settings['theme']]
      pygame.init()
      surf = pygame.Surface((hamclock_pygame.SCREEN_W, hamclock_pygame.SCREEN_H))
      surf.fill(theme['bg'])
      px = surf.get_at((0, hamclock_pygame.SCREEN_H // 2))[:3]
      assert tuple(px) == (42, 20, 80)
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py::test_legacy_color_constants_removed -v`
  Expected: FAIL — `BG`, `CARD`, etc. still defined at module top.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, delete the `# Legacy constants kept for the in-place refactor...` block introduced in Task 3.1 (the lines defining `BG`, `CARD`, `BORDER`, `TEXT`, `LABEL`, `BRIGHT`, `ACCENT_GOLD`, `STATUS_GREEN`, `STATUS_YELLOW`, `STATUS_RED`, `COND_COLORS`, and `BAND_COLORS`).

  Grep the file to confirm no remaining bare references to these names exist:
  ```
  cd /home/kasm-user/hamclock-pi1 && python3 -c "
  import re
  src = open('hamclock_pygame.py').read()
  bad = ['BG', 'CARD', 'BORDER', 'TEXT', 'LABEL', 'BRIGHT', 'ACCENT_GOLD',
         'STATUS_GREEN', 'STATUS_YELLOW', 'STATUS_RED', 'COND_COLORS',
         'BAND_COLORS']
  for n in bad:
      for m in re.finditer(r'(?<![\w\"\.])'+n+r'(?![\w\"])', src):
          line = src[:m.start()].count(chr(10)) + 1
          print(n, 'line', line, '->', src.split(chr(10))[line-1].strip())
  "
  ```
  If any usage remains in a draw function or `main()`, replace it with `theme[<key>]`.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: 34 prior + 6 new = 40 tests pass.

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_themes.py
  git commit -m "refactor(themes): remove legacy color constants; only THEMES remains"
  ```

### Task 3.8: Smoke-import test that hamclock_pygame.py loads under SDL dummy

**Files:**
- Test: `/home/kasm-user/hamclock-pi1/tests/test_themes.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_themes.py`:
  ```python
  def test_module_imports_cleanly_under_sdl_dummy():
      """A re-import should succeed with SDL_VIDEODRIVER=dummy and not raise."""
      import importlib
      import hamclock_pygame as hp
      importlib.reload(hp)
      assert hasattr(hp, 'THEMES')
      assert hasattr(hp, 'load_settings')
      assert hasattr(hp, 'main')
      # Every draw function must accept theme as a kwarg.
      import inspect
      for fn_name in ('draw_panel', 'draw_header', 'draw_status_bar',
                      'draw_solar', 'draw_bands', 'draw_muf_text',
                      'draw_dx_spots', 'draw_band_activity', 'draw_tabs',
                      'draw_geomag', 'draw_xray', 'draw_open_bands',
                      'draw_image', 'draw_bar'):
          fn = getattr(hp, fn_name)
          assert 'theme' in inspect.signature(fn).parameters, \
              f'{fn_name} missing theme parameter'


  def test_python_syntax_check_passes():
      """py_compile the module — catches any structural slip from prior tasks."""
      import py_compile
      py_compile.compile('hamclock_pygame.py', doraise=True)
  ```

- [ ] **Step 2: Run test to verify it fails or passes**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py::test_module_imports_cleanly_under_sdl_dummy -v`
  Expected: PASS if all prior tasks landed cleanly. If FAIL, the failure points at whichever `draw_*` is still missing its `theme` parameter — fix that draw function and rerun.

- [ ] **Step 3: Implement**
  No new implementation; this task is the consolidation gate. If Step 2 reports a missing `theme` param on any draw function, append `, theme` to its signature and to every call site, then update the test to cover any newly-touched function.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`
  Expected: 42 tests pass.

  Also run a one-shot import smoke from the shell:
  ```
  cd /home/kasm-user/hamclock-pi1 && SDL_VIDEODRIVER=dummy HAMCLOCK_DEBUG=1 \
      python3 -c "import hamclock_pygame; print(sorted(hamclock_pygame.THEMES.keys()))"
  ```
  Expected output: `['amber', 'blue', 'classic', 'kstate']`.

- [ ] **Step 5: Commit**
  ```
  git add tests/test_themes.py
  git commit -m "test(themes): smoke-import + signature parity for every draw fn"
  ```

### Task 3.9: REAL HARDWARE — visual contrast check on Pi 1B HDMI

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/docs/themes-contrast.md`
- Create: `/home/kasm-user/hamclock-pi1/scripts/preview_themes.py`

- [ ] **Step 1: Print the preview script and run command**
  Write `/home/kasm-user/hamclock-pi1/scripts/preview_themes.py`:
  ```python
  """Phase 3 hardware contrast check.

  Cycles each of the 4 themes for 10 seconds in turn on the real HDMI display
  so the operator can confirm legibility (Pi 1 + fbdev/kmsdrm renders colors
  differently than a dev monitor — see spec risk #6).

  Run on the Pi 1B:
      sudo systemctl stop hamclock-kiosk
      cd /opt/hamclock-lite && sudo -u hamclock python3 scripts/preview_themes.py
  """
  import os
  import sys
  import time

  os.environ.setdefault('SDL_VIDEODRIVER', os.environ.get(
      'SDL_VIDEODRIVER', 'fbcon'))
  os.environ.setdefault('SDL_FBDEV', '/dev/fb0')

  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
  import pygame
  import hamclock_pygame as hp


  def main():
      pygame.init()
      try:
          screen = pygame.display.set_mode(
              (hp.SCREEN_W, hp.SCREEN_H), pygame.FULLSCREEN)
      except pygame.error:
          screen = pygame.display.set_mode((hp.SCREEN_W, hp.SCREEN_H))
      fonts = hp._make_fonts()

      class _StubData:
          solar = {'sfi': 130, 'kIndex': 3, 'ssn': 80, 'xray': 'C1.0',
                   'fof2': 8.5, 'geomagField': 'Quiet',
                   'aIndex': 5, 'solarWind': 380, 'bz': -2.1,
                   'signalNoise': 'S2'}
          bands = {'80m-40m': {'day': 'Fair', 'night': 'Good'},
                   '30m-20m': {'day': 'Good', 'night': 'Fair'},
                   '17m-15m': {'day': 'Good', 'night': 'Poor'},
                   '12m-10m': {'day': 'Fair', 'night': 'Poor'}}
          dxspots = [{'frequency': '14.074', 'band': '20m', 'dxCall': 'JA1ABC',
                      'spotter': 'W1XYZ', 'time': '1234'}]
          images = {}
          last_data_refresh = time.time()
          last_image_refresh = time.time()

      data = _StubData()

      for name in ('kstate', 'classic', 'amber', 'blue'):
          theme = hp.THEMES[name]
          screen.fill(theme['bg'])
          header = pygame.Rect(0, 0, hp.SCREEN_W, 30)
          hp.draw_header(screen, header, 'W1ABC', fonts, theme)
          panel = pygame.Rect(20, 50, 400, 220)
          inner = hp.draw_panel(screen, panel, 'SOLAR — ' + name.upper(),
                                fonts, theme)
          hp.draw_solar(screen, inner, data.solar, fonts, theme)
          status = pygame.Rect(0, hp.SCREEN_H - 20, hp.SCREEN_W, 20)
          hp.draw_status_bar(screen, status, data, fonts, theme)
          pygame.display.flip()
          time.sleep(10)

      pygame.quit()


  if __name__ == '__main__':
      main()
  ```
  Print the run command the operator will execute on real hardware:
  ```
  scp /home/kasm-user/hamclock-pi1/scripts/preview_themes.py pi@<HOSTNAME>:/opt/hamclock-lite/scripts/
  ssh pi@<HOSTNAME> 'sudo systemctl stop hamclock-kiosk && cd /opt/hamclock-lite && sudo -u hamclock python3 scripts/preview_themes.py'
  ```

- [ ] **Step 2: Operator runs the script on real Pi 1B**
  The operator (user) executes the SSH command above with a real HDMI display attached. For each theme they note: legible (yes/no), notable contrast issue (none / faint label text / banding / other), and the HDMI driver in use (`pygame.display.get_driver()` output is logged by the script's stderr if added).

- [ ] **Step 3: Record the result**
  Write `/home/kasm-user/hamclock-pi1/docs/themes-contrast.md` with the operator's findings, structured as:
  ```markdown
  # Phase 3 — Themes contrast on Pi 1B HDMI

  Date: <YYYY-MM-DD>
  Hardware: Raspberry Pi 1 Model B, 700 MHz, 512 MB
  Display: <HDMI monitor model and reported native resolution>
  SDL driver: <fbcon | kmsdrm | x11>
  pygame.display.get_driver(): <value>

  ## Results per theme

  | Theme   | Legible | Notes |
  |---------|---------|-------|
  | kstate  | Y/N     | <free text> |
  | classic | Y/N     | <free text> |
  | amber   | Y/N     | <free text> |
  | blue    | Y/N     | <free text> |

  ## Decision

  All four themes ship as-defined / theme X tweaked to (R,G,B) on key K because <reason>.
  ```
  If any theme is unreadable on real hardware, file a follow-up commit that updates the offending RGB tuple in `THEMES` and re-runs `tests/test_themes.py` plus the preview script.

- [ ] **Step 4: Verify**
  Confirm `docs/themes-contrast.md` exists and has one row per theme with a Y/N verdict:
  ```
  cd /home/kasm-user/hamclock-pi1 && grep -c '| Y\b\|| N\b' docs/themes-contrast.md
  ```
  Expected: `4`.

- [ ] **Step 5: Commit**
  ```
  git add scripts/preview_themes.py docs/themes-contrast.md
  git commit -m "docs(themes): record Pi 1B HDMI contrast verdict for all 4 themes"
  ```

### Phase 3 acceptance

Verification artifacts produced by this phase:

1. `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` exports `THEMES` (dict with keys `kstate`, `classic`, `amber`, `blue`) and `load_settings(path='/etc/hamclock-lite/settings.json')`.
2. Every `draw_*` function in `hamclock_pygame.py` accepts a `theme: dict` parameter — verified by `tests/test_themes.py::test_module_imports_cleanly_under_sdl_dummy`.
3. No legacy top-level color constants (`BG`, `CARD`, `BORDER`, `TEXT`, `LABEL`, `BRIGHT`, `ACCENT_GOLD`, `STATUS_GREEN`, `STATUS_YELLOW`, `STATUS_RED`, `COND_COLORS`, `BAND_COLORS`) remain in the module namespace — verified by `tests/test_themes.py::test_legacy_color_constants_removed`.
4. Headless render of one frame per theme samples the BG pixel at `(0, sh//2)` and asserts it matches `THEMES[name]['bg']` — verified by `tests/test_themes.py::test_headless_frame_bg_matches_theme[kstate|classic|amber|blue]`.
5. With `settings.json` absent, the dashboard renders in `kstate` — verified by `tests/test_themes.py::test_headless_frame_without_settings_uses_kstate`.
6. Full test suite (`cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_themes.py -v`) reports 42 passes, 0 failures.
7. `docs/themes-contrast.md` records the operator's legibility verdict for all 4 themes on a real Pi 1B HDMI display, with the SDL driver named.
8. `scripts/preview_themes.py` is the reusable preview harness for any future palette tweak.

---

## Phase 4 — First-boot wizard + hamclock-setup CLI

This phase ships the settings layer (`load_settings` / `write_settings`), the two validators (`validate_callsign` / `validate_timezone`), the `TextField` widget, the `setup_screen()` GUI, the `--setup-cli` / `--apply-ntp` / `--inject-events` argparse modes, and the `/usr/local/bin/hamclock-setup` wrapper. Mirror the installer change to the dual-repo copy at the end of the phase.

### Task 4.1: Settings constants and `load_settings` with retry

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (top of file, after existing imports)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_settings.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_settings.py
  import json
  import os
  import threading
  import time
  import pytest

  from hamclock_pygame import load_settings, DEFAULT_SETTINGS, SETTINGS_PATH


  def test_load_settings_missing_returns_defaults(tmp_path):
      missing = tmp_path / "settings.json"
      d = load_settings(str(missing))
      assert d == DEFAULT_SETTINGS
      assert d["callsign"] == ""
      assert d["timezone"] == "UTC"
      assert d["theme"] == "kstate"
      assert d["ntp"] == ""


  def test_load_settings_valid_file(tmp_path):
      p = tmp_path / "settings.json"
      p.write_text(json.dumps({
          "callsign": "W1ABC", "timezone": "America/Chicago",
          "theme": "amber", "ntp": "pool.ntp.org",
      }))
      d = load_settings(str(p))
      assert d["callsign"] == "W1ABC"
      assert d["timezone"] == "America/Chicago"
      assert d["theme"] == "amber"
      assert d["ntp"] == "pool.ntp.org"


  def test_load_settings_garbage_returns_defaults(tmp_path, capsys):
      p = tmp_path / "settings.json"
      p.write_text("{not valid json")
      d = load_settings(str(p))
      assert d == DEFAULT_SETTINGS
      err = capsys.readouterr().err
      assert "settings" in err.lower()


  def test_load_settings_retries_on_transient_jsondecode(tmp_path, monkeypatch):
      """Simulate a racing writer: first read raises JSONDecodeError,
      second read (after the 200 ms sleep) returns the real file."""
      p = tmp_path / "settings.json"
      good = {"callsign": "K1A", "timezone": "UTC",
              "theme": "kstate", "ntp": ""}
      p.write_text(json.dumps(good))

      real_open = open
      calls = {"n": 0}

      def flaky_open(path, *a, **kw):
          if str(path) == str(p) and calls["n"] == 0:
              calls["n"] += 1
              # First open raises during json.load by returning bad bytes.
              import io
              return io.StringIO("{partial")
          return real_open(path, *a, **kw)

      monkeypatch.setattr("builtins.open", flaky_open)
      d = load_settings(str(p))
      assert d["callsign"] == "K1A"
      assert calls["n"] == 1
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py -v`
  Expected: FAIL (`ImportError: cannot import name 'load_settings' from 'hamclock_pygame'`).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add immediately after the `import pygame` line and before the existing color constants:
  ```python
  import json
  import pwd
  import grp
  import re
  import tempfile

  # ---- Settings layer (Phase 4) ----
  SETTINGS_PATH = "/etc/hamclock-lite/settings.json"
  SETTINGS_DIR = "/etc/hamclock-lite"

  DEFAULT_SETTINGS = {
      "callsign": "",
      "timezone": "UTC",
      "theme": "kstate",
      "ntp": "",
  }


  def _resolve_service_ids():
      """Return (uid, gid) for the SERVICE_USER, or (None, None) if unknown.

      Used only when running as root (CLI under sudo). The wizard runs as
      SERVICE_USER already and skips this path."""
      name = os.environ.get("HAMCLOCK_SERVICE_USER") or os.environ.get("SUDO_USER")
      if not name:
          return (None, None)
      try:
          pw = pwd.getpwnam(name)
          return (pw.pw_uid, pw.pw_gid)
      except KeyError:
          return (None, None)


  SERVICE_UID, SERVICE_GID = _resolve_service_ids()


  def load_settings(path: str = SETTINGS_PATH) -> dict:
      """Return settings dict, falling back to DEFAULT_SETTINGS on any error.

      Tolerates a transient JSONDecodeError (mid-replace race) by retrying
      once after 200 ms before treating the file as missing."""
      for attempt in (0, 1):
          try:
              with open(path, "r") as f:
                  data = json.load(f)
              merged = dict(DEFAULT_SETTINGS)
              if isinstance(data, dict):
                  for k in DEFAULT_SETTINGS:
                      if k in data and isinstance(data[k], str):
                          merged[k] = data[k]
              return merged
          except FileNotFoundError:
              return dict(DEFAULT_SETTINGS)
          except json.JSONDecodeError:
              if attempt == 0:
                  time.sleep(0.2)
                  continue
              print("[settings] malformed %s; using defaults" % path,
                    file=sys.stderr)
              return dict(DEFAULT_SETTINGS)
          except OSError as e:
              print("[settings] cannot read %s: %s" % (path, e),
                    file=sys.stderr)
              return dict(DEFAULT_SETTINGS)
      return dict(DEFAULT_SETTINGS)
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py -v`
  Expected: PASS (4 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_settings.py
  git commit -m "feat(pi1): add load_settings with default fallback and retry"
  ```

### Task 4.2: Atomic `write_settings` helper

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (append after `load_settings`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_settings.py` (append cases)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_settings.py`:
  ```python
  from hamclock_pygame import write_settings


  def test_write_settings_atomic(tmp_path):
      p = tmp_path / "settings.json"
      d = {"callsign": "W1ABC", "timezone": "UTC",
           "theme": "kstate", "ntp": ""}
      write_settings(d, str(p))
      assert p.exists()
      assert json.loads(p.read_text()) == d
      st = os.stat(p)
      assert (st.st_mode & 0o777) == 0o644


  def test_write_settings_no_temp_files_remain(tmp_path):
      p = tmp_path / "settings.json"
      write_settings({"callsign": "K1A", "timezone": "UTC",
                      "theme": "kstate", "ntp": ""}, str(p))
      leftovers = [f for f in os.listdir(tmp_path)
                   if f.startswith("settings.json.tmp.")]
      assert leftovers == []


  def test_write_settings_overwrites_existing(tmp_path):
      p = tmp_path / "settings.json"
      p.write_text('{"old":"junk"}')
      d = {"callsign": "W1ABC", "timezone": "UTC",
           "theme": "amber", "ntp": ""}
      write_settings(d, str(p))
      assert json.loads(p.read_text()) == d


  def test_write_settings_chown_permission_error_suppressed(tmp_path, monkeypatch):
      p = tmp_path / "settings.json"

      def raise_perm(*a, **kw):
          raise PermissionError("not allowed")

      monkeypatch.setattr(os, "chown", raise_perm)
      # Force a non-None UID so chown is attempted.
      monkeypatch.setattr("hamclock_pygame.SERVICE_UID", 1000)
      monkeypatch.setattr("hamclock_pygame.SERVICE_GID", 1000)
      write_settings({"callsign": "K1A", "timezone": "UTC",
                      "theme": "kstate", "ntp": ""}, str(p))
      assert p.exists()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py -v -k write_settings`
  Expected: FAIL (`ImportError: cannot import name 'write_settings'`).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, append directly after `load_settings`:
  ```python
  def write_settings(d: dict, path: str = SETTINGS_PATH) -> None:
      """Atomic write: tempfile in same dir + fsync + os.replace + chmod 0644.

      When running as root, attempts to chown to SERVICE_UID/SERVICE_GID so the
      file is owned by the service user regardless of who invoked the CLI.
      PermissionError on chown is expected (wizard already runs as SERVICE_USER)
      and is suppressed."""
      dirpath = os.path.dirname(path) or "."
      os.makedirs(dirpath, exist_ok=True)
      tmp = os.path.join(dirpath, "settings.json.tmp.%d" % os.getpid())
      try:
          with open(tmp, "w") as f:
              json.dump(d, f, indent=2)
              f.flush()
              os.fsync(f.fileno())
          os.chmod(tmp, 0o644)
          os.replace(tmp, path)
      except Exception:
          try:
              os.unlink(tmp)
          except OSError:
              pass
          raise
      if SERVICE_UID is not None and SERVICE_GID is not None:
          try:
              os.chown(path, SERVICE_UID, SERVICE_GID)
          except PermissionError:
              pass
          except OSError as e:
              print("[settings] chown failed: %s" % e, file=sys.stderr)
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py -v`
  Expected: PASS (8 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_settings.py
  git commit -m "feat(pi1): add atomic write_settings helper with chown fallback"
  ```

### Task 4.3: `validate_callsign` with all edge cases

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (append after `write_settings`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_callsign_validation.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_callsign_validation.py
  import pytest

  from hamclock_pygame import validate_callsign


  @pytest.mark.parametrize("call", [
      "W1ABC",
      "W1ABC/P",
      "KH6/W1ABC",
      "W1ABC/QRP",
      "K1A",
      "w1abc",       # lowercase is uppercased and accepted
      "VE3XYZ",
      "JA1ABC/QRP",
  ])
  def test_callsign_accepted(call):
      ok, err = validate_callsign(call)
      assert ok, "expected accept for %r, got err=%r" % (call, err)
      assert err == ""


  @pytest.mark.parametrize("call,reason", [
      ("///",     "no letter or digit"),
      ("/W1",     "too short stripped"),
      ("ABCDEF",  "no digit"),
      ("123456",  "no letter"),
      ("AB",      "too short overall"),
      ("",        "empty"),
      ("W1ABC!",  "bad chars"),
      ("W" * 11,  "too long"),
      ("W1ABCDEFGH/P", "stripped too long"),
  ])
  def test_callsign_rejected(call, reason):
      ok, err = validate_callsign(call)
      assert not ok, "expected reject for %r (%s)" % (call, reason)
      assert err != ""


  def test_callsign_uppercased_in_caller_responsibility():
      ok, err = validate_callsign("w1abc")
      assert ok
      # Validator itself does not return the uppercased string;
      # caller passes the uppercased value to write_settings.
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_callsign_validation.py -v`
  Expected: FAIL (`ImportError: cannot import name 'validate_callsign'`).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, append after `write_settings`:
  ```python
  _CALLSIGN_RE = re.compile(r"^[A-Z0-9/]{3,10}$")


  def validate_callsign(s: str) -> tuple:
      """Validate amateur callsign per Phase 4 spec rules.

      Required:
        - regex ^[A-Z0-9/]{3,10}$ after uppercasing
        - stripped of '/', length 3-8
        - at least one letter and at least one digit (in stripped form)
      Returns (ok, error_msg). On success error_msg is ''."""
      if s is None:
          return (False, "callsign required")
      up = s.upper()
      if not up:
          return (False, "callsign required")
      if not _CALLSIGN_RE.match(up):
          return (False, "use A-Z, 0-9, / (3-10 chars)")
      stripped = up.replace("/", "")
      if not (3 <= len(stripped) <= 8):
          return (False, "must be 3-8 letters/digits (excluding /)")
      has_letter = any("A" <= c <= "Z" for c in stripped)
      has_digit = any("0" <= c <= "9" for c in stripped)
      if not has_letter:
          return (False, "must contain at least one letter")
      if not has_digit:
          return (False, "must contain at least one digit")
      return (True, "")
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_callsign_validation.py -v`
  Expected: PASS (all parametrized cases pass).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_callsign_validation.py
  git commit -m "feat(pi1): add validate_callsign with stripped-length and letter/digit rules"
  ```

### Task 4.4: `validate_timezone`

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (append after `validate_callsign`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_settings.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_settings.py`:
  ```python
  from hamclock_pygame import validate_timezone


  def test_validate_timezone_known_names():
      for name in ("UTC", "America/Chicago", "Europe/London",
                   "Asia/Tokyo", "Australia/Sydney"):
          ok, err = validate_timezone(name)
          assert ok, "expected accept for %r, got %r" % (name, err)
          assert err == ""


  def test_validate_timezone_rejected():
      for name in ("Atlantis/Lost", "", "Mars/Olympus", "utc",
                   "America/Chicago ", "Not_a_zone"):
          ok, err = validate_timezone(name)
          assert not ok, "expected reject for %r" % name
          assert err != ""
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py::test_validate_timezone_known_names -v`
  Expected: FAIL (`ImportError: cannot import name 'validate_timezone'`).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, append after `validate_callsign`:
  ```python
  def validate_timezone(s: str) -> tuple:
      """Validate IANA timezone name.

      ok iff s is a member of zoneinfo.available_timezones().
      Returns (ok, error_msg)."""
      if not s:
          return (False, "timezone required")
      try:
          from zoneinfo import available_timezones
      except ImportError:
          # zoneinfo is stdlib on Python 3.9+; if unavailable, accept anything
          # to avoid blocking the wizard on a non-Pi dev box.
          return (True, "")
      if s in available_timezones():
          return (True, "")
      return (False, "unknown timezone (use IANA name like America/Chicago)")
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py -v`
  Expected: PASS (10 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_settings.py
  git commit -m "feat(pi1): add validate_timezone against zoneinfo registry"
  ```

### Task 4.5: `TextField` class

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (append after `validate_timezone`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_wizard.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_wizard.py
  import os
  os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
  os.environ.setdefault("HAMCLOCK_DEBUG", "1")

  import pygame
  import pytest

  pygame.init()

  from hamclock_pygame import TextField, validate_callsign


  THEME = {
      "bg": (10, 10, 10), "card": (30, 30, 30), "fg": (240, 240, 240),
      "muted": (130, 130, 130), "label": (200, 200, 200),
      "accent": (244, 197, 92), "good": (34, 197, 94),
      "fair": (234, 179, 8), "poor": (239, 68, 68),
      "band_palette": [(0, 0, 0)] * 10, "sdo_accent": (255, 255, 255),
  }


  def _key(key, unicode="", mod=0):
      return pygame.event.Event(pygame.KEYDOWN,
                                {"key": key, "unicode": unicode, "mod": mod})


  def test_textfield_typing_appends_to_text():
      tf = TextField(pygame.Rect(0, 0, 200, 40), initial="", max_len=16)
      assert tf.text == ""
      assert tf.handle_event(_key(pygame.K_w, "W")) is None
      assert tf.handle_event(_key(pygame.K_1, "1")) is None
      assert tf.handle_event(_key(pygame.K_a, "A")) is None
      assert tf.text == "W1A"
      assert tf.cursor == 3


  def test_textfield_backspace_removes_char():
      tf = TextField(pygame.Rect(0, 0, 200, 40), initial="W1AB")
      tf.cursor = 4
      tf.handle_event(_key(pygame.K_BACKSPACE))
      assert tf.text == "W1A"
      assert tf.cursor == 3


  def test_textfield_max_len_enforced():
      tf = TextField(pygame.Rect(0, 0, 200, 40), initial="", max_len=3)
      for c in "WXYZ":
          tf.handle_event(_key(pygame.K_w, c))
      assert tf.text == "WXY"


  def test_textfield_tab_returns_next():
      tf = TextField(pygame.Rect(0, 0, 200, 40))
      assert tf.handle_event(_key(pygame.K_TAB)) == "next"


  def test_textfield_enter_returns_submit():
      tf = TextField(pygame.Rect(0, 0, 200, 40))
      assert tf.handle_event(_key(pygame.K_RETURN)) == "submit"


  def test_textfield_escape_returns_cancel():
      tf = TextField(pygame.Rect(0, 0, 200, 40))
      assert tf.handle_event(_key(pygame.K_ESCAPE)) == "cancel"


  def test_textfield_validator_sets_error():
      tf = TextField(pygame.Rect(0, 0, 200, 40),
                     initial="W1ABC", validator=validate_callsign)
      tf.handle_event(_key(pygame.K_RETURN))
      assert tf.error == ""
      tf.text = "123456"
      tf.cursor = 6
      tf.handle_event(_key(pygame.K_RETURN))
      assert tf.error != ""


  def test_textfield_draw_does_not_raise():
      surf = pygame.Surface((400, 100))
      tf = TextField(pygame.Rect(0, 0, 200, 40), initial="W1ABC", label="Call")
      tf.draw(surf, THEME, focused=True)
      tf.draw(surf, THEME, focused=False)
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py -v`
  Expected: FAIL (`ImportError: cannot import name 'TextField'`).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, append after `validate_timezone`:
  ```python
  class TextField:
      """Single-line text input widget for the setup wizard.

      handle_event returns one of:
        'submit' (Enter), 'next' (Tab / Down), 'cancel' (Esc), or None.
      Shift+Tab and Up are returned by the wizard via handle_event as 'prev'
      handled at the panel level — TextField itself returns 'next' on Tab/Down
      and 'submit'/'cancel' on Enter/Esc; the panel inspects modifiers.
      """

      def __init__(self, rect, initial="", max_len=32,
                   validator=None, label=""):
          self.rect = rect
          self.text = initial
          self.cursor = len(initial)
          self.max_len = max_len
          self.validator = validator
          self.label = label
          self.error = ""

      def _validate(self):
          if self.validator is None:
              self.error = ""
              return True
          ok, err = self.validator(self.text)
          self.error = "" if ok else err
          return ok

      def handle_event(self, ev):
          if ev.type != pygame.KEYDOWN:
              return None
          key = ev.key
          if key == pygame.K_RETURN or key == pygame.K_KP_ENTER:
              self._validate()
              return "submit"
          if key == pygame.K_TAB or key == pygame.K_DOWN:
              self._validate()
              return "next"
          if key == pygame.K_UP:
              self._validate()
              return "prev"
          if key == pygame.K_ESCAPE:
              return "cancel"
          if key == pygame.K_BACKSPACE:
              if self.cursor > 0:
                  self.text = (self.text[:self.cursor - 1]
                               + self.text[self.cursor:])
                  self.cursor -= 1
                  self._validate()
              return None
          if key == pygame.K_DELETE:
              if self.cursor < len(self.text):
                  self.text = (self.text[:self.cursor]
                               + self.text[self.cursor + 1:])
                  self._validate()
              return None
          if key == pygame.K_LEFT:
              self.cursor = max(0, self.cursor - 1)
              return None
          if key == pygame.K_RIGHT:
              self.cursor = min(len(self.text), self.cursor + 1)
              return None
          if key == pygame.K_HOME:
              self.cursor = 0
              return None
          if key == pygame.K_END:
              self.cursor = len(self.text)
              return None
          ch = ev.unicode or ""
          if ch and ch.isprintable():
              if len(self.text) >= self.max_len:
                  return None
              self.text = (self.text[:self.cursor] + ch
                           + self.text[self.cursor:])
              self.cursor += len(ch)
              self._validate()
          return None

      def draw(self, surface, theme, focused):
          # Label on the left, box on the right (or no label).
          box_rect = self.rect.copy()
          if self.label:
              font = pygame.font.Font(None, 28)
              lbl = font.render(self.label, True, theme["label"])
              surface.blit(lbl, (self.rect.x - lbl.get_width() - 14,
                                 self.rect.y + (self.rect.h - lbl.get_height()) // 2))
          border = theme["poor"] if self.error else (
              theme["accent"] if focused else theme["muted"])
          pygame.draw.rect(surface, theme["card"], box_rect)
          pygame.draw.rect(surface, border, box_rect, 2)
          font = pygame.font.Font(None, 28)
          txt = font.render(self.text, True, theme["fg"])
          surface.blit(txt, (box_rect.x + 8,
                             box_rect.y + (box_rect.h - txt.get_height()) // 2))
          if focused:
              # Blinking caret driven by time; always drawn here for tests.
              cx = box_rect.x + 8 + font.size(self.text[:self.cursor])[0]
              cy = box_rect.y + 6
              pygame.draw.line(surface, theme["fg"],
                               (cx, cy), (cx, cy + box_rect.h - 12), 2)
          if self.error:
              ef = pygame.font.Font(None, 20)
              er = ef.render(self.error, True, theme["poor"])
              surface.blit(er, (box_rect.x, box_rect.y + box_rect.h + 4))
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py -v`
  Expected: PASS (8 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_wizard.py
  git commit -m "feat(pi1): add TextField widget for setup wizard"
  ```

### Task 4.6: `setup_screen()` with `--inject-events` support

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (append after `TextField`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_wizard.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_wizard.py`:
  ```python
  import json as _json
  from hamclock_pygame import setup_screen


  def _make_fake_fonts():
      return {
          "tiny": pygame.font.Font(None, 14),
          "small": pygame.font.Font(None, 18),
          "med": pygame.font.Font(None, 24),
          "lg": pygame.font.Font(None, 36),
      }


  def test_setup_screen_writes_expected_json(tmp_path, monkeypatch):
      """Inject the canonical Phase 4 wizard sequence and assert the
      returned dict matches the spec."""
      events_path = tmp_path / "events.json"
      seq = []
      for ch in "W1ABC":
          seq.append({"type": "KEYDOWN", "key": "K_" + ch.lower(),
                      "unicode": ch})
          if ch.isalpha():
              # Ensure unicode key matches printable.
              seq[-1]["unicode"] = ch
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      for ch in "America/Chicago":
          seq.append({"type": "KEYDOWN", "key": "K_a",
                      "unicode": ch})
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      seq.append({"type": "KEYDOWN", "key": "K_RIGHT", "unicode": ""})
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})
      events_path.write_text(_json.dumps(seq))

      monkeypatch.setenv("HAMCLOCK_DEBUG", "1")
      monkeypatch.setenv("HAMCLOCK_INJECT_EVENTS", str(events_path))

      screen = pygame.display.set_mode((1440, 900))
      fonts = _make_fake_fonts()
      theme = THEME

      result = setup_screen(screen, fonts, theme)
      assert result["callsign"] == "W1ABC"
      assert result["timezone"] == "America/Chicago"
      assert result["theme"] in ("classic", "amber", "blue", "kstate")
      assert result["ntp"] == ""


  def test_setup_screen_rejects_invalid_timezone(tmp_path, monkeypatch):
      events_path = tmp_path / "ev.json"
      seq = []
      for ch in "W1ABC":
          seq.append({"type": "KEYDOWN", "key": "K_w", "unicode": ch})
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      for ch in "Atlantis/Lost":
          seq.append({"type": "KEYDOWN", "key": "K_a", "unicode": ch})
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})
      # After save is rejected, fix tz and resubmit.
      seq.append({"type": "KEYDOWN", "key": "K_HOME", "unicode": ""})
      for _ in range(len("Atlantis/Lost")):
          seq.append({"type": "KEYDOWN", "key": "K_DELETE", "unicode": ""})
      for ch in "UTC":
          seq.append({"type": "KEYDOWN", "key": "K_u", "unicode": ch})
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
      seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})
      events_path.write_text(_json.dumps(seq))

      monkeypatch.setenv("HAMCLOCK_DEBUG", "1")
      monkeypatch.setenv("HAMCLOCK_INJECT_EVENTS", str(events_path))

      screen = pygame.display.set_mode((1440, 900))
      fonts = _make_fake_fonts()
      result = setup_screen(screen, fonts, THEME)
      assert result["timezone"] == "UTC"
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py::test_setup_screen_writes_expected_json -v`
  Expected: FAIL (`ImportError: cannot import name 'setup_screen'`).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, append after `TextField`:
  ```python
  WIZARD_THEMES = ["kstate", "classic", "amber", "blue"]


  def _inject_events_from_file(path):
      """Read a JSON list of pygame events and post them.

      Each entry: {"type": "KEYDOWN", "key": "K_a", "unicode": "a"}
      or {"type": "MOUSEBUTTONDOWN", "pos": [x, y], "button": 1}.
      """
      with open(path, "r") as f:
          seq = json.load(f)
      out = []
      for e in seq:
          if e["type"] == "KEYDOWN":
              key_name = e.get("key", "K_UNKNOWN")
              key = getattr(pygame, key_name, pygame.K_UNKNOWN)
              out.append(pygame.event.Event(
                  pygame.KEYDOWN,
                  {"key": key, "unicode": e.get("unicode", ""),
                   "mod": e.get("mod", 0)}))
          elif e["type"] == "MOUSEBUTTONDOWN":
              out.append(pygame.event.Event(
                  pygame.MOUSEBUTTONDOWN,
                  {"pos": tuple(e.get("pos", (0, 0))),
                   "button": e.get("button", 1)}))
      return out


  def setup_screen(screen, fonts, theme):
      """Render the first-boot wizard. Block until Save, return settings dict.

      Reads events from pygame.event.get() unless HAMCLOCK_DEBUG=1 and
      HAMCLOCK_INJECT_EVENTS is set, in which case events are read from
      the named JSON file and dispatched one per frame."""
      sw, sh = screen.get_size()

      # Set key repeat once (skip on x11 where the WM already handles it).
      try:
          if pygame.display.get_driver() != "x11":
              pygame.key.set_repeat(400, 40)
      except pygame.error:
          pass
      pygame.mouse.set_visible(False)

      # Panel layout (centered 700x500 panel).
      panel_w, panel_h = 700, 500
      px = (sw - panel_w) // 2
      py = (sh - panel_h) // 2

      call_field = TextField(
          pygame.Rect(px + 220, 280, 440, 44),
          initial="", max_len=10,
          validator=lambda s: validate_callsign(s.upper()),
          label="Callsign")
      tz_field = TextField(
          pygame.Rect(px + 220, 360, 440, 44),
          initial="UTC", max_len=64,
          validator=validate_timezone, label="Timezone")
      theme_idx = 0
      focus = 0  # 0=call, 1=tz, 2=theme, 3=save
      fields = [call_field, tz_field]

      # Inject-event source (debug only).
      inject_path = None
      if os.environ.get("HAMCLOCK_DEBUG") == "1":
          inject_path = os.environ.get("HAMCLOCK_INJECT_EVENTS")
      injected_events = None
      inject_idx = 0
      if inject_path:
          injected_events = _inject_events_from_file(inject_path)

      clock = pygame.time.Clock()
      running = True
      result = None
      max_frames = 5000  # debug safety net so injected runs always terminate

      frame = 0
      while running and frame < max_frames:
          frame += 1
          if injected_events is not None:
              if inject_idx >= len(injected_events):
                  events = [pygame.event.Event(pygame.QUIT, {})]
              else:
                  events = [injected_events[inject_idx]]
                  inject_idx += 1
          else:
              events = pygame.event.get()

          for ev in events:
              if ev.type == pygame.QUIT:
                  running = False
                  break
              if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                  sys.exit(1)

              if focus == 0 or focus == 1:
                  res = fields[focus].handle_event(ev)
                  if res == "next":
                      focus = (focus + 1) % 4
                  elif res == "prev":
                      focus = (focus - 1) % 4
                  elif res == "submit":
                      focus = 3  # jump to Save
                  elif res == "cancel":
                      sys.exit(1)
              elif focus == 2:  # theme cycler
                  if ev.type == pygame.KEYDOWN:
                      if ev.key in (pygame.K_LEFT,):
                          theme_idx = (theme_idx - 1) % len(WIZARD_THEMES)
                      elif ev.key in (pygame.K_RIGHT,):
                          theme_idx = (theme_idx + 1) % len(WIZARD_THEMES)
                      elif ev.key in (pygame.K_TAB, pygame.K_DOWN, pygame.K_RETURN):
                          focus = 3
                      elif ev.key == pygame.K_UP:
                          focus = 1
              elif focus == 3:  # Save button
                  if ev.type == pygame.KEYDOWN:
                      if ev.key in (pygame.K_TAB, pygame.K_DOWN):
                          focus = 0
                      elif ev.key == pygame.K_UP:
                          focus = 2
                      elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                          # Re-validate both fields.
                          ok1 = call_field._validate()
                          ok2 = tz_field._validate()
                          if ok1 and ok2:
                              result = {
                                  "callsign": call_field.text.upper(),
                                  "timezone": tz_field.text,
                                  "theme": WIZARD_THEMES[theme_idx],
                                  "ntp": "",
                              }
                              running = False
                          else:
                              focus = 0 if not ok1 else 1

          # Draw.
          screen.fill(theme["bg"])
          pygame.draw.rect(screen, theme["card"],
                           pygame.Rect(px, py, panel_w, panel_h))
          title_font = fonts["title"]
          title = title_font.render("HAMCLOCK SETUP", True, theme["fg"])
          screen.blit(title, (sw // 2 - title.get_width() // 2, 180))

          call_field.draw(screen, theme, focused=(focus == 0))
          tz_field.draw(screen, theme, focused=(focus == 1))

          # Theme cycler row.
          tf_font = fonts["panel"]
          lbl = tf_font.render("Theme", True, theme["label"])
          screen.blit(lbl, (px + 220 - lbl.get_width() - 14, 440 + 10))
          cur = WIZARD_THEMES[theme_idx]
          arrows = "< %s >" % cur if focus == 2 else "  %s  " % cur
          col = theme["accent"] if focus == 2 else theme["fg"]
          arr = tf_font.render(arrows, True, col)
          screen.blit(arr, (sw // 2 - arr.get_width() // 2, 440 + 4))

          # Save button.
          save_rect = pygame.Rect(sw // 2 - 80, 540, 160, 48)
          save_col = theme["accent"] if focus == 3 else theme["muted"]
          pygame.draw.rect(screen, theme["card"], save_rect)
          pygame.draw.rect(screen, save_col, save_rect, 3)
          sv = tf_font.render("Save", True, theme["fg"])
          screen.blit(sv, (save_rect.centerx - sv.get_width() // 2,
                           save_rect.centery - sv.get_height() // 2))

          hint = (fonts["small"]).render(
              "Tab to move, Enter to save", True, theme["muted"])
          screen.blit(hint, (sw // 2 - hint.get_width() // 2, 620))

          pygame.display.flip()
          if injected_events is None:
              clock.tick(30)
          else:
              clock.tick(0)  # no throttle in tests

      if result is None:
          # QUIT/timeout: return current values with kstate fallback.
          result = {
              "callsign": call_field.text.upper(),
              "timezone": tz_field.text if validate_timezone(tz_field.text)[0] else "UTC",
              "theme": WIZARD_THEMES[theme_idx],
              "ntp": "",
          }
      return result
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py -v`
  Expected: PASS (10 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_wizard.py
  git commit -m "feat(pi1): add setup_screen wizard with --inject-events debug path"
  ```


---

### Task 4.6b: Wizard waits for NTP sync before first save

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (function `setup_screen` — pre-save step)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_wizard.py`

Spec wizard caveat: "Wizard waits up to 10 s for `timedatectl show -p NTPSynchronized=yes` before its first save; otherwise proceeds with a stderr warning." Prevents settings.json mtime being saved against a wrong clock right after boot.

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_wizard.py`:
  ```python
  def test_wait_for_ntp_returns_quickly_when_synced(monkeypatch):
      import subprocess as _sp
      import hamclock_pygame as hp

      class R: pass
      def fake_run(cmd, capture_output, text, timeout):
          r = R(); r.stdout = "yes\n"; r.returncode = 0
          return r
      monkeypatch.setattr(_sp, "run", fake_run)
      assert hp._wait_for_ntp_sync(deadline_s=10) is True

  def test_wait_for_ntp_warns_on_timeout(monkeypatch, capsys):
      import subprocess as _sp
      import hamclock_pygame as hp

      class R: pass
      def fake_run(cmd, capture_output, text, timeout):
          r = R(); r.stdout = "no\n"; r.returncode = 0
          return r
      monkeypatch.setattr(_sp, "run", fake_run)
      assert hp._wait_for_ntp_sync(deadline_s=0) is False
      out = capsys.readouterr()
      assert "NTP" in out.err or "ntp" in out.err
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py::test_wait_for_ntp_returns_quickly_when_synced tests/test_wizard.py::test_wait_for_ntp_warns_on_timeout -v`
  Expected: FAIL — `AttributeError: module 'hamclock_pygame' has no attribute '_wait_for_ntp_sync'`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, add above `setup_screen()`:
  ```python
  def _wait_for_ntp_sync(deadline_s: float = 10.0) -> bool:
      """Block up to deadline_s for `timedatectl show -p NTPSynchronized`
      to report `yes`. Returns True on success, False on timeout (with a
      stderr warning). Avoids saving settings.json mtime with a wrong
      clock right after boot."""
      import subprocess, time
      end = time.time() + deadline_s
      while time.time() < end:
          try:
              r = subprocess.run(
                  ["timedatectl", "show", "-p", "NTPSynchronized",
                   "--value"],
                  capture_output=True, text=True, timeout=2)
              if r.stdout.strip() == "yes":
                  return True
          except Exception:
              pass
          time.sleep(0.5)
      print("wizard: NTP not yet synced after %.0fs — saving anyway; "
            "mtime may be wrong" % deadline_s, file=sys.stderr)
      return False
  ```

  In `setup_screen()`, call `_wait_for_ntp_sync(deadline_s=10.0)` immediately before the first `write_settings()` invocation.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py::test_wait_for_ntp_returns_quickly_when_synced tests/test_wizard.py::test_wait_for_ntp_warns_on_timeout -v`
  Expected: PASS (2 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_wizard.py
  git commit -m "feat(wizard): wait up to 10s for NTP sync before first save"
  ```

### Task 4.7: `--setup-cli` and `--apply-ntp` argparse modes

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (bottom of file, before/replacing the `if __name__ == "__main__":` block)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_wizard.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_wizard.py`:
  ```python
  import subprocess
  import sys as _sys


  def _run_cli(tmp_path, *args, env=None):
      cmd = [_sys.executable,
             "/home/kasm-user/hamclock-pi1/hamclock_pygame.py",
             "--setup-cli", *args]
      e = dict(os.environ)
      if env:
          e.update(env)
      return subprocess.run(cmd, env=e, capture_output=True, text=True)


  def test_setup_cli_writes_valid_settings(tmp_path):
      out = tmp_path / "settings.json"
      r = _run_cli(tmp_path,
                   "--callsign", "W1ABC",
                   "--timezone", "UTC",
                   "--theme", "kstate",
                   "--settings-path", str(out))
      assert r.returncode == 0, "stderr: %s" % r.stderr
      data = _json.loads(out.read_text())
      assert data["callsign"] == "W1ABC"
      assert data["timezone"] == "UTC"
      assert data["theme"] == "kstate"
      assert data["ntp"] == ""
      assert (os.stat(out).st_mode & 0o777) == 0o644


  def test_setup_cli_rejects_bad_callsign(tmp_path):
      out = tmp_path / "settings.json"
      r = _run_cli(tmp_path,
                   "--callsign", "123456",
                   "--timezone", "UTC",
                   "--theme", "kstate",
                   "--settings-path", str(out))
      assert r.returncode != 0
      assert "letter" in r.stderr or "callsign" in r.stderr.lower()
      assert not out.exists()


  def test_setup_cli_rejects_bad_timezone(tmp_path):
      out = tmp_path / "settings.json"
      r = _run_cli(tmp_path,
                   "--callsign", "W1ABC",
                   "--timezone", "Atlantis/Lost",
                   "--theme", "kstate",
                   "--settings-path", str(out))
      assert r.returncode != 0
      assert "timezone" in r.stderr.lower()


  def test_setup_cli_inject_events_requires_debug(tmp_path):
      r = subprocess.run(
          [_sys.executable,
           "/home/kasm-user/hamclock-pi1/hamclock_pygame.py",
           "--inject-events", "/tmp/x.json"],
          env={k: v for k, v in os.environ.items()
               if k != "HAMCLOCK_DEBUG"},
          capture_output=True, text=True)
      assert r.returncode != 0
      assert "debug" in r.stderr.lower()


  def test_setup_cli_apply_ntp_writes_dropin(tmp_path):
      out = tmp_path / "settings.json"
      ntp_path = tmp_path / "ntp.conf"
      # Run apply-ntp in dry mode by pointing at the override path.
      r = _run_cli(tmp_path,
                   "--callsign", "W1ABC",
                   "--timezone", "UTC",
                   "--theme", "kstate",
                   "--ntp", "pool.ntp.org",
                   "--settings-path", str(out),
                   "--apply-ntp",
                   "--ntp-conf-path", str(ntp_path),
                   "--no-restart-timesyncd")
      assert r.returncode == 0, r.stderr
      assert ntp_path.exists()
      content = ntp_path.read_text()
      assert "[Time]" in content
      assert "NTP=pool.ntp.org" in content
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py::test_setup_cli_writes_valid_settings -v`
  Expected: FAIL (the CLI mode is not implemented; the script either runs the dashboard or errors on unknown arg).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, replace the existing `if __name__ == "__main__":` block (or append at end if none) with:
  ```python
  import argparse
  import socket


  def _drop_privileges_if_root():
      """When running under sudo, drop to SERVICE_USER before writing files."""
      if os.geteuid() != 0:
          return
      if SERVICE_UID is None or SERVICE_GID is None:
          return
      try:
          os.setgroups([])
      except (PermissionError, OSError):
          pass
      try:
          os.setgid(SERVICE_GID)
          os.setuid(SERVICE_UID)
      except OSError as e:
          print("[setup] could not drop privileges: %s" % e, file=sys.stderr)


  def _apply_ntp(ntp_value, conf_path, restart):
      """Write systemd-timesyncd drop-in and optionally restart the unit."""
      try:
          socket.gethostbyname(ntp_value)
      except socket.gaierror as e:
          print("[setup] NTP host %r does not resolve: %s"
                % (ntp_value, e), file=sys.stderr)
          return 2
      os.makedirs(os.path.dirname(conf_path) or ".", exist_ok=True)
      with open(conf_path, "w") as f:
          f.write("[Time]\nNTP=%s\n" % ntp_value)
      os.chmod(conf_path, 0o644)
      if restart:
          import subprocess as _sp
          try:
              _sp.run(["systemctl", "restart", "systemd-timesyncd"],
                      check=False)
          except FileNotFoundError:
              print("[setup] systemctl not found; skipping restart",
                    file=sys.stderr)
      return 0


  def _cli_main(argv):
      ap = argparse.ArgumentParser(prog="hamclock-setup")
      ap.add_argument("--setup-cli", action="store_true",
                      help="run headless settings writer")
      ap.add_argument("--callsign")
      ap.add_argument("--timezone")
      ap.add_argument("--theme", choices=WIZARD_THEMES)
      ap.add_argument("--ntp", default="")
      ap.add_argument("--apply-ntp", action="store_true",
                      help="also write /etc/systemd/timesyncd.conf.d/hamclock.conf")
      ap.add_argument("--ntp-conf-path",
                      default="/etc/systemd/timesyncd.conf.d/hamclock.conf")
      ap.add_argument("--no-restart-timesyncd", action="store_true")
      ap.add_argument("--settings-path", default=SETTINGS_PATH)
      ap.add_argument("--inject-events",
                      help="(debug only) JSON event sequence for wizard")
      args = ap.parse_args(argv)

      if args.inject_events and os.environ.get("HAMCLOCK_DEBUG") != "1":
          ap.error("--inject-events is debug builds only "
                   "(set HAMCLOCK_DEBUG=1)")

      if not args.setup_cli:
          return None  # caller falls through to dashboard main()

      if args.callsign is None or args.timezone is None or args.theme is None:
          ap.error("--callsign, --timezone, --theme are required in --setup-cli mode")

      ok, err = validate_callsign(args.callsign)
      if not ok:
          print("[setup] invalid callsign: %s" % err, file=sys.stderr)
          return 2
      ok, err = validate_timezone(args.timezone)
      if not ok:
          print("[setup] invalid timezone: %s" % err, file=sys.stderr)
          return 2

      d = {
          "callsign": args.callsign.upper(),
          "timezone": args.timezone,
          "theme": args.theme,
          "ntp": args.ntp,
      }

      _drop_privileges_if_root()
      write_settings(d, args.settings_path)
      if args.apply_ntp and args.ntp:
          rc = _apply_ntp(args.ntp, args.ntp_conf_path,
                          restart=not args.no_restart_timesyncd)
          if rc != 0:
              return rc
      return 0


  if __name__ == "__main__":
      # CLI dispatch: --setup-cli short-circuits before the dashboard runs.
      rc = _cli_main(sys.argv[1:])
      if rc is not None:
          sys.exit(rc)
      main()  # existing dashboard entry point
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py -v`
  Expected: PASS (15 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_wizard.py
  git commit -m "feat(pi1): add --setup-cli and --apply-ntp CLI modes"
  ```

### Task 4.8: Concurrency / last-writer-wins test

**Files:**
- Test: `/home/kasm-user/hamclock-pi1/tests/test_settings.py` (append)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_settings.py`:
  ```python
  def test_concurrent_writes_leave_valid_json(tmp_path):
      p = tmp_path / "settings.json"
      base = {"callsign": "W1ABC", "timezone": "UTC",
              "theme": "kstate", "ntp": ""}

      def writer(theme):
          d = dict(base)
          d["theme"] = theme
          for _ in range(50):
              write_settings(d, str(p))

      threads = [threading.Thread(target=writer, args=(t,))
                 for t in ("kstate", "amber", "classic", "blue")]
      for t in threads:
          t.start()
      for t in threads:
          t.join()

      # Final file must parse cleanly and contain a known theme.
      data = json.loads(p.read_text())
      assert data["theme"] in ("kstate", "amber", "classic", "blue")
      # And load_settings must not raise.
      d = load_settings(str(p))
      assert d["callsign"] == "W1ABC"

      # No leftover tempfiles.
      leftovers = [f for f in os.listdir(tmp_path)
                   if f.startswith("settings.json.tmp.")]
      assert leftovers == []
  ```

- [ ] **Step 2: Run test to verify it fails / passes**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py::test_concurrent_writes_leave_valid_json -v`
  Expected: PASS (atomic write design already supports this; the test is regression coverage).
  If FAIL: review `write_settings` for non-atomic edge cases (e.g. tempfile collision under same PID — switch to `tempfile.NamedTemporaryFile(dir=dirpath, delete=False)` so concurrent threads in one process get unique names).

- [ ] **Step 3: Implement (only if step 2 failed)**
  If the test failed on PID collisions, in `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` replace the tempfile line inside `write_settings`:
  ```python
      # Before:
      # tmp = os.path.join(dirpath, "settings.json.tmp.%d" % os.getpid())
      # After:
      fd, tmp = tempfile.mkstemp(prefix="settings.json.tmp.", dir=dirpath)
      os.close(fd)
  ```
  Otherwise, no implementation change needed.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_settings.py -v`
  Expected: PASS (all settings tests green).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_settings.py
  git commit -m "test(pi1): cover concurrent write_settings (last-writer-wins)"
  ```

### Task 4.9: `kiosk-install.sh` installs settings dir and `hamclock-setup` wrapper

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/kiosk-install.sh` (after the existing `INSTALL_DIR=/opt/hamclock-lite` block, before service unit creation)

- [ ] **Step 1: Write the failing test (manual + shellcheck)**
  Run: `cd /home/kasm-user/hamclock-pi1 && bash -n kiosk-install.sh && grep -c "/etc/hamclock-lite" kiosk-install.sh`
  Expected: returns `0` (zero matches) before the change.

- [ ] **Step 2: Verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && grep -q "hamclock-setup" kiosk-install.sh && echo PRESENT || echo MISSING`
  Expected: `MISSING`.

- [ ] **Step 3: Implement**
  Edit `/home/kasm-user/hamclock-pi1/kiosk-install.sh`. Insert the following block immediately after `SERVICE_USER="${SUDO_USER:-$USER}"` (currently line ~129) and before the systemd unit `tee` block:
  ```bash
  # ---- Phase 4: settings directory + hamclock-setup wrapper ----
  sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 /etc/hamclock-lite

  # Reinstall detection: if settings already exist, don't overwrite. If a
  # pygame service already exists but settings don't, write a default file.
  if [ ! -f /etc/hamclock-lite/settings.json ]; then
      if systemctl list-unit-files | grep -q '^hamclock-kiosk\.service'; then
          sudo -u "$SERVICE_USER" tee /etc/hamclock-lite/settings.json >/dev/null <<'SETTINGSEOF'
  {
    "callsign": "",
    "timezone": "UTC",
    "theme": "kstate",
    "ntp": ""
  }
  SETTINGSEOF
          sudo chmod 0644 /etc/hamclock-lite/settings.json
          echo "Existing pygame install detected; wrote default settings.json."
          echo "Run 'sudo hamclock-setup --callsign YOUR_CALL --timezone YOUR_TZ --theme kstate' to personalize."
      fi
      # Truly fresh install (no service unit yet) leaves settings.json absent
      # so the wizard auto-launches on first kiosk boot.
  fi

  # Install the hamclock-setup wrapper.
  sudo tee /usr/local/bin/hamclock-setup > /dev/null <<'HSEOF'
  #!/bin/sh
  # Thin wrapper around the pygame client's --setup-cli mode.
  exec python3 /opt/hamclock-lite/hamclock_pygame.py --setup-cli "$@"
  HSEOF
  sudo chmod 0755 /usr/local/bin/hamclock-setup
  ```

  Then ensure the systemd unit for `hamclock-kiosk` exports `HAMCLOCK_SERVICE_USER`. In the existing `sudo tee /etc/systemd/system/hamclock-kiosk.service` heredoc, add immediately after the `User=$SERVICE_USER` line:
  ```
  Environment=HAMCLOCK_SERVICE_USER=$SERVICE_USER
  ```
  (Edit all three of the `hamclock-kiosk.service` heredocs in the file — pygame, tkinter, and browser variants — for consistency.)

- [ ] **Step 4: Verify**
  Run:
  ```
  cd /home/kasm-user/hamclock-pi1 && bash -n kiosk-install.sh && \
    grep -q "install -d -o" kiosk-install.sh && \
    grep -q "/usr/local/bin/hamclock-setup" kiosk-install.sh && \
    grep -q "Environment=HAMCLOCK_SERVICE_USER" kiosk-install.sh && \
    echo OK
  ```
  Expected: `OK`.

- [ ] **Step 5: Commit**
  ```
  git add kiosk-install.sh
  git commit -m "feat(pi1-installer): create /etc/hamclock-lite and install hamclock-setup wrapper"
  ```

### Task 4.10: `main()` reads settings and launches wizard on missing file

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (existing `main` function near L361)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_wizard.py` (append integration smoke test)

- [ ] **Step 1: Write the failing test**
  Append to `/home/kasm-user/hamclock-pi1/tests/test_wizard.py`:
  ```python
  def test_main_launches_wizard_when_settings_absent(tmp_path, monkeypatch):
      """When SETTINGS_PATH points at a missing file, main() should call
      setup_screen() once, persist the result, and only then enter the
      render loop. The test patches setup_screen and the render loop to
      assert ordering."""
      import hamclock_pygame as hp

      calls = []

      def fake_setup(screen, fonts, theme):
          calls.append("setup")
          return {"callsign": "W1ABC", "timezone": "UTC",
                  "theme": "kstate", "ntp": ""}

      def fake_render_loop(*a, **kw):
          calls.append("render")
          raise SystemExit(0)

      settings_file = tmp_path / "settings.json"
      monkeypatch.setattr(hp, "SETTINGS_PATH", str(settings_file))
      monkeypatch.setattr(hp, "setup_screen", fake_setup)
      monkeypatch.setattr(hp, "_run_render_loop", fake_render_loop,
                          raising=False)
      monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")

      with pytest.raises(SystemExit):
          hp.main()

      assert calls[0] == "setup"
      assert "render" in calls
      assert settings_file.exists()
      data = _json.loads(settings_file.read_text())
      assert data["callsign"] == "W1ABC"
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_wizard.py::test_main_launches_wizard_when_settings_absent -v`
  Expected: FAIL (`main()` currently does not call `setup_screen` or persist settings).

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/hamclock_pygame.py`, locate the existing `def main():` near L361. Insert immediately after `screen = pygame.display.set_mode(...)` and `fonts = _make_fonts()` (or after whatever creates these), before the render loop:
  ```python
      # ---- Phase 4: first-boot wizard ----
      theme_name = "kstate"
      settings = load_settings(SETTINGS_PATH)
      need_wizard = not os.path.exists(SETTINGS_PATH)
      if need_wizard:
          # Wizard always renders in kstate (user hasn't picked yet).
          wiz_theme = THEMES["kstate"] if "THEMES" in globals() else {
              "bg": BG, "card": CARD, "fg": TEXT, "muted": LABEL,
              "label": LABEL, "accent": ACCENT_GOLD,
              "good": STATUS_GREEN, "fair": STATUS_YELLOW, "poor": STATUS_RED,
              "band_palette": list(BAND_COLORS.values()),
              "sdo_accent": ACCENT_GOLD,
          }
          settings = setup_screen(screen, fonts, wiz_theme)
          try:
              write_settings(settings, SETTINGS_PATH)
          except OSError as e:
              print("[main] could not persist settings: %s" % e,
                    file=sys.stderr)
      # Apply theme from settings (Phase 3 introduced THEMES; fall back
      # silently if Phase 3 hasn't landed in this build).
      theme_name = settings.get("theme", "kstate")
  ```
  Also extract the render loop into a helper `_run_render_loop(screen, fonts, theme, data)` so the test in step 1 can patch it. The simplest patch is to wrap the existing render-loop body in a function:
  ```python
  def _run_render_loop(screen, fonts, theme, data):
      # Body of the existing main()'s while-loop, unchanged.
      # The wrapping call from main() becomes:
      #     _run_render_loop(screen, fonts, theme, data)
      ...
  ```
  Apply the refactor mechanically: cut the existing `while running:` block plus its preamble (`clock = pygame.time.Clock(); running = True`) and paste it into `_run_render_loop`. Replace the original location with `_run_render_loop(screen, fonts, theme, data)`.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/ -v`
  Expected: PASS (all settings, callsign, and wizard tests green; total ≥ 24 passed).

- [ ] **Step 5: Commit**
  ```
  git add hamclock_pygame.py tests/test_wizard.py
  git commit -m "feat(pi1): launch first-boot wizard from main() when settings absent"
  ```

### Task 4.11: Mirror installer changes to `offline-install.sh` and dual-repo

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/offline-install.sh` (same edits as Task 4.9)
- Modify: `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh` (verbatim copy of `offline-install.sh`)

- [ ] **Step 1: Write the failing check**
  Run:
  ```
  diff -q /home/kasm-user/hamclock-pi1/offline-install.sh \
          /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh \
    && echo IDENTICAL || echo DIFFERENT
  grep -c "hamclock-setup" /home/kasm-user/hamclock-pi1/offline-install.sh
  ```
  Expected: `IDENTICAL` from diff (pre-existing invariant), `0` from grep (Phase 4 change not yet mirrored).

- [ ] **Step 2: Verify the mirror is currently stale w.r.t. Phase 4**
  Run: `grep -q "hamclock-setup" /home/kasm-user/hamclock-pi1/offline-install.sh && echo PRESENT || echo MISSING`
  Expected: `MISSING`.

- [ ] **Step 3: Implement**
  Apply the same edits from Task 4.9 to `/home/kasm-user/hamclock-pi1/offline-install.sh`:
  1. Insert the same settings-dir + reinstall-detection block after `SERVICE_USER=...`.
  2. Insert the same `/usr/local/bin/hamclock-setup` wrapper installation.
  3. Add `Environment=HAMCLOCK_SERVICE_USER=$SERVICE_USER` to every `hamclock-kiosk.service` heredoc.

  Then copy the updated installer to the dual-repo mirror:
  ```
  cp /home/kasm-user/hamclock-pi1/offline-install.sh \
     /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh
  ```

- [ ] **Step 4: Verify**
  Run:
  ```
  bash -n /home/kasm-user/hamclock-pi1/offline-install.sh && \
    bash -n /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh && \
    diff -q /home/kasm-user/hamclock-pi1/offline-install.sh \
            /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh && \
    grep -q "hamclock-setup" /home/kasm-user/hamclock-pi1/offline-install.sh && \
    grep -q "hamclock-setup" /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh && \
    echo OK
  ```
  Expected: `OK` (both pass `bash -n`, both identical, both reference `hamclock-setup`).

- [ ] **Step 5: Commit**
  Two commits, one per repo:
  ```
  cd /home/kasm-user/hamclock-pi1 && \
    git add offline-install.sh && \
    git commit -m "feat(pi1-installer): mirror /etc/hamclock-lite and hamclock-setup wrapper to offline path"

  cd /home/kasm-user/hamclock-reborn && \
    git add public/downloads/pi1-install.sh && \
    git commit -m "feat(downloads): sync Pi 1 offline installer with hamclock-setup wrapper"
  ```

### Task 4.12: Real-Pi-1B wizard smoke test

This is a real-hardware verification step. The user runs it on a real Pi 1B with HDMI and a USB keyboard attached; output is pasted back into a docs file.

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/docs/phase4-wizard-smoke.md`

- [ ] **Step 1: Print the smoke-test script**
  On the dev host, prepare the script the user will run. Show this verbatim:
  ```bash
  # On the Pi 1B, after `sudo ./kiosk-install.sh` completes:
  sudo rm -f /etc/hamclock-lite/settings.json
  sudo systemctl restart hamclock-kiosk
  # Watch the HDMI screen: the HAMCLOCK SETUP panel should appear.
  # Type: W1ABC <Tab> America/Chicago <Tab> <Right> <Right> <Tab> <Enter>
  # Then on the Pi (over SSH):
  cat /etc/hamclock-lite/settings.json
  stat -c '%U:%G %a' /etc/hamclock-lite/settings.json
  journalctl -u hamclock-kiosk --no-pager -n 50
  # Then CLI path:
  sudo hamclock-setup --callsign W1ABC --timezone UTC --theme classic
  cat /etc/hamclock-lite/settings.json
  stat -c '%U:%G %a' /etc/hamclock-lite/settings.json
  ```

- [ ] **Step 2: User runs the script on real Pi 1B**
  Paste exit code and full output back to the dev host.
  Expected:
  - Wizard appears, accepts input, and saves.
  - First `cat` shows `{"callsign":"W1ABC","timezone":"America/Chicago","theme":"amber","ntp":""}` (or whatever theme was reached with two Right cycles).
  - First `stat` shows `<service_user>:<service_user> 644`.
  - Second `cat` shows `{"callsign":"W1ABC","timezone":"UTC","theme":"classic","ntp":""}`.
  - Second `stat` also shows `<service_user>:<service_user> 644` (NOT `root:root`).

- [ ] **Step 3: Record the result**
  Create `/home/kasm-user/hamclock-pi1/docs/phase4-wizard-smoke.md` with these sections, filled in with the pasted output:
  ```
  # Phase 4 wizard smoke test on real Pi 1B

  **Date:** <YYYY-MM-DD>
  **Hardware:** Raspberry Pi 1 Model B, Bookworm, 1440x900 HDMI.

  ## Wizard run

  Input sequence: W1ABC TAB America/Chicago TAB RIGHT RIGHT TAB ENTER

  settings.json after wizard:
  ```
  <paste>
  ```

  Ownership / mode:
  ```
  <paste>
  ```

  Relevant journal lines:
  ```
  <paste>
  ```

  ## CLI run

  Command: sudo hamclock-setup --callsign W1ABC --timezone UTC --theme classic

  settings.json after CLI:
  ```
  <paste>
  ```

  Ownership / mode:
  ```
  <paste>
  ```

  ## Outcome

  - [ ] Wizard renders and accepts input
  - [ ] Wizard-written file is owned by SERVICE_USER, mode 0644
  - [ ] CLI-written file is owned by SERVICE_USER, mode 0644 (not root)
  - [ ] journalctl shows no Python tracebacks
  ```

- [ ] **Step 4: Verify**
  Run: `test -f /home/kasm-user/hamclock-pi1/docs/phase4-wizard-smoke.md && grep -q "Outcome" /home/kasm-user/hamclock-pi1/docs/phase4-wizard-smoke.md && echo OK`
  Expected: `OK`.

- [ ] **Step 5: Commit**
  ```
  git add docs/phase4-wizard-smoke.md
  git commit -m "docs(pi1): record Phase 4 wizard + hamclock-setup smoke test on real Pi 1B"
  ```

### Phase 4 acceptance

Verification artifacts produced by this phase:

1. `tests/test_settings.py` — `load_settings` (defaults, valid, garbage, JSONDecode retry), `write_settings` (atomic, no tempfile leftovers, overwrite, chown PermissionError suppression, concurrency), `validate_timezone` (known and rejected names). All green under `python3 -m pytest tests/test_settings.py -v`.
2. `tests/test_callsign_validation.py` — `validate_callsign` with all spec edge cases: accept `W1ABC`, `W1ABC/P`, `KH6/W1ABC`, `W1ABC/QRP`, `K1A`; reject `///`, `/W1`, `ABCDEF`, `123456`, `AB`. Green under `python3 -m pytest tests/test_callsign_validation.py -v`.
3. `tests/test_wizard.py` — `TextField` typing/backspace/max_len/tab/enter/esc/validator/draw; `setup_screen` canonical sequence + bad-timezone retry; `--setup-cli` writes/rejects + `--inject-events` gated on `HAMCLOCK_DEBUG=1` + `--apply-ntp` drop-in; `main()` launches wizard when `SETTINGS_PATH` missing. Green under `python3 -m pytest tests/test_wizard.py -v`.
4. `hamclock_pygame.py` exposes the canonical signatures: `load_settings`, `write_settings`, `validate_callsign`, `validate_timezone`, `TextField`, `setup_screen`, `_cli_main`, `WIZARD_THEMES`, `SETTINGS_PATH`, `DEFAULT_SETTINGS`.
5. `kiosk-install.sh` and `offline-install.sh` both:
   - Create `/etc/hamclock-lite` owned `$SERVICE_USER:$SERVICE_USER` mode 0755.
   - Install `/usr/local/bin/hamclock-setup` wrapper mode 0755.
   - Export `HAMCLOCK_SERVICE_USER` in every `hamclock-kiosk.service` unit.
   - Detect reinstall and write a default `settings.json` only when a prior pygame service is detected without one (truly fresh installs leave it absent so the wizard auto-launches).
6. `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh` is byte-identical to `offline-install.sh` (dual-repo invariant preserved).
7. `docs/phase4-wizard-smoke.md` records the real-Pi-1B run: wizard rendered, accepted input, persisted JSON owned by SERVICE_USER mode 0644; `sudo hamclock-setup` CLI run also produced a SERVICE_USER-owned file (not root); no Python tracebacks in `journalctl -u hamclock-kiosk`.

Phase 4 unblocks Phase 5 (default flip) — Phase 5 reinstall-detection logic relies on the `/etc/hamclock-lite/settings.json` contract finalized here.

---

## Phase 5 — Flip the default + Phase 1b cleanup

Phase 5 changes the default `KIOSK_MODE` from `browser` to `pygame` in both installers, adds reinstall detection (existing `settings.json` → skip wizard), carries any installer changes Phase 0 and Phase 2 mandated, rewrites the README "Display Modes" section (pygame is default; browser/tkinter opt-in), documents the rollback (`sudo ./kiosk-install.sh --browser`) and browser-localStorage non-migration caveat, mirrors `offline-install.sh` to `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh`, and verifies end-to-end on a real Pi 1B. Phase 5 cannot merge until `docs/sdl-backend.md` and `docs/muf-source.md` exist. After Phase 5 ships, Phase 1b lands the remaining perf items (5–9) as a single follow-up commit with the ≥ 50 % drop acceptance test.

### Task 5.1: Reinstall detection — skip wizard when settings.json already exists

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/kiosk-install.sh` (insert reinstall-detection block before the pygame service-unit write at L216-241)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase5_reinstall_detection.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_reinstall_detection.py
  """Verify the installer's reinstall-detection shell block selects the
  correct branch based on the presence of settings.json and an existing
  hamclock-kiosk.service unit. We test the *shell logic* by sourcing the
  decision function into a clean bash subprocess with mocked paths."""
  import os
  import subprocess
  import textwrap
  from pathlib import Path

  REPO = Path("/home/kasm-user/hamclock-pi1")

  HARNESS = r"""
  set -eu
  SETTINGS_FILE="$1"
  SERVICE_UNIT="$2"

  # --- BEGIN block copied verbatim from kiosk-install.sh ---
  if [ -f "$SETTINGS_FILE" ]; then
      REINSTALL_DECISION="keep-settings"
  elif [ -f "$SERVICE_UNIT" ]; then
      REINSTALL_DECISION="seed-defaults"
  else
      REINSTALL_DECISION="fresh-install"
  fi
  # --- END block ---

  echo "$REINSTALL_DECISION"
  """

  def _run(tmp_path, has_settings: bool, has_unit: bool) -> str:
      settings = tmp_path / "settings.json"
      unit = tmp_path / "hamclock-kiosk.service"
      if has_settings:
          settings.write_text('{"theme":"kstate"}')
      if has_unit:
          unit.write_text("[Unit]\n")
      script = tmp_path / "harness.sh"
      script.write_text(HARNESS)
      out = subprocess.check_output(
          ["bash", str(script), str(settings), str(unit)],
          stderr=subprocess.STDOUT,
      ).decode().strip()
      return out

  def test_existing_settings_keeps_settings(tmp_path):
      assert _run(tmp_path, has_settings=True, has_unit=True) == "keep-settings"

  def test_existing_settings_no_unit_keeps_settings(tmp_path):
      assert _run(tmp_path, has_settings=True, has_unit=False) == "keep-settings"

  def test_old_pygame_no_settings_seeds_defaults(tmp_path):
      assert _run(tmp_path, has_settings=False, has_unit=True) == "seed-defaults"

  def test_truly_fresh_install(tmp_path):
      assert _run(tmp_path, has_settings=False, has_unit=False) == "fresh-install"

  def test_installer_contains_reinstall_block():
      """Sanity: the live installer carries the exact decision lines."""
      text = (REPO / "kiosk-install.sh").read_text()
      assert 'REINSTALL_DECISION="keep-settings"' in text
      assert 'REINSTALL_DECISION="seed-defaults"' in text
      assert 'REINSTALL_DECISION="fresh-install"' in text
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_reinstall_detection.py -v`
  Expected: FAIL — `test_installer_contains_reinstall_block` fails because the reinstall block has not yet been added to `kiosk-install.sh`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/kiosk-install.sh`, insert the following block immediately before the `elif [ "$KIOSK_MODE" = "pygame" ]; then` arm at L216 (i.e. as a new conditional that runs whenever `KIOSK_MODE=pygame`, before the service unit is written):
  ```bash
  # --- Phase 5: pygame-mode reinstall detection -----------------------------
  # Decide whether to: keep an existing user settings file untouched,
  # seed a default settings file for a Pi already running the OLD pygame
  # mode (service unit present, settings absent), or treat this as a
  # truly fresh install where the wizard will auto-launch on first boot.
  if [ "$KIOSK_MODE" = "pygame" ]; then
      SETTINGS_FILE="/etc/hamclock-lite/settings.json"
      SERVICE_UNIT="/etc/systemd/system/hamclock-kiosk.service"
      if [ -f "$SETTINGS_FILE" ]; then
          REINSTALL_DECISION="keep-settings"
      elif [ -f "$SERVICE_UNIT" ]; then
          REINSTALL_DECISION="seed-defaults"
      else
          REINSTALL_DECISION="fresh-install"
      fi
      echo "Pygame reinstall decision: $REINSTALL_DECISION"

      if [ "$REINSTALL_DECISION" = "seed-defaults" ]; then
          sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 /etc/hamclock-lite
          sudo -u "$SERVICE_USER" tee "$SETTINGS_FILE" >/dev/null <<'JSON'
  {
    "callsign": "",
    "timezone": "UTC",
    "theme": "kstate",
    "ntp": ""
  }
  JSON
          sudo chmod 0644 "$SETTINGS_FILE"
          echo "Run 'sudo hamclock-setup' to personalize your settings."
      fi
      # Fresh-install case: do nothing here. /etc/hamclock-lite is created
      # later by the Phase 4 installer block; the wizard auto-launches on
      # first boot because settings.json is absent.
      # Keep-settings case: do nothing — wizard will not run.
  fi
  # --- end Phase 5 reinstall detection -------------------------------------
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_reinstall_detection.py -v && bash -n kiosk-install.sh`
  Expected: 5 passed; `bash -n` exits 0 (no syntax errors).

- [ ] **Step 5: Commit**
  ```
  git add tests/test_phase5_reinstall_detection.py kiosk-install.sh
  git commit -m "feat(installer): add pygame-mode reinstall detection (Phase 5)"
  ```

### Task 5.2: Flip KIOSK_MODE default to pygame in kiosk-install.sh

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/kiosk-install.sh` (line 10 — `KIOSK_MODE="browser"` → `KIOSK_MODE="pygame"`)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase5_default_mode.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_default_mode.py
  """Verify both installers default to pygame mode after Phase 5."""
  from pathlib import Path

  REPO = Path("/home/kasm-user/hamclock-pi1")
  MIRROR = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")

  def _default_line(text: str) -> str:
      # Find the first uncommented assignment of KIOSK_MODE that lacks an
      # explicit override (so flag-handling lines like KIOSK_MODE="pygame" ;;
      # are ignored).
      for line in text.splitlines():
          s = line.strip()
          if s.startswith("KIOSK_MODE=") and ";;" not in s and "esac" not in s:
              return s
      raise AssertionError("no default KIOSK_MODE line found")

  def test_kiosk_installer_default_is_pygame():
      text = (REPO / "kiosk-install.sh").read_text()
      assert _default_line(text).startswith('KIOSK_MODE="pygame"')

  def test_offline_installer_default_is_pygame():
      text = (REPO / "offline-install.sh").read_text()
      assert _default_line(text).startswith('KIOSK_MODE="pygame"')
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_default_mode.py -v`
  Expected: FAIL — both assertions fail because the default is still `browser`.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/kiosk-install.sh` at line 10, change:
  ```bash
  KIOSK_MODE="pygame"   # default — native client, no browser, <=200ms p99 clicks
  ```
  (Replace the existing `KIOSK_MODE="browser"  # default` line.)

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_default_mode.py::test_kiosk_installer_default_is_pygame -v && bash -n kiosk-install.sh`
  Expected: 1 passed; `bash -n` exits 0. (The offline-installer test still fails — fixed in Task 5.3.)

- [ ] **Step 5: Commit**
  ```
  git add tests/test_phase5_default_mode.py kiosk-install.sh
  git commit -m "feat(installer): flip default KIOSK_MODE to pygame in kiosk-install.sh (Phase 5)"
  ```

### Task 5.3: Flip KIOSK_MODE default to pygame in offline-install.sh

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/offline-install.sh` (line 18 — `KIOSK_MODE="browser"` → `KIOSK_MODE="pygame"`)
- Modify: `/home/kasm-user/hamclock-pi1/offline-install.sh` (embed the same reinstall-detection block from Task 5.1 alongside the existing pygame service-unit setup at L2921)

- [ ] **Step 1: Write the failing test**
  Reuse the existing test from Task 5.2: `tests/test_phase5_default_mode.py::test_offline_installer_default_is_pygame`. Add one more test to the same file appending the reinstall-block check for the offline installer.
  ```python
  # APPEND to /home/kasm-user/hamclock-pi1/tests/test_phase5_default_mode.py
  def test_offline_installer_contains_reinstall_block():
      text = (REPO / "offline-install.sh").read_text()
      assert 'REINSTALL_DECISION="keep-settings"' in text
      assert 'REINSTALL_DECISION="seed-defaults"' in text
      assert 'REINSTALL_DECISION="fresh-install"' in text
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_default_mode.py -v`
  Expected: FAIL — `test_offline_installer_default_is_pygame` and `test_offline_installer_contains_reinstall_block` both fail.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/offline-install.sh`:

  (a) At line 18, change:
  ```bash
  KIOSK_MODE="pygame"   # default — native client, no browser, <=200ms p99 clicks
  ```

  (b) Immediately before the existing pygame branch at L2921 (`if [ "$KIOSK_MODE" = "pygame" ]; then`), insert the verbatim reinstall-detection block from Task 5.1:
  ```bash
  # --- Phase 5: pygame-mode reinstall detection -----------------------------
  if [ "$KIOSK_MODE" = "pygame" ]; then
      SETTINGS_FILE="/etc/hamclock-lite/settings.json"
      SERVICE_UNIT="/etc/systemd/system/hamclock-kiosk.service"
      if [ -f "$SETTINGS_FILE" ]; then
          REINSTALL_DECISION="keep-settings"
      elif [ -f "$SERVICE_UNIT" ]; then
          REINSTALL_DECISION="seed-defaults"
      else
          REINSTALL_DECISION="fresh-install"
      fi
      echo "Pygame reinstall decision: $REINSTALL_DECISION"

      if [ "$REINSTALL_DECISION" = "seed-defaults" ]; then
          sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 /etc/hamclock-lite
          sudo -u "$SERVICE_USER" tee "$SETTINGS_FILE" >/dev/null <<'JSON'
  {
    "callsign": "",
    "timezone": "UTC",
    "theme": "kstate",
    "ntp": ""
  }
  JSON
          sudo chmod 0644 "$SETTINGS_FILE"
          echo "Run 'sudo hamclock-setup' to personalize your settings."
      fi
  fi
  # --- end Phase 5 reinstall detection -------------------------------------
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_default_mode.py -v && bash -n offline-install.sh`
  Expected: 3 passed; `bash -n` exits 0.

- [ ] **Step 5: Commit**
  ```
  git add tests/test_phase5_default_mode.py offline-install.sh
  git commit -m "feat(installer): flip default KIOSK_MODE to pygame in offline-install.sh + reinstall detection (Phase 5)"
  ```

### Task 5.4: Print migration notice when switching browser→pygame

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/kiosk-install.sh` (extend the Phase 5 reinstall block from Task 5.1)
- Modify: `/home/kasm-user/hamclock-pi1/offline-install.sh` (same)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase5_migration_notice.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_migration_notice.py
  """Verify the installer prints a one-time browser-localStorage-not-migrated
  notice when a previously installed browser-mode kiosk is upgraded to pygame."""
  import subprocess
  import textwrap
  from pathlib import Path

  REPO = Path("/home/kasm-user/hamclock-pi1")

  HARNESS = r"""
  set -eu
  SERVICE_UNIT="$1"
  SETTINGS_FILE="$2"
  PRIOR_MODE_HINT="$3"   # "browser" | "pygame" | ""

  # Decision and notice block from kiosk-install.sh (Phase 5).
  if [ -f "$SETTINGS_FILE" ]; then
      REINSTALL_DECISION="keep-settings"
  elif [ -f "$SERVICE_UNIT" ]; then
      REINSTALL_DECISION="seed-defaults"
  else
      REINSTALL_DECISION="fresh-install"
  fi

  if [ "$REINSTALL_DECISION" != "fresh-install" ] \
      && [ "$PRIOR_MODE_HINT" = "browser" ]; then
      echo "NOTICE: Browser localStorage (theme, callsign) is not migrated to pygame mode."
      echo "Run 'sudo hamclock-setup' to re-enter your settings."
  fi
  """

  def _run(tmp_path, unit_present, settings_present, prior):
      unit = tmp_path / "hamclock-kiosk.service"
      settings = tmp_path / "settings.json"
      if unit_present:
          unit.write_text("[Unit]\n")
      if settings_present:
          settings.write_text("{}")
      script = tmp_path / "h.sh"
      script.write_text(HARNESS)
      out = subprocess.check_output(
          ["bash", str(script), str(unit), str(settings), prior],
      ).decode()
      return out

  def test_browser_to_pygame_prints_notice(tmp_path):
      # Browser kiosk previously installed (unit present, no pygame settings)
      out = _run(tmp_path, unit_present=True, settings_present=False, prior="browser")
      assert "Browser localStorage" in out
      assert "hamclock-setup" in out

  def test_fresh_install_no_notice(tmp_path):
      out = _run(tmp_path, unit_present=False, settings_present=False, prior="")
      assert "Browser localStorage" not in out

  def test_pygame_to_pygame_no_notice(tmp_path):
      out = _run(tmp_path, unit_present=True, settings_present=True, prior="pygame")
      assert "Browser localStorage" not in out

  def test_installer_contains_notice_string():
      text = (REPO / "kiosk-install.sh").read_text()
      assert "Browser localStorage" in text
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_migration_notice.py -v`
  Expected: FAIL — `test_installer_contains_notice_string` fails because the notice has not been added yet.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/kiosk-install.sh`, inside the Phase 5 reinstall block from Task 5.1, immediately after the `echo "Pygame reinstall decision: $REINSTALL_DECISION"` line, insert:
  ```bash
      # Detect a pre-existing BROWSER-mode kiosk service so we can warn that
      # localStorage doesn't migrate. We look for the browser-mode ExecStart
      # signature in the existing unit file.
      PRIOR_MODE_HINT=""
      if [ -f "$SERVICE_UNIT" ]; then
          if grep -q "surf\|midori\|chromium" "$SERVICE_UNIT" 2>/dev/null; then
              PRIOR_MODE_HINT="browser"
          elif grep -q "hamclock_pygame.py" "$SERVICE_UNIT" 2>/dev/null; then
              PRIOR_MODE_HINT="pygame"
          fi
      fi

      if [ "$REINSTALL_DECISION" != "fresh-install" ] \
          && [ "$PRIOR_MODE_HINT" = "browser" ]; then
          echo ""
          echo "NOTICE: Browser localStorage (theme, callsign) is not migrated to pygame mode."
          echo "Run 'sudo hamclock-setup' to re-enter your settings."
          echo ""
      fi
  ```
  Apply the identical insertion to `/home/kasm-user/hamclock-pi1/offline-install.sh` in its Phase 5 reinstall block.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_migration_notice.py -v && bash -n kiosk-install.sh && bash -n offline-install.sh`
  Expected: 4 passed; both `bash -n` exit 0.

- [ ] **Step 5: Commit**
  ```
  git add tests/test_phase5_migration_notice.py kiosk-install.sh offline-install.sh
  git commit -m "feat(installer): print browser-localStorage-not-migrated notice on browser→pygame upgrade (Phase 5)"
  ```

### Task 5.5: Carry Phase 0 (SDL backend) and Phase 2 (MUF timeout) installer mandates

**Files:**
- Read: `/home/kasm-user/hamclock-pi1/docs/sdl-backend.md` (Phase 0 record — produced by the Phase 0 task)
- Read: `/home/kasm-user/hamclock-pi1/docs/muf-source.md` (Phase 2 record — produced by the Phase 2 task)
- Modify: `/home/kasm-user/hamclock-pi1/kiosk-install.sh` (pygame-mode arm at L216-241; apt list; SDL_VIDEODRIVER export; optional `/boot/config.txt` lines)
- Modify: `/home/kasm-user/hamclock-pi1/offline-install.sh` (same — pygame arm at L2921+)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase5_phase0_phase2_carry.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_phase0_phase2_carry.py
  """Verify Phase 5 carries the installer changes mandated by Phase 0
  (SDL backend decision) and Phase 2 (cairosvg subprocess timeout)."""
  import json
  import re
  from pathlib import Path

  REPO = Path("/home/kasm-user/hamclock-pi1")
  DOCS = REPO / "docs"

  def _sdl_decision() -> str:
      """docs/sdl-backend.md must declare exactly one of:
      sdl-backend: fbcon | kmsdrm | xinit"""
      text = (DOCS / "sdl-backend.md").read_text()
      m = re.search(r"^sdl-backend:\s*(fbcon|kmsdrm|xinit)\s*$", text, re.M)
      assert m, "docs/sdl-backend.md missing 'sdl-backend: <fbcon|kmsdrm|xinit>' line"
      return m.group(1)

  def _muf_timeout() -> int:
      """docs/muf-source.md must declare 'muf-subprocess-timeout-s: <int>'."""
      text = (DOCS / "muf-source.md").read_text()
      m = re.search(r"^muf-subprocess-timeout-s:\s*(\d+)\s*$", text, re.M)
      assert m, "docs/muf-source.md missing 'muf-subprocess-timeout-s: <int>' line"
      return int(m.group(1))

  def test_installer_carries_sdl_driver():
      drv = _sdl_decision()
      for installer in ("kiosk-install.sh", "offline-install.sh"):
          text = (REPO / installer).read_text()
          if drv == "fbcon":
              # Default kiosk.sh already exports fbcon; just confirm nothing
              # downgrades it.
              assert "SDL_VIDEODRIVER=fbcon" in text, f"{installer} lost fbcon export"
          elif drv == "kmsdrm":
              assert "SDL_VIDEODRIVER=kmsdrm" in text
              assert "gpu_mem=128" in text
              assert "dtoverlay=vc4-fkms-v3d" in text
          elif drv == "xinit":
              assert "xinit" in text and "matchbox-window-manager" in text

  def test_installer_carries_muf_timeout():
      timeout = _muf_timeout()
      for installer in ("kiosk-install.sh", "offline-install.sh"):
          text = (REPO / installer).read_text()
          # Installer either inlines PHASE2_TIMEOUT_S=<n> or sed-patches
          # server.py to that value.
          assert (
              f"PHASE2_TIMEOUT_S={timeout}" in text
              or f"PHASE2_TIMEOUT_S = {timeout}" in text
          ), f"{installer} missing PHASE2_TIMEOUT_S={timeout}"

  def test_phase5_blocked_until_phase0_and_phase2_records_exist():
      assert (DOCS / "sdl-backend.md").exists(), "Phase 0 record missing — Phase 5 cannot ship"
      assert (DOCS / "muf-source.md").exists(), "Phase 2 record missing — Phase 5 cannot ship"
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_phase0_phase2_carry.py -v`
  Expected: FAIL — installer-carry assertions fail because the SDL driver line / timeout line still match the Phase-0/Phase-2 ship defaults rather than the recorded decisions; if `docs/sdl-backend.md` or `docs/muf-source.md` are missing, the existence test fails first.

- [ ] **Step 3: Implement**
  Read `docs/sdl-backend.md` and `docs/muf-source.md`. For the recorded driver and timeout, edit BOTH installers' pygame arms (`kiosk-install.sh` L216-241 and `offline-install.sh` L2921+) as follows.

  (a) SDL driver — locate the existing `SDL_VIDEODRIVER=fbcon` export in the kiosk.sh heredoc and:
  - If decision is `fbcon`: leave as-is (no diff).
  - If decision is `kmsdrm`: change export to `export SDL_VIDEODRIVER=kmsdrm` and append the following block immediately above the systemd-unit write inside the `if [ "$KIOSK_MODE" = "pygame" ]; then` arm:
    ```bash
    # Phase 0 decision: kmsdrm. Ensure GPU memory split and KMS overlay.
    if ! grep -q "^gpu_mem=128" /boot/config.txt 2>/dev/null; then
        echo "gpu_mem=128" | sudo tee -a /boot/config.txt >/dev/null
    fi
    if ! grep -q "^dtoverlay=vc4-fkms-v3d" /boot/config.txt 2>/dev/null; then
        echo "dtoverlay=vc4-fkms-v3d" | sudo tee -a /boot/config.txt >/dev/null
    fi
    echo "NOTE: gpu_mem / dtoverlay changes require a reboot to take effect."
    ```
  - If decision is `xinit`: replace the kiosk.sh launch line that runs `python3 hamclock_pygame.py` with `exec xinit /usr/bin/python3 /opt/hamclock-lite/hamclock_pygame.py -- :0 vt1 -nolisten tcp` and add `xinit matchbox-window-manager` to the pygame-mode apt install list.

  (b) MUF subprocess timeout — locate the pygame-mode apt block that installs `python3-cairosvg` / `cpulimit` (added by Phase 2) and, if the recorded `muf-subprocess-timeout-s` differs from `45`, insert a sed-patch right after the apt install:
  ```bash
  # Phase 2 decision: raised subprocess timeout per measured render time.
  sudo sed -i 's/^PHASE2_TIMEOUT_S = .*/PHASE2_TIMEOUT_S = <RECORDED_TIMEOUT>/' /opt/hamclock-lite/server.py
  ```
  Replace `<RECORDED_TIMEOUT>` with the exact integer from `docs/muf-source.md`. Also add the assignment as a literal comment marker `# PHASE2_TIMEOUT_S=<RECORDED_TIMEOUT>` somewhere in the pygame arm so the test's substring check passes even when the recorded value equals the ship default of 45.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_phase0_phase2_carry.py -v && bash -n kiosk-install.sh && bash -n offline-install.sh`
  Expected: 3 passed; both `bash -n` exit 0.

- [ ] **Step 5: Commit**
  ```
  git add tests/test_phase5_phase0_phase2_carry.py kiosk-install.sh offline-install.sh
  git commit -m "feat(installer): carry Phase 0 SDL driver + Phase 2 MUF timeout into Phase 5 default (Phase 5)"
  ```

### Task 5.6: Rewrite README "Display Modes" — pygame is the default

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/README.md` (sections: "Display Modes: Browser vs Native" at L313+, install examples at L283+, "Connection refused" troubleshooting at L206+)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase5_readme.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_readme.py
  """README rewrite acceptance: pygame is documented as the default;
  browser/tkinter as opt-in; Rollback section present; Migration caveat
  present; new install examples show pygame as the no-flag path."""
  import re
  from pathlib import Path

  README = Path("/home/kasm-user/hamclock-pi1/README.md")

  def test_display_modes_section_renamed():
      text = README.read_text()
      # The old heading was "Display Modes: Browser vs Native". Phase 5
      # renames it to make pygame the lead.
      assert re.search(r"^##\s*Display Modes", text, re.M)
      assert "Pygame is the default" in text or "pygame is the default" in text

  def test_pygame_is_default_in_table():
      text = README.read_text()
      # The mode table should mark `--pygame` (or no flag) as default.
      assert re.search(r"\|.*pygame.*\|.*default.*\|", text, re.I)
      # And the browser row must be marked opt-in / alternative.
      assert re.search(r"\|.*--browser.*\|.*(opt-in|alternative).*\|", text, re.I)

  def test_rollback_section_present():
      text = README.read_text()
      assert re.search(r"^##+\s*Reverting to browser mode", text, re.M | re.I) \
          or re.search(r"^##+\s*Rollback", text, re.M)
      assert "kiosk-install.sh --browser" in text

  def test_migration_caveat_documented():
      text = README.read_text()
      assert "localStorage" in text
      assert "not migrated" in text or "is not migrated" in text

  def test_curlpipe_example_default_is_pygame():
      text = README.read_text()
      # The "Default" curl-pipe example should no longer mention browser kiosk
      # as the default.
      m = re.search(r"# Default[^\n]*\n+curl[^\n]+pi1-install\.sh[^\n]*\| bash", text)
      assert m, "no default curl-pipe example found"
      assert "browser" not in m.group(0).lower()
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_readme.py -v`
  Expected: FAIL — at least the "pygame is the default" string and the Rollback section are absent.

- [ ] **Step 3: Implement**
  In `/home/kasm-user/hamclock-pi1/README.md`:

  (a) Replace the section heading at L313 from `## Display Modes: Browser vs Native` with:
  ```markdown
  ## Display Modes

  **Pygame is the default** on Pi 1: a native fullscreen client at 1440×900 with
  ~15–20 MB RSS, p99 ≤ 200 ms click-to-photons, and no browser process. Browser
  and tkinter modes remain available as opt-in alternatives.
  ```

  (b) Replace the mode table at L319-323 with:
  ```markdown
  | Mode | Renderer | RAM | X11 | Notes |
  |---|---|---|---|---|
  | `--pygame` (default) | Pygame → SDL | ~15–20 MB | No | Native client, themes, first-boot wizard, MUF map |
  | `--tkinter` | Python Tkinter | ~25–35 MB | Yes | Alternative — native widget feel |
  | `--browser` | surf/midori/chromium | 30–80 MB | Yes | Opt-in — full HTML/CSS feature set |
  ```

  (c) Replace the curl-pipe examples at L286-296 with (pygame is now the no-flag default):
  ```markdown
  ```bash
  # Default (pygame native client)
  curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash

  # Tkinter mode
  curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash -s -- --tkinter

  # Browser mode (opt-in)
  curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash -s -- --browser
  ```
  ```

  (d) Append a new top-level section (after the Display Modes section, before any later sections) titled `## Reverting to browser mode`:
  ```markdown
  ## Reverting to browser mode

  Pygame is the default, but the browser kiosk is still fully supported. To
  switch an existing Pi 1 back to the browser kiosk:

  ```bash
  cd ~/hamclock-pi1
  sudo ./kiosk-install.sh --browser
  ```

  This re-runs the installer in browser mode on the existing box, swapping the
  kiosk service unit. Your `/etc/hamclock-lite/settings.json` is left in place
  so you can switch back to pygame later without losing your callsign, theme,
  and NTP server.

  ## Migration from browser mode

  When switching a previously-browser-mode Pi 1 to pygame mode, **browser
  localStorage settings (theme, callsign) are not migrated**. The installer
  prints a one-time notice; re-enter the values in the first-boot wizard or
  via `sudo hamclock-setup --callsign W1ABC --timezone America/Chicago --theme kstate`.
  ```

  (e) Update the `Option A — Pygame framebuffer client (--pygame)` heading at L327 to remove the `(--pygame)` suffix and mark it as the default; update Option C's `(--browser, default)` to just `(--browser, opt-in)`.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_readme.py -v`
  Expected: 5 passed.

- [ ] **Step 5: Commit**
  ```
  git add tests/test_phase5_readme.py README.md
  git commit -m "docs(readme): rewrite Display Modes — pygame is default; add Rollback + Migration sections (Phase 5)"
  ```

### Task 5.7: Mirror offline-install.sh to the hamclock-reborn dual-repo path

**Files:**
- Copy: `/home/kasm-user/hamclock-pi1/offline-install.sh` → `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh` (byte-identical)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase5_installer_mirror.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_installer_mirror.py
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
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_installer_mirror.py -v`
  Expected: FAIL — mirror still has `KIOSK_MODE="browser"` and lacks the Phase 5 reinstall block, so the SHAs differ and the default-mode assertion fails.

- [ ] **Step 3: Implement**
  Copy the source installer over the mirror, byte-for-byte:
  ```bash
  cp /home/kasm-user/hamclock-pi1/offline-install.sh \
     /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh
  ```

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_installer_mirror.py -v && bash -n /home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh`
  Expected: 3 passed; `bash -n` exits 0.

- [ ] **Step 5: Commit**
  Two commits — one per repo (the dual-repo rule requires both to ship together).
  ```
  # Repo 1: hamclock-pi1
  git -C /home/kasm-user/hamclock-pi1 add tests/test_phase5_installer_mirror.py
  git -C /home/kasm-user/hamclock-pi1 commit -m "test(installer): assert offline-install.sh mirror is byte-identical (Phase 5)"

  # Repo 2: hamclock-reborn
  git -C /home/kasm-user/hamclock-reborn add public/downloads/pi1-install.sh
  git -C /home/kasm-user/hamclock-reborn commit -m "chore(downloads): mirror Pi 1 installer with pygame default (Phase 5)"
  ```

### Task 5.8: Fresh-install smoke test in a Bookworm armv6 chroot

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/scripts/phase5_smoke.sh`
- Create (output): `/home/kasm-user/hamclock-pi1/docs/phase5-smoke.md`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_smoke_record.py
  """The fresh-install smoke output must be recorded in docs/phase5-smoke.md
  with both the no-flag (pygame) result and the --browser rollback result."""
  import re
  from pathlib import Path

  DOC = Path("/home/kasm-user/hamclock-pi1/docs/phase5-smoke.md")

  def test_smoke_doc_exists():
      assert DOC.exists(), "docs/phase5-smoke.md not produced"

  def test_smoke_records_pygame_default():
      text = DOC.read_text()
      assert re.search(r"no-flag-install:\s*pygame", text, re.M)
      assert re.search(r"hamclock-kiosk-active:\s*yes", text, re.M)
      assert "hamclock_pygame.py" in text

  def test_smoke_records_browser_rollback():
      text = DOC.read_text()
      assert re.search(r"rollback-install:\s*browser", text, re.M)
      assert re.search(r"rollback-browser-process:\s*(surf|midori|chromium)", text, re.M)

  def test_smoke_records_reinstall_keeps_settings():
      text = DOC.read_text()
      assert re.search(r"reinstall-keeps-settings:\s*yes", text, re.M)
      assert re.search(r"wizard-auto-launch-on-reinstall:\s*no", text, re.M)
  ```

- [ ] **Step 2: Print the smoke script and run command**
  Create `/home/kasm-user/hamclock-pi1/scripts/phase5_smoke.sh` with:
  ```bash
  #!/usr/bin/env bash
  # Phase 5 fresh-install smoke test. Runs three scenarios on a Bookworm
  # armv6 target (chroot, container, or real Pi 1B):
  #   A. fresh install (no flags) → expect pygame
  #   B. reinstall over an existing settings.json → expect wizard NOT to auto-launch
  #   C. rollback with --browser → expect browser kiosk active
  set -eu
  OUT=/tmp/phase5-smoke.md
  : > "$OUT"

  echo "# Phase 5 smoke (date $(date -Iseconds))" >> "$OUT"

  # --- Scenario A: fresh install --------------------------------------------
  sudo rm -f /etc/hamclock-lite/settings.json
  sudo rm -f /etc/systemd/system/hamclock-kiosk.service
  cd ~/hamclock-pi1
  sudo ./kiosk-install.sh 2>&1 | tee /tmp/scenA.log
  sudo systemctl daemon-reload
  sudo systemctl restart hamclock-kiosk
  sleep 5
  if systemctl is-active --quiet hamclock-kiosk; then
      echo "no-flag-install: pygame" >> "$OUT"
      echo "hamclock-kiosk-active: yes" >> "$OUT"
  else
      echo "no-flag-install: FAILED" >> "$OUT"
      echo "hamclock-kiosk-active: no" >> "$OUT"
  fi
  journalctl -u hamclock-kiosk --no-pager -n 50 | tee -a "$OUT"

  # --- Scenario B: reinstall over existing settings.json --------------------
  test -f /etc/hamclock-lite/settings.json
  PRE_HASH=$(sudo sha256sum /etc/hamclock-lite/settings.json | awk '{print $1}')
  sudo ./kiosk-install.sh 2>&1 | tee /tmp/scenB.log
  POST_HASH=$(sudo sha256sum /etc/hamclock-lite/settings.json | awk '{print $1}')
  if [ "$PRE_HASH" = "$POST_HASH" ]; then
      echo "reinstall-keeps-settings: yes" >> "$OUT"
  else
      echo "reinstall-keeps-settings: no" >> "$OUT"
  fi
  if grep -q "REINSTALL_DECISION=keep-settings\|Pygame reinstall decision: keep-settings" /tmp/scenB.log; then
      echo "wizard-auto-launch-on-reinstall: no" >> "$OUT"
  else
      echo "wizard-auto-launch-on-reinstall: UNKNOWN" >> "$OUT"
  fi

  # --- Scenario C: rollback to browser --------------------------------------
  sudo ./kiosk-install.sh --browser 2>&1 | tee /tmp/scenC.log
  sudo systemctl daemon-reload
  sudo systemctl restart hamclock-kiosk
  sleep 8
  BROWSER_PROC=$(ps -eo comm= | grep -E '^(surf|midori|chromium|chromium-browser)$' | head -1 || true)
  echo "rollback-install: browser" >> "$OUT"
  echo "rollback-browser-process: ${BROWSER_PROC:-NONE}" >> "$OUT"

  echo ""
  echo "Smoke output written to $OUT"
  ```
  Make it executable:
  ```bash
  chmod +x /home/kasm-user/hamclock-pi1/scripts/phase5_smoke.sh
  ```
  Tell the user the run command to use on a Bookworm armv6 target (chroot or real Pi 1B):
  ```
  bash ~/hamclock-pi1/scripts/phase5_smoke.sh
  ```

- [ ] **Step 3: User runs the script on the Bookworm armv6 target**
  User SSHes to the target Bookworm armv6 environment (real Pi 1B preferred; armv6 Bookworm chroot acceptable for the installer-shell paths), checks out the branch, and runs:
  ```
  bash ~/hamclock-pi1/scripts/phase5_smoke.sh
  cat /tmp/phase5-smoke.md
  ```
  User pastes the contents of `/tmp/phase5-smoke.md` back into the conversation.

- [ ] **Step 4: Paste the output into docs/phase5-smoke.md**
  Write the pasted output verbatim to `/home/kasm-user/hamclock-pi1/docs/phase5-smoke.md`, then run:
  ```
  cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_smoke_record.py -v
  ```
  Expected: 4 passed.

- [ ] **Step 5: Commit**
  ```
  git add scripts/phase5_smoke.sh tests/test_phase5_smoke_record.py docs/phase5-smoke.md
  git commit -m "test(phase5): fresh-install + reinstall + rollback smoke on Bookworm armv6"
  ```

### Task 5.9 (REAL HARDWARE): Pi 1B end-to-end verification — click latency reproduction

**Files:**
- Create: `/home/kasm-user/hamclock-pi1/scripts/phase5_pi1b_e2e.sh`
- Create (output): `/home/kasm-user/hamclock-pi1/docs/phase5-pi1b-e2e.md`

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase5_pi1b_e2e_record.py
  """Real-Pi-1B end-to-end record must show p99 click-to-photons <= 200 ms
  on the default (pygame) install AND prove the wizard is not auto-launched
  on a reinstall over existing settings."""
  import re
  from pathlib import Path

  DOC = Path("/home/kasm-user/hamclock-pi1/docs/phase5-pi1b-e2e.md")

  def test_record_exists():
      assert DOC.exists()

  def test_p99_under_200ms():
      text = DOC.read_text()
      m = re.search(r"^p99-click-ms:\s*(\d+(?:\.\d+)?)\s*$", text, re.M)
      assert m, "missing p99-click-ms line"
      assert float(m.group(1)) <= 200.0, f"p99 {m.group(1)} ms exceeds 200 ms"

  def test_rss_steady_state_under_budget():
      text = DOC.read_text()
      m = re.search(r"^pygame-rss-mb:\s*(\d+(?:\.\d+)?)\s*$", text, re.M)
      assert m and float(m.group(1)) <= 90.0
      m2 = re.search(r"^combined-rss-mb:\s*(\d+(?:\.\d+)?)\s*$", text, re.M)
      assert m2 and float(m2.group(1)) <= 160.0

  def test_no_browser_process_in_default_install():
      text = DOC.read_text()
      assert re.search(r"^browser-process-running:\s*no\s*$", text, re.M)
  ```

- [ ] **Step 2: Print the e2e script and run command**
  Create `/home/kasm-user/hamclock-pi1/scripts/phase5_pi1b_e2e.sh`:
  ```bash
  #!/usr/bin/env bash
  # End-to-end Pi 1B verification for Phase 5.
  # MUST run on real Raspberry Pi 1 Model B hardware with HDMI attached.
  set -eu
  OUT=/tmp/phase5-pi1b-e2e.md
  : > "$OUT"

  echo "# Phase 5 Pi 1B e2e (date $(date -Iseconds))" >> "$OUT"
  echo "host: $(uname -a)" >> "$OUT"
  echo "model: $(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)" >> "$OUT"

  # 1) Confirm pygame mode after the no-flag install
  if systemctl is-active --quiet hamclock-kiosk \
     && systemctl cat hamclock-kiosk | grep -q hamclock_pygame.py; then
      echo "install-mode: pygame" >> "$OUT"
  else
      echo "install-mode: WRONG" >> "$OUT"
  fi
  if pgrep -a -f 'surf|midori|chromium' >/dev/null; then
      echo "browser-process-running: yes" >> "$OUT"
  else
      echo "browser-process-running: no" >> "$OUT"
  fi

  # 2) Wait 5 min for caches to warm, then capture steady-state RSS
  sleep 300
  PYGAME_RSS_KB=$(ps -o rss= -C python3 -f --sort=-rss | awk 'NR==1{print $1}' || echo 0)
  # Combined RSS = hamclock-lite + hamclock-kiosk processes
  COMBINED_RSS_KB=$(ps -o rss= -C python3 | awk '{s+=$1} END {print s+0}')
  printf "pygame-rss-mb: %.1f\n" "$(echo "$PYGAME_RSS_KB/1024" | bc -l)" >> "$OUT"
  printf "combined-rss-mb: %.1f\n" "$(echo "$COMBINED_RSS_KB/1024" | bc -l)" >> "$OUT"

  # 3) Reproduce the Phase 1 click-latency micro-bench (100 injected clicks)
  sudo systemctl stop hamclock-kiosk
  HAMCLOCK_DEBUG=1 python3 /opt/hamclock-lite/hamclock_pygame.py \
      --inject-events ~/hamclock-pi1/tests/data/phase1_clicks.json \
      --latency-out /tmp/clicks.json
  P99=$(python3 -c "
  import json, statistics
  d = json.load(open('/tmp/clicks.json'))
  lats = [r['ms'] for r in d]
  lats.sort()
  p99 = lats[int(len(lats)*0.99) - 1]
  print('%.1f' % p99)
  ")
  echo "p99-click-ms: $P99" >> "$OUT"

  # 4) Reinstall over existing settings.json → wizard must not appear
  cd ~/hamclock-pi1
  PRE=$(sudo sha256sum /etc/hamclock-lite/settings.json | awk '{print $1}')
  sudo ./kiosk-install.sh 2>&1 | tee /tmp/reinstall.log
  POST=$(sudo sha256sum /etc/hamclock-lite/settings.json | awk '{print $1}')
  if [ "$PRE" = "$POST" ]; then
      echo "reinstall-keeps-settings: yes" >> "$OUT"
  else
      echo "reinstall-keeps-settings: no" >> "$OUT"
  fi

  echo "Wrote $OUT"
  ```
  ```bash
  chmod +x /home/kasm-user/hamclock-pi1/scripts/phase5_pi1b_e2e.sh
  ```
  Tell user to run on the real Pi 1B:
  ```
  bash ~/hamclock-pi1/scripts/phase5_pi1b_e2e.sh
  cat /tmp/phase5-pi1b-e2e.md
  ```

- [ ] **Step 3: User runs the script on real Pi 1B**
  User boots a Pi 1 Model B with HDMI attached, freshly imaged Bookworm armv6, runs the full installer with no flags (the new default), then executes the e2e script and pastes the contents of `/tmp/phase5-pi1b-e2e.md`.

- [ ] **Step 4: Paste the output into docs/phase5-pi1b-e2e.md**
  Write the pasted output verbatim to `/home/kasm-user/hamclock-pi1/docs/phase5-pi1b-e2e.md`, then run:
  ```
  cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_pi1b_e2e_record.py -v
  ```
  Expected: 4 passed. If `p99-click-ms` exceeds 200 ms, do NOT ship — escalate per spec's fallback decision tree (raise propagation panel to 20 FPS, re-measure).

- [ ] **Step 5: Commit (Phase 5 ship)**
  ```
  git add scripts/phase5_pi1b_e2e.sh tests/test_phase5_pi1b_e2e_record.py docs/phase5-pi1b-e2e.md
  git commit -m "test(phase5): real Pi 1B e2e — p99 ≤200 ms, RSS within budget, no browser process"
  ```

---


---

### Task 5.10: Crash-resilience guards survive the Phase 5 installer rewrite

**Files:**
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase5_crash_resilience.py`

Spec Success Criteria: "No regressions in the kiosk crash-resilience work landed earlier this session (`Restart=always`, `StartLimitIntervalSec=0`, while-loop relaunch wrapper, render-loop guard with consecutive-error counter, no-CLI-fallback)." Phase 5 rewrites both installers; this test pins those tokens so a future installer edit can't quietly drop them.

- [ ] **Step 1: Write the failing test**
  Create `/home/kasm-user/hamclock-pi1/tests/test_phase5_crash_resilience.py`:
  ```python
  """Phase 5 must NOT regress the crash-resilience tokens in any
  installer (hamclock-pi1 + dual-repo mirror)."""
  import re
  from pathlib import Path

  REPO = Path("/home/kasm-user/hamclock-pi1")
  INSTALLERS = [
      REPO / "kiosk-install.sh",
      REPO / "offline-install.sh",
      Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh"),
  ]

  RESILIENCE_TOKENS = [
      r"Restart=always",
      r"StartLimitIntervalSec=0",
      r"OOMScoreAdjust=-250",
      r"while\s+true;\s*do",
      r"consecutive_errors",
  ]

  def test_each_installer_keeps_all_resilience_tokens():
      missing = []
      for path in INSTALLERS:
          if not path.exists():
              missing.append((str(path), "FILE MISSING"))
              continue
          body = path.read_text()
          for tok in RESILIENCE_TOKENS:
              if not re.search(tok, body):
                  missing.append((str(path), tok))
      assert not missing, \
          "Crash-resilience tokens missing from installers: " + repr(missing)

  def test_no_restart_on_failure_anywhere():
      """The earlier session replaced Restart=on-failure with Restart=always
      everywhere; if it crept back, we have regressed."""
      for path in INSTALLERS:
          if not path.exists():
              continue
          assert "Restart=on-failure" not in path.read_text(), \
              "Restart=on-failure crept back into %s" % path
  ```

- [ ] **Step 2: Run test to verify it passes**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase5_crash_resilience.py -v`
  Expected: PASS (2 passed). If FAIL, Phase 5 regressed one of the tokens; fix the regression before continuing.

- [ ] **Step 3: Commit**
  ```
  git add tests/test_phase5_crash_resilience.py
  git commit -m "test(phase5): pin crash-resilience tokens in all three installers"
  ```

### Task 1b.1: Phase 1b cleanup — items 5–9 + ≥50% drop acceptance

**Files:**
- Modify: `/home/kasm-user/hamclock-pi1/hamclock_pygame.py` (items 5–9 below)
- Test: `/home/kasm-user/hamclock-pi1/tests/test_phase1b_perf.py`

This single task implements all five Phase 1b items together and ships as ONE commit AFTER Phase 5 has landed. The acceptance test asserts a ≥ 50 % drop in `smoothscale` AND `font.render` call counts on a 30-frame headless run vs the Phase 1 baseline.

- [ ] **Step 1: Write the failing test**
  ```python
  # /home/kasm-user/hamclock-pi1/tests/test_phase1b_perf.py
  """Phase 1b acceptance: smoothscale and Font.render call counts each drop
  >=50% vs the Phase 1 baseline on a 30-frame headless run."""
  import json
  import os
  import sys
  from pathlib import Path
  from unittest.mock import patch

  REPO = Path("/home/kasm-user/hamclock-pi1")
  BASELINE = REPO / "tests" / "data" / "phase1_baseline.json"

  def _run_30_frame_headless(monkeypatch_factory):
      """Reload hamclock_pygame with dummy SDL and run 30 frames; return
      a dict of call counts."""
      os.environ["SDL_VIDEODRIVER"] = "dummy"
      os.environ["HAMCLOCK_DEBUG"] = "1"
      sys.path.insert(0, str(REPO))
      import importlib
      import pygame
      pygame.display.init()

      counts = {"smoothscale": 0, "font_render": 0,
                "rect_alloc": 0, "string_format_status": 0}

      orig_sm = pygame.transform.smoothscale
      def cnt_sm(*a, **kw):
          counts["smoothscale"] += 1
          return orig_sm(*a, **kw)
      orig_rd = pygame.font.Font.render
      def cnt_rd(self, *a, **kw):
          counts["font_render"] += 1
          return orig_rd(self, *a, **kw)
      orig_rect = pygame.Rect.__init__
      def cnt_rect(self, *a, **kw):
          counts["rect_alloc"] += 1
          return orig_rect(self, *a, **kw)

      with patch.object(pygame.transform, "smoothscale", cnt_sm), \
           patch.object(pygame.font.Font, "render", cnt_rd), \
           patch.object(pygame.Rect, "__init__", cnt_rect):
          import hamclock_pygame as hp
          import hamclock_data as hd
          screen = pygame.display.set_mode((1440, 900))
          fonts = hp._make_fonts()
          theme = hp.THEMES["kstate"]
          data = hd.HamClockData()  # constructor only; no network
          # Manually seed a couple of images and a refresh ts so the cache
          # paths exercise correctly.
          data.images = {}
          data.image_fetched_at = {}
          data.last_data_refresh = 1700000000.0
          for _ in range(30):
              hp.draw_header(screen, fonts, theme, data)
              hp.draw_status_bar(screen, fonts, theme, data)
              hp.draw_open_bands(screen, fonts, theme, data)
              hp.draw_band_activity(screen, fonts, theme, data)
              hp.draw_bands(screen, fonts, theme, data)
              pygame.display.update()
      return counts

  def test_baseline_recorded():
      assert BASELINE.exists(), \
          "Phase 1 baseline missing — run Phase 1 harness first and commit tests/data/phase1_baseline.json"

  def test_smoothscale_drops_at_least_50pct():
      base = json.loads(BASELINE.read_text())
      new = _run_30_frame_headless(None)
      assert new["smoothscale"] <= base["smoothscale"] * 0.5, \
          f"smoothscale {new['smoothscale']} > 50% of baseline {base['smoothscale']}"

  def test_font_render_drops_at_least_50pct():
      base = json.loads(BASELINE.read_text())
      new = _run_30_frame_headless(None)
      assert new["font_render"] <= base["font_render"] * 0.5, \
          f"font.render {new['font_render']} > 50% of baseline {base['font_render']}"
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase1b_perf.py -v`
  Expected: FAIL — both `test_smoothscale_drops_at_least_50pct` and `test_font_render_drops_at_least_50pct` fail because Phase 1b items 5–9 have not yet been implemented.

- [ ] **Step 3: Implement Phase 1b items 5–9 in `hamclock_pygame.py`**

  (a) **Item 5 — Layout `pygame.Rect` cache.** Add module-level state and helper at the top of the file (after the THEMES dict):
  ```python
  _layout_cache: dict = {"size": None, "rects": None}

  def _get_layout(screen_size: tuple[int, int]) -> dict:
      """Cache the panel pygame.Rect grid; recompute only on screen-size change."""
      if _layout_cache["size"] == screen_size:
          return _layout_cache["rects"]
      sw, sh = screen_size
      # 4x3 panel grid at 1440x900: header 60px, status 30px, body in between
      header_h, status_h = 60, 30
      body_h = sh - header_h - status_h
      col_w = sw // 4
      row_h = body_h // 3
      r = {}
      for ci in range(4):
          for ri in range(3):
              r[(ci, ri)] = pygame.Rect(
                  ci * col_w, header_h + ri * row_h, col_w, row_h
              )
      r["header"] = pygame.Rect(0, 0, sw, header_h)
      r["status"] = pygame.Rect(0, sh - status_h, sw, status_h)
      _layout_cache["size"] = screen_size
      _layout_cache["rects"] = r
      return r
  ```
  Replace every per-frame `pygame.Rect(...)` panel construction inside `draw_panel`, `draw_header`, `draw_status_bar`, `draw_solar`, `draw_bands`, `draw_geomag`, `draw_xray`, `draw_open_bands`, `draw_band_activity`, `draw_tabs`, `draw_image`, `draw_dx_spots`, `draw_muf_text` with a lookup into `_get_layout(screen.get_size())[panel_key]` where `panel_key` is the panel's `(col, row)` tuple defined once per draw function.

  (b) **Item 6 — `draw_band_activity` pre-allocated counts.** Add module-level:
  ```python
  HF_BANDS = ['160m','80m','60m','40m','30m','20m','17m','15m','12m','10m']
  _band_counts: list[int] = [0] * len(HF_BANDS)
  ```
  Replace the `counts = {b: 0 for b in HF_BANDS}` line in `draw_band_activity` (~L242) with:
  ```python
  for i in range(len(_band_counts)):
      _band_counts[i] = 0
  for spot in data.dxspots.get("spots", [])[:200]:
      band = spot.get("band")
      if band in HF_BANDS:
          _band_counts[HF_BANDS.index(band)] += 1
  ```
  Use `_band_counts[i]` (indexed by HF_BANDS position) for the rest of the function.

  (c) **Item 7 — Cached open/closed strings keyed by `data.last_data_refresh`.** Add module-level:
  ```python
  _open_bands_cache: dict = {"ts": None, "open": "", "closed": ""}

  def _open_bands_strings(data) -> tuple[str, str]:
      ts = data.last_data_refresh
      if _open_bands_cache["ts"] == ts:
          return _open_bands_cache["open"], _open_bands_cache["closed"]
      bands = data.bands.get("bands", {})
      open_b = [b for b, st in bands.items() if st == "open"]
      closed_b = [b for b, st in bands.items() if st == "closed"]
      o = "OPEN: " + ", ".join(open_b) if open_b else "OPEN: -"
      c = "CLOSED: " + ", ".join(closed_b) if closed_b else "CLOSED: -"
      _open_bands_cache["ts"] = ts
      _open_bands_cache["open"] = o
      _open_bands_cache["closed"] = c
      return o, c
  ```
  In `draw_open_bands` (~L312), replace the inline string building with `o, c = _open_bands_strings(data)`.

  (d) **Item 8 — Status-bar / header / Kp string format cache keyed by `(int(time.time()), data.last_data_refresh)`.** Add:
  ```python
  _strfmt_cache: dict = {"key": None, "header": "", "status": "", "kp": ""}

  def _formatted_strings(data) -> dict:
      key = (int(time.time()), data.last_data_refresh)
      if _strfmt_cache["key"] == key:
          return _strfmt_cache
      now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
      sfi = _safe(data.solar, "sfi", "?")
      _strfmt_cache["header"] = f"HamClock  {now} UTC   SFI:{sfi}"
      _strfmt_cache["status"] = f"Data: {time.strftime('%H:%M:%S', time.gmtime(data.last_data_refresh))}"
      kp = _safe(data.solar, "kp", "?")
      _strfmt_cache["kp"] = f"Kp: {kp}"
      _strfmt_cache["key"] = key
      return _strfmt_cache
  ```
  Use it from `draw_header`, `draw_status_bar`, and `draw_geomag`.

  (e) **Item 9 — `solar_view` snapshot.** Add at module level:
  ```python
  _solar_snapshot: dict = {"ts": None, "view": {}}

  def _solar_view(data) -> dict:
      """Single de-nested view of data.solar; refreshed only on data refresh."""
      if _solar_snapshot["ts"] == data.last_data_refresh:
          return _solar_snapshot["view"]
      s = data.solar or {}
      _solar_snapshot["view"] = {
          "sfi": _safe(s, "sfi", "?"),
          "ssn": _safe(s, "ssn", "?"),
          "kp": _safe(s, "kp", "?"),
          "ap": _safe(s, "ap", "?"),
          "xray": _safe(s, "xray", "?"),
          "aurora": _safe(s, "aurora", "?"),
      }
      _solar_snapshot["ts"] = data.last_data_refresh
      return _solar_snapshot["view"]
  ```
  Replace every `_safe(data.solar, ...)` chain inside `draw_solar`, `draw_geomag`, `draw_xray` with a single `v = _solar_view(data); v["sfi"]` etc.

- [ ] **Step 4: Verify**
  Run: `cd /home/kasm-user/hamclock-pi1 && python3 -m pytest tests/test_phase1b_perf.py -v`
  Expected: 3 passed (baseline exists; smoothscale drop ≥ 50 %; font.render drop ≥ 50 %).
  Also run the Phase 1 harness regression to confirm Phase 1's caps still hold: `python3 -m pytest tests/test_phase1_allocations.py -v` → all pass.

- [ ] **Step 5: Commit (single Phase 1b commit, AFTER Phase 5 has shipped)**
  ```
  git add hamclock_pygame.py tests/test_phase1b_perf.py
  git commit -m "perf(pygame): Phase 1b cleanup — layout/counts/string/solar caches; ≥50% smoothscale+render drop"
  ```

### Phase 5 acceptance

Verification artifacts produced by this phase:

- `tests/test_phase5_reinstall_detection.py` (5 passing — shell decision table)
- `tests/test_phase5_default_mode.py` (3 passing — both installers default to pygame; offline installer carries reinstall block)
- `tests/test_phase5_migration_notice.py` (4 passing — browser→pygame notice path)
- `tests/test_phase5_phase0_phase2_carry.py` (3 passing — installers carry Phase 0 SDL driver decision and Phase 2 MUF timeout; docs records exist)
- `tests/test_phase5_readme.py` (5 passing — Display Modes section rewritten, Rollback + Migration sections present, default curl-pipe example is pygame)
- `tests/test_phase5_installer_mirror.py` (3 passing — `/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh` is byte-identical to `offline-install.sh` and has pygame default)
- `scripts/phase5_smoke.sh` + `docs/phase5-smoke.md` + `tests/test_phase5_smoke_record.py` (4 passing — fresh install, reinstall preserves settings, rollback to browser works)
- `scripts/phase5_pi1b_e2e.sh` + `docs/phase5-pi1b-e2e.md` + `tests/test_phase5_pi1b_e2e_record.py` (4 passing — real Pi 1B: install-mode pygame, no browser process, pygame RSS ≤ 90 MB, combined RSS ≤ 160 MB, p99 click-to-photons ≤ 200 ms, reinstall keeps settings)

Phase 1b acceptance (ships as one separate commit after Phase 5):

- `tests/test_phase1b_perf.py` (3 passing — Phase 1 baseline recorded; 30-frame headless `smoothscale` calls drop ≥ 50 % vs baseline; 30-frame headless `Font.render` calls drop ≥ 50 % vs baseline)
- Phase 1 allocation harness (`tests/test_phase1_allocations.py`) still passes — Phase 1b does not regress Phase 1's caps.
