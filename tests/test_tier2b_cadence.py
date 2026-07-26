"""Tier 2b: panels redraw at cadenced intervals, not every frame."""


def test_cadence_table_exists_and_has_known_panels():
    import hamclock_pygame as hp
    assert hasattr(hp, '_CADENCE_S'), 'Tier 2b: _CADENCE_S table missing'
    expected = {'header', 'status', 'solar', 'bands', 'geomag', 'xray',
                'open_bands', 'muf_text', 'sdo', 'dx_spots', 'band_activity',
                'propagation'}
    assert expected.issubset(set(hp._CADENCE_S.keys()))


def test_cadence_clock_panels_fast_data_panels_slow():
    import hamclock_pygame as hp
    assert hp._CADENCE_S['header'] <= 2.0
    assert hp._CADENCE_S['status'] <= 2.0
    assert hp._CADENCE_S['solar'] >= 30.0
    assert hp._CADENCE_S['bands'] >= 30.0
    assert hp._CADENCE_S['sdo'] >= 30.0


def test_no_image_cadence_table_is_a_subset_of_the_cadence_table():
    """Tier 2.5: the two image panels get a second, faster cadence for the
    state where they have nothing to draw and their body is a status line
    ("feed down / retry 15s"). It lives in a named table beside _CADENCE_S,
    not as a literal at the call site, so it stays greppable and guarded."""
    import hamclock_pygame as hp
    assert hasattr(hp, '_CADENCE_S_NO_IMAGE'), \
        'Tier 2.5: _CADENCE_S_NO_IMAGE table missing'
    assert set(hp._CADENCE_S_NO_IMAGE).issubset(set(hp._CADENCE_S))
    assert set(hp._CADENCE_S_NO_IMAGE) == {'sdo', 'propagation'}


def test_no_image_cadence_is_15s_not_5s():
    """15 s, not 5 s: "no image" is a persistent state for the whole of an
    outage, not a transient, so 5 s would be a 12x idle-CPU and redraw
    amplifier on a 700 MHz single-core ARMv6 exactly when it can least
    afford one. Still fast enough that the retry ETA visibly counts down."""
    import hamclock_pygame as hp
    for name, val in hp._CADENCE_S_NO_IMAGE.items():
        assert 15.0 <= val <= 30.0, '%s no-image cadence is %.1f s' % (name, val)
        assert val < hp._CADENCE_S[name]
