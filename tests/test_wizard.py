import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import pygame
import pytest

pygame.init()

from hamclock_pygame import TextField, validate_callsign


THEME = {
    "bg": (10, 10, 10), "card": (30, 30, 30), "fg": (240, 240, 240),
    "muted": (130, 130, 130), "label": (200, 200, 200),
    "accent": (244, 197, 92), "good": (34, 197, 94),
    "fair": (234, 179, 8), "poor": (239, 68, 68),
    "band_palette": [(0, 0, 0)] * 10, "sdo_accent": (255, 255, 255),
}


def _key(key, unicode="", mod=0):
    return pygame.event.Event(pygame.KEYDOWN,
                              {"key": key, "unicode": unicode, "mod": mod})


def test_textfield_typing_appends_to_text():
    tf = TextField(pygame.Rect(0, 0, 200, 40), initial="", max_len=16)
    assert tf.text == ""
    assert tf.handle_event(_key(pygame.K_w, "W")) is None
    assert tf.handle_event(_key(pygame.K_1, "1")) is None
    assert tf.handle_event(_key(pygame.K_a, "A")) is None
    assert tf.text == "W1A"
    assert tf.cursor == 3


def test_textfield_backspace_removes_char():
    tf = TextField(pygame.Rect(0, 0, 200, 40), initial="W1AB")
    tf.cursor = 4
    tf.handle_event(_key(pygame.K_BACKSPACE))
    assert tf.text == "W1A"
    assert tf.cursor == 3


def test_textfield_max_len_enforced():
    tf = TextField(pygame.Rect(0, 0, 200, 40), initial="", max_len=3)
    for c in "WXYZ":
        tf.handle_event(_key(pygame.K_w, c))
    assert tf.text == "WXY"


def test_textfield_tab_returns_next():
    tf = TextField(pygame.Rect(0, 0, 200, 40))
    assert tf.handle_event(_key(pygame.K_TAB)) == "next"


def test_textfield_enter_returns_submit():
    tf = TextField(pygame.Rect(0, 0, 200, 40))
    assert tf.handle_event(_key(pygame.K_RETURN)) == "submit"


def test_textfield_escape_returns_cancel():
    tf = TextField(pygame.Rect(0, 0, 200, 40))
    assert tf.handle_event(_key(pygame.K_ESCAPE)) == "cancel"


def test_textfield_validator_sets_error():
    tf = TextField(pygame.Rect(0, 0, 200, 40),
                   initial="W1ABC", validator=validate_callsign)
    tf.handle_event(_key(pygame.K_RETURN))
    assert tf.error == ""
    tf.text = "123456"
    tf.cursor = 6
    tf.handle_event(_key(pygame.K_RETURN))
    assert tf.error != ""


def test_textfield_draw_does_not_raise():
    surf = pygame.Surface((400, 100))
    tf = TextField(pygame.Rect(0, 0, 200, 40), initial="W1ABC", label="Call")
    tf.draw(surf, THEME, focused=True)
    tf.draw(surf, THEME, focused=False)
