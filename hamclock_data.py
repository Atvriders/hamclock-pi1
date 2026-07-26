"""Shared data-fetching layer for HamClock Lite native GUI clients.

Polls the same /api/* endpoints the browser uses, caching JSON dicts and
raw image bytes for Pygame/Tkinter kiosks on Raspberry Pi 1.
"""

import json
import threading
import time
import urllib.error
import urllib.request
# Re-exported so tests can monkeypatch 'hamclock_data.urlopen' / 'Request'
# and _fetch_json picks up the fake — keeps the patch site stable across
# refactors of the request helper.
from urllib.request import Request, urlopen


class HamClockData:
    """Thread-safe data-fetching layer for HamClock Lite native clients.

    Polls /api/* JSON endpoints and binary image endpoints on configurable
    intervals. Native GUI code reads the cached attributes directly
    (they're updated in-place by the background thread).

    Attribute usage is lock-free for single-reader GUI loops: the GIL
    makes single-key dict reads atomic, and the background thread only
    does whole-dict assignments. For multi-reader scenarios, use the
    lock() context manager.
    """

    DEFAULT_SERVER = 'http://localhost:8080'
    USER_AGENT = 'HamClockNative/1.0'
    JSON_TIMEOUT = 10
    IMAGE_TIMEOUT = 20

    # Tier 1.4: per-key retry backoff. Index N is the delay (seconds) after
    # the Nth consecutive failure of that key; the last entry repeats
    # forever. Retries therefore land at cumulative 5/15/35/75/135 s and
    # then once a minute, instead of the old "one shot, then nothing for
    # 900 s" behaviour that left the propagation panel blank for 15 minutes
    # after a single cold-boot miss.
    IMAGE_RETRY_BACKOFF = (5, 10, 20, 40, 60)

    _JSON_ENDPOINTS = {
        'solar': '/api/solar',
        'bands': '/api/bands',
        'dxspots': '/api/dxspots',
        'health': '/api/health',
    }
    _IMAGE_ENDPOINTS = {
        'solar-image': '/api/solar-image',
        'muf-map': '/api/muf-map',
        'enlil': '/api/enlil',
        'drap': '/api/drap',
        'real-drap': '/api/real-drap',
    }

    def __init__(self, server_url='http://localhost:8080'):
        """Initialize with the HamClock server URL (default localhost:8080)."""
        self.server_url = server_url.rstrip('/')
        # JSON cache
        self.solar = {}
        self.bands = {}
        self.dxspots = []
        self.health = {}
        # Binary image cache
        self.images = {}
        # Timestamps (Unix seconds; 0 means never)
        self.last_data_refresh = 0
        self.last_image_refresh = 0
        # Per-image refresh timestamps (epoch seconds). Maps image_key
        # ('solar-image' | 'muf-map' | 'enlil' | 'drap' | 'real-drap')
        # to the epoch-second when that key's bytes last refreshed.
        # Used by the pygame client's _scaled_cache to invalidate per-image.
        self.image_fetched_at = {}
        # Tier 1.4 retry scheduling, both keyed by image_key.
        #   image_next_due[key]    epoch second at/after which key may be
        #                          attempted again (missing => due now)
        #   image_fail_streak[key] consecutive failures; indexes
        #                          IMAGE_RETRY_BACKOFF
        # Written only by the fetch thread; single-key reads are atomic
        # under the GIL, so GUI code may sample them without the lock.
        self.image_next_due = {}
        self.image_fail_streak = {}
        # Slow (healthy) image cadence in seconds. _run() overwrites this
        # with the caller's image_interval; the default matches
        # start_background()'s so a manual refresh_images() before the
        # thread starts schedules sanely.
        self._image_interval = 900
        # Errors (most recent error per key, None if last fetch succeeded)
        self.errors = {}
        # Tier 2c perf: ETags by path so we can replay If-None-Match on the
        # next poll. ~80% of /api/{solar,bands,dxspots} polls land on
        # unchanged data; a 304 short-circuits the read+json.loads here.
        self._etags = {}
        # Internal
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def _request(self, path, timeout):
        url = self.server_url + path
        req = urllib.request.Request(url, headers={'User-Agent': self.USER_AGENT})
        return urllib.request.urlopen(req, timeout=timeout)

    def _fetch_json(self, path):
        """HTTP GET path and parse as JSON. Returns dict/list or None on failure.

        Tier 2c perf: sends If-None-Match when we have a prior ETag for
        this path. Returns None on 304 (caller should keep its cached
        value — same semantics as the existing error path).
        """
        url = self.server_url + path
        req = Request(url, headers={'User-Agent': self.USER_AGENT})
        prev_etag = self._etags.get(path)
        if prev_etag:
            req.add_header('If-None-Match', prev_etag)
        try:
            with urlopen(req, timeout=self.JSON_TIMEOUT) as resp:
                new_etag = resp.headers.get('ETag')
                if new_etag:
                    self._etags[path] = new_etag
                data = json.loads(resp.read().decode('utf-8'))
            self.errors[path] = None
            return data
        except urllib.error.HTTPError as e:
            if e.code == 304:
                # Server says: no change since prev_etag. Skip parse;
                # caller keeps its cached value (existing 'None means
                # don't overwrite' contract in refresh_data).
                self.errors[path] = None
                return None
            self.errors[path] = '{}: {}'.format(type(e).__name__, e)
            return None
        except (urllib.error.URLError, ValueError, OSError) as e:
            self.errors[path] = '{}: {}'.format(type(e).__name__, e)
            return None

    def _fetch_binary(self, path):
        """HTTP GET path and return raw bytes. Returns bytes or None on failure."""
        try:
            with self._request(path, self.IMAGE_TIMEOUT) as resp:
                data = resp.read()
            self.errors[path] = None
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            self.errors[path] = '{}: {}'.format(type(e).__name__, e)
            return None

    def refresh_data(self):
        """Fetch the 4 JSON endpoints synchronously."""
        results = {}
        fetched = {}
        for key, path in self._JSON_ENDPOINTS.items():
            data = self._fetch_json(path)
            results[key] = data is not None
            if data is not None:
                fetched[key] = data
        with self._lock:
            if 'solar' in fetched:
                self.solar = fetched['solar'] if isinstance(fetched['solar'], dict) else {}
            if 'bands' in fetched:
                self.bands = fetched['bands'] if isinstance(fetched['bands'], dict) else {}
            if 'dxspots' in fetched:
                self.dxspots = fetched['dxspots'] if isinstance(fetched['dxspots'], list) else []
            if 'health' in fetched:
                self.health = fetched['health'] if isinstance(fetched['health'], dict) else {}
            self.last_data_refresh = time.time()
        return results

    def _next_image_delay(self, key):
        """Seconds to wait before the next attempt of image `key`.

        Healthy keys get the slow cadence; failing keys walk
        IMAGE_RETRY_BACKOFF and saturate on its last entry.
        """
        streak = self.image_fail_streak.get(key, 0)
        if streak <= 0:
            return self._image_interval
        idx = min(streak, len(self.IMAGE_RETRY_BACKOFF)) - 1
        return self.IMAGE_RETRY_BACKOFF[idx]

    def _reschedule_image(self, key, ok, now):
        """Record the outcome of one image attempt and set its next due time.

        Called from a `finally`, so it must not raise for any input.
        """
        if ok:
            self.image_fail_streak[key] = 0
        else:
            self.image_fail_streak[key] = self.image_fail_streak.get(key, 0) + 1
        self.image_next_due[key] = now + self._next_image_delay(key)

    def _due_image_keys(self, now):
        """Image keys whose next-due time has passed, or None if none are.

        Returns None rather than [] in the common case: the 1 s tick calls
        this every second on a single-core ARMv6 box and the quiescent path
        must not allocate.
        """
        due = None
        for key in self._IMAGE_ENDPOINTS:
            if now >= self.image_next_due.get(key, 0.0):
                if due is None:
                    due = []
                due.append(key)
        return due

    def refresh_images(self, keys=None):
        """Fetch image endpoints synchronously. keys=None means all five.

        Every attempted key is rescheduled unconditionally (in a `finally`),
        including on an exception _fetch_binary does not catch — a
        MemoryError escaping the except clause at _fetch_binary would
        otherwise leave the key permanently due and hot-spin the 1 s tick.

        last_image_refresh is stamped only when at least one key came back
        with bytes. Stamping it after a total failure is what used to buy a
        blank panel for a full image_interval.
        """
        if keys is None:
            keys = self._IMAGE_ENDPOINTS
        results = {}
        fetched = {}
        try:
            for key in keys:
                path = self._IMAGE_ENDPOINTS.get(key)
                if path is None:
                    continue
                ok = False
                try:
                    data = self._fetch_binary(path)
                    ok = data is not None
                    results[key] = ok
                    if ok:
                        fetched[key] = data
                finally:
                    self._reschedule_image(key, ok, time.time())
        finally:
            # Also a `finally` so bytes already in hand are published even if
            # a later key blows up — those keys are rescheduled 900 s out and
            # dropping their payload here would recreate the blank-panel bug.
            if fetched:
                # Read the clock after the fetches, not before: image ages
                # should reflect when the bytes landed.
                now = time.time()
                with self._lock:
                    new_images = dict(self.images)
                    new_images.update(fetched)
                    self.images = new_images
                    new_ts = dict(self.image_fetched_at)
                    for key in fetched:
                        new_ts[key] = now
                    self.image_fetched_at = new_ts
                    self.last_image_refresh = now
        return results

    def start_background(self, data_interval=60, image_interval=900):
        """Start a daemon thread that refreshes data/images on their intervals."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, args=(data_interval, image_interval), daemon=True
        )
        self._thread.start()

    def _run(self, data_interval, image_interval):
        self._image_interval = image_interval
        # Immediate initial fetch
        try:
            self.refresh_data()
        except Exception as e:
            self.errors['_run_data'] = '{}: {}'.format(type(e).__name__, e)
        try:
            self.refresh_images()
        except Exception as e:
            self.errors['_run_images'] = '{}: {}'.format(type(e).__name__, e)
        # 1 s tick: image retries need second-resolution scheduling, the JSON
        # cadence does not, so the data check stays on a 5-tick (5 s) grid.
        tick = 0
        while self._running:
            time.sleep(1)
            if not self._running:
                return
            tick = (tick + 1) % 5
            now = time.time()
            if tick == 0 and now - self.last_data_refresh >= data_interval:
                try:
                    self.refresh_data()
                except Exception as e:
                    self.errors['_run_data'] = '{}: {}'.format(type(e).__name__, e)
                # refresh_data can block for up to 4 x JSON_TIMEOUT; re-read
                # the clock so image due-times aren't judged against a stale
                # `now`.
                now = time.time()
            due = self._due_image_keys(now)
            if due:
                try:
                    self.refresh_images(due)
                except Exception as e:
                    self.errors['_run_images'] = '{}: {}'.format(type(e).__name__, e)

    def stop(self):
        """Signal the background thread to exit."""
        self._running = False

    def lock(self):
        """Return the internal threading.Lock for use as a context manager."""
        return self._lock
