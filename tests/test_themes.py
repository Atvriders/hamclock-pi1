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
