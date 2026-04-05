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
    'solar_updated': 0,
    'bands_updated': 0,
    'dx_updated': 0,
}

UA = 'HamClockLite/1.0'


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
                    spot = {
                        'frequency': freq,
                        'spotter': parts[0].strip(),
                        'dx': parts[2].strip(),
                        'comment': parts[3].strip() if len(parts) > 3 else '',
                        'time': parts[4].strip() if len(parts) > 4 else '',
                        'band': freq_to_band(freq_khz),
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


def background_fetcher():
    """Background thread to periodically fetch data"""
    fetch_hamqsl()
    fetch_dx()

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
    last_solar = time.time()
    last_dx = time.time()

    while True:
        time.sleep(10)
        now = time.time()
        if now - last_solar >= solar_interval:
            fetch_hamqsl()
            last_solar = now
        if now - last_dx >= dx_interval:
            fetch_dx()
            last_dx = now


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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HamClock Lite</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --bg: #0a0e14;
  --bg-card: #111820;
  --bg-card-alt: #151d28;
  --border: #1e2a3a;
  --border-glow: #00ff8830;
  --accent: #00ff88;
  --accent-dim: #00cc6a;
  --accent-bg: #00ff8810;
  --text: #e0e8f0;
  --text-dim: #6b7d93;
  --text-muted: #3a4a5c;
  --good: #22c55e;
  --fair: #eab308;
  --poor: #ef4444;
  --band-160: #8b5cf6;
  --band-80: #6366f1;
  --band-60: #3b82f6;
  --band-40: #06b6d4;
  --band-30: #14b8a6;
  --band-20: #22c55e;
  --band-17: #84cc16;
  --band-15: #eab308;
  --band-12: #f97316;
  --band-10: #ef4444;
  --band-6: #ec4899;
  --band-2: #a855f7;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Rajdhani', 'Segoe UI', sans-serif;
  min-height: 100vh;
  overflow-x: hidden;
}

.mono { font-family: 'Share Tech Mono', 'Courier New', monospace; }

/* Header */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: linear-gradient(180deg, #0f1520 0%, var(--bg) 100%);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
  gap: 8px;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-title h1 {
  font-size: 1.3rem;
  font-weight: 700;
  letter-spacing: 3px;
  color: var(--accent);
  text-shadow: 0 0 20px var(--accent), 0 0 40px #00ff8840;
}

.header-title .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent);
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.clocks {
  display: flex;
  gap: 20px;
  align-items: center;
}

.clock-block {
  text-align: center;
}

.clock-label {
  font-size: 0.65rem;
  color: var(--text-dim);
  letter-spacing: 2px;
  text-transform: uppercase;
}

.clock-time {
  font-size: 1.4rem;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--text);
}

.clock-time.utc { color: var(--accent); }

/* Main layout */
.dashboard {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 12px;
  max-width: 1600px;
  margin: 0 auto;
}

@media (max-width: 900px) {
  .dashboard { grid-template-columns: 1fr; }
}

/* Cards */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: var(--bg-card-alt);
  border-bottom: 1px solid var(--border);
}

.card-header h2 {
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-dim);
}

.card-header .badge {
  font-size: 0.65rem;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--accent-bg);
  color: var(--accent);
  border: 1px solid var(--border-glow);
}

.card-body { padding: 16px; }

/* Solar panel */
.solar-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 10px;
}

.solar-item {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  text-align: center;
  transition: border-color 0.3s;
}

.solar-item:hover {
  border-color: var(--accent);
}

.solar-item .label {
  font-size: 0.65rem;
  color: var(--text-dim);
  letter-spacing: 1.5px;
  text-transform: uppercase;
  margin-bottom: 4px;
}

.solar-item .value {
  font-size: 1.6rem;
  font-weight: 700;
  line-height: 1.2;
}

.solar-item .value.small {
  font-size: 1rem;
}

.solar-item .unit {
  font-size: 0.6rem;
  color: var(--text-dim);
  margin-top: 2px;
}

/* Kp bar */
.kp-container {
  margin-top: 12px;
}

.kp-label-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
}

.kp-label-row span {
  font-size: 0.65rem;
  color: var(--text-dim);
  letter-spacing: 1px;
}

.kp-bar-bg {
  height: 10px;
  background: var(--bg);
  border-radius: 5px;
  overflow: hidden;
  border: 1px solid var(--border);
}

.kp-bar-fill {
  height: 100%;
  border-radius: 5px;
  transition: width 0.8s ease, background 0.8s ease;
  box-shadow: 0 0 10px currentColor;
}

.kp-ticks {
  display: flex;
  justify-content: space-between;
  margin-top: 2px;
  padding: 0 1px;
}

.kp-ticks span {
  font-size: 0.5rem;
  color: var(--text-muted);
  width: 11.11%;
  text-align: center;
}

/* Geomag status */
.geomag-status {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg);
  border-radius: 6px;
  border: 1px solid var(--border);
}

.geomag-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.geomag-text {
  font-size: 0.75rem;
  color: var(--text-dim);
}

.geomag-text strong {
  color: var(--text);
  text-transform: capitalize;
}

/* Signal noise */
.signal-noise {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--bg);
  border-radius: 6px;
  border: 1px solid var(--border);
}

.signal-noise .label {
  font-size: 0.65rem;
  color: var(--text-dim);
  letter-spacing: 1px;
  text-transform: uppercase;
  white-space: nowrap;
}

.signal-noise .value {
  font-size: 1rem;
  font-weight: 600;
  color: var(--accent);
}

/* Band conditions */
.band-table {
  width: 100%;
  border-collapse: collapse;
}

.band-table th {
  font-size: 0.65rem;
  color: var(--text-dim);
  letter-spacing: 1.5px;
  text-transform: uppercase;
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.band-table th:not(:first-child) { text-align: center; }

.band-table td {
  padding: 7px 12px;
  border-bottom: 1px solid #1a2332;
  font-size: 0.85rem;
}

.band-table td:not(:first-child) { text-align: center; }

.band-table tr:hover { background: #ffffff06; }

.band-name {
  font-weight: 600;
  letter-spacing: 1px;
}

.condition-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.condition-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.condition-dot.good { background: var(--good); box-shadow: 0 0 6px var(--good); }
.condition-dot.fair { background: var(--fair); box-shadow: 0 0 6px var(--fair); }
.condition-dot.poor { background: var(--poor); box-shadow: 0 0 6px var(--poor); }

.condition-text {
  font-size: 0.75rem;
  color: var(--text-dim);
}

/* DX cluster */
.dx-table-wrap {
  max-height: 520px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

.dx-table-wrap::-webkit-scrollbar { width: 4px; }
.dx-table-wrap::-webkit-scrollbar-track { background: transparent; }
.dx-table-wrap::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

.dx-table {
  width: 100%;
  border-collapse: collapse;
}

.dx-table th {
  font-size: 0.6rem;
  color: var(--text-dim);
  letter-spacing: 1.5px;
  text-transform: uppercase;
  padding: 8px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg-card);
  z-index: 1;
}

.dx-table td {
  padding: 6px 10px;
  border-bottom: 1px solid #1a2332;
  font-size: 0.8rem;
  white-space: nowrap;
}

.dx-table tr:hover { background: #ffffff06; }

.dx-freq {
  color: var(--text-dim);
}

.dx-call {
  font-weight: 600;
  color: var(--text);
}

.dx-spotter {
  color: var(--text-dim);
  font-size: 0.75rem;
}

.dx-comment {
  color: var(--text-dim);
  font-size: 0.7rem;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dx-time {
  color: var(--text-muted);
  font-size: 0.7rem;
}

.band-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.65rem;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #fff;
  min-width: 32px;
  text-align: center;
}

/* Status bar */
.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 20px;
  background: var(--bg-card-alt);
  border-top: 1px solid var(--border);
  font-size: 0.6rem;
  color: var(--text-muted);
  letter-spacing: 0.5px;
  flex-wrap: wrap;
  gap: 4px;
}

.status-bar .status-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.status-dot.ok { background: var(--good); }
.status-dot.warn { background: var(--fair); }
.status-dot.err { background: var(--poor); }

/* Loading / empty states */
.loading-text {
  text-align: center;
  padding: 30px;
  color: var(--text-muted);
  font-size: 0.85rem;
  letter-spacing: 1px;
}

/* Color helpers */
.c-good { color: var(--good); }
.c-fair { color: var(--fair); }
.c-poor { color: var(--poor); }
.c-accent { color: var(--accent); }
.c-dim { color: var(--text-dim); }
</style>
</head>
<body>

<!-- Header -->
<header class="header">
  <div class="header-title">
    <div class="dot"></div>
    <h1>HAMCLOCK LITE</h1>
  </div>
  <div class="clocks">
    <div class="clock-block">
      <div class="clock-label">UTC</div>
      <div class="clock-time utc mono" id="utcClock">--:--:--</div>
    </div>
    <div class="clock-block">
      <div class="clock-label">LOCAL</div>
      <div class="clock-time mono" id="localClock">--:--:--</div>
    </div>
  </div>
</header>

<!-- Dashboard -->
<main class="dashboard">

  <!-- Left column -->
  <div class="left-col">

    <!-- Solar Data -->
    <div class="card" style="margin-bottom:12px">
      <div class="card-header">
        <h2>Solar Conditions</h2>
        <span class="badge mono" id="solarUpdated">--</span>
      </div>
      <div class="card-body">
        <div class="solar-grid" id="solarGrid">
          <div class="loading-text">Waiting for data...</div>
        </div>
        <div class="kp-container" id="kpContainer" style="display:none">
          <div class="kp-label-row">
            <span>KP INDEX</span>
            <span class="mono" id="kpValue">--</span>
          </div>
          <div class="kp-bar-bg">
            <div class="kp-bar-fill" id="kpBar" style="width:0%"></div>
          </div>
          <div class="kp-ticks">
            <span>0</span><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span><span>6</span><span>7</span><span>8</span><span>9</span>
          </div>
        </div>
        <div class="geomag-status" id="geomagStatus" style="display:none">
          <div class="geomag-dot" id="geomagDot"></div>
          <div class="geomag-text">Geomagnetic Field: <strong id="geomagText">--</strong></div>
        </div>
        <div class="signal-noise" id="signalNoise" style="display:none">
          <span class="label">Signal Noise</span>
          <span class="value mono" id="signalNoiseValue">--</span>
        </div>
      </div>
    </div>

    <!-- Band Conditions -->
    <div class="card">
      <div class="card-header">
        <h2>HF Band Conditions</h2>
        <span class="badge mono" id="bandsUpdated">--</span>
      </div>
      <div class="card-body" style="padding:0">
        <table class="band-table" id="bandTable">
          <thead>
            <tr><th>Band</th><th>Day</th><th>Night</th></tr>
          </thead>
          <tbody id="bandBody">
            <tr><td colspan="3" class="loading-text">Waiting for data...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>

  <!-- Right column — DX Cluster -->
  <div class="right-col">
    <div class="card" style="height:100%">
      <div class="card-header">
        <h2>DX Cluster</h2>
        <span class="badge mono" id="dxUpdated">--</span>
      </div>
      <div class="card-body" style="padding:0">
        <div class="dx-table-wrap">
          <table class="dx-table">
            <thead>
              <tr><th>Freq</th><th>Band</th><th>DX Call</th><th>Spotter</th><th>UTC</th><th>Comment</th></tr>
            </thead>
            <tbody id="dxBody">
              <tr><td colspan="6" class="loading-text">Waiting for data...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

</main>

<!-- Status bar -->
<footer class="status-bar">
  <div class="status-item">
    <div class="status-dot" id="serverDot"></div>
    <span id="serverStatus">Connecting...</span>
  </div>
  <div class="status-item">
    <span id="dataAges">Solar: -- | Bands: -- | DX: --</span>
  </div>
  <div class="status-item">
    <span>HamClock Lite v1.0</span>
  </div>
</footer>

<script>
(function() {
  'use strict';

  // --- Clocks ---
  function pad(n) { return n < 10 ? '0' + n : '' + n; }

  function updateClocks() {
    var now = new Date();
    document.getElementById('utcClock').textContent =
      pad(now.getUTCHours()) + ':' + pad(now.getUTCMinutes()) + ':' + pad(now.getUTCSeconds());
    document.getElementById('localClock').textContent =
      pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
  }
  setInterval(updateClocks, 1000);
  updateClocks();

  // --- Color helpers ---
  var BAND_COLORS = {
    '160m': '#8b5cf6', '80m': '#6366f1', '60m': '#3b82f6', '40m': '#06b6d4',
    '30m': '#14b8a6', '20m': '#22c55e', '17m': '#84cc16', '15m': '#eab308',
    '12m': '#f97316', '10m': '#ef4444', '6m': '#ec4899', '2m': '#a855f7', '70cm': '#f472b6'
  };

  function conditionColor(c) {
    if (!c) return '#3a4a5c';
    var l = c.toLowerCase();
    if (l === 'good') return '#22c55e';
    if (l === 'fair') return '#eab308';
    if (l === 'poor') return '#ef4444';
    return '#3a4a5c';
  }

  function conditionClass(c) {
    if (!c) return '';
    var l = c.toLowerCase();
    if (l === 'good') return 'good';
    if (l === 'fair') return 'fair';
    if (l === 'poor') return 'poor';
    return '';
  }

  function kpColor(kp) {
    var k = parseFloat(kp) || 0;
    if (k <= 2) return '#22c55e';
    if (k <= 4) return '#eab308';
    if (k <= 6) return '#f97316';
    return '#ef4444';
  }

  function sfiColor(sfi) {
    var s = parseFloat(sfi) || 0;
    if (s >= 150) return '#22c55e';
    if (s >= 100) return '#84cc16';
    if (s >= 70) return '#eab308';
    return '#ef4444';
  }

  function geomagColor(status) {
    if (!status) return '#3a4a5c';
    var l = status.toLowerCase();
    if (l === 'quiet' || l === 'inactive') return '#22c55e';
    if (l === 'unsettled' || l === 'active') return '#eab308';
    return '#ef4444';
  }

  function timeAgo(seconds) {
    if (seconds < 0) return 'never';
    if (seconds < 60) return seconds + 's ago';
    return Math.floor(seconds / 60) + 'm ago';
  }

  function escapeHtml(s) {
    if (s === undefined || s === null) return '';
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(String(s)));
    return d.innerHTML;
  }

  // --- Render solar ---
  function renderSolar(data) {
    if (!data || !data.sfi) return;

    var items = [
      { label: 'SFI', value: data.sfi, unit: 'Solar Flux', color: sfiColor(data.sfi) },
      { label: 'SSN', value: data.ssn, unit: 'Sunspots', color: '#06b6d4' },
      { label: 'A-INDEX', value: data.aIndex, unit: 'Planetary', color: parseInt(data.aIndex) > 20 ? '#ef4444' : '#22c55e' },
      { label: 'X-RAY', value: data.xray, unit: 'Class', color: '#eab308', small: true },
      { label: 'SOLAR WIND', value: data.solarWind, unit: 'km/s', color: parseFloat(data.solarWind) > 500 ? '#ef4444' : '#06b6d4' },
      { label: 'BZ', value: data.magneticField, unit: 'nT', color: parseFloat(data.magneticField) < 0 ? '#ef4444' : '#22c55e' },
      { label: 'AURORA', value: data.aurora, unit: 'Activity', color: '#a855f7' },
      { label: 'FOF2', value: data.fof2, unit: 'MHz', color: '#3b82f6' },
    ];

    var html = '';
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      html += '<div class="solar-item">' +
        '<div class="label">' + it.label + '</div>' +
        '<div class="value mono' + (it.small ? ' small' : '') + '" style="color:' + it.color + '">' + escapeHtml(it.value) + '</div>' +
        '<div class="unit">' + it.unit + '</div>' +
        '</div>';
    }
    document.getElementById('solarGrid').innerHTML = html;

    // Kp bar
    var kp = parseFloat(data.kIndex) || 0;
    var kpPct = Math.min(kp / 9 * 100, 100);
    var kc = kpColor(data.kIndex);
    document.getElementById('kpContainer').style.display = 'block';
    document.getElementById('kpValue').textContent = data.kIndex;
    document.getElementById('kpValue').style.color = kc;
    var bar = document.getElementById('kpBar');
    bar.style.width = kpPct + '%';
    bar.style.background = 'linear-gradient(90deg, #22c55e, ' + kc + ')';
    bar.style.color = kc;

    // Geomag
    document.getElementById('geomagStatus').style.display = 'flex';
    var gc = geomagColor(data.geomagField);
    document.getElementById('geomagDot').style.background = gc;
    document.getElementById('geomagDot').style.boxShadow = '0 0 8px ' + gc;
    document.getElementById('geomagText').textContent = data.geomagField;
    document.getElementById('geomagText').style.color = gc;

    // Signal noise
    if (data.signalNoise) {
      document.getElementById('signalNoise').style.display = 'flex';
      document.getElementById('signalNoiseValue').textContent = data.signalNoise;
    }

    document.getElementById('solarUpdated').textContent = data.updated || '--';
  }

  // --- Render bands ---
  var BAND_ORDER = ['80m-40m', '30m-20m', '17m-15m', '12m-10m'];

  function renderBands(data) {
    if (!data || Object.keys(data).length === 0) {
      document.getElementById('bandBody').innerHTML =
        '<tr><td colspan="3" class="loading-text">No band data available</td></tr>';
      return;
    }

    var bands = Object.keys(data).sort(function(a, b) {
      var order = ['80m-40m', '30m-20m', '17m-15m', '12m-10m'];
      var ai = order.indexOf(a);
      var bi = order.indexOf(b);
      if (ai === -1) ai = 99;
      if (bi === -1) bi = 99;
      return ai - bi;
    });

    var html = '';
    for (var i = 0; i < bands.length; i++) {
      var name = bands[i];
      var bd = data[name];
      var dayC = bd.day || bd.Day || 'N/A';
      var nightC = bd.night || bd.Night || 'N/A';
      html += '<tr>' +
        '<td><span class="band-name">' + escapeHtml(name) + '</span></td>' +
        '<td><span class="condition-cell"><span class="condition-dot ' + conditionClass(dayC) + '"></span><span class="condition-text">' + escapeHtml(dayC) + '</span></span></td>' +
        '<td><span class="condition-cell"><span class="condition-dot ' + conditionClass(nightC) + '"></span><span class="condition-text">' + escapeHtml(nightC) + '</span></span></td>' +
        '</tr>';
    }
    document.getElementById('bandBody').innerHTML = html;
    document.getElementById('bandsUpdated').textContent = 'LIVE';
  }

  // --- Render DX ---
  function renderDX(spots) {
    if (!spots || spots.length === 0) {
      document.getElementById('dxBody').innerHTML =
        '<tr><td colspan="6" class="loading-text">No DX spots available</td></tr>';
      document.getElementById('dxUpdated').textContent = '0 spots';
      return;
    }

    var html = '';
    for (var i = 0; i < spots.length; i++) {
      var s = spots[i];
      var bc = BAND_COLORS[s.band] || '#6b7d93';
      html += '<tr>' +
        '<td class="dx-freq mono">' + escapeHtml(s.frequency) + '</td>' +
        '<td><span class="band-badge" style="background:' + bc + '20;color:' + bc + ';border:1px solid ' + bc + '40">' + escapeHtml(s.band) + '</span></td>' +
        '<td class="dx-call mono">' + escapeHtml(s.dx) + '</td>' +
        '<td class="dx-spotter mono">' + escapeHtml(s.spotter) + '</td>' +
        '<td class="dx-time mono">' + escapeHtml(s.time) + '</td>' +
        '<td class="dx-comment">' + escapeHtml(s.comment) + '</td>' +
        '</tr>';
    }
    document.getElementById('dxBody').innerHTML = html;
    document.getElementById('dxUpdated').textContent = spots.length + ' spots';
  }

  // --- Fetch data ---
  var failCount = 0;

  function fetchAll() {
    fetchJSON('/api/solar', function(data) { renderSolar(data); });
    fetchJSON('/api/bands', function(data) { renderBands(data); });
    fetchJSON('/api/dxspots', function(data) { renderDX(data); });
    fetchJSON('/api/health', function(data) {
      if (data && data.status === 'ok') {
        failCount = 0;
        document.getElementById('serverDot').className = 'status-dot ok';
        document.getElementById('serverStatus').textContent = 'Connected';
        document.getElementById('dataAges').textContent =
          'Solar: ' + timeAgo(data.solar_age) +
          ' | Bands: ' + timeAgo(data.bands_age) +
          ' | DX: ' + timeAgo(data.dx_age);
      }
    });
  }

  function fetchJSON(url, cb) {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', url, true);
    xhr.timeout = 8000;
    xhr.onload = function() {
      if (xhr.status === 200) {
        try { cb(JSON.parse(xhr.responseText)); } catch(e) {}
      }
    };
    xhr.onerror = function() {
      failCount++;
      if (failCount > 3) {
        document.getElementById('serverDot').className = 'status-dot err';
        document.getElementById('serverStatus').textContent = 'Disconnected — retrying...';
      }
    };
    xhr.ontimeout = xhr.onerror;
    xhr.send();
  }

  // Initial fetch + interval
  fetchAll();
  setInterval(fetchAll, 30000);

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
sudo apt install -y xserver-xorg xinit x11-xserver-utils unclutter curl

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

# Launch browser fullscreen
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
