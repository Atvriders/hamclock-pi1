"""The UPDATE button, and the line it must not cross.

The dashboard runs as an unprivileged service user with no sudo. Offering an
"install the update" button from that process is only defensible because the
button does exactly one thing: create an empty file in /run/hamclock-lite. A
root-owned path unit notices it and hands the actual work to
hamclock-update.sh, which fetches the manifest, verifies the SHA-256, installs,
health-checks and rolls back — all decided on the root side, none of it
influenced by anything this process says.

So the tests here are weighted towards:

  * confirming produces the flag file and NOTHING else — no download, no
    digest check, no install, no subprocess;
  * nothing is requested without an explicit confirm on a box that was
    actually put on screen;
  * the button exists only when there is genuinely something to install;
  * a missing status file is the ordinary state and is silent — the check runs
    once a day, so most Pis have never written one.
"""
import json
import os
import time

import pygame
import pytest

import hamclock_pygame as hp


SIZES = [(720, 450), (1440, 900)]


@pytest.fixture
def rundir(tmp_path, monkeypatch):
    """A stand-in for /run/hamclock-lite that the tests can inspect."""
    d = tmp_path / "run"
    d.mkdir()
    monkeypatch.setattr(hp, "RUN_DIR", str(d))
    return d


def _status(state="available", installed="1.0.0", available="1.1.0",
            detail=""):
    return {"state": state, "installed": installed, "available": available,
            "detail": detail, "checked": "2026-07-26T07:00:00Z"}


def _write_status(rundir, **kw):
    (rundir / "update-status.json").write_text(json.dumps(_status(**kw)))


class _StubData:
    """Enough HamClockData surface for the render loop."""
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


def _drive(monkeypatch, events, settings=None):
    """Run _run_render_loop over `events` (one list per frame) and return the
    updater state dict the loop used."""
    holder = {}
    real_new = hp._new_update_state

    def capture():
        st = real_new()
        holder['state'] = st
        return st
    monkeypatch.setattr(hp, '_new_update_state', capture)
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
                            settings if settings is not None else {},
                            injected_iter=gen())
    finally:
        pygame.init()
        pygame.font.init()
    return holder['state']


# ------------------------------------------------------ reading the status

def test_a_missing_status_file_is_the_normal_state(rundir, capsys):
    """The check runs at 07:00 daily; a Pi that has not reached it has never
    written one. This must not be an error, and must not be logged."""
    assert hp.read_update_status() is None
    assert capsys.readouterr() == ("", ""), \
        "the absent-file case wrote to the console"


@pytest.mark.parametrize("body", [
    "", "   ", "not json at all", "{", '{"state": "available"',
    "[1,2,3]", '"a string"', "null", "12",
])
def test_a_corrupt_status_file_is_handled_silently(rundir, capsys, body):
    (rundir / "update-status.json").write_text(body)
    assert hp.read_update_status() is None
    assert capsys.readouterr() == ("", "")


def test_a_status_file_that_cannot_be_read_is_handled_silently(rundir, capsys,
                                                               monkeypatch):
    def boom(*a, **kw):
        raise OSError("simulated EACCES")
    monkeypatch.setattr(hp, "open", boom, raising=False)
    monkeypatch.setattr("builtins.open", boom)
    assert hp.read_update_status() is None
    assert capsys.readouterr() == ("", "")


def test_a_well_formed_status_is_returned_verbatim(rundir):
    _write_status(rundir, detail="ready")
    d = hp.read_update_status()
    assert d["state"] == "available"
    assert d["installed"] == "1.0.0" and d["available"] == "1.1.0"


def test_polling_a_missing_file_never_raises_or_logs(rundir, capsys):
    st = hp._new_update_state()
    now = 0.0
    for _ in range(50):
        hp._update_poll(st, now=now)
        now += hp.UPDATE_POLL_S + 1
    assert st['status'] is None and st['button'] is None
    assert capsys.readouterr() == ("", "")


def test_the_status_file_is_read_on_a_slow_cadence(rundir, monkeypatch):
    """It is written at most once a day. Re-reading it per frame would be 10
    stats a second on a single-core ARMv6 for a file that never changes."""
    _write_status(rundir)
    reads = []
    real_read = hp.read_update_status
    monkeypatch.setattr(hp, "read_update_status",
                        lambda *a, **kw: reads.append(1) or real_read(*a, **kw))
    st = hp._new_update_state()
    t = 1000.0
    for _ in range(300):          # 30 s of frames at 10 FPS
        hp._update_poll(st, now=t)
        t += 0.1
    assert len(reads) == 1, "read the status file %d times in 30 s" % len(reads)


def test_the_cadence_speeds_up_while_an_update_is_running(rundir):
    _write_status(rundir, state="applying", detail="installing")
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    assert st['next_poll'] == pytest.approx(1000.0 + hp.UPDATE_APPLY_POLL_S)
    assert hp.UPDATE_APPLY_POLL_S < hp.UPDATE_POLL_S


# -------------------------------------------------- when the button exists

@pytest.mark.parametrize("state", [
    "current", "error", "applying", "updated", "rollback", "rolled_back",
    "broken", "rebooting", "", "AVAILABLE", "avail",
])
def test_no_button_for_any_state_but_available(rundir, state):
    _write_status(rundir, state=state)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    assert st['button'] is None, "offered an update while state=%r" % state


def test_the_button_appears_on_available_and_names_the_version(rundir):
    _write_status(rundir, available="1.4.2")
    st = hp._new_update_state()
    assert hp._update_poll(st, now=1000.0) is True
    assert st['button'] is not None
    assert st['button'].startswith("UPDATE")
    assert "1.4.2" in st['button'], \
        "the operator cannot see which version they would install"


def test_the_button_goes_away_once_an_update_has_been_asked_for(rundir):
    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    assert st['button'] is not None
    hp._update_open(st)
    assert st['button'] is None, "the chip is still clickable mid-dialog"
    st['shown'] = True
    hp._update_confirm(st)
    assert st['button'] is None, "a second update could be requested"


def test_cancelling_puts_the_button_back(rundir):
    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    hp._update_open(st)
    hp._update_cancel(st)
    assert st['button'] is not None


# ------------------------------------------------- the chip in the status bar

@pytest.fixture
def fonts():
    pygame.init()
    pygame.font.init()
    pygame.display.set_mode((720, 450))
    return hp._make_fonts()


@pytest.fixture
def theme():
    return dict(hp.THEMES['kstate'])


@pytest.mark.parametrize("size", SIZES)
def test_the_bar_registers_an_update_hit_target(fonts, theme, size):
    """Registered exactly the way draw_tabs registers the propagation tabs, so
    the render loop's existing MOUSEBUTTONDOWN sweep picks it up."""
    surf = pygame.Surface(size)
    rect = hp._get_layout(size)['status']
    hp._strfmt_cache['key'] = None
    regions = hp.draw_status_bar(surf, rect, _StubData(), fonts, theme,
                                 update_label='UPDATE 1.1.0 [U]')
    assert 'update' in regions
    assert rect.contains(regions['update']), \
        'the update chip escapes the status bar'
    assert 'send_report' in regions, \
        'the update chip displaced the SEND REPORT control'
    assert not regions['update'].colliderect(regions['send_report']), \
        'the two chips overlap; a click would be ambiguous'


@pytest.mark.parametrize("size", SIZES)
def test_no_update_label_means_no_update_target(fonts, theme, size):
    surf = pygame.Surface(size)
    rect = hp._get_layout(size)['status']
    hp._strfmt_cache['key'] = None
    regions = hp.draw_status_bar(surf, rect, _StubData(), fonts, theme)
    assert 'update' not in regions


@pytest.mark.parametrize("size", SIZES)
def test_the_chip_paints_inside_the_bar(fonts, theme, size):
    sentinel = (255, 0, 255)
    surf = pygame.Surface(size)
    surf.fill(sentinel)
    rect = hp._get_layout(size)['status']
    hp._strfmt_cache['key'] = None
    hp.draw_status_bar(surf, rect, _StubData(), fonts, theme,
                       update_label='UPDATE 1.1.0 [U]')
    surf.fill(sentinel, rect)
    raw = pygame.image.tostring(surf, 'RGB')
    assert raw == bytes(sentinel) * (size[0] * size[1]), \
        'the update chip painted outside the status bar'


def test_a_bar_too_narrow_for_the_chip_still_returns_a_dict(fonts, theme):
    surf = pygame.Surface((120, 20))
    regions = hp.draw_status_bar(surf, pygame.Rect(0, 0, 120, 20),
                                 _StubData(), fonts, theme,
                                 update_label='UPDATE 1.1.0 [U]')
    assert isinstance(regions, dict)


# ----------------------------------------------------------- the confirm box

def test_opening_the_box_asks_for_nothing(rundir):
    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    assert hp._update_open(st) is True
    assert st['stage'] == 'confirm'
    assert list(rundir.iterdir()) == [rundir / "update-status.json"], \
        "opening the dialog touched /run"


def test_the_box_cannot_be_opened_when_there_is_nothing_to_install(rundir):
    _write_status(rundir, state="current")
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    assert hp._update_open(st) is False
    assert st['stage'] == 'idle'


def test_the_box_states_the_version_change_and_what_will_happen(rundir):
    lines = hp._update_confirm_lines(_status(installed="1.0.0",
                                             available="1.1.0"))
    body = " ".join(t for t, _c in lines).lower()
    assert "1.0.0" in body and "1.1.0" in body, "the version change is not stated"
    assert "restart" in body, "does not say services restart"
    assert "reboot" in body, "does not warn that the Pi may reboot"
    assert "health check" in body, "does not mention the health check"
    assert "back" in body, "does not say it rolls back on failure"
    assert "sha-256" in body, "does not say the download is verified"


@pytest.mark.parametrize("size", SIZES)
def test_the_box_fits_and_offers_both_answers(fonts, theme, size):
    surf = pygame.Surface(size)
    lines = hp._update_confirm_lines(_status())
    rect = hp._report_overlay_rect(size, lines, fonts)
    regions = hp.draw_update_overlay(surf, rect, lines, fonts, theme)
    assert set(regions) == {'install', 'cancel'}
    for r in regions.values():
        assert rect.contains(r), 'a button escapes the confirm box'
    assert rect.w <= size[0] and rect.h <= size[1]


def test_the_box_survives_a_degenerate_rect(fonts, theme):
    surf = pygame.Surface((720, 450))
    assert hp.draw_update_overlay(surf, None, [], fonts, theme) == {}
    assert hp.draw_update_overlay(surf, pygame.Rect(0, 0, 0, 0), [],
                                  fonts, theme) == {}


# ------------------------------------------------ what confirming really does

def test_confirming_creates_the_request_file_and_nothing_else(rundir):
    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    hp._update_open(st)
    st['shown'] = True
    assert hp._update_confirm(st) is True
    assert sorted(p.name for p in rundir.iterdir()) == \
        ["update-status.json", "update.request"]
    assert (rundir / "update.request").read_bytes() == b"", \
        "the request carries a payload; root must decide, not be told"


def test_confirming_downloads_verifies_and_installs_nothing(rundir,
                                                            monkeypatch):
    """Every one of those steps belongs to root. This side asks; that is all."""
    touched = []
    monkeypatch.setattr(hp, '_urlopen',
                        lambda *a, **kw: touched.append('urlopen'))
    monkeypatch.setattr(hp, '_Request',
                        lambda *a, **kw: touched.append('request'))
    import subprocess
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **kw: touched.append(a))
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda *a, **kw: touched.append(a))
    monkeypatch.setattr(os, 'system', lambda *a: touched.append(a))

    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    hp._update_open(st)
    st['shown'] = True
    hp._update_confirm(st)
    assert touched == [], "the client did privileged work itself: %r" % touched


def test_nothing_is_requested_without_an_explicit_confirm(rundir):
    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    hp._update_open(st)
    hp._update_cancel(st)
    assert not (rundir / "update.request").exists()
    assert st['stage'] == 'idle'


def test_a_confirm_that_beats_the_box_onto_the_screen_is_refused(rundir):
    """A 'U' and a 'Y' can land in the same 100 ms event batch. Consent means
    having seen what you agreed to."""
    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    hp._update_open(st)
    assert st['shown'] is False
    assert hp._update_confirm(st) is False
    assert not (rundir / "update.request").exists()
    st['shown'] = True
    assert hp._update_confirm(st) is True
    assert (rundir / "update.request").exists()


def test_confirming_from_idle_does_nothing(rundir):
    st = hp._new_update_state()
    assert hp._update_confirm(st) is False
    assert not (rundir / "update.request").exists()


def test_a_run_dir_that_does_not_exist_is_reported_not_fatal(tmp_path,
                                                             monkeypatch):
    monkeypatch.setattr(hp, "RUN_DIR", "/proc/nope/hamclock-lite")
    st = hp._new_update_state()
    st['status'] = _status()
    st['stage'] = 'confirm'
    st['shown'] = True
    assert hp._update_confirm(st) is True     # handled, not raised
    assert st['stage'] == 'idle'
    assert st['notice'] and st['notice_color'] == 'poor'


# ------------------------------------------------------- reflecting progress

@pytest.mark.parametrize("state,needle,color", [
    ("applying",    "installing",  "accent"),
    ("rollback",    "rolling back", "fair"),
    ("rebooting",   "reboot",      "accent"),
    ("updated",     "updated",     "good"),
    ("rolled_back", "rolled back", "poor"),
    ("broken",      "broken",      "poor"),
    ("error",       "failed",      "poor"),
])
def test_each_root_side_state_becomes_one_readable_line(rundir, state,
                                                        needle, color):
    _write_status(rundir, state=state, detail="the detail string")
    st = hp._new_update_state()
    assert hp._update_poll(st, now=1000.0) is True
    msg = hp._update_notice_text(st, 1000.0)
    assert msg is not None, "state=%r produced no message" % state
    assert needle in msg.lower(), "%r not in %r" % (needle, msg)
    assert st['notice_color'] == color
    assert "the detail string" in msg, \
        "the root side's detail was dropped: %r" % msg


def test_a_finished_update_re_arms_the_button_logic(rundir):
    _write_status(rundir)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    hp._update_open(st)
    st['shown'] = True
    hp._update_confirm(st)
    assert st['stage'] == 'requested'
    _write_status(rundir, state="updated", installed="1.1.0", detail="done")
    assert hp._update_poll(st, now=2000.0) is True
    assert st['stage'] == 'idle'


def test_an_over_long_detail_cannot_crowd_out_the_message(rundir):
    _write_status(rundir, state="error", detail="x" * 4000)
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    assert len(st['notice']) < 200, \
        "a 4 KB detail string went straight into the status bar"


def test_the_message_ages_out_but_a_broken_install_does_not(rundir):
    _write_status(rundir, state="updated", detail="restart complete")
    st = hp._new_update_state()
    hp._update_poll(st, now=1000.0)
    assert hp._update_notice_text(st, 1000.0) is not None
    assert hp._update_notice_text(st, 1000.0 + hp.UPDATE_NOTICE_TTL_S + 1) is None

    st2 = hp._new_update_state()
    _write_status(rundir, state="broken", detail="see the log")
    hp._update_poll(st2, now=1000.0)
    assert hp._update_notice_text(st2, 1000.0 + hp.UPDATE_NOTICE_TTL_S + 1) \
        is not None, "an install that is broken scrolled off the screen"


# -------------------------------------------------- through the render loop

def test_pressing_u_opens_the_box_and_asks_for_nothing(rundir, monkeypatch):
    _write_status(rundir)
    st = _drive(monkeypatch,
                [[],                                       # frame 1: poll
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_u)],
                 []])
    assert st['stage'] == 'confirm'
    assert not (rundir / "update.request").exists(), \
        "pressing the button once requested an update"


def test_pressing_u_then_y_requests_exactly_one_update(rundir, monkeypatch):
    _write_status(rundir)
    st = _drive(monkeypatch,
                [[],
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_u)],
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y)],
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y)],
                 []])
    assert (rundir / "update.request").exists()
    assert sorted(p.name for p in rundir.iterdir()) == \
        ["update-status.json", "update.request"]
    assert st['stage'] == 'requested'


def test_escape_answers_the_box_instead_of_quitting_the_kiosk(rundir,
                                                              monkeypatch):
    """ESC and Q must dismiss the dialog, not kill the dashboard out from
    under an operator who is still reading it."""
    _write_status(rundir)
    frames = []
    st = _drive(monkeypatch,
                [[],
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_u)],
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)],
                 [],
                 []])
    assert st['stage'] == 'idle'
    assert not (rundir / "update.request").exists()


def test_clicking_the_chip_opens_the_box(rundir, monkeypatch, fonts, theme):
    _write_status(rundir)
    rect = hp._get_layout((720, 450))['status']
    hp._strfmt_cache['key'] = None
    probe = pygame.Surface((720, 450))
    chip = hp.draw_status_bar(probe, rect, _StubData(), fonts, theme,
                              update_label='UPDATE 1.1.0 [U]')['update']
    st = _drive(monkeypatch,
                [[],   # frame 1 polls the status and registers the region
                 [pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                     pos=chip.center, button=1)],
                 []])
    assert st['stage'] == 'confirm'
    assert not (rundir / "update.request").exists()


def test_a_missing_status_file_leaves_the_loop_silent(rundir, monkeypatch,
                                                      capsys):
    _drive(monkeypatch, [[], [], []])
    err = capsys.readouterr().err
    assert "update" not in err.lower(), \
        "the loop complained about a status file that is normally absent"


def test_u_does_nothing_when_there_is_no_update(rundir, monkeypatch):
    _write_status(rundir, state="current")
    st = _drive(monkeypatch,
                [[],
                 [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_u)],
                 []])
    assert st['stage'] == 'idle'
    assert not (rundir / "update.request").exists()
