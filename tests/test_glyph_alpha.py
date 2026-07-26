"""Antialiased glyphs must keep their alpha channel through the glyph cache.

Found while eyeballing the Tier 1.1 geometry work: 'HAMCLOCK LITE' in the
header and all five MUF STATUS values rendered as solid filled rectangles,
not text.

Cause: _make_fonts enables antialiasing for the 'title' font only (the Tier-1a
perf compromise — AA costs 5-10x on ARMv6, so the small faces render flat).
pygame's Font.render(text, True, colour) returns a 32-bit SRCALPHA surface
whose RGB is the text colour on EVERY pixel: the glyph shape exists purely in
the alpha channel. _blit_text then did surf.convert(display) to pre-pay the
per-blit format conversion — and convert() drops per-pixel alpha, turning the
whole glyph box opaque in the text colour.

The flat (AA=False) faces render as an 8-bit colourkeyed surface and are
unaffected, which is why only the two title-font call sites were visibly
broken.
"""
import pygame
import pytest

import hamclock_pygame as hp


@pytest.fixture(scope='module')
def display():
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((720, 450))
    yield screen
    pygame.display.quit()


def _ink_ratio(surf, rect, color):
    """Fraction of pixels inside `rect` painted exactly `color`."""
    hits = 0
    total = 0
    for y in range(rect.top, rect.bottom):
        for x in range(rect.left, rect.right):
            total += 1
            if surf.get_at((x, y))[:3] == tuple(color):
                hits += 1
    return hits / float(total or 1)


def test_render_of_an_aa_font_is_alpha_only(display):
    """Pins the premise this whole file rests on."""
    f = pygame.font.SysFont('monospace', 13)
    surf = f.render('9.8 MHz', True, (255, 255, 255))
    assert surf.get_flags() & pygame.SRCALPHA, (
        'AA render is expected to carry per-pixel alpha')
    assert _ink_ratio(surf, surf.get_rect(), (255, 255, 255)) == 1.0, (
        'AA render is expected to be uniform RGB with the shape in alpha')


def test_title_font_text_is_not_a_solid_block(display):
    """The regression itself, through the real _blit_text path."""
    fonts = hp._make_fonts()
    theme = hp.THEMES['kstate']
    screen = pygame.Surface((200, 30))
    screen.fill(theme['card'])
    hp._glyph_cache.clear()
    hp._blit_text(screen, fonts['title'], '9.8 MHz', theme['bright'], 2, 2)
    box = pygame.Rect(2, 2, fonts['title'].size('9.8 MHz')[0],
                      fonts['title'].get_height())
    ink = _ink_ratio(screen, box, theme['bright'])
    assert ink < 0.75, (
        'the glyph box is %.0f%% solid %r — the antialiased surface lost its '
        'alpha channel and painted as a filled rectangle'
        % (ink * 100, tuple(theme['bright'])))
    assert ink > 0.0, 'nothing was painted at all'


def test_flat_fonts_still_render(display):
    """The AA=False faces (everything except 'title') must be unaffected."""
    fonts = hp._make_fonts()
    theme = hp.THEMES['kstate']
    for name in ('panel', 'body', 'label', 'small', 'tiny'):
        screen = pygame.Surface((200, 30))
        screen.fill(theme['card'])
        hp._glyph_cache.clear()
        hp._blit_text(screen, fonts[name], 'Kp 3', theme['bright'], 2, 2)
        box = pygame.Rect(2, 2, fonts[name].size('Kp 3')[0],
                          fonts[name].get_height())
        ink = _ink_ratio(screen, box, theme['bright'])
        assert 0.0 < ink < 0.75, (
            "font %r painted %.0f%% solid ink" % (name, ink * 100))


def test_cached_glyph_paints_the_same_as_the_first_blit(display):
    """The cache stores the converted surface, so a hit must look like a miss."""
    fonts = hp._make_fonts()
    theme = hp.THEMES['kstate']
    first = pygame.Surface((200, 30))
    first.fill(theme['card'])
    hp._glyph_cache.clear()
    hp._blit_text(first, fonts['title'], 'HAMCLOCK LITE', theme['accent'], 2, 2)

    second = pygame.Surface((200, 30))
    second.fill(theme['card'])
    hp._blit_text(second, fonts['title'], 'HAMCLOCK LITE', theme['accent'], 2, 2)

    assert (pygame.image.tostring(first, 'RGB')
            == pygame.image.tostring(second, 'RGB')), (
        'the cached glyph blits differently from the freshly rendered one')
