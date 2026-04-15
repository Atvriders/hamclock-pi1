"""Shared data-fetching layer for HamClock Lite native GUI clients.

Polls the same /api/* endpoints the browser uses, caching JSON dicts and
raw image bytes for Pygame/Tkinter kiosks on Raspberry Pi 1.
"""

import json
import threading
import time
import urllib.error
import urllib.request


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
        # Errors (most recent error per key, None if last fetch succeeded)
        self.errors = {}
        # Internal
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def _request(self, path, timeout):
        url = self.server_url + path
        req = urllib.request.Request(url, headers={'User-Agent': self.USER_AGENT})
        return urllib.request.urlopen(req, timeout=timeout)

    def _fetch_json(self, path):
        """HTTP GET path and parse as JSON. Returns dict/list or None on failure."""
        try:
            with self._request(path, self.JSON_TIMEOUT) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            self.errors[path] = None
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
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

    def refresh_images(self):
        """Fetch the 5 image endpoints synchronously."""
        results = {}
        fetched = {}
        for key, path in self._IMAGE_ENDPOINTS.items():
            data = self._fetch_binary(path)
            results[key] = data is not None
            if data is not None:
                fetched[key] = data
        with self._lock:
            new_images = dict(self.images)
            new_images.update(fetched)
            self.images = new_images
            self.last_image_refresh = time.time()
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
        # Immediate initial fetch
        try:
            self.refresh_data()
        except Exception as e:
            self.errors['_run_data'] = '{}: {}'.format(type(e).__name__, e)
        try:
            self.refresh_images()
        except Exception as e:
            self.errors['_run_images'] = '{}: {}'.format(type(e).__name__, e)
        # Sleep-and-check loop
        while self._running:
            for _ in range(5):
                if not self._running:
                    return
                time.sleep(1)
            now = time.time()
            if now - self.last_data_refresh >= data_interval:
                try:
                    self.refresh_data()
                except Exception as e:
                    self.errors['_run_data'] = '{}: {}'.format(type(e).__name__, e)
            if now - self.last_image_refresh >= image_interval:
                try:
                    self.refresh_images()
                except Exception as e:
                    self.errors['_run_images'] = '{}: {}'.format(type(e).__name__, e)

    def stop(self):
        """Signal the background thread to exit."""
        self._running = False

    def lock(self):
        """Return the internal threading.Lock for use as a context manager."""
        return self._lock
