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


import json as _json
from hamclock_pygame import setup_screen


def _make_fake_fonts():
    # An earlier test in the full suite may have called pygame.quit() (which
    # also deinitialises the font subsystem). Re-init here so Font() succeeds
    # regardless of ordering.
    pygame.font.init()
    return {
        "tiny": pygame.font.Font(None, 14),
        "small": pygame.font.Font(None, 18),
        "med": pygame.font.Font(None, 24),
        "lg": pygame.font.Font(None, 36),
    }


def test_setup_screen_writes_expected_json(tmp_path, monkeypatch):
    """Inject the canonical Phase 4 wizard sequence and assert the
    returned dict matches the spec."""
    events_path = tmp_path / "events.json"
    seq = []
    for ch in "W1ABC":
        seq.append({"type": "KEYDOWN", "key": "K_" + ch.lower(),
                    "unicode": ch})
        if ch.isalpha():
            # Ensure unicode key matches printable.
            seq[-1]["unicode"] = ch
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    for ch in "America/Chicago":
        seq.append({"type": "KEYDOWN", "key": "K_a",
                    "unicode": ch})
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    seq.append({"type": "KEYDOWN", "key": "K_RIGHT", "unicode": ""})
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})
    events_path.write_text(_json.dumps(seq))

    monkeypatch.setenv("HAMCLOCK_DEBUG", "1")
    monkeypatch.setenv("HAMCLOCK_INJECT_EVENTS", str(events_path))

    screen = pygame.display.set_mode((1440, 900))
    fonts = _make_fake_fonts()
    theme = THEME

    result = setup_screen(screen, fonts, theme)
    assert result["callsign"] == "W1ABC"
    assert result["timezone"] == "America/Chicago"
    assert result["theme"] in ("classic", "amber", "blue", "kstate")
    assert result["ntp"] == ""


def test_setup_screen_rejects_invalid_timezone(tmp_path, monkeypatch):
    events_path = tmp_path / "ev.json"
    seq = []
    for ch in "W1ABC":
        seq.append({"type": "KEYDOWN", "key": "K_w", "unicode": ch})
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    for ch in "Atlantis/Lost":
        seq.append({"type": "KEYDOWN", "key": "K_a", "unicode": ch})
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})
    # After save is rejected, fix tz and resubmit.
    seq.append({"type": "KEYDOWN", "key": "K_HOME", "unicode": ""})
    for _ in range(len("Atlantis/Lost")):
        seq.append({"type": "KEYDOWN", "key": "K_DELETE", "unicode": ""})
    for ch in "UTC":
        seq.append({"type": "KEYDOWN", "key": "K_u", "unicode": ch})
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})
    seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})
    events_path.write_text(_json.dumps(seq))

    monkeypatch.setenv("HAMCLOCK_DEBUG", "1")
    monkeypatch.setenv("HAMCLOCK_INJECT_EVENTS", str(events_path))

    screen = pygame.display.set_mode((1440, 900))
    fonts = _make_fake_fonts()
    result = setup_screen(screen, fonts, THEME)
    assert result["timezone"] == "UTC"


def test_wait_for_ntp_returns_quickly_when_synced(monkeypatch):
    import subprocess as _sp
    import hamclock_pygame as hp

    class R: pass
    def fake_run(cmd, capture_output, text, timeout):
        r = R(); r.stdout = "yes\n"; r.returncode = 0
        return r
    monkeypatch.setattr(_sp, "run", fake_run)
    assert hp._wait_for_ntp_sync(deadline_s=10) is True


def test_wait_for_ntp_warns_on_timeout(monkeypatch, capsys):
    import subprocess as _sp
    import hamclock_pygame as hp

    class R: pass
    def fake_run(cmd, capture_output, text, timeout):
        r = R(); r.stdout = "no\n"; r.returncode = 0
        return r
    monkeypatch.setattr(_sp, "run", fake_run)
    assert hp._wait_for_ntp_sync(deadline_s=0) is False
    out = capsys.readouterr()
    assert "NTP" in out.err or "ntp" in out.err


import subprocess
import sys as _sys


def _run_cli(tmp_path, *args, env=None):
    cmd = [_sys.executable,
           "/home/kasm-user/hamclock-pi1/hamclock_pygame.py",
           "--setup-cli", *args]
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, env=e, capture_output=True, text=True)


def test_setup_cli_writes_valid_settings(tmp_path):
    out = tmp_path / "settings.json"
    r = _run_cli(tmp_path,
                 "--callsign", "W1ABC",
                 "--timezone", "UTC",
                 "--theme", "kstate",
                 "--settings-path", str(out))
    assert r.returncode == 0, "stderr: %s" % r.stderr
    data = _json.loads(out.read_text())
    assert data["callsign"] == "W1ABC"
    assert data["timezone"] == "UTC"
    assert data["theme"] == "kstate"
    assert data["ntp"] == ""
    assert (os.stat(out).st_mode & 0o777) == 0o644


def test_setup_cli_rejects_bad_callsign(tmp_path):
    out = tmp_path / "settings.json"
    r = _run_cli(tmp_path,
                 "--callsign", "123456",
                 "--timezone", "UTC",
                 "--theme", "kstate",
                 "--settings-path", str(out))
    assert r.returncode != 0
    assert "letter" in r.stderr or "callsign" in r.stderr.lower()
    assert not out.exists()


def test_setup_cli_rejects_bad_timezone(tmp_path):
    out = tmp_path / "settings.json"
    r = _run_cli(tmp_path,
                 "--callsign", "W1ABC",
                 "--timezone", "Atlantis/Lost",
                 "--theme", "kstate",
                 "--settings-path", str(out))
    assert r.returncode != 0
    assert "timezone" in r.stderr.lower()


def test_setup_cli_inject_events_requires_debug(tmp_path):
    r = subprocess.run(
        [_sys.executable,
         "/home/kasm-user/hamclock-pi1/hamclock_pygame.py",
         "--inject-events", "/tmp/x.json"],
        env={k: v for k, v in os.environ.items()
             if k != "HAMCLOCK_DEBUG"},
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "debug" in r.stderr.lower()


def test_setup_cli_apply_ntp_writes_dropin(tmp_path):
    out = tmp_path / "settings.json"
    ntp_path = tmp_path / "ntp.conf"
    # Run apply-ntp in dry mode by pointing at the override path.
    r = _run_cli(tmp_path,
                 "--callsign", "W1ABC",
                 "--timezone", "UTC",
                 "--theme", "kstate",
                 "--ntp", "pool.ntp.org",
                 "--settings-path", str(out),
                 "--apply-ntp",
                 "--ntp-conf-path", str(ntp_path),
                 "--no-restart-timesyncd")
    assert r.returncode == 0, r.stderr
    assert ntp_path.exists()
    content = ntp_path.read_text()
    assert "[Time]" in content
    assert "NTP=pool.ntp.org" in content


def test_main_launches_wizard_when_settings_absent(tmp_path, monkeypatch):
    """When SETTINGS_PATH points at a missing file, main() should call
    setup_screen() once, persist the result, and only then enter the
    render loop. The test patches setup_screen and the render loop to
    assert ordering."""
    import hamclock_pygame as hp

    calls = []

    def fake_setup(screen, fonts, theme):
        calls.append("setup")
        return {"callsign": "W1ABC", "timezone": "UTC",
                "theme": "kstate", "ntp": ""}

    def fake_render_loop(*a, **kw):
        calls.append("render")
        raise SystemExit(0)

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(hp, "SETTINGS_PATH", str(settings_file))
    monkeypatch.setattr(hp, "setup_screen", fake_setup)
    monkeypatch.setattr(hp, "_run_render_loop", fake_render_loop,
                        raising=False)
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")

    with pytest.raises(SystemExit):
        hp.main()

    assert calls[0] == "setup"
    assert "render" in calls
    assert settings_file.exists()
    data = _json.loads(settings_file.read_text())
    assert data["callsign"] == "W1ABC"
