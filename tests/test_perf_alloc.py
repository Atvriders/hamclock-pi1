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
