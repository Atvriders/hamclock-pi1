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


def test_make_fonts_includes_tiny():
    import pygame
    pygame.init()
    try:
        import hamclock_pygame
        fonts = hamclock_pygame._make_fonts()
        assert 'tiny' in fonts, '_make_fonts() must produce a "tiny" font'
        assert fonts['tiny'].render('x', True, (255, 255, 255)) is not None
    finally:
        pygame.quit()


def test_draw_image_loading_branch_no_inline_font_construction():
    """draw_image's loading branch must not allocate a fresh Font per frame.

    pygame.font.Font is a C-extension immutable type so we cannot monkey-patch
    Font.__init__ to count allocations directly. Instead, verify the spec by
    static inspection: draw_image's body must not contain a Font(...) literal.
    The 'tiny' font is cached by _make_fonts and reused.
    """
    import inspect
    import re
    import hamclock_pygame
    src = inspect.getsource(hamclock_pygame.draw_image)
    assert not re.search(r"pygame\.font\.Font\s*\(", src), \
        "draw_image still constructs a pygame.font.Font inline:\n%s" % src
    assert "Font(None" not in src, \
        "draw_image still references Font(None, ...) inline:\n%s" % src


def test_draw_image_loading_branch_uses_cached_tiny_font(monkeypatch):
    """Smoke: drive the loading branch 30 times; passing fonts dict reuses
    fonts['tiny'] without raising. If the implementation regressed to inline
    Font allocation, this still passes — but the static check above catches it.
    """
    import pygame
    pygame.init()
    try:
        import hamclock_pygame
        fonts = hamclock_pygame._make_fonts()
        assert "tiny" in fonts
        screen = pygame.Surface((200, 100))
        rect = pygame.Rect(0, 0, 200, 100)
        for _ in range(30):
            hamclock_pygame.draw_image(screen, rect, None, fonts)
    finally:
        pygame.quit()


def test_glyph_cache_hit_rate(monkeypatch):
    """Repeated _blit_text of the same (font,text,color) hits cache."""
    import pygame
    pygame.init()
    try:
        import hamclock_pygame
        fonts = hamclock_pygame._make_fonts()
        screen = pygame.Surface((400, 100))
        hamclock_pygame._glyph_cache.clear()
        # 100 repeated identical labels — should only populate 1 cache entry.
        for _ in range(100):
            hamclock_pygame._blit_text(screen, fonts['panel'],
                                       'SOLAR', (255, 255, 255), 0, 0)
        assert len(hamclock_pygame._glyph_cache) == 1, \
            'expected 1 cache entry; got %d' % len(hamclock_pygame._glyph_cache)
    finally:
        pygame.quit()


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
    finally:
        pygame.quit()


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


def test_scaled_cache_avoids_repeat_smoothscale(monkeypatch):
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
    finally:
        pygame.quit()


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
    finally:
        pygame.quit()


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
    finally:
        pygame.quit()


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
    finally:
        pygame.quit()


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


def test_render_loop_30_frame_alloc_budget(monkeypatch):
    """30-frame headless render: smoothscale <= N_visible_images,
    no Font(None, 18) literal in draw_image source, glyph hit rate >= 90%.
    """
    import inspect, re
    import pygame
    pygame.init()
    try:
        import hamclock_pygame

        calls = {'smoothscale': 0, 'render': 0}

        real_ss = pygame.transform.smoothscale
        def counting_ss(surf, size, *a, **kw):
            calls['smoothscale'] += 1
            return real_ss(surf, size, *a, **kw)
        monkeypatch.setattr(pygame.transform, 'smoothscale', counting_ss)

        # pygame.font.Font is an immutable C type; class and instance .render
        # cannot be monkeypatched. Wrap each font in the fonts dict with a
        # proxy that counts render calls and forwards everything else.
        class _CountingFont:
            def __init__(self, inner):
                self._inner = inner
            def render(self, *a, **kw):
                calls['render'] += 1
                return self._inner.render(*a, **kw)
            def __getattr__(self, name):
                return getattr(self._inner, name)

        # Static-source check: draw_image must not construct Font inline.
        di_src = inspect.getsource(hamclock_pygame.draw_image)
        assert not re.search(r"pygame\.font\.Font\s*\(", di_src), \
            'draw_image still constructs Font inline'

        hamclock_pygame._scaled_cache.clear()
        hamclock_pygame._glyph_cache.clear()
        raw_fonts = hamclock_pygame._make_fonts()
        fonts = {k: _CountingFont(v) for k, v in raw_fonts.items()}
        screen = pygame.Surface((1440, 900))
        src_img = pygame.Surface((800, 600))
        rect_sdo = pygame.Rect(0, 30, 280, 250)
        rect_prop = pygame.Rect(1100, 600, 340, 280)

        # Reset render counter AFTER fonts built (SysFont init may call render).
        calls['render'] = 0

        for frame in range(30):
            hamclock_pygame.draw_image(screen, rect_sdo, src_img, fonts,
                                       image_key='solar-image', fetched_at=1000.0)
            hamclock_pygame.draw_image(screen, rect_prop, src_img, fonts,
                                       image_key='real-drap', fetched_at=1000.0)
            for label in ('SOLAR', 'BANDS', 'SDO IMAGE', 'OPEN:', 'CLOSED:',
                          'SFI', 'Kp', 'A', 'FREQ', 'BND'):
                hamclock_pygame._blit_text(screen, fonts['panel'], label,
                                           (255, 255, 255), 0, 0)

        assert calls['smoothscale'] <= 2, \
            'smoothscale ran %d times over 30 frames (expected <= 2)' % calls['smoothscale']
        # 30 frames * 10 labels = 300 _blit_text calls; first frame: 10 cache
        # misses, remaining 29 frames hit. Expected render count = 10.
        assert calls['render'] == 10, \
            'glyph cache: expected 10 renders, got %d' % calls['render']
        hit_rate = 1.0 - (calls['render'] / 300.0)
        assert hit_rate >= 0.90, 'glyph cache hit rate %.2f < 0.90' % hit_rate
    finally:
        pygame.quit()


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
