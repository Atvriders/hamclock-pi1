"""Tests for the --inject-events debug flag.

The flag must be:
  - gated by HAMCLOCK_DEBUG=1 (argparse errors otherwise),
  - read a JSON list of event dicts (one per frame),
  - convert them into pygame events the render loop can consume.
"""
import json
import os
import pygame
import pytest

import hamclock_pygame


def test_parse_args_rejects_inject_without_debug_env(monkeypatch):
    monkeypatch.delenv('HAMCLOCK_DEBUG', raising=False)
    with pytest.raises(SystemExit):
        hamclock_pygame._parse_args(['--inject-events', '/tmp/x.json'])


def test_parse_args_accepts_inject_with_debug_env(monkeypatch, tmp_path):
    monkeypatch.setenv('HAMCLOCK_DEBUG', '1')
    p = tmp_path / 'events.json'
    p.write_text('[]')
    args = hamclock_pygame._parse_args(['--inject-events', str(p)])
    assert args.inject_events == str(p)


def test_parse_args_no_inject_is_fine_without_debug(monkeypatch):
    monkeypatch.delenv('HAMCLOCK_DEBUG', raising=False)
    args = hamclock_pygame._parse_args([])
    assert args.inject_events is None


def test_load_injected_events_translates_mousebuttondown(tmp_path):
    p = tmp_path / 'events.json'
    p.write_text(json.dumps([
        {'type': 'MOUSEBUTTONDOWN', 'pos': [100, 200], 'button': 1},
        {'type': 'KEYDOWN', 'key': 'q'},
    ]))
    pygame.init()
    try:
        events = hamclock_pygame._load_injected_events(str(p))
        assert len(events) == 2
        assert events[0].type == pygame.MOUSEBUTTONDOWN
        assert events[0].pos == (100, 200)
        assert events[0].button == 1
        assert events[1].type == pygame.KEYDOWN
        assert events[1].key == pygame.K_q
    finally:
        pygame.quit()


def test_inject_events_iterator_pops_one_per_frame(tmp_path):
    p = tmp_path / 'events.json'
    p.write_text(json.dumps([
        {'type': 'KEYDOWN', 'key': 'q'},
        {'type': 'KEYDOWN', 'key': 'q'},
    ]))
    pygame.init()
    try:
        events = hamclock_pygame._load_injected_events(str(p))
        it = hamclock_pygame._inject_event_iter(events)
        frame1 = next(it)
        frame2 = next(it)
        frame3 = next(it)
        assert len(frame1) == 1
        assert len(frame2) == 1
        assert frame3 == []  # exhausted
    finally:
        pygame.quit()
