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
import time

import pygame

from hamclock_data import HamClockData

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


def setup_screen(screen, fonts, theme):
    """Render the first-boot wizard. Block until Save, return settings dict.

    Reads events from pygame.event.get() unless HAMCLOCK_DEBUG=1 and
    HAMCLOCK_INJECT_EVENTS is set, in which case events are read from
    the named JSON file and dispatched one per frame."""
    sw, sh = screen.get_size()

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
        initial="", max_len=64,
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
                            _wait_for_ntp_sync(deadline_s=10.0)
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
        title = title_font.render("HAMCLOCK SETUP", True, theme["fg"])
        screen.blit(title, (sw // 2 - title.get_width() // 2, 180))

        call_field.draw(screen, theme, focused=(focus == 0))
        tz_field.draw(screen, theme, focused=(focus == 1))

        # Theme cycler row.
        tf_font = panel_font
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

        hint = small_font.render(
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

SCREEN_W = 720    # Tier 2a: native render at 720x450; BCM2835 HVS upscales to 1440x900 in firmware
SCREEN_H = 450

# Propagation panel tabs (module-level so the wiring is testable and lives in
# one place). Each tab maps to one of hamclock_data's _IMAGE_ENDPOINTS keys;
# the render loop resolves the active tab through PROP_TAB_IMAGE_KEY and blits
# data.images[key]. 'muf' surfaces the KC2G MUF map the server already fetches
# and rasterizes to PNG for /api/muf-map — decoded lazily only when selected,
# so idle RAM/FPS on the Pi 1B are unchanged.
PROP_TABS = ['drap', 'aurora', 'enlil', 'muf']
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
    header_h = 30
    status_h = 20
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


def _make_fonts():
    """Build the fonts dict. Falls back to default font if SysFont fails.

    Sizes are tuned for the Tier 2a 720x450 native framebuffer (HVS upscales
    to 1440x900 on Pi 1 / VideoCore IV). A 'tiny' font at 8 px renders
    pleasantly at 16 px effective on the HDMI output.
    """
    # Ensure font subsystem is up; callers (incl. recovery-overlay tests) may
    # only have initialized pygame.display, leaving pygame.font uninitialized.
    try:
        if not pygame.font.get_init():
            pygame.font.init()
    except Exception:
        pass
    def mk(size):
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


def draw_muf_text(screen, rect, solar, fonts, theme):
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
        # Was '(Map available in web UI)', which stopped being true once the
        # native client grew a MUF tab — it sent operators to a browser this
        # hardware cannot usefully run. Point at the tab that now has the map.
        _blit_fit(screen, foot_f, 'Map: PROPAGATION > MUF', theme['label'],
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


def draw_status_bar(screen, rect, data, fonts, theme):
    """Item 8: status bar text is pulled from _strfmt_cache so the format
    string is built at most once per UTC second."""
    pygame.draw.rect(screen, theme['card'], rect)
    pygame.draw.rect(screen, theme['border'], rect, 1)
    text = _formatted_strings(data)["status"]
    f = fonts['small']
    # Tier 1.1: the quit hint was pinned 110 px from the right edge, which is
    # a different fraction of a 720 px bar than of a 1440 px one; place it by
    # its measured width and give the status string the rest.
    hint = 'ESC/Q to quit'
    hint_x = max(rect.x, rect.right - 6 - f.size(hint)[0])
    ty = rect.y + max(0, min(4, rect.h - f.get_height()))
    _blit_fit(screen, f, text, theme['label'], rect.x + 6, ty,
              hint_x - rect.x - 12)
    _blit_fit(screen, f, hint, theme['label'], hint_x, ty,
              rect.right - hint_x)


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
        for k in ('sdo', 'propagation'):
            r = panel_rects.get(k)
            if r is not None and r not in dirty:
                dirty.append(r)
    return dirty


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


def _init_display():
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
            scr = pygame.display.set_mode(
                (SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
            print('[display] SDL driver=%s mode=%s'
                  % (drv, scr.get_size()), file=sys.stderr)
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

    screen = _init_display()
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
    image_cache = {}
    image_cache_ts = {}
    tab_regions = {}
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

    clock = pygame.time.Clock()
    running = True
    # A transient SDL/framebuffer error (e.g. an HDMI hotplug or VT switch)
    # raising out of the loop would crash the client to the bare console.
    # Absorb such errors; if they persist, exit cleanly so the kiosk wrapper
    # restarts us with a fresh SDL context.
    consecutive_errors = 0
    while running:
        try:
            frame_events = (next(injected_iter)
                            if injected_iter is not None
                            else pygame.event.get())
            for event in frame_events:
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    pos = event.pos
                    for name, r in tab_regions.items():
                        if r.collidepoint(pos):
                            active_tab = name
                            dirty_state['full_flip_pending'] = True
                            break

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
            callsign = settings.get('callsign') or os.environ.get(
                'HAMCLOCK_CALLSIGN', 'N0CALL')
            if _panel_due('header'):
                draw_header(screen, header, callsign, fonts, theme, data=data)
                redrawn_this_frame.add('header')
                _panel_due_at['header'] = now_ts + _CADENCE_S['header']

            status = layout["status"]
            if _panel_due('status'):
                draw_status_bar(screen, status, data, fonts, theme)
                redrawn_this_frame.add('status')
                _panel_due_at['status'] = now_ts + _CADENCE_S['status']

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
                try:
                    draw_solar(screen, panel_rects[0], data.solar or {},
                               fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                redrawn_this_frame.add('solar')
                _panel_due_at['solar'] = now_ts + _CADENCE_S['solar']
            if _panel_due('bands'):
                try:
                    draw_bands(screen, panel_rects[1], data.bands or {}, fonts, theme)
                except Exception:
                    pass
                redrawn_this_frame.add('bands')
                _panel_due_at['bands'] = now_ts + _CADENCE_S['bands']
            if _panel_due('sdo'):
                # Tier 2.5: hoisted out of the try. The cadence line below
                # reads it, and a NameError there would land in the render
                # loop's consecutive_errors counter instead of this panel's
                # own except.
                sdo_surf = None
                try:
                    sdo_surf = _get_cached_image(data, 'solar-image', image_cache, image_cache_ts)
                    draw_image(screen, panel_rects[2], sdo_surf, fonts, theme,
                               image_key='solar-image',
                               fetched_at=_image_stamp(data, 'solar-image'),
                               status=_image_status_text(data, 'solar-image'))
                except Exception:
                    pass
                redrawn_this_frame.add('sdo')
                _panel_due_at['sdo'] = now_ts + (
                    _CADENCE_S['sdo'] if sdo_surf is not None
                    else _CADENCE_S_NO_IMAGE.get('sdo', _CADENCE_S['sdo']))
            if _panel_due('geomag'):
                try:
                    draw_geomag(screen, panel_rects[3], data.solar or {},
                                fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                redrawn_this_frame.add('geomag')
                _panel_due_at['geomag'] = now_ts + _CADENCE_S['geomag']
            if _panel_due('xray'):
                try:
                    draw_xray(screen, panel_rects[4], data.solar or {},
                              fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                redrawn_this_frame.add('xray')
                _panel_due_at['xray'] = now_ts + _CADENCE_S['xray']
            if _panel_due('open_bands'):
                try:
                    draw_open_bands(screen, panel_rects[5], data.bands or {},
                                    fonts, theme, data_refresh_ts=data_ts)
                except Exception:
                    pass
                redrawn_this_frame.add('open_bands')
                _panel_due_at['open_bands'] = now_ts + _CADENCE_S['open_bands']

            # ---- MIDDLE COLUMN ----
            mid_rect = layout["muf"]
            if _panel_due('muf_text'):
                mid_inner = draw_panel(screen, mid_rect, 'MUF STATUS', fonts, theme)
                try:
                    draw_muf_text(screen, mid_inner, data.solar or {}, fonts, theme)
                except Exception:
                    pass
                redrawn_this_frame.add('muf_text')
                _panel_due_at['muf_text'] = now_ts + _CADENCE_S['muf_text']

            # ---- RIGHT COLUMN ----
            dx_r = layout["dx_spots"]
            if _panel_due('dx_spots'):
                dx_inner = draw_panel(screen, dx_r, 'DX SPOTS', fonts, theme)
                try:
                    draw_dx_spots(screen, dx_inner, data.dxspots or [], fonts, theme)
                except Exception:
                    pass
                redrawn_this_frame.add('dx_spots')
                _panel_due_at['dx_spots'] = now_ts + _CADENCE_S['dx_spots']

            ba_r = layout["band_activity"]
            if _panel_due('band_activity'):
                ba_inner = draw_panel(screen, ba_r, 'BAND ACTIVITY', fonts, theme)
                try:
                    draw_band_activity(screen, ba_inner, data.dxspots or [], fonts, theme)
                except Exception:
                    pass
                redrawn_this_frame.add('band_activity')
                _panel_due_at['band_activity'] = now_ts + _CADENCE_S['band_activity']

            prop_r = layout["propagation"]
            if _panel_due('propagation'):
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
            else:
                rects = [panel_rects_map[n] for n in redrawn_this_frame
                         if n in panel_rects_map]
                if rects:
                    pygame.display.update(rects)
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


if __name__ == '__main__':
    # CLI dispatch: --setup-cli short-circuits before the dashboard runs.
    rc = _cli_main(sys.argv[1:])
    if rc is not None:
        sys.exit(rc)
    main()  # existing dashboard entry point
