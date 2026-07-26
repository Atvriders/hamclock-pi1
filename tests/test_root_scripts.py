"""Tests for the two root-privileged helper scripts.

Both run as root, are triggered by an unprivileged process, and act on values
that an unprivileged process can influence:

  hamclock-update.sh          reads a manifest off the network, then executes
                              an installer as root
  hamclock-apply-settings.sh  reads settings.json (writable by SERVICE_USER),
                              then writes a root-owned systemd config

That makes their input validation a privilege boundary, not a nicety. These
tests exercise the real shell functions rather than grepping for text, because
the property that matters is behavioural: hostile input must be refused.
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPDATE = os.path.join(REPO, "scripts", "hamclock-update.sh")
SETTINGS = os.path.join(REPO, "scripts", "hamclock-apply-settings.sh")
UNITS = os.path.join(REPO, "systemd")


def sh(script, func_pat, call):
    """Source selected functions out of a script and run one call."""
    body = subprocess.run(
        ["sed", "-n", func_pat, script], capture_output=True, text=True, check=True
    ).stdout
    return subprocess.run(["bash", "-c", body + "\n" + call],
                          capture_output=True, text=True)


# ---------------------------------------------------------------- basics

@pytest.mark.parametrize("path", [UPDATE, SETTINGS])
def test_scripts_exist_and_are_valid_bash(path):
    assert os.path.exists(path), f"{path} missing"
    assert os.access(path, os.X_OK), f"{path} is not executable"
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", [UPDATE, SETTINGS])
def test_scripts_reject_unknown_mode(path):
    r = subprocess.run(["bash", path, "--wat"], capture_output=True, text=True)
    assert r.returncode == 1
    assert "usage" in r.stderr.lower()


# ------------------------------------------------------- version compare

@pytest.mark.parametrize("newer,older,expect", [
    ("1.1.0", "1.0.0", True), ("1.0.0", "1.1.0", False),
    ("1.0.0", "1.0.0", False), ("1.0.10", "1.0.9", True),
    ("1.10.0", "1.9.0", True), ("2.0.0", "1.99.99", True),
    ("1.2", "1.1.9", True), ("0.0.0", "1.0.0", False),
])
def test_version_gt(newer, older, expect):
    r = sh(UPDATE, "/^version_gt()/,/^}/p", f'version_gt "{newer}" "{older}"')
    assert (r.returncode == 0) is expect, f"{newer} vs {older}"


def test_version_compare_ignores_prerelease_suffix():
    """1.0.0-rc1 must not read as newer than 1.0.0."""
    r = sh(UPDATE, "/^version_gt()/,/^}/p", 'version_gt "1.0.0-rc1" "1.0.0"')
    assert r.returncode != 0


# --------------------------------------------------- manifest sanitizing

PATS = "/^sanitize_version()/,/^}/p;/^sanitize_sha256()/,/^}/p"


@pytest.mark.parametrize("hostile", [
    '1.2.3" evil', "1.2.3\nrm -rf /", "1.2.3;reboot", "1.2.3$(id)", "1.2.3`id`",
])
def test_sanitize_version_strips_everything_dangerous(hostile):
    """The version lands in a JSON status file, so a quote or newline would
    corrupt it; shell metacharacters have no business here at all."""
    r = sh(UPDATE, PATS, f'sanitize_version {hostile!r}')
    out = r.stdout
    assert re.fullmatch(r"[0-9A-Za-z.+-]*", out), f"leaked: {out!r}"
    for bad in ('"', "\n", ";", "$", "`", "(", ")", " "):
        assert bad not in out


def test_sanitize_version_is_length_capped():
    r = sh(UPDATE, PATS, 'sanitize_version "' + "9" * 500 + '"')
    assert len(r.stdout) <= 32


@pytest.mark.parametrize("bad", ["", "deadbeef", "x" * 64, "a" * 63, "a" * 65])
def test_sanitize_sha256_rejects_non_digests(bad):
    """Anything not exactly 64 hex chars must FAIL, not be silently coerced —
    a coerced digest would sail past the integrity gate."""
    r = sh(UPDATE, PATS, f'sanitize_sha256 "{bad}"')
    assert r.returncode != 0, f"accepted {bad!r} as a digest"


def test_sanitize_sha256_accepts_a_real_digest():
    d = "a" * 64
    r = sh(UPDATE, PATS, f'sanitize_sha256 "{d}"')
    assert r.returncode == 0 and r.stdout == d


def test_update_refuses_when_manifest_has_no_sha256():
    """Without a digest this is just curl | bash as root."""
    src = open(UPDATE).read()
    assert 'REFUSING — manifest carries no sha256' in src or \
           'no sha256' in src, "missing the no-digest refusal"
    assert 'sha256sum' in src and 'got_sha' in src, "no digest verification"


def test_update_uses_https_only():
    src = open(UPDATE).read()
    assert "--proto '=https'" in src, "must pin https; a redirect to http would "\
                                      "let the network choose what root runs"
    assert "http://hamclock-reborn.org" not in src


# ------------------------------------------------------- NTP validation

@pytest.mark.parametrize("value", [
    "pool.ntp.org", "0.pool.ntp.org 1.pool.ntp.org", "192.168.1.1",
    "time.nist.gov", "2001:4860:4860::8888", "ntp1.internal.lan",
    # A tab is just whitespace: this is a two-server list, and systemd reads it
    # as one. It is normalised to single spaces before being written.
    "a.com\tb.com",
])
def test_ntp_accepts_legitimate_values(value):
    r = subprocess.run(["bash", SETTINGS, "--validate", value],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"rejected legitimate {value!r}"


@pytest.mark.parametrize("value", [
    "", "evil\nNTP=attacker.com", "a.com #comment", "a.com=b",
    "999.999.999.999", "192.168.1", "-bad.com", "$(reboot)", "a;reboot",
    "'x'", '"x"', "a|b", "a&b",
])
def test_ntp_rejects_hostile_or_malformed(value):
    """This value is written into a root-owned systemd config, so anything
    that could start a second directive must be refused."""
    r = subprocess.run(["bash", SETTINGS, "--validate", value],
                       capture_output=True, text=True)
    assert r.returncode != 0, f"ACCEPTED dangerous {value!r}"


def test_ntp_empty_clears_rather_than_writing_empty_directive():
    """gethostbyname('') resolves, so the old python path let an empty value
    through and wrote a bare 'NTP=' line, which systemd cannot parse."""
    src = open(SETTINGS).read()
    assert 'rm -f "$CONF"' in src, "empty NTP must remove the drop-in"


def test_ntp_revalidates_instead_of_trusting_the_wizard():
    """settings.json is unprivileged-writable; the root side must not assume
    the wizard already checked."""
    src = open(SETTINGS).read()
    assert "valid_ntp_list" in src
    i_read = src.index("read_ntp")
    i_valid = src.index("valid_ntp_list")
    assert i_valid < src.index("mv -f \"$CONF.tmp\""), \
        "validation must happen before the config is written"


# ------------------------------------------------------------ the units

def test_all_units_verify_without_ordering_cycles():
    """systemd-analyze caught a Before=systemd-timesyncd cycle here that would
    have made systemd DELETE the timesyncd start job — no time sync at all on
    an RTC-less Pi 1, which cascades into broken TLS and a broken update check.
    """
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze not available")
    noise = re.compile(
        r"hamclock-(update|apply-settings)\.sh|Unknown user|command not found"
        r"|dbus|Failed to (create|load)", re.I)
    for name in sorted(os.listdir(UNITS)):
        r = subprocess.run(["systemd-analyze", "verify", os.path.join(UNITS, name)],
                           capture_output=True, text=True)
        real = [l for l in (r.stderr + r.stdout).splitlines()
                if l.strip() and not noise.search(l)]
        assert not real, f"{name}: {real}"
        assert "ordering cycle" not in (r.stderr + r.stdout).lower()


def test_boot_unit_does_not_reintroduce_the_timesyncd_cycle():
    src = open(os.path.join(UNITS, "hamclock-apply-settings-boot.service")).read()
    # Only real directives count — the unit deliberately CARRIES a comment
    # explaining why this line must never be added back.
    directives = [l.strip() for l in src.splitlines()
                  if l.strip() and not l.lstrip().startswith("#")]
    assert not any(d.startswith("Before=systemd-timesyncd") for d in directives), \
        "Before=systemd-timesyncd.service forms an ordering cycle"
    assert any("do NOT add Before=systemd-timesyncd" in l for l in src.splitlines()), \
        "the warning comment should stay; it is why this stays fixed"


def test_daily_check_timer_is_seven_am_and_persistent():
    src = open(os.path.join(UNITS, "hamclock-update-check.timer")).read()
    assert "OnCalendar=*-*-* 07:00:00" in src
    # The Pi 1 has no RTC and may be powered off at 07:00; without Persistent
    # a missed run is simply skipped.
    assert "Persistent=true" in src


def test_apply_is_a_separate_unit_from_check():
    """The daily timer must never be able to install anything by itself."""
    check = open(os.path.join(UNITS, "hamclock-update-check.service")).read()
    assert "--check" in check and "--apply" not in check
