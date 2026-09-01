#!/bin/bash
# HamClock Pi1 — Full Installer (Kiosk + Server)
# Self-contained: all files are embedded, no external downloads needed.
#
# Usage (online — curl pipe is safe because main() only runs after full download):
#   curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash
#
# Usage (offline — copy to USB):
#   1. Copy this file to a USB drive
#   2. Plug USB into your Pi
#   3. Mount the USB: sudo mount /dev/sda1 /mnt
#   4. Run: bash /mnt/offline-install.sh
#   5. Unplug USB when done

main() {
set -euo pipefail

KIOSK_MODE="pygame"   # default — native client, no browser, <=200ms p99 clicks
PROBE_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --pygame)  KIOSK_MODE="pygame" ;;
        --tkinter) KIOSK_MODE="tkinter" ;;
        --browser) KIOSK_MODE="browser" ;;
        --probe)   PROBE_ONLY=1 ;;
        --help|-h) echo "Usage: curl ... | bash -s -- [--browser|--pygame|--tkinter] [--probe]"; exit 0 ;;
        *) echo "Unknown arg: $arg (try --help)"; exit 1 ;;
    esac
done

# --probe: produce the Phase 0/2 evidence docs (sdl-backend.md, muf-source.md)
# inline -- this branch is what runs when the user curl-pipe'd us and has no
# scripts/ directory. Writes to /tmp/pi1-evidence/ and exits without touching
# the system.
if [ "$PROBE_ONLY" = "1" ]; then
    OUT_DIR="/tmp/pi1-evidence"
    mkdir -p "$OUT_DIR"
    DATE_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # ---- SDL probe (mirrors scripts/probe_sdl_backends.sh) ----
    PROBE_LOG="$OUT_DIR/probe_sdl_backends.log"
    {
        echo "=== hamclock-pi1 SDL backend probe (inline) ==="
        echo "host:   $(uname -a)"
        echo "date:   $DATE_UTC"
        echo "python: $(python3 --version 2>&1)"
        echo "pygame: $(python3 -c 'import pygame; print(pygame.version.ver)' 2>&1)"
        echo "SDL:    $(python3 -c 'import pygame; print(pygame.get_sdl_version())' 2>&1)"
        echo
        echo "--- /etc/default/keyboard ---"
        if [ -r /etc/default/keyboard ]; then cat /etc/default/keyboard; else echo "(not readable)"; fi
        echo
        echo "--- /dev/dri ---"
        ls -la /dev/dri 2>&1 || echo "(no /dev/dri -- kmsdrm will not work)"
        echo
        python3 - <<'PYINLINE'
import os, time, pygame
for drv in ('fbcon', 'kmsdrm', 'x11', 'dummy'):
    os.environ['SDL_VIDEODRIVER'] = drv
    print('--- trying', drv, '---')
    try:
        pygame.display.init()
        scr = pygame.display.set_mode((1440, 900), pygame.FULLSCREEN)
        scr.fill((40, 20, 80)); pygame.display.flip(); time.sleep(1)
        print(drv, '-> OK driver=', pygame.display.get_driver())
    except Exception as e:
        print(drv, '-> FAIL', type(e).__name__, e)
    finally:
        try: pygame.display.quit()
        except Exception: pass
PYINLINE
        echo "=== probe complete ==="
    } 2>&1 | tee "$PROBE_LOG"

    CHOSEN=""
    if   grep -q '^fbcon -> OK'  "$PROBE_LOG"; then CHOSEN="fbcon"
    elif grep -q '^kmsdrm -> OK' "$PROBE_LOG"; then CHOSEN="kmsdrm"
    else CHOSEN="xinit"
    fi
    KEYBOARD_CONF=""
    [ -r /etc/default/keyboard ] && KEYBOARD_CONF="$(cat /etc/default/keyboard)"
    {
        echo "# Pi 1B SDL backend probe — offline-install.sh --probe output"
        echo
        echo "date: $DATE_UTC"
        echo
        echo "chosen backend: $CHOSEN"
        echo "sdl-backend: $CHOSEN"
        echo
        echo "## /etc/default/keyboard"
        echo '```'
        echo "${KEYBOARD_CONF:-(not readable)}"
        echo '```'
        echo
        echo "## Raw probe stdout"
        echo '```'
        cat "$PROBE_LOG"
        echo '```'
    } > "$OUT_DIR/sdl-backend.md"

    # ---- cairosvg perf benchmark ----
    if ! python3 -c 'import cairosvg' 2>/dev/null; then
        if [ -r /etc/os-release ] && grep -qi 'bookworm\|debian' /etc/os-release 2>/dev/null; then
            sudo apt install -y python3-cairosvg || true
        fi
    fi
    if ! python3 -c 'import cairosvg' 2>/dev/null; then
        echo "ERROR: python3-cairosvg not importable; cannot benchmark" >&2
        exit 1
    fi
    TIMES=()
    for i in 1 2 3 4 5; do
        start=$(date +%s.%N)
        python3 -c "import cairosvg; cairosvg.svg2png(url='https://prop.kc2g.com/renders/current/mufd-normal-now.svg', output_width=360, write_to='/tmp/m_$i.png')"
        end=$(date +%s.%N)
        elapsed=$(echo "$end - $start" | bc -l)
        TIMES+=("$elapsed")
        echo "  run $i: ${elapsed}s"
    done
    MEDIAN=$(printf '%s\n' "${TIMES[@]}" | sort -g | sed -n '3p')
    if (( $(echo "$MEDIAN <= 20" | bc -l) )); then
        DECISION="median ${MEDIAN}s <= 20s -> ship cairosvg, PHASE2_TIMEOUT_S=45"
        TIMEOUT_S=45; MUF_SOURCE_LINE="muf-source: kc2g-svg-cairosvg"
    elif (( $(echo "$MEDIAN <= 30" | bc -l) )); then
        THREEX=$(echo "$MEDIAN * 3" | bc -l)
        TIMEOUT_S=$(python3 -c "import math; print(int(math.ceil(float('$THREEX'))))")
        [ "$TIMEOUT_S" -lt 60 ] && TIMEOUT_S=60
        DECISION="median ${MEDIAN}s in (20,30]s -> ship cairosvg, PHASE2_TIMEOUT_S=$TIMEOUT_S"
        MUF_SOURCE_LINE="muf-source: kc2g-svg-cairosvg"
    else
        DECISION="median ${MEDIAN}s > 30s -> cairosvg too slow; use BOM World I-Map GIF"
        TIMEOUT_S=0; MUF_SOURCE_LINE="muf-source: bom-world-imap"
    fi
    {
        echo "# Pi 1B MUF source decision — offline-install.sh --probe output"
        echo
        echo "date: $DATE_UTC"
        echo
        echo "## Measurements (cairosvg rasterize of KC2G mufd-normal-now.svg, output_width=360)"
        i=1; for t in "${TIMES[@]}"; do echo "  run $i: ${t}s"; i=$((i+1)); done
        echo "median: ${MEDIAN}s"
        echo
        echo "## Decision"
        echo "$DECISION"
        echo
        echo "$MUF_SOURCE_LINE"
        echo "muf-subprocess-timeout-s: $TIMEOUT_S"
    } > "$OUT_DIR/muf-source.md"

    echo
    echo "Wrote $OUT_DIR/sdl-backend.md"
    echo "Wrote $OUT_DIR/muf-source.md"
    echo "Copy these into docs/ in a hamclock-pi1 clone to unblock Phase 5."
    exit 0
fi

echo "Kiosk mode: $KIOSK_MODE"

INSTALL_DIR="/opt/hamclock-lite"
SERVICE_USER="${SUDO_USER:-${USER:-root}}"

# ---- Phase 4: settings directory + hamclock-setup wrapper ----
sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 /etc/hamclock-lite

# Always seed a default settings.json so the wizard never auto-launches
# on first boot — a headless Pi 1B has no USB keyboard to drive it.
# User runs `sudo hamclock-setup --callsign W1ABC ...` later to personalize.
if [ ! -f /etc/hamclock-lite/settings.json ]; then
    sudo tee /etc/hamclock-lite/settings.json > /dev/null <<'SETTINGSEOF'
{
  "callsign": "N0CALL",
  "timezone": "UTC",
  "theme": "kstate",
  "ntp": ""
}
SETTINGSEOF
    sudo chown $SERVICE_USER:$SERVICE_USER /etc/hamclock-lite/settings.json
    sudo chmod 0644 /etc/hamclock-lite/settings.json
    echo "Wrote default /etc/hamclock-lite/settings.json (callsign=N0CALL)."
    echo "Run 'sudo hamclock-setup --callsign YOUR_CALL --timezone YOUR_TZ --theme kstate' to personalize."
fi

# Install the hamclock-setup wrapper.
sudo tee /usr/local/bin/hamclock-setup > /dev/null <<'HSEOF'
#!/bin/sh
# Thin wrapper around the pygame client's --setup-cli mode.
exec python3 /opt/hamclock-lite/hamclock_pygame.py --setup-cli "$@"
HSEOF
sudo chmod 0755 /usr/local/bin/hamclock-setup

echo "=== HamClock Pi1 — Full Installer ==="
echo "This will install HamClock with kiosk mode (fullscreen on monitor)."
echo "Estimated time: 15-30 minutes on Pi 1"
echo ""

# ── Step 1: Check Python 3 ──────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required. Run: sudo apt install python3"
    exit 1
fi

# ── Step 2: Check internet connectivity (needed for apt) ────────────
if ! ping -c 1 -W 3 google.com &>/dev/null && ! ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
    echo "ERROR: No internet connection detected."
    echo "Please connect the Pi to the internet and try again."
    exit 1
fi

# ── Step 3: Write embedded server.py ────────────────────────────────
echo "Writing server.py..."
sudo mkdir -p "$INSTALL_DIR"
sudo tee "$INSTALL_DIR/server.py" > /dev/null << 'SERVEREOF'
#!/usr/bin/env python3
"""HamClock Lite — Lightweight server for Raspberry Pi 1"""

import gzip
import json
import signal
import tempfile
import time
import threading
import subprocess
import re
from http.server import SimpleHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer as HTTPServer
except ImportError:
    from http.server import HTTPServer  # Python < 3.7 fallback
from urllib.request import urlopen, Request
# HTTPError as well as URLError: Tier 2.2 uses conditional GETs, and urllib
# raises HTTPError for a 304. Importing the NAME here matters — writing
# `except urllib.error.HTTPError` would raise NameError *while evaluating the
# except clause* (server.py has no `import urllib`), which escapes the
# trailing `except Exception` and kills background_fetcher on the first
# network blip.
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse, parse_qs
from xml.etree import ElementTree
import os
import sys

PORT = 8080
CACHE = {
    'solar': None,
    'bands': None,
    'dxspots': None,
    # Tier 1b perf: pre-encoded JSON bytes for the three hot polling endpoints.
    # Populated alongside the dict so /api/solar, /api/bands, /api/dxspots can
    # write straight to the socket without re-running json.dumps every ~60 s.
    'solar_bytes': None,
    'bands_bytes': None,
    'dxspots_bytes': None,
    'solar_image': None,
    'solar_updated': 0,
    'bands_updated': 0,
    'dx_updated': 0,
    'solar_image_updated': 0,
    'muf_image': None,
    'muf_image_png': None,
    'muf_image_updated': 0,
    # Stamped only when a rasterize actually SUCCEEDS, so the keep-last-good
    # logic in fetch_muf can age the PNG independently of the SVG fetch.
    'muf_image_png_updated': 0,
    'enlil_image': None,
    'enlil_image_updated': 0,
    'drap_image': None,
    'drap_image_updated': 0,
    'real_drap_image': None,
    'real_drap_image_updated': 0,
    'host_ntp': None,
}

UA = 'HamClockLite/1.0'

# Phase 2: raised by installer if pre-merge cairosvg benchmark > 20 s
# (see docs/muf-source.md for the recorded measurement).
PHASE2_TIMEOUT_S = 45

# Tier 2.4: PHASE2_TIMEOUT_S above is the FLOOR. A render that is merely slow
# (ARMv6 under cpulimit's 50 % duty cycle) used to be indistinguishable from a
# render that is hung, and the 45 s cap turned "slow" into "blank panel
# forever". The ceiling is deliberately below the 120 s fetch_dx cadence so a
# pathological render can never starve the DX spot refresh.
PHASE2_TIMEOUT_MAX_S = 110
# Budget = 4x the smoothed cost of the last successful render.
MUF_TIMEOUT_FACTOR = 4.0
MUF_EWMA_ALPHA = 0.3

# RAM ONLY — deliberately never persisted. It recalibrates inside one 15 min
# image cycle, and writing it to disk would make the rasterize tests
# non-idempotent across runs (and let one pathological boot raise the budget
# permanently).
_muf_render_ewma = None

# Tier 1.5: how long a successfully rendered MUF PNG may keep being served
# after a later rasterize fails. Beyond this the slot is cleared rather than
# handing the client a map whose terminator is a day out of date.
MUF_STALE_MAX_S = 24 * 3600

MUF_URL = 'https://prop.kc2g.com/renders/current/mufd-normal-now.svg'


# ---------------------------------------------------------------------------
# Diagnostics counters — the read side is GET /api/diagnostics
#
# The maintainer owns no ARMv6 hardware, so every Pi figure in this project is
# extrapolated from x86 and unverified. These counters exist to answer the
# questions that extrapolation cannot: is the 45 s rasterize budget actually
# firing, is the conditional GET actually producing 304s, did the disk cache
# come back warm.
#
# Rules every counter here follows:
#   * plain module-level ints/floats updated at call sites that ALREADY exist.
#     No new threads, no locks, no work added to a request path.
#   * every dict is created with its complete key set and never grows, so the
#     handler thread can copy one while the fetcher thread writes values into
#     it. A dict that could gain a key mid-copy would raise RuntimeError
#     ("changed size during iteration") inside the diagnostics handler.
#   * nothing in here is transmitted by itself. /api/diagnostics is a local
#     read; the operator's explicit press is what sends anything anywhere.
# ---------------------------------------------------------------------------

_BOOT_EPOCH = time.time()
_BOOT_MONOTONIC = time.monotonic()

# Rasterize outcome counts since boot. The whole point of splitting timeout
# from fail: a timeout means PHASE2_TIMEOUT_S/_muf_timeout() is too low for
# this hardware, a fail means cairosvg or cpulimit is broken/absent. They are
# indistinguishable in the log line, which prints "rasterize failed" for both.
_MUF_RASTERIZE_OK = 0
_MUF_RASTERIZE_TIMEOUT = 0
_MUF_RASTERIZE_FAIL = 0

# Wall clock of the last SUCCESSFUL rasterize, and the budget that was in
# force the last time one timed out (i.e. the number that fired).
_MUF_LAST_RENDER_S = None
_MUF_LAST_TIMEOUT_BUDGET_S = None

# Byte sizes from the last rasterize attempt. slim_bytes is None when
# _slim_muf_svg declined the document (upstream re-render), which is exactly
# the case where the render cost doubles.
_MUF_LAST_SVG_BYTES = None
_MUF_LAST_SLIM_BYTES = None
_MUF_LAST_PNG_BYTES = None
_MUF_SLIM_OK = 0
_MUF_SLIM_DECLINED = 0
_MUF_UNSLIMMED_RETRIES = 0

# What /api/muf-map last wrote to a client: 'png', 'svg' or 'none' (503).
# Distinct from _MUF_PNG_SOURCE, which says where the PNG we hold came from
# ('live' = rendered here, 'disk' = restored at boot).
_MUF_LAST_SERVED = 'none'

# Filled by _load_persisted() at boot, before any thread starts. A tuple, not
# a mutable set, so the handler never observes it half-built.
_DISK_RESTORED = ()
_BOOT_DISK_WARM = False

# One entry per fetcher. 'hamqsl' covers both solar and bands (one XML).
_FETCH_NAMES = ('hamqsl', 'dx', 'muf', 'sdo', 'enlil', 'drap', 'real_drap')

# product -> (CACHE payload key, CACHE stamp key). The five image products,
# in the same order as _PERSIST_KEYS.
_DIAG_PRODUCTS = (
    ('muf', 'muf_image_png', 'muf_image_png_updated'),
    ('sdo', 'solar_image', 'solar_image_updated'),
    ('enlil', 'enlil_image', 'enlil_image_updated'),
    ('drap', 'drap_image', 'drap_image_updated'),
    ('real_drap', 'real_drap_image', 'real_drap_image_updated'),
)


def _new_fetch_stat():
    """Fixed key set — see the no-resize rule above."""
    return {
        'last_ms': None,
        'last_status': None,
        'last_error': None,
        'last_at': None,
        'consecutive_failures': 0,
        'ok': 0,
        'not_modified': 0,
        'errors': 0,
    }


_FETCH_STATS = {name: _new_fetch_stat() for name in _FETCH_NAMES}

# name -> seconds from process start to the first fetch that came back with
# usable data. Pre-seeded with every name so the dict never resizes.
_FIRST_OK_S = {name: None for name in _FETCH_NAMES}


def _diag_error_name(error):
    """A short, safe label for a failure.

    Deliberately the exception TYPE, never str(e): an exception message can
    carry a proxy URL with credentials in it, and this structure is what the
    operator transmits.
    """
    if error is None:
        return None
    if isinstance(error, HTTPError):
        try:
            return 'HTTPError:%d' % int(error.code)
        except Exception:
            return 'HTTPError'
    if isinstance(error, BaseException):
        return type(error).__name__
    return str(error)[:40]


def _diag_fetch(name, status, started, error=None):
    """Record the outcome of ONE fetcher call. MUST NOT RAISE.

    Called from the fetcher thread only, at the points where the fetchers
    already return or log. `started` is a time.monotonic() reading taken at
    the top of the fetcher.

    `status` is one of:
      'ok'            — we now hold new bytes.
      'not_modified'  — upstream confirmed (304) the copy we already hold.
      'error'         — nothing usable came back.

    The first two are both successes for boot timing: a 304 means the panel
    has current data, which is the question first_ok_s is asked to answer.

    For fetch_muf this records the HTTP exchange only, stamped BEFORE the
    rasterize — the rasterize has its own three counters and its own wall
    clock, and folding a 45 s render into 'last_ms' would hide the fetch.
    """
    try:
        stat = _FETCH_STATS.get(name)
        if stat is None:
            return
        stat['last_ms'] = int(max(0.0, time.monotonic() - started) * 1000)
        stat['last_status'] = status
        stat['last_at'] = time.time()
        if status == 'error':
            stat['errors'] += 1
            stat['consecutive_failures'] += 1
            stat['last_error'] = _diag_error_name(error)
            return
        stat['consecutive_failures'] = 0
        stat['last_error'] = None
        if status == 'not_modified':
            stat['not_modified'] += 1
        else:
            stat['ok'] += 1
        if _FIRST_OK_S.get(name) is None:
            _FIRST_OK_S[name] = round(
                max(0.0, time.monotonic() - _BOOT_MONOTONIC), 1)
    except Exception:
        # A diagnostics counter may never be the reason a fetch is lost.
        pass


def _note_muf_served(kind):
    """Remember what /api/muf-map last wrote ('png' / 'svg' / 'none').

    A module-level function so the handler branch stays free of a `global`
    declaration; the cost on that path is one call and one store, on an
    endpoint the client hits about once every 15 minutes.
    """
    global _MUF_LAST_SERVED
    _MUF_LAST_SERVED = kind


# ---------------------------------------------------------------------------
# Tier 2.1 — disk persistence for the five image products
#
# CACHE is RAM-only, so every restart (and every power cut on a box with no
# RTC and no shutdown) starts from zero: five upstream round trips plus a
# multi-second rasterize before the first pixel, and nothing at all when the
# Pi boots without a network. Persisting the decoded payloads makes every
# boot after the first answer 200 to the client's very first image request.
#
# This is a design-intent change: the rootfs is deliberately quiescent
# (journald Storage=volatile, noatime,commit=60, PYTHONDONTWRITEBYTECODE=1).
# PERSIST_MIN_INTERVAL_S keeps it to ~7 MB/day of SD writes.
# ---------------------------------------------------------------------------

CACHE_DIR = os.environ.get('HAMCLOCK_CACHE_DIR', '/var/cache/hamclock-lite')

# At most one write per key per hour. The image cadence is 900 s, so this is a
# 4:1 throttle: ~319 KB x 24 = ~7.5 MB/day worst case.
PERSIST_MIN_INTERVAL_S = 3600

# Anything older than this is not loaded back. Matched to MUF_STALE_MAX_S on
# purpose: being *stricter* on disk than the running process is with its own
# in-RAM keep-last-good would mean a warm reboot throws away an image the
# server would happily have kept serving.
PERSIST_MAX_AGE_S = 24 * 3600

_MANIFEST_NAME = 'manifest.json'

# CACHE key -> (filename, the *_updated CACHE key that carries its real epoch)
_PERSIST_KEYS = {
    'muf_image_png': ('muf.png', 'muf_image_png_updated'),
    'solar_image': ('sdo.img', 'solar_image_updated'),
    'enlil_image': ('enlil.img', 'enlil_image_updated'),
    'drap_image': ('drap.img', 'drap_image_updated'),
    'real_drap_image': ('real-drap.img', 'real_drap_image_updated'),
}

# key -> epoch of the last persist ATTEMPT (success or failure). Seeding it
# from the manifest on load stops us rewriting bytes we just read back, and
# stamping it on failure keeps a read-only rootfs from retrying every 900 s.
_PERSIST_LAST = {}

# 2020-01-01. The Pi 1 has no RTC: before NTP syncs, time.time() is whatever
# fake-hwclock last wrote (or the epoch). Age-based eviction is only applied
# when the clock is past this, or a Pi powered off for a week would nuke its
# cache on exactly the boot where it is most valuable.
_CLOCK_SANITY_EPOCH = 1577836800.0

# Where the currently-served MUF PNG came from: 'live' (rendered in this
# process), 'disk' (restored from the cache dir at boot) or 'none'. Exposed
# via /api/health so the client can label a persisted-stale map honestly —
# serve-stale without a label is worse than blank for an operator making a
# band decision.
_MUF_PNG_SOURCE = 'none'


def _sniff_image(data):
    """Return 'png'/'jpeg'/'gif' for a recognised payload, else None."""
    if not data:
        return None
    head = bytes(data[:8])
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if head[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    return None


def _image_is_complete(data):
    """True only if `data` carries a full, correctly terminated image.

    Guards the load path against a truncated or SD-corrupted file: the atomic
    write below makes a half-written file impossible, but flash wear is not.
    """
    fmt = _sniff_image(data)
    tail = bytes(data[-8:]) if data else b''
    if fmt == 'png':
        # ...IEND + 4-byte CRC
        return len(data) > 16 and tail[-8:-4] == b'IEND'
    if fmt == 'jpeg':
        return len(data) > 4 and tail[-2:] == b'\xff\xd9'
    if fmt == 'gif':
        return len(data) > 6 and tail[-1:] == b'\x3b'
    return False


def _fsync_dir(path):
    """fsync a directory so a rename is durable.

    The rootfs is mounted noatime,commit=60 (offline-install.sh), so
    os.replace() alone only guarantees atomicity, not durability — a power cut
    inside the commit window can leave the directory entry pointing at
    nothing. Persisted images are only useful across an unclean restart, which
    is precisely the case os.replace() does not cover on its own.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path, data):
    """tempfile -> flush -> fsync -> os.replace -> fsync(dir)."""
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    _fsync_dir(directory)


def _read_manifest():
    try:
        with open(os.path.join(CACHE_DIR, _MANIFEST_NAME), 'rb') as f:
            man = json.loads(f.read().decode('utf-8'))
        if isinstance(man, dict) and isinstance(man.get('entries'), dict):
            return man
    except Exception:
        pass
    return {'version': 1, 'entries': {}}


def _persist(key):
    """Best-effort write-through of CACHE[key] to the cache dir.

    MUST NOT RAISE, ever. In fetch_enlil/fetch_drap/fetch_real_drap the
    `return` is the last statement of the `try` inside `for url in urls`, so
    an OSError escaping from here would skip that return, be logged as a bogus
    fetch failure, and fall through to the fallback URL — re-downloading the
    same product on every cycle, forever.
    """
    try:
        meta = _PERSIST_KEYS.get(key)
        if meta is None:
            return
        data = CACHE.get(key)
        # Falsy is legitimate, not an error: muf_image_png is None whenever a
        # rasterize has failed. Never write an empty file over a good one.
        if not data:
            return
        now = time.time()
        if now - (_PERSIST_LAST.get(key) or 0.0) < PERSIST_MIN_INTERVAL_S:
            return
        if _sniff_image(data) is None:
            # Not an image we recognise (an upstream HTML error page, say).
            return
        # Stamp the ATTEMPT, so a read-only or full filesystem is throttled
        # exactly like a success instead of retrying every image cycle.
        _PERSIST_LAST[key] = now
        fname, stamp_key = meta
        os.makedirs(CACHE_DIR, exist_ok=True)
        _write_atomic(os.path.join(CACHE_DIR, fname), data)
        man = _read_manifest()
        man['entries'][key] = {
            'file': fname,
            'size': len(data),
            'epoch': float(CACHE.get(stamp_key) or now),
        }
        _write_atomic(os.path.join(CACHE_DIR, _MANIFEST_NAME),
                      json.dumps(man, separators=(',', ':')).encode('utf-8'))
    except Exception as e:
        print(f'[{time.strftime("%H:%M:%S")}] persist {key} failed: {e}')


def _load_persisted():
    """Restore persisted images into CACHE. Returns the list of keys loaded.

    Called from __main__ BEFORE the fetcher thread starts and BEFORE the
    socket binds, so the very first request a client makes is already served
    from the restored payloads rather than racing the boot fetch chain.

    Seeds each *_updated stamp from the manifest's REAL epoch, never from
    now(): the age guards downstream (/api/health, MUF_STALE_MAX_S) exist to
    tell the operator how old the picture is, and stamping now() would make a
    week-old map claim to be seconds fresh.
    """
    global _MUF_PNG_SOURCE, _DISK_RESTORED, _BOOT_DISK_WARM
    loaded = []
    try:
        entries = _read_manifest().get('entries') or {}
        now = time.time()
        clock_ok = now > _CLOCK_SANITY_EPOCH
        for key, (default_name, stamp_key) in _PERSIST_KEYS.items():
            ent = entries.get(key)
            if not isinstance(ent, dict):
                continue
            fname = os.path.basename(str(ent.get('file') or default_name))
            path = os.path.join(CACHE_DIR, fname)
            try:
                with open(path, 'rb') as f:
                    data = f.read()
            except OSError:
                continue
            size = ent.get('size')
            try:
                epoch = float(ent.get('epoch') or 0.0)
            except (TypeError, ValueError):
                epoch = 0.0
            corrupt = (not data
                       or not isinstance(size, int)
                       or size != len(data)
                       or not _image_is_complete(data))
            if corrupt:
                # Unlink only for corruption. An over-age entry is merely
                # skipped (below) — a clock that is wrong but sane must not be
                # able to destroy the cache.
                print(f'[{time.strftime("%H:%M:%S")}] cache {key}: corrupt, '
                      f'discarding {fname}')
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            if clock_ok and epoch > 0 and (now - epoch) > PERSIST_MAX_AGE_S:
                continue
            CACHE[key] = data
            if epoch > 0:
                CACHE[stamp_key] = epoch
            _PERSIST_LAST[key] = epoch or now
            if key == 'muf_image_png':
                _MUF_PNG_SOURCE = 'disk'
            loaded.append(key)
        if loaded:
            print(f'[{time.strftime("%H:%M:%S")}] restored {len(loaded)} '
                  f'cached image(s) from {CACHE_DIR}: {", ".join(loaded)}')
    except Exception as e:
        print(f'[{time.strftime("%H:%M:%S")}] cache restore failed: {e}')
    # Rebound (not mutated) so the diagnostics handler can never see this
    # half-built, and stamped even on the failure path so a restore that blew
    # up reports an honest "cold".
    _DISK_RESTORED = tuple(loaded)
    _BOOT_DISK_WARM = bool(loaded)
    return loaded


# ---------------------------------------------------------------------------
# Tier 2.2 — conditional GET (If-Modified-Since) + gzip on every fetcher
# ---------------------------------------------------------------------------

# URL -> the Last-Modified header we last saw for THAT url. Keyed per URL, not
# per fetcher: enlil/drap/real-drap each iterate a two-URL fallback list, and
# NOAA's Last-Modified is a shared mirror-sync stamp (measured identical
# across unrelated products), so a per-fetcher key would replay one product's
# validator against a different one.
_HTTP_LAST_MODIFIED = {}
_LAST_MODIFIED_CAP = 32


def _conditional_get(url, timeout=20, record_lm=True):
    """GET `url` with Accept-Encoding: gzip and If-Modified-Since.

    Returns (body_bytes, not_modified). On a 304 the body is None.

    Deliberately does NOT replay an ETag. prop.kc2g.com is Apache with
    mod_deflate, which appends '-gzip' to the ETag on the way out but does not
    strip it on the way back in (measured: If-None-Match with the gzip ETag +
    Accept-Encoding: gzip returns 200 and the full body), and RFC 7232 makes
    If-None-Match win when both validators are sent — so an ETag here would
    silently disable the If-Modified-Since that does work.

    urllib does not auto-inflate, so the gzip.decompress step is mandatory.
    """
    headers = {'User-Agent': UA, 'Accept-Encoding': 'gzip'}
    lm = _HTTP_LAST_MODIFIED.get(url)
    if lm:
        headers['If-Modified-Since'] = lm
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Test doubles for urlopen expose read() only.
            hdrs = getattr(resp, 'headers', None)
            encoding = ''
            new_lm = None
            if hdrs is not None:
                try:
                    encoding = (hdrs.get('Content-Encoding') or '').lower()
                    new_lm = hdrs.get('Last-Modified')
                except Exception:
                    encoding, new_lm = '', None
    except HTTPError as e:
        if e.code == 304:
            return None, True
        raise
    if 'gzip' in encoding and raw:
        raw = gzip.decompress(raw)
    if record_lm and new_lm:
        if len(_HTTP_LAST_MODIFIED) >= _LAST_MODIFIED_CAP:
            # Bounded: the static URL set is <10 entries; only a derived
            # (per-frame) NOAA animation URL could ever grow this.
            _HTTP_LAST_MODIFIED.clear()
        _HTTP_LAST_MODIFIED[url] = new_lm
    return raw, False


def _etag_for(key_updated):
    """Format an ETag header value from a CACHE['..._updated'] timestamp.

    Tier 2c perf: the fetchers stamp CACHE['{solar,bands,dx}_updated'] every
    time they pre-encode the payload. Reusing that epoch as a quoted
    millisecond-resolution string gives us a stable, free ETag — no hashing,
    no extra state. RFC 7232 requires the quoted-string form.
    """
    ts = CACHE.get(key_updated) or 0
    return '"%.3f"' % float(ts)


def _kill_process_group(p):
    """SIGCONT then SIGKILL the whole process group of subprocess `p`.

    Tier 1.6: the rasterize child is `cpulimit -- python3 -c cairosvg`, and
    cpulimit enforces its duty cycle by alternating SIGSTOP/SIGCONT on the
    grandchild. Killing only `p` (what subprocess.run's timeout path does via
    Popen.kill) SIGKILLs cpulimit and leaves the ~48 MB python3/cairosvg
    grandchild orphaned — permanently SIGSTOPped if the timeout landed in the
    STOP half of the cycle, or running unthrottled if it landed in the CONT
    half. Repeated every 900 s on a 512 MB box that is an OOM.

    SIGCONT goes first because a stopped process acts on nothing but SIGCONT
    and SIGKILL; SIGKILL then takes the whole group down regardless of state.
    """
    pgid = None
    try:
        pgid = os.getpgid(p.pid)
    except OSError:
        pgid = None
    # Safety rail: only ever signal a group we know is NOT our own. If
    # start_new_session did not take effect the child shares our group and a
    # killpg would SIGKILL the server itself.
    try:
        own = os.getpgid(0)
    except OSError:
        own = None
    if pgid is None or pgid == own:
        try:
            p.kill()
        except OSError:
            pass
        return
    for sig in (signal.SIGCONT, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Tier 2.3 — strip chrome from the MUF SVG before handing it to cairosvg
#
# cairosvg's cost here is not pixels, it is <use> resolution: a profile of the
# unslimmed render spends 61 % of its wall clock in cairosvg/defs.py:use(),
# an O(n^2) cssselect2 re-walk per <use>. Removing the axis ticks, tick
# labels, gridlines and the colorbar takes 257 <use> down to 114 and halves
# the render (measured, cairosvg 2.9.0, output_width=360: 1157 ms -> 561 ms;
# PNG 70,117 B -> 60,601 B).
#
# THREE THINGS THIS MUST NOT DO, each verified to destroy real data:
#   (a) blanket-strip <use>. Of the 257, only 143 are chrome. text_29..text_50
#       are the per-station MUF values printed on the ionosonde dots and
#       text_52..text_69 are the contour value labels — both are <use>-only.
#   (b) remove <g id="matplotlib.axis_1"> without hoisting the six digit glyph
#       <defs> that live INSIDE it (DejaVuSans-30/31/32/34/36/38). The station
#       and contour labels reference them, and would lose most of their
#       digits.
#   (c) prune "groups with no drawing descendant" — that deletes the document
#       <style>*{stroke-linejoin:round;stroke-linecap:butt}</style>.
# ---------------------------------------------------------------------------

_SVG_NS = 'http://www.w3.org/2000/svg'
_XLINK_NS = 'http://www.w3.org/1999/xlink'
_SVG_Q = '{%s}' % _SVG_NS
_XLINK_HREF = '{%s}href' % _XLINK_NS

# Serialize back with the same prefixes matplotlib used, so the output stays
# byte-for-byte plausible SVG rather than ns0:-prefixed.
ElementTree.register_namespace('', _SVG_NS)
ElementTree.register_namespace('xlink', _XLINK_NS)

# patch_2 is the axes rectangle: "M<x> <y+h>v-<h>h<w>v<h>z"
_PATCH2_D_RE = re.compile(
    r'^M\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*'
    r'v\s*(-?[\d.]+)\s*h\s*(-?[\d.]+)\s*v\s*(-?[\d.]+)\s*z?$')
_VIEWBOX_RE = re.compile(r'^\s*(-?[\d.]+)[ ,]+(-?[\d.]+)[ ,]+'
                         r'(-?[\d.]+)[ ,]+(-?[\d.]+)\s*$')
_LEN_RE = re.compile(r'^\s*(-?[\d.]+)')
_URL_REF_RE = re.compile(r'url\(\s*#([^)\s]+)\s*\)')

# Groups whose entire subtree is chrome: the x/y axis ticks + labels +
# gridlines, and axes_2 (the colorbar, with its own matplotlib.axis_3).
_MUF_CHROME_ID_PREFIX = 'matplotlib.axis'
_MUF_CHROME_IDS = ('axes_2',)

# The real document is ~365 KB. Refusing to build a DOM for anything wildly
# larger bounds both the peak RSS of the parse on a 512 MB box and the blast
# radius of an entity-expansion payload (xml.etree is safe against XXE and DTD
# retrieval but not against billion-laughs). defusedxml is not in the stdlib
# and cannot be installed on the target, and cairosvg parses these same bytes
# with its own parser either way, so this cap is the available mitigation.
_MUF_SVG_MAX_BYTES = 8 * 1024 * 1024


def _slim_muf_svg(svg_bytes):
    """Return a chrome-free copy of the KC2G MUF SVG, or None.

    None means "I did not recognise this document" and the caller must fall
    back to the original bytes unchanged — an upstream re-render that moves
    the structure must degrade to today's exact behaviour, never to a broken
    or half-stripped map.
    """
    try:
        if not svg_bytes or len(svg_bytes) > _MUF_SVG_MAX_BYTES:
            return None
        root = ElementTree.fromstring(svg_bytes)
        if root.tag != _SVG_Q + 'svg':
            return None

        parents = {}
        for parent in root.iter():
            for child in parent:
                parents[child] = parent

        # --- crop rectangle from patch_2 (the axes box) --------------------
        patch2 = None
        for el in root.iter(_SVG_Q + 'path'):
            if el.get('id') == 'patch_2':
                patch2 = el
                break
        if patch2 is None:
            return None
        m = _PATCH2_D_RE.match((patch2.get('d') or '').strip())
        if m is None:
            return None
        px, py, pdy, pdx, _pdy2 = (float(v) for v in m.groups())
        crop_w = abs(pdx)
        crop_h = abs(pdy)
        crop_x = px
        crop_y = min(py, py + pdy)

        vb = _VIEWBOX_RE.match(root.get('viewBox') or '')
        if vb is None:
            return None
        _vx, _vy, vw, vh = (float(v) for v in vb.groups())
        # Plausibility: the axes box must sit inside the page and must be most
        # of it. A 5x5 crop out of a 1145x679 page is a parse accident, not a
        # map.
        if not (vw > 0 and vh > 0 and crop_w > 0 and crop_h > 0):
            return None
        if crop_x < 0 or crop_y < 0:
            return None
        if crop_x + crop_w > vw * 1.001 or crop_y + crop_h > vh * 1.001:
            return None
        if crop_w < 0.5 * vw or crop_h < 0.5 * vh:
            return None

        # --- collect the chrome groups ------------------------------------
        doomed = []
        for el in root.iter(_SVG_Q + 'g'):
            eid = el.get('id') or ''
            if eid.startswith(_MUF_CHROME_ID_PREFIX) or eid in _MUF_CHROME_IDS:
                doomed.append(el)
        if not doomed:
            return None

        # (b) stash every <defs> child before the subtrees go away.
        stashed = []
        for group in doomed:
            for defs in group.iter(_SVG_Q + 'defs'):
                for child in defs:
                    if child.get('id'):
                        stashed.append(child)

        for group in doomed:
            parent = parents.get(group)
            if parent is None:
                continue
            try:
                parent.remove(group)
            except ValueError:
                # Already gone with an ancestor (matplotlib.axis_3 lives
                # inside axes_2). Nothing to do.
                pass

        # --- hoist back only the defs the survivors still reference --------
        referenced = set()
        defined = set()
        for el in root.iter():
            eid = el.get('id')
            if eid:
                defined.add(eid)
            href = el.get(_XLINK_HREF) or el.get('href')
            if href and href.startswith('#'):
                referenced.add(href[1:])
            for value in el.attrib.values():
                for ref in _URL_REF_RE.finditer(value):
                    referenced.add(ref.group(1))
        needed = [d for d in stashed
                  if d.get('id') in referenced and d.get('id') not in defined]
        if needed:
            hoisted = ElementTree.Element(_SVG_Q + 'defs')
            hoisted.extend(needed)
            root.insert(0, hoisted)

        # --- crop ----------------------------------------------------------
        # Keep the intrinsic px-per-unit scale so cairosvg's output_width
        # keeps meaning the same thing; only the aspect ratio changes (the
        # axis gutters and colorbar are gone).
        scale = 1.0
        wm = _LEN_RE.match(root.get('width') or '')
        if wm is not None:
            try:
                scale = float(wm.group(1)) / vw
            except (ValueError, ZeroDivisionError):
                scale = 1.0
        if not (0.01 < scale < 100.0):
            scale = 1.0
        root.set('viewBox', '%g %g %g %g' % (crop_x, crop_y, crop_w, crop_h))
        root.set('width', '%g' % (crop_w * scale))
        root.set('height', '%g' % (crop_h * scale))

        return ElementTree.tostring(root, encoding='utf-8')
    except Exception as e:
        print('[muf] slim failed, using full SVG: %s' % e, file=sys.stderr)
        return None


def _record_muf_render(seconds):
    """Fold a successful render's wall clock into the in-RAM EWMA."""
    global _muf_render_ewma
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return
    # Reject NaN/inf/non-positive so one bad sample can never pin the budget.
    if not (0.0 < s < 1e6):
        return
    if _muf_render_ewma is None:
        _muf_render_ewma = s
    else:
        _muf_render_ewma = ((1.0 - MUF_EWMA_ALPHA) * _muf_render_ewma
                            + MUF_EWMA_ALPHA * s)


def _muf_timeout():
    """Adaptive rasterize budget, floored at PHASE2_TIMEOUT_S.

    Escalates while no render has EVER succeeded. The EWMA is only written on
    success, so a box whose true render time sits above the floor used to
    deadlock: time out at 45 s, record nothing, keep the 45 s budget, time out
    again, forever. The budget could only widen after a success it could never
    achieve.

    A Pi 1B is exactly that box. Slimmed cairosvg measures 0.57 s on x86;
    scaled 30-50x for ARMv6 and doubled again by cpulimit's 50% duty cycle
    that lands around 34-57 s — straddling the 45 s floor. So consecutive
    timeouts are treated as evidence the floor is wrong for this hardware and
    the budget steps toward the ceiling. Fast boxes never pay it: one success
    switches to the EWMA path and the escalation is forgotten.
    """
    ewma = _muf_render_ewma
    if ewma:
        return int(min(PHASE2_TIMEOUT_MAX_S,
                       max(PHASE2_TIMEOUT_S, MUF_TIMEOUT_FACTOR * ewma)))
    if _MUF_RASTERIZE_TIMEOUT > 0:
        steps = 3.0
        step = min(float(_MUF_RASTERIZE_TIMEOUT), steps)
        widened = PHASE2_TIMEOUT_S + (
            (PHASE2_TIMEOUT_MAX_S - PHASE2_TIMEOUT_S) * (step / steps))
        return int(min(PHASE2_TIMEOUT_MAX_S, widened))
    return PHASE2_TIMEOUT_S


#: Rasterizer engines, fastest first. Both read SVG on stdin and write PNG to
#: stdout, so they are interchangeable.
#:
#: cairosvg parses the whole document in Python, and profiling put 61% of its
#: time in an O(n^2) cssselect2 re-walk triggered once per <use> element — of
#: which this map has 257. librsvg is C and has no equivalent. Measured on the
#: real KC2G SVG: cairosvg 1.204 s, rsvg-convert 0.181 s, a 6.7x speedup, with
#: visually identical output (same contours, station dots, colorbar, labels).
#:
#: This matters because the field diagnostics showed cairosvg NEVER completing
#: on a Pi 1B: rasterize_ok 0, rasterize_timeout 3, still failing at an 88 s
#: budget. docs/muf-source.md's own rule says >30 s means change source or
#: engine. 6.7x puts the same work near 13 s, inside the 45 s floor.
#:
#: cairosvg stays as the second rung, NOT as dead weight: a Pi upgraded in
#: place has server.py refreshed without necessarily having librsvg2-bin
#: installed, and falling back is better than a blank panel.
MUF_ENGINES = (
    ('rsvg', ['cpulimit', '-l', '50', '-q', '--',
              'rsvg-convert', '-w', '360', '-f', 'png']),
    ('cairosvg', ['cpulimit', '-l', '50', '-q', '--',
                  'python3', '-c',
                  'import sys, cairosvg; cairosvg.svg2png('
                  'bytestring=sys.stdin.buffer.read(), '
                  'output_width=360, write_to=sys.stdout.buffer)']),
)

_PNG_MAGIC = b'\x89PNG\r\n\x1a\x0a'

#: Which engine last produced a PNG, for /api/diagnostics.
_MUF_ENGINE_USED = None

#: Times an engine was skipped because its binary is not installed. Kept apart
#: from _MUF_RASTERIZE_FAIL on purpose — see _is_engine_absent.
_MUF_ENGINE_ABSENT = 0


def _is_engine_absent(exc):
    """True when the engine binary simply is not installed.

    ONLY exit 127: cpulimit ran but could not find the program after `--`.

    A FileNotFoundError is deliberately NOT this. That means argv[0] — cpulimit
    itself — is missing, so no engine on the ladder can run and nothing will
    ever render. That is a genuine fault and must keep incrementing
    rasterize_fail, or the one environment problem that breaks every engine
    would be the one the diagnostics stayed silent about.
    """
    return (isinstance(exc, subprocess.CalledProcessError)
            and exc.returncode == 127)


def _rasterize_once(payload, timeout_s, argv=None):
    """One cpulimit+rasterizer subprocess round trip. None on any failure."""
    global _MUF_RASTERIZE_OK, _MUF_RASTERIZE_TIMEOUT, _MUF_RASTERIZE_FAIL
    global _MUF_LAST_TIMEOUT_BUDGET_S, _MUF_ENGINE_ABSENT
    if argv is None:
        argv = list(MUF_ENGINES[-1][1])
    p = None
    try:
        p = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out, _err = p.communicate(input=payload, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_process_group(p)
            try:
                p.communicate()  # reap cpulimit, drain the pipes
            except Exception:
                pass
            raise
        if p.returncode:
            raise subprocess.CalledProcessError(p.returncode, argv, output=out)
        # A zero exit with an empty stdout is still a failed render as far as
        # every caller is concerned (`if out:`), so count it as one.
        # cpulimit can swallow a failing child's exit status, so a zero return
        # code is not enough — check the bytes are actually a PNG.
        if out and not out.startswith(_PNG_MAGIC):
            raise subprocess.CalledProcessError(0, argv, output=out)
        if out:
            _MUF_RASTERIZE_OK += 1
        else:
            _MUF_RASTERIZE_FAIL += 1
        return out
    # FileNotFoundError (cpulimit not installed) is an OSError subclass.
    except (subprocess.SubprocessError, OSError) as e:
        # The log line below says "rasterize failed" for a 45 s timeout and
        # for a missing cpulimit alike. Splitting the counters is what tells
        # the maintainer which one the Pi is actually hitting.
        if isinstance(e, subprocess.TimeoutExpired):
            _MUF_RASTERIZE_TIMEOUT += 1
            _MUF_LAST_TIMEOUT_BUDGET_S = timeout_s
        elif _is_engine_absent(e):
            # An engine that is not installed did not FAIL to render — it was
            # never asked. Counting it would make rasterize_fail climb on every
            # refresh for every Pi that has not yet installed librsvg2-bin
            # (i.e. every existing install), while cairosvg quietly succeeds
            # right after. Diagnostics that cry wolf are worse than none.
            _MUF_ENGINE_ABSENT += 1
            print('[muf] engine unavailable (%s); trying the next one'
                  % (argv[argv.index('--') + 1] if '--' in argv else argv[0]),
                  file=sys.stderr)
            return None
        else:
            _MUF_RASTERIZE_FAIL += 1
        print('[muf] rasterize failed: %s' % e, file=sys.stderr)
        return None
    finally:
        # Belt and braces: no exit path may leave the group alive. A no-op on
        # every normal path (communicate() has already reaped by then).
        if p is not None and p.poll() is None:
            _kill_process_group(p)


def _rasterize_ladder(payload, timeout_s):
    """Try each engine in MUF_ENGINES until one yields a PNG.

    Returns (png_or_None, seconds_of_the_attempt_that_settled_it,
    any_attempt_timed_out). An engine
    that is not installed is skipped without counting as a failure — see
    _is_engine_absent — so a Pi that has never installed librsvg2-bin simply
    lands on cairosvg rather than reporting a fault every refresh.
    """
    global _MUF_ENGINE_USED
    elapsed = 0.0
    before = _MUF_RASTERIZE_TIMEOUT
    for name, argv in MUF_ENGINES:
        started = time.monotonic()
        out = _rasterize_once(payload, timeout_s, argv=list(argv))
        elapsed = time.monotonic() - started
        if out:
            _MUF_ENGINE_USED = name
            return out, elapsed, False
        print('[muf] engine %s produced nothing in %.1fs' % (name, elapsed),
              file=sys.stderr)
    # Read the counter rather than the clock: `elapsed` is only the LAST
    # attempt, so a ladder whose first engine timed out and whose second
    # failed instantly would otherwise look fast enough to retry.
    return None, elapsed, (_MUF_RASTERIZE_TIMEOUT > before)


def _rasterize_muf(svg_bytes):
    """Render the KC2G MUF SVG to PNG in a subprocess so the multi-second
    render does not block the request thread or the background fetcher.

    output_width=360 because the MUF panel is ~360x210 in the Tier 2a
    720x450 native framebuffer (HVS upscales to 1440x900); rendering at
    native panel width halves cairo's CPU cost vs. the prior 720 width.
    cairosvg.svg2png preserves aspect ratio when only output_width is
    given. Width is NOT the lever it looks like — measured, cairosvg's cost
    is per-parse, not per-pixel (original @360 = 1157 ms vs @204 = 1172 ms),
    and /api/muf-map is shared with the browser dashboard where the panel is
    ~790 px wide. Tier 2.3's slimming is what actually halves the render.

    cpulimit caps the subprocess to 50% of one core so the render loop
    keeps its frame budget even mid-rasterize. nice -n 19 alone is
    ineffective on an idle single-core box because the render loop
    sleeps between 10 FPS frames; the cairosvg job would still claim
    the core. cpulimit enforces a hard duty cycle.

    Tier 1.6: started with start_new_session=True so cpulimit and the
    python3/cairosvg grandchild it forks share one process group we can take
    down as a unit on timeout (see _kill_process_group). start_new_session is
    implemented natively in _posixsubprocess — unlike preexec_fn it is safe in
    a threaded process and keeps the vfork fast path.

    Returns the PNG bytes, or None on subprocess error / timeout /
    missing cpulimit / cairosvg ImportError inside the child.
    """
    global _MUF_LAST_RENDER_S, _MUF_LAST_SVG_BYTES, _MUF_LAST_SLIM_BYTES
    global _MUF_LAST_PNG_BYTES, _MUF_SLIM_OK, _MUF_SLIM_DECLINED
    global _MUF_UNSLIMMED_RETRIES

    timeout_s = _muf_timeout()
    slimmed = _slim_muf_svg(svg_bytes)
    payload = slimmed if slimmed else svg_bytes

    # Slimmed vs original is the lever on render cost (257 <use> -> 114,
    # 1157 ms -> 561 ms on x86). If a future upstream re-render makes
    # _slim_muf_svg decline, slim_bytes goes null and the render doubles —
    # this is where that shows up.
    _MUF_LAST_SVG_BYTES = len(svg_bytes) if svg_bytes else 0
    _MUF_LAST_SLIM_BYTES = len(slimmed) if slimmed else None
    if slimmed:
        _MUF_SLIM_OK += 1
    else:
        _MUF_SLIM_DECLINED += 1

    out, elapsed, timed_out = _rasterize_ladder(payload, timeout_s)

    if out:
        _record_muf_render(elapsed)
        _MUF_LAST_RENDER_S = round(elapsed, 3)
        _MUF_LAST_PNG_BYTES = len(out)
        return out

    # Tier 2.3 safety net: if the SLIMMED payload failed quickly, that is a
    # cairosvg parse complaint about our surgery, not a slow box — retry once
    # with the untouched bytes so a future upstream re-render can never turn a
    # working map into a permanently blank panel. Deliberately not retried
    # after a timeout: two full budgets back to back (up to 220 s) would
    # starve the 120 s fetch_dx cadence, which is exactly what
    # PHASE2_TIMEOUT_MAX_S exists to prevent.
    if slimmed and not timed_out and elapsed < timeout_s * 0.5:
        print('[muf] slimmed SVG did not render in %.1fs; retrying unslimmed'
              % elapsed, file=sys.stderr)
        _MUF_UNSLIMMED_RETRIES += 1
        # The retry walks the SAME ladder. Pinning it to one engine would mean
        # the fallback path silently used a different rasterizer from the one
        # that just failed, which is not a like-for-like retry.
        out, retry_elapsed, _ = _rasterize_ladder(svg_bytes, timeout_s)
        if out:
            _record_muf_render(retry_elapsed)
            _MUF_LAST_RENDER_S = round(retry_elapsed, 3)
            _MUF_LAST_PNG_BYTES = len(out)
            return out
    return None


# Solar image proxy (NASA SDO)
SDO_URL = 'https://sdo.gsfc.nasa.gov/assets/img/latest/latest_256_HMIIC.jpg'

# Approximate lat/lng for top DXCC entities
COUNTRY_COORDS = {
    'United States': (39, -98), 'Russia': (55, 37), 'Germany': (51, 10),
    'Japan': (36, 140), 'United Kingdom': (52, -1), 'France': (47, 2),
    'Italy': (42, 12), 'Spain': (40, -4), 'Brazil': (-15, -47),
    'Canada': (45, -75), 'Australia': (-25, 134), 'China': (35, 105),
    'India': (20, 77), 'Netherlands': (52, 5), 'Poland': (52, 20),
    'Sweden': (59, 18), 'Argentina': (-34, -58), 'South Africa': (-26, 28),
    'Greece': (38, 24), 'Belgium': (51, 4), 'Portugal': (39, -8),
    'Czech Republic': (50, 15), 'Austria': (48, 16), 'Ukraine': (49, 32),
    'Finland': (61, 25), 'Norway': (60, 11), 'Denmark': (56, 10),
    'Switzerland': (47, 8), 'Croatia': (45, 16), 'Romania': (45, 25),
    'Hungary': (47, 19), 'Ireland': (53, -8), 'Serbia': (44, 21),
    'Bulgaria': (43, 25), 'New Zealand': (-41, 175), 'Chile': (-33, -71),
    'Mexico': (19, -99), 'Colombia': (4, -74), 'Thailand': (14, 101),
    'Indonesia': (-5, 120), 'Philippines': (13, 122), 'South Korea': (37, 127),
    'Turkey': (39, 35), 'Israel': (32, 35), 'Egypt': (30, 31),
    'Nigeria': (10, 8), 'Kenya': (-1, 37), 'Morocco': (32, -5),
    'French Guiana': (4, -53), 'Cuba': (22, -80),
}


def lookup_callsign(call):
    """Look up callsign via callook.info (US) or hamdb.org (international)"""
    result = {'callsign': call, 'grid': None, 'lat': None, 'lng': None, 'name': None, 'country': None}

    # Try callook.info first (US callsigns)
    try:
        req = Request(f'https://callook.info/{call}/json', headers={'User-Agent': UA})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if data.get('status') == 'VALID':
            loc = data.get('location', {})
            result['grid'] = loc.get('gridsquare', '')[:6]
            result['lat'] = float(loc.get('latitude', 0))
            result['lng'] = float(loc.get('longitude', 0))
            result['name'] = data.get('name', '')
            result['country'] = data.get('address', {}).get('line2', 'United States')
            return result
    except Exception:
        pass

    # Fallback: hamdb.org (international)
    try:
        req = Request(f'https://api.hamdb.org/{call}/json/hamclock', headers={'User-Agent': UA})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        cs = data.get('hamdb', {}).get('callsign', {})
        if cs.get('grid'):
            result['grid'] = cs['grid'][:6]
        if cs.get('lat'):
            result['lat'] = float(cs['lat'])
        if cs.get('lon'):
            result['lng'] = float(cs['lon'])
        result['name'] = f"{cs.get('fname', '')} {cs.get('name', '')}".strip()
        result['country'] = cs.get('country', '')
    except Exception:
        pass

    return result


def fetch_hamqsl():
    """Fetch solar and band data from HamQSL XML"""
    started = time.monotonic()
    try:
        req = Request('https://www.hamqsl.com/solarxml.php', headers={'User-Agent': UA})
        # Tier 1b perf: 8 s is well above HamQSL's median response (~1 s);
        # the prior 15 s pinned the fetcher on a transient upstream stall.
        with urlopen(req, timeout=8) as resp:
            xml_data = resp.read().decode('utf-8')

        root = ElementTree.fromstring(xml_data)
        sd = root.find('.//solardata')
        if sd is None:
            _diag_fetch('hamqsl', 'error', started, 'no_solardata')
            return

        def gt(tag, default=''):
            el = sd.find(tag)
            return el.text.strip() if el is not None and el.text else default

        solar = {
            'sfi': gt('solarflux', '0'),
            'ssn': gt('sunspots', '0'),
            'aIndex': gt('aindex', '0'),
            'kIndex': gt('kindex', '0'),
            'xray': gt('xray', 'N/A'),
            'heliumLine': gt('heliumline', 'N/A'),
            'protonFlux': gt('protonflux', 'N/A'),
            'electronFlux': gt('electronflux', 'N/A'),
            'aurora': gt('aurora', '0'),
            'solarWind': gt('solarwind', '0'),
            'magneticField': gt('magneticfield', '0'),
            'geomagField': gt('geomagfield', 'quiet'),
            'signalNoise': gt('signalnoise', 'S0-S0'),
            'fof2': gt('fof2', '0'),
            'mpiVer': gt('mpiVer', ''),
            'updated': gt('updated', ''),
        }

        bands = {}
        for band_el in sd.findall('.//band'):
            name = band_el.get('name', '')
            time_attr = band_el.get('time', '')
            condition = band_el.text or 'N/A'
            if name:
                if name not in bands:
                    bands[name] = {}
                bands[name][time_attr] = condition

        CACHE['solar'] = solar
        CACHE['bands'] = bands
        # Tier 1b perf: pre-encode once per fetch so /api/solar + /api/bands
        # can write the cached bytes straight to the socket.
        CACHE['solar_bytes'] = json.dumps(solar, separators=(',', ':')).encode('utf-8')
        CACHE['bands_bytes'] = json.dumps(bands, separators=(',', ':')).encode('utf-8')
        # Payload slots are written BEFORE the *_updated stamps on purpose:
        # the stamp is the ETag source and this is a ThreadingHTTPServer, so a
        # request landing between the two statements would receive the NEW
        # ETag with the OLD body — and would then be 304'd on that ETag for up
        # to the full 5-minute solar cadence.
        stamp = time.time()
        CACHE['solar_updated'] = stamp
        CACHE['bands_updated'] = stamp
        _diag_fetch('hamqsl', 'ok', started)
        print(f'[{time.strftime("%H:%M:%S")}] Solar/bands updated: SFI={solar["sfi"]} Kp={solar["kIndex"]}')
    except Exception as e:
        _diag_fetch('hamqsl', 'error', started, e)
        print(f'[{time.strftime("%H:%M:%S")}] HamQSL fetch failed: {e}')


def freq_to_band(freq_khz):
    f = float(freq_khz)
    if f < 2000:
        return '160m'
    if f < 4000:
        return '80m'
    if f < 5500:
        return '60m'
    if f < 8000:
        return '40m'
    if f < 11000:
        return '30m'
    if f < 15000:
        return '20m'
    if f < 19000:
        return '17m'
    if f < 22000:
        return '15m'
    if f < 26000:
        return '12m'
    if f < 30000:
        return '10m'
    if f < 55000:
        return '6m'
    if f < 148000:
        return '2m'
    return '70cm'


def fetch_dx():
    """Fetch DX spots from HamQTH or fallback.

    Tier 1b perf: limit=15 (was 30) is enough for the pygame client
    (5 rows in draw_dx_spots + band-activity histogram which only needs
    counts per band). Halves the upstream CSV and the cached payload.
    """
    urls = [
        'https://www.hamqth.com/dxc_csv.php?limit=15',
        'https://www.ha8tks.hu/dx/dxc_csv.php?limit=15',
    ]
    # One diagnostics record per CALL, not per URL: recording inside the loop
    # would count a primary failure that the fallback rescued as a fetch
    # failure, and inflate consecutive_failures by 2 per cycle when both fail.
    started = time.monotonic()
    last_error = None
    for url in urls:
        try:
            req = Request(url, headers={'User-Agent': UA})
            with urlopen(req, timeout=10) as resp:
                csv_data = resp.read().decode('utf-8', errors='replace')

            spots = []
            for line in csv_data.strip().split('\n'):
                # HamQTH uses ^ as delimiter, some use ,
                sep = '^' if '^' in line else ','
                parts = line.split(sep)
                if len(parts) < 5:
                    continue
                try:
                    # Format: spotter^freq^dx^comment^time^...
                    freq = parts[1].strip()
                    freq_khz = float(freq)
                    country = parts[10].strip() if len(parts) > 10 else ''
                    coords = COUNTRY_COORDS.get(country)
                    # Tier 1b perf: drop the 'country' field — nothing downstream
                    # reads it; only lat/lng are used to plot on the map.
                    spot = {
                        'frequency': freq,
                        'spotter': parts[0].strip(),
                        'dx': parts[2].strip(),
                        'comment': parts[3].strip() if len(parts) > 3 else '',
                        'time': parts[4].strip() if len(parts) > 4 else '',
                        'band': freq_to_band(freq_khz),
                        'lat': coords[0] if coords else None,
                        'lng': coords[1] if coords else None,
                    }
                    spots.append(spot)
                except (ValueError, IndexError):
                    continue

            if spots:
                CACHE['dxspots'] = spots
                # Tier 1b perf: pre-encode the dxspots payload once per fetch.
                CACHE['dxspots_bytes'] = json.dumps(
                    spots, separators=(',', ':')
                ).encode('utf-8')
                # Body before stamp — see the note in fetch_hamqsl: the stamp
                # is the ETag and a request racing between the two would be
                # 304'd against a body it never received.
                CACHE['dx_updated'] = time.time()
                _diag_fetch('dx', 'ok', started)
                print(f'[{time.strftime("%H:%M:%S")}] DX spots updated: {len(spots)} spots from {url.split("/")[2]}')
                return
        except Exception as e:
            last_error = e
            print(f'[{time.strftime("%H:%M:%S")}] DX fetch failed ({url.split("/")[2]}): {e}')
    _diag_fetch('dx', 'error', started, last_error)
    print(f'[{time.strftime("%H:%M:%S")}] All DX sources failed')


def fetch_muf():
    """Fetch KC2G MUF propagation map SVG and rasterize to PNG.

    The SVG bytes stay in CACHE['muf_image'] so the browser dashboard keeps
    working (it consumes the SVG directly). The native pygame client wants
    pre-rasterized PNG because cairosvg on a Pi 1 takes seconds — too slow
    for the render loop, and SDL/nanosvg decodes the raw SVG into a 5.5 MB
    greyscale surface on the render thread if it is ever handed one.

    Tier 1.5: a failed rasterize no longer wipes CACHE['muf_image_png'].
    Overwriting a good PNG with None turns one 45 s timeout into a blank
    panel until the next successful render 15+ minutes later, and (with the
    native client now refusing SVG) into a blank panel forever if the
    rasterize keeps timing out. The last good PNG is kept until it is older
    than MUF_STALE_MAX_S.

    Tier 2.2: revalidates with If-Modified-Since (365,246 B -> 148,984 B on
    the wire with gzip, 0 B on a 304).
    """
    global _MUF_PNG_SOURCE
    # Stamped before the rasterize on every path below, so fetch.muf.last_ms
    # is the HTTP exchange only. The render has its own wall clock
    # (muf.last_render_s) and its own three outcome counters.
    started = time.monotonic()
    try:
        data, not_modified = _conditional_get(MUF_URL, timeout=20)
        if not_modified:
            # CRITICAL: a 304 must not short-circuit when we have no PNG.
            # One failed rasterize plus a stalled upstream generator (the SVG
            # is regenerated on a schedule; a stall is normal) would otherwise
            # strand /api/muf-map on 503 permanently, because the only code
            # path that renders is the one that just returned early.
            if CACHE.get('muf_image_png'):
                _diag_fetch('muf', 'not_modified', started)
                print(f'[{time.strftime("%H:%M:%S")}] MUF map unchanged (304)')
                return
            data = CACHE.get('muf_image')
            if not data:
                # A validator with nothing behind it: the map is missing and
                # upstream will keep telling us it has not changed. Counted as
                # an error, not a 304, because no data reached the panel.
                _diag_fetch('muf', 'error', started, '304_no_body')
                print(f'[{time.strftime("%H:%M:%S")}] MUF map unchanged (304), '
                      f'nothing cached to rasterize')
                return
            _diag_fetch('muf', 'not_modified', started)
            print(f'[{time.strftime("%H:%M:%S")}] MUF map unchanged (304), '
                  f're-rasterizing the cached SVG')
        else:
            CACHE['muf_image'] = data
            CACHE['muf_image_updated'] = time.time()
            _diag_fetch('muf', 'ok', started)
        # The PNG inherits the SVG's epoch, not now(): re-rendering a
        # six-hour-old SVG must not make the map claim to be fresh, or the
        # /api/health age label (and MUF_STALE_MAX_S) start lying.
        stamp = CACHE.get('muf_image_updated') or time.time()
        png = _rasterize_muf(data)
        if png:
            CACHE['muf_image_png'] = png
            CACHE['muf_image_png_updated'] = stamp
            _MUF_PNG_SOURCE = 'live'
            _persist('muf_image_png')
            print(f'[{time.strftime("%H:%M:%S")}] MUF map updated '
                  f'({len(data)} B SVG -> {len(png)} B PNG)')
        else:
            prev = CACHE.get('muf_image_png')
            age = time.time() - (CACHE.get('muf_image_png_updated') or 0)
            if prev and age <= MUF_STALE_MAX_S:
                print(f'[{time.strftime("%H:%M:%S")}] MUF map updated '
                      f'({len(data)} B SVG, PNG rasterize failed — '
                      f'keeping last good PNG, age {int(age)} s)')
            else:
                CACHE['muf_image_png'] = None
                CACHE['muf_image_png_updated'] = 0
                _MUF_PNG_SOURCE = 'none'
                print(f'[{time.strftime("%H:%M:%S")}] MUF map updated '
                      f'({len(data)} B SVG, PNG rasterize failed)')
    except Exception as e:
        _diag_fetch('muf', 'error', started, e)
        print(f'[{time.strftime("%H:%M:%S")}] MUF map fetch failed: {e}')


def _note_unchanged(label, cache_key, url):
    """Handle a 304 for an image product. True => the caller should return.

    True means "we already hold the body upstream just told us is current".
    False is the pathological case (a validator with no body behind it — the
    slot was cleared after the validator was recorded); we drop the stale
    validator so the next attempt is unconditional and let the caller fall
    through to its fallback URL.
    """
    if CACHE.get(cache_key):
        print(f'[{time.strftime("%H:%M:%S")}] {label} unchanged (304)')
        return True
    _HTTP_LAST_MODIFIED.pop(url, None)
    print(f'[{time.strftime("%H:%M:%S")}] {label} unchanged (304) but the '
          f'cache slot is empty; dropping the validator')
    return False


def fetch_enlil():
    """Fetch WSA-Enlil solar wind prediction image"""
    urls = [
        'https://services.swpc.noaa.gov/images/animations/enlil/latest.jpg',
        'https://services.swpc.noaa.gov/products/animations/enlil.json',
    ]
    # One record per call — see the note in fetch_dx.
    started = time.monotonic()
    last_error = None
    for url in urls:
        try:
            data, not_modified = _conditional_get(url, timeout=20)
            if not_modified:
                if _note_unchanged('Enlil', 'enlil_image', url):
                    # return, not continue: falling through would re-download
                    # the same product from the fallback URL every cycle.
                    _diag_fetch('enlil', 'not_modified', started)
                    return
                continue
            if url.endswith('.json'):
                # JSON response — extract latest image URL
                items = json.loads(data.decode('utf-8'))
                if items:
                    last = items[-1]
                    img_url = 'https://services.swpc.noaa.gov' + last.get('url', '')
                    # record_lm=False: this URL names one animation FRAME and
                    # changes every cycle, so caching its validator would grow
                    # _HTTP_LAST_MODIFIED without ever producing a 304.
                    img, img_304 = _conditional_get(img_url, timeout=20,
                                                    record_lm=False)
                    if img_304:
                        continue
                    data = img
            if not data:
                raise ValueError('empty body')
            CACHE['enlil_image'] = data
            CACHE['enlil_image_updated'] = time.time()
            _persist('enlil_image')
            _diag_fetch('enlil', 'ok', started)
            print(f'[{time.strftime("%H:%M:%S")}] Enlil updated ({len(data)} bytes)')
            return
        except Exception as e:
            last_error = e
            print(f'[{time.strftime("%H:%M:%S")}] Enlil fetch failed ({url}): {e}')
    _diag_fetch('enlil', 'error', started, last_error)


def fetch_drap():
    """Fetch Aurora forecast (Northern Hemisphere) image"""
    urls = [
        'https://services.swpc.noaa.gov/images/aurora-forecast-northern-hemisphere.jpg',
        'https://services.swpc.noaa.gov/images/swx-overview-large.gif',
    ]
    # One record per call — see the note in fetch_dx.
    started = time.monotonic()
    last_error = None
    for url in urls:
        try:
            data, not_modified = _conditional_get(url, timeout=20)
            if not_modified:
                if _note_unchanged('Aurora', 'drap_image', url):
                    _diag_fetch('drap', 'not_modified', started)
                    return
                continue
            if not data:
                raise ValueError('empty body')
            CACHE['drap_image'] = data
            CACHE['drap_image_updated'] = time.time()
            _persist('drap_image')
            _diag_fetch('drap', 'ok', started)
            # 'Aurora', not 'DRAP': fetch_real_drap logs the D-RAP global map.
            # Two identical "DRAP updated" lines made the journal useless for
            # telling which of the two products actually landed.
            print(f'[{time.strftime("%H:%M:%S")}] Aurora updated ({len(data)} bytes)')
            return
        except Exception as e:
            last_error = e
            print(f'[{time.strftime("%H:%M:%S")}] Aurora fetch failed ({url}): {e}')
    _diag_fetch('drap', 'error', started, last_error)


def fetch_real_drap():
    """Fetch DRAP (D-Region Absorption Prediction) global image"""
    urls = [
        'https://services.swpc.noaa.gov/images/animations/d-rap/global/latest.png',
        'https://services.swpc.noaa.gov/images/d-rap/global_f10.png',
    ]
    # One record per call — see the note in fetch_dx.
    started = time.monotonic()
    last_error = None
    for url in urls:
        try:
            data, not_modified = _conditional_get(url, timeout=20)
            if not_modified:
                if _note_unchanged('DRAP', 'real_drap_image', url):
                    _diag_fetch('real_drap', 'not_modified', started)
                    return
                continue
            if not data:
                raise ValueError('empty body')
            CACHE['real_drap_image'] = data
            CACHE['real_drap_image_updated'] = time.time()
            _persist('real_drap_image')
            _diag_fetch('real_drap', 'ok', started)
            print(f'[{time.strftime("%H:%M:%S")}] DRAP updated ({len(data)} bytes)')
            return
        except Exception as e:
            last_error = e
            print(f'[{time.strftime("%H:%M:%S")}] DRAP fetch failed ({url}): {e}')
    _diag_fetch('real_drap', 'error', started, last_error)


def fetch_sdo():
    """Fetch the NASA SDO solar disk image into CACHE['solar_image'].

    Tier 1.3: this used to happen inline in the /api/solar-image handler,
    which made that endpoint the only one doing upstream I/O on a request
    thread — with a 20 s timeout that exactly equals the native client's
    IMAGE_TIMEOUT, no negative cache and no in-flight dedupe, so every
    request after a failure re-tried upstream and burned the client's entire
    serial image budget before it ever reached /api/muf-map.

    Assigns on success only: writing None over a good image would blank the
    SDO panel on a single transient upstream failure.
    """
    started = time.monotonic()
    try:
        data, not_modified = _conditional_get(SDO_URL, timeout=20)
        if not_modified:
            # False => a validator with no body behind it (the slot was
            # cleared after the validator was recorded). SDO has no fallback
            # URL to fall through to, so that is a miss, not a hit.
            held = _note_unchanged('SDO', 'solar_image', SDO_URL)
            _diag_fetch('sdo', 'not_modified' if held else 'error', started,
                        '304_no_body')
            return
        if not data:
            raise ValueError('empty body')
        CACHE['solar_image'] = data
        CACHE['solar_image_updated'] = time.time()
        _persist('solar_image')
        _diag_fetch('sdo', 'ok', started)
        print(f'[{time.strftime("%H:%M:%S")}] SDO updated ({len(data)} bytes)')
    except Exception as e:
        _diag_fetch('sdo', 'error', started, e)
        print(f'[{time.strftime("%H:%M:%S")}] SDO image fetch failed: {e}')


_NTP_HOSTNAME_RE = re.compile(r'^[a-z0-9\-\.]+$', re.IGNORECASE)


def _valid_ntp_hostname(s):
    if not s:
        return False
    s = s.strip()
    if not s:
        return False
    return ('.' in s) or bool(_NTP_HOSTNAME_RE.match(s))


def _parse_ntp_conf_line(path, keywords):
    """Parse a config file; return first token after any of `keywords` on a non-comment line."""
    try:
        with open(path, 'r') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or line.startswith(';'):
                    continue
                # strip inline comments
                for c in ('#', ';'):
                    idx = line.find(c)
                    if idx >= 0:
                        line = line[:idx].strip()
                if not line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                head = parts[0].lower()
                for kw in keywords:
                    if kw.endswith('='):
                        # NTP= style — may be joined or split
                        if parts[0].lower().startswith(kw.lower()):
                            # e.g. "NTP=time.example.com foo"
                            after = line.split('=', 1)[1].strip()
                            toks = after.split()
                            if toks and _valid_ntp_hostname(toks[0]):
                                return toks[0]
                    else:
                        if head == kw.lower() and len(parts) >= 2:
                            if _valid_ntp_hostname(parts[1]):
                                return parts[1]
    except Exception:
        pass
    return None


def get_host_ntp():
    """Return the host's active NTP server name, trying several sources."""
    try:
        # 1. timedatectl
        try:
            r = subprocess.run(
                ['timedatectl', 'show-timesync', '--property=ServerName', '--value'],
                capture_output=True, text=True, timeout=2
            )
            name = (r.stdout or '').strip()
            if _valid_ntp_hostname(name):
                return name
        except Exception:
            pass

        # 2. /etc/systemd/timesyncd.conf — look for NTP=
        val = _parse_ntp_conf_line('/etc/systemd/timesyncd.conf', ['NTP='])
        if val:
            return val

        # 3. chrony
        for p in ('/etc/chrony/chrony.conf', '/etc/chrony.conf'):
            val = _parse_ntp_conf_line(p, ['server', 'pool'])
            if val:
                return val

        # 4. ntpd
        val = _parse_ntp_conf_line('/etc/ntp.conf', ['server', 'pool'])
        if val:
            return val
    except Exception:
        pass

    # 5. Fallback
    return 'pool.ntp.org'


# Tier 1.3: boot order for the five image products. 'real-drap' is FIRST
# because it backs the propagation panel's DEFAULT tab (PROP_TAB_IMAGE_KEY
# ['drap'] == 'real-drap') — it used to be fetched last, ~56 s after the
# client's one and only image refresh had already given up. fetch_muf is last
# because it is by far the slowest (365 KB SVG + a multi-second rasterize).
# The CACHE key is the one the fast-retry probes for emptiness.
_IMAGE_FETCH_ORDER = (
    ('real_drap_image', 'real_drap'),
    ('drap_image', 'drap'),
    ('enlil_image', 'enlil'),
    ('solar_image', 'sdo'),
    # Probe the SVG slot, not muf_image_png: a failed rasterize is a
    # deterministic timeout, not a transient, so re-running it twice more at
    # 45 s a go would just burn the boot window.
    ('muf_image', 'muf'),
)

# Two extra passes, 20 s apart. Bounded on purpose: unbounded retries here
# would be indistinguishable from the refetch storm this tier removes.
IMAGE_FAST_RETRY_PASSES = 2
IMAGE_FAST_RETRY_SLEEP_S = 20

# Five image products on a 900 s cadence => a 180 s comb. Evenly staggering
# the refreshes keeps two large decodes off the same tick; a simultaneous
# refresh transiently doubles image memory and can tip a 512MB Pi into an OOM
# kill. (Was a 4-slot 225 s comb before fetch_sdo joined the loop.)
IMAGE_STAGGER_S = 180


def _image_fetchers():
    """(cache_key, fetch_fn) pairs in boot order.

    Resolved lazily by name so tests (and any future wrapper) can monkeypatch
    server.fetch_* and have the boot chain honour it.
    """
    g = globals()
    return [(key, g['fetch_' + name]) for key, name in _IMAGE_FETCH_ORDER]


def _image_stagger_offsets(now0):
    """Initial last-fetch stamps for the five image products.

    The product fetched FIRST at boot is the oldest, so it gets the earliest
    comb slot (now0 - 4*IMAGE_STAGGER_S => next refresh in 180 s) and the
    slowest/last one keeps the full 900 s.
    """
    n = len(_IMAGE_FETCH_ORDER)
    return {name: now0 - (n - 1 - i) * IMAGE_STAGGER_S
            for i, (_key, name) in enumerate(_IMAGE_FETCH_ORDER)}


# Resolve once at import so a typo in _IMAGE_FETCH_ORDER refuses to start the
# service, instead of killing background_fetcher on a daemon thread mid-boot
# and leaving the dashboard frozen on empty data with only a stderr traceback.
_image_fetchers()


def _image_fast_retry(passes=IMAGE_FAST_RETRY_PASSES,
                      sleep_s=IMAGE_FAST_RETRY_SLEEP_S):
    """Retry only the image products whose CACHE slot is still empty.

    Without this a single boot-time failure (network not up yet) left that
    panel blank until the next 900 s cadence tick.
    """
    for _ in range(passes):
        missing = [(key, fn) for key, fn in _image_fetchers()
                   if not CACHE.get(key)]
        if not missing:
            return
        time.sleep(sleep_s)
        for key, fn in missing:
            if not CACHE.get(key):
                fn()


def background_fetcher():
    """Background thread to periodically fetch data"""
    fetch_hamqsl()
    fetch_dx()

    # Fast retry if initial fetch failed (network might not be ready yet).
    # Hoisted ABOVE the image fetches: those are five upstream round trips
    # plus a rasterize, so leaving the recovery path below them delayed the
    # first solar/DX retry by ~100 s on exactly the boot where the network
    # was not ready at ExecStart.
    for _ in range(6):
        if CACHE['solar'] and CACHE['dxspots']:
            break
        time.sleep(10)
        if not CACHE['solar']:
            fetch_hamqsl()
        if not CACHE['dxspots']:
            fetch_dx()

    for _key, fn in _image_fetchers():
        fn()
    _image_fast_retry()

    solar_interval = 300   # 5 minutes
    dx_interval = 120      # 2 minutes
    image_interval = 900   # 15 minutes
    now0 = time.time()
    last_solar = now0
    last_dx = now0
    stagger = _image_stagger_offsets(now0)
    last_muf = stagger['muf']
    last_enlil = stagger['enlil']
    last_drap = stagger['drap']
    last_real_drap = stagger['real_drap']
    last_sdo = stagger['sdo']

    while True:
        try:
            time.sleep(10)
            now = time.time()
            if now - last_solar >= solar_interval:
                fetch_hamqsl()
                last_solar = now
            if now - last_dx >= dx_interval:
                fetch_dx()
                last_dx = now
            if now - last_muf >= image_interval:
                fetch_muf()
                last_muf = now
            if now - last_enlil >= image_interval:
                fetch_enlil()
                last_enlil = now
            if now - last_drap >= image_interval:
                fetch_drap()
                last_drap = now
            if now - last_real_drap >= image_interval:
                fetch_real_drap()
                last_real_drap = now
            if now - last_sdo >= image_interval:
                fetch_sdo()
                last_sdo = now
        except Exception as e:
            # Never let the loop die silently — that would freeze the
            # dashboard on stale data forever. Log and keep going.
            print(f'[{time.strftime("%H:%M:%S")}] background loop error: {e}')
            time.sleep(10)


def _age_of(stamp_key, now):
    """Seconds since CACHE[stamp_key], or -1 if it was never stamped.

    Clamped at 0: the Pi 1 has no RTC, so a persisted epoch written after an
    NTP sync can legitimately be in the future relative to a fresh boot's
    fake-hwclock time, and a negative age renders as garbage.
    """
    stamp = CACHE.get(stamp_key) or 0
    if not stamp:
        return -1
    return max(0, int(now - stamp))


# ---------------------------------------------------------------------------
# GET /api/diagnostics — the read side of the counters above
#
# Read-only, and it must NEVER block: no upstream fetch, no subprocess, no
# import of cairosvg. (Doing upstream I/O on a handler thread is the mistake
# that was just removed from /api/solar-image; it burned the native client's
# entire serial image budget on one 20 s stall.) Everything here is either a
# counter we already keep or a single small /proc read.
#
# The client embeds this body verbatim under "server" in the diagnostics
# payload, and that payload is only ever transmitted by an explicit operator
# press. Nothing in this structure is a credential, a network name, or file
# content from outside this project: failures are recorded as exception TYPE
# names (see _diag_error_name), and no URL, hostname or path is echoed.
# ---------------------------------------------------------------------------

_DEPS_SNAPSHOT = None


def _deps_snapshot():
    """cairosvg + cpulimit availability. Computed once, then memoised.

    Deliberately does NOT import cairosvg. The rasterize runs in a subprocess
    precisely because that import costs ~48 MB RSS on a 512 MB box, and a
    diagnostics request must never make the long-lived server pay it.
    importlib.util.find_spec and importlib.metadata answer both questions from
    the filesystem without executing the package.

    Memoised because the answer cannot change without a restart (installing
    cairosvg under a running server does not make the render work — the child
    process is what imports it), and because find_spec walks sys.path.
    """
    global _DEPS_SNAPSHOT
    if _DEPS_SNAPSHOT is not None:
        return _DEPS_SNAPSHOT
    info = {'cairosvg': False, 'cairosvg_version': None, 'cpulimit': False}
    try:
        import importlib.util
        info['cairosvg'] = importlib.util.find_spec('cairosvg') is not None
    except Exception:
        # ModuleNotFoundError/ValueError from a broken sys.path entry.
        info['cairosvg'] = False
    if info['cairosvg']:
        try:
            from importlib.metadata import version
            info['cairosvg_version'] = version('cairosvg')
        except Exception:
            info['cairosvg_version'] = None
    try:
        import shutil
        # The bool only. The resolved path would leak the account name if
        # cpulimit ever came off a PATH entry under /home.
        info['cpulimit'] = shutil.which('cpulimit') is not None
    except Exception:
        pass
    _DEPS_SNAPSHOT = info
    return info


def _proc_self_status():
    """(VmRSS kB, thread count) from /proc/self/status.

    Either value is None when the field is missing or the file cannot be
    read — a non-Linux dev box, or a container with a hidden /proc. Guarded
    because an endpoint that can 500 is worse than one that reports null.
    """
    rss = None
    threads = None
    try:
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    rss = int(line.split()[1])
                elif line.startswith('Threads:'):
                    threads = int(line.split()[1])
                if rss is not None and threads is not None:
                    break
    except Exception:
        pass
    return rss, threads


def _diagnostics_snapshot():
    """Build the /api/diagnostics body. Pure reads; never raises."""
    now = time.time()
    png = CACHE.get('muf_image_png')
    svg = CACHE.get('muf_image')
    ewma = _muf_render_ewma

    # dict(stat) copies a fixed-key dict the fetcher thread may be writing
    # values into. Safe: values change, the key set never does, so no resize
    # can happen mid-copy.
    fetch = {}
    for name in _FETCH_NAMES:
        fetch[name] = dict(_FETCH_STATS[name])

    cache = {}
    for product, payload_key, stamp_key in _DIAG_PRODUCTS:
        body = CACHE.get(payload_key)
        cache[payload_key] = {
            'bytes': len(body) if body else 0,
            'age_s': _age_of(stamp_key, now),
            'from_disk': payload_key in _DISK_RESTORED,
            # 304 vs full is the field measurement of Tier 2.2's conditional
            # GET: on a working install the 304 count should dominate.
            'http_304': fetch[product]['not_modified'],
            'http_full': fetch[product]['ok'],
        }

    rss_kb, threads = _proc_self_status()

    return {
        'schema': 1,
        'now': now,
        'muf': {
            # Seconds of the last SUCCESSFUL rasterize, the smoothed cost the
            # adaptive budget is derived from, and the budget itself. If
            # timeout_s is pinned at timeout_floor_s while rasterize_timeout
            # climbs, PHASE2_TIMEOUT_S is too low for this hardware — the
            # EWMA only learns from renders that finish.
            'last_render_s': _MUF_LAST_RENDER_S,
            'render_ewma_s': round(ewma, 3) if ewma else None,
            'timeout_s': _muf_timeout(),
            'timeout_floor_s': PHASE2_TIMEOUT_S,
            'timeout_max_s': PHASE2_TIMEOUT_MAX_S,
            'timeout_factor': MUF_TIMEOUT_FACTOR,
            'last_timeout_budget_s': _MUF_LAST_TIMEOUT_BUDGET_S,
            'rasterize_ok': _MUF_RASTERIZE_OK,
            'rasterize_timeout': _MUF_RASTERIZE_TIMEOUT,
            'rasterize_fail': _MUF_RASTERIZE_FAIL,
            'slim_ok': _MUF_SLIM_OK,
            'slim_declined': _MUF_SLIM_DECLINED,
            # Which rasterizer actually produced the PNG. The field report that
            # exposed cairosvg never completing could not say which engine ran,
            # because there was only one; with a ladder the next report must.
            'engine_used': _MUF_ENGINE_USED,
            'engine_absent': _MUF_ENGINE_ABSENT,
            'engines': [n for n, _ in MUF_ENGINES],
            'unslimmed_retries': _MUF_UNSLIMMED_RETRIES,
            'svg_bytes': _MUF_LAST_SVG_BYTES,
            'slim_bytes': _MUF_LAST_SLIM_BYTES,
            'png_bytes': _MUF_LAST_PNG_BYTES,
            # Where the PNG we hold came from vs what the endpoint last
            # served. 'disk' + 'png' means the map on screen is a restored
            # one, which is also what /api/health labels it.
            'png_source': _MUF_PNG_SOURCE if png else 'none',
            'last_served': _MUF_LAST_SERVED,
            'svg_cached_bytes': len(svg) if svg else 0,
            'svg_age_s': _age_of('muf_image_updated', now),
            'png_cached_bytes': len(png) if png else 0,
            'png_age_s': _age_of('muf_image_png_updated', now),
            'stale_max_s': MUF_STALE_MAX_S,
        },
        'deps': _deps_snapshot(),
        'cache': cache,
        'fetch': fetch,
        'boot': {
            'started_at': _BOOT_EPOCH,
            'uptime_s': int(max(0.0, time.monotonic() - _BOOT_MONOTONIC)),
            'disk_warm': _BOOT_DISK_WARM,
            'restored': list(_DISK_RESTORED),
            # Seconds from process start to the first fetch that produced
            # usable data (a 304 counts — it means the panel has current
            # data). null = it has never succeeded.
            'first_ok_s': dict(_FIRST_OK_S),
        },
        'process': {
            'rss_kb': rss_kb,
            'threads': threads,
            'python': '%d.%d.%d' % sys.version_info[:3],
        },
    }


class Handler(SimpleHTTPRequestHandler):
    # Socket timeout so a stalled client can't pin a thread (and its buffered
    # response) forever — unbounded stuck threads are a memory-pressure source.
    timeout = 30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    # API paths a HEAD must never be dispatched into, because serving them
    # performs unbounded upstream network I/O on the handler thread. Every
    # other branch is a pure cache/static read and honours self.command
    # == 'HEAD' by suppressing the body, so delegating is safe for those.
    _HEAD_UNSAFE_PREFIXES = ('/api/callsign/',)

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path.startswith(self._HEAD_UNSAFE_PREFIXES):
            # A header-only probe has no business firing two 8 s upstream
            # callbook lookups.
            self.send_error(405, 'HEAD not supported for callsign lookup')
            return
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/solar':
            # Tier 2c perf: 304 conditional GET. ~80% of polls land on
            # unchanged data (5 min upstream cadence vs ~60 s client poll);
            # a 304 + empty body lets the client skip json.loads entirely.
            etag = _etag_for('solar_updated')
            inm = self.headers.get('If-None-Match', '')
            if inm == etag and etag != '"0.000"':
                self.send_response(304)
                self.send_header('ETag', etag)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return
            # Tier 1b perf: prefer the pre-encoded bytes; fall back to the dict
            # (and let send_json re-encode) until the first fetch completes.
            self.send_json_with_etag(
                CACHE.get('solar_bytes') or (CACHE.get('solar') or {}),
                etag)
        elif path == '/api/bands':
            etag = _etag_for('bands_updated')
            inm = self.headers.get('If-None-Match', '')
            if inm == etag and etag != '"0.000"':
                self.send_response(304)
                self.send_header('ETag', etag)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return
            self.send_json_with_etag(
                CACHE.get('bands_bytes') or (CACHE.get('bands') or {}),
                etag)
        elif path == '/api/dxspots':
            etag = _etag_for('dx_updated')
            inm = self.headers.get('If-None-Match', '')
            if inm == etag and etag != '"0.000"':
                self.send_response(304)
                self.send_header('ETag', etag)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return
            self.send_json_with_etag(
                CACHE.get('dxspots_bytes') or (CACHE.get('dxspots') or []),
                etag)
        elif path == '/api/solar-image':
            # Tier 1.3: pure cache read. background_fetcher owns the SDO
            # refresh (fetch_sdo); doing it here made this the one endpoint
            # that could stall a handler thread for 20 s — the client's whole
            # image budget — and re-tried upstream on EVERY request after a
            # failure, because the age guard stays true forever once the slot
            # is empty. Single snapshot read so the fetcher thread cannot swap
            # the slot between the test and the write.
            img = CACHE.get('solar_image')
            if img:
                self.send_binary(img, 'image/jpeg')
            else:
                self.send_error(503, 'Solar image not yet loaded')
        elif path.startswith('/api/muf-map'):
            # Phase 2 / Tier 1.5: prefer the pre-rasterized PNG (the native
            # pygame client blits it directly). The SVG fallback exists only
            # for the browser dashboard — handing 365 KB of SVG to the native
            # client makes SDL/nanosvg decode a 5.5 MB greyscale surface on
            # the render thread (a 3-5 s freeze on ARMv6), so a client that
            # asks for ?fmt=png gets PNG or 503, never SVG.
            #
            # Negotiation keys off the query string, NOT Accept: index.html
            # requests via <img src>, whose Accept header we do not control,
            # and it already cache-busts with '?t=' (index.html:842).
            want_png = 'png' in parse_qs(urlparse(self.path).query).get('fmt', ())
            png = CACHE.get('muf_image_png')
            svg = CACHE.get('muf_image')
            if png:
                body = png
                ctype = 'image/png'
                _note_muf_served('png')
            elif want_png:
                _note_muf_served('none')
                self.send_error(503, 'MUF PNG not yet rendered')
                return
            elif svg:
                body = svg
                ctype = 'image/svg+xml'
                _note_muf_served('svg')
            else:
                _note_muf_served('none')
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
        elif path.startswith('/api/enlil'):
            if CACHE.get('enlil_image'):
                self.send_binary(CACHE['enlil_image'], 'image/jpeg')
            else:
                self.send_error(503, 'Enlil image not yet loaded')
        elif path.startswith('/api/real-drap'):
            if CACHE.get('real_drap_image'):
                self.send_binary(CACHE['real_drap_image'], 'image/png')
            else:
                self.send_error(503, 'DRAP image not yet loaded')
        elif path.startswith('/api/drap'):
            if CACHE.get('drap_image'):
                self.send_binary(CACHE['drap_image'], 'image/jpeg')
            else:
                self.send_error(503, 'Aurora image not yet loaded')
        elif path.startswith('/api/callsign/'):
            call = path.split('/')[-1].upper()
            result = lookup_callsign(call)
            self.send_json(result)
        elif path == '/api/ntp':
            if CACHE.get('host_ntp') is None:
                CACHE['host_ntp'] = get_host_ntp()
            self.send_json({'ntp': CACHE['host_ntp']})
        elif path == '/api/health':
            # Tier 2.1/2.5: the image ages are how the clients label a
            # persisted-stale picture. Neither client can read response
            # headers — the browser gets these via <img src> and
            # hamclock_data._fetch_binary discards them — so /api/health is
            # the only channel. Serving a day-old map unlabelled is worse
            # than serving nothing to an operator making a band decision.
            now = time.time()
            self.send_json({
                'status': 'ok',
                'solar_age': _age_of('solar_updated', now),
                'bands_age': _age_of('bands_updated', now),
                'dx_age': _age_of('dx_updated', now),
                'muf_age': _age_of('muf_image_png_updated', now),
                # 'live' (rendered by this process), 'disk' (restored from the
                # cache dir at boot) or 'none'.
                'muf_source': (_MUF_PNG_SOURCE if CACHE.get('muf_image_png')
                               else 'none'),
                'sdo_age': _age_of('solar_image_updated', now),
                'enlil_age': _age_of('enlil_image_updated', now),
                'drap_age': _age_of('drap_image_updated', now),
                'real_drap_age': _age_of('real_drap_image_updated', now),
            })
        elif path == '/api/diagnostics':
            # Opt-in diagnostics: a read-only snapshot of counters this
            # process already keeps. No upstream I/O, no subprocess, no
            # cairosvg import — see _diagnostics_snapshot. The client embeds
            # this body under "server" and transmits it only when the operator
            # presses the button.
            self.send_json(_diagnostics_snapshot())
        else:
            if self.command == 'HEAD':
                super().do_HEAD()
            else:
                super().do_GET()

    def send_json(self, data):
        # Tier 1b perf: accept pre-encoded JSON bytes directly so the hot
        # polling endpoints (/api/solar, /api/bands, /api/dxspots) can skip
        # json.dumps on every request. The cached bytes are built once per
        # fetch in fetch_hamqsl / fetch_dx. Live dicts still re-encode here
        # (e.g. /api/health which has dynamic age fields).
        if isinstance(data, (bytes, bytearray)):
            body = data
        else:
            body = json.dumps(data, separators=(',', ':')).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_json_with_etag(self, data, etag):
        """Like send_json, but also emit an ETag header.

        Tier 2c perf: used by /api/{solar,bands,dxspots} so the client can
        replay it as If-None-Match on the next poll and short-circuit to
        304 when nothing has changed.
        """
        if isinstance(data, (bytes, bytearray)):
            body = data
        else:
            body = json.dumps(data, separators=(',', ':')).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_binary(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(data))
        self.send_header('Access-Control-Allow-Origin', '*')
        # no-store: prevents the browser image cache from growing unbounded.
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def log_message(self, format, *args):
        pass  # Suppress request logs for performance


if __name__ == '__main__':
    print(f'HamClock Lite starting on port {PORT}...')
    # Tier 2.1: restore BEFORE the fetcher thread starts and BEFORE the socket
    # binds. Both orderings matter — after the thread, a boot fetch could
    # publish a fresh image and then be overwritten by the older disk copy;
    # after the bind, the client's very first image request could arrive
    # while CACHE is still empty and take a 503 we already had the answer to.
    _load_persisted()
    t = threading.Thread(target=background_fetcher, daemon=True)
    t.start()
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Server ready: http://localhost:{PORT}')
    server.serve_forever()
SERVEREOF
sudo chmod +x "$INSTALL_DIR/server.py"

# ── Step 4: Write embedded index.html ───────────────────────────────
echo "Writing index.html..."
sudo tee "$INSTALL_DIR/index.html" > /dev/null << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HamClock Lite</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
--bg:#0a0e14;--card:#111820;--border:#1a2530;--text:#c8d0d8;--bright:#e8f0f0;
--green:#22c55e;--yellow:#eab308;--red:#ef4444;--cyan:#06b6d4;--muted:#4a5568;--label:#6b7b8d;--callsign:#f472b6;
}
html,body{
width:100%;height:100vh;overflow:hidden;
background:var(--bg);color:var(--text);
font-family:'Courier New','Liberation Mono',monospace;
font-size:clamp(16px,1.4vh,16px);
}
.hdr{
display:flex;align-items:center;justify-content:space-between;
height:clamp(20px,3vh,30px);
padding:0 clamp(4px,0.8vw,12px);
background:var(--card);border-bottom:1px solid var(--border);
font-size:clamp(14px,1.3vh,14px);
}
.hdr-title{color:var(--cyan);font-weight:bold;letter-spacing:2px}
.powercat{height:clamp(18px,2.5vh,24px);width:auto;vertical-align:middle;margin-right:clamp(6px,0.8vw,10px);color:var(--cyan);display:none}
body.theme-kstate .powercat{display:inline-block}
.hdr-clocks{color:var(--bright);letter-spacing:1px}
.hdr-clocks span{margin-left:clamp(8px,2vw,24px)}
.hdr-utc{color:var(--cyan)}
.hdr-dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#22c55e;margin-left:clamp(8px,1.5vw,16px);animation:blink-status 1.2s steps(2,start) infinite}
@keyframes blink-status{50%{opacity:0}}
.grid{
display:grid;
grid-template-columns:20vw 1fr 25vw;
gap:clamp(2px,0.4vh,6px);
padding:clamp(2px,0.4vh,6px);
height:calc(100vh - clamp(20px,3vh,30px) - clamp(16px,2.5vh,28px));
overflow:hidden;
}
.col{display:flex;flex-direction:column;gap:clamp(2px,0.4vh,6px);overflow:hidden;min-height:0}
.panel{
background:var(--card);border:1px solid var(--border);
overflow:hidden;position:relative;
display:flex;flex-direction:column;
}
.panel-title{
display:flex;justify-content:space-between;align-items:center;
padding:2px 6px;
background:var(--bg);
font-size:clamp(13px,1.1vh,13px);
color:var(--label);letter-spacing:1px;
border-bottom:1px solid var(--border);
flex-shrink:0;
}
.panel-body{padding:4px 6px;flex:1;overflow:hidden}
.solar-flex .panel-body{overflow-y:auto}
.solar-flex .panel-body::-webkit-scrollbar{width:0;background:transparent}
.timer{color:var(--muted);font-size:clamp(11px,0.9vh,11px)}
.tab{cursor:pointer;padding:0 8px;color:var(--label);user-select:none}
.tab-active{color:var(--cyan);font-weight:bold}
.solar-flex{flex:1;min-height:0}
.bands-flex{flex:0 0 auto}
.mid-img{flex:0 0 auto;min-height:0;overflow:hidden}
.dx-full{flex:1;min-height:0}
.s-row{
display:flex;justify-content:space-between;align-items:center;
padding:clamp(0px,0.12vh,2px) 0;
border-bottom:1px solid var(--border);
}
.s-lbl{color:var(--label);font-size:clamp(12px,1vh,12px);flex:0 0 clamp(40px,5vw,70px)}
.s-val{color:var(--bright);font-size:clamp(14px,1.2vh,14px);font-weight:bold;text-align:right;flex:1}
.kp-wrap{display:flex;align-items:center;gap:clamp(2px,0.3vw,6px)}
.kp-bar{height:clamp(6px,0.8vh,10px);background:var(--bg);flex:1;overflow:hidden}
.kp-fill{height:100%}
.band-row{
display:flex;align-items:center;
padding:clamp(1px,0.15vh,2px) 0;
border-bottom:1px solid var(--border);
font-size:clamp(12px,1vh,12px);
}
.band-name{flex:0 0 clamp(40px,5vw,60px);color:var(--bright);font-weight:bold}
.band-cond{
flex:0 0 clamp(16px,2vw,24px);
text-align:center;
font-weight:bold;
font-size:clamp(12px,1vh,12px);
margin:0 clamp(2px,0.3vw,4px);
padding:clamp(0px,0.1vh,1px) 0;
}
.band-lbl{color:var(--label);font-size:clamp(10px,0.8vh,10px);flex:0 0 clamp(24px,3vw,36px);text-align:center}
.cG{background:#22c55e;color:#000}.cF{background:#eab308;color:#000}.cP{background:#ef4444;color:#fff}.cN{background:var(--muted);color:#fff}
.img-wrap{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;min-height:0}
.img-wrap img{object-fit:contain;max-width:100%;max-height:100%;display:block}
#imgSolar{height:22vh;width:100%;object-fit:contain}
#imgMuf{height:auto;width:100%;max-height:90vh;object-fit:contain}
#imgEnlil{width:100%;object-fit:contain}
#imgDrap{width:100%;object-fit:contain}
.dx-tbl{width:100%;border-collapse:collapse}
.dx-tbl th{
font-size:clamp(12px,1vw,12px);color:var(--label);
text-align:left;padding:clamp(1px,0.2vh,3px) clamp(2px,0.3vw,6px);
border-bottom:1px solid var(--border);
position:sticky;top:0;background:var(--card);
letter-spacing:1px;
}
.dx-tbl td{
padding:clamp(1px,0.15vh,2px) clamp(2px,0.3vw,6px);
border-bottom:1px solid var(--border);
font-size:clamp(13px,1.2vw,13px);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.dx-freq{color:var(--label);font-size:clamp(13px,1.2vw,13px)}
.dx-band{color:var(--cyan);font-size:clamp(12px,1.1vw,12px)}
.dx-call{color:var(--bright);font-weight:bold;font-size:clamp(13px,1.2vw,13px)}
.dx-sp{color:var(--muted);font-size:clamp(12px,1.1vw,12px)}
.dx-tm{color:var(--muted);font-size:clamp(12px,1.1vw,12px)}
.dx-body-wrap{flex:1;overflow:hidden;min-height:0}
.sbar{
display:flex;align-items:center;justify-content:space-between;
height:clamp(16px,2.5vh,28px);
padding:0 clamp(6px,1vw,20px);
background:var(--card);border-top:1px solid var(--border);
font-size:clamp(11px,0.9vh,11px);color:var(--muted);
flex-shrink:0;
}
.stale{opacity:0.5}
.stale::after{content:' (stale)';color:var(--yellow)}
/* Setup screen */
#setup{
position:fixed;inset:0;z-index:9999;
background:var(--bg);
display:flex;align-items:center;justify-content:center;
font-family:'Courier New','Liberation Mono',monospace;
}
.setup-card{
background:var(--card);
border:1px solid var(--border);
padding:24px 32px;
max-width:460px;
width:90%;
}
.setup-title{
color:var(--cyan);
font-size:18px;
font-weight:bold;
letter-spacing:3px;
text-align:center;
margin-bottom:20px;
}
.setup-field{margin-bottom:14px}
.setup-label{
display:block;
color:var(--label);
font-size:10px;
letter-spacing:1px;
margin-bottom:4px;
}
.setup-input{
width:100%;
background:var(--bg);
border:1px solid var(--border);
color:var(--bright);
padding:6px 10px;
font-family:inherit;
font-size:14px;
}
.setup-input:focus{border-color:var(--cyan);outline:none}
.setup-select{
width:100%;
background:var(--bg);
border:1px solid var(--border);
color:var(--bright);
padding:6px 10px;
font-family:inherit;
font-size:13px;
}
.theme-row{display:flex;gap:12px}
.theme-opt{flex:1;text-align:center;cursor:pointer}
.theme-opt input{display:none}
.theme-swatch{
display:block;
height:32px;
border:2px solid var(--border);
margin-bottom:4px;
}
.theme-opt input:checked + .theme-swatch{border-color:var(--bright)}
.theme-name{font-size:10px;color:var(--label)}
.setup-btn{
display:block;
width:100%;
margin-top:20px;
padding:10px;
background:var(--cyan);
color:var(--bg);
border:none;
font-family:inherit;
font-size:14px;
font-weight:bold;
letter-spacing:2px;
cursor:pointer;
}
</style>
</head>
<body>
<!-- Setup wizard overlay -->
<div id="setup" style="display:none">
<div class="setup-card">
<div class="setup-title">HAMCLOCK LITE SETUP</div>
<div class="setup-field">
<label class="setup-label">YOUR CALLSIGN</label>
<input type="text" id="inCallsign" class="setup-input" placeholder="W1ABC" style="text-transform:uppercase">
<span id="callLookupName" style="font-size:10px;color:var(--green);display:block;margin-top:2px;min-height:14px"></span>
</div>
<div class="setup-field">
<label class="setup-label">GRID SQUARE</label>
<input type="text" id="inGrid" class="setup-input" placeholder="FN31" maxlength="6" style="text-transform:uppercase">
</div>
<div class="setup-field">
<label class="setup-label">TIMEZONE</label>
<select id="inTimezone" class="setup-select">
<option value="auto">Auto (Browser)</option>
<option value="UTC">UTC</option>
<option value="US/Eastern">US/Eastern</option>
<option value="US/Central">US/Central</option>
<option value="US/Mountain">US/Mountain</option>
<option value="US/Pacific">US/Pacific</option>
<option value="US/Alaska">US/Alaska</option>
<option value="US/Hawaii">US/Hawaii</option>
<option value="Europe/London">Europe/London</option>
<option value="Europe/Berlin">Europe/Berlin</option>
<option value="Europe/Paris">Europe/Paris</option>
<option value="Europe/Moscow">Europe/Moscow</option>
<option value="Asia/Tokyo">Asia/Tokyo</option>
<option value="Asia/Shanghai">Asia/Shanghai</option>
<option value="Asia/Kolkata">Asia/Kolkata</option>
<option value="Australia/Sydney">Australia/Sydney</option>
</select>
</div>
<div class="setup-field">
<label class="setup-label">COLOR THEME</label>
<div class="theme-row">
<label class="theme-opt">
<input type="radio" name="theme" value="classic" checked>
<span class="theme-swatch" style="background:#0a0e14;border-bottom:3px solid #22c55e"></span>
<span class="theme-name">CLASSIC</span>
</label>
<label class="theme-opt">
<input type="radio" name="theme" value="amber">
<span class="theme-swatch" style="background:#1a1000;border-bottom:3px solid #f59e0b"></span>
<span class="theme-name">AMBER</span>
</label>
<label class="theme-opt">
<input type="radio" name="theme" value="blue">
<span class="theme-swatch" style="background:#0a0f1e;border-bottom:3px solid #60a5fa"></span>
<span class="theme-name">BLUE</span>
</label>
<label class="theme-opt">
<input type="radio" name="theme" value="red">
<span class="theme-swatch" style="background:#1a0a0a;border-bottom:3px solid #f87171"></span>
<span class="theme-name">RED</span>
</label>
<label class="theme-opt" id="themeKstate" style="display:none">
<input type="radio" name="theme" value="kstate">
<span class="theme-swatch" style="background:#512888;border-bottom:3px solid #FFFFFF"></span>
<span class="theme-name">K-STATE</span>
</label>
</div>
</div>
<div class="setup-field">
<label class="setup-label">TIME SERVER (NTP)</label>
<input type="text" id="inNtp" class="setup-input" placeholder="(using host NTP)">
</div>
<button class="setup-btn" onclick="saveSetup()">START</button>
</div>
</div>
<!-- Dashboard -->
<div id="dashboard">
<div class="hdr">
<span>
<svg class="powercat" viewBox="0 0 300 222.84" aria-hidden="true"><path fill="currentColor" d="M299.82 58.88c-5 16-13.15 30.68-23.6 44.37-3.31-.23-6.62-1-9.92-1.46-2.62 5.85-6.15 11.61-11.62 16.15a1.15 1.15 0 01-1.38-.23c-1.31-1.85.46-4 .62-6a21.07 21.07 0 00-4.46-18.07 22.56 22.56 0 00-9.92-5.64c-15.54-3-29.76 5.26-38.45 17.94-26.61-8.38-59.14-15.3-88.82-11.68-40.76 5.84-81.51 18.14-106.81 54.82A105.5 105.5 0 000 158.39v-139c11.15-4.49 22.76-7.72 34.6-10.64C46.91 24.28 63.83 34.58 82.43 37c13.92 1.81 27.57-4.19 33.92-16.8A30.9 30.9 0 00119.42.82a268.58 268.58 0 0157.75 1.77A175.28 175.28 0 01208.39 10a130.16 130.16 0 0115.38 6.22c.62.3 1.08.62 1 1.31s-.3 1.32-.92 1.31c-6.63-.17-12.84-.46-19.3-.77a1.85 1.85 0 00-1.85.85c-.28.43.48.69.93.76a342.15 342.15 0 0145.21 9.85 153.28 153.28 0 0148.75 24.6c1.41 1.06 2.93 2.68 2.23 4.75zM77.13 29.81c8.23 2.16 18.45 1.54 25.45-3.07 7.77-5 12-16.23 10.76-25.45H37.22C47.37 15.05 61 26.51 77.13 29.81zm29.14 181.1c-12.07-15.23-14.38-39.3-5.38-56.75 8.84-20 29.84-30.07 50-32.68 14.38-2.16 29.46-.46 43.37 2.15a59.86 59.86 0 013.23-12.84c-6.61-1.54-13.15-3.46-19.84-4.77-19.37-3.77-40-6.38-60.36-4.23-19.69 2.08-38.45 7.08-55.06 16.92-12.38 6.69-24.91 17.92-29.84 31.22-2.15 5.84-4.07 11.84-4.07 18.45.23 19.92 13.46 39.07 31.76 47.37 11.45 5.85 25.14 8.85 38.75 6 4.69-1 9.39-2 13.38-4.31a69.83 69.83 0 01-5.94-6.53zM239.3 152c2.54-5.61 4.77-11.79 5.08-18.25-.23-.77-1-.84-1.69-.92-5.38 7.46-12.71 13.93-21.22 16.53-3.77 1.15-9.43 1.94-13.23.85-8.31-2.39-13.77-11.23-15-19.31a58.7 58.7 0 00-9.53-1.76c-20.3-2.08-40.91 3.15-52.52 20.76-7.46 11.84-6.2 28.85.38 41.52a26.08 26.08 0 009.92 10.46c1.69-5.15 5.46-9.84 9.92-13.38a34.65 34.65 0 0118-5.84c11.76-.39 22.68 6.61 28 17.22 15-6.84 29.76-16.62 41.6-29.37a66.59 66.59 0 008.3-10.31 76.48 76.48 0 00-8.01-8.2z"/></svg>
<span class="hdr-title">HAMCLOCK LITE</span>
<span id="hdrCallsign" style="color:var(--callsign);margin-left:12px;cursor:pointer"></span>
</span>
<span class="hdr-clocks">
<span class="hdr-utc" id="utc">UTC --:--:--</span>
<span id="lcl">LOCAL --:--:--</span>
</span>
<span class="hdr-dot" id="statusDot"></span>
</div>
<div class="grid">
<div class="col">
<div class="panel solar-flex">
<div class="panel-title"><span>SOLAR</span><span class="timer" id="tmSolar"></span></div>
<div class="panel-body" id="solarPanel">Loading...</div>
</div>
<div class="panel bands-flex">
<div class="panel-title"><span>BANDS</span><span class="timer" id="tmBands"></span></div>
<div class="panel-body" id="bandsPanel">Loading...</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>SDO IMAGE</span><span class="timer" id="tmSolarImg"></span></div>
<div class="img-wrap"><img id="imgSolar" src="/api/solar-image" alt="SDO"></div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>GEOMAGNETIC</span></div>
<div class="panel-body" id="geomagBody" style="padding:4px 6px">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for data...</span>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>X-RAY FLUX</span></div>
<div class="panel-body" id="xrayBody" style="padding:4px 6px">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for data...</span>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>OPEN BANDS</span></div>
<div class="panel-body" id="openBandsBody" style="padding:4px 6px;overflow-y:auto">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for data...</span>
</div>
</div>
</div>
<div class="col">
<div class="panel" style="flex:1">
<div class="panel-title"><span>MUF MAP</span><span class="timer" id="tmMuf"></span></div>
<div class="img-wrap"><img id="imgMuf" src="/api/muf-map" alt="MUF"></div>
</div>
</div>
<div class="col">
<!-- Right column: DX, band activity, x-ray, geomag, open bands -->
<div class="panel" style="flex:0 0 auto;max-height:18vh;min-height:0">
<div class="panel-title"><span>DX SPOTS</span><span class="timer" id="tmDx"></span></div>
<div class="panel-body dx-body-wrap" style="padding:0;overflow-y:auto;max-height:calc(18vh - 22px)">
<table class="dx-tbl"><thead><tr><th>FREQ</th><th>B</th><th>DX CALL</th><th>DE</th><th>UTC</th></tr></thead><tbody id="dxBody"><tr><td colspan="5" style="color:var(--muted);padding:8px">Loading...</td></tr></tbody></table>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>BAND ACTIVITY</span></div>
<div class="panel-body" id="bandActivity" style="padding:4px 6px;overflow-y:auto">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for DX data...</span>
</div>
</div>
<div class="panel" style="flex:1;min-height:0">
<div class="panel-title">
<span><span class="tab tab-active" data-tab="drap">DRAP</span><span class="tab" data-tab="aurora">AURORA</span><span class="tab" data-tab="enlil">ENLIL</span></span>
<span class="timer" id="tmDrap"></span>
</div>
<div class="panel-body img-wrap" style="padding:2px">
<img id="imgDrap" src="/api/drap" alt="Aurora" data-tab="aurora" style="width:100%;object-fit:contain;display:none">
<img id="imgRealDrap" src="/api/real-drap" alt="DRAP" data-tab="drap" style="width:100%;object-fit:contain">
<img id="imgEnlil" src="/api/enlil" alt="WSA-ENLIL" data-tab="enlil" style="width:100%;object-fit:contain;display:none">
</div>
</div>
</div>
</div>
<div class="sbar">
<span id="sbarLeft">Connecting...</span>
<span id="sbarRight">HamClock Lite v2.0</span>
</div>
</div><!-- end dashboard -->
<script>
(function(){
'use strict';
var P=function(n){return n<10?'0'+n:''+n};
var esc=function(s){
if(s==null)return'';
return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
};

// Timestamps for countdown timers
var lastSolarFetch=0,lastDxFetch=0,lastImageFetch=0;
var SOLAR_INTERVAL=300,DX_INTERVAL=120,IMAGE_INTERVAL=900;
var POLL_INTERVAL=60000;
var failCount=0;

// Cached last-known data for stale display
var lastSolar=null,lastBands=null,lastDx=null;
var solarStale=false,bandsStale=false,dxStale=false;

// Settings
var settings=null;
try{settings=JSON.parse(localStorage.getItem('hamclock-settings'));}catch(e){}

// Theme definitions
var themes={
classic:{cyan:'#06b6d4',green:'#22c55e',callsign:'#f472b6',bg:'#0a0e14',card:'#111820',border:'#1a2530',label:'#8899aa',muted:'#607080'},
amber:{cyan:'#f59e0b',green:'#f59e0b',callsign:'#3b82f6',bg:'#1a1000',card:'#1f1800',border:'#332800',label:'#B88060',muted:'#8A6840'},
blue:{cyan:'#3b82f6',green:'#60a5fa',callsign:'#f59e0b',bg:'#0a0f1e',card:'#0f1628',border:'#1a2540',label:'#7090b0',muted:'#506888'},
red:{cyan:'#ef4444',green:'#f87171',callsign:'#fbbf24',bg:'#1a0a0a',card:'#201010',border:'#3a1a1a',label:'#b07070',muted:'#905858'},
kstate:{cyan:'#FFFFFF',green:'#FFFFFF',callsign:'#FFFFFF',bg:'#512888',card:'#3D1366',border:'#694190',bright:'#FFFFFF',text:'#E8DDF5',label:'#C0B5D5',muted:'#927EB4'}
};

function applySettings(s){
var el=document.getElementById('hdrCallsign');
if(el)el.textContent=s.callsign||'';
var root=document.documentElement;
var t=themes[s.theme]||themes.classic;
root.style.setProperty('--cyan',t.cyan);
root.style.setProperty('--green',t.green);
root.style.setProperty('--callsign',t.callsign||'#f472b6');
root.style.setProperty('--bg',t.bg);
root.style.setProperty('--card',t.card);
root.style.setProperty('--border',t.border);
root.style.setProperty('--bright',t.bright||'#e8f0f0');
root.style.setProperty('--text',t.text||'#c8d0d8');
if(t.label)root.style.setProperty('--label',t.label);
if(t.muted)root.style.setProperty('--muted',t.muted);
document.body.className=document.body.className.replace(/\btheme-\S+/g,'').trim()+' theme-'+(s.theme||'classic');
}

if(!settings){
document.getElementById('setup').style.display='flex';
document.getElementById('dashboard').style.display='none';
}else{
document.getElementById('setup').style.display='none';
document.getElementById('dashboard').style.display='';
applySettings(settings);
if(settings&&settings.theme==='kstate'){
var kstateEl=document.getElementById('themeKstate');
if(kstateEl)kstateEl.style.display='';
}
}

// Fetch the host's NTP server to use as the default placeholder
(function(){
  var xhr=new XMLHttpRequest();
  xhr.open('GET','/api/ntp');
  xhr.timeout=5000;
  xhr.onload=function(){
    if(xhr.status!==200)return;
    try{
      var d=JSON.parse(xhr.responseText);
      if(d&&d.ntp){
        var el=document.getElementById('inNtp');
        if(el)el.placeholder=d.ntp+' (host)';
        window.__hostNtp=d.ntp;
      }
    }catch(e){}
  };
  xhr.send();
})();

// Make saveSetup global
window.saveSetup=function(){
var s={
callsign:document.getElementById('inCallsign').value.toUpperCase().trim(),
grid:document.getElementById('inGrid').value.toUpperCase().trim(),
timezone:document.getElementById('inTimezone').value,
theme:(document.querySelector('input[name="theme"]:checked')||{}).value||'classic',
ntp:document.getElementById('inNtp').value.trim()||window.__hostNtp||'pool.ntp.org'
};
if(!s.callsign){alert('Please enter your callsign');return;}
localStorage.setItem('hamclock-settings',JSON.stringify(s));
settings=s;
document.getElementById('setup').style.display='none';
document.getElementById('dashboard').style.display='';
applySettings(s);
startFetching();
};

// Callsign auto-lookup with debounce
var callsignTimer=null;
document.getElementById('inCallsign').addEventListener('input',function(){
var call=this.value.toUpperCase().trim();
clearTimeout(callsignTimer);
// Show/hide K-State theme based on callsign
var kstateEl=document.getElementById('themeKstate');
if(call==='W0QQQ'){
if(kstateEl)kstateEl.style.display='';
var kstateRadio=document.querySelector('input[name="theme"][value="kstate"]');
if(kstateRadio)kstateRadio.checked=true;
document.getElementById('inNtp').value='ntp.ksu.edu';
}else{
if(kstateEl)kstateEl.style.display='none';
var currentTheme=document.querySelector('input[name="theme"]:checked');
if(currentTheme&&currentTheme.value==='kstate'){
document.querySelector('input[name="theme"][value="classic"]').checked=true;
}
var ntpEl=document.getElementById('inNtp');
if(ntpEl&&ntpEl.value==='ntp.ksu.edu'){
ntpEl.value='';
}
}
if(call.length<3)return;
callsignTimer=setTimeout(function(){
var xhr=new XMLHttpRequest();
xhr.open('GET','/api/callsign/'+encodeURIComponent(call));
xhr.timeout=8000;
xhr.onload=function(){
if(xhr.status!==200)return;
try{
var d=JSON.parse(xhr.responseText);
// Auto-fill grid
if(d.grid){
document.getElementById('inGrid').value=d.grid;
}
// Auto-fill timezone from longitude
if(d.lng!=null){
var offset=Math.round(d.lng/15);
var tzMap={
'-5':'US/Eastern','-6':'US/Central','-7':'US/Mountain',
'-8':'US/Pacific','-9':'US/Alaska','-10':'US/Hawaii',
'0':'Europe/London','1':'Europe/Paris','2':'Europe/Berlin',
'3':'Europe/Moscow','9':'Asia/Tokyo','8':'Asia/Shanghai',
'5':'Asia/Kolkata','10':'Australia/Sydney'
};
var tz=tzMap[String(offset)];
if(tz){
document.getElementById('inTimezone').value=tz;
}
}
// Show name as confirmation
if(d.name){
var nameEl=document.getElementById('callLookupName');
if(nameEl)nameEl.textContent=d.name+(d.country?' \u2014 '+d.country:'');
}
}catch(e){}
};
xhr.send();
},800);
});

// Re-open settings when callsign is clicked
document.getElementById('hdrCallsign').onclick=function(){
// Pre-fill form with current settings
if(settings){
document.getElementById('inCallsign').value=settings.callsign||'';
document.getElementById('inGrid').value=settings.grid||'';
document.getElementById('inTimezone').value=settings.timezone||'auto';
document.getElementById('inNtp').value=settings.ntp||'';
var radios=document.querySelectorAll('input[name="theme"]');
for(var i=0;i<radios.length;i++){radios[i].checked=radios[i].value===(settings.theme||'classic');}
// Show K-State swatch if current theme is kstate
if(settings.theme==='kstate'){
var kstateEl=document.getElementById('themeKstate');
if(kstateEl)kstateEl.style.display='';
}
}
document.getElementById('setup').style.display='flex';
};

// Tab switcher for AURORA/DRAP combined panel
document.querySelectorAll('.tab').forEach(function(t){
t.addEventListener('click',function(){
var name=this.getAttribute('data-tab');
document.querySelectorAll('.tab').forEach(function(s){
s.classList.toggle('tab-active',s.getAttribute('data-tab')===name);
});
document.querySelectorAll('img[data-tab]').forEach(function(im){
im.style.display=im.getAttribute('data-tab')===name?'':'none';
});
});
});

// DOM refs
var elUtc=document.getElementById('utc');
var elLcl=document.getElementById('lcl');
var elSolar=document.getElementById('solarPanel');
var elBands=document.getElementById('bandsPanel');
var elDxBody=document.getElementById('dxBody');
var elDot=document.getElementById('statusDot');
var elSbarL=document.getElementById('sbarLeft');
var tmSolar=document.getElementById('tmSolar');
var tmBands=document.getElementById('tmBands');
var tmDx=document.getElementById('tmDx');
var tmSolarImg=document.getElementById('tmSolarImg');
var tmMuf=document.getElementById('tmMuf');
var tmDrap=document.getElementById('tmDrap');

// Clock — uses timezone setting if available
setInterval(function(){
var d=new Date();
elUtc.textContent='UTC '+P(d.getUTCHours())+':'+P(d.getUTCMinutes())+':'+P(d.getUTCSeconds());
if(settings&&settings.timezone&&settings.timezone!=='auto'){
try{
var opts={hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:settings.timezone};
elLcl.textContent='LOCAL '+d.toLocaleTimeString('en-US',opts);
}catch(e){
elLcl.textContent='LOCAL '+P(d.getHours())+':'+P(d.getMinutes())+':'+P(d.getSeconds());
}
}else{
elLcl.textContent='LOCAL '+P(d.getHours())+':'+P(d.getMinutes())+':'+P(d.getSeconds());
}
},1000);

// Static countdown — called once per fetch cycle, not every second
function formatCountdown(lastFetch,intervalSec){
var elapsed=Math.floor((Date.now()/1000)-lastFetch);
var remaining=Math.max(0,intervalSec-elapsed);
return remaining>=60?Math.ceil(remaining/60)+'m':remaining+'s';
}
function updateCountdowns(){
if(lastSolarFetch){var sc='next \u21BB '+formatCountdown(lastSolarFetch,SOLAR_INTERVAL);tmSolar.textContent=sc;tmBands.textContent=sc;}
if(lastDxFetch){tmDx.textContent='next \u21BB '+formatCountdown(lastDxFetch,DX_INTERVAL);}
if(lastImageFetch){var ic='next \u21BB '+formatCountdown(lastImageFetch,IMAGE_INTERVAL);tmSolarImg.textContent=ic;tmMuf.textContent=ic;tmDrap.textContent=ic;}
}

// Color helpers
function kpColor(k){k=parseFloat(k)||0;if(k<=2)return'var(--green)';if(k<=4)return'var(--yellow)';return'var(--red)';}
function sfiColor(s){s=parseFloat(s)||0;if(s>=150)return'var(--green)';if(s>=100)return'var(--cyan)';if(s>=70)return'var(--yellow)';return'var(--red)';}
function condLetter(c){if(!c)return{l:'?',cls:'cN'};var v=c.toLowerCase();if(v==='good')return{l:'G',cls:'cG'};if(v==='fair')return{l:'F',cls:'cF'};if(v==='poor')return{l:'P',cls:'cP'};return{l:'?',cls:'cN'};}
function geoColor(s){if(!s)return'var(--muted)';var v=s.toLowerCase();if(v==='quiet'||v==='inactive')return'var(--green)';if(v==='unsettled'||v==='active')return'var(--yellow)';return'var(--red)';}

// Render solar
function renderSolar(d){
if(!d||!d.sfi)return;
lastSolar=d;solarStale=false;
var kp=parseFloat(d.kIndex)||0;
var kpPct=Math.min(kp/9*100,100);
var h='';
h+='<div class="s-row"><span class="s-lbl">SFI</span><span class="s-val" style="color:'+sfiColor(d.sfi)+'">'+esc(d.sfi)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Kp</span><span class="s-val"><span class="kp-wrap"><span class="kp-bar"><span class="kp-fill" style="width:'+kpPct+'%;background:'+kpColor(d.kIndex)+'"></span></span><span style="color:'+kpColor(d.kIndex)+'">'+esc(d.kIndex)+'</span></span></span></div>';
h+='<div class="s-row"><span class="s-lbl">SSN</span><span class="s-val" style="color:var(--cyan)">'+esc(d.ssn)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">A</span><span class="s-val" style="color:'+(parseInt(d.aIndex)>20?'var(--red)':'var(--green)')+'">'+esc(d.aIndex)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">X-Ray</span><span class="s-val" style="color:var(--yellow)">'+esc(d.xray)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Wind</span><span class="s-val" style="color:'+(parseFloat(d.solarWind)>500?'var(--red)':'var(--cyan)')+'">'+esc(d.solarWind)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Bz</span><span class="s-val" style="color:'+(parseFloat(d.magneticField)<0?'var(--red)':'var(--green)')+'">'+esc(d.magneticField)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Geo</span><span class="s-val" style="color:'+geoColor(d.geomagField)+'">'+esc(d.geomagField)+'</span></div>';
if(d.signalNoise){h+='<div class="s-row"><span class="s-lbl">S/N</span><span class="s-val" style="color:var(--cyan)">'+esc(d.signalNoise)+'</span></div>';}
if(d.aurora){h+='<div class="s-row"><span class="s-lbl">Aurora</span><span class="s-val" style="color:#a855f7">'+esc(d.aurora)+'</span></div>';}
if(d.fof2){h+='<div class="s-row"><span class="s-lbl">foF2</span><span class="s-val" style="color:#3b82f6">'+esc(d.fof2)+'</span></div>';}
elSolar.innerHTML=h;
}

// Render bands
function renderBands(d){
if(!d||Object.keys(d).length===0)return;
lastBands=d;bandsStale=false;
var order=['80m-40m','30m-20m','17m-15m','12m-10m'];
var keys=Object.keys(d).sort(function(a,b){
var ai=order.indexOf(a),bi=order.indexOf(b);
if(ai===-1)ai=99;if(bi===-1)bi=99;return ai-bi;
});
var h='<div class="band-row"><span class="band-name"></span><span class="band-lbl">DAY</span><span class="band-lbl">NIGHT</span></div>';
for(var i=0;i<keys.length;i++){
var n=keys[i],b=d[n];
var dc=condLetter(b.day||b.Day);
var nc=condLetter(b.night||b.Night);
h+='<div class="band-row"><span class="band-name">'+esc(n)+'</span><span class="band-cond '+dc.cls+'">'+dc.l+'</span><span class="band-cond '+nc.cls+'">'+nc.l+'</span></div>';
}
elBands.innerHTML=h;
}

// Render DX
var BAND_COLORS={'160m':'#8b5cf6','80m':'#6366f1','60m':'#3b82f6','40m':'#06b6d4','30m':'#14b8a6','20m':'#22c55e','17m':'#84cc16','15m':'#eab308','12m':'#f97316','10m':'#ef4444','6m':'#ec4899','2m':'#a855f7','70cm':'#f472b6'};
function renderDX(spots){
if(!spots||spots.length===0){elDxBody.innerHTML='<tr><td colspan="5" style="color:var(--muted);padding:8px">No spots</td></tr>';return;}
lastDx=spots;dxStale=false;
// Hard cap at 10 rows
var maxRows=Math.min(spots.length,5);
var h='';
for(var i=0;i<maxRows;i++){
var s=spots[i];
var bc=BAND_COLORS[s.band]||'var(--muted)';
h+='<tr><td class="dx-freq">'+esc(s.frequency)+'</td><td class="dx-band" style="color:'+bc+'">'+esc(s.band)+'</td><td class="dx-call">'+esc(s.dx)+'</td><td class="dx-sp">'+esc(s.spotter)+'</td><td class="dx-tm">'+esc(s.time)+'</td></tr>';
}
elDxBody.innerHTML=h;
}

// Render band activity bar chart from DX spots
function renderBandActivity(spots){
if(!spots||!spots.length)return;
var counts={};
spots.forEach(function(s){
counts[s.band]=(counts[s.band]||0)+1;
});
var bandOrder=['160m','80m','60m','40m','30m','20m','17m','15m','12m','10m'];
var sorted=bandOrder.filter(function(b){return counts[b];});
var max=1;
sorted.forEach(function(b){if(counts[b]>max)max=counts[b];});
var h='';
var bandColors={
'160m':'#ff6b6b','80m':'#f06595','60m':'#cc5de8','40m':'#845ef7',
'30m':'#5c7cfa','20m':'#339af0','17m':'#22b8cf','15m':'#20c997',
'12m':'#51cf66','10m':'#94d82d','6m':'#fcc419','2m':'#ff922b'
};
sorted.forEach(function(band){
var pct=Math.round(counts[band]/max*100);
var color=bandColors[band]||'#666';
h+='<div style="display:flex;align-items:center;gap:6px;margin:5px 0">';
h+='<span style="width:30px;text-align:right;color:var(--label);font-size:clamp(13px,1.3vw,13px)">'+esc(band)+'</span>';
h+='<div style="flex:1;height:clamp(14px,2.4vh,24px);background:var(--bg)">';
h+='<div style="width:'+pct+'%;height:100%;background:'+color+'"></div>';
h+='</div>';
h+='<span style="width:20px;color:var(--text);font-size:clamp(11px,1vw,11px)">'+counts[band]+'</span>';
h+='</div>';
});
document.getElementById('bandActivity').innerHTML=h;
}

// Render X-Ray Flux panel
function renderXray(solar){
if(!solar)return;
var el=document.getElementById('xrayBody');
if(!el)return;
var xr=solar.xray||'N/A';
var cls=xr.charAt(0).toUpperCase();
var color=(cls==='A'||cls==='B')?'#22c55e':cls==='C'?'#eab308':cls==='M'?'#ff8c00':cls==='X'?'#ef4444':'#666';
var level=cls==='A'?10:cls==='B'?25:cls==='C'?50:cls==='M'?75:cls==='X'?100:0;
var label=(cls==='A'||cls==='B')?'QUIET':cls==='C'?'ACTIVE':cls==='M'?'STORM':cls==='X'?'MAJOR':'';
el.innerHTML='<div style="display:flex;align-items:center;gap:8px">'+
'<span style="font-size:clamp(24px,2.5vh,24px);font-weight:bold;color:'+color+'">'+esc(xr)+'</span>'+
'<div style="flex:1;height:clamp(6px,1vh,10px);background:var(--bg)">'+
'<div style="width:'+level+'%;height:100%;background:'+color+'"></div></div>'+
'<span style="font-size:clamp(11px,1vh,11px);color:'+color+'">'+label+'</span></div>';
}

// Render Geomagnetic panel
function renderGeomag(solar){
if(!solar)return;
var el=document.getElementById('geomagBody');
if(!el)return;
var kp=parseInt(solar.kIndex)||0;
var geo=solar.geomagField||'unknown';
var color=kp<=3?'#22c55e':kp<=5?'#eab308':kp<=7?'#ff8c00':'#ef4444';
var pct=Math.min(100,kp/9*100);
el.innerHTML='<div style="display:flex;align-items:center;gap:8px">'+
'<span style="font-size:clamp(24px,2.5vh,24px);font-weight:bold;color:'+color+'">Kp '+kp+'</span>'+
'<div style="flex:1;height:clamp(6px,1vh,10px);background:var(--bg)">'+
'<div style="width:'+pct+'%;height:100%;background:'+color+'"></div></div>'+
'<span style="font-size:clamp(11px,1vh,11px);color:'+color+'">'+esc(geo.toUpperCase())+'</span></div>';
}

// Render Open Bands panel
function renderOpenBands(bands){
if(!bands)return;
var el=document.getElementById('openBandsBody');
if(!el)return;
var open=[],closed=[];
for(var key in bands){
var d=bands[key];
var dayOk=d.day&&(d.day.toLowerCase()==='good'||d.day.toLowerCase()==='fair');
var nightOk=d.night&&(d.night.toLowerCase()==='good'||d.night.toLowerCase()==='fair');
var dayOk2=d.Day&&(d.Day.toLowerCase()==='good'||d.Day.toLowerCase()==='fair');
var nightOk2=d.Night&&(d.Night.toLowerCase()==='good'||d.Night.toLowerCase()==='fair');
if(dayOk||nightOk||dayOk2||nightOk2){open.push(key);}else{closed.push(key);}
}
var h='';
if(open.length){
h+='<div style="margin-bottom:4px"><span style="color:#22c55e;font-size:clamp(11px,1vh,11px)">OPEN: </span>';
h+=open.map(function(b){return'<span style="color:#22c55e;font-weight:bold;margin-right:6px">'+esc(b)+'</span>';}).join('');
h+='</div>';
}
if(closed.length){
h+='<div><span style="color:#ef4444;font-size:clamp(11px,1vh,11px)">CLOSED: </span>';
h+=closed.map(function(b){return'<span style="color:#ef4444;margin-right:6px">'+esc(b)+'</span>';}).join('');
h+='</div>';
}
el.innerHTML=h||'<span style="color:var(--muted)">Waiting for data...</span>';
}

// Fetch queue — one request at a time, 1.5s gap between each
var fetchQueue=[];
var fetchBusy=false;

function queueFetch(url,callback){
fetchQueue.push({url:url,cb:callback});
processQueue();
}

function processQueue(){
if(fetchBusy||fetchQueue.length===0)return;
fetchBusy=true;
var item=fetchQueue.shift();
var xhr=new XMLHttpRequest();
xhr.open('GET',item.url);
xhr.timeout=8000;
xhr.onload=function(){
if(xhr.status===200){
try{item.cb(JSON.parse(xhr.responseText));}catch(e){}
}
fetchBusy=false;
setTimeout(processQueue,1500);
};
xhr.onerror=xhr.ontimeout=function(){
fetchBusy=false;
setTimeout(processQueue,1500);
};
xhr.send();
}

function fetchAll(){
updateCountdowns();
// If the previous cycle's requests haven't drained, skip this cycle so the
// queue can't grow without bound when the backend is slow or hung.
if(fetchQueue.length>0)return;
queueFetch('/api/solar',function(data){
renderSolar(data);
renderXray(data);
renderGeomag(data);
lastSolarFetch=Math.floor(Date.now()/1000);
});
queueFetch('/api/bands',function(data){
renderBands(data);
renderOpenBands(data);
});
queueFetch('/api/dxspots',function(data){
renderDX(data);
renderBandActivity(data);
lastDxFetch=Math.floor(Date.now()/1000);
});
queueFetch('/api/health',function(data){
if(data&&data.status==='ok'){
failCount=0;
elDot.style.background='';
elSbarL.textContent='Solar:'+fmtAge(data.solar_age)+' Bands:'+fmtAge(data.bands_age)+' DX:'+fmtAge(data.dx_age);
elSbarL.className='';
}else{
failCount++;
if(failCount>3){elDot.style.background='var(--red)';elSbarL.textContent='Disconnected';elSbarL.className='stale';}
else{elDot.style.background='var(--yellow)';elSbarL.textContent='Retrying...';}
}
});
}

function fmtAge(s){
if(s==null||s<0)return' --';
if(s<60)return' '+s+'s';
return' '+Math.floor(s/60)+'m';
}

// Image refresh (separate, every 15 min) — staggered to spread CPU load
var elImgSolar=document.getElementById('imgSolar');
var elImgMuf=document.getElementById('imgMuf');
var elImgEnlil=document.getElementById('imgEnlil');
var elImgDrap=document.getElementById('imgDrap');
var elImgRealDrap=document.getElementById('imgRealDrap');

function refreshImages(){
// Cache-bust with the 15-min refresh slot index, NOT Date.now(). A unique
// timestamp every cycle made the browser cache thousands of distinct image
// URLs per day and never evict them — a slow OOM on a 512MB Pi. The slot
// index changes once per refresh (enough to force a reload) but repeats
// rarely, and the server sends Cache-Control: no-store so nothing piles up.
var now=Date.now();
var t=Math.floor(now/(IMAGE_INTERVAL*1000));
lastImageFetch=Math.floor(now/1000);
if(elImgSolar)elImgSolar.src='/api/solar-image?t='+t;
setTimeout(function(){
if(elImgMuf)elImgMuf.src='/api/muf-map?t='+t;
},3000);
setTimeout(function(){
if(elImgEnlil)elImgEnlil.src='/api/enlil?t='+t;
},6000);
setTimeout(function(){
if(elImgDrap)elImgDrap.src='/api/drap?t='+t;
},9000);
setTimeout(function(){
if(elImgRealDrap)elImgRealDrap.src='/api/real-drap?t='+t;
},12000);
updateCountdowns();
}

// Start fetching data
var fetchStarted=false;
function startFetching(){
if(fetchStarted)return;
fetchStarted=true;
lastImageFetch=Math.floor(Date.now()/1000);
setTimeout(function(){queueFetch('/api/solar',function(d){renderSolar(d);renderXray(d);renderGeomag(d);lastSolarFetch=Math.floor(Date.now()/1000);});},500);
setTimeout(function(){queueFetch('/api/bands',function(d){renderBands(d);renderOpenBands(d);});},2000);
setTimeout(function(){queueFetch('/api/dxspots',function(d){renderDX(d);renderBandActivity(d);lastDxFetch=Math.floor(Date.now()/1000);});},3500);
setTimeout(refreshImages,5000);
setInterval(fetchAll,POLL_INTERVAL);
setInterval(refreshImages,IMAGE_INTERVAL*1000);
}

// Init — only start fetching if settings exist (setup already done)
if(settings){
startFetching();
}
})();
</script>
</body>
</html>
HTMLEOF

echo "Writing hamclock_data.py..."
sudo tee "$INSTALL_DIR/hamclock_data.py" > /dev/null << 'HCDATAEOF'
"""Shared data-fetching layer for HamClock Lite native GUI clients.

Polls the same /api/* endpoints the browser uses, caching JSON dicts and
raw image bytes for Pygame/Tkinter kiosks on Raspberry Pi 1.
"""

import json
import threading
import time
import urllib.error
import urllib.request
# Re-exported so tests can monkeypatch 'hamclock_data.urlopen' / 'Request'
# and _fetch_json picks up the fake — keeps the patch site stable across
# refactors of the request helper.
from urllib.request import Request, urlopen


class HamClockData:
    """Thread-safe data-fetching layer for HamClock Lite native clients.

    Polls /api/* JSON endpoints and binary image endpoints on configurable
    intervals. Native GUI code reads the cached attributes directly
    (they're updated in-place by the background thread).

    Attribute usage is lock-free for single-reader GUI loops: the GIL
    makes single-key dict reads atomic, and the background thread only
    does whole-dict assignments. For multi-reader scenarios, use the
    lock() context manager.
    """

    DEFAULT_SERVER = 'http://localhost:8080'
    USER_AGENT = 'HamClockNative/1.0'
    JSON_TIMEOUT = 10
    IMAGE_TIMEOUT = 20

    # Tier 1.4: per-key retry backoff. Index N is the delay (seconds) after
    # the Nth consecutive failure of that key; the last entry repeats
    # forever. Retries therefore land at cumulative 5/15/35/75/135 s and
    # then once a minute, instead of the old "one shot, then nothing for
    # 900 s" behaviour that left the propagation panel blank for 15 minutes
    # after a single cold-boot miss.
    IMAGE_RETRY_BACKOFF = (5, 10, 20, 40, 60)

    _JSON_ENDPOINTS = {
        'solar': '/api/solar',
        'bands': '/api/bands',
        'dxspots': '/api/dxspots',
        'health': '/api/health',
    }
    _IMAGE_ENDPOINTS = {
        'solar-image': '/api/solar-image',
        # ?fmt=png matters: without it the server falls back to serving the
        # raw KC2G SVG while the rasterize is still running, and pygame cannot
        # decode SVG. _fetch_binary treats any HTTP 200 as success, so those
        # undecodable bytes were cached as a satisfied fetch and the key was
        # not retried until the next 900 s cycle — 15 minutes of a blank MUF
        # panel. With ?fmt=png the server answers PNG-or-503, and a 503 is a
        # real failure that the retry backoff picks up within seconds.
        'muf-map': '/api/muf-map?fmt=png',
        'enlil': '/api/enlil',
        'drap': '/api/drap',
        'real-drap': '/api/real-drap',
    }

    def __init__(self, server_url='http://localhost:8080'):
        """Initialize with the HamClock server URL (default localhost:8080)."""
        self.server_url = server_url.rstrip('/')
        # JSON cache
        self.solar = {}
        self.bands = {}
        self.dxspots = []
        self.health = {}
        # Binary image cache
        self.images = {}
        # Timestamps (Unix seconds; 0 means never)
        self.last_data_refresh = 0
        self.last_image_refresh = 0
        # Per-image refresh timestamps (epoch seconds). Maps image_key
        # ('solar-image' | 'muf-map' | 'enlil' | 'drap' | 'real-drap')
        # to the epoch-second when that key's bytes last refreshed.
        # Used by the pygame client's _scaled_cache to invalidate per-image.
        self.image_fetched_at = {}
        # Tier 1.4 retry scheduling, both keyed by image_key.
        #   image_next_due[key]    epoch second at/after which key may be
        #                          attempted again (missing => due now)
        #   image_fail_streak[key] consecutive failures; indexes
        #                          IMAGE_RETRY_BACKOFF
        # Written only by the fetch thread; single-key reads are atomic
        # under the GIL, so GUI code may sample them without the lock.
        self.image_next_due = {}
        self.image_fail_streak = {}
        # Slow (healthy) image cadence in seconds. _run() overwrites this
        # with the caller's image_interval; the default matches
        # start_background()'s so a manual refresh_images() before the
        # thread starts schedules sanely.
        self._image_interval = 900
        # Errors (most recent error per key, None if last fetch succeeded)
        self.errors = {}
        # Tier 2c perf: ETags by path so we can replay If-None-Match on the
        # next poll. ~80% of /api/{solar,bands,dxspots} polls land on
        # unchanged data; a 304 short-circuits the read+json.loads here.
        self._etags = {}
        # Internal
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def _request(self, path, timeout):
        url = self.server_url + path
        req = urllib.request.Request(url, headers={'User-Agent': self.USER_AGENT})
        return urllib.request.urlopen(req, timeout=timeout)

    def _fetch_json(self, path):
        """HTTP GET path and parse as JSON. Returns dict/list or None on failure.

        Tier 2c perf: sends If-None-Match when we have a prior ETag for
        this path. Returns None on 304 (caller should keep its cached
        value — same semantics as the existing error path).
        """
        url = self.server_url + path
        req = Request(url, headers={'User-Agent': self.USER_AGENT})
        prev_etag = self._etags.get(path)
        if prev_etag:
            req.add_header('If-None-Match', prev_etag)
        try:
            with urlopen(req, timeout=self.JSON_TIMEOUT) as resp:
                new_etag = resp.headers.get('ETag')
                if new_etag:
                    self._etags[path] = new_etag
                data = json.loads(resp.read().decode('utf-8'))
            self.errors[path] = None
            return data
        except urllib.error.HTTPError as e:
            if e.code == 304:
                # Server says: no change since prev_etag. Skip parse;
                # caller keeps its cached value (existing 'None means
                # don't overwrite' contract in refresh_data).
                self.errors[path] = None
                return None
            self.errors[path] = '{}: {}'.format(type(e).__name__, e)
            return None
        except (urllib.error.URLError, ValueError, OSError) as e:
            self.errors[path] = '{}: {}'.format(type(e).__name__, e)
            return None

    def _fetch_binary(self, path):
        """HTTP GET path and return raw bytes. Returns bytes or None on failure."""
        try:
            with self._request(path, self.IMAGE_TIMEOUT) as resp:
                data = resp.read()
            self.errors[path] = None
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            self.errors[path] = '{}: {}'.format(type(e).__name__, e)
            return None

    def mark_image_undecodable(self, key, retry_in=None):
        """Report that key's cached bytes could not be decoded.

        A 200 carrying a payload this client cannot render is a failed fetch in
        every way that matters, but _fetch_binary cannot see that — only the
        decoder can. Without this the key stays "satisfied" until the slow
        cycle comes round again. Schedules a retry on the normal backoff.
        """
        try:
            if key not in self._IMAGE_ENDPOINTS:
                return
            with self._lock:
                streak = self.image_fail_streak.get(key, 0) + 1
                self.image_fail_streak[key] = streak
                if retry_in is None:
                    idx = min(streak, len(self.IMAGE_RETRY_BACKOFF)) - 1
                    retry_in = self.IMAGE_RETRY_BACKOFF[max(0, idx)]
                self.image_next_due[key] = time.time() + retry_in
                # Drop the bad payload so a stale-but-good surface is not
                # rebuilt from it, and so "have we got bytes" stays honest.
                imgs = dict(self.images)
                imgs.pop(key, None)
                self.images = imgs
        except Exception:
            pass

    def refresh_data(self):
        """Fetch the 4 JSON endpoints synchronously."""
        results = {}
        fetched = {}
        for key, path in self._JSON_ENDPOINTS.items():
            data = self._fetch_json(path)
            results[key] = data is not None
            if data is not None:
                fetched[key] = data
        with self._lock:
            if 'solar' in fetched:
                self.solar = fetched['solar'] if isinstance(fetched['solar'], dict) else {}
            if 'bands' in fetched:
                self.bands = fetched['bands'] if isinstance(fetched['bands'], dict) else {}
            if 'dxspots' in fetched:
                self.dxspots = fetched['dxspots'] if isinstance(fetched['dxspots'], list) else []
            if 'health' in fetched:
                self.health = fetched['health'] if isinstance(fetched['health'], dict) else {}
            self.last_data_refresh = time.time()
        return results

    def _next_image_delay(self, key):
        """Seconds to wait before the next attempt of image `key`.

        Healthy keys get the slow cadence; failing keys walk
        IMAGE_RETRY_BACKOFF and saturate on its last entry.
        """
        streak = self.image_fail_streak.get(key, 0)
        if streak <= 0:
            return self._image_interval
        idx = min(streak, len(self.IMAGE_RETRY_BACKOFF)) - 1
        return self.IMAGE_RETRY_BACKOFF[idx]

    def _reschedule_image(self, key, ok, now):
        """Record the outcome of one image attempt and set its next due time.

        Called from a `finally`, so it must not raise for any input.
        """
        if ok:
            self.image_fail_streak[key] = 0
        else:
            self.image_fail_streak[key] = self.image_fail_streak.get(key, 0) + 1
        self.image_next_due[key] = now + self._next_image_delay(key)

    def _due_image_keys(self, now):
        """Image keys whose next-due time has passed, or None if none are.

        Returns None rather than [] in the common case: the 1 s tick calls
        this every second on a single-core ARMv6 box and the quiescent path
        must not allocate.
        """
        due = None
        for key in self._IMAGE_ENDPOINTS:
            if now >= self.image_next_due.get(key, 0.0):
                if due is None:
                    due = []
                due.append(key)
        return due

    def refresh_images(self, keys=None):
        """Fetch image endpoints synchronously. keys=None means all five.

        Every attempted key is rescheduled unconditionally (in a `finally`),
        including on an exception _fetch_binary does not catch — a
        MemoryError escaping the except clause at _fetch_binary would
        otherwise leave the key permanently due and hot-spin the 1 s tick.

        last_image_refresh is stamped only when at least one key came back
        with bytes. Stamping it after a total failure is what used to buy a
        blank panel for a full image_interval.
        """
        if keys is None:
            keys = self._IMAGE_ENDPOINTS
        results = {}
        fetched = {}
        try:
            for key in keys:
                path = self._IMAGE_ENDPOINTS.get(key)
                if path is None:
                    continue
                ok = False
                try:
                    data = self._fetch_binary(path)
                    ok = data is not None
                    results[key] = ok
                    if ok:
                        fetched[key] = data
                finally:
                    self._reschedule_image(key, ok, time.time())
        finally:
            # Also a `finally` so bytes already in hand are published even if
            # a later key blows up — those keys are rescheduled 900 s out and
            # dropping their payload here would recreate the blank-panel bug.
            if fetched:
                # Read the clock after the fetches, not before: image ages
                # should reflect when the bytes landed.
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

    def start_background(self, data_interval=60, image_interval=900):
        """Start a daemon thread that refreshes data/images on their intervals."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, args=(data_interval, image_interval), daemon=True
        )
        self._thread.start()

    def _run(self, data_interval, image_interval):
        self._image_interval = image_interval
        # Immediate initial fetch
        try:
            self.refresh_data()
        except Exception as e:
            self.errors['_run_data'] = '{}: {}'.format(type(e).__name__, e)
        try:
            self.refresh_images()
        except Exception as e:
            self.errors['_run_images'] = '{}: {}'.format(type(e).__name__, e)
        # 1 s tick: image retries need second-resolution scheduling, the JSON
        # cadence does not, so the data check stays on a 5-tick (5 s) grid.
        tick = 0
        while self._running:
            time.sleep(1)
            if not self._running:
                return
            tick = (tick + 1) % 5
            now = time.time()
            if tick == 0 and now - self.last_data_refresh >= data_interval:
                try:
                    self.refresh_data()
                except Exception as e:
                    self.errors['_run_data'] = '{}: {}'.format(type(e).__name__, e)
                # refresh_data can block for up to 4 x JSON_TIMEOUT; re-read
                # the clock so image due-times aren't judged against a stale
                # `now`.
                now = time.time()
            due = self._due_image_keys(now)
            if due:
                try:
                    self.refresh_images(due)
                except Exception as e:
                    self.errors['_run_images'] = '{}: {}'.format(type(e).__name__, e)

    def stop(self):
        """Signal the background thread to exit."""
        self._running = False

    def lock(self):
        """Return the internal threading.Lock for use as a context manager."""
        return self._lock
HCDATAEOF

echo "Writing hamclock_pygame.py..."
sudo tee "$INSTALL_DIR/hamclock_pygame.py" > /dev/null << 'HCPYEOF'
"""Native Pygame client for HamClock Lite.

Replaces the browser on a Raspberry Pi 1 Model B, fetching data from
the same /api/* endpoints as the web UI but rendering directly with
Pygame/SDL for a ~50 MB RAM and ~10% CPU win over the browser stack.
"""

import argparse
import collections
import gc
import io
import json
import os
import sys
import threading
import time
import urllib.error

import pygame

from hamclock_data import HamClockData

import pwd
import grp
import re
import tempfile

# Re-exported so tests can monkeypatch hamclock_pygame._urlopen / _Request and
# the diagnostics sender picks up the fake — same patch-site convention
# hamclock_data uses. urllib.request is already resident (hamclock_data imports
# it at module scope), so this costs no extra RAM on the Pi.
from urllib.request import Request as _Request, urlopen as _urlopen

# ---- Settings layer (Phase 4) ----
SETTINGS_PATH = "/etc/hamclock-lite/settings.json"
SETTINGS_DIR = "/etc/hamclock-lite"

DEFAULT_SETTINGS = {
    "callsign": "",
    "timezone": "UTC",
    "theme": "kstate",
    "ntp": "",
}

# Opt-in diagnostics: the random per-Pi report id lives in the same
# settings.json as the four keys above, but deliberately NOT in
# DEFAULT_SETTINGS. There is no sensible default for it — a Pi that has never
# opened the report dialog has no id at all — and load_settings() must keep
# returning exactly the four keys above for a fresh install (an installed
# base, the wizard, --setup-cli and the settings tests all round-trip that
# exact shape). It is created on first use by _get_or_create_device_id() and
# carried through load_settings/--setup-cli once it exists.
DEVICE_ID_KEY = "device_id"


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
                # Carry an already-assigned diagnostics report id through, so
                # repeat reports from this Pi correlate across reboots. Absent
                # (the common case) the key simply is not in the result.
                did = data.get(DEVICE_ID_KEY)
                if isinstance(did, str) and did:
                    merged[DEVICE_ID_KEY] = did
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


def write_settings(d: dict, path: str = SETTINGS_PATH) -> None:
    """Atomic write: tempfile in same dir + fsync + os.replace + chmod 0644.

    When running as root, attempts to chown to SERVICE_UID/SERVICE_GID so the
    file is owned by the service user regardless of who invoked the CLI.
    PermissionError on chown is expected (wizard already runs as SERVICE_USER)
    and is suppressed."""
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="settings.json.tmp.", dir=dirpath)
    os.close(fd)
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


_CALLSIGN_RE = re.compile(r"^[A-Z0-9/]{3,10}$")


def validate_callsign(s: str) -> tuple:
    """Validate amateur callsign per Phase 4 spec rules.

    Required:
      - regex ^[A-Z0-9/]{3,10}$ after uppercasing
      - stripped of '/', length 3-9
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
    if not (3 <= len(stripped) <= 9):
        return (False, "must be 3-9 letters/digits (excluding /)")
    has_letter = any("A" <= c <= "Z" for c in stripped)
    has_digit = any("0" <= c <= "9" for c in stripped)
    if not has_letter:
        return (False, "must contain at least one letter")
    if not has_digit:
        return (False, "must contain at least one digit")
    return (True, "")


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


# ---- NTP server validation ----
#
# This mirrors valid_one()/valid_ntp_list() in
# scripts/hamclock-apply-settings.sh, and must keep mirroring them: the shell
# is what actually writes the root-owned timesyncd drop-in, so a value this
# side accepts but the root side refuses becomes a setting that silently never
# applies, with the operator's only clue an hour of wrong clock.
#
# THIS COPY IS NOT THE SECURITY BOUNDARY. settings.json is writable by the
# unprivileged service user, so the root applier re-validates from scratch and
# never trusts that the wizard checked. This exists purely to put the error
# under the operator's cursor while they can still fix it.
#
# Deliberately NOT a DNS check. The box may be pointed at an internal time
# server on a network that is not up yet, and a Pi 1 has no RTC, so at setup
# time the clock is routinely wrong and the LAN routinely absent. Format is
# the only thing that can honestly be decided here; reachability is the root
# side's business, after saving.
NTP_MAX_LEN = 512        # whole list; matches the shell's `[ ${#v} -le 512 ]`
NTP_MAX_HOST_LEN = 253   # one element; matches the shell's `[ ${#s} -le 253 ]`

# A hostname or an IPv4/IPv6 literal, and nothing else. No spaces, no '#', no
# '=', no quotes, no shell metacharacters: this string is written into a
# systemd config as root, so anything that could open a second directive is
# refused outright.
_NTP_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.:-]*[A-Za-z0-9])?$")
_NTP_DIGITS_DOTS_RE = re.compile(r"^[0-9.]+$")


def _valid_ntp_host(s: str) -> bool:
    """One element of an NTP= list. Parity with the shell's valid_one()."""
    if not s or len(s) > NTP_MAX_HOST_LEN:
        return False
    if not _NTP_HOST_RE.match(s):
        return False
    if _NTP_DIGITS_DOTS_RE.match(s):
        # A bare run of digits and dots that is not a valid dotted quad is a
        # typo ('192.168.1'), not a hostname. Catching it here beats a
        # silently broken time source.
        octets = s.split(".")
        if len(octets) != 4:
            return False
        for o in octets:
            if not (1 <= len(o) <= 3) or int(o) > 255:
                return False
    return True


def normalize_ntp(s) -> str:
    """Collapse whitespace to single spaces; '' when there is nothing to set.

    Applied before the value is written to settings.json so the root side
    never sees the whitespace-only string, which its word-splitting loop reads
    as an empty list but its `[ -z ]` guard does not."""
    if not isinstance(s, str):
        return ""
    return " ".join(s.split())


def validate_ntp_list(s) -> tuple:
    """Strict parity with `hamclock-apply-settings.sh --validate`.

    Empty is INVALID here, exactly as it is there. The wizard wants the
    optional-field semantics instead — use validate_ntp() for that.
    Returns (ok, error_msg)."""
    if not isinstance(s, str) or not s.strip():
        return (False, "NTP server required")
    if len(s) > NTP_MAX_LEN:
        return (False, "too long (max %d characters)" % NTP_MAX_LEN)
    for one in s.split():
        if not _valid_ntp_host(one):
            return (False, "invalid server: %s" % one[:28])
    return (True, "")


def validate_ntp(s) -> tuple:
    """Wizard-facing NTP validator. The field is OPTIONAL.

    Empty means "use the distro's default time servers", which is a perfectly
    good answer and the one most operators want. A non-empty value may be a
    space-separated list — systemd's NTP= takes several, and that is how
    fallbacks are specified. Returns (ok, error_msg)."""
    if s is None:
        return (True, "")
    if not isinstance(s, str):
        return (False, "invalid value")
    if not s.strip():
        return (True, "")
    return validate_ntp_list(s)


# ---- The unprivileged half of the privilege boundary ----
#
# THIS PROCESS RUNS AS AN UNPRIVILEGED SERVICE USER WITH NO SUDO AT ALL. The
# only privileged thing it can cause to happen is the creation of an empty
# flag file under /run/hamclock-lite. A root-owned systemd .path unit notices
# that file and starts the matching root service, which then re-reads and
# re-validates everything itself and decides what to do.
#
# So this side never downloads, never verifies, never installs and never
# writes a root-owned path. It asks. What gets installed, and whether the
# request is honoured at all, is not ours to say — which is the point: a
# compromised dashboard can ask for an update it cannot choose the contents
# of, and can suggest an NTP value that root will re-check character by
# character before writing.
RUN_DIR = "/run/hamclock-lite"
UPDATE_REQUEST_NAME = "update.request"
SETTINGS_REQUEST_NAME = "settings.request"
UPDATE_STATUS_NAME = "update-status.json"

# Read cap for the status file. It is ~150 bytes; anything vastly larger is
# not a status file and is not worth parsing.
UPDATE_STATUS_MAX_BYTES = 16 * 1024


def _run_path(name, run_dir=None):
    return os.path.join(RUN_DIR if run_dir is None else run_dir, name)


def _touch_request(path):
    """Create the empty request flag. Returns True/False; never raises.

    Failure is ordinary, not exceptional: a dev box has no /run/hamclock-lite
    at all. The caller tells the operator; nothing crashes the render loop
    over it."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # O_APPEND rather than truncate: the file's existence is the entire
        # message, and PathExists= is what the unit watches.
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
        os.close(fd)
        return True
    except OSError as e:
        print("[request] could not create %s: %s" % (path, e),
              file=sys.stderr)
        return False


def request_settings_apply(run_dir=None):
    """Ask root to apply the NTP server recorded in settings.json.

    Root re-reads the file and re-validates the value; nothing about it is
    passed through this call."""
    return _touch_request(_run_path(SETTINGS_REQUEST_NAME, run_dir))


def request_update(run_dir=None):
    """Ask root to install the available update.

    This is the whole of the client's involvement in updating. It does not
    fetch the manifest, check the SHA-256, unpack, install or restart
    anything — hamclock-update.sh does all of that as root and verifies the
    download itself."""
    return _touch_request(_run_path(UPDATE_REQUEST_NAME, run_dir))


def read_update_status(run_dir=None):
    """Parse /run/hamclock-lite/update-status.json, or return None.

    A missing file is the NORMAL state — the check runs once a day and a Pi
    that has not reached 07:00 yet has never written one. So this never logs
    and never raises: no file, an unparseable file, a truncated file mid-write
    and a file that is not a JSON object all read as "nothing to say"."""
    try:
        with open(_run_path(UPDATE_STATUS_NAME, run_dir), "r") as f:
            raw = f.read(UPDATE_STATUS_MAX_BYTES)
        d = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    return d


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
        # A space-separated NTP list is the first value that can outrun the
        # box, so scroll it horizontally to keep the caret in view and clip to
        # the box instead of painting over the neighbouring row's label.
        inner_w = max(1, box_rect.w - 16)
        caret_w = font.size(self.text[:self.cursor])[0]
        scroll = max(0, caret_w - inner_w)
        prev_clip = surface.get_clip()
        surface.set_clip(box_rect.clip(prev_clip) if prev_clip else box_rect)
        surface.blit(txt, (box_rect.x + 8 - scroll,
                           box_rect.y + (box_rect.h - txt.get_height()) // 2))
        if focused:
            # Blinking caret driven by time; always drawn here for tests.
            cx = box_rect.x + 8 + caret_w - scroll
            cy = box_rect.y + 6
            pygame.draw.line(surface, theme["fg"],
                             (cx, cy), (cx, cy + box_rect.h - 12), 2)
        surface.set_clip(prev_clip)
        if self.error:
            ef = pygame.font.Font(None, 20)
            er = ef.render(self.error, True, theme["poor"])
            surface.blit(er, (box_rect.x, box_rect.y + box_rect.h + 4))


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


def setup_screen(screen, fonts, theme, initial=None, allow_cancel=False,
                 wait_ntp=True):
    """Render the setup wizard. Block until Save, return a settings dict.

    Reads events from pygame.event.get() unless HAMCLOCK_DEBUG=1 and
    HAMCLOCK_INJECT_EVENTS is set, in which case events are read from
    the named JSON file and dispatched one per frame.

    `initial`      pre-fills the fields from an existing settings dict. This
                   is what makes the wizard re-openable from the dashboard
                   ('S'): before it existed, the wizard ran only when
                   settings.json was absent, so an operator could never change
                   their NTP server or timezone from the device again.
    `allow_cancel` ESC returns None ("leave everything as it was") instead of
                   exiting the process. First boot leaves this False — there
                   is nothing to fall back to and the kiosk wrapper should
                   restart us — but killing a running dashboard because
                   someone pressed S by accident would be absurd.
    `wait_ntp`     block briefly for a clock sync before saving. Worth 10 s at
                   first boot on a Pi with no RTC, pointless once the
                   dashboard has been up and running."""
    sw, sh = screen.get_size()
    init = initial if isinstance(initial, dict) else {}

    # Resolve fonts defensively: the kiosk passes {title, panel, small, ...}
    # but tests use {tiny, small, med, lg}. Fall back to any available font.
    def _font(*names):
        for n in names:
            f = fonts.get(n)
            if f is not None:
                return f
        return next(iter(fonts.values()))
    title_font = _font("title", "lg", "med")
    panel_font = _font("panel", "med", "small")
    small_font = _font("small", "tiny")
    if title_font.get_height() < 26:
        # The kiosk's font set is sized for the 720x450 dashboard, where
        # 'title' is 15 px — smaller than the 28 px face TextField draws its
        # own labels in, so the heading would come out dwarfed by "Callsign".
        # Built once here, outside the frame loop.
        try:
            title_font = pygame.font.Font(None, 34)
        except Exception:
            pass

    # Set key repeat once (skip on x11 where the WM already handles it).
    try:
        if pygame.display.get_driver() != "x11":
            pygame.key.set_repeat(400, 40)
    except pygame.error:
        pass
    pygame.mouse.set_visible(False)

    # Panel layout. Everything below is derived from the screen size: the
    # kiosk renders at 720x450 (Tier 2a), where the old hard-coded 700x500
    # panel with its Save button pinned at y=540 fell off the bottom of the
    # display entirely — and a fourth row makes that worse, not better.
    panel_w = min(700, max(240, sw - 20))
    panel_h = min(500, max(180, sh - 20))
    px = (sw - panel_w) // 2
    py = (sh - panel_h) // 2

    label_col = min(170, max(70, panel_w // 4))
    field_x = px + label_col
    field_w = max(80, panel_w - label_col - 16)

    title_h = title_font.get_height()
    hint_h = small_font.get_height()
    content_top = py + 10 + title_h + 10
    # Two short hint lines rather than one long one: at 720x450 a single line
    # carrying both the key bindings and the "blank means distro default" note
    # is wider than the display, and centring it would clip both ends.
    hint2_y = py + panel_h - 6 - hint_h
    hint1_y = hint2_y - hint_h - 2
    save_h = min(44, max(24, panel_h // 12))
    save_y = hint1_y - 8 - save_h
    # 4 rows: callsign, timezone, ntp, theme.
    row_pitch = max(26, (save_y - content_top) // 4)
    # Leave room under each box for its inline error, which TextField draws at
    # box bottom + 4 in an ~16 px face.
    field_h = max(18, min(44, row_pitch - 18))

    def _row(i):
        return pygame.Rect(field_x, content_top + i * row_pitch,
                           field_w, field_h)

    call_field = TextField(
        _row(0),
        initial=str(init.get("callsign") or ""), max_len=10,
        validator=lambda s: validate_callsign(s.upper()),
        label="Callsign")
    tz_field = TextField(
        _row(1),
        initial=str(init.get("timezone") or ""), max_len=64,
        validator=validate_timezone, label="Timezone")
    # Optional. 128 is well past the two or three servers an operator
    # realistically lists, and far short of the 512-char cap the root applier
    # enforces, so the field can never produce a value the shell calls
    # over-long.
    ntp_field = TextField(
        _row(2),
        initial=normalize_ntp(init.get("ntp") or ""), max_len=128,
        validator=validate_ntp, label="NTP server")
    theme_row = _row(3)
    try:
        theme_idx = WIZARD_THEMES.index(init.get("theme"))
    except ValueError:
        theme_idx = 0
    # The theme row is not a TextField but sits on the same grid, so it is
    # lettered in the same face TextField.draw uses for its labels — built
    # once here rather than per frame.
    try:
        form_font = pygame.font.Font(None, 28)
    except Exception:
        form_font = panel_font
    focus = 0  # 0=call, 1=tz, 2=ntp, 3=theme, 4=save
    fields = [call_field, tz_field, ntp_field]
    n_focus = len(fields) + 2
    save_focus = n_focus - 1
    theme_focus = len(fields)

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
                if allow_cancel:
                    return None
                sys.exit(1)

            if focus < len(fields):
                res = fields[focus].handle_event(ev)
                if res == "next":
                    focus = (focus + 1) % n_focus
                elif res == "prev":
                    focus = (focus - 1) % n_focus
                elif res == "submit":
                    focus = save_focus
                elif res == "cancel":
                    if allow_cancel:
                        return None
                    sys.exit(1)
            elif focus == theme_focus:  # theme cycler
                if ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_LEFT,):
                        theme_idx = (theme_idx - 1) % len(WIZARD_THEMES)
                    elif ev.key in (pygame.K_RIGHT,):
                        theme_idx = (theme_idx + 1) % len(WIZARD_THEMES)
                    elif ev.key in (pygame.K_TAB, pygame.K_DOWN, pygame.K_RETURN):
                        focus = save_focus
                    elif ev.key == pygame.K_UP:
                        focus = theme_focus - 1
            elif focus == save_focus:  # Save button
                if ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_TAB, pygame.K_DOWN):
                        focus = 0
                    elif ev.key == pygame.K_UP:
                        focus = theme_focus
                    elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                        # Re-validate every field.
                        oks = [f._validate() for f in fields]
                        if all(oks):
                            if wait_ntp:
                                _wait_for_ntp_sync(deadline_s=10.0)
                            result = {
                                "callsign": call_field.text.upper(),
                                "timezone": tz_field.text,
                                "theme": WIZARD_THEMES[theme_idx],
                                # Normalised here so the root applier never
                                # sees ragged whitespace — its own guard reads
                                # a whitespace-only string as non-empty but
                                # word-splits it to nothing.
                                "ntp": normalize_ntp(ntp_field.text),
                            }
                            running = False
                        else:
                            focus = oks.index(False)

        # Draw.
        screen.fill(theme["bg"])
        pygame.draw.rect(screen, theme["card"],
                         pygame.Rect(px, py, panel_w, panel_h))
        heading = "HAMCLOCK SETTINGS" if initial is not None else "HAMCLOCK SETUP"
        title = title_font.render(heading, True, theme["fg"])
        screen.blit(title, (sw // 2 - title.get_width() // 2, py + 10))

        for i, fld in enumerate(fields):
            fld.draw(screen, theme, focused=(focus == i))

        # Theme cycler row, on the same grid as the fields above it.
        lbl = form_font.render("Theme", True, theme["label"])
        screen.blit(lbl, (field_x - lbl.get_width() - 14,
                          theme_row.y + (field_h - lbl.get_height()) // 2))
        cur = WIZARD_THEMES[theme_idx]
        arrows = "< %s >" % cur if focus == theme_focus else "  %s  " % cur
        col = theme["accent"] if focus == theme_focus else theme["fg"]
        arr = form_font.render(arrows, True, col)
        screen.blit(arr, (field_x + 8,
                          theme_row.y + (field_h - arr.get_height()) // 2))

        # Save button.
        save_w = min(200, max(90, panel_w // 4))
        save_rect = pygame.Rect(sw // 2 - save_w // 2, save_y, save_w, save_h)
        save_col = theme["accent"] if focus == save_focus else theme["muted"]
        pygame.draw.rect(screen, theme["card"], save_rect)
        pygame.draw.rect(screen, save_col, save_rect, 3)
        sv = form_font.render("Save", True, theme["fg"])
        screen.blit(sv, (save_rect.centerx - sv.get_width() // 2,
                         save_rect.centery - sv.get_height() // 2))

        for _hy, _ht in (
                (hint1_y, "NTP server is optional - leave it blank to use "
                          "the distro defaults"),
                (hint2_y, "Tab to move, Enter to save"
                          + (", Esc to leave it as it was"
                             if allow_cancel else ""))):
            _hs = small_font.render(_fit_text(small_font, _ht, panel_w - 16),
                                    True, theme["muted"])
            screen.blit(_hs, (sw // 2 - _hs.get_width() // 2, _hy))

        pygame.display.flip()
        if injected_events is None:
            clock.tick(30)
        else:
            clock.tick(0)  # no throttle in tests

    if result is None:
        if allow_cancel:
            # Re-opened from a running dashboard: QUIT or the frame cap means
            # "the operator walked away", and the settings they already have
            # are a better answer than half-typed ones.
            return None
        # QUIT/timeout at first boot: return current values with fallbacks.
        result = {
            "callsign": call_field.text.upper(),
            "timezone": tz_field.text if validate_timezone(tz_field.text)[0] else "UTC",
            "theme": WIZARD_THEMES[theme_idx],
            "ntp": (normalize_ntp(ntp_field.text)
                    if validate_ntp(ntp_field.text)[0] else ""),
        }
    return result


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

# Tier 2b: per-panel redraw cadence (seconds). The render loop still ticks at
# 10 FPS so click latency stays bounded (<= ~200 ms p99), but each draw_<x>
# function only runs when its panel's cadence has elapsed since the last
# redraw. Clock-driven panels (header, status) tick every second; data panels
# poll for a refresh every 60 s — the underlying data layer refreshes every
# 5 min (solar/bands) or 2 min (DX), so 60 s here is "check whether the data
# changed", not "force a redraw on stale data". Tab clicks trigger a full
# flip on the next frame via dirty_state['full_flip_pending'], which bypasses
# this table.
_CADENCE_S = {
    'header': 1.0,
    'status': 1.0,
    'solar': 60.0,
    'bands': 60.0,
    'geomag': 60.0,
    'xray': 60.0,
    'open_bands': 60.0,
    'muf_text': 60.0,
    'sdo': 60.0,
    'dx_spots': 60.0,
    'band_activity': 60.0,
    'propagation': 60.0,
}

# Tier 2.5: cadence for the two image panels while they have NO decoded image
# to show. Their body is then a status line ("fetching...", "feed down /
# retry 15s"), and a countdown that only moves once a minute reads as a hang —
# which is the exact confusion this tier exists to remove.
# Deliberately 15 s and not 5 s: "no image" is a *persistent* state during an
# outage, not a transient, so a 5 s cadence would be a 12x idle-CPU and
# glyph/redraw amplifier on a single-core ARMv6 box for precisely the hours
# when the box is least able to spare it. 15 s also lines up with the fastest
# rung of hamclock_data.IMAGE_RETRY_BACKOFF once the streak is a few deep.
# Never faster than its _CADENCE_S entry, and only for keys that have one.
_CADENCE_S_NO_IMAGE = {
    'sdo': 15.0,
    'propagation': 15.0,
}

def _build_panel_phase():
    """One-time offset per panel so panels sharing a cadence do not all come
    due on the same frame.

    Every 60 s panel was initialised to the same due time and then rescheduled
    to now+60, so all ten redrew together, forever. Their measured cost on a
    Pi 1B sums to ~203 ms and the reported p90 was 176 ms — i.e. the p90 frame
    WAS the pile-up. It was survivable at 800x600 and became a visible stutter
    once the client started rendering at the panel's native 1440x900.

    Spreading them across the cadence window means at most one lands per frame,
    so the worst frame costs one panel (~58 ms) instead of ten.
    """
    by_cadence = {}
    for name, c in _CADENCE_S.items():
        by_cadence.setdefault(c, []).append(name)
    phase = {}
    for c, names in by_cadence.items():
        names = sorted(names)
        for i, name in enumerate(names):
            phase[name] = (float(c) * i) / max(1, len(names))
    return phase


_PANEL_PHASE = _build_panel_phase()


def _next_due(name, now_ts, phased):
    """Next due time for `name`. The phase is applied ONCE — after that each
    panel simply advances by its cadence, so the spread persists instead of
    re-synchronising the way `now + cadence` alone did."""
    c = _CADENCE_S[name]
    if name in phased:
        return now_ts + c
    phased.add(name)
    return now_ts + c + _PANEL_PHASE.get(name, 0.0)


SCREEN_W = 720    # Tier 2a: native render at 720x450; BCM2835 HVS upscales to 1440x900 in firmware
SCREEN_H = 450

# Propagation panel tabs (module-level so the wiring is testable and lives in
# one place). Each tab maps to one of hamclock_data's _IMAGE_ENDPOINTS keys;
# the render loop resolves the active tab through PROP_TAB_IMAGE_KEY and blits
# data.images[key]. 'muf' surfaces the KC2G MUF map the server already fetches
# and rasterizes to PNG for /api/muf-map — decoded lazily only when selected,
# so idle RAM/FPS on the Pi 1B are unchanged.
#: The MUF map is NOT here: it now occupies the centre panel full time, so a
#: tab showing the same map would spend a quarter of the cycle duplicating what
#: is already on screen. PROP_TAB_IMAGE_KEY keeps its 'muf' entry so anything
#: that still resolves that name (older settings, tests) maps correctly.
PROP_TABS = ['drap', 'aurora', 'enlil']

#: How long each propagation tab stays up before the panel advances itself.
#: This is a wall display: nobody is standing at it clicking through tabs, so a
#: tab that is never selected is a map the operator never sees. Cycling makes
#: all four reachable without input. Five minutes is long enough to actually
#: read a map and short enough to see every one within a coffee break.
#: Set to 0 to disable and leave the panel wherever it was last put.
TAB_CYCLE_S = 300.0


def _next_cycle_tab(current, tabs, key_map, data):
    """The tab to show next, skipping ones with no image to show.

    A feed that is down (or has not loaded yet) would otherwise hold the panel
    blank for a full interval, which on a five-minute cycle is a quarter of the
    rotation spent showing nothing. Skipping it costs nothing when everything
    is healthy and degrades gracefully when it is not.

    Falls back to plain next-in-order when NOTHING has an image — during the
    first seconds after boot that is the normal state, and refusing to advance
    would pin the panel to whatever tab happened to be first.
    """
    if not tabs:
        return current
    n = len(tabs)
    try:
        i = tabs.index(current)
    except ValueError:
        i = 0
    images = getattr(data, 'images', None)
    if isinstance(images, dict):
        for step in range(1, n + 1):
            cand = tabs[(i + step) % n]
            if images.get(key_map.get(cand)):
                return cand
    return tabs[(i + 1) % n]
PROP_TAB_IMAGE_KEY = {
    'drap': 'real-drap',
    'aurora': 'drap',
    'enlil': 'enlil',
    'muf': 'muf-map',
}

# ---- Phase 1b: layout / counts / string / solar caches ----
# Item 5: panel rect grid is recomputed only when screen size changes; every
# per-frame pygame.Rect(...) panel allocation now reads from this dict.
_layout_cache: dict = {"size": None, "rects": None}


def _get_layout(screen_size):
    """Cache the dashboard layout rects; recompute only on resize."""
    if _layout_cache["size"] == screen_size:
        return _layout_cache["rects"]
    sw, sh = screen_size
    # Chrome heights scale with the framebuffer for the same reason the fonts
    # do: at 1440x900 the title face is 35 px, and a hardcoded 30 px header
    # bar meant the callsign painted straight out of its own rect and over the
    # panel below. Derived from the same factor so the two can never drift.
    _s = _font_scale(screen_size)
    header_h = int(round(30 * _s))
    status_h = int(round(20 * _s))
    content_top = header_h + 2
    content_bot = sh - status_h - 2
    content_h = content_bot - content_top
    left_w = int(sw * 288 / 1440)
    mid_w = int(sw * (936 - 288) / 1440)
    right_w = sw - left_w - mid_w
    panel_gap = 4
    # Tier 1.1: the left column's six panels share content_h minus 6x26 px of
    # panel chrome (title bar + padding, see _panel_inner_rect) and 5x4 px of
    # gap — at 720x450 that is 396 - 176 = 220 px of usable content for the
    # whole column. The old split gave BANDS 12 % (a 21 px inner rect) which
    # cannot hold its header row plus four band rows at ANY font size, and
    # gave GEOMAG/X-RAY 10 % (13 px) for a text row plus a bar. These weights
    # size each panel to what its draw function actually needs at the 8-11 px
    # fonts _make_fonts builds for the 720x450 framebuffer:
    #   solar  53 px = 5 rows x 10 px pitch + 11 px glyph (2 columns of 5)
    #   bands  53 px = header + 4 band rows, same pitch
    #   sdo    53 px of image (letterboxed square)
    #   geomag 17 px = 11 px value row + 6 px bar   (x-ray identical)
    #   open   27 px = 2 wrapped label rows
    # Everything stays fractional, so 1440x900 scales up unchanged in shape.
    heights = [
        int(content_h * 0.20),  # solar
        int(content_h * 0.20),  # bands
        int(content_h * 0.20),  # sdo
        int(content_h * 0.11),  # geomag
        int(content_h * 0.11),  # xray
    ]
    heights.append(content_h - sum(heights) - panel_gap * 5)
    titles = ['solar', 'bands', 'sdo', 'geomag', 'xray', 'open_bands']
    rects = {
        "header": pygame.Rect(0, 0, sw, header_h),
        "status": pygame.Rect(0, sh - status_h, sw, status_h),
    }
    cy = content_top
    for h, key in zip(heights, titles):
        rects[key] = pygame.Rect(2, cy, left_w - 4, h)
        cy += h + panel_gap
    mx = 2 + left_w
    rects["muf"] = pygame.Rect(mx, content_top, mid_w - 4, content_h)
    rx = mx + mid_w
    rh_dx = int(content_h * 0.28)
    rh_ba = int(content_h * 0.32)
    rh_prop = content_h - rh_dx - rh_ba - panel_gap * 2
    rects["dx_spots"] = pygame.Rect(rx, content_top, right_w - 4, rh_dx)
    rects["band_activity"] = pygame.Rect(
        rx, content_top + rh_dx + panel_gap, right_w - 4, rh_ba)
    rects["propagation"] = pygame.Rect(
        rx, content_top + rh_dx + rh_ba + panel_gap * 2,
        right_w - 4, rh_prop)
    _layout_cache["size"] = screen_size
    _layout_cache["rects"] = rects
    return rects


# Item 6: draw_band_activity pre-allocated counts (no per-frame dict alloc).
_band_counts: list = [0] * len(HF_BANDS)

# Item 7: cached OPEN / CLOSED label strings keyed by data.last_data_refresh.
_open_bands_cache: dict = {"ts": None, "open": "", "closed": ""}


def _open_bands_strings(bands, data_refresh_ts):
    """Return the cached (open_label, closed_label) strings; refresh only
    on a new data.last_data_refresh tick."""
    if _open_bands_cache["ts"] == data_refresh_ts:
        return _open_bands_cache["open"], _open_bands_cache["closed"]
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
    o = 'OPEN: ' + (', '.join(opens) or '--')
    c = 'CLOSED: ' + (', '.join(closes) or '--')
    _open_bands_cache["ts"] = data_refresh_ts
    _open_bands_cache["open"] = o
    _open_bands_cache["closed"] = c
    return o, c


# Item 8: header / status / Kp string format cache keyed by
# (int(time.time()), data.last_data_refresh, data.last_image_refresh, dx_len).
_strfmt_cache: dict = {
    "key": None, "utc": "", "local": "", "status": "", "kp": "",
}


def _formatted_strings(data):
    """Return cached strings for header (utc, local), status bar, and Kp.
    Refreshes once per UTC second OR on a data/image refresh tick."""
    try:
        now_sec = int(time.time())
    except Exception:
        now_sec = 0
    dx_len = len(data.dxspots) if isinstance(data.dxspots, list) else 0
    key = (now_sec, data.last_data_refresh,
           data.last_image_refresh, dx_len,
           bool(data.solar), bool(data.bands))
    if _strfmt_cache["key"] == key:
        return _strfmt_cache
    try:
        utc = time.strftime('%H:%M:%S', time.gmtime())
        local = time.strftime('%H:%M:%S')
    except Exception:
        utc = local = '--:--:--'
    dage = int(now_sec - data.last_data_refresh) if data.last_data_refresh else -1
    iage = int(now_sec - data.last_image_refresh) if data.last_image_refresh else -1
    _strfmt_cache["utc"] = 'UTC ' + utc
    _strfmt_cache["local"] = 'LOC ' + local
    _strfmt_cache["status"] = 'Data:{}s  Img:{}s  Solar:{}  Bands:{}  DX:{}'.format(
        dage if dage >= 0 else '--',
        iage if iage >= 0 else '--',
        'OK' if data.solar else '--',
        'OK' if data.bands else '--',
        dx_len,
    )
    kp = _safe(data.solar or {}, 'kIndex', 0) if data.solar is not None else 0
    _strfmt_cache["kp"] = 'Kp {}'.format(kp)
    _strfmt_cache["key"] = key
    return _strfmt_cache


# Item 9: de-nested solar snapshot keyed by data.last_data_refresh.
_solar_snapshot: dict = {"ts": None, "view": {}}


def _solar_view(solar, data_refresh_ts):
    """Single de-nested view of solar dict; refreshed only on data refresh."""
    if _solar_snapshot["ts"] == data_refresh_ts and _solar_snapshot["view"]:
        return _solar_snapshot["view"]
    s = solar or {}
    _solar_snapshot["view"] = {
        'sfi':         _safe(s, 'sfi'),
        'kIndex':      _safe(s, 'kIndex'),
        'ssn':         _safe(s, 'ssn'),
        'aIndex':      _safe(s, 'aIndex'),
        'xray':        _safe(s, 'xray'),
        'solarWind':   _safe(s, 'solarWind'),
        'bz':          _safe(s, 'bz'),
        'geomagField': _safe(s, 'geomagField'),
        'signalNoise': _safe(s, 'signalNoise'),
        'fof2':        _safe(s, 'fof2'),
        'kIndex_raw':  _safe(s, 'kIndex', 0),
        'xray_raw':    _safe(s, 'xray', 'A0.0'),
    }
    _solar_snapshot["ts"] = data_refresh_ts
    return _solar_snapshot["view"]


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


# ---- Scaled-image cache (Phase 1 perf fix #1) ----
# Keyed by (image_key, fetched_at, (w, h)) -> scaled pygame.Surface.
# Cap 16: dashboard has 5 image slots × 1 active scale each = 5; 16 leaves
# margin for tab changes. Eviction is LRU (popitem(last=False) on overflow).
_SCALED_CACHE_CAP = 16
_scaled_cache = collections.OrderedDict()

# ---- Per-font AA flag (Tier-1a perf) ----
# pygame.font.Font is a C-extension type that rejects arbitrary attribute
# assignment, so we side-channel the AA flag through a module-level dict
# keyed by id(font_obj). _make_fonts populates it; _blit_text reads it.
# The dict is cleared in _make_fonts alongside _glyph_cache.
_font_aa = {}


#: The framebuffer the base font sizes below were measured against. Every
#: size is scaled by the ratio of the real surface to this, so the layout is
#: tuned for whatever mode we actually get rather than one we hoped for.
FONT_REF_W, FONT_REF_H = 720, 450

#: Keep the scale sane if a mode is wildly different from the reference — a
#: 4x font on a huge panel would look absurd, and shrinking below 1.0 makes an
#: already-small face illegible on a display being read across a room.
FONT_SCALE_MIN, FONT_SCALE_MAX = 1.0, 2.5


def _font_scale(size):
    """Scale factor for the fonts given the real surface size.

    Under KMS the framebuffer is a REAL DRM mode, not an arbitrary size: the
    legacy framebuffer_width/height knobs only worked with the old firmware
    scaler, which KMS removed. Asking for 720x450 therefore gets snapped to the
    nearest mode the connector actually offers — a Pi 1 on a 1440x900 panel
    lands on 800x600 — so hardcoding sizes for 720x450 means the layout is
    tuned for a mode that never appears. Derive from the surface instead.

    Uses min(w, h) ratio so text never outgrows the shorter axis, which is what
    actually runs out of room first on these panels.
    """
    try:
        w, h = size
        if w <= 0 or h <= 0:
            return 1.0
        s = min(w / float(FONT_REF_W), h / float(FONT_REF_H))
        return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, s))
    except Exception:
        return 1.0


def _make_fonts(screen_size=None):
    """Build the fonts dict. Falls back to default font if SysFont fails.

    Base sizes are measured against a 720x450 framebuffer and scaled to the
    surface actually in use (see _font_scale). Pass screen_size explicitly in
    tests; otherwise the live display surface is queried.
    """
    # Ensure font subsystem is up; callers (incl. recovery-overlay tests) may
    # only have initialized pygame.display, leaving pygame.font uninitialized.
    try:
        if not pygame.font.get_init():
            pygame.font.init()
    except Exception:
        pass
    if screen_size is None:
        try:
            _surf = pygame.display.get_surface()
            screen_size = _surf.get_size() if _surf is not None else None
        except Exception:
            screen_size = None
    _scale = _font_scale(screen_size or (FONT_REF_W, FONT_REF_H))

    def mk(size):
        size = max(6, int(round(size * _scale)))
        try:
            f = pygame.font.SysFont('monospace', size)
            if f is None:
                raise RuntimeError('no monospace')
            return f
        except Exception:
            return pygame.font.Font(None, size + 4)
    _glyph_cache.clear()
    _font_aa.clear()
    # Sizes render at the 720x450 native framebuffer and are doubled by the
    # HVS on the way to a 1440x900 panel, so the effective on-screen size is
    # 2x each number below. Tier 2a had halved these (13/9/9/8/7/7) alongside
    # the resolution drop, which put body text at 9 px -> 18 px effective with
    # antialiasing off — legible, but visibly pixelated on a real monitor, and
    # this display is read by operators who should not have to squint.
    # Antialiasing, not size, is what fixes the pixelated look — so the sizes
    # move only where there is genuinely room.
    #
    # 'title' and 'panel' head up panels and the big MUF STATUS readouts, which
    # have space to spare, and go up.
    #
    # 'body' and 'label' stay at the Tier 2a sizes because SOLAR sets a hard
    # ceiling: ten label+value pairs inside a 132 px inner rect is ~66 px per
    # column, split between the two. Rendering that at 10 px clips live
    # readings — verified on screen, C1.0 became "C1…" and 456.9 became "45…".
    # A bigger font that eats the measurement is a bad trade; the operator can
    # read the large values in MUF STATUS, and _fit_text now marks anything
    # that does clip.
    fonts = {
        'title': mk(15),    # 30 px effective on the 1440x900 panel
        'panel': mk(11),    # 22 px effective
        'body':  mk(9),     # SOLAR-limited; do not raise without widening it
        'label': mk(8),     # SOLAR-limited
        'small': mk(8),
        'tiny':  mk(8),
    }
    # Antialias everything. The old policy (AA on 'title' only) cited a 5-10x
    # cost for the AA glyph path, but that predates the Tier-1a glyph cache:
    # measured here, AA is ~10-20% slower per render (2.1 -> 2.3 us at size 9)
    # and every unique string is rendered once and reused, so the steady-state
    # cost is a few clock digits per second. The real price is memory — an AA
    # glyph is a 32-bit SRCALPHA surface, 4x the 8-bit flat one — which at
    # _GLYPH_CACHE_CAP=256 tops out around 1.4 MB. That is affordable on 512 MB
    # and buys smooth edges on every panel instead of just the header.
    # Side-channel via id() because pygame.font.Font rejects attribute
    # assignment. NOTE: _blit_text must preserve per-pixel alpha for these —
    # see the convert() guard there, without which AA glyphs paint as solid
    # filled rectangles.
    for name, f in fonts.items():
        _font_aa[id(f)] = True
    return fonts


def _safe(d, key, default='--'):
    try:
        v = d.get(key)
        if v is None or v == '':
            return default
        return v
    except Exception:
        return default


def _blit_text(screen, font, text, color, x, y):
    try:
        s = str(text)
        if not isinstance(color, tuple):
            color = tuple(color)
        key = (_font_key(font), s, color)
        surf = _glyph_cache.get(key)
        if surf is None:
            # Tier-1a perf: per-font AA flag (set in _make_fonts) read via
            # the _font_aa side-channel dict (pygame Font rejects attr set).
            # AA only for 'title' at 22 px; smaller fonts render flat to
            # dodge the 5-10x AA cost on armv6. Default True for fonts not
            # registered (e.g. ad-hoc fonts in recovery overlay).
            aa = _font_aa.get(id(font), True)
            surf = font.render(s, aa, color)
            # Tier-1a perf: convert glyph to the display's pixel format once
            # at cache-insert time so subsequent blits skip the per-pixel
            # format-conversion the blitter would otherwise pay.
            try:
                disp = pygame.display.get_surface()
                if disp is not None:
                    # An antialiased render (the 'title' font, see _make_fonts)
                    # comes back as a 32-bit SRCALPHA surface whose RGB is the
                    # text colour EVERYWHERE — the glyph shape lives entirely
                    # in the alpha channel. Surface.convert() drops that
                    # channel, so every AA'd string painted as a solid filled
                    # rectangle: the 'HAMCLOCK LITE' banner and all five MUF
                    # STATUS values were unreadable blocks on the real display
                    # as well as headless. Keep the alpha for those.
                    if surf.get_flags() & pygame.SRCALPHA:
                        surf = surf.convert_alpha(disp)
                    else:
                        surf = surf.convert(disp)
            except Exception:
                pass
            _glyph_cache[key] = surf
            if len(_glyph_cache) > _GLYPH_CACHE_CAP:
                _glyph_cache.popitem(last=False)
        else:
            _glyph_cache.move_to_end(key)
        screen.blit(surf, (x, y))
        return surf.get_width()
    except Exception:
        return 0


def _fit_text(font, text, max_w):
    """Return `text` truncated so it renders within `max_w` px.

    Tier 1.1: at the 720x450 native framebuffer the narrowest panel content
    rect is 128 px, so a long value ('Very Unsettled', a 10-char spotter)
    would otherwise paint straight over the panel border and into its
    neighbour. Fast path is a single Font.size() and the original string back
    (no allocation); the binary search only runs when the text overflows, and
    only on a panel's cadence tick, not per frame.

    Truncation is always MARKED. Cutting by character alone turns an SFI of
    148 into "14" and a solar wind of 456.9 into "45" - still perfectly
    plausible numbers, so the operator has no way to know they are reading a
    fragment. A trailing marker makes a clipped value obviously clipped. It
    costs one character of width, which is the correct trade against silently
    reporting wrong space weather.
    """
    try:
        if max_w <= 0:
            return ''
        if font.size(text)[0] <= max_w:
            return text
        for mark in ('…', '~'):     # ellipsis, then ASCII fallback
            mark_w = font.size(mark)[0]
            if mark_w <= max_w:
                break
        else:
            return ''
        budget = max_w - mark_w
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.size(text[:mid])[0] <= budget:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + mark
    except Exception:
        return text


def _blit_fit(screen, font, text, color, x, y, max_w):
    """_blit_text with a hard width clamp. See _fit_text."""
    return _blit_text(screen, font, _fit_text(font, text, max_w), color, x, y)


def _smoothscale_safe(surface, size):
    """smoothscale that cannot raise on a sub-24-bit source surface.

    pygame.transform.smoothscale accepts only 24- and 32-bit surfaces and
    raises ValueError("Only 24-bit or 32-bit surfaces can be smoothly
    scaled") on 8bpp AND 16bpp (verified, pygame 2.6.1). Both depths occur on
    the shipped Pi: 10-monitor.conf sets DefaultDepth 16, and /api/real-drap
    — the DEFAULT propagation tab — decodes as an 8bpp palettised PNG. Every
    image panel would then silently paint nothing via draw_image's bare
    `except Exception: pass`.

    Promote to 24-bit and retry; if even that fails, fall back to
    transform.scale, which is nearest-neighbour but depth-agnostic (a coarse
    image beats a blank panel). Deliberately NOT a convert() against a
    template built from pygame.display.get_surface().get_flags(): the display
    is created with pygame.FULLSCREEN, so get_flags() returns 2164260864
    (> INT32_MAX) and Surface((w, h), flags, ...) raises OverflowError.
    """
    try:
        if surface.get_bitsize() < 24:
            try:
                surface = surface.convert(24)
            except Exception:
                return pygame.transform.scale(surface, size)
        return pygame.transform.smoothscale(surface, size)
    except Exception:
        return pygame.transform.scale(surface, size)


def _load_image(data_bytes):
    """Decode JPEG/PNG bytes into a Pygame surface, or None on failure."""
    if not data_bytes:
        return None
    # Tier 1.5: never hand SVG to SDL_image. nanosvg happily "succeeds" on the
    # 365 KB KC2G MUF vector in 104-193 ms on x86 (3.1-5.2 s on ARMv6) and
    # yields a 1526x905 / 5,524,120-byte surface — ~11 MB peak once .convert()
    # copies it — on a 512 MB box, in pure greyscale because nanosvg ignores
    # the CSS that carries the contour colours. That is a multi-second
    # render-loop freeze every 900 s for a panel that is 236x102 px and would
    # be unreadable anyway. The server is meant to send PNG; if it ever falls
    # back to the raw vector we want a blank panel, not a stall.
    try:
        head = data_bytes[:256].lstrip()
        if head[:5] == b'<?xml' or head[:4] == b'<svg':
            return None
    except Exception:
        pass
    for hint in ('x.jpg', 'x.png'):
        try:
            return pygame.image.load_extended(io.BytesIO(data_bytes), hint).convert()
        except Exception:
            continue
    try:
        return pygame.image.load(io.BytesIO(data_bytes)).convert()
    except Exception:
        return None


def draw_panel(screen, rect, title, fonts, theme):
    pygame.draw.rect(screen, theme['card'], rect)
    pygame.draw.rect(screen, theme['border'], rect, 1)
    bar = pygame.Rect(rect.x, rect.y, rect.w, 18)
    pygame.draw.rect(screen, theme['border'], bar)
    _blit_text(screen, fonts['panel'], title, theme['bright'],
               rect.x + 6, rect.y + 2)
    return _panel_inner_rect(rect)


def _panel_inner_rect(rect):
    """Compute the inner content rect for a panel without painting the chrome.

    Tier 2b uses this on frames where a panel's cadence has NOT elapsed,
    so we can still hand its inner rect to a no-op skip path while not
    re-blitting the title bar and border. Keep this formula in lockstep
    with draw_panel's return value above."""
    return pygame.Rect(rect.x + 6, rect.y + 22, rect.w - 12, rect.h - 26)


def draw_header(screen, rect, callsign, fonts, theme, data=None):
    """Item 8: pull pre-formatted UTC/LOC strings from _strfmt_cache when a
    HamClockData reference is available; the cache hits on every same-second
    frame, eliminating per-frame strftime + Font.render churn."""
    pygame.draw.rect(screen, theme['card'], rect)
    pygame.draw.rect(screen, theme['border'], rect, 1)
    # Tier 1.1: columns are fractions of rect.w so the header keeps its shape
    # at the 720x450 framebuffer instead of assuming the old 1440 px width.
    title_x = rect.x + 8
    call_x = rect.x + int(rect.w * 0.30)
    utc_x = rect.x + int(rect.w * 0.53)
    loc_x = rect.x + int(rect.w * 0.75)
    dot_x = rect.x + rect.w - 18
    _blit_fit(screen, fonts['title'], 'HAMCLOCK LITE', theme['accent'],
              title_x, rect.y + 4, call_x - title_x - 4)
    if callsign:
        _blit_fit(screen, fonts['body'], str(callsign), theme['bright'],
                  call_x, rect.y + 8, utc_x - call_x - 4)
    if data is not None:
        cached = _formatted_strings(data)
        utc_str = cached["utc"]
        local_str = cached["local"]
    else:
        try:
            utc_str = 'UTC ' + time.strftime('%H:%M:%S', time.gmtime())
            local_str = 'LOC ' + time.strftime('%H:%M:%S')
        except Exception:
            utc_str = local_str = '--:--:--'
    _blit_fit(screen, fonts['body'], utc_str, theme['fg'],
              utc_x, rect.y + 8, loc_x - utc_x - 4)
    _blit_fit(screen, fonts['body'], local_str, theme['fg'],
              loc_x, rect.y + 8, dot_x - 6 - loc_x)
    dot_color = theme['good'] if (int(time.time()) % 2 == 0) else theme['fair']
    pygame.draw.circle(screen, dot_color, (dot_x, rect.y + 14), 5)


def draw_solar(screen, rect, solar, fonts, theme, data_refresh_ts=None):
    """Item 9: pull values from _solar_view snapshot when a refresh ts is
    known so the per-frame _safe(...) chain runs at most once per refresh."""
    v = _solar_view(solar, data_refresh_ts) if data_refresh_ts is not None else None
    if v is not None:
        rows = [
            ('SFI', v['sfi']), ('Kp', v['kIndex']), ('SSN', v['ssn']),
            ('A', v['aIndex']), ('Xray', v['xray']),
            ('Wind', v['solarWind']), ('Bz', v['bz']),
            ('Geo', v['geomagField']), ('S/N', v['signalNoise']),
            ('foF2', v['fof2']),
        ]
    else:
        rows = [
            ('SFI', _safe(solar, 'sfi')),
            ('Kp', _safe(solar, 'kIndex')),
            ('SSN', _safe(solar, 'ssn')),
            ('A', _safe(solar, 'aIndex')),
            ('Xray', _safe(solar, 'xray')),
            ('Wind', _safe(solar, 'solarWind')),
            ('Bz', _safe(solar, 'bz')),
            ('Geo', _safe(solar, 'geomagField')),
            ('S/N', _safe(solar, 'signalNoise')),
            ('foF2', _safe(solar, 'fof2')),
        ]
    # Tier 1.1: ten label/value rows at the old fixed pitch 16 needed 160 px;
    # the SOLAR content rect at 720x450 is 128x53. Derive the pitch from the
    # font and wrap into as many columns as it takes to fit, so the panel
    # degrades by getting narrower cells rather than by painting over its
    # neighbours. Values are clamped to their cell width.
    lab_f, val_f = fonts['label'], fonts['body']
    lab_h, val_h = lab_f.get_height(), val_f.get_height()
    glyph_h = max(lab_h, val_h)
    n = len(rows)
    if n == 0 or rect.w <= 0 or rect.h < glyph_h:
        return
    ncols, per_col = 1, n
    for ncols in range(1, 5):
        per_col = -(-n // ncols)
        if (per_col - 1) * lab_h + glyph_h <= rect.h:
            break
    pitch = lab_h if per_col < 2 else max(
        lab_h, min(lab_h + 4, (rect.h - glyph_h) // (per_col - 1)))
    col_w = rect.w // ncols
    # Give the labels exactly the width they need and hand the rest to the
    # values. The old 'MMMMMM' constant reserved six monospace M's for every
    # label, which at a 66 px column left the value under half the cell —
    # enough to clip a live 456.9 down to a plausible-looking 45. The reading
    # is the payload here; the label is only context.
    lab_max = max((lab_f.size(l)[0] for l, _ in rows), default=0)
    val_x = min(col_w // 2, lab_max + 4)
    lab_w = val_x - 2
    val_w = col_w - val_x - 2
    for i, (label, value) in enumerate(rows):
        y = rect.y + (i % per_col) * pitch
        if y + glyph_h > rect.bottom:
            continue
        cx = rect.x + (i // per_col) * col_w
        _blit_fit(screen, lab_f, label, theme['label'], cx, y, lab_w)
        _blit_fit(screen, val_f, str(value), theme['bright'],
                  cx + val_x, y, val_w)


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
    # Tier 1.1: DAY at +100 and NIGHT at +160 were absolute pixels for the
    # 1440x900 dashboard; the BANDS content rect at 720x450 is 128 px wide, so
    # NIGHT was drawn 32 px past the panel's right border. Columns are now
    # fractions of rect.w and the row pitch comes from the font.
    lab_f, val_f = fonts['label'], fonts['body']
    lab_h, glyph_h = lab_f.get_height(), max(fonts['label'].get_height(),
                                             val_f.get_height())
    if rect.w <= 0 or rect.h < glyph_h:
        return
    n = len(groups) + 1                       # header row + one row per group
    pitch = max(lab_h, min(lab_h + 4, (rect.h - glyph_h) // max(1, n - 1)))
    name_x = rect.x
    day_x = rect.x + int(rect.w * 0.42)
    night_x = rect.x + int(rect.w * 0.71)
    name_w = day_x - name_x - 2
    day_w = night_x - day_x - 2
    night_w = rect.right - night_x
    _blit_fit(screen, lab_f, 'BAND', theme['label'], name_x, rect.y, name_w)
    _blit_fit(screen, lab_f, 'DAY', theme['label'], day_x, rect.y, day_w)
    _blit_fit(screen, lab_f, 'NIGHT', theme['label'], night_x, rect.y, night_w)
    y = rect.y + pitch
    for name, keys in groups:
        if y + glyph_h > rect.bottom:
            break
        entry = bands.get(keys[0], {}) if isinstance(bands, dict) else {}
        day = entry.get('day', 'N/A') if isinstance(entry, dict) else 'N/A'
        night = entry.get('night', 'N/A') if isinstance(entry, dict) else 'N/A'
        _blit_fit(screen, val_f, name, theme['fg'], name_x, y, name_w)
        _blit_fit(screen, val_f, str(day),
                  cond.get(day, theme['fg']), day_x, y, day_w)
        _blit_fit(screen, val_f, str(night),
                  cond.get(night, theme['fg']), night_x, y, night_w)
        y += pitch


def _draw_status_lines(screen, rect, text, font, color,
                       top=True, backdrop=None):
    """Paint up to two short status lines inside rect. Never raises.

    Tier 2.5. `text` may carry a single '\\n' to split a status into a head
    ("D-layer: feed down") and a detail ("retry 15s"); at the 128x53 SDO
    content rect a 7 px monospace font fits ~25 characters, so two short
    lines read where one long one is truncated to noise.

    Allocation stays bounded: the vocabulary is a handful of fixed strings
    plus one coarse ETA/age token, all absorbed by _blit_text's glyph cache,
    and this only runs on a cadenced redraw (>= 15 s apart), never per frame.
    Every write is clamped inside rect so tests/test_panel_containment.py
    stays green at 720x450.
    """
    try:
        if not text or rect.w <= 8 or rect.h <= 0:
            return
        gh = font.get_height()
        if gh <= 0 or rect.h < gh:
            return
        lines = text.split('\n') if '\n' in text else (text,)
        n = max(1, min(2, len(lines), rect.h // gh))
        if top:
            y = rect.y + min(6, rect.h - n * gh)
        else:
            # Bottom-anchored, over a painted image: lay a card-coloured bar
            # first or the text fights the pixels underneath it.
            y = max(rect.y, rect.bottom - n * gh - 1)
            if backdrop is not None:
                bar = pygame.Rect(rect.x, y, rect.w,
                                  min(rect.bottom - y, n * gh + 1))
                if bar.h > 0:
                    pygame.draw.rect(screen, backdrop, bar)
        for i in range(n):
            _blit_fit(screen, font, lines[i], color,
                      rect.x + 6, y, rect.w - 8)
            y += gh
    except Exception:
        pass


def draw_image(screen, rect, surface, fonts=None, theme=None,
               image_key=None, fetched_at=None, status=None):
    """Blit `surface` into `rect`, with an honest status line.

    Tier 2.5. `status` is appended LAST on purpose — tests/test_perf_alloc.py
    and tests/test_themes.py:277 call this with up to five positional
    arguments. It is the (possibly None) return of _image_status_text; None
    means "nothing worth saying" and the panel renders exactly as it did
    before this tier.

    Two placements, because both states are real on a Pi:
      * no decoded image -> the status IS the panel body, so an operator can
        tell "fetching, retry in ~15 s" from "feed is down" from "bytes
        arrived but will not decode" instead of staring at a permanent
        "image loading..." that means all three;
      * an image is painted but the status is non-empty -> label it over the
        bottom of the image. _get_cached_image keeps serving the last good
        surface, so a stale or newly-undecodable payload would otherwise be
        invisible; serve-stale without an age label is worse than blank for
        someone making a band decision.
    """
    if surface is None:
        if fonts is not None and 'tiny' in fonts:
            label_color = theme['label'] if theme is not None else (184, 160, 216)
            # Tier 1.1: the SDO content rect is 128x53 at 720x450 — clamp the
            # placeholder to it instead of trusting a 1440-wide panel.
            _draw_status_lines(
                screen, rect,
                status if isinstance(status, str) and status
                else 'image loading...',
                fonts['tiny'], label_color, top=True)
        return
    try:
        iw, ih = surface.get_size()
        if iw == 0 or ih == 0 or rect.w <= 0 or rect.h <= 0:
            return
        scale = min(rect.w / iw, rect.h / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        if nw > rect.w or nh > rect.h:
            # Rounding at extreme aspect ratios can push the 1 px floor past
            # the rect; a panel too small to show anything shows nothing.
            return
        if scale >= 1.0:
            scaled = surface
        elif image_key is not None and fetched_at is not None:
            key = (image_key, float(fetched_at), (nw, nh))
            scaled = _scaled_cache.get(key)
            if scaled is None:
                scaled = _smoothscale_safe(surface, (nw, nh))
                _scaled_cache[key] = scaled
                if len(_scaled_cache) > _SCALED_CACHE_CAP:
                    _scaled_cache.popitem(last=False)
            else:
                _scaled_cache.move_to_end(key)
        else:
            scaled = _smoothscale_safe(surface, (nw, nh))
        x = rect.x + (rect.w - nw) // 2
        y = rect.y + (rect.h - nh) // 2
        screen.blit(scaled, (x, y))
    except Exception:
        pass
    # Outside the try above: a scale/blit failure must not also silence the
    # label that explains what the operator is (or is not) looking at.
    if isinstance(status, str) and status and fonts is not None and 'tiny' in fonts:
        _draw_status_lines(
            screen, rect, status, fonts['tiny'],
            theme['accent'] if theme is not None else (244, 197, 92),
            top=False,
            backdrop=theme['card'] if theme is not None else (0, 0, 0))


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


def _draw_value_and_bar(screen, rect, text, value, vmax, color, fonts, theme):
    """Shared GEOMAGNETIC / X-RAY FLUX body: a value row plus a gauge bar.

    Tier 1.1: both panels used to blit the value at rect.y + 2 and the bar at
    a fixed rect.y + 20 with a fixed height of 10 — 30 px of content in a
    content rect that is 17 px tall at 720x450, so the bar was painted over
    the panel border and into the next panel. The bar now stacks below the
    value when there is room and sits beside it when there is not.
    """
    f = fonts['body']
    gh = f.get_height()
    if rect.w <= 0 or rect.h <= 0:
        return
    if rect.h >= gh + 4:
        _blit_fit(screen, f, text, theme['bright'], rect.x, rect.y, rect.w)
        bar_x, bar_w = rect.x, rect.w
        bar_y = rect.y + gh + 1
        bar_h = min(10, rect.bottom - bar_y)
    else:
        # No room to stack: value on the left, gauge filling what is left.
        if rect.h >= gh:
            _blit_fit(screen, f, text, theme['bright'], rect.x, rect.y,
                      max(0, int(rect.w * 0.45) - 4))
        bar_x = rect.x + int(rect.w * 0.45)
        bar_w = rect.right - bar_x
        bar_h = min(10, rect.h)
        bar_y = rect.y + (rect.h - bar_h) // 2
    if bar_w > 0 and bar_h >= 2:
        draw_bar(screen, pygame.Rect(bar_x, bar_y, bar_w, bar_h),
                 value, vmax, color, theme)


def draw_muf_text(screen, rect, solar, fonts, theme, surf=None,
                  image_key=None, fetched_at=None):
    """MUF map across the top of the panel, the numbers beneath it.

    The map used to live in a tab of the propagation panel, where it got about
    7% of the screen while this panel spent 35% on five numbers — the one
    genuinely spatial display in the layout was the smallest thing on it. The
    map now takes the width of this panel and the stats keep the space below,
    which is roughly 1.6x the map area the tab could give it.

    The panel is portrait (356x541 at 800x600) and the map is landscape
    (~1.69:1), so the map is fitted to WIDTH and whatever it does not use is
    left to the stats rather than stretching it — a distorted contour map is
    worse than a smaller honest one.

    surf=None keeps the old text-only behaviour, which is what every caller
    that has no image (and the theme tests) still gets.
    """
    map_h = 0
    if surf is not None:
        try:
            iw, ih = surf.get_size()
            if iw > 0 and ih > 0:
                avail_w = rect.w
                # Never upscale past the raster's own width: the PNG is
                # rendered at output_width=360 and blowing it up past that
                # just makes the contours mushy.
                draw_w = min(avail_w, iw)
                draw_h = max(1, int(ih * (draw_w / float(iw))))
                # Leave at least enough room for the five rows underneath.
                if draw_h <= rect.h * 0.55:
                    map_rect = pygame.Rect(rect.x + (rect.w - draw_w) // 2,
                                           rect.y, draw_w, draw_h)
                    draw_image(screen, map_rect, surf, fonts, theme,
                               image_key=image_key, fetched_at=fetched_at)
                    map_h = draw_h + max(4, rect.h // 40)
        except Exception:
            map_h = 0
    if map_h:
        rect = pygame.Rect(rect.x, rect.y + map_h, rect.w, rect.h - map_h)

    rows = [
        ('FOF2',   '{} MHz'.format(_safe(solar, 'fof2'))),
        ('GEOMAG', _safe(solar, 'geomagField')),
        ('KP',     _safe(solar, 'kIndex')),
        ('SFI',    _safe(solar, 'sfi')),
        ('SSN',    _safe(solar, 'ssn')),
    ]
    # Tier 1.1: pitch 44 and the +20/+140 columns were absolute pixels. Keep
    # the same look where there is room (the MUF panel is the roomy one, 308
    # px wide at 720x450) but derive both from the rect so a smaller panel
    # compresses instead of overflowing.
    lab_f, val_f, foot_f = fonts['panel'], fonts['title'], fonts['small']
    val_h = val_f.get_height()
    glyph_h = max(lab_f.get_height(), val_h)
    if rect.w <= 0 or rect.h < glyph_h:
        return
    foot_h = foot_f.get_height() + 4 if rect.h >= glyph_h * 2 + 12 else 0
    n = len(rows)
    top = rect.y + min(20, max(0, (rect.h - foot_h - glyph_h) // 4))
    avail = rect.bottom - foot_h - top - glyph_h
    pitch = max(glyph_h + 1, min(44, avail // max(1, n - 1)))
    lab_x = rect.x + int(rect.w * 0.06)
    val_x = rect.x + int(rect.w * 0.45)
    lab_w = val_x - lab_x - 2
    val_w = rect.right - val_x
    y = top
    for label, value in rows:
        if y + glyph_h > rect.bottom - foot_h:
            break
        _blit_fit(screen, lab_f, label, theme['label'], lab_x, y, lab_w)
        _blit_fit(screen, val_f, str(value), theme['bright'],
                  val_x, y + (glyph_h - val_h) // 2, val_w)
        y += pitch
    if foot_h:
        # This line has been wrong twice. It began as '(Map available in web
        # UI)' — sending operators to a browser this hardware cannot usefully
        # run — then became 'Map: PROPAGATION > MUF', which went stale the
        # moment the map moved into this panel and that tab was retired.
        # Directions to the map are pointless when the map is directly above,
        # so it now credits the source instead, which cannot go stale while
        # the source is still what fetch_muf() asks for.
        _blit_fit(screen, foot_f,
                  'MUF(3000)km  ·  prop.kc2g.com' if surf is not None
                  else 'MUF(3000)km  ·  prop.kc2g.com  (map loading)',
                  theme['label'],
                  lab_x, rect.bottom - foot_h, rect.right - lab_x)


def draw_dx_spots(screen, rect, dxspots, fonts, theme):
    if not isinstance(dxspots, list):
        dxspots = []
    # Tier-1a perf: read the band-palette LUT cached on the theme dict by
    # _run_render_loop; fall back to building it inline so callers that
    # short-circuit the loop (tests, recovery overlay) still work.
    band_lut = theme.get('_band_lut') or dict(zip(HF_BANDS, theme['band_palette']))
    # Tier 1.1: the +90/+140/+230/+340 columns assumed a 488 px panel; the DX
    # SPOTS content rect at 720x450 is 236 px wide, so SPOTTER and TIME landed
    # outside it entirely. Column starts are now fractions of rect.w (the same
    # proportions the 1440 layout had) and every cell is width-clamped.
    lab_f, val_f = fonts['label'], fonts['body']
    glyph_h = max(lab_f.get_height(), val_f.get_height())
    if rect.w <= 0 or rect.h < glyph_h:
        return
    fracs = (0.0, 0.26, 0.40, 0.60, 0.85)
    xs = [rect.x + int(rect.w * f) for f in fracs]
    ws = [xs[i + 1] - xs[i] - 4 for i in range(4)] + [rect.right - xs[4]]
    n_rows = 1 + 5
    pitch = max(lab_f.get_height(),
                min(lab_f.get_height() + 4,
                    (rect.h - glyph_h) // max(1, n_rows - 1)))
    for i, head in enumerate(('FREQ', 'BND', 'DX', 'SPOTTER', 'TIME')):
        _blit_fit(screen, lab_f, head, theme['label'], xs[i], rect.y, ws[i])
    y = rect.y + pitch
    for spot in dxspots[:5]:
        if not isinstance(spot, dict):
            continue
        if y + glyph_h > rect.bottom:
            break
        freq = _safe(spot, 'frequency')
        band = _safe(spot, 'band')
        dx = _safe(spot, 'dxCall')
        spotter = _safe(spot, 'spotter')
        tm = _safe(spot, 'time')
        _blit_fit(screen, val_f, str(freq), theme['accent'], xs[0], y, ws[0])
        _blit_fit(screen, val_f, str(band),
                  band_lut.get(str(band), theme['fg']), xs[1], y, ws[1])
        _blit_fit(screen, val_f, str(dx), theme['bright'], xs[2], y, ws[2])
        _blit_fit(screen, val_f, str(spotter)[:10], theme['fg'],
                  xs[3], y, ws[3])
        _blit_fit(screen, val_f, str(tm), theme['label'], xs[4], y, ws[4])
        y += pitch


def draw_band_activity(screen, rect, dxspots, fonts, theme):
    """Item 6: pre-allocated _band_counts list (reset in place) replaces the
    per-frame {b: 0 for b in HF_BANDS} dict comprehension."""
    for i in range(len(_band_counts)):
        _band_counts[i] = 0
    if isinstance(dxspots, list):
        for spot in dxspots[:200]:
            if isinstance(spot, dict):
                b = spot.get('band')
                if b in HF_BANDS:
                    _band_counts[HF_BANDS.index(b)] += 1
    vmax = max(_band_counts) if any(_band_counts) else 1
    # Tier-1a perf: same theme-cached LUT as draw_dx_spots.
    band_lut = theme.get('_band_lut') or dict(zip(HF_BANDS, theme['band_palette']))
    # Tier 1.1: `row_h = max(14, ...)` forced 10 x 14 = 140 px of rows into a
    # content rect that is 100 px tall at 720x450 — the floor was the bug, not
    # the divisor. Pitch is now the rect's share per band (capped so a tall
    # 1440 panel does not stretch the bars absurdly) and the label/count
    # gutters are fractions of rect.w rather than 40/36 absolute px.
    lab_f = fonts['label']
    glyph_h = lab_f.get_height()
    n = len(HF_BANDS)
    if rect.w <= 0 or rect.h < glyph_h:
        return
    row_h = max(1, min(rect.h // n, glyph_h + 10))
    label_w = min(rect.w, max(glyph_h * 2, int(rect.w * 0.17)))
    count_w = max(0, min(rect.w - label_w, max(glyph_h * 2,
                                               int(rect.w * 0.15))))
    bar_w = rect.w - label_w - count_w
    count_pad = 2 if count_w > 4 else 0
    count_x = rect.right - count_w + count_pad
    y = rect.y
    for i, band in enumerate(HF_BANDS):
        if y + glyph_h > rect.bottom:
            break
        c = _band_counts[i]
        _blit_fit(screen, lab_f, band, theme['label'], rect.x, y,
                  label_w - 2)
        bar_h = min(max(2, row_h - 3), rect.bottom - y - 1)
        if bar_w > 2 and bar_h >= 2:
            bar_rect = pygame.Rect(rect.x + label_w, y + 1, bar_w, bar_h)
            draw_bar(screen, bar_rect, c, vmax,
                     band_lut.get(band, theme['fg']), theme)
        _blit_fit(screen, lab_f, str(c), theme['bright'],
                  count_x, y, count_w - 2)
        y += row_h


def draw_tabs(screen, rect, tabs, active, fonts, theme):
    """Draw a tab bar across rect.y (height 20). Returns {name: Rect}."""
    regions = {}
    if not tabs or rect.w <= 0 or rect.h <= 0:
        return regions
    tw = rect.w // len(tabs)
    if tw < 4:
        # Tier 1.1: `tw - 2` goes negative below two tabs' worth of width and
        # pygame normalises a negative-width Rect by moving its left edge, so
        # the chrome would be painted to the LEFT of the bar.
        return regions
    f = fonts['panel']
    fh = f.get_height()
    th = min(20, rect.h)
    pad = min(8, max(1, tw // 6))
    ty = rect.y + min(2, max(0, th - fh))
    for i, name in enumerate(tabs):
        tab_rect = pygame.Rect(rect.x + i * tw, rect.y, max(1, tw - 2), th)
        color = theme['border'] if name == active else theme['card']
        pygame.draw.rect(screen, color, tab_rect)
        pygame.draw.rect(screen, theme['border'], tab_rect, 1)
        text_color = theme['accent'] if name == active else theme['label']
        if th >= fh:
            _blit_fit(screen, f, name.upper(), text_color,
                      tab_rect.x + pad, ty, tab_rect.w - pad - 1)
        regions[name] = tab_rect
    return regions


def draw_geomag(screen, rect, solar, fonts, theme, data_refresh_ts=None):
    """Items 8 + 9: pull Kp value & label from caches when a refresh ts is
    known so neither the _safe call nor the format string runs each frame."""
    if data_refresh_ts is not None:
        v = _solar_view(solar, data_refresh_ts)
        kp = v['kIndex_raw']
    else:
        kp = _safe(solar, 'kIndex', 0)
    try:
        kp_val = float(kp)
    except Exception:
        kp_val = 0.0
    color = (theme['good'] if kp_val < 4
             else theme['fair'] if kp_val < 6
             else theme['poor'])
    _draw_value_and_bar(screen, rect, 'Kp {}'.format(kp), kp_val, 9.0,
                        color, fonts, theme)


def draw_xray(screen, rect, solar, fonts, theme, data_refresh_ts=None):
    """Item 9: read the X-Ray value from the cached _solar_view when a
    refresh ts is known."""
    if data_refresh_ts is not None:
        v = _solar_view(solar, data_refresh_ts)
        xray = v['xray_raw']
    else:
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
    _draw_value_and_bar(screen, rect, s, value, 5.0, color, fonts, theme)


def draw_open_bands(screen, rect, bands, fonts, theme, data_refresh_ts=None):
    """Item 7: build the OPEN / CLOSED labels once per data refresh; until
    the next refresh tick we just read the cached strings."""
    o, c = _open_bands_strings(bands, data_refresh_ts)
    # Tier 1.1: 'OPEN: 80m-40m, 30m-20m, 17m-15m' is 31 chars — 155 px in the
    # label font, in a content rect 128 px wide at 720x450. Step down to the
    # smaller face before truncating so the band list survives, and derive the
    # second row's offset from the font instead of a fixed 16 px.
    f = fonts['label']
    if rect.w > 0 and (f.size(o)[0] > rect.w or f.size(c)[0] > rect.w):
        f = fonts['small']
    gh = f.get_height()
    if rect.h < gh:
        return
    pitch = min(gh + 6, rect.h - gh)
    _blit_fit(screen, f, o, theme['good'], rect.x, rect.y, rect.w)
    if pitch > 0:
        _blit_fit(screen, f, c, theme['poor'], rect.x, rect.y + pitch, rect.w)


def draw_status_bar(screen, rect, data, fonts, theme,
                    notice=None, notice_color='accent', update_label=None):
    """Item 8: status bar text is pulled from _strfmt_cache so the format
    string is built at most once per UTC second.

    Returns {'send_report': Rect} — and, when `update_label` is given,
    {'update': Rect} as well: the hit targets for the opt-in diagnostics
    report and for installing an available update, registered the same way
    draw_tabs registers the propagation tabs so the render loop's existing
    MOUSEBUTTONDOWN sweep picks them up. The dict is empty when the bar is too
    narrow to hold the chips; the 'T' and 'U' key bindings are what make the
    features reachable at all, because a Pi 1 kiosk is routinely run with no
    mouse attached.

    `update_label` is None whenever there is no update to offer, which is
    almost always — the chip appears only while the root updater's status file
    says state=available.

    `notice` replaces the left-hand status string while a report is in
    flight or has just finished. It displaces the least important text on the
    display for ten seconds, which is the correct trade for telling the
    operator whether their report actually went."""
    pygame.draw.rect(screen, theme['card'], rect)
    pygame.draw.rect(screen, theme['border'], rect, 1)
    f = fonts['small']
    regions = {}
    # Tier 1.1: the quit hint was pinned 110 px from the right edge, which is
    # a different fraction of a 720 px bar than of a 1440 px one; place it by
    # its measured width and give the status string the rest.
    hint = 'ESC/Q to quit'
    hint_x = max(rect.x, rect.right - 6 - f.size(hint)[0])
    ty = rect.y + max(0, min(4, rect.h - f.get_height()))

    btn_label = 'REPORT [T]'
    btn_w = f.size(btn_label)[0] + 10
    btn_x = hint_x - 8 - btn_w
    text_right = hint_x
    if btn_x >= rect.x + 48 and rect.h >= f.get_height() + 2:
        btn = pygame.Rect(btn_x, rect.y + 1, btn_w, rect.h - 2)
        pygame.draw.rect(screen, theme['border'], btn)
        pygame.draw.rect(screen, theme['accent'], btn, 1)
        _blit_fit(screen, f, btn_label, theme['accent'],
                  btn.x + 5, ty, btn.w - 10)
        regions['send_report'] = btn
        text_right = btn.x

    # The update chip sits immediately left of SEND REPORT and is drawn
    # filled, not outlined: it is the one control on this bar that is offering
    # to change the machine, and it is only ever present when there is
    # genuinely something to install.
    if update_label:
        uw = f.size(update_label)[0] + 10
        ux = text_right - 8 - uw
        if ux >= rect.x + 48 and rect.h >= f.get_height() + 2:
            ubtn = pygame.Rect(ux, rect.y + 1, uw, rect.h - 2)
            pygame.draw.rect(screen, theme['accent'], ubtn)
            pygame.draw.rect(screen, theme['bright'], ubtn, 1)
            _blit_fit(screen, f, update_label, theme['card'],
                      ubtn.x + 5, ty, ubtn.w - 10)
            regions['update'] = ubtn
            text_right = ubtn.x

    if notice:
        text = notice
        color = theme.get(notice_color, theme['accent'])
    else:
        text = _formatted_strings(data)["status"]
        color = theme['label']
    _blit_fit(screen, f, text, color, rect.x + 6, ty,
              text_right - rect.x - 12)
    _blit_fit(screen, f, hint, theme['label'], hint_x, ty,
              rect.right - hint_x)
    return regions


# Tier 1.2: keys whose payload failed to decode, stamped with the fetch ts
# that produced them. _load_image makes up to three SDL probes per call and
# pygame offers no cheap "is this decodable" test, so without this memo an
# undecodable payload (a 503 body, a truncated JPEG, an SVG the Tier 1.5 guard
# refuses) is re-probed on every redraw of its panel instead of once per
# refresh. Bounded by the five _IMAGE_ENDPOINTS keys.
_decode_failed_ts: dict = {}


def _image_stamp(data, key):
    """Per-key fetch timestamp, falling back to the global refresh tick.

    The getattr/isinstance guard is load-bearing: HamClockData grew
    image_fetched_at in Tier 1a but stand-ins that predate it (e.g.
    tests/test_themes.py's _StubData) do not have the attribute at all, and an
    AttributeError here is swallowed by the render loop's per-panel
    `except Exception: pass` — which is exactly how the installer-embedded
    client ended up with two permanently blank image panels.
    """
    _fa = getattr(data, 'image_fetched_at', None)
    return (_fa.get(key, data.last_image_refresh)
            if isinstance(_fa, dict) else data.last_image_refresh)


def _get_cached_image(data, key, image_cache, image_cache_ts):
    """Return a pygame Surface for data.images[key], rebuilt when THAT key's
    fetch timestamp changes.

    Tier 1.2: the stamp used to be the global data.last_image_refresh, so one
    endpoint arriving invalidated the decoded surfaces of all five and the
    render thread paid 3-4 redundant full JPEG/PNG decodes per retry during
    cold boot (~36-165 ms each, ARMv6 extrapolated). A decode that fails is
    now remembered against the same stamp so it is retried once per refresh,
    not once per redraw; the previously decoded surface (if any) keeps being
    served meanwhile.
    """
    raw = data.images.get(key) if isinstance(data.images, dict) else None
    if raw is None:
        return None
    ts = _image_stamp(data, key)
    if image_cache_ts.get(key) != ts or key not in image_cache:
        if _decode_failed_ts.get(key) == ts:
            return image_cache.get(key)
        surf = _load_image(raw)
        if surf is not None:
            image_cache[key] = surf
            image_cache_ts[key] = ts
            _decode_failed_ts.pop(key, None)
        else:
            # First time we have seen THIS payload fail. Tell the data layer,
            # so the key is refetched on the retry backoff instead of sitting
            # satisfied until the next slow cycle: the decoder is the only
            # part of the system that can tell a useless 200 from a good one.
            if _decode_failed_ts.get(key) != ts:
                try:
                    data.mark_image_undecodable(key)
                except AttributeError:
                    pass        # older data layer; nothing to report to
                except Exception:
                    pass
            _decode_failed_ts[key] = ts
    return image_cache.get(key)


# Tier 2.5: what to call each feed on screen. The panel titles ("SDO IMAGE")
# and the propagation tab labels ("drap", "aurora") do not name the upstream
# product, and during an outage the feed name is most of the information.
_IMAGE_LABEL = {
    'solar-image': 'SDO',
    'muf-map': 'MUF map',
    'enlil': 'Enlil',
    'drap': 'Aurora',
    'real-drap': 'D-layer',
}

# Optional per-key content-age fields on /api/health. Client-side fetch time
# cannot see that the server answered from its persisted disk cache (Tier
# 2.1), so when the server publishes a real content age we prefer it. Absent
# or -1 (the existing "unknown" convention at server.py's /api/health) falls
# back to the client's own last-successful-fetch stamp.
_HEALTH_AGE_FIELD = {
    'solar-image': 'sdo_age',
    'muf-map': 'muf_age',
    'enlil': 'enlil_age',
    'drap': 'drap_age',
    'real-drap': 'real_drap_age',
}

# Consecutive failed attempts before a feed is called "down" rather than
# "no data yet". With hamclock_data.IMAGE_RETRY_BACKOFF = (5, 10, 20, 40, 60)
# the 4th failure lands ~75 s in, which is long enough that a slow server or a
# boot-time race has been ruled out.
_IMAGE_DOWN_AFTER_FAILS = 4

# Show an age label once a displayed image is this old. Below it the label is
# clutter (the feeds refresh every 900 s); above it the picture may no longer
# describe the band conditions in front of the operator.
_IMAGE_STALE_S = 3600.0


def _fmt_eta(secs):
    """Coarse 'retry in ...' token. Never raises; never returns ''."""
    try:
        s = float(secs)
        if s != s:          # NaN
            return '?'
        if s <= 1:
            return 'now'
        # Round UP: a countdown that reads 0s while nothing has happened yet
        # is the same lie this tier exists to remove. Ceil first, then pick
        # the unit, so 59.9 s reads "1m" rather than "60s".
        s = int(s) + (1 if s > int(s) else 0)
        if s < 60:
            return '%ds' % s
        if s < 3600:
            return '%dm' % ((s + 59) // 60)
        if s < 86400:
            return '%dh' % (s // 3600)
        return '%dd' % (s // 86400)
    except Exception:
        return '?'


def _fmt_age(secs):
    """Coarse '... old' token. Never raises; never returns ''."""
    try:
        s = float(secs)
        if s != s or s < 0:
            return '?'
        if s < 60:
            return '%ds' % int(s)
        if s < 3600:
            return '%dm' % int(s // 60)
        if s < 86400:
            return '%dh' % int(s // 3600)
        return '%dd' % int(s // 86400)
    except Exception:
        return '?'


def _image_status_text(data, key):
    """Honest one/two-line status for image panel `key`, or None.

    TOTAL by construction. The render loop evaluates this as an *argument* to
    draw_image, inside the per-panel `except Exception: pass`, so an exception
    escaping here does not merely lose the status — it skips the draw_image
    call entirely and leaves the panel blank, which is strictly worse than the
    string it was meant to replace. Hence .get() on every dict lookup (getattr
    guards the attribute, never the key), isinstance checks on everything that
    came off the wire, and a blanket except returning None.

    Returning None means "say nothing", which is the right answer for a fresh
    image and for any data object this function does not understand.

    Deliberately NOT sourced from `data.images.get(key) is not None`: that
    cannot tell a decode failure from a decode success, and data.images is
    cumulative (refresh_images does new_images.update(fetched) and never
    deletes), so it stays truthy forever after one good cycle. The decode
    verdict comes from _decode_failed_ts (Tier 1.2) and the liveness verdict
    from the per-key retry state (Tier 1.4).
    """
    try:
        name = _IMAGE_LABEL.get(key, 'image')
        now = time.time()

        images = getattr(data, 'images', None)
        raw = images.get(key) if isinstance(images, dict) else None

        fa = getattr(data, 'image_fetched_at', None)
        stamp = fa.get(key) if isinstance(fa, dict) else None
        if stamp is None:
            stamp = getattr(data, 'last_image_refresh', None)

        # 1) Bytes in hand that SDL refused. Checked first and against the
        #    stamp that produced them, so a *newly* bad payload is reported
        #    even while _get_cached_image is still showing the last good
        #    surface underneath.
        failed = _decode_failed_ts.get(key)
        if (raw is not None and failed is not None and stamp is not None
                and failed == stamp):
            return '%s: image data\nnot readable' % name

        fs = getattr(data, 'image_fail_streak', None)
        streak = fs.get(key, 0) if isinstance(fs, dict) else 0
        if not isinstance(streak, int) or isinstance(streak, bool):
            streak = 0

        # 2) Nothing to draw at all.
        if raw is None:
            if streak <= 0:
                return '%s: fetching...' % name
            nd = getattr(data, 'image_next_due', None)
            due = nd.get(key) if isinstance(nd, dict) else None
            head = ('%s: feed down' % name if streak >= _IMAGE_DOWN_AFTER_FAILS
                    else '%s: no data yet' % name)
            if isinstance(due, (int, float)) and not isinstance(due, bool):
                return '%s\nretry %s' % (head, _fmt_eta(due - now))
            return head

        # 3) An image is on screen. Say how old it is once that matters.
        age = None
        health = getattr(data, 'health', None)
        if isinstance(health, dict):
            hv = health.get(_HEALTH_AGE_FIELD.get(key) or '\x00')
            if (isinstance(hv, (int, float)) and not isinstance(hv, bool)
                    and hv >= 0):
                age = float(hv)
        if (age is None and isinstance(stamp, (int, float))
                and not isinstance(stamp, bool) and stamp > 0):
            age = now - float(stamp)
        if age is not None and age >= _IMAGE_STALE_S:
            return '%s %s old' % (name, _fmt_age(age))
        return None
    except Exception:
        return None


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
        # muf_text joined this group when the map moved into it: it is now
        # image-fed as well as data-fed, and without this the map would only
        # refresh when the SOLAR numbers happened to change.
        for k in ('sdo', 'propagation', 'muf_text'):
            r = panel_rects.get(k)
            if r is not None and r not in dirty:
                dirty.append(r)
    return dirty


# ==========================================================================
# Opt-in diagnostics report
# ==========================================================================
# The maintainer has no ARMv6 hardware, so every Pi wall-clock figure in this
# project is extrapolated from x86 and unverified. This lets an operator who
# WANTS to help answer that with one keypress.
#
# Three rules govern everything below, and they are not negotiable:
#
#   1. Nothing is ever sent unless the operator presses the button/key AND
#      then confirms. Every single time. There is no boot-time send, no timer,
#      no retry-on-failure (a retry loop is an unattended resend), and no code
#      path from module import to a POST that does not pass through
#      _report_confirm().
#   2. The confirm dialog says, in plain language, exactly what leaves the
#      device — including that the attached screenshot has the operator's
#      callsign in the header, so a report with a screenshot is NOT anonymous.
#      We say that rather than let someone assume otherwise.
#   3. Nothing outside this project's own diagnostics is collected. No file
#      contents, no credentials, no WiFi config. The one block we do not
#      author ourselves — the local server's /api/diagnostics body — is walked
#      and any secret-shaped key is dropped before it can be sent.
#
# Stdlib only (urllib/json/base64/uuid): the Pi must not grow a pip dependency
# for this.

TELEMETRY_URL = 'https://hamclock-reborn.org/api/telemetry'
TELEMETRY_SCHEMA = 1
# One attempt, 15 s. Long enough for a slow rural uplink to finish a ~130 KB
# POST, short enough that the operator gets an answer while still looking at
# the screen.
TELEMETRY_TIMEOUT_S = 15.0
TELEMETRY_UA = 'hamclock-pi1-report/1 (+https://hamclock-reborn.org)'

# The local server's diagnostics endpoint, embedded verbatim (after scrubbing)
# under payload['server']. Path only — the host comes from the live
# HamClockData instance so a non-default --server URL is honoured.
SERVER_DIAG_PATH = '/api/diagnostics'
# Measured 1-18 ms on x86, from which I concluded a 2 s budget had "ample
# ARMv6 headroom". The first field report that could answer the question said
# otherwise: 'TimeoutError: timed out'. A 700 MHz single core already running a
# 10 FPS loop, and possibly a cpulimited cairosvg, does not serve a request in
# the time an idle x86 does. The fetch now happens on the SENDER thread rather
# than the render thread, so a generous budget costs the display nothing.
SERVER_DIAG_TIMEOUT_S = 20.0
SERVER_DIAG_MAX_BYTES = 64 * 1024

# A 720x450 antialiased frame is ~73 KB of PNG -> ~98 KB of base64. The cap is
# ~3.5x that, so a legitimate frame always fits and a pathological one (a
# corrupted surface, a future 1440x900 native mode) is DROPPED rather than
# truncated: half a PNG is not a smaller screenshot, it is a broken one.
SCREENSHOT_MAX_B64 = 350 * 1024

# How long the status bar keeps a report outcome on screen.
REPORT_NOTICE_TTL_S = 10.0

# Key names that may never appear in the payload. Applied to the one block we
# do not author (the local server's /api/diagnostics body) and asserted
# against the whole payload by the test suite.
_SECRET_KEY_RE = re.compile(
    r'pass|pwd|secret|token|api[-_]?key|apikey|credential|auth|cookie|'
    r'session|ssid|psk|wpa|wifi|private|privkey|ssh|bearer|salt|signature',
    re.I)

_DEVICE_ID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _mono():
    """Monotonic seconds, falling back to the wall clock.

    Looked up with getattr rather than imported at module scope on purpose:
    the render-loop tests replace hamclock_pygame.time with a stub exposing
    only time/sleep/gmtime/strftime, and an AttributeError raised per frame
    would land in the loop's consecutive_errors counter."""
    fn = getattr(time, 'monotonic', None)
    if fn is None:
        return time.time()
    return fn()


# ---- frame-time ring buffer ----------------------------------------------
# Fixed-size, index-advanced, never grows, never allocates a container. 600
# slots is 60 s of history at 10 FPS — long enough that a report taken any
# time after the first minute of uptime describes steady state, small enough
# (~5 KB) to be free on a 512 MB box.
_FRAME_MS_CAP = 600
_frame_ms_ring = [0.0] * _FRAME_MS_CAP
_frame_ms_pos = 0
_frame_ms_n = 0
# Samples above this are not frame times: they are an NTP step (the loop reads
# the wall clock when monotonic is unavailable), a VT switch, or the process
# being descheduled. Recording them would move p99 by an order of magnitude
# and make the whole measurement worthless.
_FRAME_MS_SANE_MAX = 10_000.0

# Last observed draw cost per panel, in ms. Bounded by the _CADENCE_S key set.
_panel_ms: dict = {}

_first_paint_s = None


def _record_frame_ms(t0):
    """Record one frame's render cost, measured from `t0` (a _mono() stamp).

    Called once per frame from the render loop: no allocation beyond the
    float itself, no growth, no sort. Percentiles are computed only when a
    report is actually built."""
    global _frame_ms_pos, _frame_ms_n
    ms = (_mono() - t0) * 1000.0
    if ms < 0.0 or ms > _FRAME_MS_SANE_MAX:
        return
    _frame_ms_ring[_frame_ms_pos] = ms
    _frame_ms_pos = (_frame_ms_pos + 1) % _FRAME_MS_CAP
    if _frame_ms_n < _FRAME_MS_CAP:
        _frame_ms_n += 1


def _record_panel_ms(name, t0):
    """Record the last draw cost of one panel. Same no-allocation contract."""
    ms = (_mono() - t0) * 1000.0
    if 0.0 <= ms <= _FRAME_MS_SANE_MAX:
        _panel_ms[name] = ms


def _percentile(vals, q):
    """Nearest-rank percentile of an already-sorted list. None when empty."""
    if not vals:
        return None
    idx = int(round((len(vals) - 1) * q))
    if idx < 0:
        idx = 0
    elif idx >= len(vals):
        idx = len(vals) - 1
    return vals[idx]


def _frame_ms_summary():
    """{'p50','p90','p99','n'} over the ring. Values are ms, 1 decimal."""
    n = _frame_ms_n
    if n <= 0:
        return {'p50': None, 'p90': None, 'p99': None, 'n': 0}
    vals = sorted(_frame_ms_ring[:n])
    return {
        'p50': round(_percentile(vals, 0.50), 1),
        'p90': round(_percentile(vals, 0.90), 1),
        'p99': round(_percentile(vals, 0.99), 1),
        'n': n,
    }


def _note_first_paint():
    """Stamp boot-to-first-paint the first time the dashboard is presented."""
    global _first_paint_s
    if _first_paint_s is not None:
        return
    age = _process_age_s()
    _first_paint_s = age if age is not None else 0.0


def _process_age_s():
    """Seconds since THIS process started, from /proc.

    Measured against process start rather than module import so the figure
    includes interpreter startup and the pygame import — on an ARMv6 Pi 1 that
    is a large and entirely unmeasured slice of boot-to-first-paint. None when
    /proc is unavailable or unparseable."""
    stat = _read_text('/proc/self/stat', 4096)
    up = _read_text('/proc/uptime', 256)
    if not stat or not up:
        return None
    try:
        # comm (field 2) is parenthesised and may itself contain spaces.
        tail = stat.rsplit(')', 1)[1].split()
        start_ticks = float(tail[19])          # field 22 overall
        hz = float(os.sysconf('SC_CLK_TCK'))
        uptime = float(up.split()[0])
        age = uptime - (start_ticks / hz)
    except Exception:
        return None
    if age < 0.0 or age > 10.0 * 365 * 24 * 3600:
        return None
    return round(age, 2)


# ---- collection ----------------------------------------------------------

def _read_text(path, limit=65536):
    """Read a small text file. Returns None on ANY failure.

    Every /proc and /etc read in the collector goes through here so that a
    missing, unreadable, or exploding file yields null for that one field
    instead of aborting the whole report."""
    try:
        with open(path, 'r', errors='replace') as f:
            return f.read(limit)
    except Exception:
        return None


def _cpuinfo_fields():
    """{Model, Hardware, Revision, model name, Processor} from /proc/cpuinfo.

    Raspberry Pi OS reports the board as 'Model' and the SoC as 'Hardware' +
    'Revision'; desktop x86 kernels report neither and use 'model name'."""
    out = {}
    txt = _read_text('/proc/cpuinfo')
    if not txt:
        return out
    for line in txt.splitlines():
        if ':' not in line:
            continue
        k, _, v = line.partition(':')
        k = k.strip()
        v = v.strip()
        if k in ('Model', 'Hardware', 'Revision', 'model name', 'Processor'):
            # Keep the FIRST occurrence: on a multi-core box every core
            # repeats 'model name', and they are identical.
            out.setdefault(k, v)
    return out


def _meminfo_total_kb():
    txt = _read_text('/proc/meminfo', 8192)
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith('MemTotal:'):
            try:
                return int(line.split()[1])
            except Exception:
                return None
    return None


def _uptime_s():
    txt = _read_text('/proc/uptime', 256)
    if not txt:
        return None
    try:
        return int(float(txt.split()[0]))
    except Exception:
        return None


def _os_pretty_name():
    txt = _read_text('/etc/os-release', 8192)
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith('PRETTY_NAME='):
            return line.split('=', 1)[1].strip().strip('"') or None
    return None


def _uname():
    """os.uname() when available, else platform.uname(). None on failure.

    platform is imported lazily because it drags subprocess in with it, and
    this client boots on a 512 MB single-core box."""
    fn = getattr(os, 'uname', None)
    if fn is not None:
        try:
            return fn()
        except Exception:
            pass
    try:
        import platform
        return platform.uname()
    except Exception:
        return None


def _collect_host():
    """host block. Every field individually guarded -> null, never an except."""
    cpu = _cpuinfo_fields()
    un = _uname()

    model = cpu.get('Model')
    if not model:
        # /proc/device-tree/model is NUL-terminated.
        dt = _read_text('/proc/device-tree/model', 512)
        model = dt.strip('\x00 \n') if dt else None
    if not model and un is not None:
        model = getattr(un, 'machine', None)

    cpu_name = cpu.get('model name') or cpu.get('Processor') or (
        getattr(un, 'machine', None) if un is not None else None)
    hw = cpu.get('Hardware')
    rev = cpu.get('Revision')
    if cpu_name and (hw or rev):
        cpu_name = '%s [%s%s]' % (
            cpu_name, hw or '?', (' rev ' + rev) if rev else '')
    elif hw:
        cpu_name = '%s%s' % (hw, (' rev ' + rev) if rev else '')

    try:
        cores = os.cpu_count()
    except Exception:
        cores = None

    return {
        'model': model or None,
        'cpu': cpu_name or None,
        'cores': cores,
        'mem_total_kb': _meminfo_total_kb(),
        'kernel': (getattr(un, 'release', None) if un is not None else None),
        'os': _os_pretty_name() or (
            getattr(un, 'sysname', None) or getattr(un, 'system', None)
            if un is not None else None),
        'python': '%d.%d.%d' % sys.version_info[:3],
        'uptime_s': _uptime_s(),
    }


def _collect_display(screen):
    """display block — the single most valuable field in the whole report.

    10-monitor.conf ships DefaultDepth 16 and pygame's smoothscale raises
    ValueError on 8- and 16-bit surfaces (see _smoothscale_safe), so what the
    kiosk actually gets for driver and depth has never been observed on real
    hardware. This settles it."""
    out = {'sdl_driver': None, 'bitsize': None, 'size': None,
           'fullscreen': None}
    try:
        out['sdl_driver'] = pygame.display.get_driver()
    except Exception:
        pass
    if screen is None:
        return out
    try:
        out['bitsize'] = int(screen.get_bitsize())
    except Exception:
        pass
    try:
        w, h = screen.get_size()
        out['size'] = [int(w), int(h)]
    except Exception:
        pass
    try:
        out['fullscreen'] = bool(screen.get_flags() & pygame.FULLSCREEN)
    except Exception:
        pass
    return out


def _cairosvg_version():
    """cairosvg's version WITHOUT importing it.

    Importing cairosvg pulls in cffi and the cairo shared library — tens of MB
    of RSS in a client process that never rasterizes anything (server.py does
    that, in its own process). importlib.metadata reads the dist-info instead."""
    try:
        from importlib import metadata as _md
    except Exception:
        return None
    try:
        return _md.version('cairosvg')
    except Exception:
        pass
    # Installed without metadata (vendored, or a .pth): fall back to asking
    # the import system whether the module exists, still without importing it.
    try:
        import importlib.util
        return 'present' if importlib.util.find_spec('cairosvg') else None
    except Exception:
        return None


def _collect_versions():
    try:
        pg = pygame.version.ver
    except Exception:
        pg = None
    try:
        sdl = '.'.join(str(int(x)) for x in pygame.get_sdl_version())
    except Exception:
        sdl = None
    try:
        import shutil
        cpulimit = bool(shutil.which('cpulimit'))
    except Exception:
        cpulimit = None
    return {
        'pygame': pg,
        'sdl': sdl,
        'cairosvg': _cairosvg_version(),
        'cpulimit': cpulimit,
    }


def _git_head_sha():
    """Short HEAD sha read straight out of .git, or None.

    Deliberately not `git rev-parse`: the installed kiosk has neither a git
    checkout nor a git binary, and spawning a subprocess on ARMv6 to learn a
    string that is sitting in a file is a poor trade."""
    here = os.path.dirname(os.path.abspath(__file__))
    head = _read_text(os.path.join(here, '.git', 'HEAD'), 512)
    if not head:
        return None
    head = head.strip()
    if not head.startswith('ref:'):
        return head[:12] if re.match(r'^[0-9a-f]{7,40}$', head) else None
    ref = head.split(':', 1)[1].strip()
    sha = _read_text(os.path.join(here, '.git', ref), 128)
    if sha and sha.strip():
        return sha.strip()[:12]
    packed = _read_text(os.path.join(here, '.git', 'packed-refs'), 262144)
    for line in (packed or '').splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref:
            return parts[0][:12]
    return None


def _app_version():
    """Release version and/or git sha, or 'unknown'.

    Both when both are known ('1.0.0+2b52f68f'): the release string is what an
    operator can quote, the sha is what pins the exact code a timing figure
    was measured on, and a report that has to be matched to one of several
    builds of the same release is worth much less."""
    env = os.environ.get('HAMCLOCK_VERSION')
    if env:
        return env.strip()[:64]
    here = os.path.dirname(os.path.abspath(__file__))
    ver = None
    for cand in (os.path.join(here, 'VERSION'),
                 '/etc/hamclock-lite/version'):
        txt = _read_text(cand, 256)
        if txt and txt.strip():
            ver = txt.strip().splitlines()[0][:32]
            break
    sha = _git_head_sha()
    if ver and sha:
        return ('%s+%s' % (ver, sha))[:64]
    return ver or sha or 'unknown'


def _install_kind():
    """'kiosk' | 'offline' | None. Never guesses beyond what is on disk."""
    env = os.environ.get('HAMCLOCK_INSTALL')
    if env in ('kiosk', 'offline'):
        return env
    try:
        if os.path.exists('/etc/hamclock-lite/offline-install'):
            return 'offline'
        if os.path.exists('/etc/systemd/system/hamclock-kiosk.service'):
            return 'kiosk'
    except OSError:
        pass
    return None


def _scrub_secrets(obj, depth=0):
    """Drop any secret-shaped key from a structure we did not author.

    The /api/diagnostics body is written by the server, embedded verbatim, and
    could grow a field we never reviewed. Defence in depth: walk it once and
    drop anything whose KEY looks like a credential. Depth-limited so a
    self-referential or absurdly nested body cannot blow the stack."""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if _SECRET_KEY_RE.search(ks):
                continue
            out[ks] = _scrub_secrets(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_scrub_secrets(v, depth + 1) for v in obj[:200]]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)[:512]


def _diag_error(reason):
    """A self-describing stand-in for a diagnostics block we could not get.

    Returning a bare None taught us nothing: the first report from real
    hardware came back with "server": null, and the reason was only in the
    Pi's journal — on a box the maintainer cannot reach. A report that cannot
    explain its own gaps costs a round trip through the operator, which is the
    thing telemetry exists to avoid. Short, no secrets, no host paths.
    """
    return {'error': str(reason)[:200]}


def _fetch_server_diagnostics(base_url=None, timeout=SERVER_DIAG_TIMEOUT_S):
    """GET the local server's /api/diagnostics.

    On failure returns {'error': ...} rather than None, so the report itself
    says why the block is missing.

    Short timeout on purpose: this runs on the render thread, once, in
    response to a keypress. The server is on loopback, so the realistic
    failure is 'not running' (instant ECONNREFUSED), not a slow reply — and
    measured, the endpoint answers in 1-18 ms on x86, so even a 50x slower
    ARMv6 has ample headroom.
    """
    base = (base_url or 'http://localhost:8080').rstrip('/')
    try:
        req = _Request(base + SERVER_DIAG_PATH)
        req.add_header('Accept', 'application/json')
        req.add_header('User-Agent', TELEMETRY_UA)
        resp = _urlopen(req, timeout=timeout)
    except Exception as e:
        # HTTP 404 here means the local server predates /api/diagnostics —
        # worth distinguishing from "not running", because the fix differs.
        code = getattr(e, 'code', None)
        reason = ('http %s' % code) if code else '%s: %s' % (type(e).__name__, e)
        print('[report] server diagnostics unavailable: %s' % e,
              file=sys.stderr)
        return _diag_error(reason)
    try:
        raw = resp.read(SERVER_DIAG_MAX_BYTES + 1)
    except Exception as e:
        return _diag_error('read: %s' % type(e).__name__)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if not raw:
        return _diag_error('empty response')
    if len(raw) > SERVER_DIAG_MAX_BYTES:
        return _diag_error('oversize: %d bytes' % len(raw))
    try:
        body = json.loads(raw.decode('utf-8', 'replace'))
    except Exception as e:
        return _diag_error('parse: %s' % type(e).__name__)
    if not isinstance(body, (dict, list)):
        return None
    return _scrub_secrets(body)


def _screenshot_b64(surface, cap=None):
    """Base64 PNG of `surface`, or None.

    Encoded string is capped: over the limit we drop the screenshot entirely
    and send null. A truncated image is not a smaller screenshot."""
    if surface is None:
        return None
    limit = SCREENSHOT_MAX_B64 if cap is None else cap
    try:
        buf = io.BytesIO()
        pygame.image.save(surface, buf, 'shot.png')
        raw = buf.getvalue()
    except Exception as e:
        print('[report] screenshot failed: %s' % e, file=sys.stderr)
        return None
    if not raw:
        return None
    # Cheap pre-check against the raw PNG (base64 is 4/3 of it) so an absurd
    # frame never costs us the big encode allocation on a 512 MB box.
    if len(raw) > (limit // 4) * 3:
        print('[report] screenshot %d B exceeds the cap; sending none'
              % len(raw), file=sys.stderr)
        return None
    try:
        import base64
        b64 = base64.b64encode(raw).decode('ascii')
    except Exception as e:
        print('[report] screenshot encode failed: %s' % e, file=sys.stderr)
        return None
    if len(b64) > limit:
        print('[report] screenshot %d B base64 exceeds the cap; sending none'
              % len(b64), file=sys.stderr)
        return None
    return b64


def _get_or_create_device_id(settings, path=None):
    """Return this Pi's report id, minting and persisting one on first use.

    A uuid4 — random. Deliberately NOT derived from the MAC, the CPU serial,
    or the callsign: its only job is to let two reports from the same Pi be
    recognised as the same Pi, and nothing about the operator should be
    recoverable from it. Persisted via the existing write_settings so it
    survives reboots; a failed write is logged and the id is still used for
    this session (a report with a fresh id beats no report)."""
    if isinstance(settings, dict):
        cur = settings.get(DEVICE_ID_KEY)
        if isinstance(cur, str) and _DEVICE_ID_RE.match(cur):
            return cur
    try:
        import uuid
        new_id = str(uuid.uuid4())
    except Exception:
        return None
    if isinstance(settings, dict):
        settings[DEVICE_ID_KEY] = new_id
        try:
            write_settings(settings, SETTINGS_PATH if path is None else path)
        except Exception as e:
            print('[report] could not persist the report id: %s' % e,
                  file=sys.stderr)
    return new_id


def _collect_telemetry(screen, data, fonts=None, settings=None,
                       settings_path=None, screenshot=True,
                       defer_server=False):
    """Build the diagnostics payload. Collects only; sends nothing.

    `fonts` is accepted for call-site symmetry with the draw helpers and is
    not read — no font metric goes into the report.

    Every sub-collector is individually guarded, so a Pi with a hostile /proc
    still produces a well-formed payload full of nulls rather than an
    exception in the render loop."""
    try:
        sent_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    except Exception:
        sent_at = None
    base = getattr(data, 'server_url', None)
    return {
        'schema': TELEMETRY_SCHEMA,
        'device_id': _get_or_create_device_id(settings, settings_path),
        'sent_at': sent_at,
        'app': {
            'version': _app_version(),
            'mode': 'pygame',
            'install': _install_kind(),
        },
        'host': _collect_host(),
        'display': _collect_display(screen),
        'versions': _collect_versions(),
        'perf': {
            'frame_ms': _frame_ms_summary(),
            'panel_ms': dict((k, round(v, 1)) for k, v in _panel_ms.items()),
            'boot_to_first_paint_s': _first_paint_s,
        },
        # defer_server: leave this for the sender thread. Collecting it here
        # blocks the 10 FPS render loop while the confirm dialog is being
        # built, which is how a 2 s budget turned into a visible stall and
        # then a timeout on real hardware. The dialog lists categories, not
        # values, so nothing on screen depends on having it yet.
        'server': (None if defer_server else _fetch_server_diagnostics(base)),
        'screenshot_png_b64': (_screenshot_b64(screen) if screenshot
                               else None),
    }


# ---- send ----------------------------------------------------------------

def _post_telemetry(payload, url=None, timeout=None):
    """POST the payload ONCE. Returns (ok, human message).

    Exactly one attempt, by design. A retry loop here would be an unattended
    resend of data the operator confirmed once — if it fails, the operator can
    look at the reason and press the button again."""
    dest = TELEMETRY_URL if url is None else url
    tmo = TELEMETRY_TIMEOUT_S if timeout is None else timeout
    try:
        body = json.dumps(payload).encode('utf-8')
    except Exception as e:
        return (False, 'could not encode report: %s' % e)
    try:
        req = _Request(dest, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', TELEMETRY_UA)
        resp = _urlopen(req, timeout=tmo)
    except urllib.error.HTTPError as e:
        return (False, 'rejected: HTTP %s' % getattr(e, 'code', '?'))
    except urllib.error.URLError as e:
        return (False, 'could not reach the server: %s'
                % (getattr(e, 'reason', None) or e))
    except Exception as e:
        return (False, 'send failed: %s' % e)
    code = None
    try:
        code = resp.getcode()
        resp.read(4096)
    except Exception:
        pass
    finally:
        try:
            resp.close()
        except Exception:
            pass
    try:
        if code is not None and not (200 <= int(code) < 300):
            return (False, 'rejected: HTTP %s' % code)
    except Exception:
        pass
    return (True, 'report sent — thank you')


def _send_telemetry_async(payload, holder, url=None, diag_base=None):
    """Run one _post_telemetry on a short-lived daemon thread.

    The render loop must not block for up to 15 s at 10 FPS. The thread only
    ever writes into `holder`, and sets 'done' LAST so the loop can poll a
    single key without a lock."""
    def _worker():
        try:
            # Off the render thread, so this may take as long as it needs.
            if diag_base is not None and payload.get('server') is None:
                payload['server'] = _fetch_server_diagnostics(diag_base)
            ok, msg = _post_telemetry(payload, url=url)
        except Exception as e:          # belt and braces; must never escape
            ok, msg = (False, 'send failed: %s' % e)
        holder['ok'] = ok
        holder['msg'] = msg
        holder['done'] = True

    t = threading.Thread(target=_worker, name='hamclock-report', daemon=True)
    t.start()
    return t


# ---- confirm-before-send UI ----------------------------------------------

def _new_report_state():
    """All report UI state, allocated once outside the render loop."""
    return {
        'stage': 'idle',        # idle -> confirm -> sending -> idle
        'payload': None,
        'lines': None,
        'regions': {},
        'result': None,
        # Set by the render loop once the confirm box has actually been
        # presented. _report_confirm refuses to send until it is True, so a
        # 'T' and a 'Y' arriving in the SAME 100 ms event batch cannot send a
        # report the operator was never shown.
        'shown': False,
        'notice': '',
        'notice_color': 'accent',
        'notice_until': 0.0,
    }


def _fmt_uptime(secs):
    try:
        secs = int(secs)
    except Exception:
        return '?'
    if secs < 3600:
        return '%dm' % (secs // 60)
    if secs < 86400:
        return '%dh %dm' % (secs // 3600, (secs % 3600) // 60)
    return '%dd %dh' % (secs // 86400, (secs % 86400) // 3600)


def _join_dash(*parts):
    return ' - '.join(str(p) for p in parts if p not in (None, '', '--'))


def _report_confirm_lines(payload, callsign=''):
    """The plain-language inventory shown before anything is sent.

    Returns [(text, theme-colour-key)]. Built once when the dialog opens, so
    the overlay redraw allocates nothing."""
    p = payload or {}
    host = p.get('host') or {}
    disp = p.get('display') or {}
    ver = p.get('versions') or {}
    perf = p.get('perf') or {}
    fm = perf.get('frame_ms') or {}
    shot = p.get('screenshot_png_b64')

    mem = host.get('mem_total_kb')
    mem_s = ('%d MB' % (mem // 1024)) if isinstance(mem, int) else None
    frames = ('p50 %s / p90 %s / p99 %s ms  (n=%s)'
              % (fm.get('p50'), fm.get('p90'), fm.get('p99'), fm.get('n')))
    fp = perf.get('boot_to_first_paint_s')
    depth = disp.get('bitsize')
    shot_kb = (len(shot) * 3) // 4096 if shot else 0
    try:
        size_s = '%sx%s' % (disp['size'][0], disp['size'][1])
    except Exception:
        size_s = None

    L = []
    L.append(('Send a one-off diagnostics report to the HamClock project?',
              'fg'))
    L.append(('Destination:  ' + TELEMETRY_URL, 'accent'))
    L.append(('', 'fg'))
    L.append(('THIS IS EVERYTHING THAT LEAVES YOUR PI:', 'bright'))
    L.append(('  Report id   %s' % (p.get('device_id') or '(none)'), 'label'))
    L.append(('              random, made up on this Pi — not your callsign, '
              'MAC or serial', 'muted'))
    L.append(('  Hardware    %s' % (_join_dash(
        host.get('model'), host.get('cpu'),
        ('%s core' % host.get('cores')) if host.get('cores') else None,
        mem_s) or '(unknown)'), 'label'))
    L.append(('  System      %s' % (_join_dash(
        host.get('os'), 'kernel %s' % host.get('kernel')
        if host.get('kernel') else None,
        'Python %s' % host.get('python') if host.get('python') else None,
        'up %s' % _fmt_uptime(host.get('uptime_s'))
        if host.get('uptime_s') is not None else None) or '(unknown)'),
        'label'))
    L.append(('  Display     %s' % (_join_dash(
        'SDL %s' % disp.get('sdl_driver') if disp.get('sdl_driver') else None,
        '%s-bit colour' % depth if depth else None,
        size_s,
        'fullscreen' if disp.get('fullscreen') else None) or '(unknown)'),
        'label'))
    L.append(('  Versions    %s' % (_join_dash(
        'pygame %s' % ver.get('pygame') if ver.get('pygame') else None,
        'SDL %s' % ver.get('sdl') if ver.get('sdl') else None,
        'cairosvg %s' % ver.get('cairosvg') if ver.get('cairosvg')
        else 'no cairosvg',
        'cpulimit installed' if ver.get('cpulimit')
        else 'no cpulimit') or '(unknown)'), 'label'))
    L.append(('  Speed       frame %s%s' % (
        frames,
        (' - first paint %ss' % fp) if fp else ''), 'label'))
    L.append(('  Server      this Pi\'s own %s (feed + cache health)'
              % SERVER_DIAG_PATH, 'label'))
    if shot:
        L.append(('  Screenshot  ~%d KB PNG of the screen behind this box'
                  % shot_kb, 'label'))
        L.append(('', 'fg'))
        L.append(('THAT SCREENSHOT SHOWS YOUR CALLSIGN%s IN THE HEADER, SO'
                  % ((' (%s)' % callsign) if callsign else ''), 'poor'))
        L.append(('THIS REPORT IS NOT ANONYMOUS. Cancel if that is not OK.',
                  'poor'))
    else:
        L.append(('  Screenshot  none — could not be captured, nothing '
                  'attached', 'label'))
    L.append(('', 'fg'))
    L.append(('NOT sent: passwords, tokens, Wi-Fi names or keys, SSH keys, '
              'and no', 'good'))
    L.append(('file contents beyond the diagnostics listed above.', 'good'))
    L.append(('Nothing has been sent yet, and nothing ever is without this '
              'box.', 'good'))
    return L


def _report_overlay_rect(screen_size, lines=None, fonts=None):
    """Where the confirm box goes.

    Sized to its content when the lines and fonts are known — a box padded out
    to 92% of a 450 px screen with 200 px of empty violet under the text reads
    as a broken dialog, and it hides more of the dashboard than it needs to.
    Falls back to the maximum when called without them (tests, and the
    display.update rect on the frame the box closes)."""
    try:
        sw, sh = screen_size
    except Exception:
        return None
    w = max(240, min(sw - 12, int(sw * 0.95)))
    h = max(140, min(sh - 12, int(sh * 0.92)))
    if lines is not None and fonts is not None:
        try:
            title_f = fonts.get('panel') or fonts.get('small') or fonts['label']
            body_f = fonts.get('small') or fonts.get('label') or title_f
            need = (6 + title_f.get_height() + 4
                    + len(lines) * (body_f.get_height() + 1)
                    + (body_f.get_height() + 8) + 12 + 6)
            h = max(140, min(h, need))
        except Exception:
            pass
    return pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)


def _draw_confirm_overlay(screen, rect, title, lines, fonts, theme,
                          confirm_label, cancel_label,
                          confirm_key='send', cancel_key='cancel'):
    """Draw a modal confirm box. Returns {confirm_key: Rect, cancel_key: Rect}.

    Shared by the diagnostics report and the updater, which ask the same
    question in the same shape: here is exactly what will happen, say yes or
    no, and nothing happens until you do.

    Opaque (no per-pixel-alpha scrim): compositing a translucent full-screen
    surface would allocate 1.3 MB every frame the box is up, on a box that has
    512 MB and one core."""
    regions = {}
    if rect is None or rect.w <= 0 or rect.h <= 0:
        return regions
    pygame.draw.rect(screen, theme['card'], rect)
    pygame.draw.rect(screen, theme['accent'], rect, 2)
    title_f = fonts.get('panel') or fonts.get('small') or fonts['label']
    body_f = fonts.get('small') or fonts.get('label') or title_f
    x = rect.x + 10
    max_w = rect.w - 20
    if max_w <= 0:
        return regions
    y = rect.y + 6
    _blit_fit(screen, title_f, title, theme['bright'], x, y, max_w)
    y += title_f.get_height() + 4
    pitch = body_f.get_height() + 1
    btn_h = body_f.get_height() + 8
    body_bottom = rect.bottom - btn_h - 12
    for text, ckey in (lines or ()):
        if y + body_f.get_height() > body_bottom:
            break
        if text:
            _blit_fit(screen, body_f, text,
                      theme.get(ckey, theme['fg']), x, y, max_w)
        y += pitch
    sw_ = body_f.size(confirm_label)[0] + 14
    cw_ = body_f.size(cancel_label)[0] + 14
    by = rect.bottom - btn_h - 6
    sx = rect.x + 12
    cx = sx + sw_ + 14
    if by > y - pitch and cx + cw_ <= rect.right - 12 and by >= rect.y:
        for bx, bw, col, lab, name in (
                (sx, sw_, theme['good'], confirm_label, confirm_key),
                (cx, cw_, theme['poor'], cancel_label, cancel_key)):
            br = pygame.Rect(bx, by, bw, btn_h)
            pygame.draw.rect(screen, theme['border'], br)
            pygame.draw.rect(screen, col, br, 1)
            _blit_fit(screen, body_f, lab, col, br.x + 7, br.y + 4, br.w - 14)
            regions[name] = br
    return regions


def draw_report_overlay(screen, rect, lines, fonts, theme):
    """The confirm-before-send box. Returns {'send': Rect, 'cancel': Rect}."""
    return _draw_confirm_overlay(
        screen, rect, 'SEND DIAGNOSTICS REPORT?', lines, fonts, theme,
        '[Y] SEND IT ONCE', '[N] CANCEL')


def draw_update_overlay(screen, rect, lines, fonts, theme):
    """The confirm-before-update box. Returns {'install': Rect,
    'cancel': Rect}."""
    return _draw_confirm_overlay(
        screen, rect, 'INSTALL THIS UPDATE?', lines, fonts, theme,
        '[Y] INSTALL IT NOW', '[N] NOT NOW',
        confirm_key='install', cancel_key='cancel')


def _set_notice(state, text, color='accent', ttl=None, now=None):
    """Post a message to the status bar's notice line.

    The report state owns that line; the updater and the settings wizard post
    through the same three keys rather than growing a third source the bar
    would have to arbitrate between."""
    state['notice'] = text
    state['notice_color'] = color
    state['notice_until'] = ((time.time() if now is None else now)
                             + (REPORT_NOTICE_TTL_S if ttl is None else ttl))


def _report_notice(state, text, color='accent', now=None):
    _set_notice(state, text, color, REPORT_NOTICE_TTL_S, now)


def _report_notice_text(state, now):
    """The status-bar message, or None once it has aged out."""
    if state.get('notice') and now < state.get('notice_until', 0.0):
        return state['notice']
    return None


def _report_open(state, screen, data, fonts, settings, callsign='',
                 settings_path=None):
    """Arm the confirm dialog. Builds the payload; SENDS NOTHING.

    The screenshot is taken from `screen` as it stands right now — i.e. the
    dashboard frame the operator was looking at when they pressed the button,
    before any overlay pixels exist — so the report shows the real display,
    not this dialog."""
    if state.get('stage') != 'idle':
        return False
    try:
        payload = _collect_telemetry(screen, data, fonts, settings=settings,
                                     settings_path=settings_path,
                                     defer_server=True)
        lines = _report_confirm_lines(payload, callsign)
    except Exception as e:
        print('[report] could not build the report: %s' % e, file=sys.stderr)
        _report_notice(state, 'report failed: %s' % e, 'poor')
        return False
    state['payload'] = payload
    # Carried for the sender thread: _report_confirm has no `data` in scope,
    # and the server diagnostics are deliberately fetched there rather than
    # here so the confirm dialog does not stall the render loop.
    try:
        state['diag_base'] = getattr(data, 'server_url', None)
    except Exception:
        state['diag_base'] = None
    state['lines'] = lines
    state['regions'] = {}
    state['shown'] = False
    state['stage'] = 'confirm'
    state['notice'] = ''
    return True


def _report_cancel(state):
    """Dismiss without sending. Drops the payload (and its screenshot)."""
    if state.get('stage') != 'confirm':
        return False
    state['stage'] = 'idle'
    state['payload'] = None
    state['lines'] = None
    state['regions'] = {}
    state['shown'] = False
    _report_notice(state, 'report cancelled — nothing was sent', 'label')
    return True


def _report_confirm(state, url=None):
    """The ONLY path from a built payload to the network.

    Reachable exactly one way: the operator opened the dialog, the dialog was
    put on screen, and they then explicitly confirmed it. Never called on a
    timer, at boot, or on retry."""
    if state.get('stage') != 'confirm' or state.get('payload') is None:
        return False
    if not state.get('shown'):
        # The box has not been presented yet — this frame's events arrived
        # before it was drawn. Refuse: consent requires having seen it.
        return False
    holder = {'done': False, 'ok': False, 'msg': ''}
    state['result'] = holder
    payload = state['payload']
    # Drop the loop's reference now: the worker holds the only one, so the
    # ~130 KB payload is freed the moment the POST finishes.
    state['payload'] = None
    state['lines'] = None
    state['regions'] = {}
    state['shown'] = False
    state['stage'] = 'sending'
    _report_notice(state, 'sending report…', 'accent')
    _send_telemetry_async(payload, holder, url=url,
                          diag_base=state.get('diag_base'))
    return True


def _report_poll(state):
    """Pick up the worker's result. True when the status bar must redraw."""
    if state.get('stage') != 'sending':
        return False
    holder = state.get('result')
    if not holder or not holder.get('done'):
        return False
    state['result'] = None
    state['stage'] = 'idle'
    if holder.get('ok'):
        _report_notice(state, holder.get('msg') or 'report sent', 'good')
    else:
        _report_notice(state, 'report NOT sent: %s'
                       % (holder.get('msg') or 'unknown error'), 'poor')
    return True


# ---- The UPDATE button ----
#
# hamclock-update-check.timer runs --check once a day at 07:00 and leaves its
# verdict in /run/hamclock-lite/update-status.json. All this side does is read
# that file, offer a button when it says an update exists, and — on an explicit
# confirm — create /run/hamclock-lite/update.request. Root does the rest and
# re-derives everything (manifest, digest, install, health check, rollback)
# for itself.

# The status file changes at most once a day at rest, so 30 s is already an
# extravagant polling rate; it exists so a status written while the operator
# is standing at the screen shows up without a reboot.
UPDATE_POLL_S = 30.0
# ...except while an update is actually running, when the file is rewritten
# every few seconds and the operator is watching a progress line.
UPDATE_APPLY_POLL_S = 5.0

# How long an update message stays on the status bar. Terminal outcomes get a
# minute; anything still in flight (or a broken install) stays until it is
# superseded, because it is the most important thing on the display.
UPDATE_NOTICE_TTL_S = 60.0
UPDATE_PROGRESS_TTL_S = 3600.0

_UPDATE_BUSY_STATES = ('applying', 'rollback', 'rebooting')
_UPDATE_DONE_STATES = ('updated', 'rolled_back', 'broken', 'error', 'current')

# A `detail` long enough to push the version out of the status bar defeats the
# point of showing it.
_UPDATE_DETAIL_MAX = 64


def _new_update_state():
    """All updater UI state, allocated once outside the render loop."""
    return {
        'stage': 'idle',      # idle -> confirm -> requested -> idle
        'status': None,       # last parse of update-status.json, or None
        'next_poll': 0.0,
        'lines': None,
        'regions': {},
        # Same consent interlock the report dialog uses: the confirm is only
        # honoured once the box has actually been put on screen, so a 'U' and
        # a 'Y' arriving in one 100 ms event batch cannot request an update
        # the operator was never shown.
        'shown': False,
        'button': None,       # cached status-bar label, or None
        'notice': '',
        'notice_color': 'accent',
        'notice_until': 0.0,
    }


def _update_detail(status):
    d = (status or {}).get('detail')
    return d[:_UPDATE_DETAIL_MAX] if isinstance(d, str) else ''


def _update_button_label(state):
    """The status-bar chip's text, or None when there is nothing to offer.

    Only 'available' produces a button. 'current', 'error', a mid-apply state
    and a missing status file all mean there is nothing to press."""
    st = state.get('status') or {}
    if state.get('stage') != 'idle' or st.get('state') != 'available':
        return None
    ver = st.get('available')
    if isinstance(ver, str) and ver:
        return 'UPDATE %s [U]' % ver
    return 'UPDATE [U]'


def _update_refresh_button(state):
    state['button'] = _update_button_label(state)
    return state['button']


def _update_progress_notice(state, now=None):
    """Translate the root side's state machine into one line of English."""
    st = state.get('status') or {}
    s = st.get('state')
    detail = _update_detail(st)
    avail = st.get('available') or ''
    inst = st.get('installed') or ''
    if s == 'applying':
        _set_notice(state, 'installing %s… %s' % (avail, detail),
                    'accent', UPDATE_PROGRESS_TTL_S, now)
    elif s == 'rollback':
        _set_notice(state, 'update failed — rolling back to %s: %s'
                    % (inst, detail), 'fair', UPDATE_PROGRESS_TTL_S, now)
    elif s == 'rebooting':
        _set_notice(state, 'update installed — rebooting (%s)' % detail,
                    'accent', UPDATE_PROGRESS_TTL_S, now)
    elif s == 'updated':
        _set_notice(state, 'updated to %s — %s' % (inst or avail, detail),
                    'good', UPDATE_NOTICE_TTL_S, now)
    elif s == 'rolled_back':
        _set_notice(state, 'update failed; rolled back to %s — %s'
                    % (inst, detail), 'poor', UPDATE_NOTICE_TTL_S, now)
    elif s == 'broken':
        # Update AND rollback both failed. This one stays up.
        _set_notice(state, 'UPDATE BROKEN — %s' % detail,
                    'poor', UPDATE_PROGRESS_TTL_S, now)
    elif s == 'error':
        _set_notice(state, 'update check failed: %s' % detail,
                    'poor', UPDATE_NOTICE_TTL_S, now)
    if s in _UPDATE_DONE_STATES:
        # Root is finished with us either way; re-arm the button logic.
        state['stage'] = 'idle'


def _update_poll(state, now=None, run_dir=None):
    """Re-read the status file on the slow cadence.

    Returns True when the status bar must redraw. A missing file is the normal
    case and is not an error, is not logged, and does not raise."""
    now = time.time() if now is None else now
    if now < state.get('next_poll', 0.0):
        return False
    new = read_update_status(run_dir)
    old = state.get('status')
    changed = new != old
    state['status'] = new
    if changed and (new or {}).get('state') != (old or {}).get('state'):
        _update_progress_notice(state, now)
    # Scheduled from what we just read, not from what we knew a moment ago:
    # the first poll after boot finds whatever the file already says, and an
    # update running right then must not be watched at the once-a-day rate.
    busy = (state.get('stage') == 'requested'
            or (new or {}).get('state') in _UPDATE_BUSY_STATES)
    state['next_poll'] = now + (UPDATE_APPLY_POLL_S if busy else UPDATE_POLL_S)
    if not changed:
        return False
    _update_refresh_button(state)
    return True


def _update_confirm_lines(status):
    """Exactly what confirming will do, in the order it will happen.

    Everything here is a claim about hamclock-update.sh's behaviour, so it
    stays honest about the two things an operator most needs to know before
    pressing yes on a kiosk: the display goes away for a while, and it may
    reboot."""
    st = status or {}
    inst = st.get('installed') or '(unknown)'
    avail = st.get('available') or '(unknown)'
    L = []
    L.append(('Install HamClock Lite %s on this Pi?' % avail, 'fg'))
    L.append(('', 'fg'))
    L.append(('  Installed now   %s' % inst, 'label'))
    L.append(('  Would install   %s' % avail, 'accent'))
    L.append(('', 'fg'))
    L.append(('IF YOU CONFIRM, THE PI WILL:', 'bright'))
    L.append(('  1. download the installer over https and check it against',
              'label'))
    L.append(('     the published SHA-256 before running any part of it', 'label'))
    L.append(('  2. back up the current install, then replace it', 'label'))
    L.append(('  3. restart the HamClock services — THIS DISPLAY WILL GO',
              'label'))
    L.append(('     BLANK for a minute or two and come back on its own', 'label'))
    L.append(('  4. REBOOT ITSELF if the boot configuration changed', 'fair'))
    L.append(('', 'fg'))
    L.append(('If the new version fails its health check, the Pi puts %s'
              % inst, 'good'))
    L.append(('back automatically — you do not have to be here for that.',
              'good'))
    L.append(('', 'fg'))
    L.append(('Nothing is downloaded or installed unless you confirm here.',
              'good'))
    return L


def _update_open(state):
    """Arm the confirm box. Downloads nothing and requests nothing."""
    if state.get('stage') != 'idle':
        return False
    st = state.get('status') or {}
    if st.get('state') != 'available':
        _set_notice(state, 'no update is available', 'label', 10.0)
        return False
    state['lines'] = _update_confirm_lines(st)
    state['regions'] = {}
    state['shown'] = False
    state['stage'] = 'confirm'
    state['notice'] = ''
    _update_refresh_button(state)
    return True


def _update_cancel(state):
    """Dismiss without asking for anything."""
    if state.get('stage') != 'confirm':
        return False
    state['stage'] = 'idle'
    state['lines'] = None
    state['regions'] = {}
    state['shown'] = False
    _set_notice(state, 'update postponed — nothing was installed',
                'label', 10.0)
    _update_refresh_button(state)
    return True


def _update_confirm(state, run_dir=None):
    """The ONLY path to /run/hamclock-lite/update.request.

    And creating that empty file is the ONLY thing that happens here. No
    download, no digest check, no unpack, no install, no service restart, no
    reboot — a root-owned path unit sees the file and hands all of that to
    hamclock-update.sh, which re-derives what to install and verifies it
    itself. This side cannot choose what gets installed, only ask that
    something be."""
    if state.get('stage') != 'confirm':
        return False
    if not state.get('shown'):
        # This frame's events arrived before the box was drawn. Consent
        # requires having seen it.
        return False
    state['lines'] = None
    state['regions'] = {}
    state['shown'] = False
    if request_update(run_dir):
        state['stage'] = 'requested'
        _set_notice(state, 'update requested — the Pi is installing it',
                    'accent', UPDATE_PROGRESS_TTL_S)
    else:
        state['stage'] = 'idle'
        _set_notice(state, 'could not ask for the update — no %s' % RUN_DIR,
                    'poor')
    state['next_poll'] = 0.0   # show progress on the very next frame
    _update_refresh_button(state)
    return True


def _update_notice_text(state, now):
    """The status-bar message, or None once it has aged out."""
    if state.get('notice') and now < state.get('notice_until', 0.0):
        return state['notice']
    return None


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
        text = "RECOVERING…"
        fg = theme.get("fg", (220, 230, 240))
        # Compute approx text width to center via _blit_text (which uses the glyph cache).
        try:
            sample = font.render(text, True, fg)
            tw, th = sample.get_size()
        except Exception:
            tw, th = 200, 30
        x = (sw - tw) // 2
        y = (sh - th) // 2
        _blit_text(screen, font, text, fg, x, y)
        import pygame as _pg
        _pg.display.flip()
    except Exception:
        pass


#: Ceiling on how many pixels we will render per frame. Measured: the draw
#: cost of the text-heavy panels scales SUB-linearly with area (2.70x the
#: pixels costs 2.03x the time, because most of it is cached-glyph blitting
#: rather than fills). A Pi 1B reporting a 68 ms p99 at 800x600 therefore lands
#: near 140 ms at 1440x900 — one slow frame per data refresh, roughly once a
#: minute, while the once-a-second clock path stays at 0.19 ms. That is a good
#: trade for a display read across a room. A 4K panel would be 17x the pixels
#: and is not, hence the cap: above it we fall back to the largest mode that
#: keeps the panel's own aspect ratio.
DISPLAY_PIXEL_BUDGET = 2_200_000        # ~1920x1080

#: Tolerance when deciding whether a mode "matches" the panel's shape.
_ASPECT_TOL = 0.03


def _choose_display_mode(native, modes, budget=DISPLAY_PIXEL_BUDGET,
                         requested=None):
    """Pick the framebuffer to render into.

    Native is strongly preferred: rendering at the panel's own resolution means
    NO scaling at all. The alternative is what this Pi was doing — asking for
    720x450, having KMS snap it to the nearest real mode (800x600), and letting
    the panel stretch that 4:3 image across a 16:10 1440x900 screen at a
    fractional 1.8x. Every source pixel then lands on 1.8 destination pixels,
    which is why the text looked chewed no matter how the fonts were tuned;
    antialiasing cannot rescue a non-integer upscale.

    `requested` ("1280x800") wins if it is offered. Otherwise native, if it
    fits the budget. Otherwise the largest same-shape mode that does — a
    smaller mode with the right aspect still scales, but at least it does not
    distort, and an integer fraction of native scales cleanly.

    Returns (w, h), or None to mean "let SDL decide" when we know nothing.
    """
    def ok(m):
        try:
            return int(m[0]) > 0 and int(m[1]) > 0
        except Exception:
            return False

    cands = [(int(w), int(h)) for (w, h) in (modes or []) if ok((w, h))]

    if requested:
        try:
            rw, rh = (int(v) for v in str(requested).lower().split('x'))
            if rw > 0 and rh > 0 and (not cands or (rw, rh) in cands):
                return (rw, rh)
        except Exception:
            pass

    if not ok(native or (0, 0)):
        return None
    nw, nh = int(native[0]), int(native[1])
    if nw * nh <= budget:
        return (nw, nh)

    target = nw / float(nh)
    same_shape = [m for m in cands
                  if abs(m[0] / float(m[1]) - target) <= _ASPECT_TOL
                  and m[0] * m[1] <= budget]
    if same_shape:
        return max(same_shape, key=lambda m: m[0] * m[1])
    under = [m for m in cands if m[0] * m[1] <= budget]
    if under:
        return max(under, key=lambda m: m[0] * m[1])
    return (nw, nh)


def _init_display(requested_mode=None):
    """SDL driver ladder. Bookworm SDL2 may lack fbcon (Phase 0 risk).
    Try fbcon -> kmsdrm -> x11 -> dummy; honor a pre-set SDL_VIDEODRIVER
    first if it's in the ladder. Logs every attempt to stderr so
    journalctl captures the actual reason on the Pi."""
    import pygame
    preset = os.environ.get('SDL_VIDEODRIVER')
    ladder = ['fbcon', 'kmsdrm', 'x11', 'dummy']
    if preset:
        ladder = [preset] + [d for d in ladder if d != preset]
    os.environ.setdefault('SDL_FBDEV', '/dev/fb0')
    pygame.init()
    last_err = None
    for drv in ladder:
        os.environ['SDL_VIDEODRIVER'] = drv
        try:
            pygame.display.quit()
        except Exception:
            pass
        try:
            pygame.display.init()
            # Ask the driver what the panel actually is, rather than asking for
            # a size no connector offers and letting it snap to the nearest.
            native, modes = None, []
            try:
                info = pygame.display.Info()
                if info.current_w > 0 and info.current_h > 0:
                    native = (info.current_w, info.current_h)
            except Exception:
                pass
            try:
                listed = pygame.display.list_modes()
                if listed and listed != -1:
                    modes = list(listed)
            except Exception:
                pass
            want = _choose_display_mode(native, modes,
                                        requested=requested_mode)
            if want is None:
                want = (SCREEN_W, SCREEN_H)
            scr = pygame.display.set_mode(want, pygame.FULLSCREEN)
            print('[display] SDL driver=%s native=%s asked=%s got=%s modes=%d'
                  % (drv, native, want, scr.get_size(), len(modes)),
                  file=sys.stderr)
            return scr
        except Exception as e:
            print('[display] %s failed: %s' % (drv, e), file=sys.stderr)
            last_err = e
    raise RuntimeError(
        'No SDL video driver succeeded; last error: %s' % last_err)


def main(argv=None):
    # Use parse_known_args so stray runner args (e.g. pytest) don't kill us
    # when a caller invokes main() directly without scrubbing sys.argv.
    raw_argv = sys.argv[1:] if argv is None else argv
    args, _unknown = _parse_args_known(raw_argv)
    injected_iter = None
    if args.inject_events:
        injected_iter = _inject_event_iter(
            _load_injected_events(args.inject_events))

    # Tier-1a perf: relax the gen-0 GC threshold from the default 700 to
    # 50000 so short-lived per-frame allocations don't trigger a sweep mid
    # render. We still collect gen-1/gen-2 normally so long-lived churn is
    # cleaned. The Pi 1's 256 MB RAM tolerates this comfortably given our
    # working set is dominated by SDL surfaces, not Python objects.
    gc.set_threshold(50_000, 10, 10)

    # Read the display override before initialising, but do NOT go through
    # DEFAULT_SETTINGS: load_settings is contractually required to return
    # exactly its four keys on a fresh install (the wizard, --setup-cli and the
    # settings tests all round-trip that shape). An absent key just means
    # "choose for me", which is the default behaviour anyway.
    _mode_pref = None
    try:
        _mode_pref = (load_settings(SETTINGS_PATH) or {}).get('display_mode')
    except Exception:
        _mode_pref = None
    if _mode_pref in ('', 'auto', 'native'):
        _mode_pref = None

    screen = _init_display(requested_mode=_mode_pref)
    pygame.display.set_caption('HamClock Lite')
    try:
        pygame.mouse.set_visible(True)
    except Exception:
        pass

    fonts = _make_fonts()

    # ---- Phase 4: first-boot wizard ----
    settings = load_settings(SETTINGS_PATH)
    need_wizard = not os.path.exists(SETTINGS_PATH)
    if need_wizard:
        # Wizard always renders in kstate (user hasn't picked yet).
        wiz_theme = THEMES["kstate"] if "THEMES" in globals() else {
            "bg": (42, 20, 80), "card": (58, 29, 101),
            "fg": (232, 221, 245), "muted": (146, 126, 180),
            "label": (184, 160, 216), "accent": (244, 197, 92),
            "good": (34, 197, 94), "fair": (234, 179, 8),
            "poor": (239, 68, 68),
            "band_palette": [(0, 0, 0)] * 10,
            "sdo_accent": (244, 197, 92),
        }
        settings = setup_screen(screen, fonts, wiz_theme)
        try:
            write_settings(settings, SETTINGS_PATH)
        except OSError as e:
            print("[main] could not persist settings: %s" % e,
                  file=sys.stderr)
        else:
            # Ask root to apply the NTP server. We cannot write the timesyncd
            # drop-in ourselves and must not try; hamclock-apply-settings.sh
            # re-reads settings.json and re-validates the value from scratch.
            request_settings_apply()

    theme = THEMES.get(settings.get('theme', 'kstate'), THEMES['kstate'])

    _run_render_loop(screen, fonts, theme, settings, injected_iter)


def _parse_args_known(argv):
    """parse_known_args wrapper around _parse_args' parser so that callers
    invoking main() inside a host process (e.g. pytest) don't blow up on
    arguments meant for the host."""
    p = argparse.ArgumentParser(prog='hamclock_pygame')
    p.add_argument('--inject-events', default=None,
                   help='debug builds only: JSON event list to replay')
    args, unknown = p.parse_known_args(argv)
    if args.inject_events is not None and os.environ.get('HAMCLOCK_DEBUG') != '1':
        p.error('--inject-events is debug builds only '
                '(set HAMCLOCK_DEBUG=1 to enable)')
    return args, unknown


def _reopen_setup(screen, fonts, theme, settings, settings_path=None):
    """'S' on the dashboard: re-run the wizard over the current settings.

    Before this existed the wizard ran only when settings.json was absent, so
    once a Pi was set up the operator could never change their NTP server,
    timezone or theme from the device again — the only route was an SSH
    session and a text editor, on a kiosk that may well have neither a network
    nor a keyboard-shell.

    Returns (new_settings_or_None, notice_text, notice_colour_key). None means
    nothing changed, which is also what a cancel produces."""
    path = SETTINGS_PATH if settings_path is None else settings_path
    try:
        new = setup_screen(screen, fonts, theme, initial=settings,
                           allow_cancel=True,
                           # The dashboard has been up and fetching for a
                           # while, so the clock is already whatever it is
                           # going to be; blocking ten seconds for a sync here
                           # would only stall an operator at the keyboard.
                           wait_ntp=False)
    except SystemExit:
        raise
    except Exception as e:
        print('[setup] wizard failed: %s' % e, file=sys.stderr)
        return (None, 'settings unchanged: %s' % e, 'poor')
    finally:
        try:
            pygame.mouse.set_visible(True)
        except Exception:
            pass
    if new is None:
        return (None, 'settings unchanged', 'label')
    merged = dict(settings)
    merged.update(new)
    try:
        write_settings(merged, path)
    except OSError as e:
        print('[setup] could not persist settings: %s' % e, file=sys.stderr)
        return (None, 'could not save settings: %s' % e, 'poor')
    # All we may do about the NTP server is ask. Root re-reads settings.json
    # and re-validates the value itself; nothing is passed through this call.
    if request_settings_apply():
        return (merged, 'settings saved', 'good')
    return (merged, 'settings saved (could not ask root to apply NTP)', 'fair')


def _run_render_loop(screen, fonts, theme, settings, injected_iter=None):
    """The dashboard render loop, factored out of main() so that the
    Phase-4 first-boot wizard can run beforehand and tests can patch this
    entry point to assert ordering without spinning up real rendering."""
    # Tier-1a perf: stash the {band: color} LUT on the theme so draw_dx_spots
    # and draw_band_activity don't rebuild dict(zip(...)) every frame.
    if '_band_lut' not in theme:
        theme['_band_lut'] = dict(zip(HF_BANDS, theme['band_palette']))
    data = HamClockData()
    try:
        data.start_background(data_interval=60, image_interval=900)
    except Exception as e:
        print('data start error:', e, file=sys.stderr)

    active_tab = 'drap'
    # Absolute deadline rather than a countdown, so a long frame or a paused
    # process cannot make the panel drift slower than the configured interval.
    next_tab_at = time.time() + TAB_CYCLE_S
    image_cache = {}
    image_cache_ts = {}
    tab_regions = {}
    # Opt-in diagnostics: the status bar's SEND REPORT chip is hit-tested from
    # here exactly like the propagation tabs above, and the state dict is
    # allocated once, outside the loop.
    status_regions = {}
    report = _new_report_state()
    # The UPDATE chip lives in the same bar and is hit-tested the same way.
    # Its state is polled off /run/hamclock-lite/update-status.json on a slow
    # cadence — the file is written at most once a day — so the loop pays a
    # stat and a ~150-byte read every 300 frames, and nothing at all in
    # between.
    update = _new_update_state()
    tab_image_key = PROP_TAB_IMAGE_KEY
    dirty_state = {
        'prev_active_tab': None,
        'prev_second': -1,
        'prev_data_refresh': 0.0,
        'prev_image_refresh': 0.0,
        'full_flip_pending': True,
    }
    # Tier 2b: per-panel next-due-at clock. 0.0 means "draw on the very next
    # frame" so first-paint catches every panel. After each panel's draw, we
    # bump its entry by _CADENCE_S[name]. A tab change or pending full flip
    # forces all panels to redraw regardless of due time.
    _panel_due_at = {name: 0.0 for name in _CADENCE_S}
    # Panels that have already taken their one-time phase offset.
    _phased = set()

    clock = pygame.time.Clock()
    running = True
    # A transient SDL/framebuffer error (e.g. an HDMI hotplug or VT switch)
    # raising out of the loop would crash the client to the bare console.
    # Absorb such errors; if they persist, exit cleanly so the kiosk wrapper
    # restarts us with a fresh SDL context.
    consecutive_errors = 0
    while running:
        try:
            frame_t0 = _mono()
            frame_events = (next(injected_iter)
                            if injected_iter is not None
                            else pygame.event.get())
            callsign = settings.get('callsign') or os.environ.get(
                'HAMCLOCK_CALLSIGN', 'N0CALL')
            for event in frame_events:
                if event.type == pygame.QUIT:
                    running = False
                elif report['stage'] == 'confirm':
                    # The confirm box owns the keyboard while it is up: ESC
                    # and Q must dismiss the report, NOT quit the kiosk out
                    # from under an operator who is still reading it.
                    if event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_y, pygame.K_RETURN,
                                         pygame.K_KP_ENTER):
                            _report_confirm(report)
                        elif event.key in (pygame.K_n, pygame.K_ESCAPE,
                                           pygame.K_q):
                            _report_cancel(report)
                    elif event.type == pygame.MOUSEBUTTONDOWN:
                        pos = event.pos
                        send_r = report['regions'].get('send')
                        cancel_r = report['regions'].get('cancel')
                        if send_r is not None and send_r.collidepoint(pos):
                            _report_confirm(report)
                        elif (cancel_r is not None
                                and cancel_r.collidepoint(pos)):
                            _report_cancel(report)
                    if report['stage'] != 'confirm':
                        # Box just closed: repaint the dashboard under it and
                        # surface the outcome without waiting a cadence tick.
                        dirty_state['full_flip_pending'] = True
                        _panel_due_at['status'] = 0.0
                elif update['stage'] == 'confirm':
                    # Same rule as the report box: while it is up, ESC and Q
                    # answer the question in front of the operator, they do
                    # not quit the kiosk.
                    if event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_y, pygame.K_RETURN,
                                         pygame.K_KP_ENTER):
                            _update_confirm(update)
                        elif event.key in (pygame.K_n, pygame.K_ESCAPE,
                                           pygame.K_q):
                            _update_cancel(update)
                    elif event.type == pygame.MOUSEBUTTONDOWN:
                        pos = event.pos
                        go_r = update['regions'].get('install')
                        no_r = update['regions'].get('cancel')
                        if go_r is not None and go_r.collidepoint(pos):
                            _update_confirm(update)
                        elif no_r is not None and no_r.collidepoint(pos):
                            _update_cancel(update)
                    if update['stage'] != 'confirm':
                        dirty_state['full_flip_pending'] = True
                        _panel_due_at['status'] = 0.0
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif event.key == pygame.K_t:
                        # Opens the confirm box only. This never sends.
                        _report_open(report, screen, data, fonts, settings,
                                     callsign)
                    elif event.key == pygame.K_u:
                        # Opens the confirm box only. This never installs.
                        # Bound as well as clickable because a kiosk is
                        # routinely run with no mouse.
                        _update_open(update)
                        _panel_due_at['status'] = 0.0
                    elif event.key == pygame.K_s:
                        # Re-open the setup wizard over the live settings.
                        new_settings, _note, _col = _reopen_setup(
                            screen, fonts, theme, settings)
                        if new_settings is not None:
                            settings = new_settings
                            _nt = THEMES.get(settings.get('theme'))
                            if _nt is not None:
                                theme = _nt
                                if '_band_lut' not in theme:
                                    theme['_band_lut'] = dict(
                                        zip(HF_BANDS, theme['band_palette']))
                        _report_notice(report, _note, _col)
                        # The wizard painted over the whole display; every
                        # panel has to come back, not just the due ones.
                        dirty_state['full_flip_pending'] = True
                        for _name in _panel_due_at:
                            _panel_due_at[_name] = 0.0
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    pos = event.pos
                    send_btn = status_regions.get('send_report')
                    update_btn = status_regions.get('update')
                    if send_btn is not None and send_btn.collidepoint(pos):
                        _report_open(report, screen, data, fonts, settings,
                                     callsign)
                    elif (update_btn is not None
                            and update_btn.collidepoint(pos)):
                        _update_open(update)
                        _panel_due_at['status'] = 0.0
                    else:
                        for name, r in tab_regions.items():
                            if r.collidepoint(pos):
                                active_tab = name
                                dirty_state['full_flip_pending'] = True
                                # Someone is standing at the screen and chose
                                # this map. Give them the full interval to
                                # read it rather than yanking it away partway
                                # through whatever the cycle had left.
                                next_tab_at = time.time() + TAB_CYCLE_S
                                break

            if _report_poll(report):
                _panel_due_at['status'] = 0.0
            if _update_poll(update):
                _panel_due_at['status'] = 0.0

            if report['stage'] == 'confirm':
                # Freeze the dashboard behind the box: the pixels underneath
                # stay as they were, we present only the box's own rect, and
                # the full repaint is deferred to the frame it closes on.
                orect = _report_overlay_rect(screen.get_size(),
                                             report['lines'], fonts)
                report['regions'] = draw_report_overlay(
                    screen, orect, report['lines'], fonts, theme)
                if orect is not None:
                    pygame.display.update(orect)
                # The operator has now actually seen it; only from here can a
                # confirm be accepted.
                report['shown'] = True
                dirty_state['full_flip_pending'] = True
                clock.tick(10)
                consecutive_errors = 0
                continue

            if update['stage'] == 'confirm':
                # Identical treatment to the report box above.
                orect = _report_overlay_rect(screen.get_size(),
                                             update['lines'], fonts)
                update['regions'] = draw_update_overlay(
                    screen, orect, update['lines'], fonts, theme)
                if orect is not None:
                    pygame.display.update(orect)
                update['shown'] = True
                dirty_state['full_flip_pending'] = True
                clock.tick(10)
                consecutive_errors = 0
                continue

            # Advance the propagation panel on its own. Must happen BEFORE
            # will_full_flip is computed below, because a tab change is one of
            # the things that predicate tests for — deciding after it would
            # paint the new tab into a partial-update frame and leave the old
            # tab's pixels behind until something else forced a flip.
            if TAB_CYCLE_S > 0 and time.time() >= next_tab_at:
                _cycled = _next_cycle_tab(active_tab, PROP_TABS,
                                          tab_image_key, data)
                if _cycled != active_tab:
                    active_tab = _cycled
                    dirty_state['full_flip_pending'] = True
                next_tab_at = time.time() + TAB_CYCLE_S

            sw, sh = screen.get_size()
            # Tier-0 perf: only memset the whole 720x450 framebuffer when this
            # frame will end in a full display.flip(). The dirty-rect helper
            # signals "full flip" on first frame, tab change, or pending flag;
            # peek at the same predicate here (without mutating state) so we
            # can gate the fill. On partial-update frames each panel's
            # draw_panel paints over its own pixels, so the bg fill is dead
            # work that negates the dirty-rect win.
            will_full_flip = (
                dirty_state.get('full_flip_pending')
                or dirty_state.get('prev_active_tab') != active_tab
            )
            if will_full_flip:
                screen.fill(theme['bg'])

            # Phase 1b item 5: panel rects are cached and rebuilt only on
            # screen-size change. Per-frame pygame.Rect allocations are gone
            # for every panel that uses a stable position.
            layout = _get_layout((sw, sh))
            data_ts = data.last_data_refresh

            # Tier 2b: cadence gate. On a full-flip frame (first paint or
            # tab change) every panel redraws; otherwise each panel only
            # redraws when its _panel_due_at has elapsed. Panels that ran
            # this frame land in redrawn_this_frame so we can build the
            # display.update() rect list from the actual draws, instead of
            # the speculative dirty-rect helper.
            now_ts = time.time()
            force_all = will_full_flip

            def _panel_due(name):
                if force_all:
                    return True
                return _panel_due_at[name] <= now_ts

            redrawn_this_frame = set()

            header = layout["header"]
            if _panel_due('header'):
                _t0 = _mono()
                draw_header(screen, header, callsign, fonts, theme, data=data)
                _record_panel_ms('header', _t0)
                redrawn_this_frame.add('header')
                _panel_due_at['header'] = _next_due('header', now_ts, _phased)

            status = layout["status"]
            if _panel_due('status'):
                _t0 = _mono()
                # One notice line, two sources. A report outcome is a direct
                # answer to something the operator just did, so it wins; an
                # update message is the fallback and is the longer-lived of
                # the two anyway.
                _msg = _report_notice_text(report, now_ts)
                _msg_col = report['notice_color']
                if _msg is None:
                    _msg = _update_notice_text(update, now_ts)
                    _msg_col = update['notice_color']
                _sr = draw_status_bar(
                    screen, status, data, fonts, theme,
                    notice=_msg, notice_color=_msg_col,
                    update_label=update['button'])
                _record_panel_ms('status', _t0)
                if _sr is not None:
                    status_regions = _sr
                redrawn_this_frame.add('status')
                _panel_due_at['status'] = _next_due('status', now_ts, _phased)

            panel_gap = 4

            # ---- LEFT COLUMN ----
            titles = ['SOLAR', 'BANDS', 'SDO IMAGE',
                      'GEOMAGNETIC', 'X-RAY FLUX', 'OPEN BANDS']
            layout_keys = ['solar', 'bands', 'sdo',
                           'geomag', 'xray', 'open_bands']
            # Compute inner rects without re-issuing draw_panel chrome on
            # frames where no left-column panel is due (chrome blit is
            # cheap but pointless if nothing inside changed).
            panel_rects = []
            for key, t in zip(layout_keys, titles):
                if _panel_due(key):
                    inner = draw_panel(screen, layout[key], t, fonts, theme)
                else:
                    inner = _panel_inner_rect(layout[key])
                panel_rects.append(inner)

            if _panel_due('solar'):
                _t0 = _mono()
                try:
                    draw_solar(screen, panel_rects[0], data.solar or {},
                               fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                _record_panel_ms('solar', _t0)
                redrawn_this_frame.add('solar')
                _panel_due_at['solar'] = _next_due('solar', now_ts, _phased)
            if _panel_due('bands'):
                _t0 = _mono()
                try:
                    draw_bands(screen, panel_rects[1], data.bands or {}, fonts, theme)
                except Exception:
                    pass
                _record_panel_ms('bands', _t0)
                redrawn_this_frame.add('bands')
                _panel_due_at['bands'] = _next_due('bands', now_ts, _phased)
            if _panel_due('sdo'):
                # Tier 2.5: hoisted out of the try. The cadence line below
                # reads it, and a NameError there would land in the render
                # loop's consecutive_errors counter instead of this panel's
                # own except.
                sdo_surf = None
                _t0 = _mono()
                try:
                    sdo_surf = _get_cached_image(data, 'solar-image', image_cache, image_cache_ts)
                    draw_image(screen, panel_rects[2], sdo_surf, fonts, theme,
                               image_key='solar-image',
                               fetched_at=_image_stamp(data, 'solar-image'),
                               status=_image_status_text(data, 'solar-image'))
                except Exception:
                    pass
                _record_panel_ms('sdo', _t0)
                redrawn_this_frame.add('sdo')
                _panel_due_at['sdo'] = now_ts + (
                    _CADENCE_S['sdo'] if sdo_surf is not None
                    else _CADENCE_S_NO_IMAGE.get('sdo', _CADENCE_S['sdo']))
            if _panel_due('geomag'):
                _t0 = _mono()
                try:
                    draw_geomag(screen, panel_rects[3], data.solar or {},
                                fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                _record_panel_ms('geomag', _t0)
                redrawn_this_frame.add('geomag')
                _panel_due_at['geomag'] = _next_due('geomag', now_ts, _phased)
            if _panel_due('xray'):
                _t0 = _mono()
                try:
                    draw_xray(screen, panel_rects[4], data.solar or {},
                              fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                _record_panel_ms('xray', _t0)
                redrawn_this_frame.add('xray')
                _panel_due_at['xray'] = _next_due('xray', now_ts, _phased)
            if _panel_due('open_bands'):
                _t0 = _mono()
                try:
                    draw_open_bands(screen, panel_rects[5], data.bands or {},
                                    fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                _record_panel_ms('open_bands', _t0)
                redrawn_this_frame.add('open_bands')
                _panel_due_at['open_bands'] = _next_due('open_bands', now_ts, _phased)

            # ---- MIDDLE COLUMN ----
            mid_rect = layout["muf"]
            if _panel_due('muf_text'):
                _t0 = _mono()
                mid_inner = draw_panel(screen, mid_rect, 'MUF MAP', fonts, theme)
                muf_surf = None
                try:
                    muf_surf = _get_cached_image(data, 'muf-map',
                                                 image_cache, image_cache_ts)
                except Exception:
                    muf_surf = None
                try:
                    draw_muf_text(
                        screen, mid_inner, data.solar or {}, fonts, theme,
                        surf=muf_surf, image_key='muf-map',
                        fetched_at=(data.image_fetched_at.get('muf-map', 0.0)
                                    if isinstance(
                                        getattr(data, 'image_fetched_at', None),
                                        dict) else 0.0))
                except Exception:
                    pass
                _record_panel_ms('muf_text', _t0)
                redrawn_this_frame.add('muf_text')
                _panel_due_at['muf_text'] = _next_due('muf_text', now_ts, _phased)

            # ---- RIGHT COLUMN ----
            dx_r = layout["dx_spots"]
            if _panel_due('dx_spots'):
                _t0 = _mono()
                dx_inner = draw_panel(screen, dx_r, 'DX SPOTS', fonts, theme)
                try:
                    draw_dx_spots(screen, dx_inner, data.dxspots or [], fonts, theme)
                except Exception:
                    pass
                _record_panel_ms('dx_spots', _t0)
                redrawn_this_frame.add('dx_spots')
                _panel_due_at['dx_spots'] = _next_due('dx_spots', now_ts, _phased)

            ba_r = layout["band_activity"]
            if _panel_due('band_activity'):
                _t0 = _mono()
                ba_inner = draw_panel(screen, ba_r, 'BAND ACTIVITY', fonts, theme)
                try:
                    draw_band_activity(screen, ba_inner, data.dxspots or [], fonts, theme)
                except Exception:
                    pass
                _record_panel_ms('band_activity', _t0)
                redrawn_this_frame.add('band_activity')
                _panel_due_at['band_activity'] = _next_due('band_activity', now_ts, _phased)

            prop_r = layout["propagation"]
            if _panel_due('propagation'):
                _t0 = _mono()
                prop_inner = draw_panel(screen, prop_r, 'PROPAGATION', fonts, theme)
                tab_bar = pygame.Rect(prop_inner.x, prop_inner.y, prop_inner.w, 20)
                tab_regions = draw_tabs(screen, tab_bar, PROP_TABS,
                                        active_tab, fonts, theme)
                img_rect = pygame.Rect(prop_inner.x, prop_inner.y + 24,
                                       prop_inner.w, prop_inner.h - 24)
                # Tier 2.5: hoisted out of the try — see the sdo panel above.
                surf = None
                try:
                    key = tab_image_key.get(active_tab, 'real-drap')
                    surf = _get_cached_image(data, key, image_cache, image_cache_ts)
                    draw_image(screen, img_rect, surf, fonts, theme,
                               image_key=key,
                               fetched_at=_image_stamp(data, key),
                               status=_image_status_text(data, key))
                except Exception:
                    pass
                _record_panel_ms('propagation', _t0)
                redrawn_this_frame.add('propagation')
                _panel_due_at['propagation'] = now_ts + (
                    _CADENCE_S['propagation'] if surf is not None
                    else _CADENCE_S_NO_IMAGE.get('propagation',
                                                 _CADENCE_S['propagation']))

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
            # Tier 2b: present this frame. Full-flip path matches the legacy
            # _compute_dirty_rects contract (first frame, tab change, pending
            # flag). Otherwise we update only the rects of panels actually
            # redrawn this frame; if nothing was due, we present nothing.
            if (dirty_state.get('full_flip_pending')
                    or dirty_state.get('prev_active_tab') != active_tab):
                dirty_state['full_flip_pending'] = False
                dirty_state['prev_active_tab'] = active_tab
                pygame.display.flip()
                _note_first_paint()
            else:
                rects = [panel_rects_map[n] for n in redrawn_this_frame
                         if n in panel_rects_map]
                if rects:
                    pygame.display.update(rects)
            # Diagnostics: one ring-buffer slot, written in place. Measures
            # the render work, not the tick() sleep that pads it out to the
            # frame budget — the question this answers is whether an ARMv6 Pi
            # can do a frame inside 100 ms, and a sleep would mask that.
            _record_frame_ms(frame_t0)
            clock.tick(10)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            print("render loop error (%d): %s"
                  % (consecutive_errors, e), file=sys.stderr)
            backoff_ms = min(100 * consecutive_errors, 500)
            _render_recovering_overlay(screen, fonts, theme if 'theme' in dir() else THEMES["kstate"])
            if consecutive_errors > 15:
                print("too many render errors — exiting for a clean restart",
                      file=sys.stderr)
                running = False
            else:
                time.sleep(backoff_ms / 1000.0)

    # A report the operator confirmed a moment before pressing Q is in flight
    # on a daemon thread, which the interpreter kills on exit. Give it a short
    # grace period rather than silently dropping it; deliberately far below
    # TELEMETRY_TIMEOUT_S so a dead uplink cannot hold up a kiosk restart.
    if report['stage'] == 'sending':
        print('[report] finishing an in-flight report before exit…',
              file=sys.stderr)
        for t in threading.enumerate():
            if t.name == 'hamclock-report':
                try:
                    t.join(timeout=3.0)
                except Exception:
                    pass

    try:
        data.stop()
    except Exception:
        pass
    pygame.quit()


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


def _restart_timesyncd():
    import subprocess as _sp
    try:
        _sp.run(["systemctl", "restart", "systemd-timesyncd"], check=False)
    except FileNotFoundError:
        print("[setup] systemctl not found; skipping restart",
              file=sys.stderr)


def _apply_ntp(ntp_value, conf_path, restart):
    """Write the systemd-timesyncd drop-in, or remove it. Returns an exit code.

    Validation is by FORMAT, first, and it is what decides whether anything is
    written:

      * The old implementation's only check was socket.gethostbyname(), and
        gethostbyname("") RESOLVES — so an empty value sailed straight through
        and wrote a bare `NTP=` line, which systemd cannot parse. Empty now
        means what the operator meant by it: remove the drop-in and go back to
        the distro's default time servers.
      * systemd's NTP= takes a space-separated LIST, which is how fallbacks
        are specified; the old check passed the whole string to
        gethostbyname() and so rejected every list out of hand.
      * Resolution is now advisory and happens AFTER the file is written.
        Refusing to save an internal time server because the LAN is not up yet
        — on a machine with no RTC, at first boot, which is exactly when this
        runs — was the wrong call.
    """
    value = normalize_ntp(ntp_value)

    if not value:
        try:
            os.remove(conf_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            print("[setup] could not remove %s: %s" % (conf_path, e),
                  file=sys.stderr)
            return 2
        else:
            print("[setup] NTP cleared; removed %s (using distro defaults)"
                  % conf_path, file=sys.stderr)
            if restart:
                _restart_timesyncd()
        return 0

    ok, err = validate_ntp_list(value)
    if not ok:
        print("[setup] REFUSING invalid NTP value %r: %s" % (ntp_value, err),
              file=sys.stderr)
        return 3

    os.makedirs(os.path.dirname(conf_path) or ".", exist_ok=True)
    with open(conf_path, "w") as f:
        f.write("[Time]\nNTP=%s\n" % value)
    os.chmod(conf_path, 0o644)

    # Advisory only: worth telling the operator, never worth refusing over.
    # Skipped for IP literals, which have nothing to resolve — and on which
    # gethostbyname() would report a bogus failure for every IPv6 address.
    first = value.split()[0]
    if ":" not in first and not _NTP_DIGITS_DOTS_RE.match(first):
        try:
            socket.gethostbyname(first)
        except OSError as e:
            print("[setup] warning: NTP host %r does not resolve yet: %s"
                  % (first, e), file=sys.stderr)

    if restart:
        _restart_timesyncd()
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
    ok, err = validate_ntp(args.ntp)
    if not ok:
        print("[setup] invalid ntp: %s" % err, file=sys.stderr)
        return 2

    d = {
        "callsign": args.callsign.upper(),
        "timezone": args.timezone,
        "theme": args.theme,
        "ntp": normalize_ntp(args.ntp),
    }
    # Re-running setup must not churn the diagnostics report id: reports from
    # this Pi should still correlate after the operator fixes a typo in their
    # callsign. Only carried through if one already exists; never created here.
    prior = load_settings(args.settings_path).get(DEVICE_ID_KEY)
    if isinstance(prior, str) and prior:
        d[DEVICE_ID_KEY] = prior

    _drop_privileges_if_root()
    write_settings(d, args.settings_path)
    # Deliberately not `and args.ntp`: an empty value is a real instruction
    # ("go back to the distro defaults"), and _apply_ntp implements it by
    # removing the drop-in. Gating on truthiness made clearing the setting
    # impossible from here.
    if args.apply_ntp:
        rc = _apply_ntp(d["ntp"], args.ntp_conf_path,
                        restart=not args.no_restart_timesyncd)
        if rc != 0:
            return rc
    return 0


if __name__ == '__main__':
    # CLI dispatch: --setup-cli short-circuits before the dashboard runs.
    rc = _cli_main(sys.argv[1:])
    if rc is not None:
        sys.exit(rc)
    main()  # existing dashboard entry point
HCPYEOF

echo "Writing hamclock_tkinter.py..."
sudo tee "$INSTALL_DIR/hamclock_tkinter.py" > /dev/null << 'HCTKEOF'
"""HamClock Lite native Tkinter client.

A minimal-dependency native GUI that replaces the browser-based HamClock Lite
dashboard on Raspberry Pi 1 Model B (700 MHz ARMv6, 512 MB RAM). Fetches data
from the existing HamClock server at http://localhost:8080/api/* via the
shared hamclock_data.HamClockData class and renders the dashboard using
native Tkinter widgets, saving significant RAM/CPU vs. a browser stack.

Apt dependencies (Raspberry Pi OS):
    sudo apt install python3-tk python3-pil python3-pil.imagetk

Tkinter's built-in PhotoImage handles GIF/PGM/PNG but NOT JPEG, so Pillow
(PIL) is used for image decoding. If Pillow is unavailable, the image panels
are hidden gracefully and the rest of the dashboard still works.

Usage:
    python3 hamclock_tkinter.py

Press Escape to exit fullscreen.

Target viewport: 1440x900 fullscreen (scales gracefully on smaller screens).
"""

import io
import time
import tkinter as tk
from tkinter import ttk

from hamclock_data import HamClockData

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:  # Pillow missing — degrade image panels gracefully
    HAS_PIL = False


# ---------- Theme (K-State royal purple + gold) ----------
BG = '#2a1450'
CARD = '#3a1d65'
BORDER = '#512888'
TEXT = '#e8ddf5'
LABEL = '#b8a0d8'
BRIGHT = '#ffffff'
ACCENT_GOLD = '#f4c55c'

COND_COLORS = {
    'Good': '#22c55e',
    'Fair': '#eab308',
    'Poor': '#ef4444',
    'N/A': '#4a5568',
}

BAND_COLORS = {
    '160m': '#ff6b6b', '80m': '#f06595', '60m': '#cc5de8', '40m': '#845ef7',
    '30m': '#5c7cfa', '20m': '#339af0', '17m': '#22b8cf', '15m': '#20c997',
    '12m': '#51cf66', '10m': '#94d82d',
}
BAND_ORDER = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']

# Fonts — DejaVu Sans Mono is standard on Raspberry Pi OS.
FONT_TITLE = ('DejaVu Sans Mono', 12, 'bold')
FONT_BODY = ('DejaVu Sans Mono', 11)
FONT_VALUE = ('DejaVu Sans Mono', 11, 'bold')
FONT_LABEL = ('DejaVu Sans Mono', 9)
FONT_HEADER = ('DejaVu Sans Mono', 18, 'bold')
FONT_CLOCK = ('DejaVu Sans Mono', 13, 'bold')


def _safe(v, default='—'):
    """Return str(v) or placeholder if v is empty/None/'N/A'."""
    if v is None:
        return default
    s = str(v).strip()
    if not s or s.upper() == 'N/A':
        return default
    return s


def _make_panel(parent, title):
    """Create a titled card Frame; return (outer, body) where body holds content."""
    outer = tk.Frame(
        parent, bg=CARD, bd=1, relief='solid',
        highlightbackground=BORDER, highlightthickness=1,
    )
    header = tk.Label(
        outer, text=title, bg=BORDER, fg=ACCENT_GOLD,
        font=FONT_TITLE, anchor='w', padx=8, pady=3,
    )
    header.pack(side='top', fill='x')
    body = tk.Frame(outer, bg=CARD, padx=8, pady=6)
    body.pack(side='top', fill='both', expand=True)
    return outer, body


def _kv_row(body, row, label, initial='—'):
    """Place a label/value pair in a 2-column grid row. Returns the value Label."""
    tk.Label(
        body, text=label, bg=CARD, fg=LABEL, font=FONT_LABEL,
        anchor='w',
    ).grid(row=row, column=0, sticky='w', padx=(0, 6))
    val = tk.Label(
        body, text=initial, bg=CARD, fg=BRIGHT, font=FONT_VALUE,
        anchor='e',
    )
    val.grid(row=row, column=1, sticky='e')
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=0)
    return val


class HamClockTkApp:
    """Native Tkinter HamClock Lite dashboard."""

    def __init__(self, root):
        self.root = root
        self.data = HamClockData()
        self.data.start_background()

        root.configure(bg=BG)
        root.title('HamClock Lite')
        root.geometry('1440x900')
        try:
            root.attributes('-fullscreen', True)
        except Exception:
            pass
        root.bind('<Escape>', lambda _e: root.destroy())
        root.bind('<F11>', self._toggle_fullscreen)

        # ttk theme for Treeview / Notebook
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure(
            'HC.Treeview',
            background=CARD, foreground=TEXT, fieldbackground=CARD,
            rowheight=18, borderwidth=0, font=FONT_LABEL,
        )
        style.configure(
            'HC.Treeview.Heading',
            background=BORDER, foreground=ACCENT_GOLD, font=FONT_LABEL,
        )
        style.map('HC.Treeview', background=[('selected', BORDER)])
        style.configure('HC.TNotebook', background=CARD, borderwidth=0)
        style.configure(
            'HC.TNotebook.Tab',
            background=CARD, foreground=LABEL,
            padding=[8, 3], font=FONT_LABEL,
        )
        style.map(
            'HC.TNotebook.Tab',
            background=[('selected', BORDER)],
            foreground=[('selected', ACCENT_GOLD)],
        )

        self._value_labels = {}
        self._last_image_ts = {}  # Tier 1.2: per-image-key fetch stamps
        self._image_refs = {}  # hold refs to prevent GC

        self._build_ui()
        self._update_ui()

    def _toggle_fullscreen(self, _e=None):
        try:
            cur = bool(self.root.attributes('-fullscreen'))
            self.root.attributes('-fullscreen', not cur)
        except Exception:
            pass

    # ----- UI construction -----
    def _build_ui(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        for c in range(3):
            self.root.grid_columnconfigure(c, weight=1, uniform='col')

        # --- Header bar ---
        header = tk.Frame(self.root, bg=BORDER, bd=0)
        header.grid(row=0, column=0, columnspan=3, sticky='ew', padx=4, pady=(4, 2))
        tk.Label(
            header, text='HAMCLOCK LITE', bg=BORDER, fg=ACCENT_GOLD,
            font=FONT_HEADER, padx=10, pady=6,
        ).pack(side='left')
        tk.Label(
            header, text='W0QQQ', bg=BORDER, fg=TEXT, font=FONT_BODY,
        ).pack(side='left', padx=(4, 10))
        self.status_dot = tk.Label(
            header, text='\u25cf', bg=BORDER, fg='#ef4444',
            font=FONT_HEADER,
        )
        self.status_dot.pack(side='right', padx=8)
        self.local_lbl = tk.Label(
            header, text='LOCAL --:--:--', bg=BORDER, fg=TEXT, font=FONT_CLOCK,
        )
        self.local_lbl.pack(side='right', padx=10)
        self.utc_lbl = tk.Label(
            header, text='UTC --:--:--', bg=BORDER, fg=BRIGHT, font=FONT_CLOCK,
        )
        self.utc_lbl.pack(side='right', padx=10)

        # --- Columns ---
        col_left = tk.Frame(self.root, bg=BG)
        col_mid = tk.Frame(self.root, bg=BG)
        col_right = tk.Frame(self.root, bg=BG)
        col_left.grid(row=1, column=0, sticky='nsew', padx=4, pady=2)
        col_mid.grid(row=1, column=1, sticky='nsew', padx=4, pady=2)
        col_right.grid(row=1, column=2, sticky='nsew', padx=4, pady=2)

        self._build_left_column(col_left)
        self._build_middle_column(col_mid)
        self._build_right_column(col_right)

        # --- Status bar ---
        self.status_bar = tk.Label(
            self.root, text='Solar:— Bands:— DX:—',
            bg=BORDER, fg=LABEL, font=FONT_LABEL, anchor='w', padx=8, pady=2,
        )
        self.status_bar.grid(row=2, column=0, columnspan=3, sticky='ew', padx=4, pady=(2, 4))

    def _build_left_column(self, col):
        # SOLAR
        solar_p, solar_b = _make_panel(col, 'SOLAR')
        solar_p.pack(fill='x', pady=(0, 4))
        for i, (k, lbl) in enumerate([
            ('sfi', 'SFI'), ('ssn', 'SSN'), ('aIndex', 'A-Index'),
            ('kIndex', 'K-Index'), ('xray', 'X-Ray'), ('solarWind', 'Solar Wind'),
            ('protonFlux', 'Proton Flux'), ('aurora', 'Aurora'),
        ]):
            self._value_labels['solar_' + k] = _kv_row(solar_b, i, lbl)

        # BANDS
        bands_p, bands_b = _make_panel(col, 'BANDS')
        bands_p.pack(fill='x', pady=4)
        tk.Label(bands_b, text='BAND', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='w').grid(row=0, column=0, sticky='w', padx=(0, 8))
        tk.Label(bands_b, text='DAY', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='center').grid(row=0, column=1, sticky='ew', padx=4)
        tk.Label(bands_b, text='NIGHT', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='center').grid(row=0, column=2, sticky='ew', padx=4)
        bands_b.grid_columnconfigure(0, weight=1)
        bands_b.grid_columnconfigure(1, weight=0, minsize=60)
        bands_b.grid_columnconfigure(2, weight=0, minsize=60)
        self._band_rows = {}
        for i, band in enumerate(['80m-40m', '30m-20m', '17m-15m', '12m-10m'], start=1):
            tk.Label(bands_b, text=band, bg=CARD, fg=TEXT, font=FONT_BODY,
                     anchor='w').grid(row=i, column=0, sticky='w', padx=(0, 8), pady=1)
            day = tk.Label(bands_b, text='—', bg=COND_COLORS['N/A'], fg=BRIGHT,
                           font=FONT_LABEL, width=7)
            day.grid(row=i, column=1, sticky='ew', padx=2, pady=1)
            night = tk.Label(bands_b, text='—', bg=COND_COLORS['N/A'], fg=BRIGHT,
                             font=FONT_LABEL, width=7)
            night.grid(row=i, column=2, sticky='ew', padx=2, pady=1)
            self._band_rows[band] = (day, night)

        # SDO IMAGE
        sdo_p, sdo_b = _make_panel(col, 'SDO IMAGE')
        sdo_p.pack(fill='x', pady=4)
        self.sdo_label = tk.Label(
            sdo_b, text='(image unavailable)' if not HAS_PIL else '(loading...)',
            bg=CARD, fg=LABEL, font=FONT_LABEL,
        )
        self.sdo_label.pack()

        # GEOMAGNETIC (Kp bar)
        geo_p, geo_b = _make_panel(col, 'GEOMAGNETIC')
        geo_p.pack(fill='x', pady=4)
        self.kp_value = tk.Label(geo_b, text='Kp —', bg=CARD, fg=BRIGHT,
                                 font=FONT_VALUE)
        self.kp_value.pack(anchor='w')
        self.kp_canvas = tk.Canvas(geo_b, height=14, bg=CARD, bd=0,
                                   highlightthickness=0)
        self.kp_canvas.pack(fill='x', pady=(2, 0))

        # X-RAY bar
        xray_p, xray_b = _make_panel(col, 'X-RAY')
        xray_p.pack(fill='x', pady=4)
        self.xray_value = tk.Label(xray_b, text='—', bg=CARD, fg=BRIGHT,
                                   font=FONT_VALUE)
        self.xray_value.pack(anchor='w')
        self.xray_canvas = tk.Canvas(xray_b, height=14, bg=CARD, bd=0,
                                     highlightthickness=0)
        self.xray_canvas.pack(fill='x', pady=(2, 0))

        # OPEN BANDS
        open_p, open_b = _make_panel(col, 'OPEN BANDS')
        open_p.pack(fill='x', pady=(4, 0))
        self.open_lbl = tk.Label(
            open_b, text='OPEN: —', bg=CARD, fg='#22c55e', font=FONT_BODY,
            anchor='w', justify='left', wraplength=360,
        )
        self.open_lbl.pack(anchor='w', fill='x')
        self.closed_lbl = tk.Label(
            open_b, text='CLOSED: —', bg=CARD, fg='#ef4444', font=FONT_BODY,
            anchor='w', justify='left', wraplength=360,
        )
        self.closed_lbl.pack(anchor='w', fill='x')

    def _build_middle_column(self, col):
        muf_p, muf_b = _make_panel(col, 'MUF STATUS')
        muf_p.pack(fill='x', pady=(0, 4))
        for i, (k, lbl) in enumerate([
            ('fof2', 'foF2 (MHz)'),
            ('geomagField', 'Geomag Field'),
            ('kIndex', 'K-Index'),
            ('sfi', 'SFI'),
            ('ssn', 'SSN'),
            ('heliumLine', 'Helium Line'),
            ('signalNoise', 'Signal/Noise'),
            ('magneticField', 'Magnetic Field'),
        ]):
            self._value_labels['muf_' + k] = _kv_row(muf_b, i, lbl)

        # Info / update panel
        info_p, info_b = _make_panel(col, 'STATION')
        info_p.pack(fill='both', expand=True, pady=4)
        self.updated_lbl = tk.Label(
            info_b, text='Updated: —', bg=CARD, fg=LABEL, font=FONT_LABEL,
            anchor='w', justify='left', wraplength=360,
        )
        self.updated_lbl.pack(anchor='w', fill='x', pady=(0, 4))
        self.server_lbl = tk.Label(
            info_b, text='Server: ' + self.data.server_url, bg=CARD, fg=LABEL,
            font=FONT_LABEL, anchor='w',
        )
        self.server_lbl.pack(anchor='w', fill='x')
        self.errors_lbl = tk.Label(
            info_b, text='', bg=CARD, fg='#ef4444', font=FONT_LABEL,
            anchor='nw', justify='left', wraplength=360,
        )
        self.errors_lbl.pack(anchor='w', fill='x', pady=(6, 0))

    def _build_right_column(self, col):
        # DX SPOTS (Treeview)
        dx_p, dx_b = _make_panel(col, 'DX SPOTS')
        dx_p.pack(fill='x', pady=(0, 4))
        cols = ('freq', 'band', 'dx', 'de', 'utc')
        self.dx_tree = ttk.Treeview(
            dx_b, columns=cols, show='headings', height=8, style='HC.Treeview',
        )
        widths = {'freq': 70, 'band': 50, 'dx': 90, 'de': 90, 'utc': 50}
        for c in cols:
            self.dx_tree.heading(c, text=c.upper())
            self.dx_tree.column(c, width=widths[c], anchor='w', stretch=True)
        self.dx_tree.pack(fill='both', expand=True)

        # BAND ACTIVITY — Canvas bars
        act_p, act_b = _make_panel(col, 'BAND ACTIVITY')
        act_p.pack(fill='x', pady=4)
        self.activity_canvas = tk.Canvas(
            act_b, height=180, bg=CARD, bd=0, highlightthickness=0,
        )
        self.activity_canvas.pack(fill='x')

        # PROPAGATION — ttk.Notebook with tabs for DRAP/AURORA/ENLIL
        prop_p, prop_b = _make_panel(col, 'PROPAGATION')
        prop_p.pack(fill='both', expand=True, pady=(4, 0))
        self.prop_nb = ttk.Notebook(prop_b, style='HC.TNotebook')
        self.prop_nb.pack(fill='both', expand=True)
        self.prop_tabs = {}
        for key, title in [('real-drap', 'DRAP'), ('drap', 'AURORA'),
                           ('enlil', 'ENLIL')]:
            frame = tk.Frame(self.prop_nb, bg=CARD)
            lbl = tk.Label(
                frame, text='(loading...)' if HAS_PIL else '(PIL missing)',
                bg=CARD, fg=LABEL, font=FONT_LABEL,
            )
            lbl.pack(expand=True)
            self.prop_nb.add(frame, text=title)
            self.prop_tabs[key] = lbl

    # ----- Image helpers -----
    def _load_image(self, data_bytes, max_w, max_h):
        if not data_bytes or not HAS_PIL:
            return None
        try:
            img = Image.open(io.BytesIO(data_bytes))
            img.thumbnail((max_w, max_h), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _set_image(self, label, key, photo):
        """Assign photo to label; hold ref to prevent GC."""
        if photo is None:
            return
        self._image_refs[key] = photo
        label.configure(image=photo, text='')
        label.image_ref = photo  # belt and suspenders

    # ----- Update loop -----
    def _update_ui(self):
        try:
            self._update_clocks()
            self._update_solar()
            self._update_muf()
            self._update_bands()
            self._update_dxspots()
            self._update_band_activity()
            self._update_open_closed()
            self._update_images()
            self._update_status()
        except Exception as e:
            try:
                self.status_bar.configure(text='update error: {}'.format(e))
            except Exception:
                pass
        # Reschedule. Guard against the root being destroyed (e.g. Escape
        # pressed mid-tick) — an uncaught TclError here would crash the client.
        try:
            self.root.after(1000, self._update_ui)
        except Exception:
            pass

    def _update_clocks(self):
        now = time.time()
        self.utc_lbl.configure(text='UTC ' + time.strftime('%H:%M:%S', time.gmtime(now)))
        self.local_lbl.configure(text='LOCAL ' + time.strftime('%H:%M:%S', time.localtime(now)))
        ok = bool(self.data.last_data_refresh) and (now - self.data.last_data_refresh) < 180
        self.status_dot.configure(fg='#22c55e' if ok else '#ef4444')

    def _update_solar(self):
        s = self.data.solar or {}
        for key in ['sfi', 'ssn', 'aIndex', 'kIndex', 'xray', 'solarWind',
                    'protonFlux', 'aurora']:
            self._value_labels['solar_' + key].configure(text=_safe(s.get(key)))

        # Kp bar (0-9 scale)
        kp_raw = s.get('kIndex')
        try:
            kp = float(kp_raw)
        except (TypeError, ValueError):
            kp = None
        self.kp_value.configure(text='Kp ' + (_safe(kp_raw)))
        self._draw_bar(self.kp_canvas, kp, 9.0,
                       ['#22c55e', '#22c55e', '#22c55e', '#22c55e',
                        '#eab308', '#eab308', '#ef4444', '#ef4444',
                        '#ef4444', '#ef4444'])

        # X-Ray bar
        xray_raw = s.get('xray') or ''
        self.xray_value.configure(text=_safe(xray_raw))
        xv = self._xray_to_scalar(xray_raw)
        self._draw_bar(self.xray_canvas, xv, 5.0,
                       ['#22c55e', '#84cc16', '#eab308', '#f97316', '#ef4444'])

    def _xray_to_scalar(self, xray):
        """Convert NOAA xray class (e.g. 'B4.0', 'M1.5', 'X2.0') to 0..5 scalar."""
        if not xray or len(xray) < 2:
            return None
        cls = xray[0].upper()
        try:
            mag = float(xray[1:])
        except ValueError:
            mag = 1.0
        # Normalize within class: 1-9 → 0..1
        frac = max(0.0, min(1.0, (mag - 1.0) / 8.0))
        base = {'A': 0, 'B': 1, 'C': 2, 'M': 3, 'X': 4}.get(cls, 0)
        return base + frac

    def _draw_bar(self, canvas, value, max_val, gradient_colors):
        canvas.delete('all')
        w = int(canvas.winfo_width()) or 360
        h = int(canvas.winfo_height()) or 14
        canvas.create_rectangle(0, 0, w, h, fill='#1a0a30', outline=BORDER)
        if value is None or max_val <= 0:
            return
        frac = max(0.0, min(1.0, value / max_val))
        fill_w = int(w * frac)
        if fill_w < 1:
            return
        idx = min(len(gradient_colors) - 1, int(frac * len(gradient_colors)))
        canvas.create_rectangle(0, 0, fill_w, h,
                                fill=gradient_colors[idx], outline='')

    def _update_muf(self):
        s = self.data.solar or {}
        for key in ['fof2', 'geomagField', 'kIndex', 'sfi', 'ssn',
                    'heliumLine', 'signalNoise', 'magneticField']:
            self._value_labels['muf_' + key].configure(text=_safe(s.get(key)))
        self.updated_lbl.configure(text='Updated: ' + _safe(s.get('updated')))

    def _update_bands(self):
        b = self.data.bands or {}
        for band, (day_lbl, night_lbl) in self._band_rows.items():
            entry = b.get(band) or {}
            day = entry.get('day') or 'N/A'
            night = entry.get('night') or 'N/A'
            day_lbl.configure(text=day, bg=COND_COLORS.get(day, COND_COLORS['N/A']))
            night_lbl.configure(text=night, bg=COND_COLORS.get(night, COND_COLORS['N/A']))

    def _update_dxspots(self):
        spots = self.data.dxspots or []
        existing = self.dx_tree.get_children()
        if len(existing) != min(len(spots), 12):
            self.dx_tree.delete(*existing)
            existing = ()
        rows = spots[:12]
        if not existing:
            for sp in rows:
                utc = (sp.get('time') or '')[:4]
                self.dx_tree.insert('', 'end', values=(
                    _safe(sp.get('frequency')),
                    _safe(sp.get('band')),
                    _safe(sp.get('dx')),
                    _safe(sp.get('spotter')),
                    utc,
                ))
        else:
            for iid, sp in zip(existing, rows):
                utc = (sp.get('time') or '')[:4]
                self.dx_tree.item(iid, values=(
                    _safe(sp.get('frequency')),
                    _safe(sp.get('band')),
                    _safe(sp.get('dx')),
                    _safe(sp.get('spotter')),
                    utc,
                ))

    def _update_band_activity(self):
        canvas = self.activity_canvas
        canvas.delete('all')
        spots = self.data.dxspots or []
        counts = {}
        for sp in spots:
            band = sp.get('band')
            if band in BAND_COLORS:
                counts[band] = counts.get(band, 0) + 1
        max_count = max(counts.values()) if counts else 1

        w = int(canvas.winfo_width()) or 380
        h = int(canvas.winfo_height()) or 180
        rows = len(BAND_ORDER)
        row_h = max(12, h // rows)
        label_w = 44
        bar_x0 = label_w + 4
        bar_max = max(40, w - bar_x0 - 40)
        for i, band in enumerate(BAND_ORDER):
            y = i * row_h + 2
            canvas.create_text(
                4, y + row_h / 2 - 2, text=band, anchor='w',
                fill=LABEL, font=FONT_LABEL,
            )
            count = counts.get(band, 0)
            frac = count / max_count if max_count else 0
            bar_w = int(bar_max * frac)
            if bar_w > 0:
                canvas.create_rectangle(
                    bar_x0, y, bar_x0 + bar_w, y + row_h - 4,
                    fill=BAND_COLORS[band], outline='',
                )
            canvas.create_text(
                bar_x0 + bar_w + 4, y + row_h / 2 - 2,
                text=str(count), anchor='w', fill=TEXT, font=FONT_LABEL,
            )

    def _update_open_closed(self):
        b = self.data.bands or {}
        open_list = []
        closed_list = []
        for band, entry in b.items():
            if not isinstance(entry, dict):
                continue
            day = entry.get('day') or 'N/A'
            night = entry.get('night') or 'N/A'
            if day == 'Good' or night == 'Good':
                open_list.append(band)
            elif day == 'Poor' and night == 'Poor':
                closed_list.append(band)
        self.open_lbl.configure(
            text='OPEN: ' + (', '.join(open_list) if open_list else '—'),
        )
        self.closed_lbl.configure(
            text='CLOSED: ' + (', '.join(closed_list) if closed_list else '—'),
        )

    def _image_stamp(self, key):
        """Per-key fetch timestamp, falling back to the global refresh tick.

        The getattr/isinstance guard is load-bearing: image_fetched_at arrived
        with Tier 1a and older HamClockData copies (notably the one embedded
        in the installers) do not have it at all.
        """
        fa = getattr(self.data, 'image_fetched_at', None)
        if isinstance(fa, dict):
            return fa.get(key, self.data.last_image_refresh)
        return self.data.last_image_refresh

    def _update_images(self):
        # Tier 1.2: gate each image on ITS OWN fetch timestamp. The single
        # last_image_refresh gate re-decoded and re-thumbnailed all five
        # payloads whenever any one of them arrived (PIL LANCZOS resize is the
        # expensive part), and a refresh cycle that fetched nothing at all
        # still bumped the stamp. The per-key stamp is recorded whether or not
        # the decode succeeds, so an undecodable payload costs one attempt per
        # refresh rather than one per UI tick.
        imgs = self.data.images or {}
        seen = self._last_image_ts

        ts = self._image_stamp('solar-image')
        if seen.get('solar-image') != ts:
            seen['solar-image'] = ts
            sdo = self._load_image(imgs.get('solar-image'), 360, 220)
            if sdo is not None:
                self._set_image(self.sdo_label, 'sdo', sdo)
            elif not HAS_PIL:
                self.sdo_label.configure(text='(PIL missing)')

        for key, label in self.prop_tabs.items():
            ts = self._image_stamp(key)
            if seen.get(key) == ts:
                continue
            seen[key] = ts
            photo = self._load_image(imgs.get(key), 380, 260)
            if photo is not None:
                self._set_image(label, 'prop_' + key, photo)
            elif not HAS_PIL:
                label.configure(text='(PIL missing)')
            else:
                label.configure(text='(no image)')

    def _update_status(self):
        now = time.time()
        d_age = int(now - self.data.last_data_refresh) if self.data.last_data_refresh else -1
        i_age = int(now - self.data.last_image_refresh) if self.data.last_image_refresh else -1
        def fmt(a):
            return '{}s'.format(a) if a >= 0 else '—'
        errs = [k for k, v in (self.data.errors or {}).items() if v]
        status = 'Data:{}  Images:{}  Spots:{}  Errors:{}'.format(
            fmt(d_age), fmt(i_age),
            len(self.data.dxspots or []), len(errs),
        )
        self.status_bar.configure(text=status)
        if errs:
            self.errors_lbl.configure(text='Errors: ' + ', '.join(errs[:3]))
        else:
            self.errors_lbl.configure(text='')


def main():
    root = tk.Tk()
    HamClockTkApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
HCTKEOF

# ── Step 4a: Root-executed helper scripts + the version stamp ───────
#
# hamclock-update.sh and hamclock-apply-settings.sh are run BY ROOT, started by
# the systemd units written near the end of this script. The dashboard runs as
# an unprivileged SERVICE_USER and is granted no sudo whatsoever; the single
# privileged thing it can do is create an empty flag file under
# /run/hamclock-lite, which a root-owned path unit notices. That boundary holds
# only while these two scripts are root-owned and NOT writable by SERVICE_USER
# — a service user who can edit a script that root executes already has root.
#
# So the ownership is asserted after the write rather than inherited from
# whatever umask is in effect, or from whatever ownership $INSTALL_DIR happens
# to carry on a Pi that has been hand-fixed at some point in its life.
assert_root_owned() {
    # assert_root_owned <file> <expected-octal-mode>
    local f="$1" want="$2" owner mode
    owner="$(stat -c '%U:%G' "$f" 2>/dev/null || echo '?')"
    mode="$(stat -c '%a' "$f" 2>/dev/null || echo '?')"
    if [ "$owner" != "root:root" ] || [ "$mode" != "$want" ]; then
        echo "FATAL: $f is owned by $owner with mode $mode; expected root:root $want." >&2
        echo "Refusing to continue: the unprivileged dashboard user must never be" >&2
        echo "able to modify a file that systemd executes as root." >&2
        exit 1
    fi
}

echo "Writing root helper scripts..."
# Delimiters are deliberately not EOF/PY: hamclock-update.sh contains its own
# `<<EOF` status heredoc and hamclock-apply-settings.sh contains a `<<'PY'`
# block, either of which would terminate this one early and pipe the rest of
# the file into the shell. Quoted so nothing inside expands.
sudo tee "$INSTALL_DIR/hamclock-update.sh" > /dev/null << 'HCUPDATESH'
#!/bin/bash
# HamClock Lite updater — runs as root, triggered by an unprivileged request.
#
# SECURITY MODEL
# --------------
# The dashboard server runs as SERVICE_USER (non-root). Granting it sudo to
# install software would hand a network-facing process a route to root, so it
# gets none. Instead it writes an empty flag file, and a root-owned systemd
# path unit starts this script.
#
# The critical property: the unprivileged side can only say "please update".
# It cannot say WHAT to install. This script does its own fetch, from a URL
# compiled in below, and refuses to execute anything whose SHA-256 does not
# match the signed-in-place manifest. A compromised or merely buggy dashboard
# process therefore cannot cause arbitrary code to run as root.
#
# This file must be root:root and NOT writable by SERVICE_USER — if the service
# user can edit the script that root executes, the whole model collapses.
# install_updater() in the installer enforces 0755 root:root.
#
# Exit codes: 0 ok / 1 usage / 2 network / 3 integrity / 4 apply failed
#             (rolled back) / 5 apply failed AND rollback failed
set -o pipefail

BASE_URL="https://hamclock-reborn.org"
MANIFEST_URL="$BASE_URL/downloads/pi1-install.version.json"
INSTALLER_URL="$BASE_URL/downloads/pi1-install.sh"

INSTALL_DIR="/opt/hamclock-lite"
RUN_DIR="/run/hamclock-lite"
STATE_DIR="/var/lib/hamclock-lite"
STATUS_FILE="$RUN_DIR/update-status.json"
REQUEST_FILE="$RUN_DIR/update.request"
BACKUP_DIR="$STATE_DIR/backup"
LOG_TAG="hamclock-update"

log() { echo "[$(date -u +%H:%M:%S)] $*"; logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; }

# Status is written to tmpfs so the dashboard can read it without any shared
# privilege. Never put anything secret in here.
write_status() {
    mkdir -p "$RUN_DIR" 2>/dev/null || true
    cat > "$STATUS_FILE.tmp" <<EOF
{"state":"$1","installed":"$2","available":"$3","detail":"$4","checked":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
    mv -f "$STATUS_FILE.tmp" "$STATUS_FILE" 2>/dev/null || true
    chmod 0644 "$STATUS_FILE" 2>/dev/null || true
}

installed_version() {
    # The single-file installer has no git, which is the entire reason it
    # exists, so never ask git. Read what the install stamped.
    if [ -r "$INSTALL_DIR/VERSION" ]; then
        sanitize_version "$(tr -d ' \t\r\n' < "$INSTALL_DIR/VERSION")"
    else
        echo "0.0.0"
    fi
}

# Pure-shell semver compare. Returns 0 when $1 is strictly newer than $2.
# sort -V would be shorter but is not guaranteed on a minimal Pi OS image.
version_gt() {
    local a b i
    IFS='.' read -r -a a <<< "${1%%-*}"
    IFS='.' read -r -a b <<< "${2%%-*}"
    for i in 0 1 2; do
        local x=${a[$i]:-0} y=${b[$i]:-0}
        # Strip anything non-numeric so a malformed field cannot inject.
        x=${x//[^0-9]/}; y=${y//[^0-9]/}
        x=${x:-0}; y=${y:-0}
        if [ "$x" -gt "$y" ] 2>/dev/null; then return 0; fi
        if [ "$x" -lt "$y" ] 2>/dev/null; then return 1; fi
    done
    return 1
}

json_field() {
    # Extract one string field without needing python/jq on the target.
    sed -n 's/.*"'"$2"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' <<< "$1" | head -1
}

# Everything below comes off the network. Even though the manifest is served
# from our own site over TLS, it is parsed by a hand-rolled sed and then
# interpolated into the JSON status file, so a stray quote would corrupt that
# file and a stray shell metacharacter has no business being here at all.
# Whitelist rather than escape.
sanitize_version() {
    local v="${1//[^0-9A-Za-z.+-]/}"
    printf '%s' "${v:0:32}"
}

# A digest that is not exactly 64 hex characters is not a digest, and treating
# it as one would let a malformed manifest slip past the integrity gate.
sanitize_sha256() {
    local s="${1,,}"
    s="${s//[^0-9a-f]/}"
    [ ${#s} -eq 64 ] || return 1
    printf '%s' "$s"
}

fetch_manifest() {
    curl -fsS --max-time 20 --proto '=https' --tlsv1.2 "$MANIFEST_URL" 2>/dev/null
}

do_check() {
    local cur mani avail
    cur=$(installed_version)
    mani=$(fetch_manifest) || {
        log "check: manifest unreachable"
        write_status "error" "$cur" "" "cannot reach $BASE_URL"
        return 2
    }
    avail=$(sanitize_version "$(json_field "$mani" version)")
    if [ -z "$avail" ]; then
        log "check: manifest has no version field"
        write_status "error" "$cur" "" "malformed manifest"
        return 3
    fi
    if version_gt "$avail" "$cur"; then
        log "check: update available $cur -> $avail"
        write_status "available" "$cur" "$avail" ""
    else
        log "check: up to date ($cur)"
        write_status "current" "$cur" "$avail" ""
    fi
    return 0
}

health_ok() {
    # An update that leaves the API dead is a failed update, no matter what
    # the installer's exit code said.
    local i
    for i in $(seq 1 30); do
        if curl -fsS --max-time 3 http://localhost:8080/api/health >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

do_apply() {
    local cur mani avail want_sha tmp got_sha
    cur=$(installed_version)

    mani=$(fetch_manifest) || { write_status "error" "$cur" "" "cannot reach $BASE_URL"; return 2; }
    avail=$(sanitize_version "$(json_field "$mani" version)")
    want_sha=$(sanitize_sha256 "$(json_field "$mani" sha256)") || want_sha=""
    [ -n "$avail" ] || { write_status "error" "$cur" "" "malformed manifest"; return 3; }

    if ! version_gt "$avail" "$cur"; then
        log "apply: already at $cur, nothing to do"
        write_status "current" "$cur" "$avail" ""
        return 0
    fi

    # An unverifiable download is not installed. Refusing here is the whole
    # point of the manifest; without the digest this is just curl | bash.
    if [ -z "$want_sha" ]; then
        log "apply: REFUSING — manifest carries no sha256"
        write_status "error" "$cur" "$avail" "manifest has no sha256; refusing to install"
        return 3
    fi

    write_status "applying" "$cur" "$avail" "downloading"
    tmp=$(mktemp /tmp/hamclock-install.XXXXXX.sh) || return 2
    trap 'rm -f "$tmp"' RETURN

    if ! curl -fsS --max-time 180 --proto '=https' --tlsv1.2 -o "$tmp" "$INSTALLER_URL"; then
        log "apply: download failed"
        write_status "error" "$cur" "$avail" "download failed"
        return 2
    fi

    got_sha=$(sha256sum "$tmp" | awk '{print $1}')
    if [ "$got_sha" != "$want_sha" ]; then
        log "apply: REFUSING — sha256 mismatch (want $want_sha got $got_sha)"
        write_status "error" "$cur" "$avail" "integrity check failed; nothing installed"
        return 3
    fi
    log "apply: integrity verified ($got_sha)"

    # Back up what we are about to overwrite. A headless kiosk with a broken
    # display and no keyboard is not somewhere you want to debug an update.
    rm -rf "$BACKUP_DIR.prev" 2>/dev/null
    [ -d "$BACKUP_DIR" ] && mv "$BACKUP_DIR" "$BACKUP_DIR.prev"
    mkdir -p "$BACKUP_DIR"
    cp -a "$INSTALL_DIR/." "$BACKUP_DIR/" 2>/dev/null || {
        log "apply: backup failed; refusing to continue without a rollback path"
        write_status "error" "$cur" "$avail" "backup failed; nothing installed"
        return 4
    }

    write_status "applying" "$cur" "$avail" "installing"
    log "apply: running installer $cur -> $avail"
    if bash "$tmp" >>/var/log/hamclock-update.log 2>&1 && health_ok; then
        log "apply: success, now $(installed_version)"
        write_status "updated" "$(installed_version)" "$avail" "restart complete"
        maybe_reboot
        return 0
    fi

    # Roll back. Restore files first, then prove the service actually recovers.
    log "apply: FAILED — rolling back to $cur"
    write_status "rollback" "$cur" "$avail" "update failed; restoring previous version"
    rm -rf "$INSTALL_DIR.failed" 2>/dev/null
    mv "$INSTALL_DIR" "$INSTALL_DIR.failed" 2>/dev/null
    mkdir -p "$INSTALL_DIR"
    if cp -a "$BACKUP_DIR/." "$INSTALL_DIR/" 2>/dev/null; then
        systemctl restart hamclock-lite 2>/dev/null
        if health_ok; then
            log "rollback: restored $cur and service is healthy"
            write_status "rolled_back" "$cur" "$avail" "update failed; previous version restored"
            return 4
        fi
    fi
    log "rollback: FAILED — manual recovery needed; failed tree kept at $INSTALL_DIR.failed"
    write_status "broken" "$cur" "$avail" "update and rollback both failed; see /var/log/hamclock-update.log"
    return 5
}

maybe_reboot() {
    # Almost nothing this project ships needs a reboot — restarting the two
    # units is enough, and the installer already does that. Rebooting a kiosk
    # is disruptive and risks not coming back, so it happens only when
    # something outside our services genuinely changed.
    local need=0
    [ -f /var/run/reboot-required ] && need=1
    if [ -f "$STATE_DIR/boot-config.sha" ]; then
        local now
        now=$(cat /boot/config.txt /boot/cmdline.txt 2>/dev/null | sha256sum | awk '{print $1}')
        [ "$now" != "$(cat "$STATE_DIR/boot-config.sha")" ] && need=1
    fi
    if [ "$need" = "1" ]; then
        log "reboot: boot configuration changed; rebooting in 10s"
        write_status "rebooting" "$(installed_version)" "" "boot config changed"
        ( sleep 10; systemctl reboot ) &
    else
        log "reboot: not required; services restarted in place"
    fi
}

record_boot_config() {
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    cat /boot/config.txt /boot/cmdline.txt 2>/dev/null \
        | sha256sum | awk '{print $1}' > "$STATE_DIR/boot-config.sha" 2>/dev/null || true
}

case "${1:-}" in
    --check)
        record_boot_config
        do_check
        ;;
    --apply)
        rm -f "$REQUEST_FILE" 2>/dev/null    # consume the trigger first, so a
                                             # failure cannot spin the path unit
        record_boot_config
        do_apply
        ;;
    *)
        echo "usage: $0 --check | --apply" >&2
        exit 1
        ;;
esac
HCUPDATESH
sudo tee "$INSTALL_DIR/hamclock-apply-settings.sh" > /dev/null << 'HCSETTINGSSH'
#!/bin/bash
# Apply the parts of settings.json that need root. Runs as root, triggered by
# an unprivileged request, exactly like hamclock-update.sh.
#
# Right now that means the NTP server. The on-device setup wizard runs as
# SERVICE_USER and can write /etc/hamclock-lite/settings.json, but the thing
# that actually changes the clock source is
# /etc/systemd/timesyncd.conf.d/hamclock.conf, which is root-owned. Rather than
# grant the wizard sudo, it saves its choice and asks for it to be applied.
#
# SECURITY: settings.json is writable by an unprivileged user, and this script
# takes a value out of it and puts it into a systemd unit config as root. That
# is a privilege boundary, so the value is validated against a strict whitelist
# HERE, before it is trusted — never on the assumption that the wizard already
# checked. A hostile settings.json must not be able to inject directives.
#
# Exit codes: 0 applied / 1 usage / 2 no/!readable settings / 3 invalid value
set -o pipefail

SETTINGS="/etc/hamclock-lite/settings.json"
CONF="/etc/systemd/timesyncd.conf.d/hamclock.conf"
RUN_DIR="/run/hamclock-lite"
REQUEST_FILE="$RUN_DIR/settings.request"
STATUS_FILE="$RUN_DIR/settings-status.json"
LOG_TAG="hamclock-settings"

log() { echo "[$(date -u +%H:%M:%S)] $*"; logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; }

write_status() {
    mkdir -p "$RUN_DIR" 2>/dev/null || true
    printf '{"state":"%s","ntp":"%s","detail":"%s","at":"%s"}\n' \
        "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE.tmp" 2>/dev/null
    mv -f "$STATUS_FILE.tmp" "$STATUS_FILE" 2>/dev/null || true
    chmod 0644 "$STATUS_FILE" 2>/dev/null || true
}

# One NTP server: a hostname or an IPv4/IPv6 literal. Deliberately strict —
# no spaces, no '#', no '=', no newlines, nothing that could start a second
# directive inside timesyncd.conf.
valid_one() {
    local s="$1"
    [ -n "$s" ] || return 1
    [ ${#s} -le 253 ] || return 1
    [[ "$s" =~ ^[A-Za-z0-9]([A-Za-z0-9.:-]*[A-Za-z0-9])?$ ]] || return 1
    # A bare run of digits and dots that is not a valid IPv4 is a typo, not a
    # hostname; catching it here beats a silently broken time source.
    if [[ "$s" =~ ^[0-9.]+$ ]]; then
        local IFS=. o n=0
        for o in $s; do
            [[ "$o" =~ ^[0-9]{1,3}$ ]] || return 1
            [ "$o" -le 255 ] || return 1
            n=$((n+1))
        done
        [ "$n" -eq 4 ] || return 1
    fi
    return 0
}

# systemd's NTP= takes a space-separated list, which is how an operator
# specifies fallbacks. Every element must pass on its own.
valid_ntp_list() {
    local v="$1" one
    [ -n "$v" ] || return 1
    [ ${#v} -le 512 ] || return 1
    for one in $v; do
        valid_one "$one" || return 1
    done
    return 0
}

read_ntp() {
    # No jq on a minimal Pi OS image, and python3 is guaranteed here because
    # the whole application is written in it.
    python3 - "$SETTINGS" <<'PY' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f:
        v = json.load(f).get("ntp", "")
    print(v if isinstance(v, str) else "")
except Exception:
    print("")
PY
}

apply_ntp() {
    local ntp
    [ -r "$SETTINGS" ] || { log "no readable $SETTINGS"; return 2; }
    ntp=$(read_ntp)

    if [ -z "$ntp" ]; then
        # Empty means "use the distro default". Remove our drop-in rather than
        # writing an empty NTP= line, which systemd treats as a parse error.
        # (The old python path validated with gethostbyname alone, and
        # gethostbyname("") RESOLVES — so an empty value used to sail through
        # and produce exactly that broken config.)
        if [ -f "$CONF" ]; then
            rm -f "$CONF"
            log "ntp cleared; removed $CONF and reverted to distro defaults"
            systemctl restart systemd-timesyncd 2>/dev/null
        fi
        write_status "cleared" "" "using distro default time servers"
        return 0
    fi

    if ! valid_ntp_list "$ntp"; then
        log "REFUSING invalid ntp value from settings.json"
        write_status "invalid" "" "rejected; not a valid host or address list"
        return 3
    fi

    # Normalise whitespace before writing. Word-splitting already treats a tab
    # or a run of spaces as a separator, so 'a.com<TAB>b' validates as the
    # two-server list it is — but the config file should record it as one clean
    # space-separated line rather than echoing whatever spacing was in the JSON.
    ntp=$(echo $ntp)

    mkdir -p "$(dirname "$CONF")" 2>/dev/null
    printf '[Time]\nNTP=%s\n' "$ntp" > "$CONF.tmp" || return 2
    chmod 0644 "$CONF.tmp"
    mv -f "$CONF.tmp" "$CONF" || return 2
    log "ntp set to '$ntp'"

    if systemctl restart systemd-timesyncd 2>/dev/null; then
        # Resolution is checked AFTER saving, not before. An operator setting an
        # internal time server on a network that is not up yet must still be
        # able to configure it; a name that does not resolve is worth reporting,
        # not worth refusing.
        local first="${ntp%% *}"
        if getent hosts "$first" >/dev/null 2>&1; then
            write_status "applied" "$ntp" "timesyncd restarted"
        else
            write_status "applied" "$ntp" "saved, but '$first' does not resolve yet"
            log "warning: '$first' does not currently resolve"
        fi
    else
        write_status "applied" "$ntp" "saved; could not restart timesyncd"
    fi
    return 0
}

case "${1:-}" in
    --apply)
        rm -f "$REQUEST_FILE" 2>/dev/null   # consume the trigger first so a
                                            # failure cannot re-arm the path unit
        apply_ntp
        ;;
    --validate)
        # For tests and for the wizard to shell out to if it wants a check.
        valid_ntp_list "${2:-}" && { echo valid; exit 0; } || { echo invalid; exit 3; }
        ;;
    *)
        echo "usage: $0 --apply | --validate <value>" >&2
        exit 1
        ;;
esac
HCSETTINGSSH

for _hc_root in hamclock-update.sh hamclock-apply-settings.sh; do
    sudo chown root:root "$INSTALL_DIR/$_hc_root"
    sudo chmod 0755 "$INSTALL_DIR/$_hc_root"
    assert_root_owned "$INSTALL_DIR/$_hc_root" 755
done

# installed_version() in hamclock-update.sh reads this file and nothing else.
# It deliberately never asks git — this single-file installer exists precisely
# for Pis with no checkout — so the version has to be baked into the installer
# text itself. scripts/sync_installers.py stamps it from the repo VERSION file
# and --check fails the build if the two drift.
sudo tee "$INSTALL_DIR/VERSION" > /dev/null << 'HCVERSIONTXT'
1.0.7
HCVERSIONTXT
sudo chown root:root "$INSTALL_DIR/VERSION"
sudo chmod 0644 "$INSTALL_DIR/VERSION"
assert_root_owned "$INSTALL_DIR/VERSION" 644
echo "Installed version: $(cat "$INSTALL_DIR/VERSION")"

# ── Step 4b: Remove stale files left by earlier versions ────────────
# $INSTALL_DIR is owned exclusively by this installer, so any program file in it
# that we no longer ship is a leftover from an older version.
#
# Bytecode is the case that actually bites. The pygame unit runs
# `python3 -O -m compileall` as an ExecStartPre (Tier 1c), and compileall only
# ever ADDS .pyc files — it never prunes them. A module dropped in a later
# release therefore leaves an importable orphan in __pycache__ forever, which
# can shadow a since-renamed import long after the .py is gone. Wiping the cache
# here costs nothing: the next service start rebuilds it with -O.
#
# User state is deliberately out of reach — settings live in /etc/hamclock-lite
# and cached images in /var/cache/hamclock-lite, both outside $INSTALL_DIR.
# The systemd units are rewritten unconditionally every run, so they cannot go
# stale and are not touched here.
#
# Set HAMCLOCK_SKIP_CLEAN=1 to skip this step.
# hamclock-update.sh and hamclock-apply-settings.sh MUST be listed: the sweep
# below deletes every *.sh in $INSTALL_DIR that is not shipped, so omitting them
# would delete the two root helpers moments after installing them and leave the
# path units pointing at nothing.
HAMCLOCK_SHIPPED_FILES="server.py index.html hamclock_data.py hamclock_pygame.py hamclock_tkinter.py kiosk.sh hamclock-update.sh hamclock-apply-settings.sh VERSION"

cleanup_stale_install() {
    if [ "${HAMCLOCK_SKIP_CLEAN:-0}" = "1" ]; then
        echo "Skipping stale-file cleanup (HAMCLOCK_SKIP_CLEAN=1)"
        return 0
    fi
    [ -d "$INSTALL_DIR" ] || return 0

    _hc_cleaned=0

    if [ -d "$INSTALL_DIR/__pycache__" ]; then
        sudo rm -rf "$INSTALL_DIR/__pycache__"
        echo "  removed stale bytecode cache (__pycache__/)"
        _hc_cleaned=$((_hc_cleaned + 1))
    fi

    # Only file types we have ever shipped, so a file the operator deliberately
    # parked here is left alone rather than silently deleted.
    for _hc_stale in "$INSTALL_DIR"/*.py "$INSTALL_DIR"/*.pyc "$INSTALL_DIR"/*.pyo \
                     "$INSTALL_DIR"/*.html "$INSTALL_DIR"/*.sh "$INSTALL_DIR"/*.bak \
                     "$INSTALL_DIR"/*.orig "$INSTALL_DIR"/*.rej "$INSTALL_DIR"/*.tmp \
                     "$INSTALL_DIR"/*~; do
        [ -f "$_hc_stale" ] || continue
        _hc_base=$(basename "$_hc_stale")
        case " $HAMCLOCK_SHIPPED_FILES " in
            *" $_hc_base "*) continue ;;
        esac
        sudo rm -f "$_hc_stale"
        echo "  removed stale file: $_hc_base"
        _hc_cleaned=$((_hc_cleaned + 1))
    done

    if [ "$_hc_cleaned" -gt 0 ]; then
        echo "Cleaned $_hc_cleaned stale item(s) from $INSTALL_DIR"
    fi
    return 0
}

echo "Checking for stale files from earlier versions..."
cleanup_stale_install

# ---- Pre-compile bytecode as root, here, not at service start --------------
# The hamclock-lite unit runs as User=$SERVICE_USER (non-root) while
# $INSTALL_DIR is root-owned, so the service user cannot CREATE
# $INSTALL_DIR/__pycache__. The unit's `compileall` ExecStartPre only ever
# worked because that directory already happened to exist; once the cleanup
# above removes it, ExecStartPre dies with PermissionError and systemd refuses
# to start the service ("control process exited with error code").
#
# Compiling here, as root, is also simply the right place: the .py files change
# only at install time, so there is nothing for a per-start pass to discover.
# Both variants are produced because PYTHONOPTIMIZE=1 (pygame mode) looks for
# .opt-1.pyc while browser/tkinter mode looks for the plain .pyc.
echo "Pre-compiling bytecode..."
sudo python3 -m compileall -q "$INSTALL_DIR" >/dev/null 2>&1 || true
sudo python3 -O -m compileall -q "$INSTALL_DIR" >/dev/null 2>&1 || true

# ── Step 5: Create hamclock-lite systemd service ────────────────────
echo "Creating HamClock server service..."
# The unit is rewritten on EVERY run (it used to sit inside
# `if ! systemctl is-enabled hamclock-lite`, so an existing box never picked up
# unit changes such as CacheDirectory=). Only enable/start is first-run
# behaviour; a re-run is covered by the explicit restart at the end of the script.
# Tier 1c: pygame-mode only — reduce glibc arena fragmentation, strip
# asserts/docstrings, and pre-compile .pyc once at service start so
# subsequent imports skip the bytecode compile path on a 512 MB Pi.
LITE_PYGAME_ENV=""
LITE_PYGAME_PRE=""
if [ "$KIOSK_MODE" = "pygame" ]; then
    LITE_PYGAME_ENV="Environment=MALLOC_ARENA_MAX=1 PYTHONOPTIMIZE=1 PYTHONDONTWRITEBYTECODE=1"
    # Leading '-': failure here must never block startup. The install already
    # compiled this as root; the service user cannot write $INSTALL_DIR, so a
    # non-zero exit is expected whenever anything is genuinely out of date and
    # is not a reason to refuse to run — worst case Python compiles in memory.
    LITE_PYGAME_PRE="ExecStartPre=-/usr/bin/python3 -O -m compileall -q /opt/hamclock-lite"
fi
# CacheDirectory= makes systemd create/chown /var/cache/hamclock-lite for the
# service user, which is where server.py persists the fetched images so a warm
# boot paints immediately instead of waiting on the network. Needs systemd>=235
# (Buster 241, Bullseye 247, Bookworm 252).
sudo tee /etc/systemd/system/hamclock-lite.service > /dev/null <<EOF
[Unit]
Description=HamClock Lite Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
CacheDirectory=hamclock-lite
# The request directory for the privilege boundary. The dashboard's ONLY
# privileged action is creating an empty flag file in here; the root path units
# watch for it. RuntimeDirectory= makes systemd create /run/hamclock-lite owned
# by this same User=, so the service user can write flag files while the status
# files root drops alongside them stay root-owned.
# It has to be systemd's job rather than a one-time mkdir at install: /run is a
# tmpfs and is empty again after every reboot.
# Preserve=yes keeps the directory across a restart of THIS unit — the updater
# restarts hamclock-lite mid-apply, and without it the directory and the status
# file the dashboard is polling would disappear underneath the update.
RuntimeDirectory=hamclock-lite
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
$LITE_PYGAME_ENV
$LITE_PYGAME_PRE
ExecStart=/usr/bin/python3 $INSTALL_DIR/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
if ! systemctl is-enabled hamclock-lite &>/dev/null; then
    sudo systemctl enable hamclock-lite
    sudo systemctl start hamclock-lite
fi

# ── Step 6: Install X server packages (browser/tkinter modes) ──────
echo "Installing display server packages (this may take 15-30 minutes on a Pi 1)..."
sudo apt update
if [ "$KIOSK_MODE" = "browser" ] || [ "$KIOSK_MODE" = "tkinter" ]; then
    sudo apt install -y xserver-xorg xinit x11-xserver-utils unclutter curl matchbox-window-manager xfonts-base dmz-cursor-theme
else
    sudo apt install -y curl
fi

# Mode-specific Python packages
if [ "$KIOSK_MODE" = "pygame" ]; then
    # Phase 2: python3-cairosvg for MUF SVG->PNG rasterize; cpulimit caps
    # the subprocess to 50% of one core so the render loop keeps its budget.
    sudo apt install -y python3-pygame python3-cairosvg cpulimit

    # librsvg2-bin gets its OWN line, deliberately: apt fails the whole
    # transaction if any one package is unavailable, and this is an
    # optimisation rather than a requirement — losing it must not take
    # python3-pygame down with it. Hence `|| true`.
    #
    # It provides rsvg-convert, which server.py prefers over cairosvg for the
    # MUF map. cairosvg parses the document in Python and spends ~61% of its
    # time in an O(n^2) re-walk triggered once per <use> element (257 of them
    # in this map); librsvg is C and does not. Measured on the real KC2G SVG:
    # 1.204 s -> 0.181 s, a 6.7x speedup, visually identical output.
    #
    # This is not a nicety. Field diagnostics from a Pi 1B showed cairosvg
    # NEVER completing: 0 successes, 3 timeouts, still failing at an 88 s
    # budget, so the MUF panel could only ever read "(map loading)".
    sudo apt install -y librsvg2-bin || true

    # Tier 1c: free RAM + boot time on a 512 MB Pi by masking kiosk-irrelevant daemons.
    # All four are non-essential for a wired-Ethernet HDMI kiosk.
    sudo systemctl mask bluetooth hciuart ModemManager avahi-daemon triggerhappy 2>/dev/null || true

    # Tier 1c: config.txt trims — free RAM, disable kiosk-irrelevant subsystems.
    BOOT_CFG="/boot/firmware/config.txt"
    [ -f "$BOOT_CFG" ] || BOOT_CFG="/boot/config.txt"
    if [ -f "$BOOT_CFG" ]; then
        add_cfg() {
            # add_cfg <key>=<value>
            local kv="$1" key="${1%%=*}"
            if ! grep -qE "^${key}=" "$BOOT_CFG"; then
                echo "$kv" | sudo tee -a "$BOOT_CFG" > /dev/null
            fi
        }
        add_cfg "gpu_mem=16"               # min split; pygame is software, frees ~48 MB
        add_cfg "dtparam=audio=off"        # no audio on a kiosk
        add_cfg "camera_auto_detect=0"     # no camera probe
        add_cfg "display_auto_detect=0"    # no extra display probe
        add_cfg "disable_overscan=1"       # full HDMI canvas
        add_cfg "hdmi_blanking=0"          # never DPMS the display
        # framebuffer_width/height are NOT set any more. Under KMS they are
        # silently ignored — they only ever worked with the firmware scaler
        # that KMS removed — which is how a Pi asking for 720x450 ended up at
        # 800x600 on a 1440x900 panel, upscaled by a fractional 1.8x. The
        # client now asks the connector for its real mode instead.
        #
        # Leaving them would be worse than useless: on a legacy or fkms stack
        # they DO take effect, pinning exactly the half-resolution framebuffer
        # whose blur this change exists to remove.
    fi

    # Tier 1c: quieter boot, no fsck at boot, no cursor on the TTY before kiosk paints.
    CMDLINE="/boot/firmware/cmdline.txt"
    [ -f "$CMDLINE" ] || CMDLINE="/boot/cmdline.txt"
    if [ -f "$CMDLINE" ]; then
        for tok in "quiet" "loglevel=3" "logo.nologo" "vt.global_cursor_default=0" "fsck.mode=skip"; do
            grep -q "$tok" "$CMDLINE" || sudo sed -i "s|\$| $tok|" "$CMDLINE"
        done
    fi

    # Tier 1c: journald in RAM + capped to keep SD writes low.
    sudo mkdir -p /etc/systemd/journald.conf.d
    sudo tee /etc/systemd/journald.conf.d/hamclock-kiosk.conf > /dev/null <<'JEOF'
[Journal]
Storage=volatile
RuntimeMaxUse=32M
RuntimeMaxFileSize=8M
JEOF
    sudo systemctl restart systemd-journald 2>/dev/null || true

    # Tier 1c: ext4 mount opts — fewer SD writes on read-heavy workload.
    if grep -qE '^[^#].* / +ext4 +[^ ]+ +' /etc/fstab; then
        if ! grep -qE '^[^#].* / +ext4 +[^ ]*noatime' /etc/fstab; then
            sudo sed -i -E 's|^([^#].* / +ext4 +)([^ ]+)( +)|\1\2,noatime,commit=60\3|' /etc/fstab
        fi
    fi

    # Tier 1c: low-pressure swap + low dirty thresholds for a 512 MB Pi.
    sudo tee /etc/sysctl.d/99-hamclock-kiosk.conf > /dev/null <<'SEOF'
vm.swappiness=10
vm.vfs_cache_pressure=50
vm.dirty_background_ratio=5
vm.dirty_ratio=10
SEOF
    sudo sysctl --system > /dev/null 2>&1 || true
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    sudo apt install -y python3-tk python3-pil python3-pil.imagetk
fi

# ── Step 7: Try browser fallback chain (browser mode only) ─────────
BROWSER=""
BROWSER_CMD=""
if [ "$KIOSK_MODE" = "browser" ]; then
    for pkg in surf epiphany-browser midori chromium-browser chromium; do
        if sudo apt install -y "$pkg" 2>&1 | tail -1; then
            case "$pkg" in
                surf) BROWSER="surf"; BROWSER_CMD="surf http://localhost:8080" ;;
                epiphany-browser) BROWSER="epiphany"; BROWSER_CMD="epiphany-browser --application-mode http://localhost:8080" ;;
                midori) BROWSER="midori"; BROWSER_CMD="midori -e Fullscreen -a http://localhost:8080" ;;
                chromium-browser|chromium) BROWSER="chromium"; BROWSER_CMD="$pkg --kiosk --noerrdialogs --disable-translate --no-first-run --disable-features=TranslateUI --disk-cache-size=0 http://localhost:8080" ;;
            esac
            break
        fi
    done

    if [ -z "$BROWSER" ]; then
        echo "ERROR: Could not install any browser (tried surf, epiphany, midori, chromium)."
        echo "Please install a browser manually and re-run this script."
        exit 1
    fi
    echo "Browser installed: $BROWSER"
fi

# ── Step 8: Set Xwrapper.config ─────────────────────────────────────
sudo mkdir -p /etc/X11
sudo tee /etc/X11/Xwrapper.config > /dev/null <<XEOF
allowed_users=anybody
needs_root_rights=yes
XEOF

# Add user to video and tty groups for X server access
sudo usermod -aG video,tty,input "$SERVICE_USER"

# ── Step 8b: Write X11 monitor config (auto-detect resolution, 16-bit for Pi 1) ──
sudo mkdir -p /usr/share/X11/xorg.conf.d
sudo tee /usr/share/X11/xorg.conf.d/10-monitor.conf > /dev/null << 'MONEOF'
Section "Device"
    Identifier "default"
    Driver "fbdev"
    Option "SWcursor" "true"
EndSection

Section "Screen"
    Identifier "default"
    Device "default"
    Monitor "default"
    DefaultDepth 16
    SubSection "Display"
        Depth 16
    EndSubSection
EndSection

Section "Monitor"
    Identifier "default"
    Option "PreferredMode" "true"
EndSection
MONEOF

# ── Step 8b: Ensure adequate swap ──────────────────────────────────
# A 512MB Pi 1 running X + a browser has no RAM headroom, and an OOM kill
# is a common cause of the kiosk dropping to the bare CLI.
CUR_SWAP=$(free -m 2>/dev/null | awk '/Swap:/{print $2}')
if [ "${CUR_SWAP:-0}" -lt 512 ]; then
    echo "Configuring 512MB of swap (current: ${CUR_SWAP:-0}MB)..."
    if [ -f /etc/dphys-swapfile ]; then
        sudo sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
        grep -q '^CONF_SWAPSIZE=' /etc/dphys-swapfile || \
            echo 'CONF_SWAPSIZE=512' | sudo tee -a /etc/dphys-swapfile > /dev/null
        sudo dphys-swapfile setup && sudo dphys-swapfile swapon || true
    elif [ ! -f /swapfile ]; then
        sudo fallocate -l 512M /swapfile 2>/dev/null || \
            sudo dd if=/dev/zero of=/swapfile bs=1M count=512
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile && sudo swapon /swapfile || true
        grep -q '/swapfile' /etc/fstab 2>/dev/null || \
            echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    fi
fi

# ── Step 9: Create kiosk.sh launch script (mode-specific) ──────────
if [ "$KIOSK_MODE" = "browser" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<KIOSKEOF
#!/bin/bash
# Wait for HamClock server to be ready (Pi 1 boots slowly — allow 2 min)
for i in \$(seq 1 120); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Disable screen blanking and power management
xset s off
xset -dpms
xset s noblank

# Show a real cursor (fbdev has no HW cursor; without this nothing is drawn)
# then auto-hide it after 3s idle.
xsetroot -cursor_name left_ptr
unclutter -idle 3 -root &

# Start matchbox window manager (auto-maximizes all windows)
matchbox-window-manager -use_titlebar no -use_desktop_mode plain &
sleep 1

# Launch browser (matchbox will maximize it). Relaunch it if it ever exits —
# crashed, OOM-killed, or closed cleanly — so the kiosk never falls back to the
# bare console. This loop keeps the X server alive across browser restarts.
while true; do
    $BROWSER_CMD
    echo "kiosk: browser exited (\$?), relaunching in 2s..." >&2
    sleep 2
done
KIOSKEOF
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<'KIOSKEOF'
#!/bin/bash
# Wait for HamClock server to be ready (Pi 1 boots slowly — allow 2 min)
for i in $(seq 1 120); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
xset s off
xset -dpms
xset s noblank
# Relaunch the client if it ever exits so the kiosk never falls back to the
# bare console; the loop keeps the X server alive across client restarts.
while true; do
    python3 /opt/hamclock-lite/hamclock_tkinter.py
    echo "kiosk: tkinter client exited ($?), relaunching in 2s..." >&2
    sleep 2
done
KIOSKEOF
elif [ "$KIOSK_MODE" = "pygame" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<'KIOSKEOF'
#!/bin/bash
# Wait for HamClock server to be ready (Pi 1 boots slowly — allow 2 min)
for i in $(seq 1 120); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
# Pygame framebuffer mode: no X server, SDL draws directly to /dev/fb0.
# Driver selection is owned by the Python ladder in hamclock_pygame._init_display
# (fbcon -> kmsdrm -> x11 -> dummy). We keep SDL_FBDEV as a hint for the fbcon
# rung of the ladder.
export SDL_FBDEV=/dev/fb0
# Relaunch the client if it ever exits so the kiosk never falls back to the
# bare console (e.g. an SDL/framebuffer error after an HDMI hotplug).
while true; do
    python3 /opt/hamclock-lite/hamclock_pygame.py
    echo "kiosk: pygame client exited ($?), relaunching in 2s..." >&2
    sleep 2
done
KIOSKEOF
fi
sudo chmod +x /opt/hamclock-lite/kiosk.sh

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

    if [ "$REINSTALL_DECISION" = "seed-defaults" ] \
        || [ "$REINSTALL_DECISION" = "fresh-install" ]; then
        # Always seed a default settings.json so the wizard never
        # auto-launches on first boot — a headless Pi 1B has no USB
        # keyboard to drive it. User runs `sudo hamclock-setup` later.
        sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 /etc/hamclock-lite
        if [ ! -f "$SETTINGS_FILE" ]; then
            sudo tee "$SETTINGS_FILE" >/dev/null <<'SETTINGSEOF'
{
  "callsign": "N0CALL",
  "timezone": "UTC",
  "theme": "kstate",
  "ntp": ""
}
SETTINGSEOF
            sudo chown $SERVICE_USER:$SERVICE_USER "$SETTINGS_FILE"
            sudo chmod 0644 "$SETTINGS_FILE"
        fi
        echo "Run 'sudo hamclock-setup --callsign W1ABC ...' to personalize your settings."
    fi
fi
# --- end Phase 5 reinstall detection -------------------------------------

# ── Step 10: Create hamclock-kiosk systemd service (mode-specific) ──
if [ "$KIOSK_MODE" = "pygame" ]; then
    sudo tee /etc/systemd/system/hamclock-kiosk.service > /dev/null <<EOF
[Unit]
Description=HamClock Kiosk Display (pygame framebuffer)
After=hamclock-lite.service
Wants=hamclock-lite.service
# Never stop retrying — a fast-failing display must not leave the bare console.
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
Environment=HAMCLOCK_SERVICE_USER=$SERVICE_USER
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
# Force the kernel to switch to tty7 BEFORE the kiosk launches so the
# pygame client can't paint invisibly while tty1 stays foreground.
ExecStartPre=/usr/bin/chvt 7
ExecStart=/opt/hamclock-lite/kiosk.sh
# Restart on ANY exit (including clean exit 0), not just failures.
Restart=always
RestartSec=10
# Prefer to OOM-kill other processes before this display service.
OOMScoreAdjust=-250

[Install]
WantedBy=multi-user.target
EOF
else
    sudo tee /etc/systemd/system/hamclock-kiosk.service > /dev/null <<EOF
[Unit]
Description=HamClock Kiosk Display
After=hamclock-lite.service
Wants=hamclock-lite.service
# Never stop retrying — this also lets X recover once a flaky HDMI/EDID
# handshake finally succeeds, instead of giving up to the bare console.
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
Environment=HAMCLOCK_SERVICE_USER=$SERVICE_USER
Environment=DISPLAY=:0
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
# Force the kernel to switch to tty7 BEFORE the kiosk launches so the
# X server can't paint invisibly while tty1 stays foreground.
ExecStartPre=/usr/bin/chvt 7
ExecStart=/usr/bin/xinit /opt/hamclock-lite/kiosk.sh -- :0 vt7
# Restart on ANY exit (including clean exit 0), not just failures.
Restart=always
RestartSec=10
# Prefer to OOM-kill other processes before this display service.
OOMScoreAdjust=-250

[Install]
WantedBy=multi-user.target
EOF
fi

# ── Step 11: Fix consoleblank in cmdline.txt ────────────────────────
CMDLINE=""
if [ -f /boot/firmware/cmdline.txt ]; then
    CMDLINE="/boot/firmware/cmdline.txt"
elif [ -f /boot/cmdline.txt ]; then
    CMDLINE="/boot/cmdline.txt"
fi
if [ -n "$CMDLINE" ]; then
    if ! grep -q "consoleblank=0" "$CMDLINE"; then
        sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
    fi
fi

# Force HDMI output even if no monitor detected at boot
BOOT_CONFIG=""
if [ -f /boot/firmware/config.txt ]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    BOOT_CONFIG="/boot/config.txt"
fi
if [ -n "$BOOT_CONFIG" ]; then
    grep -q "hdmi_force_hotplug" "$BOOT_CONFIG" || sudo sh -c "echo 'hdmi_force_hotplug=1' >> $BOOT_CONFIG"
    grep -q "hdmi_drive" "$BOOT_CONFIG" || sudo sh -c "echo 'hdmi_drive=2' >> $BOOT_CONFIG"
fi

# ── Step 11b: Root-owned update / settings units ────────────────────
# The root half of the privilege model: two path units watching for the flag
# files the dashboard is allowed to create, the services they trigger, the
# daily check timer, and the boot-time NTP applier.
HAMCLOCK_ROOT_UNITS="hamclock-update.path hamclock-update.service hamclock-update-check.timer hamclock-update-check.service hamclock-apply-settings.path hamclock-apply-settings.service hamclock-apply-settings-boot.service"
# Only these four are activated directly. The three left out are *triggered*
# units — a .path or the .timer starts them. Enabling a triggered unit would
# additionally run it unconditionally at every boot, which for
# hamclock-update.service means attempting an update on every reboot.
HAMCLOCK_ENABLE_UNITS="hamclock-update-check.timer hamclock-update.path hamclock-apply-settings.path hamclock-apply-settings-boot.service"

enable_hamclock_root_units() {
    sudo systemctl daemon-reload
    local _u
    for _u in $HAMCLOCK_ENABLE_UNITS; do
        sudo systemctl enable "$_u"
        # A unit that will not start right now (no network yet, no settings
        # file yet) is not a reason to fail the install — it is enabled, and
        # the next boot will run it.
        sudo systemctl start "$_u" \
            || echo "note: $_u did not start now; it is enabled and runs at boot" >&2
    done
}

echo "Installing update/settings systemd units..."
sudo mkdir -p /etc/systemd/system
sudo tee "/etc/systemd/system/hamclock-update.path" > /dev/null << 'HCUNIT_UPDATE_PATH'
[Unit]
Description=HamClock Lite update request watcher
# This is the privilege boundary. The dashboard runs as an unprivileged user and
# is granted no sudo at all; the single thing it can do is create this empty
# file. systemd (as root) notices and starts the updater, which then decides for
# itself what to install and verifies it. The unprivileged side can ask for an
# update but can never choose its contents.

[Path]
PathExists=/run/hamclock-lite/update.request
Unit=hamclock-update.service

[Install]
WantedBy=multi-user.target
HCUNIT_UPDATE_PATH
sudo tee "/etc/systemd/system/hamclock-update.service" > /dev/null << 'HCUNIT_UPDATE_SERVICE'
[Unit]
Description=HamClock Lite update apply
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=oneshot
# Runs as root: it writes /opt/hamclock-lite, rewrites units, and may reboot.
# The script consumes the request file first thing, so a crash cannot leave the
# path unit re-triggering in a loop.
ExecStart=/opt/hamclock-lite/hamclock-update.sh --apply
# Long enough for a slow ARMv6 download plus install plus the health probe.
TimeoutStartSec=900
HCUNIT_UPDATE_SERVICE
sudo tee "/etc/systemd/system/hamclock-update-check.timer" > /dev/null << 'HCUNIT_CHECK_TIMER'
[Unit]
Description=HamClock Lite daily update check
Documentation=file:///opt/hamclock-lite/docs/versioning.md

[Timer]
# 07:00 local time, every day.
OnCalendar=*-*-* 07:00:00
# The Pi 1 has NO RTC. If the box was powered off at 07:00 — or the clock was
# still wrong at 07:00 because NTP had not synced yet — Persistent makes systemd
# run the check as soon as it can instead of silently skipping a day.
Persistent=true
# Nothing else is contending at 07:00, but a spread keeps a shack full of Pis
# from hitting the site in the same second.
RandomizedDelaySec=900
AccuracySec=1m
Unit=hamclock-update-check.service

[Install]
WantedBy=timers.target
HCUNIT_CHECK_TIMER
sudo tee "/etc/systemd/system/hamclock-update-check.service" > /dev/null << 'HCUNIT_CHECK_SERVICE'
[Unit]
Description=HamClock Lite update check (writes status, installs nothing)
# Checking before the clock is right produces a bogus "checked" timestamp, and
# TLS certificate validation itself fails when the date is wildly wrong — which
# on an RTC-less Pi 1 is the normal state for the first minute after boot.
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=oneshot
# --check only ever fetches a small manifest and writes a status file.
# It cannot install anything; that is a separate unit with a separate trigger.
ExecStart=/opt/hamclock-lite/hamclock-update.sh --check
TimeoutStartSec=120
Nice=19
IOSchedulingClass=idle
HCUNIT_CHECK_SERVICE
sudo tee "/etc/systemd/system/hamclock-apply-settings.path" > /dev/null << 'HCUNIT_SETTINGS_PATH'
[Unit]
Description=HamClock Lite settings-apply request watcher
# Same privilege boundary as the updater: the setup wizard runs as an
# unprivileged user and can save settings.json, but the NTP source lives in a
# root-owned timesyncd drop-in. The wizard creates this empty file to ask; root
# reads the value, re-validates it from scratch, and applies it.

[Path]
PathExists=/run/hamclock-lite/settings.request
Unit=hamclock-apply-settings.service

[Install]
WantedBy=multi-user.target
HCUNIT_SETTINGS_PATH
sudo tee "/etc/systemd/system/hamclock-apply-settings.service" > /dev/null << 'HCUNIT_SETTINGS_SERVICE'
[Unit]
Description=HamClock Lite apply root-owned settings (NTP)
# No network ordering on purpose: an operator must be able to configure an
# internal time server before that network is reachable. The script saves the
# value regardless and only reports whether it resolves.

[Service]
Type=oneshot
ExecStart=/opt/hamclock-lite/hamclock-apply-settings.sh --apply
TimeoutStartSec=120
HCUNIT_SETTINGS_SERVICE
sudo tee "/etc/systemd/system/hamclock-apply-settings-boot.service" > /dev/null << 'HCUNIT_SETTINGS_BOOT'
[Unit]
Description=HamClock Lite apply NTP setting at boot
ConditionPathExists=/etc/hamclock-lite/settings.json
# NOTE: do NOT add Before=systemd-timesyncd.service. timesyncd is pulled in by
# sysinit.target, and this unit is WantedBy=multi-user.target which is ordered
# after it — so Before= forms a cycle, and systemd breaks that cycle by DELETING
# the timesyncd start job. On a Pi 1 with no RTC that means no time sync at all,
# which then breaks TLS, the update check, and every timestamp on the box.
# (systemd-analyze verify catches this; keep it in the pre-ship checks.)
# Applying the drop-in late and restarting timesyncd is correct and cycle-free.

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/hamclock-lite/hamclock-apply-settings.sh --apply

[Install]
WantedBy=multi-user.target
HCUNIT_SETTINGS_BOOT

for _hc_unit in $HAMCLOCK_ROOT_UNITS; do
    sudo chown root:root "/etc/systemd/system/$_hc_unit"
    sudo chmod 0644 "/etc/systemd/system/$_hc_unit"
    assert_root_owned "/etc/systemd/system/$_hc_unit" 644
done
enable_hamclock_root_units

# ── Step 12: Enable and start both services ─────────────────────────
sudo systemctl daemon-reload
sudo systemctl enable hamclock-lite hamclock-kiosk
# Always restart to pick up any file changes. This is also what gives
# /run/hamclock-lite its RuntimeDirectory= ownership on an upgrade of a Pi
# whose hamclock-lite unit predates that setting.
sudo systemctl restart hamclock-lite
sudo systemctl restart hamclock-kiosk

# ── Step 13: Print IP address and completion message ────────────────
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "=== Installation Complete — Kiosk Mode Installed ($KIOSK_MODE) ==="
echo "HamClock will now display fullscreen on this Pi's monitor."
echo "It will auto-start on every boot."
echo ""
if [ "$KIOSK_MODE" = "browser" ]; then
    echo "Display: browser ($BROWSER)"
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    echo "Display: native tkinter client (Python/X11)"
elif [ "$KIOSK_MODE" = "pygame" ]; then
    echo "Display: native pygame client (framebuffer, no X)"
fi
echo ""
echo "Commands:"
echo "  sudo systemctl status hamclock-kiosk   — check kiosk status"
echo "  sudo systemctl restart hamclock-kiosk  — restart display"
echo "  sudo systemctl stop hamclock-kiosk     — stop display"
echo "  sudo systemctl disable hamclock-kiosk  — disable auto-start"
echo ""
echo "To go back to normal CLI, run:"
echo "  sudo systemctl disable hamclock-kiosk"
echo "  sudo systemctl stop hamclock-kiosk"
echo ""
echo "Also accessible from any browser at: http://${PI_IP}:8080"

}
main "$@"
