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
    global _MUF_PNG_SOURCE
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
    """Adaptive rasterize budget, floored at PHASE2_TIMEOUT_S."""
    ewma = _muf_render_ewma
    if not ewma:
        return PHASE2_TIMEOUT_S
    return int(min(PHASE2_TIMEOUT_MAX_S,
                   max(PHASE2_TIMEOUT_S, MUF_TIMEOUT_FACTOR * ewma)))


def _rasterize_once(payload, timeout_s):
    """One cpulimit+cairosvg subprocess round trip. None on any failure."""
    argv = ['cpulimit', '-l', '50', '-q', '--',
            'python3', '-c',
            'import sys, cairosvg; cairosvg.svg2png('
            'bytestring=sys.stdin.buffer.read(), '
            'output_width=360, write_to=sys.stdout.buffer)']
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
        return out
    # FileNotFoundError (cpulimit not installed) is an OSError subclass.
    except (subprocess.SubprocessError, OSError) as e:
        print('[muf] rasterize failed: %s' % e, file=sys.stderr)
        return None
    finally:
        # Belt and braces: no exit path may leave the group alive. A no-op on
        # every normal path (communicate() has already reaped by then).
        if p is not None and p.poll() is None:
            _kill_process_group(p)


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
    timeout_s = _muf_timeout()
    slimmed = _slim_muf_svg(svg_bytes)
    payload = slimmed if slimmed else svg_bytes

    started = time.monotonic()
    out = _rasterize_once(payload, timeout_s)
    elapsed = time.monotonic() - started

    if out:
        _record_muf_render(elapsed)
        return out

    # Tier 2.3 safety net: if the SLIMMED payload failed quickly, that is a
    # cairosvg parse complaint about our surgery, not a slow box — retry once
    # with the untouched bytes so a future upstream re-render can never turn a
    # working map into a permanently blank panel. Deliberately not retried
    # after a timeout: two full budgets back to back (up to 220 s) would
    # starve the 120 s fetch_dx cadence, which is exactly what
    # PHASE2_TIMEOUT_MAX_S exists to prevent.
    if slimmed and elapsed < timeout_s * 0.5:
        print('[muf] slimmed SVG did not render in %.1fs; retrying unslimmed'
              % elapsed, file=sys.stderr)
        started = time.monotonic()
        out = _rasterize_once(svg_bytes, timeout_s)
        if out:
            _record_muf_render(time.monotonic() - started)
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
    try:
        req = Request('https://www.hamqsl.com/solarxml.php', headers={'User-Agent': UA})
        # Tier 1b perf: 8 s is well above HamQSL's median response (~1 s);
        # the prior 15 s pinned the fetcher on a transient upstream stall.
        with urlopen(req, timeout=8) as resp:
            xml_data = resp.read().decode('utf-8')

        root = ElementTree.fromstring(xml_data)
        sd = root.find('.//solardata')
        if sd is None:
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
        print(f'[{time.strftime("%H:%M:%S")}] Solar/bands updated: SFI={solar["sfi"]} Kp={solar["kIndex"]}')
    except Exception as e:
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
                print(f'[{time.strftime("%H:%M:%S")}] DX spots updated: {len(spots)} spots from {url.split("/")[2]}')
                return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] DX fetch failed ({url.split("/")[2]}): {e}')
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
    try:
        data, not_modified = _conditional_get(MUF_URL, timeout=20)
        if not_modified:
            # CRITICAL: a 304 must not short-circuit when we have no PNG.
            # One failed rasterize plus a stalled upstream generator (the SVG
            # is regenerated on a schedule; a stall is normal) would otherwise
            # strand /api/muf-map on 503 permanently, because the only code
            # path that renders is the one that just returned early.
            if CACHE.get('muf_image_png'):
                print(f'[{time.strftime("%H:%M:%S")}] MUF map unchanged (304)')
                return
            data = CACHE.get('muf_image')
            if not data:
                print(f'[{time.strftime("%H:%M:%S")}] MUF map unchanged (304), '
                      f'nothing cached to rasterize')
                return
            print(f'[{time.strftime("%H:%M:%S")}] MUF map unchanged (304), '
                  f're-rasterizing the cached SVG')
        else:
            CACHE['muf_image'] = data
            CACHE['muf_image_updated'] = time.time()
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
    for url in urls:
        try:
            data, not_modified = _conditional_get(url, timeout=20)
            if not_modified:
                if _note_unchanged('Enlil', 'enlil_image', url):
                    # return, not continue: falling through would re-download
                    # the same product from the fallback URL every cycle.
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
            print(f'[{time.strftime("%H:%M:%S")}] Enlil updated ({len(data)} bytes)')
            return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] Enlil fetch failed ({url}): {e}')


def fetch_drap():
    """Fetch Aurora forecast (Northern Hemisphere) image"""
    urls = [
        'https://services.swpc.noaa.gov/images/aurora-forecast-northern-hemisphere.jpg',
        'https://services.swpc.noaa.gov/images/swx-overview-large.gif',
    ]
    for url in urls:
        try:
            data, not_modified = _conditional_get(url, timeout=20)
            if not_modified:
                if _note_unchanged('Aurora', 'drap_image', url):
                    return
                continue
            if not data:
                raise ValueError('empty body')
            CACHE['drap_image'] = data
            CACHE['drap_image_updated'] = time.time()
            _persist('drap_image')
            # 'Aurora', not 'DRAP': fetch_real_drap logs the D-RAP global map.
            # Two identical "DRAP updated" lines made the journal useless for
            # telling which of the two products actually landed.
            print(f'[{time.strftime("%H:%M:%S")}] Aurora updated ({len(data)} bytes)')
            return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] Aurora fetch failed ({url}): {e}')


def fetch_real_drap():
    """Fetch DRAP (D-Region Absorption Prediction) global image"""
    urls = [
        'https://services.swpc.noaa.gov/images/animations/d-rap/global/latest.png',
        'https://services.swpc.noaa.gov/images/d-rap/global_f10.png',
    ]
    for url in urls:
        try:
            data, not_modified = _conditional_get(url, timeout=20)
            if not_modified:
                if _note_unchanged('DRAP', 'real_drap_image', url):
                    return
                continue
            if not data:
                raise ValueError('empty body')
            CACHE['real_drap_image'] = data
            CACHE['real_drap_image_updated'] = time.time()
            _persist('real_drap_image')
            print(f'[{time.strftime("%H:%M:%S")}] DRAP updated ({len(data)} bytes)')
            return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] DRAP fetch failed ({url}): {e}')


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
    try:
        data, not_modified = _conditional_get(SDO_URL, timeout=20)
        if not_modified:
            _note_unchanged('SDO', 'solar_image', SDO_URL)
            return
        if not data:
            raise ValueError('empty body')
        CACHE['solar_image'] = data
        CACHE['solar_image_updated'] = time.time()
        _persist('solar_image')
        print(f'[{time.strftime("%H:%M:%S")}] SDO updated ({len(data)} bytes)')
    except Exception as e:
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
            elif want_png:
                self.send_error(503, 'MUF PNG not yet rendered')
                return
            elif svg:
                body = svg
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
