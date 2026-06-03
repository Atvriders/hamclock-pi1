"""Verify conftest.py sets the env vars all later phases depend on."""
import os


def test_sdl_videodriver_is_dummy():
    assert os.environ.get("SDL_VIDEODRIVER") == "dummy"


def test_hamclock_debug_is_one():
    assert os.environ.get("HAMCLOCK_DEBUG") == "1"


def test_pygame_importable_under_dummy_driver():
    import pygame
    pygame.display.init()
    try:
        assert pygame.display.get_driver() == "dummy"
    finally:
        pygame.display.quit()
