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
