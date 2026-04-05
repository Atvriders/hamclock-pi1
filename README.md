# HamClock Pi1

Lightweight ham radio dashboard designed for the Raspberry Pi 1 (ARMv6). Displays real-time solar conditions, HF band conditions, and DX cluster spots in a clean cyberpunk-styled web interface.

## Features

- **Solar Data** -- SFI, SSN, A/K index, X-ray class, solar wind, Bz, aurora, foF2
- **HF Band Conditions** -- Day/night propagation status for all HF bands
- **DX Cluster** -- Live DX spots with frequency, band, callsign, and spotter
- **Kp Index Bar** -- Visual geomagnetic activity indicator
- **Dual Clocks** -- UTC and local time display
- **Zero Dependencies** -- Pure Python 3 standard library, no pip packages needed

## Requirements

- Raspberry Pi 1 (or any Linux system with Python 3)
- Network connection for fetching solar/DX data

## Quick Install (Raspberry Pi)

```bash
git clone https://github.com/Atvriders/hamclock-pi1.git
cd hamclock-pi1
chmod +x install.sh
sudo ./install.sh
```

This installs a systemd service that starts automatically on boot.

## Manual Run

```bash
python3 server.py
```

Open `http://<pi-ip>:8080` in a browser.

## API Endpoints

| Endpoint | Description |
|---|---|
| `/api/solar` | Current solar conditions |
| `/api/bands` | HF band conditions (day/night) |
| `/api/dxspots` | Recent DX cluster spots |
| `/api/health` | Server health and data freshness |

## Data Sources

- [HamQSL](https://www.hamqsl.com/) -- Solar and band condition XML feed
- [HamQTH](https://www.hamqth.com/) -- DX cluster spots (CSV)

## Architecture

```
server.py    -- Python HTTP server with background data fetcher
index.html   -- Single-page dashboard (no build step)
install.sh   -- Raspberry Pi systemd installer
```

## License

MIT
