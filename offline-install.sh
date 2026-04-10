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

INSTALL_DIR="/opt/hamclock-lite"
SERVICE_USER="${SUDO_USER:-$USER}"

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
        elif path.startswith('/api/real-drap'):
            if CACHE.get('real_drap_image'):
                self.send_binary(CACHE['real_drap_image'], 'image/png')
            else:
                self.send_json({'error': 'not loaded'})
        elif path.startswith('/api/drap'):
            if CACHE.get('drap_image'):
                self.send_binary(CACHE['drap_image'], 'image/jpeg')
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
--green:#22c55e;--yellow:#eab308;--red:#ef4444;--cyan:#06b6d4;--muted:#4a5568;--label:#6b7b8d;
}
html,body{
width:100%;height:100vh;overflow:hidden;
background:var(--bg);color:var(--text);
font-family:'Courier New','Liberation Mono',monospace;
font-size:clamp(10px,1.4vh,16px);
}
.hdr{
display:flex;align-items:center;justify-content:space-between;
height:clamp(20px,3vh,30px);
padding:0 clamp(4px,0.8vw,12px);
background:var(--card);border-bottom:1px solid var(--border);
font-size:clamp(9px,1.3vh,14px);
}
.hdr-title{color:var(--cyan);font-weight:bold;letter-spacing:2px}
.hdr-clocks{color:var(--bright);letter-spacing:1px}
.hdr-clocks span{margin-left:clamp(8px,2vw,24px)}
.hdr-utc{color:var(--cyan)}
.hdr-dot{display:inline-block;width:8px;height:8px;background:var(--green);margin-left:clamp(8px,1.5vw,16px)}
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
font-size:clamp(8px,1.1vh,13px);
color:var(--label);letter-spacing:1px;
border-bottom:1px solid var(--border);
flex-shrink:0;
}
.panel-body{padding:4px 6px;flex:1;overflow:hidden}
.timer{color:var(--muted);font-size:clamp(7px,0.9vh,11px)}
.solar-flex{flex:1}
.bands-flex{flex:0 0 auto}
.mid-img{flex:0 0 auto;min-height:0;overflow:hidden}
.dx-full{flex:1;min-height:0}
.s-row{
display:flex;justify-content:space-between;align-items:center;
padding:clamp(1px,0.2vh,3px) 0;
border-bottom:1px solid var(--border);
}
.s-lbl{color:var(--label);font-size:clamp(8px,1vh,12px);flex:0 0 clamp(40px,5vw,70px)}
.s-val{color:var(--bright);font-size:clamp(9px,1.2vh,14px);font-weight:bold;text-align:right;flex:1}
.kp-wrap{display:flex;align-items:center;gap:clamp(2px,0.3vw,6px)}
.kp-bar{height:clamp(6px,0.8vh,10px);background:var(--bg);flex:1;overflow:hidden}
.kp-fill{height:100%}
.band-row{
display:flex;align-items:center;
padding:clamp(1px,0.15vh,2px) 0;
border-bottom:1px solid var(--border);
font-size:clamp(8px,1vh,12px);
}
.band-name{flex:0 0 clamp(40px,5vw,60px);color:var(--bright);font-weight:bold}
.band-cond{
flex:0 0 clamp(16px,2vw,24px);
text-align:center;
font-weight:bold;
font-size:clamp(8px,1vh,12px);
margin:0 clamp(2px,0.3vw,4px);
padding:clamp(0px,0.1vh,1px) 0;
}
.band-lbl{color:var(--label);font-size:clamp(7px,0.8vh,10px);flex:0 0 clamp(24px,3vw,36px);text-align:center}
.cG{background:#22c55e;color:#000}.cF{background:#eab308;color:#000}.cP{background:#ef4444;color:#fff}.cN{background:var(--muted);color:#fff}
.img-wrap{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;min-height:0}
.img-wrap img{object-fit:contain;max-width:100%;max-height:100%;display:block}
#imgSolar{height:12vh;width:100%;object-fit:contain}
#imgMuf{height:auto;width:100%;max-height:90vh;object-fit:contain}
#imgEnlil{height:12vh;width:100%;object-fit:contain}
#imgDrap{height:12vh;width:100%;object-fit:contain}
.dx-tbl{width:100%;border-collapse:collapse}
.dx-tbl th{
font-size:clamp(9px,1vw,12px);color:var(--label);
text-align:left;padding:clamp(1px,0.2vh,3px) clamp(2px,0.3vw,6px);
border-bottom:1px solid var(--border);
position:sticky;top:0;background:var(--card);
letter-spacing:1px;
}
.dx-tbl td{
padding:clamp(1px,0.15vh,2px) clamp(2px,0.3vw,6px);
border-bottom:1px solid var(--border);
font-size:clamp(10px,1.2vw,13px);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.dx-freq{color:var(--label);font-size:clamp(10px,1.2vw,13px)}
.dx-band{color:var(--cyan);font-size:clamp(9px,1.1vw,12px)}
.dx-call{color:var(--bright);font-weight:bold;font-size:clamp(10px,1.2vw,13px)}
.dx-sp{color:var(--muted);font-size:clamp(9px,1.1vw,12px)}
.dx-tm{color:var(--muted);font-size:clamp(9px,1.1vw,12px)}
.dx-body-wrap{flex:1;overflow:hidden;min-height:0}
.sbar{
display:flex;align-items:center;justify-content:space-between;
height:clamp(16px,2.5vh,28px);
padding:0 clamp(6px,1vw,20px);
background:var(--card);border-top:1px solid var(--border);
font-size:clamp(7px,0.9vh,11px);color:var(--muted);
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
<span class="theme-swatch" style="background:#120a20;border-bottom:3px solid #F4C55C"></span>
<span class="theme-name">K-STATE</span>
</label>
</div>
</div>
<div class="setup-field">
<label class="setup-label">TIME SERVER (NTP)</label>
<input type="text" id="inNtp" class="setup-input" placeholder="pool.ntp.org (default)">
</div>
<button class="setup-btn" onclick="saveSetup()">START</button>
</div>
</div>
<!-- Dashboard -->
<div id="dashboard">
<div class="hdr">
<span>
<span class="hdr-title">HAMCLOCK LITE</span>
<span id="hdrCallsign" style="color:var(--green);margin-left:12px;cursor:pointer"></span>
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
<div class="panel-title"><span>WSA-ENLIL</span><span class="timer" id="tmEnlil"></span></div>
<div class="panel-body img-wrap" style="padding:2px"><img id="imgEnlil" src="/api/enlil" alt="Enlil" style="height:12vh;width:100%;object-fit:contain"></div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>AURORA</span><span class="timer" id="tmDrap"></span></div>
<div class="panel-body img-wrap" style="padding:2px"><img id="imgDrap" src="/api/drap" alt="Aurora" style="height:10vh;width:100%;object-fit:contain"></div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>DRAP</span><span class="timer" id="tmRealDrap"></span></div>
<div class="panel-body img-wrap" style="padding:2px"><img id="imgRealDrap" src="/api/real-drap" alt="DRAP" style="height:10vh;width:100%;object-fit:contain"></div>
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
<div class="panel" style="flex:0 0 auto;max-height:25vh;min-height:0">
<div class="panel-title"><span>DX CLUSTER</span><span class="timer" id="tmDx"></span></div>
<div class="panel-body dx-body-wrap" style="padding:0;overflow-y:auto;max-height:calc(25vh - 22px)">
<table class="dx-tbl"><thead><tr><th>FREQ</th><th>B</th><th>DX CALL</th><th>DE</th><th>UTC</th></tr></thead><tbody id="dxBody"><tr><td colspan="5" style="color:var(--muted);padding:8px">Loading...</td></tr></tbody></table>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>BAND ACTIVITY</span></div>
<div class="panel-body" id="bandActivity" style="padding:4px 6px;overflow-y:auto">
<span style="color:var(--muted);font-size:clamp(8px,1vh,11px)">Waiting for DX data...</span>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>X-RAY FLUX</span></div>
<div class="panel-body" id="xrayBody" style="padding:4px 6px">
<span style="color:var(--muted);font-size:clamp(8px,1vh,11px)">Waiting for data...</span>
</div>
</div>
<div class="panel" style="flex:0 0 auto">
<div class="panel-title"><span>GEOMAGNETIC</span></div>
<div class="panel-body" id="geomagBody" style="padding:4px 6px">
<span style="color:var(--muted);font-size:clamp(8px,1vh,11px)">Waiting for data...</span>
</div>
</div>
<div class="panel" style="flex:1;min-height:0">
<div class="panel-title"><span>OPEN BANDS</span></div>
<div class="panel-body" id="openBandsBody" style="padding:4px 6px;overflow-y:auto">
<span style="color:var(--muted);font-size:clamp(8px,1vh,11px)">Waiting for data...</span>
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
classic:{cyan:'#06b6d4',green:'#22c55e',bg:'#0a0e14',card:'#111820',border:'#1a2530',label:'#8899aa',muted:'#607080'},
amber:{cyan:'#f59e0b',green:'#f59e0b',bg:'#1a1000',card:'#1f1800',border:'#332800',label:'#B88060',muted:'#8A6840'},
blue:{cyan:'#3b82f6',green:'#60a5fa',bg:'#0a0f1e',card:'#0f1628',border:'#1a2540',label:'#7090b0',muted:'#506888'},
red:{cyan:'#ef4444',green:'#f87171',bg:'#1a0a0a',card:'#201010',border:'#3a1a1a',label:'#b07070',muted:'#905858'},
kstate:{cyan:'#F4C55C',green:'#F4C55C',bg:'#120a20',card:'#1e1230',border:'#3d2660',bright:'#FFFFFF',text:'#E7DED0',label:'#C8B8A0',muted:'#9080B0'}
};

function applySettings(s){
var el=document.getElementById('hdrCallsign');
if(el)el.textContent=s.callsign||'';
var root=document.documentElement;
var t=themes[s.theme]||themes.classic;
root.style.setProperty('--cyan',t.cyan);
root.style.setProperty('--green',t.green);
root.style.setProperty('--bg',t.bg);
root.style.setProperty('--card',t.card);
root.style.setProperty('--border',t.border);
if(t.bright)root.style.setProperty('--bright',t.bright);
if(t.text)root.style.setProperty('--text',t.text);
if(t.label)root.style.setProperty('--label',t.label);
if(t.muted)root.style.setProperty('--muted',t.muted);
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

// Make saveSetup global
window.saveSetup=function(){
var s={
callsign:document.getElementById('inCallsign').value.toUpperCase().trim(),
grid:document.getElementById('inGrid').value.toUpperCase().trim(),
timezone:document.getElementById('inTimezone').value,
theme:(document.querySelector('input[name="theme"]:checked')||{}).value||'classic',
ntp:document.getElementById('inNtp').value.trim()||'pool.ntp.org'
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
var tmEnlil=document.getElementById('tmEnlil');
var tmDrap=document.getElementById('tmDrap');
var tmRealDrap=document.getElementById('tmRealDrap');

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
if(lastImageFetch){var ic='next \u21BB '+formatCountdown(lastImageFetch,IMAGE_INTERVAL);tmSolarImg.textContent=ic;tmMuf.textContent=ic;tmEnlil.textContent=ic;tmDrap.textContent=ic;if(tmRealDrap)tmRealDrap.textContent=ic;}
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
// Limit to visible rows based on viewport
var maxRows=Math.min(spots.length,Math.floor((window.innerHeight-120)/18));
if(maxRows<5)maxRows=5;
if(maxRows>spots.length)maxRows=spots.length;
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
var bandOrder=['160m','80m','60m','40m','30m','20m','17m','15m','12m','10m','6m','2m','70cm'];
var sorted=bandOrder.filter(function(b){return counts[b];});
var max=counts[sorted[0]]||1;
var h='';
var bandColors={
'160m':'#ff6b6b','80m':'#f06595','60m':'#cc5de8','40m':'#845ef7',
'30m':'#5c7cfa','20m':'#339af0','17m':'#22b8cf','15m':'#20c997',
'12m':'#51cf66','10m':'#94d82d','6m':'#fcc419','2m':'#ff922b'
};
sorted.forEach(function(band){
var pct=Math.round(counts[band]/max*100);
var color=bandColors[band]||'#666';
h+='<div style="display:flex;align-items:center;gap:6px;margin:2px 0">';
h+='<span style="width:30px;text-align:right;color:var(--label);font-size:clamp(9px,1vw,11px)">'+esc(band)+'</span>';
h+='<div style="flex:1;height:clamp(8px,1.2vh,14px);background:var(--bg)">';
h+='<div style="width:'+pct+'%;height:100%;background:'+color+'"></div>';
h+='</div>';
h+='<span style="width:20px;color:var(--text);font-size:clamp(9px,1vw,11px)">'+counts[band]+'</span>';
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
'<span style="font-size:clamp(14px,2.5vh,24px);font-weight:bold;color:'+color+'">'+esc(xr)+'</span>'+
'<div style="flex:1;height:clamp(6px,1vh,10px);background:var(--bg)">'+
'<div style="width:'+level+'%;height:100%;background:'+color+'"></div></div>'+
'<span style="font-size:clamp(8px,1vh,11px);color:'+color+'">'+label+'</span></div>';
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
'<span style="font-size:clamp(14px,2.5vh,24px);font-weight:bold;color:'+color+'">Kp '+kp+'</span>'+
'<div style="flex:1;height:clamp(6px,1vh,10px);background:var(--bg)">'+
'<div style="width:'+pct+'%;height:100%;background:'+color+'"></div></div>'+
'<span style="font-size:clamp(8px,1vh,11px);color:'+color+'">'+esc(geo.toUpperCase())+'</span></div>';
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
h+='<div style="margin-bottom:4px"><span style="color:#22c55e;font-size:clamp(8px,1vh,11px)">OPEN: </span>';
h+=open.map(function(b){return'<span style="color:#22c55e;font-weight:bold;margin-right:6px">'+esc(b)+'</span>';}).join('');
h+='</div>';
}
if(closed.length){
h+='<div><span style="color:#ef4444;font-size:clamp(8px,1vh,11px)">CLOSED: </span>';
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
elDot.style.background='var(--green)';
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

# ── Step 5: Create hamclock-lite systemd service ────────────────────
echo "Creating HamClock server service..."
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

# ── Step 6: Install X server packages ───────────────────────────────
echo "Installing display server and browser (this may take 15-30 minutes on a Pi 1)..."
sudo apt update
sudo apt install -y xserver-xorg xinit x11-xserver-utils unclutter curl matchbox-window-manager

# ── Step 7: Try browser fallback chain ──────────────────────────────
BROWSER=""
BROWSER_CMD=""
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

# ── Step 9: Create kiosk.sh launch script ───────────────────────────
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

# Hide mouse cursor after 3 seconds of inactivity
unclutter -idle 3 -root &

# Start matchbox window manager (auto-maximizes all windows)
matchbox-window-manager -use_titlebar no -use_desktop_mode plain &
sleep 1

# Launch browser (matchbox will maximize it)
exec $BROWSER_CMD
KIOSKEOF
sudo chmod +x /opt/hamclock-lite/kiosk.sh

# ── Step 10: Create hamclock-kiosk systemd service ──────────────────
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
ExecStart=/usr/bin/xinit /opt/hamclock-lite/kiosk.sh -- :0 vt7 -nocursor
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

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
echo "=== Installation Complete — Kiosk Mode Installed ==="
echo "HamClock will now display fullscreen on this Pi's monitor."
echo "It will auto-start on every boot."
echo ""
echo "Browser: $BROWSER"
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
