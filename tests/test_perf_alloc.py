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

        for _ in range(30):
            hamclock_pygame.draw_image(screen, rect, None, fonts)

        assert alloc_count['n'] == baseline, \
            'draw_image allocated %d Font objects in loading state' % (
                alloc_count['n'] - baseline)
    finally:
        pygame.quit()
