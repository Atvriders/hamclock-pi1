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
