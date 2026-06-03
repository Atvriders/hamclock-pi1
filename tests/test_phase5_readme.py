"""README rewrite acceptance: pygame is documented as the default;
browser/tkinter as opt-in; Rollback section present; Migration caveat
present; new install examples show pygame as the no-flag path."""
import re
from pathlib import Path

README = Path("/home/kasm-user/hamclock-pi1/README.md")

def test_display_modes_section_renamed():
    text = README.read_text()
    # The old heading was "Display Modes: Browser vs Native". Phase 5
    # renames it to make pygame the lead.
    assert re.search(r"^##\s*Display Modes", text, re.M)
    assert "Pygame is the default" in text or "pygame is the default" in text

def test_pygame_is_default_in_table():
    text = README.read_text()
    # The mode table should mark `--pygame` (or no flag) as default.
    assert re.search(r"\|.*pygame.*\|.*default.*\|", text, re.I)
    # And the browser row must be marked opt-in / alternative.
    assert re.search(r"\|.*--browser.*\|.*(opt-in|alternative).*\|", text, re.I)

def test_rollback_section_present():
    text = README.read_text()
    assert re.search(r"^##+\s*Reverting to browser mode", text, re.M | re.I) \
        or re.search(r"^##+\s*Rollback", text, re.M)
    assert "kiosk-install.sh --browser" in text

def test_migration_caveat_documented():
    text = README.read_text()
    assert "localStorage" in text
    assert "not migrated" in text or "is not migrated" in text

def test_curlpipe_example_default_is_pygame():
    text = README.read_text()
    # The "Default" curl-pipe example should no longer mention browser kiosk
    # as the default.
    m = re.search(r"# Default[^\n]*\n+curl[^\n]+pi1-install\.sh[^\n]*\| bash", text)
    assert m, "no default curl-pipe example found"
    assert "browser" not in m.group(0).lower()
