"""Tier 2.1 — disk persistence for the five image products, + the /api/health
age fields (Tier 2.5's server half) that make serving a stale image honest.

CACHE is RAM-only today, so every restart starts from zero: five upstream
round trips plus a multi-second rasterize before the first pixel, and nothing
at all when the Pi boots without a network.

The two hazards this file pins are the ones that turn a cache into a bug:
persisting must never raise into a fetcher's control flow, and restoring must
never claim a week-old picture is fresh.
"""
import json
import os
import time

import pytest

import server

PNG = b'\x89PNG\r\n\x1a\n' + b'PIXELS' * 8 + b'IEND\xaeB\x60\x82'
JPEG = b'\xff\xd8\xff' + b'SCAN' * 8 + b'\xff\xd9'
GIF = b'GIF89a' + b'PIXELS' * 4 + b'\x3b'


@pytest.fixture
def cachedir(tmp_path, monkeypatch):
    """Point every persistence global at a fresh directory."""
    monkeypatch.setattr(server, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(server, '_PERSIST_LAST', {})
    monkeypatch.setattr(server, '_MUF_PNG_SOURCE', 'none')
    saved = dict(server.CACHE)
    yield tmp_path
    server.CACHE.clear()
    server.CACHE.update(saved)


def _manifest(cachedir):
    with open(os.path.join(str(cachedir), server._MANIFEST_NAME)) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Configuration contract
# ---------------------------------------------------------------------------


def test_cache_dir_is_env_overridable():
    """The unit gets CacheDirectory=hamclock-lite; the suite gets a tmpdir."""
    src = open(server.__file__).read()
    assert "os.environ.get('HAMCLOCK_CACHE_DIR', '/var/cache/hamclock-lite')" \
        in src


def test_conftest_redirects_the_cache_dir():
    """tests/test_muf_rasterize.py calls fetch_muf() directly, which
    write-throughs the rendered PNG — the suite must never touch
    /var/cache/hamclock-lite (and must not need root to run)."""
    assert os.environ.get('HAMCLOCK_CACHE_DIR')
    assert server.CACHE_DIR != '/var/cache/hamclock-lite'


def test_persist_is_throttled_to_an_hour():
    """~319 KB across five products; at 900 s cadence unthrottled that is
    ~30 MB/day of writes to a rootfs deliberately mounted noatime,commit=60
    with journald Storage=volatile."""
    assert server.PERSIST_MIN_INTERVAL_S == 3600


def test_persist_max_age_is_not_stricter_than_the_in_ram_stale_window():
    """Being stricter on disk than the running process is with its own
    keep-last-good would mean a warm reboot throws away an image the server
    would happily have gone on serving."""
    assert server.PERSIST_MAX_AGE_S >= server.MUF_STALE_MAX_S


# ---------------------------------------------------------------------------
# _persist
# ---------------------------------------------------------------------------


def test_persist_writes_the_payload_and_a_manifest(cachedir):
    server.CACHE['muf_image_png'] = PNG
    server.CACHE['muf_image_png_updated'] = 1700000000.0

    server._persist('muf_image_png')

    assert (cachedir / 'muf.png').read_bytes() == PNG
    entry = _manifest(cachedir)['entries']['muf_image_png']
    assert entry['size'] == len(PNG)
    assert entry['epoch'] == 1700000000.0


def test_persist_records_the_real_epoch_not_now(cachedir):
    server.CACHE['solar_image'] = JPEG
    server.CACHE['solar_image_updated'] = 1000.0
    server._persist('solar_image')
    assert _manifest(cachedir)['entries']['solar_image']['epoch'] == 1000.0


def test_persist_skips_falsy_values(cachedir):
    """muf_image_png is legitimately None after a rasterize failure. Writing
    an empty file over the last good render would be the same bug Tier 1.5
    removed, just moved to disk."""
    server.CACHE['muf_image_png'] = PNG
    server._persist('muf_image_png')
    server.CACHE['muf_image_png'] = None
    server._PERSIST_LAST.clear()

    server._persist('muf_image_png')

    assert (cachedir / 'muf.png').read_bytes() == PNG


def test_persist_skips_unrecognised_payloads(cachedir):
    """An upstream HTML error page is not an image."""
    server.CACHE['drap_image'] = b'<html>503 Service Unavailable</html>'
    server._persist('drap_image')
    assert not (cachedir / 'drap.img').exists()


def test_persist_throttles_repeat_writes(cachedir):
    server.CACHE['solar_image'] = JPEG
    server._persist('solar_image')
    server.CACHE['solar_image'] = JPEG + JPEG
    server._persist('solar_image')
    assert (cachedir / 'sdo.img').read_bytes() == JPEG


def test_persist_throttles_failures_too(cachedir, monkeypatch):
    """A read-only or full rootfs must not retry on every image cycle."""
    attempts = []

    def boom(path, data):
        attempts.append(path)
        raise OSError(30, 'Read-only file system')
    monkeypatch.setattr(server, '_write_atomic', boom)
    server.CACHE['solar_image'] = JPEG

    server._persist('solar_image')
    server._persist('solar_image')

    assert len(attempts) == 1


def test_persist_never_raises(cachedir, monkeypatch, capsys):
    """In fetch_enlil/fetch_drap/fetch_real_drap the `return` is the LAST
    statement of the `try` inside `for url in urls`. An OSError escaping from
    _persist would skip that return, be logged as a bogus fetch failure, and
    fall through to the fallback URL — re-downloading the same product every
    cycle, forever."""
    def boom(*a, **kw):
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr(server, '_write_atomic', boom)
    server.CACHE['enlil_image'] = JPEG

    server._persist('enlil_image')  # must not raise

    assert 'persist enlil_image failed' in capsys.readouterr().out


def test_persist_of_an_unknown_key_is_a_noop(cachedir):
    server.CACHE['solar'] = {'sfi': '100'}
    server._persist('solar')
    assert list(cachedir.iterdir()) == []


def test_a_fetcher_still_returns_when_persist_explodes(monkeypatch, cachedir):
    """The regression the note above describes, driven end to end."""
    monkeypatch.setattr(server, '_write_atomic',
                        lambda *a, **kw: (_ for _ in ()).throw(OSError('nope')))
    urls = []

    class _Resp:
        def read(self):
            return JPEG

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        return _Resp()
    monkeypatch.setattr(server, 'urlopen', fake_urlopen)

    server.fetch_real_drap()

    assert len(urls) == 1, 'fell through to the fallback URL'
    assert server.CACHE['real_drap_image'] == JPEG


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_write_atomic_fsyncs_the_file_the_rename_and_the_directory(
        tmp_path, monkeypatch):
    """The rootfs is mounted noatime,commit=60, so os.replace() alone gives
    atomicity but not durability: a power cut inside the commit window can
    leave the directory entry pointing at nothing. Persisted images only
    matter across an unclean restart — precisely the case os.replace() does
    not cover on its own."""
    events = []
    real_fsync = os.fsync
    real_replace = os.replace

    monkeypatch.setattr(server.os, 'fsync',
                        lambda fd: (events.append('fsync'), real_fsync(fd))[1])
    monkeypatch.setattr(server.os, 'replace',
                        lambda a, b: (events.append('replace'),
                                      real_replace(a, b))[1])

    server._write_atomic(str(tmp_path / 'x.png'), PNG)

    assert events == ['fsync', 'replace', 'fsync']
    assert (tmp_path / 'x.png').read_bytes() == PNG


def test_write_atomic_leaves_no_temp_files_behind(tmp_path):
    server._write_atomic(str(tmp_path / 'x.png'), PNG)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['x.png']


def test_write_atomic_cleans_up_after_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(server.os, 'replace',
                        lambda a, b: (_ for _ in ()).throw(OSError('boom')))
    with pytest.raises(OSError):
        server._write_atomic(str(tmp_path / 'x.png'), PNG)
    assert list(tmp_path.iterdir()) == []


def test_write_atomic_never_exposes_a_partial_file(tmp_path):
    """os.replace is the only thing that ever makes the target visible."""
    target = tmp_path / 'x.png'
    server._write_atomic(str(target), PNG)
    server._write_atomic(str(target), PNG + b'MORE')
    assert target.read_bytes() == PNG + b'MORE'


# ---------------------------------------------------------------------------
# _load_persisted
# ---------------------------------------------------------------------------


def _seed(cachedir, key, data, epoch, size=None):
    fname, _stamp = server._PERSIST_KEYS[key]
    (cachedir / fname).write_bytes(data)
    path = os.path.join(str(cachedir), server._MANIFEST_NAME)
    try:
        with open(path) as f:
            man = json.load(f)
    except OSError:
        man = {'version': 1, 'entries': {}}
    man['entries'][key] = {'file': fname,
                           'size': len(data) if size is None else size,
                           'epoch': epoch}
    with open(path, 'w') as f:
        json.dump(man, f)


def test_load_restores_every_product(cachedir):
    now = time.time()
    _seed(cachedir, 'muf_image_png', PNG, now - 100)
    _seed(cachedir, 'solar_image', JPEG, now - 100)
    _seed(cachedir, 'enlil_image', JPEG, now - 100)
    _seed(cachedir, 'drap_image', GIF, now - 100)
    _seed(cachedir, 'real_drap_image', PNG, now - 100)
    for key in server._PERSIST_KEYS:
        server.CACHE[key] = None

    loaded = server._load_persisted()

    assert set(loaded) == set(server._PERSIST_KEYS)
    assert server.CACHE['muf_image_png'] == PNG
    assert server.CACHE['drap_image'] == GIF


def test_load_seeds_the_stamp_from_the_manifest_epoch_not_now(cachedir):
    """server.py's age guards exist to tell the operator how old the picture
    is. Stamping now() would make a week-old map claim to be seconds fresh —
    serve-stale without a label is worse than blank for someone making a band
    decision."""
    epoch = time.time() - 4000
    _seed(cachedir, 'solar_image', JPEG, epoch)
    server.CACHE['solar_image'] = None
    server.CACHE['solar_image_updated'] = 0

    server._load_persisted()

    assert server.CACHE['solar_image_updated'] == pytest.approx(epoch)


def test_load_marks_the_muf_png_as_coming_from_disk(cachedir):
    _seed(cachedir, 'muf_image_png', PNG, time.time() - 10)
    server._load_persisted()
    assert server._MUF_PNG_SOURCE == 'disk'


def test_load_does_not_immediately_rewrite_what_it_just_read(cachedir):
    epoch = time.time() - 10
    _seed(cachedir, 'solar_image', JPEG, epoch)
    server._load_persisted()

    server.CACHE['solar_image'] = JPEG + b'CHANGED'
    server._persist('solar_image')

    assert (cachedir / 'sdo.img').read_bytes() == JPEG


@pytest.mark.parametrize('data,size', [
    (PNG[:-8], None),                       # truncated: no IEND
    (JPEG[:-2], None),                      # truncated: no EOI
    (b'\x00' * 40, None),                   # not an image at all
    (b'', None),                            # empty
    (PNG, 999999),                          # manifest size disagrees
])
def test_load_rejects_and_unlinks_a_corrupt_file(cachedir, data, size):
    """The atomic write makes a half-written file impossible; flash wear on an
    SD card does not."""
    _seed(cachedir, 'muf_image_png', data, time.time() - 10, size=size)
    server.CACHE['muf_image_png'] = None

    assert server._load_persisted() == []

    assert server.CACHE['muf_image_png'] is None
    assert not (cachedir / 'muf.png').exists()


def test_load_skips_but_keeps_an_over_age_entry(cachedir):
    """Skipped, not unlinked: a clock that is wrong but sane must not be able
    to destroy the cache."""
    _seed(cachedir, 'muf_image_png', PNG,
          time.time() - server.PERSIST_MAX_AGE_S - 60)
    server.CACHE['muf_image_png'] = None

    assert server._load_persisted() == []

    assert server.CACHE['muf_image_png'] is None
    assert (cachedir / 'muf.png').exists()


def test_load_ignores_age_when_the_clock_is_unset(cachedir, monkeypatch):
    """The Pi 1 has no RTC. Before NTP syncs, time.time() is whatever
    fake-hwclock last wrote — age eviction there nukes the cache on exactly
    the boot where it is most valuable."""
    _seed(cachedir, 'muf_image_png', PNG, 1700000000.0)
    monkeypatch.setattr(server.time, 'time', lambda: 100.0)
    server.CACHE['muf_image_png'] = None

    assert server._load_persisted() == ['muf_image_png']


def test_load_keeps_an_entry_whose_epoch_is_in_the_future(cachedir):
    """Same no-RTC story from the other side: the file was written after an
    NTP sync, this boot has not synced yet."""
    _seed(cachedir, 'muf_image_png', PNG, time.time() + 86400)
    server.CACHE['muf_image_png'] = None
    assert server._load_persisted() == ['muf_image_png']


def test_load_survives_a_missing_cache_dir(cachedir, monkeypatch):
    monkeypatch.setattr(server, 'CACHE_DIR', str(cachedir / 'nope'))
    assert server._load_persisted() == []


def test_load_survives_a_corrupt_manifest(cachedir):
    (cachedir / server._MANIFEST_NAME).write_text('{not json at all')
    assert server._load_persisted() == []


def test_load_ignores_a_manifest_path_escape(cachedir, tmp_path):
    """The manifest is ours, but a basename() is one line and removes the
    whole class."""
    secret = tmp_path.parent / 'outside.bin'
    secret.write_bytes(PNG)
    (cachedir / server._MANIFEST_NAME).write_text(json.dumps({
        'version': 1,
        'entries': {'muf_image_png': {'file': '../../outside.bin',
                                      'size': len(PNG),
                                      'epoch': time.time()}},
    }))
    server.CACHE['muf_image_png'] = None

    server._load_persisted()

    assert server.CACHE['muf_image_png'] is None


def test_load_never_raises(cachedir, monkeypatch, capsys):
    monkeypatch.setattr(server, '_read_manifest',
                        lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    assert server._load_persisted() == []
    assert 'cache restore failed' in capsys.readouterr().out


def test_load_runs_before_the_thread_and_the_bind():
    """Ordering is load-bearing in both directions: after the thread starts, a
    boot fetch could publish a fresh image and then be overwritten by the
    older disk copy; after the bind, the client's first image request could
    take a 503 we already had the answer to."""
    src = open(server.__file__).read()
    main = src[src.index("if __name__ == '__main__':"):]
    assert main.index('_load_persisted()') < main.index('t.start()')
    assert main.index('_load_persisted()') < main.index('HTTPServer(')


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_a_persisted_image_survives_a_simulated_restart(cachedir):
    server.CACHE['muf_image_png'] = PNG
    server.CACHE['muf_image_png_updated'] = time.time() - 300
    server._persist('muf_image_png')

    # "restart": RAM cache gone, disk untouched.
    server.CACHE['muf_image_png'] = None
    server.CACHE['muf_image_png_updated'] = 0
    server._PERSIST_LAST.clear()

    server._load_persisted()

    assert server.CACHE['muf_image_png'] == PNG
    assert 200 < time.time() - server.CACHE['muf_image_png_updated'] < 400


def test_the_restored_png_is_what_api_muf_map_serves(cachedir):
    server.CACHE['muf_image_png'] = PNG
    server._persist('muf_image_png')
    server.CACHE['muf_image_png'] = None
    server.CACHE['muf_image'] = None
    server._load_persisted()

    h = _handler('/api/muf-map?fmt=png')
    h.do_GET()

    assert h.responses == [200]
    assert h.body == PNG


# ---------------------------------------------------------------------------
# /api/health age fields (Tier 2.5's server half)
# ---------------------------------------------------------------------------


def _handler(path):
    h = type('H', (), {})()
    h.command = 'GET'
    h.path = path
    h.headers = {}
    h.responses = []
    h.headers_out = []
    h.body = b''
    h.send_response = lambda code: h.responses.append(code)
    h.send_header = lambda k, v: h.headers_out.append((k, str(v)))
    h.end_headers = lambda: None
    h.send_error = lambda code, msg=None: h.responses.append(code)

    class _W:
        def write(self, b):
            h.body += b
    h.wfile = _W()
    h.send_json = server.Handler.send_json.__get__(h)
    h.send_json_with_etag = server.Handler.send_json_with_etag.__get__(h)
    h.send_binary = server.Handler.send_binary.__get__(h)
    h.do_GET = server.Handler.do_GET.__get__(h)
    return h


def _health():
    h = _handler('/api/health')
    h.do_GET()
    return json.loads(h.body.decode())


def test_health_reports_the_muf_age(cachedir):
    """Headers reach neither client — the browser gets the map via <img src>
    and hamclock_data._fetch_binary discards them — so /api/health is the
    only channel for an honest staleness label."""
    server.CACHE['muf_image_png'] = PNG
    server.CACHE['muf_image_png_updated'] = time.time() - 7200
    body = _health()
    assert 7150 < body['muf_age'] < 7250


def test_health_muf_age_is_minus_one_when_never_rendered(cachedir):
    server.CACHE['muf_image_png'] = None
    server.CACHE['muf_image_png_updated'] = 0
    assert _health()['muf_age'] == -1


def test_health_muf_source_distinguishes_disk_from_live(cachedir):
    server.CACHE['muf_image_png'] = PNG
    server.CACHE['muf_image_png_updated'] = time.time()

    server._MUF_PNG_SOURCE = 'disk'
    assert _health()['muf_source'] == 'disk'

    server._MUF_PNG_SOURCE = 'live'
    assert _health()['muf_source'] == 'live'


def test_health_muf_source_is_none_without_a_png(cachedir):
    server._MUF_PNG_SOURCE = 'disk'
    server.CACHE['muf_image_png'] = None
    assert _health()['muf_source'] == 'none'


def test_fetch_muf_marks_a_rendered_png_as_live(cachedir, monkeypatch):
    monkeypatch.setattr(server, '_conditional_get',
                        lambda url, timeout=20, record_lm=True: (b'<svg/>',
                                                                 False))
    monkeypatch.setattr(server, '_rasterize_muf', lambda b: PNG)
    server._MUF_PNG_SOURCE = 'disk'

    server.fetch_muf()

    assert server._MUF_PNG_SOURCE == 'live'
    assert (cachedir / 'muf.png').read_bytes() == PNG


def test_health_reports_every_image_age(cachedir):
    now = time.time()
    for key, (_f, stamp) in server._PERSIST_KEYS.items():
        server.CACHE[key] = PNG
        server.CACHE[stamp] = now - 600
    body = _health()
    for field in ('muf_age', 'sdo_age', 'enlil_age', 'drap_age',
                  'real_drap_age'):
        assert 550 < body[field] < 700, field


def test_health_never_reports_a_negative_age(cachedir):
    """No RTC: a stamp restored from disk can legitimately be in this boot's
    future, and a negative age renders as garbage."""
    server.CACHE['muf_image_png_updated'] = time.time() + 86400
    assert _health()['muf_age'] == 0


def test_health_keeps_its_existing_fields(cachedir):
    server.CACHE['solar_updated'] = time.time() - 60
    body = _health()
    assert body['status'] == 'ok'
    for field in ('solar_age', 'bands_age', 'dx_age'):
        assert field in body
