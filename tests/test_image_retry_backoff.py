"""Tier 1.4: per-key image retry backoff + no stamping on total failure.

The failure path had zero coverage: tests/test_perf_alloc.py monkeypatches
_fetch_binary to always succeed, so the "all five endpoints 503 during cold
boot, then nothing for 900 s" bug was invisible to the suite.

These tests drive HamClockData with a fake clock so the 1 s tick loop and the
5/10/20/40/60 backoff ladder can be asserted without sleeping.
"""
import pytest

import hamclock_data
from hamclock_data import HamClockData

ALL_KEYS = {'solar-image', 'muf-map', 'enlil', 'drap', 'real-drap'}


class FakeClock:
    """Stand-in for the `time` module inside hamclock_data.

    time() returns a frozen value; sleep() advances it instead of blocking.
    """

    def __init__(self, start=1_000_000.0):
        self.t = start
        self.slept = []

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(hamclock_data, 'time', c)
    return c


def _patch_fetch(monkeypatch, fn):
    """Install fn(self, path) as HamClockData._fetch_binary."""
    monkeypatch.setattr(HamClockData, '_fetch_binary', fn)


def _always_fail(self, path):
    return None


def _always_ok(self, path):
    return b'\x89PNG\r\n\x1a\n' + path.encode()


# --------------------------------------------------------------------------
# Stamping
# --------------------------------------------------------------------------

def test_total_failure_does_not_stamp_last_image_refresh(clock, monkeypatch):
    """The 15-minute blank-screen bug: a refresh where nothing came back
    must leave last_image_refresh alone so the retry path stays live."""
    _patch_fetch(monkeypatch, _always_fail)
    d = HamClockData()

    results = d.refresh_images()

    assert results == {k: False for k in ALL_KEYS}
    assert d.last_image_refresh == 0, \
        'last_image_refresh stamped despite zero successful fetches'
    assert d.images == {}
    assert d.image_fetched_at == {}


def test_partial_success_stamps_only_the_keys_that_returned_bytes(clock,
                                                                  monkeypatch):
    def one_ok(self, path):
        return b'PNGDATA' if path.endswith('/real-drap') else None
    _patch_fetch(monkeypatch, one_ok)
    d = HamClockData()

    d.refresh_images()

    assert d.last_image_refresh == clock.t
    assert set(d.images) == {'real-drap'}
    assert set(d.image_fetched_at) == {'real-drap'}
    assert d.image_fetched_at['real-drap'] == clock.t


def test_last_image_refresh_survives_a_later_total_failure(clock, monkeypatch):
    """A good cycle followed by an outage must not rewind or re-stamp."""
    _patch_fetch(monkeypatch, _always_ok)
    d = HamClockData()
    d.refresh_images()
    good_ts = d.last_image_refresh
    assert good_ts == clock.t

    clock.t += 900
    _patch_fetch(monkeypatch, _always_fail)
    d.refresh_images()

    assert d.last_image_refresh == good_ts
    assert set(d.images) == ALL_KEYS, 'stale-good bytes must be kept'


def test_refresh_data_still_stamps_unconditionally(clock, monkeypatch):
    """Deliberately NOT symmetric with refresh_images.

    _fetch_json returns None on HTTP 304 as well as on error, so gating
    last_data_refresh on a non-empty result would make the JSON poll spin
    every 5 s forever against an unchanged server.
    """
    monkeypatch.setattr(HamClockData, '_fetch_json', lambda self, path: None)
    d = HamClockData()

    d.refresh_data()

    assert d.last_data_refresh == clock.t


# --------------------------------------------------------------------------
# Backoff ladder
# --------------------------------------------------------------------------

def test_backoff_constant_is_the_agreed_ladder():
    assert HamClockData.IMAGE_RETRY_BACKOFF == (5, 10, 20, 40, 60)


def test_backoff_schedule_advances_5_10_20_40_60(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_fail)
    d = HamClockData()

    # The 6th failure and beyond saturate at the last rung.
    for expected in (5, 10, 20, 40, 60, 60, 60):
        t0 = clock.t
        d.refresh_images()
        for key in ALL_KEYS:
            assert d.image_next_due[key] == t0 + expected, (
                '%s scheduled at +%s, expected +%s (streak %d)'
                % (key, d.image_next_due[key] - t0, expected,
                   d.image_fail_streak[key]))
        clock.t = d.image_next_due['drap']


def test_fail_streak_counts_consecutive_failures(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_fail)
    d = HamClockData()
    assert d.image_fail_streak == {}

    for expected in (1, 2, 3):
        d.refresh_images()
        assert d.image_fail_streak['enlil'] == expected


def test_success_returns_key_to_the_slow_cadence(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_fail)
    d = HamClockData()
    d.refresh_images()
    d.refresh_images()
    assert d.image_fail_streak['drap'] == 2
    assert d.image_next_due['drap'] == clock.t + 10

    _patch_fetch(monkeypatch, _always_ok)
    t0 = clock.t
    d.refresh_images()

    assert d.image_fail_streak['drap'] == 0
    assert d.image_next_due['drap'] == t0 + 900, \
        'a recovered key must go back to the 900 s cadence, not stay on backoff'


def test_slow_cadence_follows_the_configured_image_interval(clock,
                                                            monkeypatch):
    _patch_fetch(monkeypatch, _always_ok)
    d = HamClockData()
    d._image_interval = 300
    t0 = clock.t

    d.refresh_images()

    assert d.image_next_due['muf-map'] == t0 + 300


def test_backoff_is_per_key_not_global(clock, monkeypatch):
    def only_drap(self, path):
        return b'PNGDATA' if path.endswith('/drap') else None
    _patch_fetch(monkeypatch, only_drap)
    d = HamClockData()
    t0 = clock.t

    d.refresh_images()

    assert d.image_next_due['drap'] == t0 + 900
    assert d.image_next_due['muf-map'] == t0 + 5
    assert d.image_fail_streak['drap'] == 0
    assert d.image_fail_streak['muf-map'] == 1


# --------------------------------------------------------------------------
# Selective refresh / due-key selection
# --------------------------------------------------------------------------

def test_refresh_images_with_no_args_still_fetches_all_five(clock,
                                                            monkeypatch):
    """tests/test_perf_alloc.py calls refresh_images() bare — keep it working."""
    seen = []

    def record(self, path):
        seen.append(path)
        return b'PNGDATA'
    _patch_fetch(monkeypatch, record)
    d = HamClockData()

    results = d.refresh_images()

    assert len(seen) == 5
    assert set(results) == ALL_KEYS
    assert set(d.image_fetched_at) == ALL_KEYS


def test_refresh_images_with_keys_fetches_only_those(clock, monkeypatch):
    seen = []

    def record(self, path):
        seen.append(path)
        return b'PNGDATA'
    _patch_fetch(monkeypatch, record)
    d = HamClockData()

    results = d.refresh_images(['muf-map', 'drap'])

    assert seen == ['/api/muf-map', '/api/drap']
    assert set(results) == {'muf-map', 'drap'}
    assert set(d.image_next_due) == {'muf-map', 'drap'}


def test_refresh_images_ignores_unknown_keys(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_ok)
    d = HamClockData()

    results = d.refresh_images(['drap', 'not-an-endpoint'])

    assert set(results) == {'drap'}
    assert 'not-an-endpoint' not in d.image_next_due


def test_due_image_keys_returns_none_when_nothing_is_due(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_ok)
    d = HamClockData()
    d.refresh_images()

    assert d._due_image_keys(clock.t) is None, \
        'the quiescent 1 s tick must not allocate a list'


def test_due_image_keys_selects_only_expired_keys(clock, monkeypatch):
    def only_drap(self, path):
        return b'PNGDATA' if path.endswith('/drap') else None
    _patch_fetch(monkeypatch, only_drap)
    d = HamClockData()
    d.refresh_images()

    assert d._due_image_keys(clock.t) is None
    assert d._due_image_keys(clock.t + 5) == [
        'solar-image', 'muf-map', 'enlil', 'real-drap']


def test_all_keys_due_on_a_fresh_instance():
    d = HamClockData()
    assert set(d._due_image_keys(0.0)) == ALL_KEYS


# --------------------------------------------------------------------------
# Exceptions _fetch_binary does not catch
# --------------------------------------------------------------------------

def test_exception_mid_loop_still_reschedules_the_failing_key(clock,
                                                              monkeypatch):
    """MemoryError is not in _fetch_binary's except clause. Without the
    finally, the key stays permanently due and the 1 s tick hot-spins."""
    def boom(self, path):
        raise MemoryError('out of memory decoding response')
    _patch_fetch(monkeypatch, boom)
    d = HamClockData()
    t0 = clock.t

    with pytest.raises(MemoryError):
        d.refresh_images()

    assert d.image_next_due['solar-image'] == t0 + 5
    assert d.image_fail_streak['solar-image'] == 1
    assert d.last_image_refresh == 0
    assert 'solar-image' not in d._due_image_keys(t0), \
        'raising key is still due — the tick loop would spin at 1 Hz'
    # The keys after it were never attempted, so they are legitimately still
    # due; each subsequent tick raises on one more key and backs it off, so
    # the loop drains rather than spinning.
    assert d._due_image_keys(t0) == ['muf-map', 'enlil', 'drap', 'real-drap']


def test_exception_mid_loop_publishes_bytes_already_fetched(clock,
                                                             monkeypatch):
    """Keys fetched before the blow-up are rescheduled 900 s out, so their
    payload must be published or the panel goes blank for 15 minutes."""
    def boom_on_enlil(self, path):
        if path.endswith('/enlil'):
            raise MemoryError('boom')
        return b'PNGDATA'
    _patch_fetch(monkeypatch, boom_on_enlil)
    d = HamClockData()
    t0 = clock.t

    with pytest.raises(MemoryError):
        d.refresh_images()

    assert set(d.images) == {'solar-image', 'muf-map'}
    assert d.last_image_refresh == t0
    assert d.image_next_due['solar-image'] == t0 + 900
    assert d.image_next_due['enlil'] == t0 + 5
    # Never attempted, so never scheduled — and therefore due immediately.
    assert 'drap' not in d.image_next_due


def test_exception_is_swallowed_by_the_tick_loop(clock, monkeypatch):
    """_run must survive an uncaught fetch exception and keep ticking."""
    def boom(self, path):
        raise MemoryError('boom')
    _patch_fetch(monkeypatch, boom)
    monkeypatch.setattr(HamClockData, '_fetch_json', lambda self, p: None)
    d = HamClockData()
    d._running = True

    ticks = {'n': 0}
    real_sleep = clock.sleep

    def sleep_and_stop(seconds):
        real_sleep(seconds)
        ticks['n'] += 1
        if ticks['n'] >= 6:
            d._running = False
    monkeypatch.setattr(clock, 'sleep', sleep_and_stop)

    d._run(60, 900)

    assert ticks['n'] == 6
    assert 'MemoryError' in d.errors['_run_images']


# --------------------------------------------------------------------------
# _run tick loop
# --------------------------------------------------------------------------

def _run_ticks(d, clock, monkeypatch, n, data_interval=60, image_interval=900):
    """Run d._run for n ticks against the fake clock, then stop it."""
    d._running = True
    ticks = {'n': 0}
    real_sleep = clock.sleep

    def sleep_and_stop(seconds):
        real_sleep(seconds)
        ticks['n'] += 1
        if ticks['n'] >= n:
            d._running = False
    monkeypatch.setattr(clock, 'sleep', sleep_and_stop)
    d._run(data_interval, image_interval)
    return ticks['n']


def test_run_ticks_once_per_second(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_ok)
    monkeypatch.setattr(HamClockData, '_fetch_json', lambda self, p: {})
    d = HamClockData()

    _run_ticks(d, clock, monkeypatch, 7)

    assert clock.slept == [1] * 7, \
        '_run must tick at 1 s so image retries can land on the backoff grid'


def test_run_retries_a_failed_key_after_5_seconds(clock, monkeypatch):
    """Cold boot: everything 503s, then the server warms up. The image must
    land on the +5 s retry, not 900 s later."""
    attempts = []
    state = {'ok': False}

    def flaky(self, path):
        attempts.append((clock.t, path))
        return b'PNGDATA' if state['ok'] else None
    _patch_fetch(monkeypatch, flaky)
    monkeypatch.setattr(HamClockData, '_fetch_json', lambda self, p: {})
    d = HamClockData()
    t0 = clock.t

    d._running = True
    ticks = {'n': 0}
    real_sleep = clock.sleep

    def sleep_and_stop(seconds):
        real_sleep(seconds)
        ticks['n'] += 1
        if ticks['n'] == 3:
            state['ok'] = True   # server comes up at t0+3
        if ticks['n'] >= 6:
            d._running = False
    monkeypatch.setattr(clock, 'sleep', sleep_and_stop)
    d._run(60, 900)

    retry_times = sorted({t for (t, _) in attempts})
    assert retry_times == [t0, t0 + 5], \
        'expected the boot attempt plus one +5 s retry, got %r' % retry_times
    assert set(d.images) == ALL_KEYS
    assert d.last_image_refresh == t0 + 5


def test_run_leaves_healthy_images_alone_for_the_slow_interval(clock,
                                                               monkeypatch):
    calls = {'n': 0}

    def counting(self, path):
        calls['n'] += 1
        return b'PNGDATA'
    _patch_fetch(monkeypatch, counting)
    monkeypatch.setattr(HamClockData, '_fetch_json', lambda self, p: {})
    d = HamClockData()

    _run_ticks(d, clock, monkeypatch, 30)

    assert calls['n'] == 5, \
        'healthy keys re-fetched %d times in 30 s (expected the 5 boot ' \
        'fetches only)' % calls['n']


def test_run_checks_data_on_a_5_tick_grid(clock, monkeypatch):
    """The JSON poll must stay on its 5 s grid, evaluated before images."""
    _patch_fetch(monkeypatch, _always_ok)
    data_calls = []

    def record_json(self, path):
        data_calls.append(clock.t)
        return {}
    monkeypatch.setattr(HamClockData, '_fetch_json', record_json)
    d = HamClockData()
    t0 = clock.t

    _run_ticks(d, clock, monkeypatch, 12, data_interval=5)

    # 4 endpoints per refresh: boot at t0, then t0+5 and t0+10.
    assert sorted(set(data_calls)) == [t0, t0 + 5, t0 + 10]


def test_run_records_the_configured_image_interval(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_ok)
    monkeypatch.setattr(HamClockData, '_fetch_json', lambda self, p: {})
    d = HamClockData()
    t0 = clock.t

    _run_ticks(d, clock, monkeypatch, 3, image_interval=300)

    assert d._image_interval == 300
    assert d.image_next_due['drap'] == t0 + 300


def test_run_returns_promptly_when_stopped(clock, monkeypatch):
    _patch_fetch(monkeypatch, _always_ok)
    monkeypatch.setattr(HamClockData, '_fetch_json', lambda self, p: {})
    d = HamClockData()

    _run_ticks(d, clock, monkeypatch, 1)

    assert clock.slept == [1], 'stop() must be honoured within one tick'
