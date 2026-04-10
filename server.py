#!/usr/bin/env python3
"""HamClock Lite — Lightweight server for Raspberry Pi 1"""

import json
import time
import threading
from http.server import SimpleHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer as HTTPServer
except ImportError:
    from http.server import HTTPServer  # Python < 3.7 fallback
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urlparse
from xml.etree import ElementTree
import os

PORT = 8080
CACHE = {
    'solar': None,
    'bands': None,
    'dxspots': None,
    'solar_image': None,
    'solar_updated': 0,
    'bands_updated': 0,
    'dx_updated': 0,
    'solar_image_updated': 0,
    'muf_image': None,
    'muf_image_updated': 0,
    'enlil_image': None,
    'enlil_image_updated': 0,
    'drap_image': None,
    'drap_image_updated': 0,
}

UA = 'HamClockLite/1.0'

# Solar image proxy (NASA SDO)
SDO_URL = 'https://sdo.gsfc.nasa.gov/assets/img/latest/latest_256_HMIIC.jpg'

# Approximate lat/lng for top DXCC entities
COUNTRY_COORDS = {
    'United States': (39, -98), 'Russia': (55, 37), 'Germany': (51, 10),
    'Japan': (36, 140), 'United Kingdom': (52, -1), 'France': (47, 2),
    'Italy': (42, 12), 'Spain': (40, -4), 'Brazil': (-15, -47),
    'Canada': (45, -75), 'Australia': (-25, 134), 'China': (35, 105),
    'India': (20, 77), 'Netherlands': (52, 5), 'Poland': (52, 20),
    'Sweden': (59, 18), 'Argentina': (-34, -58), 'South Africa': (-26, 28),
    'Greece': (38, 24), 'Belgium': (51, 4), 'Portugal': (39, -8),
    'Czech Republic': (50, 15), 'Austria': (48, 16), 'Ukraine': (49, 32),
    'Finland': (61, 25), 'Norway': (60, 11), 'Denmark': (56, 10),
    'Switzerland': (47, 8), 'Croatia': (45, 16), 'Romania': (45, 25),
    'Hungary': (47, 19), 'Ireland': (53, -8), 'Serbia': (44, 21),
    'Bulgaria': (43, 25), 'New Zealand': (-41, 175), 'Chile': (-33, -71),
    'Mexico': (19, -99), 'Colombia': (4, -74), 'Thailand': (14, 101),
    'Indonesia': (-5, 120), 'Philippines': (13, 122), 'South Korea': (37, 127),
    'Turkey': (39, 35), 'Israel': (32, 35), 'Egypt': (30, 31),
    'Nigeria': (10, 8), 'Kenya': (-1, 37), 'Morocco': (32, -5),
    'French Guiana': (4, -53), 'Cuba': (22, -80),
}


def lookup_callsign(call):
    """Look up callsign via callook.info (US) or hamdb.org (international)"""
    result = {'callsign': call, 'grid': None, 'lat': None, 'lng': None, 'name': None, 'country': None}

    # Try callook.info first (US callsigns)
    try:
        req = Request(f'https://callook.info/{call}/json', headers={'User-Agent': UA})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if data.get('status') == 'VALID':
            loc = data.get('location', {})
            result['grid'] = loc.get('gridsquare', '')[:6]
            result['lat'] = float(loc.get('latitude', 0))
            result['lng'] = float(loc.get('longitude', 0))
            result['name'] = data.get('name', '')
            result['country'] = data.get('address', {}).get('line2', 'United States')
            return result
    except Exception:
        pass

    # Fallback: hamdb.org (international)
    try:
        req = Request(f'https://api.hamdb.org/{call}/json/hamclock', headers={'User-Agent': UA})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        cs = data.get('hamdb', {}).get('callsign', {})
        if cs.get('grid'):
            result['grid'] = cs['grid'][:6]
        if cs.get('lat'):
            result['lat'] = float(cs['lat'])
        if cs.get('lon'):
            result['lng'] = float(cs['lon'])
        result['name'] = f"{cs.get('fname', '')} {cs.get('name', '')}".strip()
        result['country'] = cs.get('country', '')
    except Exception:
        pass

    return result


def fetch_hamqsl():
    """Fetch solar and band data from HamQSL XML"""
    try:
        req = Request('https://www.hamqsl.com/solarxml.php', headers={'User-Agent': UA})
        with urlopen(req, timeout=15) as resp:
            xml_data = resp.read().decode('utf-8')

        root = ElementTree.fromstring(xml_data)
        sd = root.find('.//solardata')
        if sd is None:
            return

        def gt(tag, default=''):
            el = sd.find(tag)
            return el.text.strip() if el is not None and el.text else default

        solar = {
            'sfi': gt('solarflux', '0'),
            'ssn': gt('sunspots', '0'),
            'aIndex': gt('aindex', '0'),
            'kIndex': gt('kindex', '0'),
            'xray': gt('xray', 'N/A'),
            'heliumLine': gt('heliumline', 'N/A'),
            'protonFlux': gt('protonflux', 'N/A'),
            'electronFlux': gt('electronflux', 'N/A'),
            'aurora': gt('aurora', '0'),
            'solarWind': gt('solarwind', '0'),
            'magneticField': gt('magneticfield', '0'),
            'geomagField': gt('geomagfield', 'quiet'),
            'signalNoise': gt('signalnoise', 'S0-S0'),
            'fof2': gt('fof2', '0'),
            'mpiVer': gt('mpiVer', ''),
            'updated': gt('updated', ''),
        }

        bands = {}
        for band_el in sd.findall('.//band'):
            name = band_el.get('name', '')
            time_attr = band_el.get('time', '')
            condition = band_el.text or 'N/A'
            if name:
                if name not in bands:
                    bands[name] = {}
                bands[name][time_attr] = condition

        CACHE['solar'] = solar
        CACHE['solar_updated'] = time.time()
        CACHE['bands'] = bands
        CACHE['bands_updated'] = time.time()
        print(f'[{time.strftime("%H:%M:%S")}] Solar/bands updated: SFI={solar["sfi"]} Kp={solar["kIndex"]}')
    except Exception as e:
        print(f'[{time.strftime("%H:%M:%S")}] HamQSL fetch failed: {e}')


def freq_to_band(freq_khz):
    f = float(freq_khz)
    if f < 2000:
        return '160m'
    if f < 4000:
        return '80m'
    if f < 5500:
        return '60m'
    if f < 8000:
        return '40m'
    if f < 11000:
        return '30m'
    if f < 15000:
        return '20m'
    if f < 19000:
        return '17m'
    if f < 22000:
        return '15m'
    if f < 26000:
        return '12m'
    if f < 30000:
        return '10m'
    if f < 55000:
        return '6m'
    if f < 148000:
        return '2m'
    return '70cm'


def fetch_dx():
    """Fetch DX spots from HamQTH or fallback"""
    urls = [
        'https://www.hamqth.com/dxc_csv.php?limit=30',
        'https://www.ha8tks.hu/dx/dxc_csv.php?limit=30',
    ]
    for url in urls:
        try:
            req = Request(url, headers={'User-Agent': UA})
            with urlopen(req, timeout=10) as resp:
                csv_data = resp.read().decode('utf-8', errors='replace')

            spots = []
            for line in csv_data.strip().split('\n'):
                # HamQTH uses ^ as delimiter, some use ,
                sep = '^' if '^' in line else ','
                parts = line.split(sep)
                if len(parts) < 5:
                    continue
                try:
                    # Format: spotter^freq^dx^comment^time^...
                    freq = parts[1].strip()
                    freq_khz = float(freq)
                    country = parts[10].strip() if len(parts) > 10 else ''
                    coords = COUNTRY_COORDS.get(country)
                    spot = {
                        'frequency': freq,
                        'spotter': parts[0].strip(),
                        'dx': parts[2].strip(),
                        'comment': parts[3].strip() if len(parts) > 3 else '',
                        'time': parts[4].strip() if len(parts) > 4 else '',
                        'band': freq_to_band(freq_khz),
                        'country': country,
                        'lat': coords[0] if coords else None,
                        'lng': coords[1] if coords else None,
                    }
                    spots.append(spot)
                except (ValueError, IndexError):
                    continue

            if spots:
                CACHE['dxspots'] = spots
                CACHE['dx_updated'] = time.time()
                print(f'[{time.strftime("%H:%M:%S")}] DX spots updated: {len(spots)} spots from {url.split("/")[2]}')
                return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] DX fetch failed ({url.split("/")[2]}): {e}')
    print(f'[{time.strftime("%H:%M:%S")}] All DX sources failed')


def fetch_muf():
    """Fetch KC2G MUF propagation map SVG"""
    try:
        req = Request('https://prop.kc2g.com/renders/current/mufd-normal-now.svg', headers={'User-Agent': UA})
        with urlopen(req, timeout=20) as resp:
            data = resp.read()
        CACHE['muf_image'] = data
        CACHE['muf_image_updated'] = time.time()
        print(f'[{time.strftime("%H:%M:%S")}] MUF map updated ({len(data)} bytes)')
    except Exception as e:
        print(f'[{time.strftime("%H:%M:%S")}] MUF map fetch failed: {e}')


def fetch_enlil():
    """Fetch WSA-Enlil solar wind prediction image"""
    urls = [
        'https://services.swpc.noaa.gov/images/animations/enlil/latest.jpg',
        'https://services.swpc.noaa.gov/products/animations/enlil.json',
    ]
    for url in urls:
        try:
            req = Request(url, headers={'User-Agent': UA})
            with urlopen(req, timeout=20) as resp:
                data = resp.read()
            if url.endswith('.json'):
                # JSON response — extract latest image URL
                items = json.loads(data.decode('utf-8'))
                if items:
                    last = items[-1]
                    img_url = 'https://services.swpc.noaa.gov' + last.get('url', '')
                    req2 = Request(img_url, headers={'User-Agent': UA})
                    with urlopen(req2, timeout=20) as resp2:
                        data = resp2.read()
            CACHE['enlil_image'] = data
            CACHE['enlil_image_updated'] = time.time()
            print(f'[{time.strftime("%H:%M:%S")}] Enlil updated ({len(data)} bytes)')
            return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] Enlil fetch failed ({url}): {e}')


def fetch_drap():
    """Fetch Aurora forecast (Northern Hemisphere) image"""
    urls = [
        'https://services.swpc.noaa.gov/images/aurora-forecast-northern-hemisphere.jpg',
        'https://services.swpc.noaa.gov/images/swx-overview-large.gif',
    ]
    for url in urls:
        try:
            req = Request(url, headers={'User-Agent': UA})
            with urlopen(req, timeout=20) as resp:
                data = resp.read()
            CACHE['drap_image'] = data
            CACHE['drap_image_updated'] = time.time()
            print(f'[{time.strftime("%H:%M:%S")}] DRAP updated ({len(data)} bytes)')
            return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] DRAP fetch failed ({url}): {e}')


def background_fetcher():
    """Background thread to periodically fetch data"""
    fetch_hamqsl()
    fetch_dx()
    fetch_muf()
    fetch_enlil()
    fetch_drap()

    # Fast retry if initial fetch failed (network might not be ready yet)
    for _ in range(6):
        if CACHE['solar'] and CACHE['dxspots']:
            break
        time.sleep(10)
        if not CACHE['solar']:
            fetch_hamqsl()
        if not CACHE['dxspots']:
            fetch_dx()

    solar_interval = 300  # 5 minutes
    dx_interval = 120     # 2 minutes
    muf_interval = 900    # 15 minutes
    enlil_interval = 900  # 15 minutes
    drap_interval = 900   # 15 minutes
    last_solar = time.time()
    last_dx = time.time()
    last_muf = time.time()
    last_enlil = time.time()
    last_drap = time.time()

    while True:
        time.sleep(10)
        now = time.time()
        if now - last_solar >= solar_interval:
            fetch_hamqsl()
            last_solar = now
        if now - last_dx >= dx_interval:
            fetch_dx()
            last_dx = now
        if now - last_muf >= muf_interval:
            fetch_muf()
            last_muf = now
        if now - last_enlil >= enlil_interval:
            fetch_enlil()
            last_enlil = now
        if now - last_drap >= drap_interval:
            fetch_drap()
            last_drap = now


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/solar':
            self.send_json(CACHE.get('solar') or {})
        elif path == '/api/bands':
            self.send_json(CACHE.get('bands') or {})
        elif path == '/api/dxspots':
            self.send_json(CACHE.get('dxspots') or [])
        elif path == '/api/solar-image':
            # Fetch/cache SDO solar image (15 min cache)
            now = time.time()
            if CACHE['solar_image'] is None or now - CACHE['solar_image_updated'] > 900:
                try:
                    req = Request(SDO_URL, headers={'User-Agent': UA})
                    with urlopen(req, timeout=20) as resp:
                        CACHE['solar_image'] = resp.read()
                        CACHE['solar_image_updated'] = now
                except Exception as e:
                    print(f'[{time.strftime("%H:%M:%S")}] SDO image fetch failed: {e}')
                    if CACHE['solar_image'] is None:
                        self.send_error(502, 'Failed to fetch solar image')
                        return
            self.send_binary(CACHE['solar_image'], 'image/jpeg')
        elif path.startswith('/api/muf-map'):
            if CACHE.get('muf_image'):
                body = CACHE['muf_image']
                self.send_response(200)
                self.send_header('Content-Type', 'image/svg+xml')
                self.send_header('Content-Length', len(body))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'public, max-age=300')
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json({'error': 'MUF map not yet loaded'})
        elif path.startswith('/api/enlil'):
            if CACHE.get('enlil_image'):
                self.send_binary(CACHE['enlil_image'], 'image/jpeg')
            else:
                self.send_json({'error': 'not loaded'})
        elif path.startswith('/api/drap'):
            if CACHE.get('drap_image'):
                self.send_binary(CACHE['drap_image'], 'image/png')
            else:
                self.send_json({'error': 'not loaded'})
        elif path.startswith('/api/callsign/'):
            call = path.split('/')[-1].upper()
            result = lookup_callsign(call)
            self.send_json(result)
        elif path == '/api/health':
            self.send_json({
                'status': 'ok',
                'solar_age': int(time.time() - CACHE['solar_updated']) if CACHE['solar_updated'] else -1,
                'bands_age': int(time.time() - CACHE['bands_updated']) if CACHE['bands_updated'] else -1,
                'dx_age': int(time.time() - CACHE['dx_updated']) if CACHE['dx_updated'] else -1,
            })
        else:
            super().do_GET()

    def send_json(self, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_binary(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(data))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'public, max-age=900')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass  # Suppress request logs for performance


if __name__ == '__main__':
    print(f'HamClock Lite starting on port {PORT}...')
    t = threading.Thread(target=background_fetcher, daemon=True)
    t.start()
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Server ready: http://localhost:{PORT}')
    server.serve_forever()
