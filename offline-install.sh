#!/bin/bash
# HamClock Pi1 — Full Installer (Kiosk + Server)
# Self-contained: all files are embedded, no external downloads needed.
#
# Usage (online — curl pipe is safe because main() only runs after full download):
#   curl -sL https://hamclock-reborn.org/downloads/pi1-install.sh | bash
#
# Usage (offline — copy to USB):
#   1. Copy this file to a USB drive
#   2. Plug USB into your Pi
#   3. Mount the USB: sudo mount /dev/sda1 /mnt
#   4. Run: bash /mnt/offline-install.sh
#   5. Unplug USB when done

main() {
set -euo pipefail

KIOSK_MODE="browser"
for arg in "$@"; do
    case "$arg" in
        --pygame)  KIOSK_MODE="pygame" ;;
        --tkinter) KIOSK_MODE="tkinter" ;;
        --browser) KIOSK_MODE="browser" ;;
        --help|-h) echo "Usage: curl ... | bash -s -- [--browser|--pygame|--tkinter]"; exit 0 ;;
        *) echo "Unknown arg: $arg (try --help)"; exit 1 ;;
    esac
done
echo "Kiosk mode: $KIOSK_MODE"

INSTALL_DIR="/opt/hamclock-lite"
SERVICE_USER="${SUDO_USER:-${USER:-root}}"

echo "=== HamClock Pi1 — Full Installer ==="
echo "This will install HamClock with kiosk mode (fullscreen on monitor)."
echo "Estimated time: 15-30 minutes on Pi 1"
echo ""

# ── Step 1: Check Python 3 ──────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required. Run: sudo apt install python3"
    exit 1
fi

# ── Step 2: Check internet connectivity (needed for apt) ────────────
if ! ping -c 1 -W 3 google.com &>/dev/null && ! ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
    echo "ERROR: No internet connection detected."
    echo "Please connect the Pi to the internet and try again."
    exit 1
fi

# ── Step 3: Write embedded server.py ────────────────────────────────
echo "Writing server.py..."
sudo mkdir -p "$INSTALL_DIR"
sudo tee "$INSTALL_DIR/server.py" > /dev/null << 'SERVEREOF'
#!/usr/bin/env python3
"""HamClock Lite — Lightweight server for Raspberry Pi 1"""

import json
import time
import threading
import subprocess
import re
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
    'real_drap_image': None,
    'real_drap_image_updated': 0,
    'host_ntp': None,
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


def fetch_real_drap():
    """Fetch DRAP (D-Region Absorption Prediction) global image"""
    urls = [
        'https://services.swpc.noaa.gov/images/animations/d-rap/global/latest.png',
        'https://services.swpc.noaa.gov/images/d-rap/global_f10.png',
    ]
    for url in urls:
        try:
            req = Request(url, headers={'User-Agent': UA})
            with urlopen(req, timeout=20) as resp:
                data = resp.read()
            CACHE['real_drap_image'] = data
            CACHE['real_drap_image_updated'] = time.time()
            print(f'[{time.strftime("%H:%M:%S")}] DRAP updated ({len(data)} bytes)')
            return
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] DRAP fetch failed ({url}): {e}')


_NTP_HOSTNAME_RE = re.compile(r'^[a-z0-9\-\.]+$', re.IGNORECASE)


def _valid_ntp_hostname(s):
    if not s:
        return False
    s = s.strip()
    if not s:
        return False
    return ('.' in s) or bool(_NTP_HOSTNAME_RE.match(s))


def _parse_ntp_conf_line(path, keywords):
    """Parse a config file; return first token after any of `keywords` on a non-comment line."""
    try:
        with open(path, 'r') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or line.startswith(';'):
                    continue
                # strip inline comments
                for c in ('#', ';'):
                    idx = line.find(c)
                    if idx >= 0:
                        line = line[:idx].strip()
                if not line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                head = parts[0].lower()
                for kw in keywords:
                    if kw.endswith('='):
                        # NTP= style — may be joined or split
                        if parts[0].lower().startswith(kw.lower()):
                            # e.g. "NTP=time.example.com foo"
                            after = line.split('=', 1)[1].strip()
                            toks = after.split()
                            if toks and _valid_ntp_hostname(toks[0]):
                                return toks[0]
                    else:
                        if head == kw.lower() and len(parts) >= 2:
                            if _valid_ntp_hostname(parts[1]):
                                return parts[1]
    except Exception:
        pass
    return None


def get_host_ntp():
    """Return the host's active NTP server name, trying several sources."""
    try:
        # 1. timedatectl
        try:
            r = subprocess.run(
                ['timedatectl', 'show-timesync', '--property=ServerName', '--value'],
                capture_output=True, text=True, timeout=2
            )
            name = (r.stdout or '').strip()
            if _valid_ntp_hostname(name):
                return name
        except Exception:
            pass

        # 2. /etc/systemd/timesyncd.conf — look for NTP=
        val = _parse_ntp_conf_line('/etc/systemd/timesyncd.conf', ['NTP='])
        if val:
            return val

        # 3. chrony
        for p in ('/etc/chrony/chrony.conf', '/etc/chrony.conf'):
            val = _parse_ntp_conf_line(p, ['server', 'pool'])
            if val:
                return val

        # 4. ntpd
        val = _parse_ntp_conf_line('/etc/ntp.conf', ['server', 'pool'])
        if val:
            return val
    except Exception:
        pass

    # 5. Fallback
    return 'pool.ntp.org'


def background_fetcher():
    """Background thread to periodically fetch data"""
    fetch_hamqsl()
    fetch_dx()
    fetch_muf()
    fetch_enlil()
    fetch_drap()
    fetch_real_drap()

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
            fetch_real_drap()
            last_drap = now


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def do_HEAD(self):
        self.do_GET()

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
                if self.command != 'HEAD':
                    self.wfile.write(body)
            else:
                self.send_error(503, 'MUF map not yet loaded')
        elif path.startswith('/api/enlil'):
            if CACHE.get('enlil_image'):
                self.send_binary(CACHE['enlil_image'], 'image/jpeg')
            else:
                self.send_error(503, 'Enlil image not yet loaded')
        elif path.startswith('/api/real-drap'):
            if CACHE.get('real_drap_image'):
                self.send_binary(CACHE['real_drap_image'], 'image/png')
            else:
                self.send_error(503, 'DRAP image not yet loaded')
        elif path.startswith('/api/drap'):
            if CACHE.get('drap_image'):
                self.send_binary(CACHE['drap_image'], 'image/jpeg')
            else:
                self.send_error(503, 'Aurora image not yet loaded')
        elif path.startswith('/api/callsign/'):
            call = path.split('/')[-1].upper()
            result = lookup_callsign(call)
            self.send_json(result)
        elif path == '/api/ntp':
            if CACHE.get('host_ntp') is None:
                CACHE['host_ntp'] = get_host_ntp()
            self.send_json({'ntp': CACHE['host_ntp']})
        elif path == '/api/health':
            self.send_json({
                'status': 'ok',
                'solar_age': int(time.time() - CACHE['solar_updated']) if CACHE['solar_updated'] else -1,
                'bands_age': int(time.time() - CACHE['bands_updated']) if CACHE['bands_updated'] else -1,
                'dx_age': int(time.time() - CACHE['dx_updated']) if CACHE['dx_updated'] else -1,
            })
        else:
            if self.command == 'HEAD':
                super().do_HEAD()
            else:
                super().do_GET()

    def send_json(self, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_binary(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(data))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'public, max-age=900')
        self.end_headers()
        if self.command != 'HEAD':
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
SERVEREOF
sudo chmod +x "$INSTALL_DIR/server.py"

# ── Step 4: Write embedded index.html ───────────────────────────────
echo "Writing index.html..."
sudo tee "$INSTALL_DIR/index.html" > /dev/null << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HamClock Lite</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
--bg:#0a0e14;--card:#111820;--border:#1a2530;--text:#c8d0d8;--bright:#e8f0f0;
--green:#22c55e;--yellow:#eab308;--red:#ef4444;--cyan:#06b6d4;--muted:#4a5568;--label:#6b7b8d;--callsign:#f472b6;
}
html,body{
width:100%;height:100vh;overflow:hidden;
background:var(--bg);color:var(--text);
font-family:'Courier New','Liberation Mono',monospace;
font-size:clamp(16px,1.4vh,16px);
}
.hdr{
display:flex;align-items:center;justify-content:space-between;
height:clamp(20px,3vh,30px);
padding:0 clamp(4px,0.8vw,12px);
background:var(--card);border-bottom:1px solid var(--border);
font-size:clamp(14px,1.3vh,14px);
}
.hdr-title{color:var(--cyan);font-weight:bold;letter-spacing:2px}
.powercat{height:clamp(18px,2.5vh,24px);width:auto;vertical-align:middle;margin-right:clamp(6px,0.8vw,10px);color:var(--cyan);display:none}
body.theme-kstate .powercat{display:inline-block}
.hdr-clocks{color:var(--bright);letter-spacing:1px}
.hdr-clocks span{margin-left:clamp(8px,2vw,24px)}
.hdr-utc{color:var(--cyan)}
.hdr-dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#22c55e;margin-left:clamp(8px,1.5vw,16px);animation:blink-status 1.2s steps(2,start) infinite}
@keyframes blink-status{50%{opacity:0}}
.grid{
display:grid;
grid-template-columns:20vw 1fr 25vw;
gap:clamp(2px,0.4vh,6px);
padding:clamp(2px,0.4vh,6px);
height:calc(100vh - clamp(20px,3vh,30px) - clamp(16px,2.5vh,28px));
overflow:hidden;
}
.col{display:flex;flex-direction:column;gap:clamp(2px,0.4vh,6px);overflow:hidden;min-height:0}
.panel{
background:var(--card);border:1px solid var(--border);
overflow:hidden;position:relative;
display:flex;flex-direction:column;
}
.panel-title{
display:flex;justify-content:space-between;align-items:center;
padding:2px 6px;
background:var(--bg);
font-size:clamp(13px,1.1vh,13px);
color:var(--label);letter-spacing:1px;
border-bottom:1px solid var(--border);
flex-shrink:0;
}
.panel-body{padding:4px 6px;flex:1;overflow:hidden}
.solar-flex .panel-body{overflow-y:auto}
.solar-flex .panel-body::-webkit-scrollbar{width:0;background:transparent}
.timer{color:var(--muted);font-size:clamp(11px,0.9vh,11px)}
.tab{cursor:pointer;padding:0 8px;color:var(--label);user-select:none}
.tab-active{color:var(--cyan);font-weight:bold}
.solar-flex{flex:1;min-height:0}
.bands-flex{flex:0 0 auto}
.mid-img{flex:0 0 auto;min-height:0;overflow:hidden}
.dx-full{flex:1;min-height:0}
.s-row{
display:flex;justify-content:space-between;align-items:center;
padding:clamp(0px,0.12vh,2px) 0;
border-bottom:1px solid var(--border);
}
.s-lbl{color:var(--label);font-size:clamp(12px,1vh,12px);flex:0 0 clamp(40px,5vw,70px)}
.s-val{color:var(--bright);font-size:clamp(14px,1.2vh,14px);font-weight:bold;text-align:right;flex:1}
.kp-wrap{display:flex;align-items:center;gap:clamp(2px,0.3vw,6px)}
.kp-bar{height:clamp(6px,0.8vh,10px);background:var(--bg);flex:1;overflow:hidden}
.kp-fill{height:100%}
.band-row{
display:flex;align-items:center;
padding:clamp(1px,0.15vh,2px) 0;
border-bottom:1px solid var(--border);
font-size:clamp(12px,1vh,12px);
}
.band-name{flex:0 0 clamp(40px,5vw,60px);color:var(--bright);font-weight:bold}
.band-cond{
flex:0 0 clamp(16px,2vw,24px);
text-align:center;
font-weight:bold;
font-size:clamp(12px,1vh,12px);
margin:0 clamp(2px,0.3vw,4px);
padding:clamp(0px,0.1vh,1px) 0;
}
.band-lbl{color:var(--label);font-size:clamp(10px,0.8vh,10px);flex:0 0 clamp(24px,3vw,36px);text-align:center}
.cG{background:#22c55e;color:#000}.cF{background:#eab308;color:#000}.cP{background:#ef4444;color:#fff}.cN{background:var(--muted);color:#fff}
.img-wrap{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;min-height:0}
.img-wrap img{object-fit:contain;max-width:100%;max-height:100%;display:block}
#imgSolar{height:22vh;width:100%;object-fit:contain}
#imgMuf{height:auto;width:100%;max-height:90vh;object-fit:contain}
#imgEnlil{width:100%;object-fit:contain}
#imgDrap{width:100%;object-fit:contain}
.dx-tbl{width:100%;border-collapse:collapse}
.dx-tbl th{
font-size:clamp(12px,1vw,12px);color:var(--label);
text-align:left;padding:clamp(1px,0.2vh,3px) clamp(2px,0.3vw,6px);
border-bottom:1px solid var(--border);
position:sticky;top:0;background:var(--card);
letter-spacing:1px;
}
.dx-tbl td{
padding:clamp(1px,0.15vh,2px) clamp(2px,0.3vw,6px);
border-bottom:1px solid var(--border);
font-size:clamp(13px,1.2vw,13px);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.dx-freq{color:var(--label);font-size:clamp(13px,1.2vw,13px)}
.dx-band{color:var(--cyan);font-size:clamp(12px,1.1vw,12px)}
.dx-call{color:var(--bright);font-weight:bold;font-size:clamp(13px,1.2vw,13px)}
.dx-sp{color:var(--muted);font-size:clamp(12px,1.1vw,12px)}
.dx-tm{color:var(--muted);font-size:clamp(12px,1.1vw,12px)}
.dx-body-wrap{flex:1;overflow:hidden;min-height:0}
.sbar{
display:flex;align-items:center;justify-content:space-between;
height:clamp(16px,2.5vh,28px);
padding:0 clamp(6px,1vw,20px);
background:var(--card);border-top:1px solid var(--border);
font-size:clamp(11px,0.9vh,11px);color:var(--muted);
flex-shrink:0;
}
.stale{opacity:0.5}
.stale::after{content:' (stale)';color:var(--yellow)}
/* Setup screen */
#setup{
position:fixed;inset:0;z-index:9999;
background:var(--bg);
display:flex;align-items:center;justify-content:center;
font-family:'Courier New','Liberation Mono',monospace;
}
.setup-card{
background:var(--card);
border:1px solid var(--border);
padding:24px 32px;
max-width:460px;
width:90%;
}
.setup-title{
color:var(--cyan);
font-size:18px;
font-weight:bold;
letter-spacing:3px;
text-align:center;
margin-bottom:20px;
}
.setup-field{margin-bottom:14px}
.setup-label{
display:block;
color:var(--label);
font-size:10px;
letter-spacing:1px;
margin-bottom:4px;
}
.setup-input{
width:100%;
background:var(--bg);
border:1px solid var(--border);
color:var(--bright);
padding:6px 10px;
font-family:inherit;
font-size:14px;
}
.setup-input:focus{border-color:var(--cyan);outline:none}
.setup-select{
width:100%;
background:var(--bg);
border:1px solid var(--border);
color:var(--bright);
padding:6px 10px;
font-family:inherit;
font-size:13px;
}
.theme-row{display:flex;gap:12px}
.theme-opt{flex:1;text-align:center;cursor:pointer}
.theme-opt input{display:none}
.theme-swatch{
display:block;
height:32px;
border:2px solid var(--border);
margin-bottom:4px;
}
.theme-opt input:checked + .theme-swatch{border-color:var(--bright)}
.theme-name{font-size:10px;color:var(--label)}
.setup-btn{
display:block;
width:100%;
margin-top:20px;
padding:10px;
background:var(--cyan);
color:var(--bg);
border:none;
font-family:inherit;
font-size:14px;
font-weight:bold;
letter-spacing:2px;
cursor:pointer;
}
</style>
</head>
<body>
<!-- Setup wizard overlay -->
<div id="setup" style="display:none">
<div class="setup-card">
<div class="setup-title">HAMCLOCK LITE SETUP</div>
<div class="setup-field">
<label class="setup-label">YOUR CALLSIGN</label>
<input type="text" id="inCallsign" class="setup-input" placeholder="W1ABC" style="text-transform:uppercase">
<span id="callLookupName" style="font-size:10px;color:var(--green);display:block;margin-top:2px;min-height:14px"></span>
</div>
<div class="setup-field">
<label class="setup-label">GRID SQUARE</label>
<input type="text" id="inGrid" class="setup-input" placeholder="FN31" maxlength="6" style="text-transform:uppercase">
</div>
<div class="setup-field">
<label class="setup-label">TIMEZONE</label>
<select id="inTimezone" class="setup-select">
<option value="auto">Auto (Browser)</option>
<option value="UTC">UTC</option>
<option value="US/Eastern">US/Eastern</option>
<option value="US/Central">US/Central</option>
<option value="US/Mountain">US/Mountain</option>
<option value="US/Pacific">US/Pacific</option>
<option value="US/Alaska">US/Alaska</option>
<option value="US/Hawaii">US/Hawaii</option>
<option value="Europe/London">Europe/London</option>
<option value="Europe/Berlin">Europe/Berlin</option>
<option value="Europe/Paris">Europe/Paris</option>
<option value="Europe/Moscow">Europe/Moscow</option>
<option value="Asia/Tokyo">Asia/Tokyo</option>
<option value="Asia/Shanghai">Asia/Shanghai</option>
<option value="Asia/Kolkata">Asia/Kolkata</option>
<option value="Australia/Sydney">Australia/Sydney</option>
</select>
</div>
<div class="setup-field">
<label class="setup-label">COLOR THEME</label>
<div class="theme-row">
<label class="theme-opt">
<input type="radio" name="theme" value="classic" checked>
<span class="theme-swatch" style="background:#0a0e14;border-bottom:3px solid #22c55e"></span>
<span class="theme-name">CLASSIC</span>
</label>
<label class="theme-opt">
<input type="radio" name="theme" value="amber">
<span class="theme-swatch" style="background:#1a1000;border-bottom:3px solid #f59e0b"></span>
<span class="theme-name">AMBER</span>
</label>
<label class="theme-opt">
<input type="radio" name="theme" value="blue">
<span class="theme-swatch" style="background:#0a0f1e;border-bottom:3px solid #60a5fa"></span>
<span class="theme-name">BLUE</span>
</label>
<label class="theme-opt">
<input type="radio" name="theme" value="red">
<span class="theme-swatch" style="background:#1a0a0a;border-bottom:3px solid #f87171"></span>
<span class="theme-name">RED</span>
</label>
<label class="theme-opt" id="themeKstate" style="display:none">
<input type="radio" name="theme" value="kstate">
<span class="theme-swatch" style="background:#512888;border-bottom:3px solid #FFFFFF"></span>
<span class="theme-name">K-STATE</span>
</label>
</div>
</div>
<div class="setup-field">
<label class="setup-label">TIME SERVER (NTP)</label>
<input type="text" id="inNtp" class="setup-input" placeholder="(using host NTP)">
</div>
<button class="setup-btn" onclick="saveSetup()">START</button>
</div>
</div>
<!-- Dashboard -->
<div id="dashboard">
<div class="hdr">
<span>
<svg class="powercat" viewBox="0 0 300 222.84" aria-hidden="true"><path fill="currentColor" d="M299.82 58.88c-5 16-13.15 30.68-23.6 44.37-3.31-.23-6.62-1-9.92-1.46-2.62 5.85-6.15 11.61-11.62 16.15a1.15 1.15 0 01-1.38-.23c-1.31-1.85.46-4 .62-6a21.07 21.07 0 00-4.46-18.07 22.56 22.56 0 00-9.92-5.64c-15.54-3-29.76 5.26-38.45 17.94-26.61-8.38-59.14-15.3-88.82-11.68-40.76 5.84-81.51 18.14-106.81 54.82A105.5 105.5 0 000 158.39v-139c11.15-4.49 22.76-7.72 34.6-10.64C46.91 24.28 63.83 34.58 82.43 37c13.92 1.81 27.57-4.19 33.92-16.8A30.9 30.9 0 00119.42.82a268.58 268.58 0 0157.75 1.77A175.28 175.28 0 01208.39 10a130.16 130.16 0 0115.38 6.22c.62.3 1.08.62 1 1.31s-.3 1.32-.92 1.31c-6.63-.17-12.84-.46-19.3-.77a1.85 1.85 0 00-1.85.85c-.28.43.48.69.93.76a342.15 342.15 0 0145.21 9.85 153.28 153.28 0 0148.75 24.6c1.41 1.06 2.93 2.68 2.23 4.75zM77.13 29.81c8.23 2.16 18.45 1.54 25.45-3.07 7.77-5 12-16.23 10.76-25.45H37.22C47.37 15.05 61 26.51 77.13 29.81zm29.14 181.1c-12.07-15.23-14.38-39.3-5.38-56.75 8.84-20 29.84-30.07 50-32.68 14.38-2.16 29.46-.46 43.37 2.15a59.86 59.86 0 013.23-12.84c-6.61-1.54-13.15-3.46-19.84-4.77-19.37-3.77-40-6.38-60.36-4.23-19.69 2.08-38.45 7.08-55.06 16.92-12.38 6.69-24.91 17.92-29.84 31.22-2.15 5.84-4.07 11.84-4.07 18.45.23 19.92 13.46 39.07 31.76 47.37 11.45 5.85 25.14 8.85 38.75 6 4.69-1 9.39-2 13.38-4.31a69.83 69.83 0 01-5.94-6.53zM239.3 152c2.54-5.61 4.77-11.79 5.08-18.25-.23-.77-1-.84-1.69-.92-5.38 7.46-12.71 13.93-21.22 16.53-3.77 1.15-9.43 1.94-13.23.85-8.31-2.39-13.77-11.23-15-19.31a58.7 58.7 0 00-9.53-1.76c-20.3-2.08-40.91 3.15-52.52 20.76-7.46 11.84-6.2 28.85.38 41.52a26.08 26.08 0 009.92 10.46c1.69-5.15 5.46-9.84 9.92-13.38a34.65 34.65 0 0118-5.84c11.76-.39 22.68 6.61 28 17.22 15-6.84 29.76-16.62 41.6-29.37a66.59 66.59 0 008.3-10.31 76.48 76.48 0 00-8.01-8.2z"/></svg>
<span class="hdr-title">HAMCLOCK LITE</span>
<span id="hdrCallsign" style="color:var(--callsign);margin-left:12px;cursor:pointer"></span>
</span>
<span class="hdr-clocks">
<span class="hdr-utc" id="utc">UTC --:--:--</span>
<span id="lcl">LOCAL --:--:--</span>
</span>
<span class="hdr-dot" id="statusDot"></span>
</div>
<div class="grid">
<div class="col">
<div class="panel solar-flex">
<div class="panel-title"><span>SOLAR</span><span class="timer" id="tmSolar"></span></div>
<div class="panel-body" id="solarPanel">Loading...</div>
</div>
<div class="panel bands-flex">
<div class="panel-title"><span>BANDS</span><span class="timer" id="tmBands"></span></div>
<div class="panel-body" id="bandsPanel">Loading...</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>SDO IMAGE</span><span class="timer" id="tmSolarImg"></span></div>
<div class="img-wrap"><img id="imgSolar" src="/api/solar-image" alt="SDO"></div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>GEOMAGNETIC</span></div>
<div class="panel-body" id="geomagBody" style="padding:4px 6px">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for data...</span>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>X-RAY FLUX</span></div>
<div class="panel-body" id="xrayBody" style="padding:4px 6px">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for data...</span>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>OPEN BANDS</span></div>
<div class="panel-body" id="openBandsBody" style="padding:4px 6px;overflow-y:auto">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for data...</span>
</div>
</div>
</div>
<div class="col">
<div class="panel" style="flex:1">
<div class="panel-title"><span>MUF MAP</span><span class="timer" id="tmMuf"></span></div>
<div class="img-wrap"><img id="imgMuf" src="/api/muf-map" alt="MUF"></div>
</div>
</div>
<div class="col">
<!-- Right column: DX, band activity, x-ray, geomag, open bands -->
<div class="panel" style="flex:0 0 auto;max-height:18vh;min-height:0">
<div class="panel-title"><span>DX SPOTS</span><span class="timer" id="tmDx"></span></div>
<div class="panel-body dx-body-wrap" style="padding:0;overflow-y:auto;max-height:calc(18vh - 22px)">
<table class="dx-tbl"><thead><tr><th>FREQ</th><th>B</th><th>DX CALL</th><th>DE</th><th>UTC</th></tr></thead><tbody id="dxBody"><tr><td colspan="5" style="color:var(--muted);padding:8px">Loading...</td></tr></tbody></table>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>BAND ACTIVITY</span></div>
<div class="panel-body" id="bandActivity" style="padding:4px 6px;overflow-y:auto">
<span style="color:var(--muted);font-size:clamp(11px,1vh,11px)">Waiting for DX data...</span>
</div>
</div>
<div class="panel" style="flex:1;min-height:0">
<div class="panel-title">
<span><span class="tab tab-active" data-tab="drap">DRAP</span><span class="tab" data-tab="aurora">AURORA</span><span class="tab" data-tab="enlil">ENLIL</span></span>
<span class="timer" id="tmDrap"></span>
</div>
<div class="panel-body img-wrap" style="padding:2px">
<img id="imgDrap" src="/api/drap" alt="Aurora" data-tab="aurora" style="width:100%;object-fit:contain;display:none">
<img id="imgRealDrap" src="/api/real-drap" alt="DRAP" data-tab="drap" style="width:100%;object-fit:contain">
<img id="imgEnlil" src="/api/enlil" alt="WSA-ENLIL" data-tab="enlil" style="width:100%;object-fit:contain;display:none">
</div>
</div>
</div>
</div>
<div class="sbar">
<span id="sbarLeft">Connecting...</span>
<span id="sbarRight">HamClock Lite v2.0</span>
</div>
</div><!-- end dashboard -->
<script>
(function(){
'use strict';
var P=function(n){return n<10?'0'+n:''+n};
var esc=function(s){
if(s==null)return'';
return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
};

// Timestamps for countdown timers
var lastSolarFetch=0,lastDxFetch=0,lastImageFetch=0;
var SOLAR_INTERVAL=300,DX_INTERVAL=120,IMAGE_INTERVAL=900;
var POLL_INTERVAL=60000;
var failCount=0;

// Cached last-known data for stale display
var lastSolar=null,lastBands=null,lastDx=null;
var solarStale=false,bandsStale=false,dxStale=false;

// Settings
var settings=null;
try{settings=JSON.parse(localStorage.getItem('hamclock-settings'));}catch(e){}

// Theme definitions
var themes={
classic:{cyan:'#06b6d4',green:'#22c55e',callsign:'#f472b6',bg:'#0a0e14',card:'#111820',border:'#1a2530',label:'#8899aa',muted:'#607080'},
amber:{cyan:'#f59e0b',green:'#f59e0b',callsign:'#3b82f6',bg:'#1a1000',card:'#1f1800',border:'#332800',label:'#B88060',muted:'#8A6840'},
blue:{cyan:'#3b82f6',green:'#60a5fa',callsign:'#f59e0b',bg:'#0a0f1e',card:'#0f1628',border:'#1a2540',label:'#7090b0',muted:'#506888'},
red:{cyan:'#ef4444',green:'#f87171',callsign:'#fbbf24',bg:'#1a0a0a',card:'#201010',border:'#3a1a1a',label:'#b07070',muted:'#905858'},
kstate:{cyan:'#FFFFFF',green:'#FFFFFF',callsign:'#FFFFFF',bg:'#512888',card:'#3D1366',border:'#694190',bright:'#FFFFFF',text:'#E8DDF5',label:'#C0B5D5',muted:'#927EB4'}
};

function applySettings(s){
var el=document.getElementById('hdrCallsign');
if(el)el.textContent=s.callsign||'';
var root=document.documentElement;
var t=themes[s.theme]||themes.classic;
root.style.setProperty('--cyan',t.cyan);
root.style.setProperty('--green',t.green);
root.style.setProperty('--callsign',t.callsign||'#f472b6');
root.style.setProperty('--bg',t.bg);
root.style.setProperty('--card',t.card);
root.style.setProperty('--border',t.border);
root.style.setProperty('--bright',t.bright||'#e8f0f0');
root.style.setProperty('--text',t.text||'#c8d0d8');
if(t.label)root.style.setProperty('--label',t.label);
if(t.muted)root.style.setProperty('--muted',t.muted);
document.body.className=document.body.className.replace(/\btheme-\S+/g,'').trim()+' theme-'+(s.theme||'classic');
}

if(!settings){
document.getElementById('setup').style.display='flex';
document.getElementById('dashboard').style.display='none';
}else{
document.getElementById('setup').style.display='none';
document.getElementById('dashboard').style.display='';
applySettings(settings);
if(settings&&settings.theme==='kstate'){
var kstateEl=document.getElementById('themeKstate');
if(kstateEl)kstateEl.style.display='';
}
}

// Fetch the host's NTP server to use as the default placeholder
(function(){
  var xhr=new XMLHttpRequest();
  xhr.open('GET','/api/ntp');
  xhr.timeout=5000;
  xhr.onload=function(){
    if(xhr.status!==200)return;
    try{
      var d=JSON.parse(xhr.responseText);
      if(d&&d.ntp){
        var el=document.getElementById('inNtp');
        if(el)el.placeholder=d.ntp+' (host)';
        window.__hostNtp=d.ntp;
      }
    }catch(e){}
  };
  xhr.send();
})();

// Make saveSetup global
window.saveSetup=function(){
var s={
callsign:document.getElementById('inCallsign').value.toUpperCase().trim(),
grid:document.getElementById('inGrid').value.toUpperCase().trim(),
timezone:document.getElementById('inTimezone').value,
theme:(document.querySelector('input[name="theme"]:checked')||{}).value||'classic',
ntp:document.getElementById('inNtp').value.trim()||window.__hostNtp||'pool.ntp.org'
};
if(!s.callsign){alert('Please enter your callsign');return;}
localStorage.setItem('hamclock-settings',JSON.stringify(s));
settings=s;
document.getElementById('setup').style.display='none';
document.getElementById('dashboard').style.display='';
applySettings(s);
startFetching();
};

// Callsign auto-lookup with debounce
var callsignTimer=null;
document.getElementById('inCallsign').addEventListener('input',function(){
var call=this.value.toUpperCase().trim();
clearTimeout(callsignTimer);
// Show/hide K-State theme based on callsign
var kstateEl=document.getElementById('themeKstate');
if(call==='W0QQQ'){
if(kstateEl)kstateEl.style.display='';
var kstateRadio=document.querySelector('input[name="theme"][value="kstate"]');
if(kstateRadio)kstateRadio.checked=true;
document.getElementById('inNtp').value='ntp.ksu.edu';
}else{
if(kstateEl)kstateEl.style.display='none';
var currentTheme=document.querySelector('input[name="theme"]:checked');
if(currentTheme&&currentTheme.value==='kstate'){
document.querySelector('input[name="theme"][value="classic"]').checked=true;
}
var ntpEl=document.getElementById('inNtp');
if(ntpEl&&ntpEl.value==='ntp.ksu.edu'){
ntpEl.value='';
}
}
if(call.length<3)return;
callsignTimer=setTimeout(function(){
var xhr=new XMLHttpRequest();
xhr.open('GET','/api/callsign/'+encodeURIComponent(call));
xhr.timeout=8000;
xhr.onload=function(){
if(xhr.status!==200)return;
try{
var d=JSON.parse(xhr.responseText);
// Auto-fill grid
if(d.grid){
document.getElementById('inGrid').value=d.grid;
}
// Auto-fill timezone from longitude
if(d.lng!=null){
var offset=Math.round(d.lng/15);
var tzMap={
'-5':'US/Eastern','-6':'US/Central','-7':'US/Mountain',
'-8':'US/Pacific','-9':'US/Alaska','-10':'US/Hawaii',
'0':'Europe/London','1':'Europe/Paris','2':'Europe/Berlin',
'3':'Europe/Moscow','9':'Asia/Tokyo','8':'Asia/Shanghai',
'5':'Asia/Kolkata','10':'Australia/Sydney'
};
var tz=tzMap[String(offset)];
if(tz){
document.getElementById('inTimezone').value=tz;
}
}
// Show name as confirmation
if(d.name){
var nameEl=document.getElementById('callLookupName');
if(nameEl)nameEl.textContent=d.name+(d.country?' \u2014 '+d.country:'');
}
}catch(e){}
};
xhr.send();
},800);
});

// Re-open settings when callsign is clicked
document.getElementById('hdrCallsign').onclick=function(){
// Pre-fill form with current settings
if(settings){
document.getElementById('inCallsign').value=settings.callsign||'';
document.getElementById('inGrid').value=settings.grid||'';
document.getElementById('inTimezone').value=settings.timezone||'auto';
document.getElementById('inNtp').value=settings.ntp||'';
var radios=document.querySelectorAll('input[name="theme"]');
for(var i=0;i<radios.length;i++){radios[i].checked=radios[i].value===(settings.theme||'classic');}
// Show K-State swatch if current theme is kstate
if(settings.theme==='kstate'){
var kstateEl=document.getElementById('themeKstate');
if(kstateEl)kstateEl.style.display='';
}
}
document.getElementById('setup').style.display='flex';
};

// Tab switcher for AURORA/DRAP combined panel
document.querySelectorAll('.tab').forEach(function(t){
t.addEventListener('click',function(){
var name=this.getAttribute('data-tab');
document.querySelectorAll('.tab').forEach(function(s){
s.classList.toggle('tab-active',s.getAttribute('data-tab')===name);
});
document.querySelectorAll('img[data-tab]').forEach(function(im){
im.style.display=im.getAttribute('data-tab')===name?'':'none';
});
});
});

// DOM refs
var elUtc=document.getElementById('utc');
var elLcl=document.getElementById('lcl');
var elSolar=document.getElementById('solarPanel');
var elBands=document.getElementById('bandsPanel');
var elDxBody=document.getElementById('dxBody');
var elDot=document.getElementById('statusDot');
var elSbarL=document.getElementById('sbarLeft');
var tmSolar=document.getElementById('tmSolar');
var tmBands=document.getElementById('tmBands');
var tmDx=document.getElementById('tmDx');
var tmSolarImg=document.getElementById('tmSolarImg');
var tmMuf=document.getElementById('tmMuf');
var tmDrap=document.getElementById('tmDrap');

// Clock — uses timezone setting if available
setInterval(function(){
var d=new Date();
elUtc.textContent='UTC '+P(d.getUTCHours())+':'+P(d.getUTCMinutes())+':'+P(d.getUTCSeconds());
if(settings&&settings.timezone&&settings.timezone!=='auto'){
try{
var opts={hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:settings.timezone};
elLcl.textContent='LOCAL '+d.toLocaleTimeString('en-US',opts);
}catch(e){
elLcl.textContent='LOCAL '+P(d.getHours())+':'+P(d.getMinutes())+':'+P(d.getSeconds());
}
}else{
elLcl.textContent='LOCAL '+P(d.getHours())+':'+P(d.getMinutes())+':'+P(d.getSeconds());
}
},1000);

// Static countdown — called once per fetch cycle, not every second
function formatCountdown(lastFetch,intervalSec){
var elapsed=Math.floor((Date.now()/1000)-lastFetch);
var remaining=Math.max(0,intervalSec-elapsed);
return remaining>=60?Math.ceil(remaining/60)+'m':remaining+'s';
}
function updateCountdowns(){
if(lastSolarFetch){var sc='next \u21BB '+formatCountdown(lastSolarFetch,SOLAR_INTERVAL);tmSolar.textContent=sc;tmBands.textContent=sc;}
if(lastDxFetch){tmDx.textContent='next \u21BB '+formatCountdown(lastDxFetch,DX_INTERVAL);}
if(lastImageFetch){var ic='next \u21BB '+formatCountdown(lastImageFetch,IMAGE_INTERVAL);tmSolarImg.textContent=ic;tmMuf.textContent=ic;tmDrap.textContent=ic;}
}

// Color helpers
function kpColor(k){k=parseFloat(k)||0;if(k<=2)return'var(--green)';if(k<=4)return'var(--yellow)';return'var(--red)';}
function sfiColor(s){s=parseFloat(s)||0;if(s>=150)return'var(--green)';if(s>=100)return'var(--cyan)';if(s>=70)return'var(--yellow)';return'var(--red)';}
function condLetter(c){if(!c)return{l:'?',cls:'cN'};var v=c.toLowerCase();if(v==='good')return{l:'G',cls:'cG'};if(v==='fair')return{l:'F',cls:'cF'};if(v==='poor')return{l:'P',cls:'cP'};return{l:'?',cls:'cN'};}
function geoColor(s){if(!s)return'var(--muted)';var v=s.toLowerCase();if(v==='quiet'||v==='inactive')return'var(--green)';if(v==='unsettled'||v==='active')return'var(--yellow)';return'var(--red)';}

// Render solar
function renderSolar(d){
if(!d||!d.sfi)return;
lastSolar=d;solarStale=false;
var kp=parseFloat(d.kIndex)||0;
var kpPct=Math.min(kp/9*100,100);
var h='';
h+='<div class="s-row"><span class="s-lbl">SFI</span><span class="s-val" style="color:'+sfiColor(d.sfi)+'">'+esc(d.sfi)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Kp</span><span class="s-val"><span class="kp-wrap"><span class="kp-bar"><span class="kp-fill" style="width:'+kpPct+'%;background:'+kpColor(d.kIndex)+'"></span></span><span style="color:'+kpColor(d.kIndex)+'">'+esc(d.kIndex)+'</span></span></span></div>';
h+='<div class="s-row"><span class="s-lbl">SSN</span><span class="s-val" style="color:var(--cyan)">'+esc(d.ssn)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">A</span><span class="s-val" style="color:'+(parseInt(d.aIndex)>20?'var(--red)':'var(--green)')+'">'+esc(d.aIndex)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">X-Ray</span><span class="s-val" style="color:var(--yellow)">'+esc(d.xray)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Wind</span><span class="s-val" style="color:'+(parseFloat(d.solarWind)>500?'var(--red)':'var(--cyan)')+'">'+esc(d.solarWind)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Bz</span><span class="s-val" style="color:'+(parseFloat(d.magneticField)<0?'var(--red)':'var(--green)')+'">'+esc(d.magneticField)+'</span></div>';
h+='<div class="s-row"><span class="s-lbl">Geo</span><span class="s-val" style="color:'+geoColor(d.geomagField)+'">'+esc(d.geomagField)+'</span></div>';
if(d.signalNoise){h+='<div class="s-row"><span class="s-lbl">S/N</span><span class="s-val" style="color:var(--cyan)">'+esc(d.signalNoise)+'</span></div>';}
if(d.aurora){h+='<div class="s-row"><span class="s-lbl">Aurora</span><span class="s-val" style="color:#a855f7">'+esc(d.aurora)+'</span></div>';}
if(d.fof2){h+='<div class="s-row"><span class="s-lbl">foF2</span><span class="s-val" style="color:#3b82f6">'+esc(d.fof2)+'</span></div>';}
elSolar.innerHTML=h;
}

// Render bands
function renderBands(d){
if(!d||Object.keys(d).length===0)return;
lastBands=d;bandsStale=false;
var order=['80m-40m','30m-20m','17m-15m','12m-10m'];
var keys=Object.keys(d).sort(function(a,b){
var ai=order.indexOf(a),bi=order.indexOf(b);
if(ai===-1)ai=99;if(bi===-1)bi=99;return ai-bi;
});
var h='<div class="band-row"><span class="band-name"></span><span class="band-lbl">DAY</span><span class="band-lbl">NIGHT</span></div>';
for(var i=0;i<keys.length;i++){
var n=keys[i],b=d[n];
var dc=condLetter(b.day||b.Day);
var nc=condLetter(b.night||b.Night);
h+='<div class="band-row"><span class="band-name">'+esc(n)+'</span><span class="band-cond '+dc.cls+'">'+dc.l+'</span><span class="band-cond '+nc.cls+'">'+nc.l+'</span></div>';
}
elBands.innerHTML=h;
}

// Render DX
var BAND_COLORS={'160m':'#8b5cf6','80m':'#6366f1','60m':'#3b82f6','40m':'#06b6d4','30m':'#14b8a6','20m':'#22c55e','17m':'#84cc16','15m':'#eab308','12m':'#f97316','10m':'#ef4444','6m':'#ec4899','2m':'#a855f7','70cm':'#f472b6'};
function renderDX(spots){
if(!spots||spots.length===0){elDxBody.innerHTML='<tr><td colspan="5" style="color:var(--muted);padding:8px">No spots</td></tr>';return;}
lastDx=spots;dxStale=false;
// Hard cap at 10 rows
var maxRows=Math.min(spots.length,5);
var h='';
for(var i=0;i<maxRows;i++){
var s=spots[i];
var bc=BAND_COLORS[s.band]||'var(--muted)';
h+='<tr><td class="dx-freq">'+esc(s.frequency)+'</td><td class="dx-band" style="color:'+bc+'">'+esc(s.band)+'</td><td class="dx-call">'+esc(s.dx)+'</td><td class="dx-sp">'+esc(s.spotter)+'</td><td class="dx-tm">'+esc(s.time)+'</td></tr>';
}
elDxBody.innerHTML=h;
}

// Render band activity bar chart from DX spots
function renderBandActivity(spots){
if(!spots||!spots.length)return;
var counts={};
spots.forEach(function(s){
counts[s.band]=(counts[s.band]||0)+1;
});
var bandOrder=['160m','80m','60m','40m','30m','20m','17m','15m','12m','10m'];
var sorted=bandOrder.filter(function(b){return counts[b];});
var max=1;
sorted.forEach(function(b){if(counts[b]>max)max=counts[b];});
var h='';
var bandColors={
'160m':'#ff6b6b','80m':'#f06595','60m':'#cc5de8','40m':'#845ef7',
'30m':'#5c7cfa','20m':'#339af0','17m':'#22b8cf','15m':'#20c997',
'12m':'#51cf66','10m':'#94d82d','6m':'#fcc419','2m':'#ff922b'
};
sorted.forEach(function(band){
var pct=Math.round(counts[band]/max*100);
var color=bandColors[band]||'#666';
h+='<div style="display:flex;align-items:center;gap:6px;margin:5px 0">';
h+='<span style="width:30px;text-align:right;color:var(--label);font-size:clamp(13px,1.3vw,13px)">'+esc(band)+'</span>';
h+='<div style="flex:1;height:clamp(14px,2.4vh,24px);background:var(--bg)">';
h+='<div style="width:'+pct+'%;height:100%;background:'+color+'"></div>';
h+='</div>';
h+='<span style="width:20px;color:var(--text);font-size:clamp(11px,1vw,11px)">'+counts[band]+'</span>';
h+='</div>';
});
document.getElementById('bandActivity').innerHTML=h;
}

// Render X-Ray Flux panel
function renderXray(solar){
if(!solar)return;
var el=document.getElementById('xrayBody');
if(!el)return;
var xr=solar.xray||'N/A';
var cls=xr.charAt(0).toUpperCase();
var color=(cls==='A'||cls==='B')?'#22c55e':cls==='C'?'#eab308':cls==='M'?'#ff8c00':cls==='X'?'#ef4444':'#666';
var level=cls==='A'?10:cls==='B'?25:cls==='C'?50:cls==='M'?75:cls==='X'?100:0;
var label=(cls==='A'||cls==='B')?'QUIET':cls==='C'?'ACTIVE':cls==='M'?'STORM':cls==='X'?'MAJOR':'';
el.innerHTML='<div style="display:flex;align-items:center;gap:8px">'+
'<span style="font-size:clamp(24px,2.5vh,24px);font-weight:bold;color:'+color+'">'+esc(xr)+'</span>'+
'<div style="flex:1;height:clamp(6px,1vh,10px);background:var(--bg)">'+
'<div style="width:'+level+'%;height:100%;background:'+color+'"></div></div>'+
'<span style="font-size:clamp(11px,1vh,11px);color:'+color+'">'+label+'</span></div>';
}

// Render Geomagnetic panel
function renderGeomag(solar){
if(!solar)return;
var el=document.getElementById('geomagBody');
if(!el)return;
var kp=parseInt(solar.kIndex)||0;
var geo=solar.geomagField||'unknown';
var color=kp<=3?'#22c55e':kp<=5?'#eab308':kp<=7?'#ff8c00':'#ef4444';
var pct=Math.min(100,kp/9*100);
el.innerHTML='<div style="display:flex;align-items:center;gap:8px">'+
'<span style="font-size:clamp(24px,2.5vh,24px);font-weight:bold;color:'+color+'">Kp '+kp+'</span>'+
'<div style="flex:1;height:clamp(6px,1vh,10px);background:var(--bg)">'+
'<div style="width:'+pct+'%;height:100%;background:'+color+'"></div></div>'+
'<span style="font-size:clamp(11px,1vh,11px);color:'+color+'">'+esc(geo.toUpperCase())+'</span></div>';
}

// Render Open Bands panel
function renderOpenBands(bands){
if(!bands)return;
var el=document.getElementById('openBandsBody');
if(!el)return;
var open=[],closed=[];
for(var key in bands){
var d=bands[key];
var dayOk=d.day&&(d.day.toLowerCase()==='good'||d.day.toLowerCase()==='fair');
var nightOk=d.night&&(d.night.toLowerCase()==='good'||d.night.toLowerCase()==='fair');
var dayOk2=d.Day&&(d.Day.toLowerCase()==='good'||d.Day.toLowerCase()==='fair');
var nightOk2=d.Night&&(d.Night.toLowerCase()==='good'||d.Night.toLowerCase()==='fair');
if(dayOk||nightOk||dayOk2||nightOk2){open.push(key);}else{closed.push(key);}
}
var h='';
if(open.length){
h+='<div style="margin-bottom:4px"><span style="color:#22c55e;font-size:clamp(11px,1vh,11px)">OPEN: </span>';
h+=open.map(function(b){return'<span style="color:#22c55e;font-weight:bold;margin-right:6px">'+esc(b)+'</span>';}).join('');
h+='</div>';
}
if(closed.length){
h+='<div><span style="color:#ef4444;font-size:clamp(11px,1vh,11px)">CLOSED: </span>';
h+=closed.map(function(b){return'<span style="color:#ef4444;margin-right:6px">'+esc(b)+'</span>';}).join('');
h+='</div>';
}
el.innerHTML=h||'<span style="color:var(--muted)">Waiting for data...</span>';
}

// Fetch queue — one request at a time, 1.5s gap between each
var fetchQueue=[];
var fetchBusy=false;

function queueFetch(url,callback){
fetchQueue.push({url:url,cb:callback});
processQueue();
}

function processQueue(){
if(fetchBusy||fetchQueue.length===0)return;
fetchBusy=true;
var item=fetchQueue.shift();
var xhr=new XMLHttpRequest();
xhr.open('GET',item.url);
xhr.timeout=8000;
xhr.onload=function(){
if(xhr.status===200){
try{item.cb(JSON.parse(xhr.responseText));}catch(e){}
}
fetchBusy=false;
setTimeout(processQueue,1500);
};
xhr.onerror=xhr.ontimeout=function(){
fetchBusy=false;
setTimeout(processQueue,1500);
};
xhr.send();
}

function fetchAll(){
updateCountdowns();
queueFetch('/api/solar',function(data){
renderSolar(data);
renderXray(data);
renderGeomag(data);
lastSolarFetch=Math.floor(Date.now()/1000);
});
queueFetch('/api/bands',function(data){
renderBands(data);
renderOpenBands(data);
});
queueFetch('/api/dxspots',function(data){
renderDX(data);
renderBandActivity(data);
lastDxFetch=Math.floor(Date.now()/1000);
});
queueFetch('/api/health',function(data){
if(data&&data.status==='ok'){
failCount=0;
elDot.style.background='';
elSbarL.textContent='Solar:'+fmtAge(data.solar_age)+' Bands:'+fmtAge(data.bands_age)+' DX:'+fmtAge(data.dx_age);
elSbarL.className='';
}else{
failCount++;
if(failCount>3){elDot.style.background='var(--red)';elSbarL.textContent='Disconnected';elSbarL.className='stale';}
else{elDot.style.background='var(--yellow)';elSbarL.textContent='Retrying...';}
}
});
}

function fmtAge(s){
if(s==null||s<0)return' --';
if(s<60)return' '+s+'s';
return' '+Math.floor(s/60)+'m';
}

// Image refresh (separate, every 15 min) — staggered to spread CPU load
var elImgSolar=document.getElementById('imgSolar');
var elImgMuf=document.getElementById('imgMuf');
var elImgEnlil=document.getElementById('imgEnlil');
var elImgDrap=document.getElementById('imgDrap');
var elImgRealDrap=document.getElementById('imgRealDrap');

function refreshImages(){
var t=Date.now();
lastImageFetch=Math.floor(t/1000);
if(elImgSolar)elImgSolar.src='/api/solar-image?t='+t;
setTimeout(function(){
if(elImgMuf)elImgMuf.src='/api/muf-map?t='+t;
},3000);
setTimeout(function(){
if(elImgEnlil)elImgEnlil.src='/api/enlil?t='+t;
},6000);
setTimeout(function(){
if(elImgDrap)elImgDrap.src='/api/drap?t='+t;
},9000);
setTimeout(function(){
if(elImgRealDrap)elImgRealDrap.src='/api/real-drap?t='+t;
},12000);
updateCountdowns();
}

// Start fetching data
var fetchStarted=false;
function startFetching(){
if(fetchStarted)return;
fetchStarted=true;
lastImageFetch=Math.floor(Date.now()/1000);
setTimeout(function(){queueFetch('/api/solar',function(d){renderSolar(d);renderXray(d);renderGeomag(d);lastSolarFetch=Math.floor(Date.now()/1000);});},500);
setTimeout(function(){queueFetch('/api/bands',function(d){renderBands(d);renderOpenBands(d);});},2000);
setTimeout(function(){queueFetch('/api/dxspots',function(d){renderDX(d);renderBandActivity(d);lastDxFetch=Math.floor(Date.now()/1000);});},3500);
setTimeout(refreshImages,5000);
setInterval(fetchAll,POLL_INTERVAL);
setInterval(refreshImages,IMAGE_INTERVAL*1000);
}

// Init — only start fetching if settings exist (setup already done)
if(settings){
startFetching();
}
})();
</script>
</body>
</html>
HTMLEOF

echo "Writing hamclock_data.py..."
sudo tee "$INSTALL_DIR/hamclock_data.py" > /dev/null << 'HCDATAEOF'
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
HCDATAEOF

echo "Writing hamclock_pygame.py..."
sudo tee "$INSTALL_DIR/hamclock_pygame.py" > /dev/null << 'HCPYEOF'
"""Native Pygame client for HamClock Lite.

Replaces the browser on a Raspberry Pi 1 Model B, fetching data from
the same /api/* endpoints as the web UI but rendering directly with
Pygame/SDL for a ~50 MB RAM and ~10% CPU win over the browser stack.
"""

import io
import os
import sys
import time

import pygame

from hamclock_data import HamClockData


# ---- K-State theme colors ----
BG = (42, 20, 80)
CARD = (58, 29, 101)
BORDER = (81, 40, 136)
TEXT = (232, 221, 245)
LABEL = (184, 160, 216)
BRIGHT = (255, 255, 255)
ACCENT_GOLD = (244, 197, 92)
STATUS_GREEN = (34, 197, 94)
STATUS_YELLOW = (234, 179, 8)
STATUS_RED = (239, 68, 68)

COND_COLORS = {
    'Good': (34, 197, 94),
    'Fair': (234, 179, 8),
    'Poor': (239, 68, 68),
    'N/A': (74, 85, 104),
}

BAND_COLORS = {
    '160m': (255, 107, 107), '80m': (240, 101, 149), '60m': (204, 93, 232),
    '40m': (132, 94, 247), '30m': (92, 124, 250), '20m': (51, 154, 240),
    '17m': (34, 184, 207), '15m': (32, 201, 151), '12m': (81, 207, 102),
    '10m': (148, 216, 45),
}

HF_BANDS = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']

SCREEN_W = 1440
SCREEN_H = 900


def _make_fonts():
    """Build the fonts dict. Falls back to default font if SysFont fails."""
    def mk(size):
        try:
            f = pygame.font.SysFont('monospace', size)
            if f is None:
                raise RuntimeError('no monospace')
            return f
        except Exception:
            return pygame.font.Font(None, size + 4)
    return {
        'title': mk(22),
        'panel': mk(14),
        'body': mk(14),
        'label': mk(12),
        'small': mk(11),
    }


def _safe(d, key, default='--'):
    try:
        v = d.get(key)
        if v is None or v == '':
            return default
        return v
    except Exception:
        return default


def _blit_text(screen, font, text, color, x, y):
    try:
        surf = font.render(str(text), True, color)
        screen.blit(surf, (x, y))
        return surf.get_width()
    except Exception:
        return 0


def _load_image(data_bytes):
    """Decode JPEG/PNG bytes into a Pygame surface, or None on failure."""
    if not data_bytes:
        return None
    for hint in ('x.jpg', 'x.png'):
        try:
            return pygame.image.load_extended(io.BytesIO(data_bytes), hint).convert()
        except Exception:
            continue
    try:
        return pygame.image.load(io.BytesIO(data_bytes)).convert()
    except Exception:
        return None


def draw_panel(screen, rect, title, fonts):
    pygame.draw.rect(screen, CARD, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
    bar = pygame.Rect(rect.x, rect.y, rect.w, 18)
    pygame.draw.rect(screen, BORDER, bar)
    _blit_text(screen, fonts['panel'], title, BRIGHT, rect.x + 6, rect.y + 2)
    return pygame.Rect(rect.x + 6, rect.y + 22, rect.w - 12, rect.h - 26)


def draw_header(screen, rect, callsign, fonts):
    pygame.draw.rect(screen, CARD, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
    _blit_text(screen, fonts['title'], 'HAMCLOCK LITE', ACCENT_GOLD, rect.x + 8, rect.y + 4)
    if callsign:
        _blit_text(screen, fonts['body'], str(callsign), BRIGHT, rect.x + 220, rect.y + 8)
    try:
        utc = time.strftime('%H:%M:%S', time.gmtime())
        local = time.strftime('%H:%M:%S')
    except Exception:
        utc = local = '--:--:--'
    _blit_text(screen, fonts['body'], 'UTC ' + utc, TEXT, rect.x + rect.w - 340, rect.y + 8)
    _blit_text(screen, fonts['body'], 'LOC ' + local, TEXT, rect.x + rect.w - 180, rect.y + 8)
    dot_color = STATUS_GREEN if (int(time.time()) % 2 == 0) else STATUS_YELLOW
    pygame.draw.circle(screen, dot_color, (rect.x + rect.w - 18, rect.y + 14), 5)


def draw_solar(screen, rect, solar, fonts):
    rows = [
        ('SFI', _safe(solar, 'sfi')),
        ('Kp', _safe(solar, 'kIndex')),
        ('SSN', _safe(solar, 'ssn')),
        ('A', _safe(solar, 'aIndex')),
        ('X-Ray', _safe(solar, 'xray')),
        ('Wind', _safe(solar, 'solarWind')),
        ('Bz', _safe(solar, 'bz')),
        ('Geo', _safe(solar, 'geomagField')),
        ('S/N', _safe(solar, 'signalNoise')),
        ('foF2', _safe(solar, 'fof2')),
    ]
    y = rect.y
    for label, value in rows:
        _blit_text(screen, fonts['label'], label, LABEL, rect.x, y)
        _blit_text(screen, fonts['body'], str(value), BRIGHT, rect.x + 70, y - 1)
        y += 16


def draw_bands(screen, rect, bands, fonts):
    groups = [
        ('80m-40m', ['80m-40m']),
        ('30m-20m', ['30m-20m']),
        ('17m-15m', ['17m-15m']),
        ('12m-10m', ['12m-10m']),
    ]
    _blit_text(screen, fonts['label'], 'BAND', LABEL, rect.x, rect.y)
    _blit_text(screen, fonts['label'], 'DAY', LABEL, rect.x + 100, rect.y)
    _blit_text(screen, fonts['label'], 'NIGHT', LABEL, rect.x + 160, rect.y)
    y = rect.y + 16
    for name, keys in groups:
        entry = bands.get(keys[0], {}) if isinstance(bands, dict) else {}
        day = entry.get('day', 'N/A') if isinstance(entry, dict) else 'N/A'
        night = entry.get('night', 'N/A') if isinstance(entry, dict) else 'N/A'
        _blit_text(screen, fonts['body'], name, TEXT, rect.x, y)
        _blit_text(screen, fonts['body'], str(day), COND_COLORS.get(day, TEXT), rect.x + 100, y)
        _blit_text(screen, fonts['body'], str(night), COND_COLORS.get(night, TEXT), rect.x + 160, y)
        y += 16


def draw_image(screen, rect, surface):
    if surface is None:
        _blit_text(screen, pygame.font.Font(None, 18), 'image loading...', LABEL, rect.x + 6, rect.y + 6)
        return
    try:
        iw, ih = surface.get_size()
        if iw == 0 or ih == 0:
            return
        scale = min(rect.w / iw, rect.h / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        scaled = pygame.transform.smoothscale(surface, (nw, nh)) if scale < 1.0 else surface
        x = rect.x + (rect.w - nw) // 2
        y = rect.y + (rect.h - nh) // 2
        screen.blit(scaled, (x, y))
    except Exception:
        pass


def draw_bar(screen, rect, value, vmax, color):
    pygame.draw.rect(screen, BG, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
    try:
        frac = 0.0 if vmax <= 0 else max(0.0, min(1.0, float(value) / float(vmax)))
    except Exception:
        frac = 0.0
    inner = pygame.Rect(rect.x + 1, rect.y + 1, int((rect.w - 2) * frac), rect.h - 2)
    if inner.w > 0:
        pygame.draw.rect(screen, color, inner)


def draw_muf_text(screen, rect, solar, fonts):
    rows = [
        ('FOF2', '{} MHz'.format(_safe(solar, 'fof2'))),
        ('GEOMAG', _safe(solar, 'geomagField')),
        ('KP', _safe(solar, 'kIndex')),
        ('SFI', _safe(solar, 'sfi')),
        ('SSN', _safe(solar, 'ssn')),
    ]
    y = rect.y + 20
    for label, value in rows:
        _blit_text(screen, fonts['panel'], label, LABEL, rect.x + 20, y)
        _blit_text(screen, fonts['title'], str(value), BRIGHT, rect.x + 140, y - 4)
        y += 44
    _blit_text(screen, fonts['small'], '(Map available in web UI)', LABEL,
               rect.x + 20, rect.y + rect.h - 20)


def draw_dx_spots(screen, rect, dxspots, fonts):
    if not isinstance(dxspots, list):
        dxspots = []
    _blit_text(screen, fonts['label'], 'FREQ', LABEL, rect.x, rect.y)
    _blit_text(screen, fonts['label'], 'BND', LABEL, rect.x + 90, rect.y)
    _blit_text(screen, fonts['label'], 'DX', LABEL, rect.x + 140, rect.y)
    _blit_text(screen, fonts['label'], 'SPOTTER', LABEL, rect.x + 230, rect.y)
    _blit_text(screen, fonts['label'], 'TIME', LABEL, rect.x + 340, rect.y)
    y = rect.y + 16
    for spot in dxspots[:5]:
        if not isinstance(spot, dict):
            continue
        freq = _safe(spot, 'frequency')
        band = _safe(spot, 'band')
        dx = _safe(spot, 'dxCall')
        spotter = _safe(spot, 'spotter')
        tm = _safe(spot, 'time')
        _blit_text(screen, fonts['body'], str(freq), ACCENT_GOLD, rect.x, y)
        _blit_text(screen, fonts['body'], str(band), BAND_COLORS.get(str(band), TEXT), rect.x + 90, y)
        _blit_text(screen, fonts['body'], str(dx), BRIGHT, rect.x + 140, y)
        _blit_text(screen, fonts['body'], str(spotter)[:10], TEXT, rect.x + 230, y)
        _blit_text(screen, fonts['body'], str(tm), LABEL, rect.x + 340, y)
        y += 16


def draw_band_activity(screen, rect, dxspots, fonts):
    counts = {b: 0 for b in HF_BANDS}
    if isinstance(dxspots, list):
        for spot in dxspots:
            if isinstance(spot, dict):
                b = spot.get('band')
                if b in counts:
                    counts[b] += 1
    vmax = max(counts.values()) if any(counts.values()) else 1
    label_w = 40
    count_w = 36
    row_h = max(14, (rect.h - 4) // len(HF_BANDS))
    y = rect.y + 2
    for band in HF_BANDS:
        c = counts[band]
        _blit_text(screen, fonts['label'], band, LABEL, rect.x, y + 1)
        bar_rect = pygame.Rect(rect.x + label_w, y + 2,
                               max(1, rect.w - label_w - count_w), row_h - 4)
        draw_bar(screen, bar_rect, c, vmax, BAND_COLORS.get(band, TEXT))
        _blit_text(screen, fonts['label'], str(c), BRIGHT,
                   rect.x + rect.w - count_w + 4, y + 1)
        y += row_h


def draw_tabs(screen, rect, tabs, active, fonts):
    """Draw a tab bar across rect.y (height 20). Returns {name: Rect}."""
    regions = {}
    if not tabs:
        return regions
    tw = rect.w // len(tabs)
    for i, name in enumerate(tabs):
        tab_rect = pygame.Rect(rect.x + i * tw, rect.y, tw - 2, 20)
        color = BORDER if name == active else CARD
        pygame.draw.rect(screen, color, tab_rect)
        pygame.draw.rect(screen, BORDER, tab_rect, 1)
        text_color = ACCENT_GOLD if name == active else LABEL
        _blit_text(screen, fonts['panel'], name.upper(), text_color,
                   tab_rect.x + 8, tab_rect.y + 2)
        regions[name] = tab_rect
    return regions


def draw_geomag(screen, rect, solar, fonts):
    kp = _safe(solar, 'kIndex', 0)
    try:
        kp_val = float(kp)
    except Exception:
        kp_val = 0.0
    color = STATUS_GREEN if kp_val < 4 else STATUS_YELLOW if kp_val < 6 else STATUS_RED
    _blit_text(screen, fonts['body'], 'Kp {}'.format(kp), BRIGHT, rect.x, rect.y + 2)
    bar_rect = pygame.Rect(rect.x, rect.y + 20, rect.w, 10)
    draw_bar(screen, bar_rect, kp_val, 9.0, color)


def draw_xray(screen, rect, solar, fonts):
    xray = _safe(solar, 'xray', 'A0.0')
    s = str(xray)
    try:
        letter = s[0]
        mag = float(s[1:]) if len(s) > 1 else 0.0
        scale = {'A': 0, 'B': 1, 'C': 2, 'M': 3, 'X': 4}.get(letter.upper(), 0)
        value = scale + (mag / 10.0)
    except Exception:
        value = 0.0
    color = STATUS_GREEN if value < 2 else STATUS_YELLOW if value < 3 else STATUS_RED
    _blit_text(screen, fonts['body'], s, BRIGHT, rect.x, rect.y + 2)
    bar_rect = pygame.Rect(rect.x, rect.y + 20, rect.w, 10)
    draw_bar(screen, bar_rect, value, 5.0, color)


def draw_open_bands(screen, rect, bands, fonts):
    opens, closes = [], []
    if isinstance(bands, dict):
        for key, entry in bands.items():
            if not isinstance(entry, dict):
                continue
            day = entry.get('day', 'N/A')
            if day in ('Good', 'Fair'):
                opens.append(key)
            elif day == 'Poor':
                closes.append(key)
    _blit_text(screen, fonts['label'], 'OPEN: ' + (', '.join(opens) or '--'),
               STATUS_GREEN, rect.x, rect.y)
    _blit_text(screen, fonts['label'], 'CLOSED: ' + (', '.join(closes) or '--'),
               STATUS_RED, rect.x, rect.y + 16)


def draw_status_bar(screen, rect, data, fonts):
    pygame.draw.rect(screen, CARD, rect)
    pygame.draw.rect(screen, BORDER, rect, 1)
    now = time.time()
    dage = int(now - data.last_data_refresh) if data.last_data_refresh else -1
    iage = int(now - data.last_image_refresh) if data.last_image_refresh else -1
    text = 'Data:{}s  Img:{}s  Solar:{}  Bands:{}  DX:{}'.format(
        dage if dage >= 0 else '--',
        iage if iage >= 0 else '--',
        'OK' if data.solar else '--',
        'OK' if data.bands else '--',
        len(data.dxspots) if isinstance(data.dxspots, list) else 0,
    )
    _blit_text(screen, fonts['small'], text, LABEL, rect.x + 6, rect.y + 4)
    _blit_text(screen, fonts['small'], 'ESC/Q to quit', LABEL,
               rect.x + rect.w - 110, rect.y + 4)


def _get_cached_image(data, key, image_cache, image_cache_ts):
    """Return a pygame Surface for data.images[key], rebuilt when refresh ts changes."""
    raw = data.images.get(key) if isinstance(data.images, dict) else None
    if raw is None:
        return None
    ts = data.last_image_refresh
    if image_cache_ts.get(key) != ts or key not in image_cache:
        surf = _load_image(raw)
        if surf is not None:
            image_cache[key] = surf
            image_cache_ts[key] = ts
    return image_cache.get(key)


def main():
    if 'DISPLAY' not in os.environ:
        os.environ.setdefault('SDL_VIDEODRIVER', 'fbcon')
        os.environ.setdefault('SDL_FBDEV', '/dev/fb0')

    pygame.init()
    try:
        pygame.mouse.set_visible(True)
    except Exception:
        pass

    try:
        screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
    except pygame.error:
        try:
            screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        except pygame.error:
            screen = pygame.display.set_mode((800, 600))
    pygame.display.set_caption('HamClock Lite')

    fonts = _make_fonts()

    data = HamClockData()
    try:
        data.start_background(data_interval=60, image_interval=900)
    except Exception as e:
        print('data start error:', e, file=sys.stderr)

    active_tab = 'drap'
    image_cache = {}
    image_cache_ts = {}
    tab_regions = {}
    tab_image_key = {'drap': 'real-drap', 'aurora': 'drap', 'enlil': 'enlil'}

    clock = pygame.time.Clock()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                pos = event.pos
                for name, r in tab_regions.items():
                    if r.collidepoint(pos):
                        active_tab = name
                        break

        sw, sh = screen.get_size()
        screen.fill(BG)

        header = pygame.Rect(0, 0, sw, 30)
        callsign = os.environ.get('HAMCLOCK_CALLSIGN', 'N0CALL')
        draw_header(screen, header, callsign, fonts)

        status = pygame.Rect(0, sh - 20, sw, 20)
        draw_status_bar(screen, status, data, fonts)

        content_top = 32
        content_bot = sh - 22
        content_h = content_bot - content_top

        left_w = int(sw * 288 / 1440)
        mid_w = int(sw * (936 - 288) / 1440)
        right_w = sw - left_w - mid_w

        # ---- LEFT COLUMN ----
        lx = 2
        ly = content_top
        panel_gap = 4
        # allocate heights (percent of content_h)
        heights = [
            int(content_h * 0.20),  # solar
            int(content_h * 0.12),  # bands
            int(content_h * 0.28),  # sdo
            int(content_h * 0.10),  # geomag
            int(content_h * 0.10),  # xray
        ]
        heights.append(content_h - sum(heights) - panel_gap * 5)  # open bands
        titles = ['SOLAR', 'BANDS', 'SDO IMAGE', 'GEOMAGNETIC', 'X-RAY FLUX', 'OPEN BANDS']
        cy = ly
        panel_rects = []
        for h, t in zip(heights, titles):
            r = pygame.Rect(lx, cy, left_w - 4, h)
            inner = draw_panel(screen, r, t, fonts)
            panel_rects.append(inner)
            cy += h + panel_gap

        try:
            draw_solar(screen, panel_rects[0], data.solar or {}, fonts)
        except Exception:
            pass
        try:
            draw_bands(screen, panel_rects[1], data.bands or {}, fonts)
        except Exception:
            pass
        try:
            sdo_surf = _get_cached_image(data, 'solar-image', image_cache, image_cache_ts)
            draw_image(screen, panel_rects[2], sdo_surf)
        except Exception:
            pass
        try:
            draw_geomag(screen, panel_rects[3], data.solar or {}, fonts)
        except Exception:
            pass
        try:
            draw_xray(screen, panel_rects[4], data.solar or {}, fonts)
        except Exception:
            pass
        try:
            draw_open_bands(screen, panel_rects[5], data.bands or {}, fonts)
        except Exception:
            pass

        # ---- MIDDLE COLUMN ----
        mx = lx + left_w
        mid_rect = pygame.Rect(mx, content_top, mid_w - 4, content_h)
        mid_inner = draw_panel(screen, mid_rect, 'MUF STATUS', fonts)
        try:
            draw_muf_text(screen, mid_inner, data.solar or {}, fonts)
        except Exception:
            pass

        # ---- RIGHT COLUMN ----
        rx = mx + mid_w
        rh_dx = int(content_h * 0.28)
        rh_ba = int(content_h * 0.32)
        rh_prop = content_h - rh_dx - rh_ba - panel_gap * 2

        dx_r = pygame.Rect(rx, content_top, right_w - 4, rh_dx)
        dx_inner = draw_panel(screen, dx_r, 'DX SPOTS', fonts)
        try:
            draw_dx_spots(screen, dx_inner, data.dxspots or [], fonts)
        except Exception:
            pass

        ba_r = pygame.Rect(rx, content_top + rh_dx + panel_gap, right_w - 4, rh_ba)
        ba_inner = draw_panel(screen, ba_r, 'BAND ACTIVITY', fonts)
        try:
            draw_band_activity(screen, ba_inner, data.dxspots or [], fonts)
        except Exception:
            pass

        prop_r = pygame.Rect(rx, content_top + rh_dx + rh_ba + panel_gap * 2,
                             right_w - 4, rh_prop)
        prop_inner = draw_panel(screen, prop_r, 'PROPAGATION', fonts)
        tab_bar = pygame.Rect(prop_inner.x, prop_inner.y, prop_inner.w, 20)
        tab_regions = draw_tabs(screen, tab_bar, ['drap', 'aurora', 'enlil'],
                                active_tab, fonts)
        img_rect = pygame.Rect(prop_inner.x, prop_inner.y + 24,
                               prop_inner.w, prop_inner.h - 24)
        try:
            key = tab_image_key.get(active_tab, 'real-drap')
            surf = _get_cached_image(data, key, image_cache, image_cache_ts)
            draw_image(screen, img_rect, surf)
        except Exception:
            pass

        pygame.display.flip()
        clock.tick(10)

    try:
        data.stop()
    except Exception:
        pass
    pygame.quit()


if __name__ == '__main__':
    main()
HCPYEOF

echo "Writing hamclock_tkinter.py..."
sudo tee "$INSTALL_DIR/hamclock_tkinter.py" > /dev/null << 'HCTKEOF'
"""HamClock Lite native Tkinter client.

A minimal-dependency native GUI that replaces the browser-based HamClock Lite
dashboard on Raspberry Pi 1 Model B (700 MHz ARMv6, 512 MB RAM). Fetches data
from the existing HamClock server at http://localhost:8080/api/* via the
shared hamclock_data.HamClockData class and renders the dashboard using
native Tkinter widgets, saving significant RAM/CPU vs. a browser stack.

Apt dependencies (Raspberry Pi OS):
    sudo apt install python3-tk python3-pil python3-pil.imagetk

Tkinter's built-in PhotoImage handles GIF/PGM/PNG but NOT JPEG, so Pillow
(PIL) is used for image decoding. If Pillow is unavailable, the image panels
are hidden gracefully and the rest of the dashboard still works.

Usage:
    python3 hamclock_tkinter.py

Press Escape to exit fullscreen.

Target viewport: 1440x900 fullscreen (scales gracefully on smaller screens).
"""

import io
import time
import tkinter as tk
from tkinter import ttk

from hamclock_data import HamClockData

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:  # Pillow missing — degrade image panels gracefully
    HAS_PIL = False


# ---------- Theme (K-State royal purple + gold) ----------
BG = '#2a1450'
CARD = '#3a1d65'
BORDER = '#512888'
TEXT = '#e8ddf5'
LABEL = '#b8a0d8'
BRIGHT = '#ffffff'
ACCENT_GOLD = '#f4c55c'

COND_COLORS = {
    'Good': '#22c55e',
    'Fair': '#eab308',
    'Poor': '#ef4444',
    'N/A': '#4a5568',
}

BAND_COLORS = {
    '160m': '#ff6b6b', '80m': '#f06595', '60m': '#cc5de8', '40m': '#845ef7',
    '30m': '#5c7cfa', '20m': '#339af0', '17m': '#22b8cf', '15m': '#20c997',
    '12m': '#51cf66', '10m': '#94d82d',
}
BAND_ORDER = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']

# Fonts — DejaVu Sans Mono is standard on Raspberry Pi OS.
FONT_TITLE = ('DejaVu Sans Mono', 12, 'bold')
FONT_BODY = ('DejaVu Sans Mono', 11)
FONT_VALUE = ('DejaVu Sans Mono', 11, 'bold')
FONT_LABEL = ('DejaVu Sans Mono', 9)
FONT_HEADER = ('DejaVu Sans Mono', 18, 'bold')
FONT_CLOCK = ('DejaVu Sans Mono', 13, 'bold')


def _safe(v, default='—'):
    """Return str(v) or placeholder if v is empty/None/'N/A'."""
    if v is None:
        return default
    s = str(v).strip()
    if not s or s.upper() == 'N/A':
        return default
    return s


def _make_panel(parent, title):
    """Create a titled card Frame; return (outer, body) where body holds content."""
    outer = tk.Frame(
        parent, bg=CARD, bd=1, relief='solid',
        highlightbackground=BORDER, highlightthickness=1,
    )
    header = tk.Label(
        outer, text=title, bg=BORDER, fg=ACCENT_GOLD,
        font=FONT_TITLE, anchor='w', padx=8, pady=3,
    )
    header.pack(side='top', fill='x')
    body = tk.Frame(outer, bg=CARD, padx=8, pady=6)
    body.pack(side='top', fill='both', expand=True)
    return outer, body


def _kv_row(body, row, label, initial='—'):
    """Place a label/value pair in a 2-column grid row. Returns the value Label."""
    tk.Label(
        body, text=label, bg=CARD, fg=LABEL, font=FONT_LABEL,
        anchor='w',
    ).grid(row=row, column=0, sticky='w', padx=(0, 6))
    val = tk.Label(
        body, text=initial, bg=CARD, fg=BRIGHT, font=FONT_VALUE,
        anchor='e',
    )
    val.grid(row=row, column=1, sticky='e')
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=0)
    return val


class HamClockTkApp:
    """Native Tkinter HamClock Lite dashboard."""

    def __init__(self, root):
        self.root = root
        self.data = HamClockData()
        self.data.start_background()

        root.configure(bg=BG)
        root.title('HamClock Lite')
        root.geometry('1440x900')
        try:
            root.attributes('-fullscreen', True)
        except Exception:
            pass
        root.bind('<Escape>', lambda _e: root.destroy())
        root.bind('<F11>', self._toggle_fullscreen)

        # ttk theme for Treeview / Notebook
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure(
            'HC.Treeview',
            background=CARD, foreground=TEXT, fieldbackground=CARD,
            rowheight=18, borderwidth=0, font=FONT_LABEL,
        )
        style.configure(
            'HC.Treeview.Heading',
            background=BORDER, foreground=ACCENT_GOLD, font=FONT_LABEL,
        )
        style.map('HC.Treeview', background=[('selected', BORDER)])
        style.configure('HC.TNotebook', background=CARD, borderwidth=0)
        style.configure(
            'HC.TNotebook.Tab',
            background=CARD, foreground=LABEL,
            padding=[8, 3], font=FONT_LABEL,
        )
        style.map(
            'HC.TNotebook.Tab',
            background=[('selected', BORDER)],
            foreground=[('selected', ACCENT_GOLD)],
        )

        self._value_labels = {}
        self._last_image_ts = 0
        self._image_refs = {}  # hold refs to prevent GC

        self._build_ui()
        self._update_ui()

    def _toggle_fullscreen(self, _e=None):
        try:
            cur = bool(self.root.attributes('-fullscreen'))
            self.root.attributes('-fullscreen', not cur)
        except Exception:
            pass

    # ----- UI construction -----
    def _build_ui(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        for c in range(3):
            self.root.grid_columnconfigure(c, weight=1, uniform='col')

        # --- Header bar ---
        header = tk.Frame(self.root, bg=BORDER, bd=0)
        header.grid(row=0, column=0, columnspan=3, sticky='ew', padx=4, pady=(4, 2))
        tk.Label(
            header, text='HAMCLOCK LITE', bg=BORDER, fg=ACCENT_GOLD,
            font=FONT_HEADER, padx=10, pady=6,
        ).pack(side='left')
        tk.Label(
            header, text='W0QQQ', bg=BORDER, fg=TEXT, font=FONT_BODY,
        ).pack(side='left', padx=(4, 10))
        self.status_dot = tk.Label(
            header, text='\u25cf', bg=BORDER, fg='#ef4444',
            font=FONT_HEADER,
        )
        self.status_dot.pack(side='right', padx=8)
        self.local_lbl = tk.Label(
            header, text='LOCAL --:--:--', bg=BORDER, fg=TEXT, font=FONT_CLOCK,
        )
        self.local_lbl.pack(side='right', padx=10)
        self.utc_lbl = tk.Label(
            header, text='UTC --:--:--', bg=BORDER, fg=BRIGHT, font=FONT_CLOCK,
        )
        self.utc_lbl.pack(side='right', padx=10)

        # --- Columns ---
        col_left = tk.Frame(self.root, bg=BG)
        col_mid = tk.Frame(self.root, bg=BG)
        col_right = tk.Frame(self.root, bg=BG)
        col_left.grid(row=1, column=0, sticky='nsew', padx=4, pady=2)
        col_mid.grid(row=1, column=1, sticky='nsew', padx=4, pady=2)
        col_right.grid(row=1, column=2, sticky='nsew', padx=4, pady=2)

        self._build_left_column(col_left)
        self._build_middle_column(col_mid)
        self._build_right_column(col_right)

        # --- Status bar ---
        self.status_bar = tk.Label(
            self.root, text='Solar:— Bands:— DX:—',
            bg=BORDER, fg=LABEL, font=FONT_LABEL, anchor='w', padx=8, pady=2,
        )
        self.status_bar.grid(row=2, column=0, columnspan=3, sticky='ew', padx=4, pady=(2, 4))

    def _build_left_column(self, col):
        # SOLAR
        solar_p, solar_b = _make_panel(col, 'SOLAR')
        solar_p.pack(fill='x', pady=(0, 4))
        for i, (k, lbl) in enumerate([
            ('sfi', 'SFI'), ('ssn', 'SSN'), ('aIndex', 'A-Index'),
            ('kIndex', 'K-Index'), ('xray', 'X-Ray'), ('solarWind', 'Solar Wind'),
            ('protonFlux', 'Proton Flux'), ('aurora', 'Aurora'),
        ]):
            self._value_labels['solar_' + k] = _kv_row(solar_b, i, lbl)

        # BANDS
        bands_p, bands_b = _make_panel(col, 'BANDS')
        bands_p.pack(fill='x', pady=4)
        tk.Label(bands_b, text='BAND', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='w').grid(row=0, column=0, sticky='w', padx=(0, 8))
        tk.Label(bands_b, text='DAY', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='center').grid(row=0, column=1, sticky='ew', padx=4)
        tk.Label(bands_b, text='NIGHT', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='center').grid(row=0, column=2, sticky='ew', padx=4)
        bands_b.grid_columnconfigure(0, weight=1)
        bands_b.grid_columnconfigure(1, weight=0, minsize=60)
        bands_b.grid_columnconfigure(2, weight=0, minsize=60)
        self._band_rows = {}
        for i, band in enumerate(['80m-40m', '30m-20m', '17m-15m', '12m-10m'], start=1):
            tk.Label(bands_b, text=band, bg=CARD, fg=TEXT, font=FONT_BODY,
                     anchor='w').grid(row=i, column=0, sticky='w', padx=(0, 8), pady=1)
            day = tk.Label(bands_b, text='—', bg=COND_COLORS['N/A'], fg=BRIGHT,
                           font=FONT_LABEL, width=7)
            day.grid(row=i, column=1, sticky='ew', padx=2, pady=1)
            night = tk.Label(bands_b, text='—', bg=COND_COLORS['N/A'], fg=BRIGHT,
                             font=FONT_LABEL, width=7)
            night.grid(row=i, column=2, sticky='ew', padx=2, pady=1)
            self._band_rows[band] = (day, night)

        # SDO IMAGE
        sdo_p, sdo_b = _make_panel(col, 'SDO IMAGE')
        sdo_p.pack(fill='x', pady=4)
        self.sdo_label = tk.Label(
            sdo_b, text='(image unavailable)' if not HAS_PIL else '(loading...)',
            bg=CARD, fg=LABEL, font=FONT_LABEL,
        )
        self.sdo_label.pack()

        # GEOMAGNETIC (Kp bar)
        geo_p, geo_b = _make_panel(col, 'GEOMAGNETIC')
        geo_p.pack(fill='x', pady=4)
        self.kp_value = tk.Label(geo_b, text='Kp —', bg=CARD, fg=BRIGHT,
                                 font=FONT_VALUE)
        self.kp_value.pack(anchor='w')
        self.kp_canvas = tk.Canvas(geo_b, height=14, bg=CARD, bd=0,
                                   highlightthickness=0)
        self.kp_canvas.pack(fill='x', pady=(2, 0))

        # X-RAY bar
        xray_p, xray_b = _make_panel(col, 'X-RAY')
        xray_p.pack(fill='x', pady=4)
        self.xray_value = tk.Label(xray_b, text='—', bg=CARD, fg=BRIGHT,
                                   font=FONT_VALUE)
        self.xray_value.pack(anchor='w')
        self.xray_canvas = tk.Canvas(xray_b, height=14, bg=CARD, bd=0,
                                     highlightthickness=0)
        self.xray_canvas.pack(fill='x', pady=(2, 0))

        # OPEN BANDS
        open_p, open_b = _make_panel(col, 'OPEN BANDS')
        open_p.pack(fill='x', pady=(4, 0))
        self.open_lbl = tk.Label(
            open_b, text='OPEN: —', bg=CARD, fg='#22c55e', font=FONT_BODY,
            anchor='w', justify='left', wraplength=360,
        )
        self.open_lbl.pack(anchor='w', fill='x')
        self.closed_lbl = tk.Label(
            open_b, text='CLOSED: —', bg=CARD, fg='#ef4444', font=FONT_BODY,
            anchor='w', justify='left', wraplength=360,
        )
        self.closed_lbl.pack(anchor='w', fill='x')

    def _build_middle_column(self, col):
        muf_p, muf_b = _make_panel(col, 'MUF STATUS')
        muf_p.pack(fill='x', pady=(0, 4))
        for i, (k, lbl) in enumerate([
            ('fof2', 'foF2 (MHz)'),
            ('geomagField', 'Geomag Field'),
            ('kIndex', 'K-Index'),
            ('sfi', 'SFI'),
            ('ssn', 'SSN'),
            ('heliumLine', 'Helium Line'),
            ('signalNoise', 'Signal/Noise'),
            ('magneticField', 'Magnetic Field'),
        ]):
            self._value_labels['muf_' + k] = _kv_row(muf_b, i, lbl)

        # Info / update panel
        info_p, info_b = _make_panel(col, 'STATION')
        info_p.pack(fill='both', expand=True, pady=4)
        self.updated_lbl = tk.Label(
            info_b, text='Updated: —', bg=CARD, fg=LABEL, font=FONT_LABEL,
            anchor='w', justify='left', wraplength=360,
        )
        self.updated_lbl.pack(anchor='w', fill='x', pady=(0, 4))
        self.server_lbl = tk.Label(
            info_b, text='Server: ' + self.data.server_url, bg=CARD, fg=LABEL,
            font=FONT_LABEL, anchor='w',
        )
        self.server_lbl.pack(anchor='w', fill='x')
        self.errors_lbl = tk.Label(
            info_b, text='', bg=CARD, fg='#ef4444', font=FONT_LABEL,
            anchor='nw', justify='left', wraplength=360,
        )
        self.errors_lbl.pack(anchor='w', fill='x', pady=(6, 0))

    def _build_right_column(self, col):
        # DX SPOTS (Treeview)
        dx_p, dx_b = _make_panel(col, 'DX SPOTS')
        dx_p.pack(fill='x', pady=(0, 4))
        cols = ('freq', 'band', 'dx', 'de', 'utc')
        self.dx_tree = ttk.Treeview(
            dx_b, columns=cols, show='headings', height=8, style='HC.Treeview',
        )
        widths = {'freq': 70, 'band': 50, 'dx': 90, 'de': 90, 'utc': 50}
        for c in cols:
            self.dx_tree.heading(c, text=c.upper())
            self.dx_tree.column(c, width=widths[c], anchor='w', stretch=True)
        self.dx_tree.pack(fill='both', expand=True)

        # BAND ACTIVITY — Canvas bars
        act_p, act_b = _make_panel(col, 'BAND ACTIVITY')
        act_p.pack(fill='x', pady=4)
        self.activity_canvas = tk.Canvas(
            act_b, height=180, bg=CARD, bd=0, highlightthickness=0,
        )
        self.activity_canvas.pack(fill='x')

        # PROPAGATION — ttk.Notebook with tabs for DRAP/AURORA/ENLIL
        prop_p, prop_b = _make_panel(col, 'PROPAGATION')
        prop_p.pack(fill='both', expand=True, pady=(4, 0))
        self.prop_nb = ttk.Notebook(prop_b, style='HC.TNotebook')
        self.prop_nb.pack(fill='both', expand=True)
        self.prop_tabs = {}
        for key, title in [('real-drap', 'DRAP'), ('drap', 'AURORA'),
                           ('enlil', 'ENLIL')]:
            frame = tk.Frame(self.prop_nb, bg=CARD)
            lbl = tk.Label(
                frame, text='(loading...)' if HAS_PIL else '(PIL missing)',
                bg=CARD, fg=LABEL, font=FONT_LABEL,
            )
            lbl.pack(expand=True)
            self.prop_nb.add(frame, text=title)
            self.prop_tabs[key] = lbl

    # ----- Image helpers -----
    def _load_image(self, data_bytes, max_w, max_h):
        if not data_bytes or not HAS_PIL:
            return None
        try:
            img = Image.open(io.BytesIO(data_bytes))
            img.thumbnail((max_w, max_h), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _set_image(self, label, key, photo):
        """Assign photo to label; hold ref to prevent GC."""
        if photo is None:
            return
        self._image_refs[key] = photo
        label.configure(image=photo, text='')
        label.image_ref = photo  # belt and suspenders

    # ----- Update loop -----
    def _update_ui(self):
        try:
            self._update_clocks()
            self._update_solar()
            self._update_muf()
            self._update_bands()
            self._update_dxspots()
            self._update_band_activity()
            self._update_open_closed()
            self._update_images()
            self._update_status()
        except Exception as e:
            try:
                self.status_bar.configure(text='update error: {}'.format(e))
            except Exception:
                pass
        self.root.after(1000, self._update_ui)

    def _update_clocks(self):
        now = time.time()
        self.utc_lbl.configure(text='UTC ' + time.strftime('%H:%M:%S', time.gmtime(now)))
        self.local_lbl.configure(text='LOCAL ' + time.strftime('%H:%M:%S', time.localtime(now)))
        ok = bool(self.data.last_data_refresh) and (now - self.data.last_data_refresh) < 180
        self.status_dot.configure(fg='#22c55e' if ok else '#ef4444')

    def _update_solar(self):
        s = self.data.solar or {}
        for key in ['sfi', 'ssn', 'aIndex', 'kIndex', 'xray', 'solarWind',
                    'protonFlux', 'aurora']:
            self._value_labels['solar_' + key].configure(text=_safe(s.get(key)))

        # Kp bar (0-9 scale)
        kp_raw = s.get('kIndex')
        try:
            kp = float(kp_raw)
        except (TypeError, ValueError):
            kp = None
        self.kp_value.configure(text='Kp ' + (_safe(kp_raw)))
        self._draw_bar(self.kp_canvas, kp, 9.0,
                       ['#22c55e', '#22c55e', '#22c55e', '#22c55e',
                        '#eab308', '#eab308', '#ef4444', '#ef4444',
                        '#ef4444', '#ef4444'])

        # X-Ray bar
        xray_raw = s.get('xray') or ''
        self.xray_value.configure(text=_safe(xray_raw))
        xv = self._xray_to_scalar(xray_raw)
        self._draw_bar(self.xray_canvas, xv, 5.0,
                       ['#22c55e', '#84cc16', '#eab308', '#f97316', '#ef4444'])

    def _xray_to_scalar(self, xray):
        """Convert NOAA xray class (e.g. 'B4.0', 'M1.5', 'X2.0') to 0..5 scalar."""
        if not xray or len(xray) < 2:
            return None
        cls = xray[0].upper()
        try:
            mag = float(xray[1:])
        except ValueError:
            mag = 1.0
        # Normalize within class: 1-9 → 0..1
        frac = max(0.0, min(1.0, (mag - 1.0) / 8.0))
        base = {'A': 0, 'B': 1, 'C': 2, 'M': 3, 'X': 4}.get(cls, 0)
        return base + frac

    def _draw_bar(self, canvas, value, max_val, gradient_colors):
        canvas.delete('all')
        w = int(canvas.winfo_width()) or 360
        h = int(canvas.winfo_height()) or 14
        canvas.create_rectangle(0, 0, w, h, fill='#1a0a30', outline=BORDER)
        if value is None or max_val <= 0:
            return
        frac = max(0.0, min(1.0, value / max_val))
        fill_w = int(w * frac)
        if fill_w < 1:
            return
        idx = min(len(gradient_colors) - 1, int(frac * len(gradient_colors)))
        canvas.create_rectangle(0, 0, fill_w, h,
                                fill=gradient_colors[idx], outline='')

    def _update_muf(self):
        s = self.data.solar or {}
        for key in ['fof2', 'geomagField', 'kIndex', 'sfi', 'ssn',
                    'heliumLine', 'signalNoise', 'magneticField']:
            self._value_labels['muf_' + key].configure(text=_safe(s.get(key)))
        self.updated_lbl.configure(text='Updated: ' + _safe(s.get('updated')))

    def _update_bands(self):
        b = self.data.bands or {}
        for band, (day_lbl, night_lbl) in self._band_rows.items():
            entry = b.get(band) or {}
            day = entry.get('day') or 'N/A'
            night = entry.get('night') or 'N/A'
            day_lbl.configure(text=day, bg=COND_COLORS.get(day, COND_COLORS['N/A']))
            night_lbl.configure(text=night, bg=COND_COLORS.get(night, COND_COLORS['N/A']))

    def _update_dxspots(self):
        spots = self.data.dxspots or []
        existing = self.dx_tree.get_children()
        if len(existing) != min(len(spots), 12):
            self.dx_tree.delete(*existing)
            existing = ()
        rows = spots[:12]
        if not existing:
            for sp in rows:
                utc = (sp.get('time') or '')[:4]
                self.dx_tree.insert('', 'end', values=(
                    _safe(sp.get('frequency')),
                    _safe(sp.get('band')),
                    _safe(sp.get('dx')),
                    _safe(sp.get('spotter')),
                    utc,
                ))
        else:
            for iid, sp in zip(existing, rows):
                utc = (sp.get('time') or '')[:4]
                self.dx_tree.item(iid, values=(
                    _safe(sp.get('frequency')),
                    _safe(sp.get('band')),
                    _safe(sp.get('dx')),
                    _safe(sp.get('spotter')),
                    utc,
                ))

    def _update_band_activity(self):
        canvas = self.activity_canvas
        canvas.delete('all')
        spots = self.data.dxspots or []
        counts = {}
        for sp in spots:
            band = sp.get('band')
            if band in BAND_COLORS:
                counts[band] = counts.get(band, 0) + 1
        max_count = max(counts.values()) if counts else 1

        w = int(canvas.winfo_width()) or 380
        h = int(canvas.winfo_height()) or 180
        rows = len(BAND_ORDER)
        row_h = max(12, h // rows)
        label_w = 44
        bar_x0 = label_w + 4
        bar_max = max(40, w - bar_x0 - 40)
        for i, band in enumerate(BAND_ORDER):
            y = i * row_h + 2
            canvas.create_text(
                4, y + row_h / 2 - 2, text=band, anchor='w',
                fill=LABEL, font=FONT_LABEL,
            )
            count = counts.get(band, 0)
            frac = count / max_count if max_count else 0
            bar_w = int(bar_max * frac)
            if bar_w > 0:
                canvas.create_rectangle(
                    bar_x0, y, bar_x0 + bar_w, y + row_h - 4,
                    fill=BAND_COLORS[band], outline='',
                )
            canvas.create_text(
                bar_x0 + bar_w + 4, y + row_h / 2 - 2,
                text=str(count), anchor='w', fill=TEXT, font=FONT_LABEL,
            )

    def _update_open_closed(self):
        b = self.data.bands or {}
        open_list = []
        closed_list = []
        for band, entry in b.items():
            if not isinstance(entry, dict):
                continue
            day = entry.get('day') or 'N/A'
            night = entry.get('night') or 'N/A'
            if day == 'Good' or night == 'Good':
                open_list.append(band)
            elif day == 'Poor' and night == 'Poor':
                closed_list.append(band)
        self.open_lbl.configure(
            text='OPEN: ' + (', '.join(open_list) if open_list else '—'),
        )
        self.closed_lbl.configure(
            text='CLOSED: ' + (', '.join(closed_list) if closed_list else '—'),
        )

    def _update_images(self):
        ts = self.data.last_image_refresh
        if ts == self._last_image_ts:
            return
        self._last_image_ts = ts
        imgs = self.data.images or {}

        sdo = self._load_image(imgs.get('solar-image'), 360, 220)
        if sdo is not None:
            self._set_image(self.sdo_label, 'sdo', sdo)
        elif not HAS_PIL:
            self.sdo_label.configure(text='(PIL missing)')

        for key, label in self.prop_tabs.items():
            photo = self._load_image(imgs.get(key), 380, 260)
            if photo is not None:
                self._set_image(label, 'prop_' + key, photo)
            elif not HAS_PIL:
                label.configure(text='(PIL missing)')
            else:
                label.configure(text='(no image)')

    def _update_status(self):
        now = time.time()
        d_age = int(now - self.data.last_data_refresh) if self.data.last_data_refresh else -1
        i_age = int(now - self.data.last_image_refresh) if self.data.last_image_refresh else -1
        def fmt(a):
            return '{}s'.format(a) if a >= 0 else '—'
        errs = [k for k, v in (self.data.errors or {}).items() if v]
        status = 'Data:{}  Images:{}  Spots:{}  Errors:{}'.format(
            fmt(d_age), fmt(i_age),
            len(self.data.dxspots or []), len(errs),
        )
        self.status_bar.configure(text=status)
        if errs:
            self.errors_lbl.configure(text='Errors: ' + ', '.join(errs[:3]))
        else:
            self.errors_lbl.configure(text='')


def main():
    root = tk.Tk()
    HamClockTkApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
HCTKEOF

# ── Step 5: Create hamclock-lite systemd service ────────────────────
echo "Creating HamClock server service..."
if ! systemctl is-enabled hamclock-lite &>/dev/null; then
    sudo tee /etc/systemd/system/hamclock-lite.service > /dev/null <<EOF
[Unit]
Description=HamClock Lite Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable hamclock-lite
    sudo systemctl start hamclock-lite
fi

# ── Step 6: Install X server packages (browser/tkinter modes) ──────
echo "Installing display server packages (this may take 15-30 minutes on a Pi 1)..."
sudo apt update
if [ "$KIOSK_MODE" = "browser" ] || [ "$KIOSK_MODE" = "tkinter" ]; then
    sudo apt install -y xserver-xorg xinit x11-xserver-utils unclutter curl matchbox-window-manager
else
    sudo apt install -y curl
fi

# Mode-specific Python packages
if [ "$KIOSK_MODE" = "pygame" ]; then
    sudo apt install -y python3-pygame
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    sudo apt install -y python3-tk python3-pil python3-pil.imagetk
fi

# ── Step 7: Try browser fallback chain (browser mode only) ─────────
BROWSER=""
BROWSER_CMD=""
if [ "$KIOSK_MODE" = "browser" ]; then
    for pkg in surf epiphany-browser midori chromium-browser chromium; do
        if sudo apt install -y "$pkg" 2>&1 | tail -1; then
            case "$pkg" in
                surf) BROWSER="surf"; BROWSER_CMD="surf http://localhost:8080" ;;
                epiphany-browser) BROWSER="epiphany"; BROWSER_CMD="epiphany-browser --application-mode http://localhost:8080" ;;
                midori) BROWSER="midori"; BROWSER_CMD="midori -e Fullscreen -a http://localhost:8080" ;;
                chromium-browser|chromium) BROWSER="chromium"; BROWSER_CMD="$pkg --kiosk --noerrdialogs --disable-translate --no-first-run --disable-features=TranslateUI --disk-cache-size=0 http://localhost:8080" ;;
            esac
            break
        fi
    done

    if [ -z "$BROWSER" ]; then
        echo "ERROR: Could not install any browser (tried surf, epiphany, midori, chromium)."
        echo "Please install a browser manually and re-run this script."
        exit 1
    fi
    echo "Browser installed: $BROWSER"
fi

# ── Step 8: Set Xwrapper.config ─────────────────────────────────────
sudo mkdir -p /etc/X11
sudo tee /etc/X11/Xwrapper.config > /dev/null <<XEOF
allowed_users=anybody
needs_root_rights=yes
XEOF

# Add user to video and tty groups for X server access
sudo usermod -aG video,tty,input "$SERVICE_USER"

# ── Step 8b: Write X11 monitor config (auto-detect resolution, 16-bit for Pi 1) ──
sudo mkdir -p /usr/share/X11/xorg.conf.d
sudo tee /usr/share/X11/xorg.conf.d/10-monitor.conf > /dev/null << 'MONEOF'
Section "Device"
    Identifier "default"
    Driver "fbdev"
EndSection

Section "Screen"
    Identifier "default"
    Device "default"
    Monitor "default"
    DefaultDepth 16
    SubSection "Display"
        Depth 16
    EndSubSection
EndSection

Section "Monitor"
    Identifier "default"
    Option "PreferredMode" "true"
EndSection
MONEOF

# ── Step 9: Create kiosk.sh launch script (mode-specific) ──────────
if [ "$KIOSK_MODE" = "browser" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<KIOSKEOF
#!/bin/bash
# Wait for HamClock server to be ready
for i in \$(seq 1 30); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Disable screen blanking and power management
xset s off
xset -dpms
xset s noblank

# Show a real cursor (fbdev has no HW cursor; without this nothing is drawn)
# then auto-hide it after 3s idle.
xsetroot -cursor_name left_ptr
unclutter -idle 3 -root &

# Start matchbox window manager (auto-maximizes all windows)
matchbox-window-manager -use_titlebar no -use_desktop_mode plain &
sleep 1

# Launch browser (matchbox will maximize it)
exec $BROWSER_CMD
KIOSKEOF
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<'KIOSKEOF'
#!/bin/bash
for i in $(seq 1 30); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
xset s off
xset -dpms
xset s noblank
exec python3 /opt/hamclock-lite/hamclock_tkinter.py
KIOSKEOF
elif [ "$KIOSK_MODE" = "pygame" ]; then
    sudo tee /opt/hamclock-lite/kiosk.sh > /dev/null <<'KIOSKEOF'
#!/bin/bash
for i in $(seq 1 30); do
    if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
export SDL_VIDEODRIVER=fbcon
export SDL_FBDEV=/dev/fb0
exec python3 /opt/hamclock-lite/hamclock_pygame.py
KIOSKEOF
fi
sudo chmod +x /opt/hamclock-lite/kiosk.sh

# ── Step 10: Create hamclock-kiosk systemd service (mode-specific) ──
if [ "$KIOSK_MODE" = "pygame" ]; then
    sudo tee /etc/systemd/system/hamclock-kiosk.service > /dev/null <<EOF
[Unit]
Description=HamClock Kiosk Display (pygame framebuffer)
After=hamclock-lite.service
Wants=hamclock-lite.service

[Service]
Type=simple
User=$SERVICE_USER
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
ExecStart=/opt/hamclock-lite/kiosk.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
else
    sudo tee /etc/systemd/system/hamclock-kiosk.service > /dev/null <<EOF
[Unit]
Description=HamClock Kiosk Display
After=hamclock-lite.service
Wants=hamclock-lite.service

[Service]
Type=simple
User=$SERVICE_USER
Environment=DISPLAY=:0
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
ExecStart=/usr/bin/xinit /opt/hamclock-lite/kiosk.sh -- :0 vt7
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
fi

# ── Step 11: Fix consoleblank in cmdline.txt ────────────────────────
CMDLINE=""
if [ -f /boot/firmware/cmdline.txt ]; then
    CMDLINE="/boot/firmware/cmdline.txt"
elif [ -f /boot/cmdline.txt ]; then
    CMDLINE="/boot/cmdline.txt"
fi
if [ -n "$CMDLINE" ]; then
    if ! grep -q "consoleblank=0" "$CMDLINE"; then
        sudo sed -i 's/$/ consoleblank=0/' "$CMDLINE"
    fi
fi

# Force HDMI output even if no monitor detected at boot
BOOT_CONFIG=""
if [ -f /boot/firmware/config.txt ]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    BOOT_CONFIG="/boot/config.txt"
fi
if [ -n "$BOOT_CONFIG" ]; then
    grep -q "hdmi_force_hotplug" "$BOOT_CONFIG" || sudo sh -c "echo 'hdmi_force_hotplug=1' >> $BOOT_CONFIG"
    grep -q "hdmi_drive" "$BOOT_CONFIG" || sudo sh -c "echo 'hdmi_drive=2' >> $BOOT_CONFIG"
fi

# ── Step 12: Enable and start both services ─────────────────────────
sudo systemctl daemon-reload
sudo systemctl enable hamclock-lite hamclock-kiosk
# Always restart to pick up any file changes
sudo systemctl restart hamclock-lite
sudo systemctl restart hamclock-kiosk

# ── Step 13: Print IP address and completion message ────────────────
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "=== Installation Complete — Kiosk Mode Installed ($KIOSK_MODE) ==="
echo "HamClock will now display fullscreen on this Pi's monitor."
echo "It will auto-start on every boot."
echo ""
if [ "$KIOSK_MODE" = "browser" ]; then
    echo "Display: browser ($BROWSER)"
elif [ "$KIOSK_MODE" = "tkinter" ]; then
    echo "Display: native tkinter client (Python/X11)"
elif [ "$KIOSK_MODE" = "pygame" ]; then
    echo "Display: native pygame client (framebuffer, no X)"
fi
echo ""
echo "Commands:"
echo "  sudo systemctl status hamclock-kiosk   — check kiosk status"
echo "  sudo systemctl restart hamclock-kiosk  — restart display"
echo "  sudo systemctl stop hamclock-kiosk     — stop display"
echo "  sudo systemctl disable hamclock-kiosk  — disable auto-start"
echo ""
echo "To go back to normal CLI, run:"
echo "  sudo systemctl disable hamclock-kiosk"
echo "  sudo systemctl stop hamclock-kiosk"
echo ""
echo "Also accessible from any browser at: http://${PI_IP}:8080"

}
main "$@"
