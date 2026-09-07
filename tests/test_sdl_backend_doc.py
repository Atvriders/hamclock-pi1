"""Phase 0 deliverable: docs/sdl-backend.md must record a real decision.

The point of this file is that the SDL backend was chosen by MEASUREMENT and
the record says so. The value was settled by the first diagnostics report from
a Pi in the field rather than by the install-time probe: SDL_VIDEODRIVER=KMSDRM
at 32bpp, 800x600 on a 1440x900 panel.

Two of the three things that report overturned are asserted here, because each
had a fix built on top of it that would be wrong if the record ever changed:
fbcon is unavailable (so the driver ladder is load-bearing), and the depth is
32 not 16 (so the smoothscale hazard does not apply on this hardware).
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "sdl-backend.md"

VALID_BACKENDS = ("fbcon", "kmsdrm", "xinit")


def _text():
    assert DOC.is_file(), "docs/sdl-backend.md is the Phase 0 record; it must exist"
    return DOC.read_text()


def test_the_record_exists_and_names_one_backend():
    m = re.search(r"^sdl-backend:\s*(%s)\s*$" % "|".join(VALID_BACKENDS),
                  _text(), re.M)
    assert m, "no machine-readable 'sdl-backend: <backend>' line"
    assert m.group(1) in VALID_BACKENDS


def test_the_machine_line_and_the_prose_agree():
    text = _text()
    machine = re.search(r"^sdl-backend:\s*(\w+)\s*$", text, re.M).group(1)
    prose = re.search(r"chosen backend:\s*`?(\w+)`?", text, re.I)
    assert prose, "the record must also state the choice in prose for a human"
    assert prose.group(1).lower() == machine.lower(), (
        f"machine line says {machine!r} but the prose says {prose.group(1)!r}")


def test_the_decision_is_backed_by_evidence_not_assertion():
    """A Phase 0 record that just names a backend is worthless — the whole
    point was to measure on hardware."""
    text = _text().lower()
    assert "kmsdrm" in text
    for token in ("bitsize", "sdl_driver", "raspberry pi"):
        assert token in text, f"record cites no {token} evidence"


def test_it_records_what_the_measurement_overturned():
    text = _text().lower()
    assert "fbcon" in text, (
        "the record must say what happened to fbcon: the driver ladder tries "
        "it first and it is unavailable, which is why the ladder exists")
    assert "32" in text and "16" in text, (
        "the record must note the depth is 32bpp, not the 16 that "
        "10-monitor.conf configures — the smoothscale hazard turns on it")


def test_the_backend_matches_what_the_client_can_actually_reach():
    """Guard against a record naming a backend the ladder cannot select."""
    import hamclock_pygame as hp
    import inspect
    ladder_src = inspect.getsource(hp._init_display)
    machine = re.search(r"^sdl-backend:\s*(\w+)\s*$", _text(), re.M).group(1)
    # 'xinit' is the X11 rung under a different name.
    needle = "x11" if machine == "xinit" else machine
    assert needle in ladder_src, (
        f"record says {machine!r} but _init_display never tries it")
