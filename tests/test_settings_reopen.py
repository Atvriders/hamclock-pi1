"""Changing settings after first setup, from the device.

Until now the wizard ran only when settings.json was absent. Once a Pi was
set up, the operator could never change their NTP server, timezone or theme
from the device again — the only route was an SSH session and a text editor,
on a kiosk that may have neither a shell nor a working clock. 'S' re-opens the
same wizard over the live values.

The privilege model does not change: the wizard runs as the unprivileged
service user, writes settings.json, and then creates an empty
/run/hamclock-lite/settings.request. Root re-reads the file, re-validates the
value from scratch and applies it. Nothing here writes a root-owned path, and
nothing here needs a restart.
"""
import json
import os

import pygame
import pytest

import hamclock_pygame as hp


@pytest.fixture
def rundir(tmp_path, monkeypatch):
    d = tmp_path / "run"
    d.mkdir()
    monkeypatch.setattr(hp, "RUN_DIR", str(d))
    return d


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setattr(hp, "SETTINGS_PATH", str(p))
    return p


CURRENT = {"callsign": "W1ABC", "timezone": "America/Chicago",
           "theme": "kstate", "ntp": "old.example.com"}


def _fonts():
    pygame.font.init()
    return {
        "tiny": pygame.font.Font(None, 14),
        "small": pygame.font.Font(None, 18),
        "med": pygame.font.Font(None, 24),
        "lg": pygame.font.Font(None, 36),
    }


def _screen(size=(720, 450)):
    pygame.init()
    pygame.font.init()
    return pygame.display.set_mode(size)


# ------------------------------------------------- the wizard, pre-filled

def _run_wizard(tmp_path, monkeypatch, seq, initial, **kwargs):
    p = tmp_path / "events.json"
    p.write_text(json.dumps(seq))
    monkeypatch.setenv("HAMCLOCK_DEBUG", "1")
    monkeypatch.setenv("HAMCLOCK_INJECT_EVENTS", str(p))
    kwargs.setdefault("allow_cancel", True)
    kwargs.setdefault("wait_ntp", False)
    return hp.setup_screen(_screen(), _fonts(), hp.THEMES["kstate"],
                           initial=initial, **kwargs)


def _tab(seq, n=1):
    for _ in range(n):
        seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})


def _enter(seq):
    seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})


def test_the_wizard_opens_with_the_current_values_and_round_trips_them(
        tmp_path, monkeypatch):
    """Tab straight through to Save and change nothing: what comes back must
    be exactly what went in, including a theme that is not the default."""
    initial = dict(CURRENT, theme="amber")
    seq = []
    _tab(seq, 4)          # callsign -> timezone -> ntp -> theme -> Save
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq, initial)
    assert result == {"callsign": "W1ABC",
                      "timezone": "America/Chicago",
                      "theme": "amber",
                      "ntp": "old.example.com"}


def test_the_ntp_server_can_be_changed_from_the_device(tmp_path, monkeypatch):
    seq = []
    _tab(seq, 2)          # onto the NTP field
    seq.append({"type": "KEYDOWN", "key": "K_HOME", "unicode": ""})
    for _ in range(len(CURRENT["ntp"])):
        seq.append({"type": "KEYDOWN", "key": "K_DELETE", "unicode": ""})
    for ch in "ntp1.internal.lan":
        seq.append({"type": "KEYDOWN", "key": "K_a", "unicode": ch})
    _tab(seq, 2)
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq, CURRENT)
    assert result["ntp"] == "ntp1.internal.lan"
    assert result["callsign"] == "W1ABC", "an unrelated field was disturbed"


def test_the_ntp_server_can_be_cleared_back_to_the_distro_default(
        tmp_path, monkeypatch):
    seq = []
    _tab(seq, 2)
    seq.append({"type": "KEYDOWN", "key": "K_END", "unicode": ""})
    for _ in range(len(CURRENT["ntp"])):
        seq.append({"type": "KEYDOWN", "key": "K_BACKSPACE", "unicode": ""})
    _tab(seq, 2)
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq, CURRENT)
    assert result["ntp"] == ""


def test_escape_leaves_everything_as_it_was_instead_of_killing_the_kiosk(
        tmp_path, monkeypatch):
    """First boot exits on ESC because there is nothing to fall back to.
    A dashboard that has been running for a month must not."""
    seq = [{"type": "KEYDOWN", "key": "K_ESCAPE", "unicode": ""}]
    assert _run_wizard(tmp_path, monkeypatch, seq, CURRENT) is None


def test_first_boot_still_exits_on_escape(tmp_path, monkeypatch):
    seq = [{"type": "KEYDOWN", "key": "K_ESCAPE", "unicode": ""}]
    with pytest.raises(SystemExit):
        _run_wizard(tmp_path, monkeypatch, seq, None, allow_cancel=False)


def test_walking_away_from_a_reopened_wizard_changes_nothing(tmp_path,
                                                             monkeypatch):
    """The injected sequence runs out, which the wizard sees as QUIT. Half
    typed values must not be saved over good ones."""
    seq = []
    for ch in "XX":       # start mangling the callsign, then stop
        seq.append({"type": "KEYDOWN", "key": "K_x", "unicode": ch})
    assert _run_wizard(tmp_path, monkeypatch, seq, CURRENT) is None


# ------------------------------------------------------------ _reopen_setup

def test_saving_writes_settings_and_asks_root_to_apply_the_ntp_server(
        monkeypatch, rundir, settings_file):
    new = dict(CURRENT, ntp="a.example.com b.example.com", theme="blue")
    monkeypatch.setattr(hp, "setup_screen", lambda *a, **kw: new)
    result, note, color = hp._reopen_setup(_screen(), _fonts(),
                                           hp.THEMES["kstate"], CURRENT)
    assert result == new
    assert json.loads(settings_file.read_text())["ntp"] == \
        "a.example.com b.example.com"
    assert (rundir / "settings.request").exists(), \
        "settings were saved but root was never asked to apply them"
    assert (rundir / "settings.request").read_bytes() == b""
    assert color == "good" and note


def test_the_wizard_is_opened_over_the_live_settings(monkeypatch, rundir,
                                                     settings_file):
    seen = {}

    def fake(screen, fonts, theme, initial=None, allow_cancel=False,
             wait_ntp=True):
        seen['initial'] = initial
        seen['allow_cancel'] = allow_cancel
        seen['wait_ntp'] = wait_ntp
        return dict(CURRENT)
    monkeypatch.setattr(hp, "setup_screen", fake)
    hp._reopen_setup(_screen(), _fonts(), hp.THEMES["kstate"], CURRENT)
    assert seen['initial'] == CURRENT, "the wizard opened empty"
    assert seen['allow_cancel'] is True, "ESC would have killed the dashboard"
    assert seen['wait_ntp'] is False, \
        "an operator at the keyboard was made to wait for a clock sync"


def test_cancelling_touches_neither_settings_nor_run(monkeypatch, rundir,
                                                     settings_file):
    settings_file.write_text(json.dumps(CURRENT))
    monkeypatch.setattr(hp, "setup_screen", lambda *a, **kw: None)
    result, note, color = hp._reopen_setup(_screen(), _fonts(),
                                           hp.THEMES["kstate"], CURRENT)
    assert result is None
    assert json.loads(settings_file.read_text()) == CURRENT
    assert list(rundir.iterdir()) == []


def test_the_diagnostics_report_id_survives_a_settings_change(monkeypatch,
                                                              rundir,
                                                              settings_file):
    """Reports from this Pi must still correlate after the operator edits
    their timezone."""
    live = dict(CURRENT)
    live[hp.DEVICE_ID_KEY] = "abc123"
    monkeypatch.setattr(hp, "setup_screen",
                        lambda *a, **kw: dict(CURRENT, timezone="UTC"))
    result, _n, _c = hp._reopen_setup(_screen(), _fonts(),
                                      hp.THEMES["kstate"], live)
    assert result[hp.DEVICE_ID_KEY] == "abc123"
    assert json.loads(settings_file.read_text())[hp.DEVICE_ID_KEY] == "abc123"


def test_a_wizard_that_explodes_does_not_take_the_dashboard_with_it(
        monkeypatch, rundir, settings_file):
    def boom(*a, **kw):
        raise RuntimeError("simulated SDL failure")
    monkeypatch.setattr(hp, "setup_screen", boom)
    result, note, color = hp._reopen_setup(_screen(), _fonts(),
                                           hp.THEMES["kstate"], CURRENT)
    assert result is None and color == "poor"
    assert list(rundir.iterdir()) == []


def test_an_unwritable_settings_file_does_not_ask_root_to_apply_anything(
        monkeypatch, rundir):
    monkeypatch.setattr(hp, "setup_screen", lambda *a, **kw: dict(CURRENT))
    monkeypatch.setattr(hp, "SETTINGS_PATH", "/proc/nope/settings.json")
    result, _note, color = hp._reopen_setup(_screen(), _fonts(),
                                            hp.THEMES["kstate"], CURRENT)
    assert result is None and color == "poor"
    assert not (rundir / "settings.request").exists(), \
        "asked root to apply a value that was never saved"


def test_reopening_never_calls_sudo_or_writes_a_root_owned_path(monkeypatch,
                                                                rundir,
                                                                settings_file):
    import subprocess
    touched = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: touched.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: touched.append(a))
    monkeypatch.setattr(os, "system", lambda *a: touched.append(a))
    monkeypatch.setattr(hp, "setup_screen",
                        lambda *a, **kw: dict(CURRENT, ntp="pool.ntp.org"))
    hp._reopen_setup(_screen(), _fonts(), hp.THEMES["kstate"], CURRENT)
    assert touched == [], "the unprivileged side ran something: %r" % touched
    # The only thing it may create is the flag file.
    assert [p.name for p in rundir.iterdir()] == ["settings.request"]


# -------------------------------------------------- through the render loop

class _StubData:
    def __init__(self):
        self.server_url = 'http://localhost:8080'
        self.solar = {}
        self.bands = {}
        self.dxspots = []
        self.images = {}
        self.image_fetched_at = {}
        self.image_fail_streak = {}
        self.image_next_due = {}
        self.health = {}
        self.last_data_refresh = 0.0
        self.last_image_refresh = 0.0

    def start_background(self, *a, **kw):
        pass

    def stop(self):
        pass


def _drive(monkeypatch, events, settings):
    monkeypatch.setattr(hp, 'HamClockData', _StubData)

    def gen():
        for ev in events:
            yield ev
        yield [pygame.event.Event(pygame.QUIT)]

    pygame.init()
    pygame.font.init()
    scr = pygame.display.set_mode((720, 450))
    try:
        hp._run_render_loop(scr, hp._make_fonts(), dict(hp.THEMES['kstate']),
                            settings, injected_iter=gen())
    finally:
        pygame.init()
        pygame.font.init()


def test_s_on_the_dashboard_reopens_the_wizard(monkeypatch, rundir,
                                               settings_file):
    calls = []

    def fake(screen, fonts, theme, initial=None, **kw):
        calls.append(initial)
        return dict(CURRENT, ntp="pool.ntp.org")
    monkeypatch.setattr(hp, "setup_screen", fake)
    _drive(monkeypatch,
           [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s)], [], []],
           dict(CURRENT))
    assert len(calls) == 1, "'S' did not open the wizard"
    assert calls[0]["callsign"] == "W1ABC", "it opened without the live values"
    assert json.loads(settings_file.read_text())["ntp"] == "pool.ntp.org"
    assert (rundir / "settings.request").exists()


def test_the_dashboard_keeps_running_afterwards(monkeypatch, rundir,
                                                settings_file):
    """No restart: the loop must come back to the dashboard, and later events
    must still be handled."""
    monkeypatch.setattr(hp, "setup_screen",
                        lambda *a, **kw: dict(CURRENT, theme="amber"))
    seen = []
    real_open = hp._report_open
    monkeypatch.setattr(hp, "_report_open",
                        lambda *a, **kw: seen.append('t') or False)
    _drive(monkeypatch,
           [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s)],
            [],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_t)],
            []],
           dict(CURRENT))
    assert seen == ['t'], "the loop stopped handling input after the wizard"
    assert json.loads(settings_file.read_text())["theme"] == "amber"


def test_a_theme_change_takes_effect_without_a_restart(monkeypatch, rundir,
                                                       settings_file):
    monkeypatch.setattr(hp, "setup_screen",
                        lambda *a, **kw: dict(CURRENT, theme="classic"))
    drawn = []
    real_bar = hp.draw_status_bar

    def spy(screen, rect, data, fonts, theme, **kw):
        drawn.append(theme['bg'])
        return real_bar(screen, rect, data, fonts, theme, **kw)
    monkeypatch.setattr(hp, "draw_status_bar", spy)
    _drive(monkeypatch,
           [[], [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s)], [], []],
           dict(CURRENT))
    assert hp.THEMES['classic']['bg'] in drawn, \
        "the dashboard kept painting the old palette after a theme change"


def test_cancelling_from_the_dashboard_leaves_settings_alone(monkeypatch,
                                                             rundir,
                                                             settings_file):
    settings_file.write_text(json.dumps(CURRENT))
    monkeypatch.setattr(hp, "setup_screen", lambda *a, **kw: None)
    _drive(monkeypatch,
           [[pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s)], [], []],
           dict(CURRENT))
    assert json.loads(settings_file.read_text()) == CURRENT
    assert list(rundir.iterdir()) == []


def test_first_boot_asks_root_to_apply_the_ntp_server(monkeypatch, rundir,
                                                      tmp_path):
    """The wizard can write settings.json but not the timesyncd drop-in, so
    a first boot that collects an NTP server and never asks is a setting that
    silently does nothing."""
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(hp, "SETTINGS_PATH", str(settings_file))
    monkeypatch.setattr(hp, "setup_screen",
                        lambda *a, **kw: dict(CURRENT, ntp="pool.ntp.org"))
    monkeypatch.setattr(hp, "_run_render_loop",
                        lambda *a, **kw: (_ for _ in ()).throw(SystemExit(0)))
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    with pytest.raises(SystemExit):
        hp.main([])
    assert (rundir / "settings.request").exists()
