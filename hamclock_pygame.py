"""Native Pygame client for HamClock Lite.

Replaces the browser on a Raspberry Pi 1 Model B, fetching data from
the same /api/* endpoints as the web UI but rendering directly with
Pygame/SDL for a ~50 MB RAM and ~10% CPU win over the browser stack.
"""

import argparse
import collections
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

SCREEN_W = 1440
SCREEN_H = 900

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


def _make_fonts():
    """Build the fonts dict. Falls back to default font if SysFont fails.

    Includes 'tiny' (size 11) used by draw_image's loading placeholder so
    the inline pygame.font.Font(None, 18) per-frame allocation is gone.
    Also clears the module-level _glyph_cache so stale renders from a
    previous font set cannot leak through (Task 1.3).
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
    return {
        'title': mk(22),
        'panel': mk(14),
        'body': mk(14),
        'label': mk(12),
        'small': mk(11),
        'tiny': mk(11),
    }


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


def _load_image(data_bytes):
    """Decode JPEG/PNG bytes into a Pygame surface, or None on failure."""
    if not data_bytes:
        return None
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
    return pygame.Rect(rect.x + 6, rect.y + 22, rect.w - 12, rect.h - 26)


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


def draw_image(screen, rect, surface, fonts=None, theme=None,
               image_key=None, fetched_at=None):
    if surface is None:
        if fonts is not None and 'tiny' in fonts:
            label_color = theme['label'] if theme is not None else (184, 160, 216)
            _blit_text(screen, fonts['tiny'], 'image loading...',
                       label_color, rect.x + 6, rect.y + 6)
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


def _get_cached_image(data, key, image_cache, image_cache_ts):
    """Return a pygame Surface for data.images[key], rebuilt when refresh ts changes."""
    raw = data.images.get(key) if isinstance(data.images, dict) else None
    if raw is None:
        return None
    ts = data.last_image_refresh
    if image_cache_ts.get(key) != ts or key not in image_cache:
        surf = _load_image(raw)
        if surf is not None:
            image_cache[key] = surf
            image_cache_ts[key] = ts
    return image_cache.get(key)


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


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    injected_iter = None
    if args.inject_events:
        injected_iter = _inject_event_iter(
            _load_injected_events(args.inject_events))

    if 'DISPLAY' not in os.environ:
        os.environ.setdefault('SDL_VIDEODRIVER', 'fbcon')
        os.environ.setdefault('SDL_FBDEV', '/dev/fb0')

    pygame.init()
    try:
        pygame.mouse.set_visible(True)
    except Exception:
        pass

    try:
        screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
    except pygame.error:
        try:
            screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        except pygame.error:
            screen = pygame.display.set_mode((800, 600))
    pygame.display.set_caption('HamClock Lite')

    fonts = _make_fonts()
    settings = load_settings()
    theme = THEMES.get(settings['theme'], THEMES['kstate'])

    data = HamClockData()
    try:
        data.start_background(data_interval=60, image_interval=900)
    except Exception as e:
        print('data start error:', e, file=sys.stderr)

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
            screen.fill(theme['bg'])

            header = pygame.Rect(0, 0, sw, 30)
            callsign = settings.get('callsign') or os.environ.get(
                'HAMCLOCK_CALLSIGN', 'N0CALL')
            draw_header(screen, header, callsign, fonts, theme)

            status = pygame.Rect(0, sh - 20, sw, 20)
            draw_status_bar(screen, status, data, fonts, theme)

            content_top = 32
            content_bot = sh - 22
            content_h = content_bot - content_top

            left_w = int(sw * 288 / 1440)
            mid_w = int(sw * (936 - 288) / 1440)
            right_w = sw - left_w - mid_w

            # ---- LEFT COLUMN ----
            lx = 2
            ly = content_top
            panel_gap = 4
            # allocate heights (percent of content_h)
            heights = [
                int(content_h * 0.20),  # solar
                int(content_h * 0.12),  # bands
                int(content_h * 0.28),  # sdo
                int(content_h * 0.10),  # geomag
                int(content_h * 0.10),  # xray
            ]
            heights.append(content_h - sum(heights) - panel_gap * 5)  # open bands
            titles = ['SOLAR', 'BANDS', 'SDO IMAGE', 'GEOMAGNETIC', 'X-RAY FLUX', 'OPEN BANDS']
            cy = ly
            panel_rects = []
            for h, t in zip(heights, titles):
                r = pygame.Rect(lx, cy, left_w - 4, h)
                inner = draw_panel(screen, r, t, fonts, theme)
                panel_rects.append(inner)
                cy += h + panel_gap

            try:
                draw_solar(screen, panel_rects[0], data.solar or {}, fonts, theme)
            except Exception:
                pass
            try:
                draw_bands(screen, panel_rects[1], data.bands or {}, fonts, theme)
            except Exception:
                pass
            try:
                sdo_surf = _get_cached_image(data, 'solar-image', image_cache, image_cache_ts)
                draw_image(screen, panel_rects[2], sdo_surf, fonts, theme,
                           image_key='solar-image',
                           fetched_at=data.image_fetched_at.get('solar-image', 0.0))
            except Exception:
                pass
            try:
                draw_geomag(screen, panel_rects[3], data.solar or {}, fonts, theme)
            except Exception:
                pass
            try:
                draw_xray(screen, panel_rects[4], data.solar or {}, fonts, theme)
            except Exception:
                pass
            try:
                draw_open_bands(screen, panel_rects[5], data.bands or {}, fonts, theme)
            except Exception:
                pass

            # ---- MIDDLE COLUMN ----
            mx = lx + left_w
            mid_rect = pygame.Rect(mx, content_top, mid_w - 4, content_h)
            mid_inner = draw_panel(screen, mid_rect, 'MUF STATUS', fonts, theme)
            try:
                draw_muf_text(screen, mid_inner, data.solar or {}, fonts, theme)
            except Exception:
                pass

            # ---- RIGHT COLUMN ----
            rx = mx + mid_w
            rh_dx = int(content_h * 0.28)
            rh_ba = int(content_h * 0.32)
            rh_prop = content_h - rh_dx - rh_ba - panel_gap * 2

            dx_r = pygame.Rect(rx, content_top, right_w - 4, rh_dx)
            dx_inner = draw_panel(screen, dx_r, 'DX SPOTS', fonts, theme)
            try:
                draw_dx_spots(screen, dx_inner, data.dxspots or [], fonts, theme)
            except Exception:
                pass

            ba_r = pygame.Rect(rx, content_top + rh_dx + panel_gap, right_w - 4, rh_ba)
            ba_inner = draw_panel(screen, ba_r, 'BAND ACTIVITY', fonts, theme)
            try:
                draw_band_activity(screen, ba_inner, data.dxspots or [], fonts, theme)
            except Exception:
                pass

            prop_r = pygame.Rect(rx, content_top + rh_dx + rh_ba + panel_gap * 2,
                                 right_w - 4, rh_prop)
            prop_inner = draw_panel(screen, prop_r, 'PROPAGATION', fonts, theme)
            tab_bar = pygame.Rect(prop_inner.x, prop_inner.y, prop_inner.w, 20)
            tab_regions = draw_tabs(screen, tab_bar, ['drap', 'aurora', 'enlil'],
                                    active_tab, fonts, theme)
            img_rect = pygame.Rect(prop_inner.x, prop_inner.y + 24,
                                   prop_inner.w, prop_inner.h - 24)
            try:
                key = tab_image_key.get(active_tab, 'real-drap')
                surf = _get_cached_image(data, key, image_cache, image_cache_ts)
                draw_image(screen, img_rect, surf, fonts, theme,
                           image_key=key,
                           fetched_at=data.image_fetched_at.get(key, 0.0))
            except Exception:
                pass

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
