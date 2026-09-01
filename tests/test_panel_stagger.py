"""Panels sharing a cadence must not all come due on the same frame.

Field measurement (1.0.6, 2026-08-30): p90 frame time 176.4 ms on a Pi 1B at
1440x900, against a 100 ms budget for 10 FPS. Every panel on the 60 s cadence
was initialised to the same due time and then rescheduled to now+60, so all ten
redrew together forever. Their reported per-panel costs sum to ~203 ms — the
p90 frame WAS the pile-up, not a slow panel.

It was survivable at 800x600 (~122 ms) and became a visible stutter once the
client began rendering at the panel's native resolution.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp

# Costs as measured on the reporting Pi at 1440x900.
MEASURED_MS = {
    'dx_spots': 57.6, 'band_activity': 39.4, 'muf_text': 37.2, 'solar': 32.9,
    'bands': 23.3, 'propagation': 16.5, 'header': 12.4, 'status': 10.2,
    'open_bands': 7.7, 'geomag': 2.4, 'xray': 1.7, 'sdo': 0.9,
}
FRAME_BUDGET_MS = 100.0          # 10 FPS


def test_every_panel_has_a_phase():
    assert set(hp._PANEL_PHASE) == set(hp._CADENCE_S)


def test_panels_on_one_cadence_are_spread_across_its_window():
    by_c = {}
    for name, c in hp._CADENCE_S.items():
        by_c.setdefault(c, []).append(name)
    for c, names in by_c.items():
        if len(names) < 2:
            continue
        phases = sorted(hp._PANEL_PHASE[n] for n in names)
        assert len(set(phases)) == len(phases), f"cadence {c}s has duplicate phases"
        assert max(phases) < c, "a phase must stay inside its own window"
        gaps = [b - a for a, b in zip(phases, phases[1:])]
        assert min(gaps) > 0, f"cadence {c}s panels still coincide"


def test_the_pile_up_cannot_recur():
    """The whole point: no frame may owe more than the budget."""
    by_c = {}
    for name, c in hp._CADENCE_S.items():
        by_c.setdefault(c, []).append(name)
    for c, names in by_c.items():
        if len(names) < 2:
            continue
        # Panels landing in the same 100 ms slice of the window.
        slots = {}
        for n in names:
            slot = int(hp._PANEL_PHASE[n] / (FRAME_BUDGET_MS / 1000.0))
            slots.setdefault(slot, []).append(n)
        for slot, group in slots.items():
            cost = sum(MEASURED_MS.get(n, 0.0) for n in group)
            assert cost <= FRAME_BUDGET_MS, (
                f"{group} still land together for {cost:.0f} ms "
                f"(budget {FRAME_BUDGET_MS:.0f} ms)")


def test_all_sixty_second_panels_together_would_have_blown_the_budget():
    """Guards the premise: if this ever stops being true the phase table is
    solving a problem that no longer exists and should be revisited."""
    sixty = [n for n, c in hp._CADENCE_S.items() if c == 60.0]
    assert sum(MEASURED_MS.get(n, 0.0) for n in sixty) > FRAME_BUDGET_MS


def test_phase_is_applied_once_then_the_cadence_persists():
    phased = set()
    c = hp._CADENCE_S['solar']
    first = hp._next_due('solar', 1000.0, phased)
    second = hp._next_due('solar', first, phased)
    assert first == 1000.0 + c + hp._PANEL_PHASE['solar'], "phase not applied"
    assert second == first + c, (
        "phase re-applied — that lengthens the period instead of offsetting it")


def test_next_due_never_returns_the_past():
    phased = set()
    for name in hp._CADENCE_S:
        assert hp._next_due(name, 1000.0, phased) > 1000.0
