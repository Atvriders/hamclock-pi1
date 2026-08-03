"""The propagation panel cycles its tabs on a wall display.

Nobody stands at a kiosk clicking through tabs, so a tab that is never selected
is a map the operator never sees. The panel advances itself every TAB_CYCLE_S.

Two behaviours carry the weight here: a feed that is down must be SKIPPED
rather than held on screen blank for a full interval (a quarter of the rotation
spent showing nothing), and a manual click must reset the timer so the person
who just chose a map gets to read it.
"""
import os
import re
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hamclock_pygame as hp

KM = hp.PROP_TAB_IMAGE_KEY
ALL_UP = {'real-drap': b'x', 'drap': b'x', 'enlil': b'x', 'muf-map': b'x'}


class _Data:
    def __init__(self, images):
        self.images = images


def _walk(data, steps, start='drap'):
    t = start
    out = [t]
    for _ in range(steps):
        t = hp._next_cycle_tab(t, hp.PROP_TABS, KM, data)
        out.append(t)
    return out


def test_interval_is_five_minutes():
    assert hp.TAB_CYCLE_S == 300.0


def test_cycles_through_every_tab_in_order():
    seq = _walk(_Data(ALL_UP), len(hp.PROP_TABS))
    assert seq[1:] == ['aurora', 'enlil', 'muf', 'drap'], seq
    # every tab reachable — the whole point
    assert set(seq) == set(hp.PROP_TABS)


def test_wraps_around():
    assert hp._next_cycle_tab('muf', hp.PROP_TABS, KM, _Data(ALL_UP)) == 'drap'


def test_skips_a_feed_with_no_image():
    """A down feed must not hold the panel blank for a full interval."""
    down = dict(ALL_UP)
    del down['enlil']
    seq = _walk(_Data(down), 6)
    assert 'enlil' not in seq[1:], seq
    assert set(seq[1:]) == {'drap', 'aurora', 'muf'}


def test_lands_on_the_only_live_feed():
    only_muf = _Data({'muf-map': b'x'})
    assert hp._next_cycle_tab('drap', hp.PROP_TABS, KM, only_muf) == 'muf'
    # and stays there rather than bouncing through dead tabs
    assert hp._next_cycle_tab('muf', hp.PROP_TABS, KM, only_muf) == 'muf'


def test_still_advances_when_nothing_has_loaded_yet():
    """During the first seconds after boot no image exists. Refusing to advance
    would pin the panel to whichever tab happened to be first."""
    seq = _walk(_Data({}), 4)
    assert seq[1:] == ['aurora', 'enlil', 'muf', 'drap'], seq


def test_tolerates_a_data_object_without_images():
    class Bare:
        pass
    assert hp._next_cycle_tab('drap', hp.PROP_TABS, KM, Bare()) in hp.PROP_TABS


def test_tolerates_an_unknown_current_tab():
    assert hp._next_cycle_tab('nonsense', hp.PROP_TABS, KM,
                              _Data(ALL_UP)) in hp.PROP_TABS


def test_empty_tab_list_is_a_noop():
    assert hp._next_cycle_tab('drap', [], KM, _Data(ALL_UP)) == 'drap'


# ---------------------------------------------------------------- wiring

def _loop_src():
    src = open(hp.__file__).read()
    i = src.index('def _run_render_loop')
    return src[i:]


def test_a_manual_click_resets_the_timer():
    """Someone chose that map; do not yank it away partway through."""
    src = _loop_src()
    click = src.index('if r.collidepoint(pos):')
    nxt = src.index('next_tab_at', click)
    # the reset must be inside the click branch, not somewhere far below
    assert nxt - click < 700, "click handler does not reset next_tab_at nearby"


def test_advance_happens_before_the_full_flip_predicate():
    """A tab change is one of the things will_full_flip tests for. Advancing
    after it would paint the new tab into a partial-update frame and leave the
    previous tab's pixels on screen until something else forced a flip."""
    src = _loop_src()
    advance = src.index('>= next_tab_at')
    predicate = src.index('will_full_flip = (')
    assert advance < predicate, "tab advance runs after will_full_flip"


def test_cycling_can_be_disabled():
    """TAB_CYCLE_S = 0 leaves the panel wherever it was last put."""
    src = _loop_src()
    assert 'TAB_CYCLE_S > 0' in src, "no guard for a disabled cycle"


def test_advance_forces_a_repaint():
    src = _loop_src()
    advance = src.index('>= next_tab_at')
    window = src[advance:advance + 500]
    assert "full_flip_pending" in window, (
        "a tab change must force a repaint or the panel keeps the old pixels")
