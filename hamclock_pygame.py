"""Native Pygame client for HamClock Lite.

Replaces the browser on a Raspberry Pi 1 Model B, fetching data from
the same /api/* endpoints as the web UI but rendering directly with
Pygame/SDL for a ~50 MB RAM and ~10% CPU win over the browser stack.
"""

import io
import os
import sys
import time

import pygame

from hamclock_data import HamClockData


# ---- K-State theme colors ----
BG = (42, 20, 80)
CARD = (58, 29, 101)
BORDER = (81, 40, 136)
TEXT = (232, 221, 245)
LABEL = (184, 160, 216)
BRIGHT = (255, 255, 255)
ACCENT_GOLD = (244, 197, 92)
STATUS_GREEN = (34, 197, 94)
STATUS_YELLOW = (234, 179, 8)
STATUS_RED = (239, 68, 68)

COND_COLORS = {
    'Good': (34, 197, 94),
    'Fair': (234, 179, 8),
    'Poor': (239, 68, 68),
    'N/A': (74, 85, 104),
}

BAND_COLORS = {
    '160m': (255, 107, 107), '80m': (240, 101, 149), '60m': (204, 93, 232),
    '40m': (132, 94, 247), '30m': (92, 124, 250), '20m': (51, 154, 240),
    '17m': (34, 184, 207), '15m': (32, 201, 151), '12m': (81, 207, 102),
    '10m': (148, 216, 45),
}

HF_BANDS = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']

SCREEN_W = 1440
SCREEN_H = 900


def _make_fonts():
    """Build the fonts dict. Falls back to default font if SysFont fails."""
    def mk(size):
        try:
            f = pygame.font.SysFont('monospace', size)
            if f is None:
                raise RuntimeError('no monospace')
            return f
        except Exception:
            return pygame.font.Font(None, size + 4)
    return {
        'title': mk(22),
        'panel': mk(14),
        'body': mk(14),
        'label': mk(12),
        'small': mk(11),
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
        surf = font.render(str(text), True, color)
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


def draw_panel(screen, rect, title, fonts):
    pygame.draw.rect(screen, CARD, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
    bar = pygame.Rect(rect.x, rect.y, rect.w, 18)
    pygame.draw.rect(screen, BORDER, bar)
    _blit_text(screen, fonts['panel'], title, BRIGHT, rect.x + 6, rect.y + 2)
    return pygame.Rect(rect.x + 6, rect.y + 22, rect.w - 12, rect.h - 26)


def draw_header(screen, rect, callsign, fonts):
    pygame.draw.rect(screen, CARD, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
    _blit_text(screen, fonts['title'], 'HAMCLOCK LITE', ACCENT_GOLD, rect.x + 8, rect.y + 4)
    if callsign:
        _blit_text(screen, fonts['body'], str(callsign), BRIGHT, rect.x + 220, rect.y + 8)
    try:
        utc = time.strftime('%H:%M:%S', time.gmtime())
        local = time.strftime('%H:%M:%S')
    except Exception:
        utc = local = '--:--:--'
    _blit_text(screen, fonts['body'], 'UTC ' + utc, TEXT, rect.x + rect.w - 340, rect.y + 8)
    _blit_text(screen, fonts['body'], 'LOC ' + local, TEXT, rect.x + rect.w - 180, rect.y + 8)
    dot_color = STATUS_GREEN if (int(time.time()) % 2 == 0) else STATUS_YELLOW
    pygame.draw.circle(screen, dot_color, (rect.x + rect.w - 18, rect.y + 14), 5)


def draw_solar(screen, rect, solar, fonts):
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
        _blit_text(screen, fonts['label'], label, LABEL, rect.x, y)
        _blit_text(screen, fonts['body'], str(value), BRIGHT, rect.x + 70, y - 1)
        y += 16


def draw_bands(screen, rect, bands, fonts):
    groups = [
        ('80m-40m', ['80m-40m']),
        ('30m-20m', ['30m-20m']),
        ('17m-15m', ['17m-15m']),
        ('12m-10m', ['12m-10m']),
    ]
    _blit_text(screen, fonts['label'], 'BAND', LABEL, rect.x, rect.y)
    _blit_text(screen, fonts['label'], 'DAY', LABEL, rect.x + 100, rect.y)
    _blit_text(screen, fonts['label'], 'NIGHT', LABEL, rect.x + 160, rect.y)
    y = rect.y + 16
    for name, keys in groups:
        entry = bands.get(keys[0], {}) if isinstance(bands, dict) else {}
        day = entry.get('day', 'N/A') if isinstance(entry, dict) else 'N/A'
        night = entry.get('night', 'N/A') if isinstance(entry, dict) else 'N/A'
        _blit_text(screen, fonts['body'], name, TEXT, rect.x, y)
        _blit_text(screen, fonts['body'], str(day), COND_COLORS.get(day, TEXT), rect.x + 100, y)
        _blit_text(screen, fonts['body'], str(night), COND_COLORS.get(night, TEXT), rect.x + 160, y)
        y += 16


def draw_image(screen, rect, surface):
    if surface is None:
        _blit_text(screen, pygame.font.Font(None, 18), 'image loading...', LABEL, rect.x + 6, rect.y + 6)
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


def draw_bar(screen, rect, value, vmax, color):
    pygame.draw.rect(screen, BG, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
    try:
        frac = 0.0 if vmax <= 0 else max(0.0, min(1.0, float(value) / float(vmax)))
    except Exception:
        frac = 0.0
    inner = pygame.Rect(rect.x + 1, rect.y + 1, int((rect.w - 2) * frac), rect.h - 2)
    if inner.w > 0:
        pygame.draw.rect(screen, color, inner)


def draw_muf_text(screen, rect, solar, fonts):
    rows = [
        ('FOF2', '{} MHz'.format(_safe(solar, 'fof2'))),
        ('GEOMAG', _safe(solar, 'geomagField')),
        ('KP', _safe(solar, 'kIndex')),
        ('SFI', _safe(solar, 'sfi')),
        ('SSN', _safe(solar, 'ssn')),
    ]
    y = rect.y + 20
    for label, value in rows:
        _blit_text(screen, fonts['panel'], label, LABEL, rect.x + 20, y)
        _blit_text(screen, fonts['title'], str(value), BRIGHT, rect.x + 140, y - 4)
        y += 44
    _blit_text(screen, fonts['small'], '(Map available in web UI)', LABEL,
               rect.x + 20, rect.y + rect.h - 20)


def draw_dx_spots(screen, rect, dxspots, fonts):
    if not isinstance(dxspots, list):
        dxspots = []
    _blit_text(screen, fonts['label'], 'FREQ', LABEL, rect.x, rect.y)
    _blit_text(screen, fonts['label'], 'BND', LABEL, rect.x + 90, rect.y)
    _blit_text(screen, fonts['label'], 'DX', LABEL, rect.x + 140, rect.y)
    _blit_text(screen, fonts['label'], 'SPOTTER', LABEL, rect.x + 230, rect.y)
    _blit_text(screen, fonts['label'], 'TIME', LABEL, rect.x + 340, rect.y)
    y = rect.y + 16
    for spot in dxspots[:5]:
        if not isinstance(spot, dict):
            continue
        freq = _safe(spot, 'frequency')
        band = _safe(spot, 'band')
        dx = _safe(spot, 'dxCall')
        spotter = _safe(spot, 'spotter')
        tm = _safe(spot, 'time')
        _blit_text(screen, fonts['body'], str(freq), ACCENT_GOLD, rect.x, y)
        _blit_text(screen, fonts['body'], str(band), BAND_COLORS.get(str(band), TEXT), rect.x + 90, y)
        _blit_text(screen, fonts['body'], str(dx), BRIGHT, rect.x + 140, y)
        _blit_text(screen, fonts['body'], str(spotter)[:10], TEXT, rect.x + 230, y)
        _blit_text(screen, fonts['body'], str(tm), LABEL, rect.x + 340, y)
        y += 16


def draw_band_activity(screen, rect, dxspots, fonts):
    counts = {b: 0 for b in HF_BANDS}
    if isinstance(dxspots, list):
        for spot in dxspots:
            if isinstance(spot, dict):
                b = spot.get('band')
                if b in counts:
                    counts[b] += 1
    vmax = max(counts.values()) if any(counts.values()) else 1
    label_w = 40
    count_w = 36
    row_h = max(14, (rect.h - 4) // len(HF_BANDS))
    y = rect.y + 2
    for band in HF_BANDS:
        c = counts[band]
        _blit_text(screen, fonts['label'], band, LABEL, rect.x, y + 1)
        bar_rect = pygame.Rect(rect.x + label_w, y + 2,
                               max(1, rect.w - label_w - count_w), row_h - 4)
        draw_bar(screen, bar_rect, c, vmax, BAND_COLORS.get(band, TEXT))
        _blit_text(screen, fonts['label'], str(c), BRIGHT,
                   rect.x + rect.w - count_w + 4, y + 1)
        y += row_h


def draw_tabs(screen, rect, tabs, active, fonts):
    """Draw a tab bar across rect.y (height 20). Returns {name: Rect}."""
    regions = {}
    if not tabs:
        return regions
    tw = rect.w // len(tabs)
    for i, name in enumerate(tabs):
        tab_rect = pygame.Rect(rect.x + i * tw, rect.y, tw - 2, 20)
        color = BORDER if name == active else CARD
        pygame.draw.rect(screen, color, tab_rect)
        pygame.draw.rect(screen, BORDER, tab_rect, 1)
        text_color = ACCENT_GOLD if name == active else LABEL
        _blit_text(screen, fonts['panel'], name.upper(), text_color,
                   tab_rect.x + 8, tab_rect.y + 2)
        regions[name] = tab_rect
    return regions


def draw_geomag(screen, rect, solar, fonts):
    kp = _safe(solar, 'kIndex', 0)
    try:
        kp_val = float(kp)
    except Exception:
        kp_val = 0.0
    color = STATUS_GREEN if kp_val < 4 else STATUS_YELLOW if kp_val < 6 else STATUS_RED
    _blit_text(screen, fonts['body'], 'Kp {}'.format(kp), BRIGHT, rect.x, rect.y + 2)
    bar_rect = pygame.Rect(rect.x, rect.y + 20, rect.w, 10)
    draw_bar(screen, bar_rect, kp_val, 9.0, color)


def draw_xray(screen, rect, solar, fonts):
    xray = _safe(solar, 'xray', 'A0.0')
    s = str(xray)
    try:
        letter = s[0]
        mag = float(s[1:]) if len(s) > 1 else 0.0
        scale = {'A': 0, 'B': 1, 'C': 2, 'M': 3, 'X': 4}.get(letter.upper(), 0)
        value = scale + (mag / 10.0)
    except Exception:
        value = 0.0
    color = STATUS_GREEN if value < 2 else STATUS_YELLOW if value < 3 else STATUS_RED
    _blit_text(screen, fonts['body'], s, BRIGHT, rect.x, rect.y + 2)
    bar_rect = pygame.Rect(rect.x, rect.y + 20, rect.w, 10)
    draw_bar(screen, bar_rect, value, 5.0, color)


def draw_open_bands(screen, rect, bands, fonts):
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
               STATUS_GREEN, rect.x, rect.y)
    _blit_text(screen, fonts['label'], 'CLOSED: ' + (', '.join(closes) or '--'),
               STATUS_RED, rect.x, rect.y + 16)


def draw_status_bar(screen, rect, data, fonts):
    pygame.draw.rect(screen, CARD, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
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
    _blit_text(screen, fonts['small'], text, LABEL, rect.x + 6, rect.y + 4)
    _blit_text(screen, fonts['small'], 'ESC/Q to quit', LABEL,
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


def main():
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

    clock = pygame.time.Clock()
    running = True
    while running:
        for event in pygame.event.get():
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
                        break

        sw, sh = screen.get_size()
        screen.fill(BG)

        header = pygame.Rect(0, 0, sw, 30)
        callsign = os.environ.get('HAMCLOCK_CALLSIGN', 'N0CALL')
        draw_header(screen, header, callsign, fonts)

        status = pygame.Rect(0, sh - 20, sw, 20)
        draw_status_bar(screen, status, data, fonts)

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
            inner = draw_panel(screen, r, t, fonts)
            panel_rects.append(inner)
            cy += h + panel_gap

        try:
            draw_solar(screen, panel_rects[0], data.solar or {}, fonts)
        except Exception:
            pass
        try:
            draw_bands(screen, panel_rects[1], data.bands or {}, fonts)
        except Exception:
            pass
        try:
            sdo_surf = _get_cached_image(data, 'solar-image', image_cache, image_cache_ts)
            draw_image(screen, panel_rects[2], sdo_surf)
        except Exception:
            pass
        try:
            draw_geomag(screen, panel_rects[3], data.solar or {}, fonts)
        except Exception:
            pass
        try:
            draw_xray(screen, panel_rects[4], data.solar or {}, fonts)
        except Exception:
            pass
        try:
            draw_open_bands(screen, panel_rects[5], data.bands or {}, fonts)
        except Exception:
            pass

        # ---- MIDDLE COLUMN ----
        mx = lx + left_w
        mid_rect = pygame.Rect(mx, content_top, mid_w - 4, content_h)
        mid_inner = draw_panel(screen, mid_rect, 'MUF STATUS', fonts)
        try:
            draw_muf_text(screen, mid_inner, data.solar or {}, fonts)
        except Exception:
            pass

        # ---- RIGHT COLUMN ----
        rx = mx + mid_w
        rh_dx = int(content_h * 0.28)
        rh_ba = int(content_h * 0.32)
        rh_prop = content_h - rh_dx - rh_ba - panel_gap * 2

        dx_r = pygame.Rect(rx, content_top, right_w - 4, rh_dx)
        dx_inner = draw_panel(screen, dx_r, 'DX SPOTS', fonts)
        try:
            draw_dx_spots(screen, dx_inner, data.dxspots or [], fonts)
        except Exception:
            pass

        ba_r = pygame.Rect(rx, content_top + rh_dx + panel_gap, right_w - 4, rh_ba)
        ba_inner = draw_panel(screen, ba_r, 'BAND ACTIVITY', fonts)
        try:
            draw_band_activity(screen, ba_inner, data.dxspots or [], fonts)
        except Exception:
            pass

        prop_r = pygame.Rect(rx, content_top + rh_dx + rh_ba + panel_gap * 2,
                             right_w - 4, rh_prop)
        prop_inner = draw_panel(screen, prop_r, 'PROPAGATION', fonts)
        tab_bar = pygame.Rect(prop_inner.x, prop_inner.y, prop_inner.w, 20)
        tab_regions = draw_tabs(screen, tab_bar, ['drap', 'aurora', 'enlil'],
                                active_tab, fonts)
        img_rect = pygame.Rect(prop_inner.x, prop_inner.y + 24,
                               prop_inner.w, prop_inner.h - 24)
        try:
            key = tab_image_key.get(active_tab, 'real-drap')
            surf = _get_cached_image(data, key, image_cache, image_cache_ts)
            draw_image(screen, img_rect, surf)
        except Exception:
            pass

        pygame.display.flip()
        clock.tick(10)

    try:
        data.stop()
    except Exception:
        pass
    pygame.quit()


if __name__ == '__main__':
    main()
