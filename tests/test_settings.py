import json
import os
import threading
import time
import pytest

from hamclock_pygame import load_settings, DEFAULT_SETTINGS, SETTINGS_PATH


def test_load_settings_missing_returns_defaults(tmp_path):
    missing = tmp_path / "settings.json"
    d = load_settings(str(missing))
    assert d == DEFAULT_SETTINGS
    assert d["callsign"] == ""
    assert d["timezone"] == "UTC"
    assert d["theme"] == "kstate"
    assert d["ntp"] == ""


def test_load_settings_valid_file(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({
        "callsign": "W1ABC", "timezone": "America/Chicago",
        "theme": "amber", "ntp": "pool.ntp.org",
    }))
    d = load_settings(str(p))
    assert d["callsign"] == "W1ABC"
    assert d["timezone"] == "America/Chicago"
    assert d["theme"] == "amber"
    assert d["ntp"] == "pool.ntp.org"


def test_load_settings_garbage_returns_defaults(tmp_path, capsys):
    p = tmp_path / "settings.json"
    p.write_text("{not valid json")
    d = load_settings(str(p))
    assert d == DEFAULT_SETTINGS
    err = capsys.readouterr().err
    assert "settings" in err.lower()


def test_load_settings_retries_on_transient_jsondecode(tmp_path, monkeypatch):
    """Simulate a racing writer: first read raises JSONDecodeError,
    second read (after the 200 ms sleep) returns the real file."""
    p = tmp_path / "settings.json"
    good = {"callsign": "K1A", "timezone": "UTC",
            "theme": "kstate", "ntp": ""}
    p.write_text(json.dumps(good))

    real_open = open
    calls = {"n": 0}

    def flaky_open(path, *a, **kw):
        if str(path) == str(p) and calls["n"] == 0:
            calls["n"] += 1
            # First open raises during json.load by returning bad bytes.
            import io
            return io.StringIO("{partial")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", flaky_open)
    d = load_settings(str(p))
    assert d["callsign"] == "K1A"
    assert calls["n"] == 1
