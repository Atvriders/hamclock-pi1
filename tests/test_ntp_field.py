"""The on-device NTP server field, and the hardening of _apply_ntp.

The value typed into this field ends up, via settings.json, inside a
root-owned systemd config. Two properties matter more than anything else here:

  * the client's validator and the root applier's validator agree on exactly
    the same accept/reject set — a value the wizard accepts but root refuses
    is a setting that silently never applies, and the operator's only symptom
    is a clock that stays wrong;
  * validation is by FORMAT and never by DNS. The Pi may be pointed at an
    internal time server on a network that is not up, and it has no RTC, so at
    setup time the clock is wrong and the LAN is often absent. Refusing to
    save on a failed lookup would lock the operator out of the one setting
    that fixes it.

And the specific bug this replaces: _apply_ntp's only check was
socket.gethostbyname(ntp_value), and gethostbyname("") RESOLVES — so an empty
value wrote a bare `NTP=` line that systemd cannot parse.
"""
import ast
import json
import os
import socket
import subprocess
import sys

import pygame
import pytest

import hamclock_pygame as hp


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_SH = os.path.join(REPO, "scripts", "hamclock-apply-settings.sh")
CLIENT_PY = os.path.join(REPO, "hamclock_pygame.py")

# The set pinned by tests/test_root_scripts.py, which is the contract the root
# applier is held to. The client must land on the same verdicts.
ACCEPT = [
    "pool.ntp.org",
    "0.pool.ntp.org 1.pool.ntp.org",
    "192.168.1.1",
    "time.nist.gov",
    "2001:4860:4860::8888",
    "ntp1.internal.lan",
    "a.com\tb.com",
]
REJECT = [
    "",
    "evil\nNTP=attacker.com",
    "a.com #comment",
    "a.com=b",
    "999.999.999.999",
    "192.168.1",
    "-bad.com",
    "$(reboot)",
    "a;reboot",
    "'x'",
    '"x"',
    "a|b",
    "a&b",
]


def _shell_verdict(value):
    r = subprocess.run(["bash", SETTINGS_SH, "--validate", value],
                       capture_output=True, text=True)
    return r.returncode == 0


# --------------------------------------------------- parity with the shell

@pytest.mark.parametrize("value", ACCEPT)
def test_client_accepts_everything_the_root_applier_accepts(value):
    ok, err = hp.validate_ntp_list(value)
    assert ok, "client rejected %r (%s) but root accepts it" % (value, err)
    assert err == ""


@pytest.mark.parametrize("value", REJECT)
def test_client_rejects_everything_the_root_applier_rejects(value):
    ok, err = hp.validate_ntp_list(value)
    assert not ok, "client ACCEPTED %r, which the root applier refuses" % value
    assert err, "a rejection with no message gives the operator nothing"


@pytest.mark.parametrize("value", ACCEPT + REJECT)
def test_the_two_validators_agree_value_by_value(value):
    """Run the real shell function against the real Python one."""
    assert hp.validate_ntp_list(value)[0] is _shell_verdict(value), \
        "client and root disagree about %r" % value


def test_no_metacharacter_survives_into_an_accepted_value():
    """Anything that could name a second key, quote, redirect or run a command
    is refused outright — the same list the root applier refuses."""
    for bad in ("#", "=", ";", "|", "&", "$", "`", "'", '"', "(", ")",
                "{", "}", "<", ">", "*", "?", "\\", "%", "!", "~", "^"):
        assert not hp.validate_ntp_list("good.example.com" + bad + "x")[0], \
            "accepted a value containing %r" % bad


def test_whitespace_is_a_separator_and_cannot_inject_a_line(tmp_path):
    """A newline inside the value is not rejected — it is whitespace, so both
    validators read it as a two-server list, exactly as systemd would. What
    makes that safe is that the value is normalised to a single line before it
    is written, so the drop-in is always two lines and never more."""
    value = "good.example.com\nx.example.com"
    assert hp.validate_ntp_list(value)[0] is _shell_verdict(value)
    conf = tmp_path / "hamclock.conf"
    assert hp._apply_ntp(value, str(conf), restart=False) == 0
    assert conf.read_text() == \
        "[Time]\nNTP=good.example.com x.example.com\n"


@pytest.mark.parametrize("value", ACCEPT)
def test_an_accepted_value_always_writes_exactly_two_lines(tmp_path, value):
    conf = tmp_path / "hamclock.conf"
    assert hp._apply_ntp(value, str(conf), restart=False) == 0
    lines = conf.read_text().rstrip("\n").split("\n")
    assert lines[0] == "[Time]"
    assert len(lines) == 2 and lines[1].startswith("NTP=")
    assert lines[1] != "NTP="


# --------------------------------------------------- optional-field semantics

@pytest.mark.parametrize("value", ["", "   ", "\t", None])
def test_wizard_treats_an_empty_field_as_use_the_distro_defaults(value):
    ok, err = hp.validate_ntp(value)
    assert ok and err == "", \
        "an empty NTP field is a valid answer, not an error"


def test_the_strict_validator_still_refuses_empty():
    """--validate '' exits 3 on the root side; the strict twin must match."""
    assert not hp.validate_ntp_list("")[0]
    assert not _shell_verdict("")


@pytest.mark.parametrize("value", REJECT[1:])   # '' is valid for the wizard
def test_wizard_validator_refuses_the_hostile_set(value):
    assert not hp.validate_ntp(value)[0]


def test_a_list_is_accepted_because_systemd_takes_one():
    ok, _ = hp.validate_ntp("0.pool.ntp.org 1.pool.ntp.org 2.pool.ntp.org")
    assert ok, "systemd's NTP= takes a list; that is how fallbacks are set"


def test_normalize_collapses_ragged_whitespace():
    assert hp.normalize_ntp("  a.com \t b.com  ") == "a.com b.com"
    assert hp.normalize_ntp("   ") == ""
    assert hp.normalize_ntp(None) == ""


def test_validation_never_performs_a_dns_lookup(monkeypatch):
    """The box may be configuring an internal server on a network that is not
    up yet, on hardware with no RTC. A lookup here would be both wrong and
    slow."""
    calls = []
    for name in ("gethostbyname", "getaddrinfo", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name,
                            lambda *a, **kw: calls.append(a) or "127.0.0.1")
    for v in ACCEPT + REJECT:
        hp.validate_ntp(v)
        hp.validate_ntp_list(v)
    assert calls == [], "the NTP field resolved a name to validate it"


# ------------------------------------------------------------- _apply_ntp

def test_empty_value_removes_the_dropin_instead_of_writing_a_bare_directive(
        tmp_path):
    """gethostbyname('') resolves, so the old code wrote `NTP=` with nothing
    after it — which systemd cannot parse, leaving the Pi with no time
    source at all."""
    conf = tmp_path / "hamclock.conf"
    conf.write_text("[Time]\nNTP=old.example.com\n")
    assert hp._apply_ntp("", str(conf), restart=False) == 0
    assert not conf.exists(), "an empty value must clear the drop-in"


@pytest.mark.parametrize("value", ["", "   ", None])
def test_no_input_ever_produces_a_bare_ntp_line(tmp_path, value):
    conf = tmp_path / "hamclock.conf"
    rc = hp._apply_ntp(value, str(conf), restart=False)
    assert rc == 0
    body = conf.read_text() if conf.exists() else ""
    assert "NTP=\n" not in body and not body.rstrip().endswith("NTP="), \
        "wrote a bare NTP= line systemd cannot parse: %r" % body


def test_clearing_when_there_is_no_dropin_is_not_an_error(tmp_path):
    conf = tmp_path / "nothing-here.conf"
    assert hp._apply_ntp("", str(conf), restart=False) == 0


def test_a_space_separated_list_is_written_as_one_normalised_line(tmp_path):
    """The old check handed the whole string to gethostbyname(), so every
    multi-server value was rejected out of hand."""
    conf = tmp_path / "hamclock.conf"
    assert hp._apply_ntp("  0.pool.ntp.org   1.pool.ntp.org ",
                         str(conf), restart=False) == 0
    assert conf.read_text() == "[Time]\nNTP=0.pool.ntp.org 1.pool.ntp.org\n"


@pytest.mark.parametrize("value", [v for v in REJECT if v.strip()])
def test_apply_refuses_hostile_values_and_writes_nothing(tmp_path, value):
    conf = tmp_path / "hamclock.conf"
    assert hp._apply_ntp(value, str(conf), restart=False) == 3
    assert not conf.exists(), "wrote a config for %r" % value


def test_an_existing_dropin_survives_a_rejected_value(tmp_path):
    conf = tmp_path / "hamclock.conf"
    conf.write_text("[Time]\nNTP=good.example.com\n")
    assert hp._apply_ntp("a;reboot", str(conf), restart=False) == 3
    assert conf.read_text() == "[Time]\nNTP=good.example.com\n", \
        "a rejected value destroyed the working configuration"


def test_a_name_that_does_not_resolve_is_a_warning_not_a_refusal(
        tmp_path, monkeypatch, capsys):
    """An operator configuring an internal time server before the LAN is up
    must still be able to save it. Reachability is checked after saving."""
    def boom(*a, **kw):
        raise socket.gaierror("simulated: network is not up yet")
    monkeypatch.setattr(socket, "gethostbyname", boom)
    conf = tmp_path / "hamclock.conf"
    assert hp._apply_ntp("ntp1.internal.lan", str(conf), restart=False) == 0
    assert "NTP=ntp1.internal.lan" in conf.read_text()
    assert "warning" in capsys.readouterr().err.lower(), \
        "the operator was told nothing about a name that will not resolve"


def test_ip_literals_are_not_sent_through_a_resolver(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "gethostbyname",
                        lambda *a: calls.append(a) or "127.0.0.1")
    conf = tmp_path / "hamclock.conf"
    assert hp._apply_ntp("2001:4860:4860::8888", str(conf), restart=False) == 0
    assert hp._apply_ntp("192.168.1.1", str(conf), restart=False) == 0
    assert calls == [], "resolved an IP literal"


def test_apply_validates_before_it_resolves(tmp_path, monkeypatch):
    """Format is the gate. A resolver that answers 'yes' to anything must not
    be able to talk a hostile value past it."""
    monkeypatch.setattr(socket, "gethostbyname", lambda *a: "127.0.0.1")
    conf = tmp_path / "hamclock.conf"
    assert hp._apply_ntp("a.com #comment", str(conf), restart=False) == 3
    assert not conf.exists()


# --------------------------------------------------------- the widget itself

def _key(key, unicode="", mod=0):
    return pygame.event.Event(pygame.KEYDOWN,
                              {"key": key, "unicode": unicode, "mod": mod})


def test_the_field_shows_an_inline_error_for_a_bad_value():
    pygame.init()
    tf = hp.TextField(pygame.Rect(0, 0, 300, 40), initial="",
                      validator=hp.validate_ntp, label="NTP server")
    for ch in "a;reboot":
        tf.handle_event(_key(pygame.K_a, ch))
    assert tf.error, "no inline error for a value root will refuse"
    assert "reboot" in tf.error or "invalid" in tf.error.lower()


def test_the_field_is_content_with_being_left_empty():
    pygame.init()
    tf = hp.TextField(pygame.Rect(0, 0, 300, 40), initial="",
                      validator=hp.validate_ntp)
    tf.handle_event(_key(pygame.K_RETURN))
    assert tf.error == "", "an empty optional field is not an error"


def test_the_field_accepts_a_typed_list():
    pygame.init()
    tf = hp.TextField(pygame.Rect(0, 0, 300, 40), initial="",
                      validator=hp.validate_ntp, max_len=128)
    for ch in "0.pool.ntp.org 1.pool.ntp.org":
        tf.handle_event(_key(pygame.K_a, ch))
    assert tf.text == "0.pool.ntp.org 1.pool.ntp.org"
    assert tf.error == ""


def test_long_text_stays_inside_the_box():
    """A space-separated list is the first value that can outrun the field."""
    pygame.init()
    pygame.font.init()
    surf = pygame.Surface((400, 120))
    surf.fill((255, 0, 255))
    box = pygame.Rect(40, 40, 200, 40)
    tf = hp.TextField(box, initial="0.pool.ntp.org 1.pool.ntp.org "
                                  "2.pool.ntp.org 3.pool.ntp.org",
                      max_len=128)
    tf.cursor = len(tf.text)
    tf.draw(surf, hp.THEMES["kstate"], focused=True)
    # Nothing painted to the right of the box on the text's own rows.
    for y in range(box.y + 4, box.bottom - 4):
        for x in range(box.right + 1, 400):
            assert surf.get_at((x, y))[:3] == (255, 0, 255), \
                "field text escaped the box at (%d,%d)" % (x, y)


# ------------------------------------------------------- the wizard end to end

def _fonts():
    pygame.font.init()
    return {
        "tiny": pygame.font.Font(None, 14),
        "small": pygame.font.Font(None, 18),
        "med": pygame.font.Font(None, 24),
        "lg": pygame.font.Font(None, 36),
    }


def _type(seq, text):
    for ch in text:
        seq.append({"type": "KEYDOWN",
                    "key": "K_SPACE" if ch == " " else "K_a",
                    "unicode": ch})


def _tab(seq, n=1):
    for _ in range(n):
        seq.append({"type": "KEYDOWN", "key": "K_TAB", "unicode": ""})


def _enter(seq):
    seq.append({"type": "KEYDOWN", "key": "K_RETURN", "unicode": ""})


def _run_wizard(tmp_path, monkeypatch, seq, size=(720, 450), **kwargs):
    p = tmp_path / "events.json"
    p.write_text(json.dumps(seq))
    monkeypatch.setenv("HAMCLOCK_DEBUG", "1")
    monkeypatch.setenv("HAMCLOCK_INJECT_EVENTS", str(p))
    pygame.init()
    screen = pygame.display.set_mode(size)
    kwargs.setdefault("wait_ntp", False)
    return hp.setup_screen(screen, _fonts(), hp.THEMES["kstate"], **kwargs)


def test_the_wizard_collects_an_ntp_server(tmp_path, monkeypatch):
    seq = []
    _type(seq, "W1ABC")
    _tab(seq)
    _type(seq, "UTC")
    _tab(seq)
    _type(seq, "0.pool.ntp.org 1.pool.ntp.org")
    _tab(seq, 2)          # past the theme cycler onto Save
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq)
    assert result["callsign"] == "W1ABC"
    assert result["ntp"] == "0.pool.ntp.org 1.pool.ntp.org"


def test_leaving_the_ntp_field_blank_saves_an_empty_string(tmp_path,
                                                           monkeypatch):
    seq = []
    _type(seq, "W1ABC")
    _tab(seq)
    _type(seq, "UTC")
    _tab(seq, 3)
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq)
    assert result["ntp"] == "", \
        "blank must mean 'distro defaults', not a bare NTP= line"


def test_a_bad_ntp_value_blocks_the_save_until_it_is_fixed(tmp_path,
                                                           monkeypatch):
    seq = []
    _type(seq, "W1ABC")
    _tab(seq)
    _type(seq, "UTC")
    _tab(seq)
    _type(seq, "a;reboot")
    _tab(seq, 2)
    _enter(seq)                       # refused; focus returns to the NTP field
    seq.append({"type": "KEYDOWN", "key": "K_HOME", "unicode": ""})
    for _ in range(len("a;reboot")):
        seq.append({"type": "KEYDOWN", "key": "K_DELETE", "unicode": ""})
    _type(seq, "pool.ntp.org")
    _tab(seq, 2)
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq)
    assert result["ntp"] == "pool.ntp.org"


def test_the_wizard_normalises_before_it_saves(tmp_path, monkeypatch):
    """The root applier reads whitespace-only as non-empty but word-splits it
    to nothing, so it must never receive ragged spacing."""
    seq = []
    _type(seq, "W1ABC")
    _tab(seq)
    _type(seq, "UTC")
    _tab(seq)
    _type(seq, "   ")
    _tab(seq, 2)
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq)
    assert result["ntp"] == ""


@pytest.mark.parametrize("size", [(720, 450), (1440, 900)])
def test_every_wizard_control_is_on_screen(tmp_path, monkeypatch, size):
    """The kiosk renders at 720x450, where the old fixed layout put the Save
    button at y=540 — off the bottom of the display."""
    seq = []
    _type(seq, "W1ABC")
    _tab(seq)
    _type(seq, "UTC")
    _tab(seq, 3)
    _enter(seq)
    result = _run_wizard(tmp_path, monkeypatch, seq, size=size)
    assert result["callsign"] == "W1ABC"
    screen = pygame.display.get_surface()
    sw, sh = screen.get_size()
    # Something other than the background must be painted in the bottom
    # quarter — that is where Save and the hint line live.
    bg = hp.THEMES["kstate"]["bg"]
    painted = any(screen.get_at((x, y))[:3] != bg
                  for y in range(int(sh * 0.75), sh, 3)
                  for x in range(0, sw, 5))
    assert painted, "nothing is drawn in the bottom quarter at %r" % (size,)


# --------------------------------------------------------------- the CLI

def _cli(*args):
    return subprocess.run(
        [sys.executable, CLIENT_PY, "--setup-cli", *args],
        capture_output=True, text=True)


def test_cli_refuses_a_hostile_ntp_value(tmp_path):
    out = tmp_path / "settings.json"
    r = _cli("--callsign", "W1ABC", "--timezone", "UTC", "--theme", "kstate",
             "--ntp", "a.com #comment", "--settings-path", str(out))
    assert r.returncode != 0
    assert "ntp" in r.stderr.lower()


def test_cli_stores_a_normalised_list(tmp_path):
    out = tmp_path / "settings.json"
    r = _cli("--callsign", "W1ABC", "--timezone", "UTC", "--theme", "kstate",
             "--ntp", "  a.example.com   b.example.com ",
             "--settings-path", str(out))
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())["ntp"] == "a.example.com b.example.com"


def test_cli_can_clear_the_dropin(tmp_path):
    """--apply-ntp with no value used to be a silent no-op, so once an NTP
    server had been set it could never be unset from here."""
    out = tmp_path / "settings.json"
    conf = tmp_path / "hamclock.conf"
    conf.write_text("[Time]\nNTP=old.example.com\n")
    r = _cli("--callsign", "W1ABC", "--timezone", "UTC", "--theme", "kstate",
             "--settings-path", str(out), "--apply-ntp",
             "--ntp-conf-path", str(conf), "--no-restart-timesyncd")
    assert r.returncode == 0, r.stderr
    assert not conf.exists()


# ------------------------------------------------------ the privilege model

def test_the_client_never_shells_out_to_sudo():
    """The dashboard is granted no sudo at all. Its entire privileged
    vocabulary is 'create an empty file in /run/hamclock-lite'."""
    tree = ast.parse(open(CLIENT_PY).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value.strip().lower() != "sudo", \
                "a literal 'sudo' argument appeared in the client"


def test_asking_for_settings_to_be_applied_creates_only_a_flag_file(tmp_path,
                                                                    monkeypatch):
    monkeypatch.setattr(hp, "RUN_DIR", str(tmp_path / "run"))
    assert hp.request_settings_apply() is True
    run = tmp_path / "run"
    assert [p.name for p in run.iterdir()] == ["settings.request"]
    assert (run / "settings.request").read_bytes() == b"", \
        "the request carries a payload; it must carry nothing"


def test_a_failed_request_is_reported_not_raised(monkeypatch, capsys):
    monkeypatch.setattr(hp, "RUN_DIR", "/proc/definitely/not/writable")
    assert hp.request_settings_apply() is False   # must not raise
    assert capsys.readouterr().err != ""
