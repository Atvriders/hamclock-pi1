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


import json
import tempfile
import pathlib


def test_load_settings_returns_defaults_when_file_missing(tmp_path):
    missing = tmp_path / 'nope.json'
    d = hamclock_pygame.load_settings(str(missing))
    assert d == {
        'callsign': '',
        'timezone': 'UTC',
        'theme': 'kstate',
        'ntp': '',
    }


def test_load_settings_returns_defaults_when_json_malformed(tmp_path, capsys):
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json')
    d = hamclock_pygame.load_settings(str(bad))
    assert d['theme'] == 'kstate'
    assert d['timezone'] == 'UTC'
    err = capsys.readouterr().err
    assert 'settings' in err.lower()


def test_load_settings_returns_defaults_when_theme_unknown(tmp_path):
    bad = tmp_path / 's.json'
    bad.write_text(json.dumps({
        'callsign': 'W1ABC', 'timezone': 'UTC',
        'theme': 'mystery', 'ntp': '',
    }))
    d = hamclock_pygame.load_settings(str(bad))
    assert d['theme'] == 'kstate'


def test_load_settings_returns_file_contents_when_valid(tmp_path):
    good = tmp_path / 's.json'
    payload = {
        'callsign': 'W1ABC', 'timezone': 'America/Chicago',
        'theme': 'classic', 'ntp': 'pool.ntp.org',
    }
    good.write_text(json.dumps(payload))
    d = hamclock_pygame.load_settings(str(good))
    assert d == payload


def test_load_settings_fills_missing_keys_with_defaults(tmp_path):
    partial = tmp_path / 's.json'
    partial.write_text(json.dumps({'theme': 'amber'}))
    d = hamclock_pygame.load_settings(str(partial))
    assert d['theme'] == 'amber'
    assert d['callsign'] == ''
    assert d['timezone'] == 'UTC'
    assert d['ntp'] == ''


def test_load_settings_retries_once_on_json_decode_error(tmp_path, monkeypatch):
    """Mid-write race: first read raises JSONDecodeError, second succeeds."""
    p = tmp_path / 's.json'
    p.write_text(json.dumps({
        'callsign': 'K1A', 'timezone': 'UTC',
        'theme': 'blue', 'ntp': '',
    }))
    real_open = open
    calls = {'n': 0}
    def flaky_open(path, *a, **kw):
        if str(path) == str(p) and calls['n'] == 0:
            calls['n'] += 1
            # First call returns a file whose contents trip JSONDecodeError.
            import io
            return io.StringIO('')
        return real_open(path, *a, **kw)
    monkeypatch.setattr('builtins.open', flaky_open)
    d = hamclock_pygame.load_settings(str(p))
    assert d['theme'] == 'blue'
    assert calls['n'] == 1


import inspect


def test_draw_panel_signature_takes_theme():
    sig = inspect.signature(hamclock_pygame.draw_panel)
    assert 'theme' in sig.parameters


def test_draw_header_signature_takes_theme():
    sig = inspect.signature(hamclock_pygame.draw_header)
    assert 'theme' in sig.parameters


def test_draw_status_bar_signature_takes_theme():
    sig = inspect.signature(hamclock_pygame.draw_status_bar)
    assert 'theme' in sig.parameters


def test_draw_panel_uses_theme_card_color():
    import pygame
    pygame.init()
    surf = pygame.Surface((200, 100))
    surf.fill((0, 0, 0))
    fonts = hamclock_pygame._make_fonts()
    theme = hamclock_pygame.THEMES['blue']
    rect = pygame.Rect(10, 10, 100, 60)
    hamclock_pygame.draw_panel(surf, rect, 'TEST', fonts, theme)
    # Sample the interior of the panel (below the title bar at y+22).
    px = surf.get_at((50, 50))[:3]
    assert tuple(px) == theme['card'], f'got {tuple(px)}, want {theme["card"]}'
