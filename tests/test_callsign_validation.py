import pytest

from hamclock_pygame import validate_callsign


@pytest.mark.parametrize("call", [
    "W1ABC",
    "W1ABC/P",
    "KH6/W1ABC",
    "W1ABC/QRP",
    "K1A",
    "w1abc",       # lowercase is uppercased and accepted
    "VE3XYZ",
    "JA1ABC/QRP",
])
def test_callsign_accepted(call):
    ok, err = validate_callsign(call)
    assert ok, "expected accept for %r, got err=%r" % (call, err)
    assert err == ""


@pytest.mark.parametrize("call,reason", [
    ("///",     "no letter or digit"),
    ("/W1",     "too short stripped"),
    ("ABCDEF",  "no digit"),
    ("123456",  "no letter"),
    ("AB",      "too short overall"),
    ("",        "empty"),
    ("W1ABC!",  "bad chars"),
    ("W" * 11,  "too long"),
    ("W1ABCDEFGH/P", "stripped too long"),
])
def test_callsign_rejected(call, reason):
    ok, err = validate_callsign(call)
    assert not ok, "expected reject for %r (%s)" % (call, reason)
    assert err != ""


def test_callsign_uppercased_in_caller_responsibility():
    ok, err = validate_callsign("w1abc")
    assert ok
    # Validator itself does not return the uppercased string;
    # caller passes the uppercased value to write_settings.
